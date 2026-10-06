"""Correcting ideas: my wording wins everywhere the idea is used, and Claude's original is kept."""

import json

import psycopg
import pytest

from im import extract, lattice, themes

from helpers import run, table
from test_extract import FakeClaude
from test_lattice import FakeMatcher, items_for
from test_themes import FakeThemer
from test_triage import HashEmbedder


def event(admin, kind, idea_id, payload):
    with admin.transaction():
        admin.execute("SET LOCAL ROLE lattice_app")
        admin.execute("INSERT INTO pub.feedback_events (kind, idea_id, payload) VALUES (%s, %s, %s)",
                      (kind, idea_id, json.dumps(payload)))


def idea_where(conn, title_like):
    return table(conn, "SELECT idea_id FROM im.ideas WHERE title LIKE %s", title_like)[0][0]


def test_my_wording_wins_and_the_original_stays(seeded_lattice, admin):
    pipe = seeded_lattice
    idea = idea_where(pipe, "Something new")
    event(admin, "idea_correct", idea, {"title": "Garden telemetry", "statement": "Soil sensors over LoRa, solar-powered."})
    event(admin, "idea_note", idea, {"note": "This is about my backyard, not a product."})
    assert table(pipe, """SELECT title, statement, claude_title, my_note, corrected FROM pub.ideas
                          WHERE idea_id = %s""", idea) == \
        [("Garden telemetry", "Soil sensors over LoRa, solar-powered.", "Something new",
          "This is about my backyard, not a product.", True)]
    event(admin, "idea_correct", idea, {"title": ""})   # an empty field reverts to Claude's
    assert table(pipe, "SELECT title, statement, corrected FROM pub.ideas WHERE idea_id = %s", idea) == \
        [("Something new", "Soil sensors over LoRa, solar-powered.", True)]


def test_matching_and_embeddings_use_the_correction(seeded_lattice, admin, cfg, stream):
    pipe = seeded_lattice
    coffee = idea_where(pipe, "%coffee%")
    before = table(pipe, "SELECT input_hash FROM im.idea_embeddings WHERE idea_id = %s", coffee)
    event(admin, "idea_correct", coffee, {"statement": "Coffee runs are where the real meeting happens."})
    event(admin, "idea_note", coffee, {"note": "office ritual"})
    lattice.relate(pipe, HashEmbedder())   # rebuilding the matrix re-embeds changed text
    assert table(pipe, "SELECT input_hash FROM im.idea_embeddings WHERE idea_id = %s", coffee) != before
    # A new capture about coffee: the matcher is shown my statement and note, not Claude's.
    stream.conversations["c-tv"].turns.append(stream.conversations["c-conv"].turns[2])
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(items_for))
    claude = FakeMatcher()
    lattice.match_stage(pipe, claude, HashEmbedder())
    sent = json.dumps([c["messages"] for c in claude.calls])
    assert "Coffee runs are where the real meeting happens. (my note: office ritual)" in sent


def test_a_correction_refiles_the_idea_but_keeps_archive_themes(seeded_lattice, admin):
    pipe = seeded_lattice
    themes.file_stage(pipe, FakeThemer())
    evolved = idea_where(pipe, "Broken builds%")
    archived = idea_where(pipe, "%coffee%")
    assert table(pipe, "SELECT origin FROM im.idea_themes WHERE idea_id = %s", evolved) == [("assigned",)]
    for idea in (evolved, archived):
        event(admin, "idea_correct", idea, {"statement": "corrected"})
    assert themes.refile_corrected(pipe) == 1   # the archive idea had no filer check: nothing to redo
    assert table(pipe, "SELECT count(*) FROM im.idea_themes WHERE idea_id = %s", evolved) == [(0,)]
    assert table(pipe, "SELECT origin FROM im.idea_themes WHERE idea_id = %s", archived) == [("archive",)]
    assert themes.file_stage(pipe, FakeThemer())["pending"] >= 1


def test_an_idea_event_must_name_its_idea(seeded_lattice, admin):
    with pytest.raises(psycopg.errors.CheckViolation):
        with admin.transaction():
            admin.execute("SET LOCAL ROLE lattice_app")
            admin.execute("INSERT INTO pub.feedback_events (kind, payload) VALUES ('idea_correct', '{}')")
