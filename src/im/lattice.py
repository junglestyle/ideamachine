"""The idea lattice (ROADMAP Phase 2, slice 1): durable ideas, with Claude's captures as their evidence.

For each new captured item, the matcher finds the closest existing ideas by embedding and asks Claude
whether the item is a new idea, the same as one of them, or an evolution of one. It only links, never
merges destructively. Discarded items are skipped. Related-idea edges come from the embeddings and are
rebuilt every run.
"""

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from im import egress

MATCH_K = 5                    # nearest ideas shown to Claude
RELATED_K, RELATED_MIN = 3, 0.70   # related edges: top 3 neighbours at cosine >= 0.70. bge-small puts a typical
                                   # nearest neighbour at 0.68 (p90 0.73) on my ideas; tune by eye in Lattice.
MATCH_MODEL, MATCH_EFFORT = "claude-opus-5-5", "low"

MATCH_SYSTEM = """You maintain my idea lattice: a long-lived collection of ideas captured from my conversations \
and notes. You get one newly captured item and the existing ideas most similar to it. Decide which it is:
- "new": a different idea from all of the candidates. Prefer this over forcing a match.
- "same_as": the same idea as a candidate: restated, repeated, or a near-paraphrase. Give its number.
- "evolves": it develops, refines, extends or applies a candidate into a distinct idea of its own. Give its number.
For "new" and "evolves", also write a short title (at most 8 words) and a one-line statement that keeps the \
item's distinctive wording; for "same_as", leave both empty. Use candidate 0 for "new". Don't evaluate, \
fact-check or soften anything."""

MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["new", "same_as", "evolves"]},
        "candidate": {"type": "integer"},
        "title": {"type": "string"},
        "statement": {"type": "string"},
    },
    "required": ["decision", "candidate", "title", "statement"],
    "additionalProperties": False,
}


def matcher_version() -> str:
    digest = hashlib.sha256(json.dumps([MATCH_SYSTEM, MATCH_SCHEMA, MATCH_MODEL, MATCH_EFFORT, MATCH_K],
                                       sort_keys=True).encode()).hexdigest()[:12]
    return f"match-1:{digest}"


# --- Seeding from an archive -------------------------------------------------------------------------

ENTRY = re.compile(r"^(\d+)\.\s+(.*)$")
CLUSTER = re.compile(r"^([A-Z])\.\s+(.+?):\s*$")


def parse_archive(text: str) -> tuple[list[dict], list[dict]]:
    """Numbered entries (the first line is the idea, the indented rest its notes) and lettered clusters
    (a name line, a line of #refs, a description)."""
    entries, clusters = [], []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = ENTRY.match(lines[i])
        c = CLUSTER.match(lines[i])
        if m:
            body = []
            i += 1
            while i < len(lines) and not ENTRY.match(lines[i]) and not lines[i].startswith("Important emerging"):
                if lines[i].strip():
                    body.append(lines[i].strip())
                i += 1
            entries.append({"n": int(m.group(1)), "statement": m.group(2).strip(), "notes": "\n".join(body)})
            continue
        if c:
            refs, desc = [], []
            i += 1
            while i < len(lines) and not CLUSTER.match(lines[i]) and lines[i].strip():
                found = re.findall(r"#(\d+)", lines[i])
                if found and not refs:
                    refs = [int(n) for n in found]
                else:
                    desc.append(lines[i].strip())
                i += 1
            clusters.append({"letter": c.group(1), "name": c.group(2), "members": refs, "description": " ".join(desc)})
            continue
        i += 1
    return entries, clusters


