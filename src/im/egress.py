"""What leaves the box, and how (docs/decisions/0004-claude-extraction.md).

Policy B (`POLICY_VERSION`):
- Everyone's words go out verbatim, mine and other people's.
- Speakers are pseudonymized per request: "me", "unknown", and S1, S2... in order of first appearance.
  The mapping back to names stays local and is applied to what comes back.
- Names *spoken* in the text are not scrubbed (that needs entity extraction, ROADMAP Phase 3).

Policy C (decision 0008) is policy B with names said pseudonymized too (`im.names`). It's what a payload is built
under when a `Scrubber` is passed: trials, and the audits' sheets, drafts and spike, until every live stage that sends
what was said uses it.
- Nothing from the projects registry goes out.
- Every request goes through `send` (a chat model's through `ask`): it's sent only while this calendar month's spend
  is below the monthly cap, so the request that reaches the cap can pass it, and none goes after. It's logged in
  im.egress_log with the segment IDs it carried and what it cost before its answer is read, so an answer that can't
  be read is still logged and counted, and fails as one item.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from im import llm
from im.triage import _part_of_day

POLICY_VERSION = "B1;speakers-pseudonymized;spoken-names-not-scrubbed;no-registry"


@dataclass(frozen=True)
class Payload:
    episode_id: object
    text: str                   # exactly what goes out as the user message
    segment_ids: list           # line n of the transcript is segment_ids[n - 1]
    speakers: dict              # pseudonym -> local speaker label (never sent)
    policy: str = POLICY_VERSION
    names: dict = field(default_factory=dict)   # name pseudonym -> the name as said (never sent), policy C

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def pseudonyms(speakers) -> dict:
    """Local speaker label -> pseudonym, for (label, kind) pairs in transcript order: "me", "unknown", and S1,
    S2... in order of first appearance. Episode identity depends on this too (segment.py), so an episode is
    re-derived exactly when what would be sent about who spoke changes."""
    alias: dict = {}
    for label, kind in speakers:
        if label not in alias:
            alias[label] = ("me" if kind == "owner" else "unknown" if kind == "unknown"
                            else f"S{sum(1 for a in alias.values() if a.startswith('S')) + 1}")
    return alias


def known_names(conn) -> list[str]:
    """Everyone Hearsay knows by name, as speakers."""
    return [n for (n,) in conn.execute(
        "SELECT DISTINCT speaker_label FROM im.source_segments WHERE speaker_kind = 'person' ORDER BY 1")]


def build(conn, episode_id, scrubber=None) -> Payload:
    rows = conn.execute(
        """SELECT s.segment_id, s.speaker_label, s.speaker_kind, s.text, s.started_at, s.ended_at
           FROM im.episode_segments es JOIN im.source_segments s USING (segment_id)
           WHERE es.episode_id = %s ORDER BY es.ord""", (episode_id,)).fetchall()
    if not rows:
        raise ValueError(f"episode {episode_id} has no segments")
    taps = conn.execute(
        """SELECT coalesce(array_agg(t ORDER BY t), '{}')
           FROM im.episodes e JOIN im.source_conversations c ON c.conversation_id = e.session_id,
                unnest(c.taps) AS t
           WHERE e.episode_id = %s
             AND t BETWEEN e.started_at - interval '30 seconds' AND e.ended_at + interval '30 seconds'""",
        (episode_id,)).fetchone()[0]

    alias = pseudonyms((label, kind) for _, label, kind, *_ in rows)
    said, names, mine = [r[3] for r in rows], {}, []
    if scrubber:
        said, names, mine = scrubber.scrub(said, scrubber.find(conn, [(r[0], r[3]) for r in rows]))
    lines = [f"[{n}] {alias[r[1]]}: {text}" for n, (r, text) in enumerate(zip(rows, said), 1)]

    start: datetime = min(r[4] for r in rows).astimezone()
    end: datetime = max(r[5] for r in rows).astimezone()
    minutes = max(1, round((end - start).total_seconds() / 60))
    context = (f"{start:%A %Y-%m-%d}, {start:%H:%M}-{end:%H:%M} ({_part_of_day(start.hour)}), {minutes} min. "
               f"Speakers: {', '.join(dict.fromkeys(alias.values()))}.")
    if taps:
        context += " I tapped the pendant at " + ", ".join(f"{t.astimezone():%H:%M:%S}" for t in taps) + "."
    if names:
        context += " Names said are pseudonyms (P-...)" + (f"; mine is {' '.join(mine)}." if mine else ".")
    text = f"Context: {context}\n\nTranscript:\n" + "\n".join(lines)
    return Payload(episode_id, text, [r[0] for r in rows], {v: k for k, v in alias.items()},
                   scrubber.policy if scrubber else POLICY_VERSION, names)


def spent_this_month(conn) -> float:
    return float(conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM im.egress_log WHERE created_at >= date_trunc('month', now())"
    ).fetchone()[0])


def segments_of(conn, idea_ids) -> list:
    """The segments behind these ideas' captures: what a request showing the ideas carried, for its log row."""
    return [s for (s,) in conn.execute(
        """SELECT DISTINCT unnest(i.source_segment_ids) FROM im.idea_evidence ev JOIN im.items i USING (item_id)
           WHERE ev.idea_id = ANY(%s::uuid[])""", (list(idea_ids),))]


