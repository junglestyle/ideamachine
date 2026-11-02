"""Idea extraction with Claude (docs/decisions/0004-claude-extraction.md).

Each current episode goes out once per model / prompt / privacy policy, and again only if what would be
sent changed. A payload already read on another episode isn't sent again: its items are reused. Claude returns items anchored to transcript line numbers; they're mapped back to segment
IDs and to who said them locally. Every request is logged in im.egress_log, and a monthly budget stops the
stage before it's exceeded.
"""

import hashlib
import json
import uuid

from im import egress

SCHEMA_VERSION = 1
STAGE_VERSION = "extract/1"
ITEM_NS = uuid.UUID("3c1e8a52-7d0f-4b6e-9f21-5a8c4e7d1b90")
KINDS = ["idea", "aphorism", "joke", "observation", "project", "task", "decision"]
# $ per million tokens (input, output); a refusal fallback may answer on another model.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-opus-4-8": (5.0, 25.0), "claude-sonnet-5-5": (2.0, 10.0),
          "claude-haiku-5-5": (0.10, 0.50)}   # Haiku 5.5: prompts up to 100K tokens, which every episode is
FALLBACK_MODEL = "claude-opus-4-8"

# v1: the first prompt (58% of its captures kept). Its text must never change: its hash is the prompt_version every
# extraction so far is filed under, and a new version re-reads every episode.
SYSTEM = """You extract ideas from transcripts of my everyday speech, recorded by an always-on pendant. \
Most of what you'll see is small talk, logistics, background noise and transcription errors. Your job is \
to find what's worth adding to my idea archive.

What counts (a low bar; when unsure, include it with low confidence):
- ideas, theories, models, hunches; half-formed is fine
- aphorisms, compact formulations, jokes, bits, slogans, images
- observations about people, myself or social dynamics that could generalize
- things I want to build, try, or look into
- tasks and decisions, only when concrete
Anyone's idea counts: mine, a friend's, a stranger's. Say who said it, using the speaker labels exactly as \
they appear in the transcript ("me", "S1", "unknown"...).

What doesn't count: greetings, logistics (ordering drinks, directions), filler, the same point repeated \
(capture it once), garbled transcription.

How to capture:
- Be faithful. Quote the distinctive wording verbatim from the transcript; fix only obvious transcription \
errors, and say so in the gist when you did. Then state the idea plainly in one line.
- Don't evaluate, fact-check, moralize, soften or balance. Provocative, controversial, exaggerated or false \
ideas are captured as ideas, not endorsed as claims.
- Don't invent. Every item cites the numbers of the transcript lines it comes from. If the speech is too \
garbled to be sure what was said, skip it.
- If I say "note to self", capture what I said there.
- The context may say I tapped the pendant. A tap usually means I wanted something kept, but taps can be \
accidental: treat one as a reason to look closely at what I said around that time and to raise your \
confidence if something there is worth keeping, not as a reason to capture filler.
- Most transcripts contain nothing worth capturing. An empty list is the usual, correct answer.

confidence is your estimate that I'd want the item in my archive."""