def _title(statement: str, words: int = 8) -> str:
    plain = statement.strip("“”\"' ")
    parts = plain.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def seed_archive(conn, path: Path, name: str = "chatgpt") -> dict:
    """Import an archive's ideas, its clusters as pinned themes, and its #N cross-references as links.
    Idempotent: entries already imported (by archive_ref) are left alone."""
    entries, clusters = parse_archive(Path(path).read_text())
    ids = {}
    added = 0
    with conn.transaction():
        for e in entries:
            ref = f"{name}#{e['n']}"
            row = conn.execute(
                """INSERT INTO im.ideas (title, statement, origin, archive_ref, notes) VALUES (%s, %s, 'archive', %s, %s)
                   ON CONFLICT (archive_ref) DO NOTHING RETURNING idea_id""",
                (_title(e["statement"]), e["statement"], ref, e["notes"] or None)).fetchone()
            added += row is not None
            ids[e["n"]] = row[0] if row else conn.execute(
                "SELECT idea_id FROM im.ideas WHERE archive_ref = %s", (ref,)).fetchone()[0]
        for e in entries:
            for n in {int(x) for x in re.findall(r"#(\d+)", e["notes"])} - {e["n"]}:
                if n in ids:
                    conn.execute("""INSERT INTO im.idea_links (a, b, kind, method) VALUES (%s, %s, 'archive', %s)
                                    ON CONFLICT DO NOTHING""", (ids[e["n"]], ids[n], f"{name}-archive"))
        for c in clusters:
            ref = f"{name}:{c['letter']}"
            theme = conn.execute(
                """INSERT INTO im.themes (name, description, origin, archive_ref, pinned) VALUES (%s, %s, 'archive', %s, true)
                   ON CONFLICT (archive_ref) DO UPDATE SET name = excluded.name RETURNING theme_id""",
                (c["name"], c["description"] or None, ref)).fetchone()[0]
            for n in c["members"]:
                if n in ids:
                    conn.execute("""INSERT INTO im.idea_themes (idea_id, theme_id, origin) VALUES (%s, %s, 'archive')
                                    ON CONFLICT DO NOTHING""", (ids[n], theme))
    return {"entries": len(entries), "added": added, "clusters": len(clusters)}


# --- Embeddings ----------------------------------------------------------------------------------------

def _vec(v) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def _embed_missing(conn, embedder, table: str, key: str, rows: list[tuple]) -> None:
    """rows: (id, text). Embeds those without a current embedding for this model and text."""
    have = {r[0]: r[1] for r in conn.execute(
        f"SELECT {key}, input_hash FROM im.{table} WHERE model_version = %s", (embedder.model_version,))}
    todo = [(i, t, hashlib.sha256(t.encode()).hexdigest()) for i, t in rows]
    todo = [(i, t, h) for i, t, h in todo if have.get(i) != h]
    if not todo:
        return
    vecs = embedder.embed([t for _, t, _ in todo])
    with conn.transaction():
        for (i, _, h), v in zip(todo, vecs, strict=True):
            conn.execute(
                f"""INSERT INTO im.{table} ({key}, model_version, embedding, input_hash) VALUES (%s, %s, %s::vector, %s)
                    ON CONFLICT ({key}) DO UPDATE SET model_version = excluded.model_version,
                      embedding = excluded.embedding, input_hash = excluded.input_hash""",
                (i, embedder.model_version, _vec(v), h))


def idea_text(title: str, statement: str) -> str:
    return f"{title}\n{statement}"


def _idea_matrix(conn, embedder) -> tuple[list, np.ndarray]:
    rows = conn.execute("SELECT idea_id, title, statement FROM im.ideas").fetchall()
    _embed_missing(conn, embedder, "idea_embeddings", "idea_id", [(r[0], idea_text(r[1], r[2])) for r in rows])
    got = conn.execute("SELECT idea_id, embedding::text FROM im.idea_embeddings WHERE model_version = %s",
                       (embedder.model_version,)).fetchall()
    if not got:
        return [], np.zeros((0, 1))
    return [r[0] for r in got], np.vstack([np.array(json.loads(r[1])) for r in got])


# --- Matching ------------------------------------------------------------------------------------------