@dataclass(frozen=True)
class Answer:
    """A request that went out, as logged. `data` is its answer, read; None when it was refused or failed, and then
    `error` says why it failed. A failed item isn't recorded as done, so it's asked again on a later run."""
    egress_id: int
    reply: llm.Reply | None     # None when the request itself failed
    data: object
    error: str | None
    cost_usd: float             # as logged: the reply's, or what a failed request's earlier attempts cost

    @property
    def refused(self) -> bool:
        return self.error is None and self.reply is not None and self.reply.refused

    @property
    def failed(self) -> bool:
        return self.error is not None


def _log(conn, model: str, payload: str, segment_ids, prompt_version: str, policy: str, episode_id,
         reply: llm.Reply | None = None, error: str | None = None, cost_usd: float = 0.0) -> int:
    r = reply
    return conn.execute(
        """INSERT INTO im.egress_log (episode_id, segment_ids, model, prompt_version, privacy_policy_version, payload,
             payload_sha256, request_id, stop_reason, served_by, input_tokens, output_tokens, cost_usd, error)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING egress_id""",
        (episode_id, list(segment_ids), model, prompt_version, policy, payload,
         hashlib.sha256(payload.encode()).hexdigest(), r and r.request_id, r and r.stop_reason, r and r.served_by,
         r and r.input_tokens, r and r.output_tokens, r.cost_usd if r else cost_usd, error)).fetchone()[0]


def send(conn, request: Callable[[], llm.Reply], *, model: str, payload: str, segment_ids, prompt_version: str,
         monthly_cap_usd: float, policy: str = POLICY_VERSION, episode_id=None,
         read: Callable[[dict], object] = lambda answer: answer) -> Answer:
    """The one way a paid request goes out: under the monthly cap, then `request()`, then the log row (its cost from
    the reply's usage; for an error, what attempts before it were charged, if any, and otherwise 0: it's logged all
    the same, since the payload went), and only then the answer read, by `read` from its JSON.

    Raises llm.Stop, sending nothing, when the cap is reached; and, after logging it, when the provider can't answer
    now or no request would get through (credentials, credit, no such model). Anything else that goes wrong once
    it's sent (an error response, no answer, one cut off or not in the schema's shape) comes back as a failed
    Answer, so the caller skips that item and goes on."""
    if spent_this_month(conn) >= monthly_cap_usd:
        raise llm.Stop(f"monthly cap of ${monthly_cap_usd:.2f} reached")
    where = (model, payload, segment_ids, prompt_version, policy, episode_id)
    try:
        reply = request()
    except llm.Stop as e:
        _log(conn, *where, error=str(e)[:500], cost_usd=getattr(e, "cost_usd", 0.0))
        raise
    except Exception as e:   # an error response: the payload went, but no answer came back
        error, paid = f"{type(e).__name__}: {e}"[:500], getattr(e, "cost_usd", 0.0)
        return Answer(_log(conn, *where, error=error, cost_usd=paid), None, None, error, paid)
    egress_id = _log(conn, *where, reply=reply)
    paid = reply.cost_usd or 0.0
    if reply.refused:
        return Answer(egress_id, reply, None, None, paid)
    try:
        return Answer(egress_id, reply, read(json.loads(reply.text)), None, paid)
    except (TypeError, ValueError, KeyError) as e:   # no answer written, one cut off, or not in the schema's shape
        error = f"stopped: {reply.stop_reason}, " + ("no answer" if reply.text is None else f"{type(e).__name__}: {e}")
        conn.execute("UPDATE im.egress_log SET error = %s WHERE egress_id = %s", (error[:500], egress_id))
        return Answer(egress_id, reply, None, error[:500], paid)


def ask(conn, client, model: str, effort: str, system: str, schema: dict, text: str, *, segment_ids,
        prompt_version: str, monthly_cap_usd: float, policy: str = POLICY_VERSION, episode_id=None,
        max_tokens: int | None = None, read: Callable[[dict], object] = lambda answer: answer) -> Answer:
    """One request to a chat model (llm.complete), by `send`, with an output budget of `max_tokens` (None:
    llm.MAX_TOKENS). A Claude model with no known price isn't sent: what it spent couldn't be counted toward the cap."""
    if not llm.via_openrouter(model) and llm.price(model) is None:
        raise llm.Stop(f"no price known for {model} (llm.PRICES): nothing sent")
    budget = max_tokens or llm.MAX_TOKENS
    return send(conn, lambda: llm.complete(client, model, effort, system, schema, text, budget), model=model,
                payload=text, segment_ids=segment_ids, prompt_version=prompt_version,
                monthly_cap_usd=monthly_cap_usd, policy=policy, episode_id=episode_id, read=read)