# v2: revised from my discard reasons (ROADMAP Phase 2, slice 3). {interests} is config (extract.interests), kept
# out of the repo; like the rest of the text, changing it is a new prompt_version.
SYSTEM_V2 = """You extract ideas from transcripts of my everyday speech, recorded by an always-on pendant. \
Most of what you'll see is small talk, logistics, background noise and transcription errors. Your job is \
to find what's worth adding to my idea archive.

What counts:
- ideas, theories, models, hunches; half-formed is fine
- aphorisms, compact formulations, jokes, bits, slogans, images
- observations about people, myself or social dynamics that generalize beyond the moment
- things I want to build, try, or look into, when there's something non-obvious in them
Anyone's idea counts: mine, a friend's, a stranger's. Say who said it, using the speaker labels exactly as \
they appear in the transcript ("me", "S1", "unknown"...).
{interests}
What doesn't count, because I've discarded these before:
- Anything that needs the conversation to make sense. Each item must stand on its own for someone who \
didn't hear the conversation: if the point depends on context that's in the transcript, put that context \
in the gist; if it isn't there, skip it. Skip fragments and one-off remarks.
- The obvious: common sense, generic advice, and routine feature ideas for the software I'm building \
(a pendant recorder, its transcription, an idea archive and its app). A design idea counts only if it has a \
non-obvious mechanism or tradeoff.
- Anecdotes: what happened to someone counts only for a point that generalizes; capture the point.
- Media: TV, radio, songs, videos, or someone reading aloud, unless someone makes a point of their own about it.
- Tasks, decisions and plans, unless I say I want to remember them. Ordinary plans and logistics never count.
- Greetings, small talk, filler, the same point repeated (capture it once), garbled transcription.

How to capture:
- Be faithful. Quote the distinctive wording verbatim from the transcript; fix only obvious transcription \
errors, and say so in the gist when you did. Then state the idea plainly in one line, with whatever context \
it needs to stand alone.
- Don't evaluate, fact-check, moralize, soften or balance. Provocative, controversial, exaggerated or false \
ideas are captured as ideas, not endorsed as claims.
- Don't invent. Every item cites the numbers of the transcript lines it comes from. If the speech is too \
garbled to be sure what was said, skip it.
- If I say "note to self", capture what I said there.
- The context may say I tapped the pendant. A tap usually means I wanted something kept, but taps can be \
accidental: treat one as a reason to look closely at what I said around that time and to raise your \
confidence if something there is worth keeping, not as a reason to capture filler.
- Most transcripts contain nothing worth capturing. An empty list is the usual, correct answer.

confidence is your estimate that I'd keep the item. I've kept a little over half of what was captured before; \
the ones I discard most often are the kinds listed above."""

# v3: widened to neutral observations (ROADMAP-v2 §4, question 6). Since M2 nothing captured is judged: an observation
# matters if it recurs and I keep its cluster, so the bar is "a thought that could come up again", not "worth
# archiving". v2's discard-driven exclusions go, except the ones that aren't thoughts. Revised before it went live
# (2026-11-02): its first trial captured fragments, in-the-moment instructions and verdicts on particular people.
SYSTEM_V3 = """You extract observations from transcripts of my everyday speech, recorded by an always-on pendant. \
Most of what you'll see is small talk, logistics, background noise and transcription errors. Your job is to \
capture each distinct thought someone expresses, so that when the same thought comes up again, days or months \
later, it can be recognized. Nothing you capture is judged on its own: what matters is decided later, by whether \
a thought recurs and whether I keep it.

The test for every item: could someone say this again in a different conversation, weeks later, and it would be \
the same thought? "The pills are a band-aid" passes. "I'm still overshooting" and "toss it on seven" don't: \
they're about this moment.

What counts, when it passes that test:
- ideas, theories, models, hunches; half-formed is fine
- questions someone is actually wondering about (not logistics: "where are the keys?" doesn't count)
- aphorisms, compact formulations, jokes, bits, slogans, images
- opinions, beliefs and preferences about something general; obvious is fine, if someone holds it
- observations about people, myself, social dynamics or the world that generalize beyond this occasion
- things I want to build, try, or look into, and principles or techniques someone is teaching
- tasks and decisions, only when I say I want to remember them
Anyone's thought counts: mine, a friend's, a stranger's. Say who said it, using the speaker labels exactly as \
they appear in the transcript ("me", "S1", "unknown"...).
{interests}
What doesn't count:
- Fragments and reactions that are only a thought with the conversation around them ("it's definitely \
strange", "you were probably locked into that anyway").
- What's happening right now: narration, venting about a particular event, instructions for the task at hand \
(counts, positions, "do it like this"), logistics, plans, scheduling.
- Verdicts on a particular person other than me (their looks, character, sex life, health). If there's a \
general point in it, capture the general point, without the person.
- Greetings, small talk and filler.
- Media: TV, radio, songs, videos, or someone reading aloud, unless someone responds with a thought of their own.
- The same thought repeated within this transcript (capture it once), and garbled transcription.

How to capture:
- Be faithful. Quote the distinctive wording verbatim from the transcript; fix only obvious transcription \
errors, and say so in the gist when you did. Then state the thought plainly in one line, with whatever context \
it needs to stand alone for someone who didn't hear the conversation. If the context it needs isn't in the \
transcript, skip it.
- kind: file a question as "observation" unless it's an idea put as a question.
- Don't evaluate, fact-check, moralize, soften or balance. Provocative, controversial, exaggerated or false \
thoughts are captured as said, not endorsed as claims.
- Don't invent. Every item cites the numbers of the transcript lines it comes from. If the speech is too \
garbled to be sure what was said, skip it.
- If I say "note to self", capture what I said there.
- The context may say I tapped the pendant. A tap usually means I wanted something kept: look closely at what I \
said around that time.
- Many transcripts contain no such thoughts. An empty list is then the correct answer.

confidence is how sure you are that the quote and gist are what was actually said and meant: lower it for \
shaky transcription or a reading that depends on guessing the context."""

