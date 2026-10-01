"""`im show <episode>`: an episode's metadata and transcript."""

import json

from im.config import SegmentConfig


def find_episode(conn, prefix: str):
    rows = conn.execute(
        """SELECT episode_id, session_id, source, started_at, ended_at, kind, n_segments, n_speakers,
                  self_segments, current, retired_at, schema_version, stage_version, input_hash
           FROM im.episodes WHERE episode_id::text LIKE %s ORDER BY started_at""",
        (prefix.lower() + "%",)).fetchall()
    if not rows:
        raise SystemExit(f"no episode matches {prefix!r}")
    if len(rows) > 1:
        raise SystemExit(f"{prefix!r} is ambiguous: " + ", ".join(str(r[0]) for r in rows[:5]))
    return rows[0]


def _speaker(label, speaker_id, is_self, conf, cfg: SegmentConfig) -> str:
    if is_self and conf is not None and conf >= cfg.self_conf_min:
        return "me"
    who = label if speaker_id is None else f"{label}/{str(speaker_id)[:8]}"
    return f"{who} (me? conf {conf:.2f})" if is_self else who


def show(conn, prefix: str, cfg: SegmentConfig) -> str:
    (eid, session, source, start, end, kind, n, nspk, nself, current, retired,
     schema_v, stage_v, ihash) = find_episode(conn, prefix)
    out = [
        f"episode   {eid}" + ("" if current else f"  [retired {retired:%Y-%m-%d %H:%M}]"),
        f"session   {session} ({source})",
        f"time      {start:%Y-%m-%d %H:%M:%S} → {end:%H:%M:%S} ({(end - start).total_seconds():.0f} s)",
        f"kind      {kind}: {n} segments, {nspk} speakers, {nself} mine",
        f"versions  schema {schema_v}, {stage_v}",
        f"input     {ihash[:16]}…",
        "",
    ]
    segs = conn.execute(
        """SELECT s.segment_id, s.started_at, s.speaker_label, s.speaker_id, s.is_self, s.speaker_conf,
                  s.text, s.audio_ref
           FROM im.episode_segments es JOIN hearsay.segments s USING (segment_id)
           WHERE es.episode_id = %s ORDER BY es.ord""", (eid,)).fetchall()
    for seg_id, at, label, spk_id, is_self, conf, text, audio in segs:
        out.append(f"{at:%H:%M:%S}  {_speaker(label, spk_id, is_self, conf, cfg):<12} {text}")
        out.append(f"          segment {seg_id}  audio {json.dumps(audio)}")
    return "\n".join(out)