def _pending_items(conn) -> list[tuple]:
    """Items on current episodes not matched yet, oldest first, with my latest verdict."""
    return conn.execute(
        """SELECT i.item_id, i.episode_id, i.kind, i.said_by, i.quote, i.gist, i.source_segment_ids,
                  (SELECT v.verdict FROM im.item_verdicts v WHERE v.item_id = i.item_id
                   ORDER BY v.decided_at DESC LIMIT 1)
           FROM im.items i JOIN im.episodes e USING (episode_id)
           WHERE e.current AND NOT EXISTS (SELECT 1 FROM im.item_matches m WHERE m.item_id = i.item_id)
           ORDER BY e.started_at, i.item_id""").fetchall()


def _ask(client, item: tuple, candidates: list[tuple]) -> tuple[dict, str, object]:
    _, _, kind, said_by, quote, gist, _, _ = item
    who = "me" if said_by == "me" else "someone else"   # no names leave the box
    lines = [f"Item ({kind}, said by {who}): “{quote}” — {gist}", "", "Candidates:"]
    lines += [f"{n}. {title}: {statement}" for n, (_, title, statement) in enumerate(candidates, 1)]
    text = "\n".join(lines)
    r = client.beta.messages.create(
        model=MATCH_MODEL, max_tokens=4000, system=MATCH_SYSTEM, messages=[{"role": "user", "content": text}],
        output_config={"effort": MATCH_EFFORT, "format": {"type": "json_schema", "schema": MATCH_SCHEMA}},
        betas=["server-side-fallback-2026-06-01"], fallbacks=[{"model": "claude-opus-4-8"}])
    if r.stop_reason == "refusal":
        return {"decision": "new", "candidate": 0, "title": "", "statement": ""}, text, r
    return json.loads(next(b.text for b in r.content if b.type == "text")), text, r


def _log(conn, item: tuple, text: str, r) -> int:
    from im.extract import _cost

    served = getattr(r, "model", MATCH_MODEL) or MATCH_MODEL
    return conn.execute(
        """INSERT INTO im.egress_log (episode_id, segment_ids, model, prompt_version, privacy_policy_version, payload,
             payload_sha256, request_id, stop_reason, served_by, input_tokens, output_tokens, cost_usd)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING egress_id""",
        (item[1], item[6], MATCH_MODEL, matcher_version(), egress.POLICY_VERSION, text,
         hashlib.sha256(text.encode()).hexdigest(), getattr(r, "_request_id", None), r.stop_reason, served,
         r.usage.input_tokens, r.usage.output_tokens, _cost(served, r.usage))).fetchone()[0]