PROMPTS = {"v1": SYSTEM, "v2": SYSTEM_V2, "v3": SYSTEM_V3}


def system_prompt(name: str = "v1", interests: str = "") -> str:
    """The system prompt named in config. `interests` fills v2's slot; v1 has none and must stay byte-identical."""
    text = PROMPTS[name]
    if "{interests}" not in text:
        return text
    return text.replace("{interests}", f"\nMy interests, as context for what's worth a closer look (not a filter): "
                                       f"{interests.strip()}\n" if interests.strip() else "")

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": KINDS},
                    "said_by": {"type": "string"},
                    "quote": {"type": "string"},
                    "lines": {"type": "array", "items": {"type": "integer"}},
                    "gist": {"type": "string"},
                    "themes": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                },
                "required": ["kind", "said_by", "quote", "lines", "gist", "themes", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def prompt_version(model: str, effort: str, system: str = SYSTEM) -> str:
    return hashlib.sha256(json.dumps([system, SCHEMA, model, effort], sort_keys=True).encode()).hexdigest()[:16]


def spent_this_month(conn) -> float:
    return float(conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM im.egress_log WHERE created_at >= date_trunc('month', now())"
    ).fetchone()[0])


def _cost(model: str, usage) -> float | None:
    price = PRICES.get(model)
    if price is None or usage is None:
        return None
    tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25 \
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
    return round(tokens_in * price[0] / 1e6 + (usage.output_tokens or 0) * price[1] / 1e6, 6)


def _pending(conn, model: str, pv: str) -> list:
    """Current episodes not yet read under this model/prompt/policy, or whose payload changed. Newest first."""
    rows = conn.execute(
        """SELECT e.episode_id, x.input_hash FROM im.episodes e
           LEFT JOIN im.extractions x ON x.episode_id = e.episode_id AND x.model = %s
                AND x.prompt_version = %s AND x.privacy_policy_version = %s
           WHERE e.current ORDER BY e.started_at DESC""", (model, pv, egress.POLICY_VERSION)).fetchall()
    out = []
    for eid, done_hash in rows:
        p = egress.build(conn, eid)
        if p.sha256 != done_hash:
            out.append(p)
    return out


