"""Edge citations (M2, migration 035): every connection and every lineage link cites the observations it rests on.

An edge cites the origin observations of its two ends: what each idea's statement was made from. Cite what exists:
an end with no observation adds none, so an edge between two such ideas cites nothing, and its `method` says
where it came from. (Archive ideas cite the messages they came from once `im seed-observations` has read them.) Citations are derived: `cite` recomputes them whenever
observations or edges change, and `uncite_missing` drops ids that are gone (forgetting, an emptied observation).
"""


# The observations each idea's statement was made from: its origin captures'. A reused read copies the origin
# relation to the new capture, which joins the same observation, so there's usually one. An archive idea or one I
# added was made from its observations that aren't speech (migration 036): my messages, or what I typed.
ORIGIN_OBSERVATIONS = """
SELECT ev.idea_id, i.observation_id FROM im.idea_evidence ev JOIN im.items i USING (item_id)
WHERE ev.relation = 'origin' AND i.observation_id IS NOT NULL
UNION ALL
SELECT o.idea_id, o.observation_id FROM im.observations o WHERE o.source <> 'speech'"""


# The two cited tables (migration 045): each with its key and its two ends.
EDGES = (("im.connections", ("a", "b")), ("im.lineage", ("from_id", "to_id", "kind")))


def cite(conn) -> int:
    """Every edge cites the origin observations of its two ends (migration 035). An end without one (the archive,
    an idea I added) adds nothing. Recomputed each run, after observations and matching; returns the edges changed."""
    changed = 0
    for table, key in EDGES:
        ends, cols = f"({key[0]}, {key[1]})", ", ".join(key)
        changed += conn.execute(f"""
            WITH origin AS ({ORIGIN_OBSERVATIONS}),
            cited AS (
              SELECT {cols},
                     ARRAY(SELECT DISTINCT o.observation_id FROM origin o WHERE o.idea_id IN {ends} ORDER BY 1) AS ids
              FROM {table})
            UPDATE {table} l SET cited_observation_ids = c.ids FROM cited c
            WHERE ({", ".join(f"l.{k}" for k in key)}) = ({", ".join(f"c.{k}" for k in key)})
              AND l.cited_observation_ids <> c.ids""").rowcount
    return changed


def uncite_missing(conn) -> int:
    """Drop cited observation ids that no longer exist (forgotten, or left with no capture) from edges."""
    return sum(conn.execute(f"""
        UPDATE {table} l SET cited_observation_ids = ARRAY(
          SELECT c FROM unnest(l.cited_observation_ids) c
          WHERE EXISTS (SELECT 1 FROM im.observations o WHERE o.observation_id = c) ORDER BY 1)
        WHERE EXISTS (SELECT 1 FROM unnest(l.cited_observation_ids) c
                      WHERE NOT EXISTS (SELECT 1 FROM im.observations o WHERE o.observation_id = c))""").rowcount
               for table, _ in EDGES)
