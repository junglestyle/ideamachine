"""Weight and proposals (M2, docs/design/m2-observations-candidates-ideas.md).

A cluster's weight is how much it contributes to links and synthesis. weight-1 adds up:
- my independent recurrences: my observations in distinct conversations, or in one conversation at least
  `recurrence_minutes` apart (two repeats closer than that count once);
- a smaller amount per observation by diarization or not me, capped;
- links to canonical ideas, and a boost when the cluster is itself canonical.
Pins add nothing; verdicts add nothing.

Each run proposes the most recently said candidates to me, up to `proposals_per_week`: weight orders nothing
(decision 0007; no signal predicted what I keep). I keep each one (canonical or pinned) or dismiss it; a dismissed
candidate comes back only once its weight has grown by `repropose_margin`. Only I make ideas: nothing here changes
how a cluster is kept.
"""

import json
from dataclasses import fields, replace

from im.config import WeightConfig


def _signals(conn) -> dict:
    """Per cluster: my observations (conversation, when said), the others' count, canonical links, canonical.

    Canonical links are connections shown (migration 045), mine included, one per pair; lineage doesn't count.
    Only current captures count (im.idea_observations), placed by their own segments: a superseded read's segments can carry a
    conversation id from before a re-key, which would count one conversation twice."""
    out = {r[0]: {"mine": [], "others": 0, "canonical_links": r[2], "canonical": r[1] == "canonical"}
           for r in conn.execute(
               """SELECT k.idea_id, k.kept_as,
                         (SELECT count(*) FROM im.shown_connections l JOIN im.idea_kept o
                            ON o.idea_id = CASE WHEN l.a = k.idea_id THEN l.b ELSE l.a END
                          WHERE (l.a = k.idea_id OR l.b = k.idea_id) AND o.kept_as = 'canonical')
                  FROM im.idea_kept k""")}
    # Speech and not (migration 036): what I said into the ChatGPT Idea Machine, or typed adding an idea, recurs too.
    for idea_id, speaker, conversation, said_at in conn.execute(
            "SELECT idea_id, speaker, conversation, said_at FROM im.idea_observations"):
        if speaker == "me":
            out[idea_id]["mine"].append((conversation, said_at))
        else:
            out[idea_id]["others"] += 1
    return out


def recurrences(mine: list, minutes: float) -> int:
    """Distinct conversations, plus repeats in one conversation at least `minutes` after the last one counted."""
    n, last = 0, {}
    for conversation, said_at in sorted(mine):
        prev = last.get(conversation)
        if prev is None or (said_at - prev).total_seconds() >= minutes * 60:
            n += 1
            last[conversation] = said_at
    return n


def weigh(sig: dict, w: WeightConfig) -> tuple[float, dict]:
    signals = {"recurrences": recurrences(sig["mine"], w.recurrence_minutes), "others": sig["others"],
               "canonical_links": sig["canonical_links"], "canonical": sig["canonical"]}
    weight = (signals["recurrences"] + w.others * min(signals["others"], w.others_cap)
              + w.canonical_link * signals["canonical_links"] + w.canonical * signals["canonical"])
    return round(weight, 3), signals


def weights(conn, w: WeightConfig) -> dict:
    return {idea_id: weigh(sig, w) for idea_id, sig in _signals(conn).items()}


def _eligible(conn, computed: dict, w: WeightConfig) -> list:
    """Candidates I could be shown next, the most recently said first (decision 0007): no open proposal, and not
    dismissed at about this weight."""
    rows = conn.execute(
        """SELECT m.idea_id,
                  EXISTS (SELECT 1 FROM im.proposals p WHERE p.idea_id = m.idea_id AND p.outcome IS NULL),
                  (SELECT p.weight FROM im.proposals p WHERE p.idea_id = m.idea_id AND p.outcome = 'dismissed'
                   ORDER BY p.decided_at DESC LIMIT 1),
                  (SELECT extract(epoch FROM max(ev.said_at)) FROM pub.evidence ev WHERE ev.idea_id = m.idea_id)
           FROM im.idea_maturity m WHERE m.maturity = 'candidate'""").fetchall()
    out = [(computed[i][0], i, float(said or 0)) for i, open_, dismissed, said in rows
           if not open_ and (dismissed is None or computed[i][0] >= dismissed + w.repropose_margin)]
    return [(wt, i) for wt, i, _ in sorted(out, key=lambda r: (-r[2], str(r[1])))]