def _reuse(conn, p: egress.Payload, model: str, pv: str) -> tuple[int, list[dict]] | None:
    """An earlier read of this exact payload on another episode: its egress id and items, mapped onto this one.

    Naming a speaker re-derives the episode but sends the same pseudonymized text, so there's nothing new to read.
    Lines map by position and speakers through the pseudonym both payloads share. Each item remembers the one it
    was copied from (`from_item_id`), so its match can come along too. None if there's no such read."""
    row = conn.execute(
        """SELECT episode_id, egress_id FROM im.extractions
           WHERE input_hash = %s AND model = %s AND prompt_version = %s AND privacy_policy_version = %s
             AND episode_id <> %s ORDER BY created_at DESC LIMIT 1""",
        (p.sha256, model, pv, egress.POLICY_VERSION, p.episode_id)).fetchone()
    if row is None:
        return None
    old = egress.build(conn, row[0])
    if old.sha256 != p.sha256:
        return None
    line = {sid: n for n, sid in enumerate(old.segment_ids)}
    alias = {label: pseudo for pseudo, label in old.speakers.items()}
    rows = conn.execute(
        """SELECT item_id, kind, w.said_by, quote, gist, themes, confidence, source_segment_ids
           FROM im.items JOIN im.item_said_by w USING (item_id)
           WHERE episode_id = %s AND model = %s AND prompt_version = %s AND privacy_policy_version = %s
           ORDER BY item_id""", (row[0], model, pv, egress.POLICY_VERSION)).fetchall()
    return row[1], [{"from_item_id": i, "kind": k, "said_by": p.speakers.get(alias.get(by), by), "quote": q,
                     "gist": g, "themes": t, "confidence": c,
                     "source_segment_ids": [p.segment_ids[line[s]] for s in segs]}
                    for i, k, by, q, g, t, c, segs in rows]


def _log(conn, p: egress.Payload, model: str, pv: str, **fields) -> int:
    cols = ["episode_id", "segment_ids", "model", "prompt_version", "privacy_policy_version", "payload",
            "payload_sha256"] + list(fields)
    vals = [p.episode_id, p.segment_ids, model, pv, egress.POLICY_VERSION, p.text, p.sha256] + list(fields.values())
    return conn.execute(
        f"INSERT INTO im.egress_log ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING egress_id",
        vals).fetchone()[0]


def _items(p: egress.Payload, raw: list[dict]) -> list[dict]:
    """Claude's items, mapped back to segments and local speakers. Items citing no real line are dropped."""
    out = []
    for item in raw:
        lines = sorted({n for n in item["lines"] if 1 <= n <= len(p.segment_ids)})
        if not lines:
            continue
        out.append(item | {
            "said_by": p.speakers.get(item["said_by"], item["said_by"]),
            "source_segment_ids": [p.segment_ids[n - 1] for n in lines],
            "confidence": min(max(float(item["confidence"]), 0.0), 1.0),
        })
    return out


def _item_id(p: egress.Payload, model: str, pv: str, i: int) -> uuid.UUID:
    return uuid.uuid5(ITEM_NS, f"{p.episode_id}|{model}|{pv}|{egress.POLICY_VERSION}|{p.sha256}|{i}")


def _carry_matches(conn, p: egress.Payload, model: str, pv: str, items: list[dict]) -> None:
    """Reused items keep the matcher's decision and my verdicts on the items they were copied from: the same capture
    isn't judged (or paid for) twice, and can't turn into a second idea. Verdicts go item to item, so two captures
    with the same quote each keep their own; feedback.carry_verdicts, which matches by quote, sees them settled."""
    for i, it in enumerate(items):
        new = _item_id(p, model, pv, i)
        copied = conn.execute(
            """INSERT INTO im.item_verdicts (item_id, segment_ids, quote, verdict, note, how, decided_at, carried_from)
               SELECT n.item_id, n.source_segment_ids, n.quote, v.verdict, v.note, v.how, v.decided_at, v.verdict_id
               FROM im.item_verdicts v JOIN im.items n ON n.item_id = %s
               WHERE v.item_id = %s AND NOT EXISTS (SELECT 1 FROM im.item_verdicts x WHERE x.item_id = n.item_id)
               ORDER BY v.decided_at, v.verdict_id""", (new, it["from_item_id"])).rowcount
        if copied:
            conn.execute("""INSERT INTO im.verdict_carries (from_item_id, to_item_id, score, carried, method)
                            VALUES (%s, %s, 1, true, 'reuse') ON CONFLICT DO NOTHING""", (it["from_item_id"], new))
        conn.execute("""INSERT INTO im.item_matches (item_id, decision, idea_id, judged_by, egress_id)
                        SELECT %s, decision, idea_id, judged_by, egress_id FROM im.item_matches WHERE item_id = %s
                        ON CONFLICT DO NOTHING""", (new, it["from_item_id"]))
        conn.execute("""INSERT INTO im.idea_evidence (idea_id, item_id, relation, judged_by)
                        SELECT idea_id, %s, relation, judged_by FROM im.idea_evidence WHERE item_id = %s
                        ON CONFLICT DO NOTHING""", (new, it["from_item_id"]))


