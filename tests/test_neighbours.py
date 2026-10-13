"""Ideas like this: each idea's nearest ideas, and search by meaning with a vector Lattice computes."""

import json

import psycopg
import pytest

from im import lattice

from helpers import table
from test_review import as_lattice
from test_triage import HashEmbedder


def vec(text):
    return "[" + ",".join(f"{x:.6f}" for x in HashEmbedder().embed([text])[0]) + "]"


def test_neighbours_and_search_by_meaning(seeded_lattice, admin):
    pipe = seeded_lattice
    lattice.relate(pipe, HashEmbedder())   # embeds every idea
    assert as_lattice(admin, "SELECT pub.embedding_model()") == [("hash-bow/64",)]
    shown = table(pipe, "SELECT count(*) FROM pub.ideas")[0][0]
    rows = as_lattice(admin, "SELECT idea_id, other_id, similarity, rank FROM pub.idea_neighbours")
    assert len(rows) == shown * min(10, shown - 1) and all(a != b for a, b, *_ in rows)
    for idea in {r[0] for r in rows}:
        mine = sorted((r[3], r[2]) for r in rows if r[0] == idea)
        assert [r for r, _ in mine] == list(range(1, shown)) and [s for _, s in mine] == sorted(
            (s for _, s in mine), reverse=True)

    coffee = table(pipe, "SELECT idea_id FROM pub.ideas WHERE statement LIKE '%%coffee%%'")[0][0]
    hits = as_lattice(admin, "SELECT idea_id, similarity FROM pub.ideas_near(%s::vector, 2)",
                      vec("The coffee order is the real meeting."))
    assert len(hits) == 2 and hits[0][0] == coffee and hits[0][1] > 0.9 > hits[1][1]   # the idea embeds its title too
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        as_lattice(admin, "SELECT * FROM pub.ideas_near('[1,0,0]'::vector)")


def test_hidden_ideas_are_never_neighbours(seeded_lattice, admin):
    pipe = seeded_lattice
    lattice.relate(pipe, HashEmbedder())
    lora = table(pipe, "SELECT idea_id FROM pub.ideas WHERE title = 'Something new'")[0][0]
    item = table(pipe, "SELECT item_id FROM im.idea_evidence WHERE idea_id = %s", lora)[0][0]
    pipe.execute("INSERT INTO pub.feedback_events (kind, item_id, payload) VALUES ('item_discard', %s, %s)",
                 (item, json.dumps({})))
    from im import feedback
    feedback.apply_item_feedback(pipe)   # its only capture discarded: the idea stops showing
    assert not as_lattice(admin, "SELECT 1 FROM pub.idea_neighbours WHERE %s IN (idea_id, other_id)", lora)
    assert lora not in [r[0] for r in as_lattice(admin, "SELECT idea_id FROM pub.ideas_near(%s::vector, 50)",
                                                  vec("garden sensors LoRa"))]
