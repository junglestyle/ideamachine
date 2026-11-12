"""Merging two clusters that make one point: `im merge KEEP OTHER`."""

import json
import uuid

import pytest

from im import extract, fixtures, lattice

from helpers import assert_invariants, run, table
from test_extract import FakeClaude
from test_lattice import FakeMatcher, items_for, lat_setup
from test_triage import HashEmbedder

EVENT = "INSERT INTO pub.feedback_events (kind, idea_id, other_idea_id, payload, source) VALUES (%s, %s, %s, %s, 'cli')"


def idea(conn, word: str):
    (got,) = table(conn, "SELECT idea_id FROM im.idea_text WHERE title LIKE %s", f"%{word}%")[0]
    return got


def merge(conn, monkeypatch, capsys, keep, other) -> dict:
    from im import cli

    monkeypatch.setattr(cli.db, "pipeline_dsn", lambda: conn.info.dsn + " password=" + conn.info.password)
    cli.main(["merge", str(keep), str(other)])
    return json.loads(capsys.readouterr().out)


def cards(conn) -> dict:
    return {r[0]: r[1:] for r in table(
        conn, "SELECT idea_id, title, statement, observations, by_others, kept_as FROM pub.cards")}


def links(conn) -> dict:
    return {(a, b): (kind, kinds) for a, b, kind, kinds in table(conn, "SELECT a, b, kind, kinds FROM pub.card_links")}


def test_a_merge_moves_captures_observations_and_connections_and_keeps_my_statement(seeded_lattice, monkeypatch,
                                                                                   capsys):
    conn = seeded_lattice
    builds, coffee, grape, lora = (idea(conn, w) for w in ("Broken builds", "coffee", "grape", "Something new"))
    conn.execute(EVENT, ("idea_correct", builds, None, json.dumps({"statement": "Broken builds tell on the system."})))
    conn.execute(EVENT, ("idea_correct", coffee, None, json.dumps({"statement": "Who orders the coffee runs it."})))
    conn.execute(EVENT, ("idea_note", coffee, None, json.dumps({"note": "Office rituals again."})))
    conn.execute(EVENT, ("link", lora, coffee, "{}"))
    # A judge's connection: coffee supports grape. One between the two merged, and a verdict on coffee.
    a, b = sorted((coffee, grape))
    conn.execute("INSERT INTO im.connections (a, b, method) VALUES (%s, %s, 'connect-1:test')", (a, b))
    conn.execute("""INSERT INTO im.connection_kinds (a, b, kind, direction, method)
                    VALUES (%s, %s, 'supports', %s, 'connect-1:test')""", (a, b, "a>b" if a == coffee else "b>a"))
    a, b = sorted((coffee, builds))
    conn.execute("INSERT INTO im.connections (a, b, method) VALUES (%s, %s, 'connect-1:test')", (a, b))
    conn.execute("""INSERT INTO im.connection_verdicts (a, b, verdict, belongs, method, input_hash)
                    VALUES (%s, %s, 'related', 0.9, 'connect-1:test', 'h')""", (a, b))
    before = cards(conn)
    assert before[coffee][4] == "canonical" and before[builds][4] is None

    out = merge(conn, monkeypatch, capsys, builds, coffee)

    after = cards(conn)
    assert coffee not in after and set(after) == set(before) - {coffee}
    title, statement, observations, by_others, kept_as = after[builds]
    assert (title, statement) == (before[builds][0], "Broken builds tell on the system.")   # mine stays
    assert observations == before[builds][2] + before[coffee][2] and by_others == before[coffee][3] == 1
    assert kept_as == "canonical"   # I kept coffee, so the cluster it's part of now stays kept
    quotes = {q for (q,) in table(conn, "SELECT quote FROM pub.card_evidence WHERE idea_id = %s", builds)}
    assert "Who's got the coffee order?" in quotes
    assert table(conn, "SELECT themes FROM pub.ideas WHERE idea_id = %s", builds) == [(["Models and signals"],)]
    shown = links(conn)
    assert not any(coffee in pair for pair in shown)
    a, b = sorted((builds, grape))
    assert shown[(a, b)] == ("related", ["supports"])
    assert table(conn, "SELECT direction FROM im.connection_kinds WHERE a = %s AND b = %s", a, b) == \
        [("a>b" if a == builds else "b>a",)]   # still the side that was coffee supporting grape
    assert shown[tuple(sorted((builds, lora)))] == ("mine", [])   # my link on coffee is on builds now
    assert table(conn, "SELECT count(*) FROM im.connection_verdicts WHERE %s IN (a, b)", coffee) == [(0,)]
    assert out["merged_away_text"] == {"title": before[coffee][0], "statement": "Who orders the coffee runs it.",
                                       "my_note": "Office rituals again."}
    assert table(conn, "SELECT keep_id, title, statement, my_note FROM im.idea_merges WHERE other_id = %s", coffee) == \
        [(builds, before[coffee][0], "Who orders the coffee runs it.", "Office rituals again.")]
    assert_invariants(conn)