def _save(conn, p: egress.Payload, model: str, pv: str, egress_id: int, items: list[dict]) -> None:
    with conn.transaction():
        conn.execute("""DELETE FROM im.items WHERE episode_id = %s AND model = %s AND prompt_version = %s
                        AND privacy_policy_version = %s""", (p.episode_id, model, pv, egress.POLICY_VERSION))
        for i, it in enumerate(items):
            # said_by_segment_id: the first cited segment the speaker said, so a later rename reaches the item.
            conn.execute(
                """INSERT INTO im.items (item_id, episode_id, kind, said_by, quote, gist, themes, confidence,
                     source_segment_ids, egress_id, model, prompt_version, privacy_policy_version,
                     schema_version, stage_version, input_hash, said_by_segment_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     (SELECT u.seg FROM unnest(%s::uuid[]) WITH ORDINALITY AS u(seg, n)
                      JOIN im.source_segments s ON s.segment_id = u.seg
                      WHERE s.speaker_label = %s ORDER BY u.n LIMIT 1))""",
                (_item_id(p, model, pv, i),
                 p.episode_id, it["kind"], it["said_by"], it["quote"], it["gist"], it["themes"], it["confidence"],
                 it["source_segment_ids"], egress_id, model, pv, egress.POLICY_VERSION, SCHEMA_VERSION,
                 STAGE_VERSION, p.sha256, it["source_segment_ids"], it["said_by"]))
        conn.execute(
            """INSERT INTO im.extractions (episode_id, model, prompt_version, privacy_policy_version, input_hash,
                 egress_id, n_items) VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (episode_id, model, prompt_version, privacy_policy_version) DO UPDATE
               SET input_hash = excluded.input_hash, egress_id = excluded.egress_id, n_items = excluded.n_items,
                   created_at = now()""",
            (p.episode_id, model, pv, egress.POLICY_VERSION, p.sha256, egress_id, len(items)))


def call(client, model: str, effort: str, system: str, text: str):
    """Opus falls back to FALLBACK_MODEL on a refusal. Sonnet and Haiku (trials only) don't take that fallback, so
    a refusal there is counted as one."""
    fallback = {"betas": ["server-side-fallback-2026-06-01"], "fallbacks": [{"model": FALLBACK_MODEL}]} \
        if model.startswith("claude-opus") else {}
    return client.beta.messages.create(
        model=model, max_tokens=16000, system=system,
        messages=[{"role": "user", "content": text}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}}, **fallback)


