"""`im show <episode>`: an episode's metadata and transcript."""


def find_episode(conn, prefix: str):
    rows = conn.execute(
        """SELECT episode_id, session_id, started_at, ended_at, kind, n_segments, n_speakers,
                  self_segments, current, retired_at, schema_version, stage_version, input_hash
           FROM im.episodes WHERE episode_id::text LIKE %s ORDER BY started_at""",
        (prefix.lower() + "%",)).fetchall()
    if not rows:
        raise SystemExit(f"no episode matches {prefix!r}")
    if len(rows) > 1:
        raise SystemExit(f"{prefix!r} is ambiguous: " + ", ".join(str(r[0]) for r in rows[:5]))
    return rows[0]


def show(conn, prefix: str) -> str:
    (eid, conversation, start, end, kind, n, nspk, nself, current, retired,
     schema_v, stage_v, ihash) = find_episode(conn, prefix)
    places = conn.execute(
        """SELECT string_agg(p.name, ' → ' ORDER BY p.start)
           FROM im.source_conversations c,
                jsonb_to_recordset(c.places) AS p(name text, start timestamptz, "end" timestamptz)
           WHERE c.conversation_id = %s AND p.start < %s AND p."end" > %s""", (conversation, end, start)).fetchone()[0]
    out = [
        f"episode       {eid}" + ("" if current else f"  [retired {retired:%Y-%m-%d %H:%M}]"),
        f"conversation  {conversation} (audio is in Hearsay under this id)",
        f"time          {start:%Y-%m-%d %H:%M:%S} → {end:%H:%M:%S} UTC ({(end - start).total_seconds():.0f} s)",
        *([f"place         {places}"] if places else []),
        f"kind          {kind}: {n} segments, {nspk} speakers, {nself} mine",
        f"versions      schema {schema_v}, {stage_v}",
        f"input         {ihash[:16]}…",
        "",
    ]
    segs = conn.execute(
        """SELECT s.started_at, s.speaker_label, s.speaker_kind, s.speaker_basis, s.text,
                  (SELECT m.utterance_id FROM im.source_members m WHERE m.segment_id = s.segment_id)
           FROM im.episode_segments es JOIN im.source_segments s USING (segment_id)
           WHERE es.episode_id = %s ORDER BY es.ord""", (eid,)).fetchall()
    for at, label, kind, basis, text, utterance_id in segs:
        who = label if basis in (None, "voice", "named", "none", "cluster") else f"{label} ({basis})"
        out.append(f"{at:%H:%M:%S}  {who:<14} {text}")
        out.append(f"          {utterance_id or 'no longer in the stream'}")
    return "\n".join(out)
