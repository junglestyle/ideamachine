"""The Phase 1 step 1 done-when criteria, against a real Postgres."""

import dataclasses
from datetime import timedelta

import psycopg
import pytest

from im import db, fixtures, pipeline
from im.config import Config, SegmentConfig
from im.fixtures import FSeg, _me, sid

from helpers import (assert_invariants, current_episodes, derived_state, episodes,
                     episodes_containing)

EXPECTED_KINDS = {
    "fx-conv": ["conversation", "conversation"],
    "fx-mono": ["monologue", "conversation", "monologue"],
    "fx-tv": ["others_only"],
    "fx-edits": ["conversation", "conversation"],
    "fx-forget": ["monologue"],
}


def kinds(conn):
    out = {}
    for session, kind in conn.execute(
            "SELECT session_id, kind FROM im.episodes WHERE current ORDER BY started_at"):
        out.setdefault(session, []).append(kind)
    return out


def test_fixtures_segment_as_expected(loaded, pipe, cfg):
    pipeline.run(pipe, cfg)
    assert kinds(pipe) == EXPECTED_KINDS
    assert_invariants(pipe)


def test_every_current_segment_in_exactly_one_current_episode(loaded, pipe, cfg):
    pipeline.run(pipe, cfg)
    assert_invariants(pipe)
    # The fixture's own corrections never made it into an episode.
    for old in ["edit-2", "edit-4", "edit-5", "edit-6", "forget-2"]:
        assert not episodes_containing(pipe, [old], current_only=False)


def _split_and_merge(admin):
    fixtures.supersede(admin, ["conv-4"], [
        _me("conv-4a", "fx-conv", 17, 4, "We should pin the cache key"),
        _me("conv-4b", "fx-conv", 21.5, 3.5, "to the lockfile hash."),
    ])
    fixtures.supersede(admin, ["mono-10", "mono-11"], [
        _me("mono-1011", "fx-mono", 3815, 22, "A restore that's never been tested isn't a backup. Add a task."),
    ])


def test_superseding_rederives_only_affected_episodes(loaded, pipe, cfg):
    pipeline.run(pipe, cfg)
    before = {r[0]: r for r in episodes(pipe)}
    affected = episodes_containing(pipe, ["conv-4", "mono-10", "mono-11"])
    assert len(affected) == 2

    _split_and_merge(loaded)
    stats = pipeline.run(pipe, cfg)

    after = {r[0]: r for r in episodes(pipe)}
    assert stats["episodes_retired"] == 2 and stats["episodes_created"] == 2
    for eid, row in before.items():
        if eid in affected:
            assert after[eid] != row  # retired, kept
        else:
            assert after[eid] == row  # untouched, down to created_at
    assert not episodes_containing(pipe, ["conv-4", "mono-10", "mono-11"])
    assert episodes_containing(pipe, ["conv-4a"]) == episodes_containing(pipe, ["conv-4b"])
    new = set(after) - set(before)
    assert new == episodes_containing(pipe, ["conv-4a", "mono-1011"])
    assert kinds(pipe) == EXPECTED_KINDS
    assert_invariants(pipe)


def test_reattribution_rederives_only_its_episode(loaded, pipe, cfg):
    pipeline.run(pipe, cfg)
    before = {r[0]: r for r in episodes(pipe)}
    affected = episodes_containing(pipe, ["tv-3"])
    fixtures.supersede(loaded, ["tv-3"], [
        FSeg("tv-3r", "fx-tv", 7213, 4, "SPEAKER_01", False, "Back to you in the studio.", 0.7)])
    pipeline.run(pipe, cfg)
    after = {r[0]: r for r in episodes(pipe)}
    assert {e for e in before if after[e] != before[e]} == affected
    assert_invariants(pipe)


def test_run_twice_changes_nothing(loaded, pipe, cfg):
    pipeline.run(pipe, cfg)
    _split_and_merge(loaded)
    pipeline.run(pipe, cfg)
    first = derived_state(pipe)
    stats = pipeline.run(pipe, cfg)
    assert derived_state(pipe) == first
    assert stats["episodes_created"] == stats["episodes_retired"] == 0


