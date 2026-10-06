"""Phase 1 done-when criteria, against a real Postgres and a fixture stream."""

import json
import os

import pytest

from im import db, fixtures, pipeline
from im.config import Config, SegmentConfig
from im.fixtures import _alice, _me
from im.importer import StreamError

from helpers import (assert_invariants, changed_episodes, current_episodes, derived_state, episodes,
                     episodes_with_text, kinds, run, segment_of, table)

EXPECTED_KINDS = {
    "c-conv": ["conversation", "conversation"],
    "c-mono": ["monologue", "conversation", "monologue"],
    "c-tv": ["others_only"],
    "c-edits": ["conversation", "conversation"],
    "c-forget": ["monologue"],
}
COFFEE = "Who's got the coffee order?"
HOSTING = "The hosting line doubled. I think we should move off the managed database."
ONE_MORE = "One more thing about the"
BACKUP = "Another thought: the backup job should verify restores weekly."
SECRET = "Private detail that must be forgotten."


def test_fixtures_segment_as_expected(pipe, cfg, stream):
    run(pipe, cfg, stream)
    assert kinds(pipe) == EXPECTED_KINDS
    assert not table(pipe, "SELECT 1 FROM im.source_segments WHERE text = '[dishwasher beeps]'")
    assert_invariants(pipe)


# (correction, text of a segment in each episode it should re-derive, how the old segment is linked)
CORRECTIONS = {
    "name a speaker": (lambda s: s.name_speaker("c-conv", "anon A", "Carol"), [COFFEE], "utterance_id"),
    "split a turn": (lambda s: s.split_turn("c-edits", 1, 10810,
                                            _alice(10806, 10810, "The hosting line doubled."),
                                            _me(10810.5, 10816, "I think we should move off the managed database.")),
                     [HOSTING], "time_overlap"),
    "merge turns": (lambda s: s.merge_turns("c-edits", 3, _me(10941, 10949.5, "One more thing about the on-call.")),
                    [ONE_MORE], "time_overlap"),
    "file as noise": (lambda s: s.file_as_noise("c-conv", 2), [COFFEE], None),
    "split a conversation": (lambda s: s.split_conversation("c-mono", 9, "c-mono-b"), [BACKUP], "time_overlap"),
    "merge conversations": (lambda s: s.merge_conversations("c-edits", "c-forget"), [SECRET], "time_overlap"),
}


@pytest.mark.parametrize("name", CORRECTIONS)
def test_a_correction_rederives_only_affected_episodes(pipe, cfg, stream, name):
    correct, texts, method = CORRECTIONS[name]
    run(pipe, cfg, stream)
    before = episodes(pipe)
    affected = set().union(*(episodes_with_text(pipe, t) for t in texts))
    assert len(affected) == len(texts)

    correct(stream)
    stream.write(stream.dir)
    stats = run(pipe, cfg, stream)

    retired, created = changed_episodes(before, episodes(pipe))
    assert retired == affected
    assert stats["episodes_retired"] == len(affected) and stats["episodes_created"] == len(created) >= 1
    methods = {r[0] for r in table(pipe, "SELECT method FROM im.source_supersessions")}
    assert methods == ({method} if method else set())
    assert_invariants(pipe)


def test_renumbered_but_unchanged_utterances_keep_their_segments(pipe, cfg, stream):
    run(pipe, cfg, stream)
    agreed = segment_of(pipe, "Agreed.")
    CORRECTIONS["split a turn"][0](stream)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert segment_of(pipe, "Agreed.") == agreed
    assert table(pipe, "SELECT utterance_id FROM im.source_members WHERE segment_id = %s", agreed) == \
        [("c-edits:0003",)]


def test_a_refiled_noise_turn_comes_back_with_the_same_ids(pipe, cfg, stream):
    run(pipe, cfg, stream)
    original = current_episodes(pipe)
    speaker = stream.file_as_noise("c-conv", 2)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert current_episodes(pipe) != original
    stream.refile("c-conv", 2, speaker)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert current_episodes(pipe) == original
    assert_invariants(pipe)


def test_every_current_segment_in_exactly_one_current_episode(pipe, cfg, stream):
    run(pipe, cfg, stream)
    edited = fixtures.edited()
    edited.write(stream.dir)
    run(pipe, cfg, stream)
    assert_invariants(pipe)
    assert set(kinds(pipe)) == {"c-conv", "c-mono", "c-mono-b", "c-tv", "c-edits"}


def test_run_twice_changes_nothing(pipe, cfg, stream):
    run(pipe, cfg, stream)
    fixtures.edited().write(stream.dir)
    run(pipe, cfg, stream)
    first = derived_state(pipe)
    stats = run(pipe, cfg, stream)
    assert derived_state(pipe) == first
    assert stats["episodes_created"] == stats["episodes_retired"] == stats["conversations_changed"] == 0