def test_a_later_capture_joins_the_kept_cluster_never_the_one_merged_away(seeded, cfg, stream, monkeypatch, capsys):
    (keep,) = seeded.execute("""INSERT INTO im.ideas (title, statement, origin, segment_ids)
                                VALUES ('Coffee runs the meeting', 'Who gets the coffee order runs the meeting.',
                                        'captured', '{}') RETURNING idea_id""").fetchone()
    lat_setup(seeded, cfg, stream)   # the coffee line makes the same point as both: they go to my merge review
    ((ideas,),) = table(seeded, "SELECT idea_ids FROM im.merge_review WHERE kind = 'duplicate'")
    (other,) = set(ideas) - {keep}
    merge(seeded, monkeypatch, capsys, keep, other)
    assert table(seeded, "SELECT merge_id IS NOT NULL FROM im.merge_review") == [(True,)]   # resolved

    stream.conversations["c-later"] = fixtures.Conversation(
        [fixtures.Turn(20000, 20006, "Whoever has the coffee order runs the meeting.", fixtures.ME, "SPEAKER_00")])
    stream.write(stream.dir)
    run(seeded, cfg, stream)
    extract.run_stage(seeded, FakeClaude(items_for))
    claude = FakeMatcher()
    stats = lattice.match_stage(seeded, claude, HashEmbedder())
    assert (stats["same_as"], stats["possible_duplicates"]) == (1, 0)
    assert "The coffee order is the real meeting." not in json.dumps([c["messages"] for c in claude.calls])
    assert table(seeded, """SELECT m.idea_id FROM im.item_matches m JOIN im.items i USING (item_id)
                            WHERE i.quote LIKE 'Whoever%%'""") == [(keep,)]
    assert table(seeded, "SELECT count(*) FROM pub.card_evidence WHERE idea_id = %s", keep) == [(2,)]
    assert_invariants(seeded)


def test_merging_refuses_one_cluster_twice_unknown_ids_and_one_already_merged(seeded_lattice, monkeypatch, capsys):
    conn = seeded_lattice
    builds, coffee, grape = (idea(conn, w) for w in ("Broken builds", "coffee", "grape"))
    for keep, other, why in [(builds, builds, "into itself"), (builds, uuid.uuid4(), "no cluster"),
                             ("builds", coffee, "not a cluster id")]:
        with pytest.raises(SystemExit, match=why):
            merge(conn, monkeypatch, capsys, keep, other)
    merge(conn, monkeypatch, capsys, builds, coffee)
    for keep, other in [(builds, coffee), (coffee, grape), (grape, coffee)]:
        with pytest.raises(SystemExit, match=f"{coffee} was already merged into {builds}"):
            merge(conn, monkeypatch, capsys, keep, other)
    assert table(conn, "SELECT other_id, keep_id FROM im.idea_merges") == [(coffee, builds)]
    assert_invariants(conn)


def test_forgetting_the_kept_clusters_speech_takes_what_was_merged_into_it(seeded_lattice, cfg, stream, monkeypatch,
                                                                          capsys):
    conn = seeded_lattice
    builds, lora = idea(conn, "Broken builds"), idea(conn, "Something new")
    merge(conn, monkeypatch, capsys, builds, lora)
    stream.forget(0, 6)   # the build line, which builds was made from
    stream.write(stream.dir)
    run(conn, cfg, stream)
    assert table(conn, "SELECT count(*) FROM im.ideas WHERE idea_id IN (%s, %s)", builds, lora) == [(0,)]
    assert table(conn, "SELECT count(*) FROM im.idea_merges") == [(0,)]
    assert_invariants(conn)
