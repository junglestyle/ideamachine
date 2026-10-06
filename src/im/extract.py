"""Idea extraction with Claude (docs/decisions/0004-claude-extraction.md).

Each current episode goes out once per model / prompt / privacy policy, and again only if what would be
sent changed. Claude returns items anchored to transcript line numbers; they're mapped back to segment
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
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-opus-4-8": (5.0, 25.0)}
FALLBACK_MODEL = "claude-opus-4-8"

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


def prompt_version(model: str, effort: str) -> str:
    return hashlib.sha256(json.dumps([SYSTEM, SCHEMA, model, effort], sort_keys=True).encode()).hexdigest()[:16]


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


def _save(conn, p: egress.Payload, model: str, pv: str, egress_id: int, items: list[dict]) -> None:
    with conn.transaction():
        conn.execute("""DELETE FROM im.items WHERE episode_id = %s AND model = %s AND prompt_version = %s
                        AND privacy_policy_version = %s""", (p.episode_id, model, pv, egress.POLICY_VERSION))
        for i, it in enumerate(items):
            conn.execute(
                """INSERT INTO im.items (item_id, episode_id, kind, said_by, quote, gist, themes, confidence,
                     source_segment_ids, egress_id, model, prompt_version, privacy_policy_version,
                     schema_version, stage_version, input_hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (uuid.uuid5(ITEM_NS, f"{p.episode_id}|{model}|{pv}|{egress.POLICY_VERSION}|{p.sha256}|{i}"),
                 p.episode_id, it["kind"], it["said_by"], it["quote"], it["gist"], it["themes"], it["confidence"],
                 it["source_segment_ids"], egress_id, model, pv, egress.POLICY_VERSION, SCHEMA_VERSION,
                 STAGE_VERSION, p.sha256))
        conn.execute(
            """INSERT INTO im.extractions (episode_id, model, prompt_version, privacy_policy_version, input_hash,
                 egress_id, n_items) VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (episode_id, model, prompt_version, privacy_policy_version) DO UPDATE
               SET input_hash = excluded.input_hash, egress_id = excluded.egress_id, n_items = excluded.n_items,
                   created_at = now()""",
            (p.episode_id, model, pv, egress.POLICY_VERSION, p.sha256, egress_id, len(items)))


def run_stage(conn, client, model: str = "claude-opus-5-5", effort: str = "medium",
              monthly_cap_usd: float = 20.0, limit: int | None = None) -> dict:
    import anthropic

    pv = prompt_version(model, effort)
    todo = _pending(conn, model, pv)
    stats = {"model": model, "prompt_version": pv, "pending": len(todo), "read": 0, "items": 0,
             "refused": 0, "cost_usd": 0.0, "stopped": None}
    for p in todo[:limit] if limit else todo:
        if spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            break
        try:
            r = client.beta.messages.create(
                model=model, max_tokens=16000, system=SYSTEM,
                messages=[{"role": "user", "content": p.text}],
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
                betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": FALLBACK_MODEL}])
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
    """Items on current episodes I haven't decided on yet: most confident first."""
    return conn.execute(
        """SELECT i.item_id, i.kind, i.said_by, i.quote, i.gist, i.themes, i.confidence, i.source_segment_ids,
                  e.started_at, e.episode_id
           FROM im.items i JOIN im.episodes e USING (episode_id)
           WHERE e.current AND NOT EXISTS (SELECT 1 FROM im.item_verdicts v WHERE v.item_id = i.item_id)
             AND NOT EXISTS (SELECT 1 FROM pub.feedback_events f WHERE f.item_id = i.item_id)
           ORDER BY i.confidence DESC, e.started_at DESC""").fetchall()


def discards(conn) -> list[tuple]:
    """Items I discarded (latest verdict), with my note and the prompt that captured them, newest first."""
    return conn.execute(
        """SELECT i.kind, i.said_by, i.quote, i.gist, i.confidence, v.decided_at, v.note, i.model, i.prompt_version
           FROM im.items i JOIN LATERAL (
             SELECT verdict, note, decided_at FROM im.item_verdicts WHERE item_id = i.item_id
             ORDER BY decided_at DESC LIMIT 1) v ON v.verdict = 'discard'
           ORDER BY v.decided_at DESC""").fetchall()


def decide(conn, item_id, verdict: str, note: str | None = None) -> None:
    conn.execute(
        """INSERT INTO im.item_verdicts (item_id, segment_ids, quote, verdict, note)
           SELECT item_id, source_segment_ids, quote, %s, %s FROM im.items WHERE item_id = %s""",
        (verdict, note, item_id))