def test_reset_and_run_reproduces_episodes(loaded, pipe, cfg):
    pipeline.run(pipe, cfg)
    _split_and_merge(loaded)
    pipeline.run(pipe, cfg)
    before = current_episodes(pipe)
    pipeline.reset(pipe, "segment")
    assert episodes(pipe) == []
    pipeline.run(pipe, cfg)
    assert current_episodes(pipe) == before
    assert_invariants(pipe)


def test_tombstoned_segments_leave_no_derived_rows(loaded, pipe, cfg):
    pipeline.run(pipe, cfg)
    _split_and_merge(loaded)
    pipeline.run(pipe, cfg)
    # conv-4 now lives only in a retired episode; conv-9 in a current one.
    assert episodes_containing(pipe, ["conv-4"], current_only=False)
    fixtures.tombstone(loaded, "conv-4", "test")
    fixtures.tombstone(loaded, "conv-9", "test")
    pipeline.run(pipe, cfg)
    for name in ["conv-4", "conv-9", "forget-2"]:
        assert not episodes_containing(pipe, [name], current_only=False)
    assert pipe.execute("SELECT count(*) FROM im.episode_segments WHERE segment_id = ANY(%s)",
                        ([sid("conv-4"), sid("conv-9")],)).fetchone()[0] == 0
    assert kinds(pipe)["fx-conv"] == ["conversation", "conversation"]
    assert_invariants(pipe)


def test_config_change_is_a_version_change(loaded, pipe, cfg):
    pipeline.run(pipe, cfg)
    n = len(current_episodes(pipe))
    wider = Config(segment=SegmentConfig(gap_s=600))
    stats = pipeline.run(pipe, wider)
    assert stats["episodes_retired"] == n
    versions = {r[0] for r in pipe.execute("SELECT DISTINCT stage_version FROM im.episodes WHERE current")}
    assert versions == {wider.segment.stage_version}
    assert_invariants(pipe)


def test_trailing_window_catches_a_late_commit(loaded, pipe, cfg):
    """A row whose seq is below the cursor (it committed late) is still picked up."""
    pipeline.run(pipe, cfg)
    loaded.execute(
        """INSERT INTO hearsay.segments (segment_id, seq, source, session_id, started_at, ended_at, text,
             speaker_label, is_self, speaker_conf, producer)
           OVERRIDING SYSTEM VALUE
           VALUES (%s, 0, 'fixture', 'fx-late', %s, %s, 'Committed after a higher seq.',
                   'SPEAKER_00', true, 0.9, 'test')""",
        (sid("late-1"), fixtures.T0, fixtures.T0 + timedelta(seconds=5)))
    no_window = dataclasses.replace(cfg, ingest_window_s=0)
    pipeline.run(pipe, no_window)
    assert not episodes_containing(pipe, ["late-1"])
    pipeline.run(pipe, cfg)
    assert episodes_containing(pipe, ["late-1"])
    assert_invariants(pipe)


def test_pipeline_role_cannot_write_hearsay(loaded, pipe):
    for sql in ["INSERT INTO hearsay.tombstones (segment_id, reason) SELECT segment_id, 'x' FROM hearsay.segments LIMIT 1",
                "DELETE FROM hearsay.segment_supersessions",
                "UPDATE hearsay.segments SET text = ''"]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with pipe.transaction():
                pipe.execute(sql)


def test_load_fixtures_is_idempotent(loaded):
    count = lambda: loaded.execute("SELECT count(*) FROM hearsay.segments").fetchone()[0]
    n = count()
    fixtures.load(loaded)
    assert count() == n


def test_dev_writes_refuse_non_local_hosts():
    with pytest.raises(SystemExit):
        db.assert_local("postgresql://u:p@192.168.50.99:5432/x")
    db.assert_local("postgresql://u:p@127.0.0.1:55432/x")