def run_stage(conn, client, model: str = "claude-opus-5-5", effort: str = "medium",
              monthly_cap_usd: float = 20.0, limit: int | None = None, system: str = SYSTEM) -> dict:
    import anthropic

    pv = prompt_version(model, effort, system)
    todo = _pending(conn, model, pv)
    stats = {"model": model, "prompt_version": pv, "pending": len(todo), "read": 0, "reused": 0, "items": 0,
             "refused": 0, "cost_usd": 0.0, "stopped": None}
    for p in todo[:limit] if limit else todo:
        if reused := _reuse(conn, p, model, pv):
            _save(conn, p, model, pv, *reused)
            _carry_matches(conn, p, model, pv, reused[1])
            stats["reused"] += 1
            stats["items"] += len(reused[1])
            continue
        if spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            break
        try:
            r = call(client, model, effort, system, p.text)
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
            _log(conn, p, model, pv, error=f"{type(e).__name__}: {e}"[:500])
            stats["stopped"] = f"API unavailable, will retry next run: {type(e).__name__}"
            break
        served = getattr(r, "model", model) or model
        cost = _cost(served, r.usage)
        egress_id = _log(conn, p, model, pv, request_id=getattr(r, "_request_id", None), stop_reason=r.stop_reason,
                         served_by=served, input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens,
                         cost_usd=cost)
        stats["cost_usd"] += cost or 0.0
        stats["read"] += 1
        if r.stop_reason == "refusal":  # the whole chain declined: record it read, with nothing captured
            stats["refused"] += 1
            _save(conn, p, model, pv, egress_id, [])
            continue
        text = next(b.text for b in r.content if b.type == "text")
        items = _items(p, json.loads(text)["items"])
        _save(conn, p, model, pv, egress_id, items)
        stats["items"] += len(items)
    stats["cost_usd"] = round(stats["cost_usd"], 4)
    stats["spent_this_month_usd"] = round(spent_this_month(conn), 4)
    return stats


def forgotten_sent(conn) -> list[tuple]:
    """Forgotten segments that had already gone out: (segment_id, forgotten_at, first sent at, requests)."""
    return conn.execute(
        """SELECT t.segment_id, t.forgotten_at, min(g.created_at), count(*)
           FROM im.source_tombstones t JOIN im.egress_log g ON t.segment_id = ANY(g.segment_ids)
           GROUP BY t.segment_id, t.forgotten_at ORDER BY min(g.created_at)""").fetchall()


def review_items(conn) -> list[tuple]:
    """Captures on current episodes I haven't decided on yet, one per observation (a verdict or feedback on any of
    its captures decides it, migration 037): most confident first."""
    return conn.execute(
        """SELECT * FROM (
             SELECT DISTINCT ON (coalesce(i.observation_id, i.item_id))
                    i.item_id, i.kind, w.said_by, i.quote, i.gist, i.themes, i.confidence, i.source_segment_ids,
                    e.started_at, e.episode_id
             FROM im.items i JOIN im.item_said_by w USING (item_id) JOIN im.episodes e USING (episode_id)
             WHERE e.current AND NOT EXISTS (SELECT 1 FROM im.capture_verdict v WHERE v.item_id = i.item_id)
               AND NOT EXISTS (SELECT 1 FROM im.items s JOIN pub.feedback_events f ON f.item_id = s.item_id
                               WHERE s.item_id = i.item_id OR s.observation_id = i.observation_id)
             ORDER BY coalesce(i.observation_id, i.item_id), i.created_at, i.item_id) r
           ORDER BY confidence DESC, started_at DESC""").fetchall()


def discards(conn) -> list[tuple]:
    """Items I discarded (latest verdict), with my note and the prompt that captured them, newest first."""
    return conn.execute(
        """SELECT i.kind, w.said_by, i.quote, i.gist, i.confidence, v.decided_at, v.note, i.model, i.prompt_version
           FROM im.items i JOIN im.item_said_by w USING (item_id) JOIN LATERAL (
             SELECT verdict, note, decided_at FROM im.item_verdicts WHERE item_id = i.item_id
             ORDER BY decided_at DESC LIMIT 1) v ON v.verdict = 'discard'
           ORDER BY v.decided_at DESC""").fetchall()


def decide(conn, item_id, verdict: str, note: str | None = None) -> None:
    conn.execute(
        """INSERT INTO im.item_verdicts (item_id, segment_ids, quote, verdict, note)
           SELECT item_id, source_segment_ids, quote, %s, %s FROM im.items WHERE item_id = %s""",
        (verdict, note, item_id))