def test_reset_and_run_reproduces_episodes(pipe, cfg, stream):
    run(pipe, cfg, stream)
    fixtures.edited().write(stream.dir)
    run(pipe, cfg, stream)
    before = current_episodes(pipe)
    pipeline.reset(pipe, "segment")
    assert episodes(pipe) == []
    run(pipe, cfg, stream)
    assert current_episodes(pipe) == before
    assert_invariants(pipe)


def test_forgetting_leaves_no_text_or_derived_rows(pipe, cfg, stream):
    run(pipe, cfg, stream)
    # The old version of the hosting turn now lives only in a retired episode.
    CORRECTIONS["split a turn"][0](stream)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert episodes_with_text(pipe, HOSTING, current_only=False)

    stream.forget(14406, 14412)
    stream.forget(10806, 10816)
    stream.write(stream.dir)
    run(pipe, cfg, stream)

    for text in [SECRET, HOSTING, "The hosting line doubled.", "I think we should move off the managed database."]:
        assert not table(pipe, "SELECT 1 FROM im.source_segments WHERE text = %s", text)
    gone = [r[0] for r in table(pipe, "SELECT segment_id FROM im.source_tombstones")]
    assert len(gone) == 4
    for t in ["episode_segments", "source_members"]:
        assert not table(pipe, f"SELECT 1 FROM im.{t} WHERE segment_id = ANY(%s)", gone)
    assert not table(pipe, "SELECT 1 FROM im.source_supersessions WHERE old_segment_id = ANY(%s) "
                           "OR new_segment_id = ANY(%s)", gone, gone)
    assert kinds(pipe)["c-forget"] == ["monologue"]
    assert_invariants(pipe)


def test_forgotten_speech_that_reappears_is_purged_again(pipe, cfg, stream):
    stream.forgotten.append({"forgotten_at": fixtures.iso(20000), "start": fixtures.iso(14406),
                             "end": fixtures.iso(14412), "conversation_id": "c-forget",
                             "utterance_ids": ["c-forget:0001"], "reason": "test"})
    stream.write(stream.dir)  # Hearsay hasn't dropped it from the stream yet
    run(pipe, cfg, stream)
    assert not table(pipe, "SELECT 1 FROM im.source_segments WHERE text = %s", SECRET)
    assert_invariants(pipe)
    first = derived_state(pipe)
    run(pipe, cfg, stream)
    assert derived_state(pipe) == first


def test_config_change_is_a_version_change(pipe, cfg, stream):
    run(pipe, cfg, stream)
    n = len(current_episodes(pipe))
    wider = Config(segment=SegmentConfig(gap_s=600))
    stats = run(pipe, wider, stream)
    assert stats["episodes_retired"] == n
    versions = {r[0] for r in table(pipe, "SELECT DISTINCT stage_version FROM im.episodes WHERE current")}
    assert versions == {wider.segment.stage_version}
    assert_invariants(pipe)


def test_a_file_that_doesnt_match_its_revision_waits_for_the_next_run(pipe, cfg, stream):
    path = stream.dir / "conversations" / "c-tv.jsonl"
    good = path.read_bytes()
    path.write_bytes(good[: len(good) // 2])  # Hearsay mid-rewrite
    stats = run(pipe, cfg, stream)
    assert stats["raced"] == 1 and "c-tv" not in kinds(pipe)
    path.write_bytes(good)
    stats = run(pipe, cfg, stream)
    assert stats["raced"] == 0 and kinds(pipe)["c-tv"] == ["others_only"]
    assert_invariants(pipe)


def test_unknown_format_version_fails_and_writes_nothing(pipe, cfg, stream):
    index = stream.dir / "index.json"
    index.write_text(json.dumps(json.loads(index.read_text()) | {"format_version": 2}))
    with pytest.raises(StreamError):
        run(pipe, cfg, stream)
    assert table(pipe, "SELECT count(*) FROM im.source_segments") == [(0,)]


def test_missing_forgotten_list_fails(pipe, cfg, stream):
    (stream.dir / "forgotten.json").unlink()
    with pytest.raises(StreamError):
        run(pipe, cfg, stream)


def test_the_stream_is_only_read(pipe, cfg, stream):
    paths = [stream.dir, *stream.dir.rglob("*")]
    for p in paths:
        os.chmod(p, 0o555 if p.is_dir() else 0o444)
    try:
        run(pipe, cfg, stream)
        assert_invariants(pipe)
    finally:
        for p in paths:
            os.chmod(p, 0o755 if p.is_dir() else 0o644)


def test_fixtures_refuse_a_directory_they_didnt_make(tmp_path):
    (tmp_path / "index.json").write_text("{}")
    with pytest.raises(SystemExit):
        fixtures.write(tmp_path)


def test_dev_writes_refuse_non_local_hosts():
    with pytest.raises(SystemExit):
        db.assert_local("postgresql://u:p@192.168.50.99:5432/x")
    db.assert_local("postgresql://u:p@127.0.0.1:55432/x")
