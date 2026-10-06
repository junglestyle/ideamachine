"""What leaves the box, and how (docs/decisions/0004-claude-extraction.md).

Policy B (`POLICY_VERSION`):
- Everyone's words go out verbatim, mine and other people's.
- Speakers are pseudonymized per request: "me", "unknown", and S1, S2... in order of first appearance.
  The mapping back to names stays local and is applied to what comes back.
- Names *spoken* in the text are not scrubbed (that needs entity extraction, ROADMAP Phase 3).
- Nothing from the projects registry goes out.
- Every request is logged in im.egress_log with the segment IDs it carried.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from im.triage import _part_of_day

POLICY_VERSION = "B1;speakers-pseudonymized;spoken-names-not-scrubbed;no-registry"


@dataclass(frozen=True)
class Payload:
    episode_id: object
    text: str                   # exactly what goes out as the user message
    segment_ids: list           # line n of the transcript is segment_ids[n - 1]
    speakers: dict              # pseudonym -> local speaker label (never sent)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def build(conn, episode_id) -> Payload:
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

    alias: dict = {}   # local label -> pseudonym
    lines = []
    for n, (_, label, kind, text, _, _) in enumerate(rows, 1):
        if label not in alias:
            alias[label] = ("me" if kind == "owner" else "unknown" if kind == "unknown"
                            else f"S{sum(1 for a in alias.values() if a.startswith('S')) + 1}")
        lines.append(f"[{n}] {alias[label]}: {text}")

    start: datetime = min(r[4] for r in rows).astimezone()
    end: datetime = max(r[5] for r in rows).astimezone()
    minutes = max(1, round((end - start).total_seconds() / 60))
    context = (f"{start:%A %Y-%m-%d}, {start:%H:%M}-{end:%H:%M} ({_part_of_day(start.hour)}), {minutes} min. "
               f"Speakers: {', '.join(dict.fromkeys(alias.values()))}.")
    if taps:
        context += " I tapped the pendant at " + ", ".join(f"{t.astimezone():%H:%M:%S}" for t in taps) + "."
    text = f"Context: {context}\n\nTranscript:\n" + "\n".join(lines)
    return Payload(episode_id, text, [r[0] for r in rows], {v: k for k, v in alias.items()})