def _settle(conn) -> dict:
    """Close open proposals I've acted on: kept (any way), or dismissed after it was made."""
    kept = conn.execute(
        """UPDATE im.proposals p SET outcome = k.kept_as, decided_at = now()
           FROM im.idea_kept k WHERE k.idea_id = p.idea_id AND p.outcome IS NULL AND k.kept_as IS NOT NULL""").rowcount
    dismissed = conn.execute(
        """UPDATE im.proposals p SET outcome = 'dismissed', decided_at = f.at
           FROM pub.feedback_events f
           WHERE f.idea_id = p.idea_id AND f.kind = 'idea_dismiss' AND f.at >= p.made_at
             AND p.outcome IS NULL""").rowcount
    return {"kept": kept, "dismissed": dismissed}


def stage(conn, w: WeightConfig) -> dict:
    version = w.formula_version
    computed = weights(conn, w)
    with conn.transaction():
        old = dict(conn.execute("SELECT idea_id, weight FROM im.weights WHERE formula_version = %s",
                                (version,)).fetchall())
        changed = [(i, version, wt, json.dumps(sig)) for i, (wt, sig) in computed.items() if old.get(i) != wt]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO im.weights (idea_id, formula_version, weight, signals) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (formula_version, idea_id) DO UPDATE
                   SET weight = EXCLUDED.weight, signals = EXCLUDED.signals, computed_at = now()""", changed)
        settled = _settle(conn)
        made = conn.execute("SELECT count(*) FROM im.proposals WHERE made_at > now() - interval '7 days'").fetchone()[0]
        new = _eligible(conn, computed, w)[:max(0, w.proposals_per_week - made)]
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO im.proposals (idea_id, weight, formula_version) VALUES (%s, %s, %s)",
                            [(i, wt, version) for wt, i in new])
    return {"formula_version": version, "weights_changed": len(changed), "proposed": len(new)} | settled


def parse_overrides(pairs: list[str]) -> dict:
    types = {f.name: f.type for f in fields(WeightConfig)}
    out = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        key = key.strip().replace("-", "_")
        if key not in types:
            raise ValueError(f"unknown knob {key!r}; known: {', '.join(types)}")
        out[key] = int(value) if types[key] in (int, "int") else float(value)
    return out


def tune(conn, current: WeightConfig, overrides: dict, top: int = 10) -> dict:
    """What changing the knobs would do, against the current settings. Writes nothing."""
    trial = replace(current, **overrides)
    before, after = weights(conn, current), weights(conn, trial)
    titles = dict(conn.execute("SELECT idea_id, title FROM im.idea_text").fetchall())
    moved = sorted(((after[i][0] - before[i][0], titles.get(i, str(i)), before[i][0], after[i][0])
                    for i in before if before[i][0] != after[i][0]), key=lambda r: -abs(r[0]))
    proposals = lambda c, w: [f"{wt:g}  {titles.get(i, i)}" for wt, i in _eligible(conn, c, w)[:top]]
    toml = "[weight]\n" + "\n".join(f"{f.name} = {getattr(trial, f.name)!r}" for f in fields(trial)
                                    if getattr(trial, f.name) != getattr(WeightConfig(), f.name))
    return {"formula_version": {"now": current.formula_version, "trial": trial.formula_version},
            "weights_changed": len(moved),
            "biggest_moves": [f"{b:g} -> {a:g}  {t}" for _, t, b, a in moved[:top]],
            "next_proposals": {"now": proposals(before, current), "trial": proposals(after, trial)},
            "im.toml": toml}
