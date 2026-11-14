"""Captures of the archive (migration 055): what I typed into the ChatGPT Idea Machine, with no episode, matched with
my speech in the order I said things, and shown and weighed as speech is."""

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from im import extract, fixtures, lattice, observations, pipeline, quotes, weight
from im.config import WeightConfig

from helpers import HashEmbedder, assert_invariants, run, table
from test_extract import FakeClaude
from test_lattice import FakeMatcher, items_for

TYPED = "a build that breaks every week is telling you something about the team"


def archive_capture(conn, said_at, quote: str = TYPED) -> uuid.UUID:
    """A capture of one message, stored as the archive's are: no episode or segments, and its archive observation."""
    (egress_id,) = conn.execute(
        """INSERT INTO im.egress_log (segment_ids, model, prompt_version, privacy_policy_version, payload,
                                      payload_sha256)
           VALUES ('{}', 'claude-opus-5-5', 'pv', 'C1', %s, 'sha') RETURNING egress_id""", (quote,)).fetchone()
    (obs,) = conn.execute(
        """INSERT INTO im.observations (segment_ids, first_seen, source, quote, said_at, said_by, conversation,
                                        source_ref)
           VALUES ('{}', %s, 'archive', %s, %s, 'me', 'chatgpt:c1', %s) RETURNING observation_id""",
        (said_at, quote, said_at, f"chatgpt:c1:{said_at.isoformat()}#1")).fetchone()
    item = uuid.uuid4()
    conn.execute(
        """INSERT INTO im.items (item_id, said_by, quote, gist, themes, source_segment_ids, egress_id, model,
             prompt_version, privacy_policy_version, schema_version, stage_version, input_hash, reading, attributed_to,
             mishearings, checks, observation_id)
           VALUES (%s, 'me', %s, %s, '{}', '{}', %s, 'claude-opus-5-5', 'pv', 'C1', 1, 'extract/1', 'sha', %s, '',
             '[]', '{}', %s)""", (item, quote, quote, egress_id, quote, obs))
    return item


def mine_only(text):
    return [i for i in items_for(text) if i["said_by"] == "me"]


def test_an_archive_capture_is_matched_between_my_speech_in_time_order(pipe, cfg, stream):
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(mine_only))   # the build line (at T0) and the LoRa idea (an hour later)
    typed = archive_capture(pipe, fixtures.T0 + timedelta(minutes=30))
    claude = FakeMatcher()
    stats = lattice.match_stage(pipe, claude, HashEmbedder())
    # The build line starts the first cluster, unasked; the archive capture is asked about next, then the LoRa idea,
    # which is shown the archive's cluster with the archive capture as its member.
    assert stats["new"] == 3 and len(claude.calls) == 2
    first, second = (c["messages"][0]["content"] for c in claude.calls)
    assert f"gist: {TYPED}" in first and "LoRa" in second and f"   - {TYPED}" in second
    (idea,) = table(pipe, "SELECT idea_id FROM im.idea_evidence WHERE item_id = %s", typed)[0]
    assert table(pipe, "SELECT origin, n_evidence, first_said FROM pub.ideas WHERE idea_id = %s", idea) == [
        ("captured", 1, fixtures.T0 + timedelta(minutes=30))]
    assert table(pipe, "SELECT maturity, observations, by_me, conversations FROM pub.cards WHERE idea_id = %s",
                 idea) == [("candidate", 1, 1, 1)]
    assert table(pipe, "SELECT quote, source, speaker FROM pub.card_evidence WHERE idea_id = %s", idea) == [
        (TYPED, "archive", "me")]
    assert weight.weights(pipe, WeightConfig())[idea][1]["recurrences"] == 1


def test_an_archive_capture_is_left_alone_by_the_speech_stages_and_a_reset(pipe, cfg, stream):
    run(pipe, cfg, stream)
    typed = archive_capture(pipe, fixtures.T0)
    lattice.match_stage(pipe, FakeMatcher(), HashEmbedder())
    before = table(pipe, "SELECT quote, quote_in_lines, observation_id FROM im.items WHERE item_id = %s", typed)
    assert quotes.place_stage(pipe)["checked"] == 0
    assert observations.assign_stage(pipe)["new"] == 0
    assert pipeline.reset(pipe, "extract")["items_deleted"] == 0
    assert table(pipe, "SELECT quote, quote_in_lines, observation_id FROM im.items WHERE item_id = %s",
                 typed) == before
    assert table(pipe, "SELECT maturity FROM pub.cards") == [("candidate",)]
    assert_invariants(pipe)


def test_the_archive_is_only_captures_now(pipe):
    # Migration 057: no idea comes from the archive (the seeded ideas stayed in im_v1), and an archive observation
    # belongs to its capture, never to an idea of its own, as what I typed adding an idea always does.
    (idea,) = pipe.execute("INSERT INTO im.ideas (title, statement, origin) VALUES ('t', 's', 'mine') "
                           "RETURNING idea_id").fetchone()
    observation = """INSERT INTO im.observations (segment_ids, first_seen, source, idea_id, quote, said_at, said_by,
                                                  conversation, source_ref)
                     VALUES ('{}', now(), %s, %s, 'q', now(), 'me', 'c', %s)"""
    for row in [("archive", idea, "chatgpt:c1:x#1"), ("typed", None, "idea_add:1")]:
        with pytest.raises(psycopg.errors.CheckViolation), pipe.transaction():
            pipe.execute(observation, row)
    with pytest.raises(psycopg.errors.CheckViolation), pipe.transaction():
        pipe.execute("INSERT INTO im.ideas (title, statement, origin) VALUES ('t', 's', 'archive')")
    archive_capture(pipe, datetime(2026, 9, 1, tzinfo=timezone.utc))
    pipe.execute(observation, ("typed", idea, "idea_add:1"))
    assert table(pipe, "SELECT source, idea_id IS NULL FROM im.observations ORDER BY source") == \
        [("archive", True), ("typed", False)]