def match_stage(conn, client, embedder, monthly_cap_usd: float = 20.0, limit: int | None = None) -> dict:
    import anthropic

    from im.extract import spent_this_month

    version = matcher_version()
    items = _pending_items(conn)
    stats = {"matcher": version, "pending": len(items), "new": 0, "same_as": 0, "evolves": 0, "skipped": 0,
             "stopped": None}
    if not items:
        return stats
    _embed_missing(conn, embedder, "item_embeddings", "item_id", [(i[0], f"{i[5]}\n{i[4]}") for i in items])
    vecs = {r[0]: np.array(json.loads(r[1])) for r in conn.execute(
        "SELECT item_id, embedding::text FROM im.item_embeddings WHERE item_id = ANY(%s)", ([i[0] for i in items],))}
    idea_ids, M = _idea_matrix(conn, embedder)
    info = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT idea_id, title, statement FROM im.ideas")}

    for item in items[:limit] if limit else items:
        item_id = item[0]
        if item[7] == "discard":
            conn.execute("INSERT INTO im.item_matches (item_id, decision, judged_by) VALUES (%s, 'skipped', %s)",
                         (item_id, version))
            stats["skipped"] += 1
            continue
        if spent_this_month(conn) >= monthly_cap_usd:
            stats["stopped"] = f"monthly cap of ${monthly_cap_usd:.2f} reached"
            break
        v = vecs[item_id]
        order = np.argsort(-(M @ v))[:MATCH_K] if len(idea_ids) else []
        candidates = [(idea_ids[j], *info[idea_ids[j]]) for j in order]
        egress_id = None
        if candidates:
            try:
                answer, text, r = _ask(client, item, candidates)
            except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
                stats["stopped"] = f"API unavailable, will retry next run: {type(e).__name__}"
                break
            egress_id = _log(conn, item, text, r)
        else:  # the very first idea: nothing to compare with
            answer = {"decision": "new", "candidate": 0, "title": "", "statement": ""}
        decision = answer["decision"]
        target = candidates[answer["candidate"] - 1][0] if 1 <= answer["candidate"] <= len(candidates) else None
        if decision != "new" and target is None:
            decision = "new"   # a match to no real candidate is no match
        with conn.transaction():
            if decision == "same_as":
                idea = target
                conn.execute("""INSERT INTO im.idea_evidence (idea_id, item_id, relation, judged_by)
                                VALUES (%s, %s, 'same_as', %s) ON CONFLICT DO NOTHING""", (idea, item_id, version))
            else:
                title = answer["title"].strip() or _title(item[5])
                statement = answer["statement"].strip() or item[5]
                idea = conn.execute("""INSERT INTO im.ideas (title, statement, origin) VALUES (%s, %s, 'captured')
                                       RETURNING idea_id""", (title, statement)).fetchone()[0]
                conn.execute("""INSERT INTO im.idea_evidence (idea_id, item_id, relation, judged_by)
                                VALUES (%s, %s, 'origin', %s)""", (idea, item_id, version))
                if decision == "evolves":
                    conn.execute("""INSERT INTO im.idea_links (a, b, kind, method) VALUES (%s, %s, 'evolves', %s)
                                    ON CONFLICT DO NOTHING""", (idea, target, version))
                info[idea] = (title, statement)
                vec = embedder.embed([idea_text(title, statement)])[0]
                conn.execute("""INSERT INTO im.idea_embeddings (idea_id, model_version, embedding, input_hash)
                                VALUES (%s, %s, %s::vector, %s)""",
                             (idea, embedder.model_version, _vec(vec),
                              hashlib.sha256(idea_text(title, statement).encode()).hexdigest()))
                idea_ids.append(idea)
                M = np.vstack([M, vec]) if len(M) and M.shape[1] == len(vec) else vec[None, :]
            conn.execute("""INSERT INTO im.item_matches (item_id, decision, idea_id, judged_by, egress_id)
                            VALUES (%s, %s, %s, %s, %s)""", (item_id, decision, idea, version, egress_id))
        stats[decision] += 1
    stats["related_links"] = relate(conn, embedder)
    return stats


def relate(conn, embedder) -> int:
    """Rebuild the derived `related` edges: each idea's nearest neighbours above RELATED_MIN."""
    ids, M = _idea_matrix(conn, embedder)
    method = f"embedding:{embedder.model_version};k={RELATED_K};min={RELATED_MIN}"
    with conn.transaction():
        conn.execute("DELETE FROM im.idea_links WHERE kind = 'related'")
        if len(ids) < 2:
            return 0
        S = M @ M.T
        np.fill_diagonal(S, -1)
        edges = set()
        for i in range(len(ids)):
            for j in np.argsort(-S[i])[:RELATED_K]:
                if S[i, j] >= RELATED_MIN:
                    edges.add((min(i, j), max(i, j), float(S[i, j])))
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO im.idea_links (a, b, kind, weight, method) VALUES (%s, %s, 'related', %s, %s)
                               ON CONFLICT DO NOTHING""",
                            [(ids[i], ids[j], round(w, 4), method) for i, j, w in edges])
    return len(edges)


def status(conn) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "ideas": q("SELECT count(*) FROM pub.ideas"),
        "archive": q("SELECT count(*) FROM pub.ideas WHERE origin = 'archive'"),
        "captured": q("SELECT count(*) FROM pub.ideas WHERE origin = 'captured'"),
        "with_more_than_one_evidence": q("SELECT count(*) FROM pub.ideas WHERE n_evidence > 1"),
        "connections": dict(conn.execute("SELECT kind, count(*) FROM pub.connections GROUP BY kind").fetchall()),
        "unmatched_items": len(_pending_items(conn)),
    }
