"""Edge citations (migration 035): every edge cites the origin observations of its ends, where they exist."""

import uuid

from im import citations, extract, lattice, observations

from helpers import HashEmbedder, assert_invariants, run, table
from test_extract import FakeClaude
from test_lattice import JUDGED, FakeMatcher, items_for, legacy_evolves, seeded  # noqa: F401

# Every edge with what the old table called its kind, or `judged` for a connection a judge made.
EDGES = """SELECT a, b, CASE WHEN method LIKE 'embedding:%%' THEN 'related' ELSE 'judged' END AS kind,
                  cited_observation_ids FROM im.connections
           UNION ALL SELECT from_id, to_id, kind, cited_observation_ids FROM im.lineage"""


def edges(conn, kinds):
    return dict(table(conn, f"SELECT kind, cited_observation_ids FROM ({EDGES}) e WHERE kind = ANY(%s)", list(kinds)))


BUILD_OBSERVATION = """SELECT i.observation_id FROM im.items i WHERE i.quote LIKE '%%build broke%%'"""


def matched(conn, cfg, stream, observe_first=True):
    """Captures matched, and the evolves edge matcher v1 would have written between them."""
    run(conn, cfg, stream)
    extract.run_stage(conn, FakeClaude(items_for))
    if observe_first:
        observations.assign_stage(conn)
    lattice.match_stage(conn, FakeMatcher(), HashEmbedder())
    legacy_evolves(conn)


def test_an_edge_cites_what_exists(seeded, cfg, stream):
    matched(seeded, cfg, stream)
    (build,) = table(seeded, BUILD_OBSERVATION)[0]
    assert edges(seeded, ["judged", "evolves"]) == {
        "judged": [],           # two ideas I added: nothing said behind either
        "evolves": [build]}     # the build capture's idea evolves an idea I added: only its end cites
    assert_invariants(seeded)   # and every related edge touching a captured idea cites it


def test_observations_assigned_after_matching_are_cited(seeded, cfg, stream):
    matched(seeded, cfg, stream, observe_first=False)
    assert edges(seeded, ["evolves"]) == {"evolves": []}
    observations.assign_stage(seeded)
    (build,) = table(seeded, BUILD_OBSERVATION)[0]
    assert edges(seeded, ["evolves"]) == {"evolves": [build]}
    assert citations.cite(seeded) == 0   # settled
    assert_invariants(seeded)


def test_forgetting_takes_the_edges_made_from_it(seeded, cfg, stream):
    matched(seeded, cfg, stream)
    stream.forget(0, 6)   # the build line: its idea goes, and the evolves edge with it
    stream.write(stream.dir)
    run(seeded, cfg, stream)
    assert table(seeded, "SELECT count(*) FROM im.lineage WHERE kind = 'evolves'") == [(0,)]
    assert table(seeded, "SELECT count(*) FROM im.connections WHERE method = %s", JUDGED) == [(1,)]
    assert_invariants(seeded)


def test_a_citation_to_an_observation_thats_gone_is_dropped(seeded, cfg, stream):
    matched(seeded, cfg, stream)
    (build,) = table(seeded, BUILD_OBSERVATION)[0]
    seeded.execute("UPDATE im.lineage SET cited_observation_ids = %s WHERE kind = 'evolves'",
                   ([build, uuid.uuid4()],))
    assert citations.uncite_missing(seeded) == 1
    assert edges(seeded, ["evolves"]) == {"evolves": [build]}


def test_an_idle_run_keeps_its_citations(seeded, cfg, stream, monkeypatch):
    """A run with nothing new to match still rebuilds the related edges, and must cite them (seen on the NAS)."""
    monkeypatch.setattr(lattice, "RELATED_MIN", -1.0)   # every idea gets its nearest neighbors
    matched(seeded, cfg, stream)
    before = table(seeded, f"SELECT * FROM ({EDGES}) e ORDER BY 1, 2, 3")
    assert any(kind == "related" and cited for _, _, kind, cited in before)
    assert lattice.match_stage(seeded, FakeMatcher(), HashEmbedder())["pending"] == 0
    assert table(seeded, f"SELECT * FROM ({EDGES}) e ORDER BY 1, 2, 3") == before
    assert_invariants(seeded)
