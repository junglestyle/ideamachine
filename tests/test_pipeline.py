"""Phase 1 done-when criteria, against a real Postgres and a fixture stream."""

import dataclasses
import json
import os
from dataclasses import replace

import pytest
from psycopg.conninfo import conninfo_to_dict

from im import db, egress, fixtures, importer, pipeline, show
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
# What stream-v3 hashed into a segment's id: who spoke and how sure Hearsay was, as well as the speech.
V3_IDENTITY = ("conversation_id", "start", "end", "text", "speaker", "speaker_confidence", "text_confidence")


def test_fixtures_segment_as_expected(pipe, cfg, stream):
    run(pipe, cfg, stream)
    assert kinds(pipe) == EXPECTED_KINDS
    assert not table(pipe, "SELECT 1 FROM im.source_segments WHERE text = '[dishwasher beeps]'")
    assert_invariants(pipe)


# (correction, text of a segment in each episode it should re-derive, how the old segment is linked)
CORRECTIONS = {
    # anon A turns out to be Alice: the speech stays, but S2 becomes S1 in what would be sent.
    "merge voices": (lambda s: s.name_speaker("c-conv", "anon A", "Alice"), [COFFEE], None),
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


def test_naming_a_voice_changes_its_label_and_nothing_else(pipe, cfg, stream):
    run(pipe, cfg, stream)
    before, coffee = episodes(pipe), segment_of(pipe, COFFEE)
    stream.name_speaker("c-conv", "anon A", "Carol")
    stream.write(stream.dir)
    stats = run(pipe, cfg, stream)
    assert stats["segments_new"] == stats["segments_left"] == stats["episodes_created"] == 0
    assert episodes(pipe) == before
    assert segment_of(pipe, COFFEE) == coffee
    assert table(pipe, "SELECT speaker_kind, speaker_name, speaker_label FROM im.source_segments WHERE segment_id = %s",
                 coffee) == [("person", "Carol", "Carol")]
    assert table(pipe, "SELECT count(*) FROM im.source_supersessions") == [(0,)]
    assert_invariants(pipe)


def test_an_older_importers_segments_are_rekeyed_in_place_of_a_migration(pipe, cfg, stream, monkeypatch):
    with monkeypatch.context() as m:   # import as stream-v3 did: who spoke is part of a segment's identity
        m.setattr(importer, "STAGE_VERSION", "import/stream-v3")
        m.setattr(importer, "IDENTITY", V3_IDENTITY)
        run(pipe, cfg, stream)
    old = table(pipe, "SELECT segment_id, text FROM im.current_segments ORDER BY text")
    stats = run(pipe, cfg, stream)
    new = table(pipe, "SELECT segment_id, text FROM im.current_segments ORDER BY text")
    assert [t for _, t in new] == [t for _, t in old] and not {s for s, _ in new} & {s for s, _ in old}
    assert table(pipe, "SELECT method, count(*) FROM im.source_supersessions GROUP BY 1") == [("rekey", len(old))]
    assert stats["segments_left"] == 0   # every old segment found its new key, so nothing links by overlap
    assert table(pipe, "SELECT DISTINCT stage_version FROM im.current_segments") == [(importer.STAGE_VERSION,)]
    assert run(pipe, cfg, stream)["conversations_changed"] == 0
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


@pytest.mark.parametrize("start, end, listed", [(14399, 14419, False), (14403, 14416, True)],
                         ids=["wholly inside: it leaves the index", "partly: it stays, untranscribed"])
def test_forgetting_all_of_a_conversations_speech(pipe, cfg, stream, start, end, listed):
    # Hearsay's contract: a conversation inside the stretch leaves the index; one partly inside with no speech
    # left stays, as if not yet transcribed (no file).
    run(pipe, cfg, stream)
    stream.forget(start, end)
    stream.write(stream.dir)
    entries = json.loads((stream.dir / "index.json").read_text())["conversations"]
    assert [e["file"] for e in entries if e["conversation_id"] == "c-forget"] == ([None] if listed else [])
    run(pipe, cfg, stream)
    assert table(pipe, "SELECT count(*) FROM im.source_segments WHERE conversation_id = 'c-forget'") == [(0,)]
    assert "c-forget" not in kinds(pipe) and len(table(pipe, "SELECT 1 FROM im.source_tombstones")) == 3
    assert_invariants(pipe)


def test_im_check_fails_when_a_segment_is_in_no_episode(pipe, cfg, stream, monkeypatch, capsys):
    from im import cli

    run(pipe, cfg, stream)
    monkeypatch.setattr(cli.db, "pipeline_dsn", lambda: pipe.info.dsn + " password=" + pipe.info.password)
    cli.main(["check"])   # every invariant holds: exits normally
    pipe.execute("DELETE FROM im.episode_segments WHERE segment_id = %s", (segment_of(pipe, COFFEE),))
    capsys.readouterr()
    with pytest.raises(SystemExit) as failed:
        cli.main(["check"])
    assert failed.value.code == 1 and json.loads(capsys.readouterr().out) == {"orphans": 1}


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


def test_forgetting_takes_what_overlaps_and_not_what_only_touches_it(pipe, cfg, stream):
    # Hearsay's overlap is exclusive: speech that ends as the stretch starts, or starts as it ends, stays.
    run(pipe, cfg, stream)
    before = _unchanged(pipe)
    stream.forget(10805, 10806)   # between "Let's review the budget." (10800-10805) and the hosting turn (10806-)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert _unchanged(pipe) == before
    assert table(pipe, "SELECT count(*) FROM im.source_tombstones") == [(0,)]


def test_forgetting_reaches_every_conversation_the_speech_was_in(pipe, cfg, stream):
    # A split moves the backup line to a new conversation. Its version in the old one is still held, and goes too.
    run(pipe, cfg, stream)
    stream.split_conversation("c-mono", 9, "c-mono-b")
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert table(pipe, "SELECT conversation_id FROM im.source_segments WHERE text = %s ORDER BY 1", BACKUP) == \
        [("c-mono",), ("c-mono-b",)]
    stream.forget(3800, 3814)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert not table(pipe, "SELECT 1 FROM im.source_segments WHERE text = %s", BACKUP)
    assert table(pipe, "SELECT count(*) FROM im.source_tombstones") == [(2,)]
    assert_invariants(pipe)



@pytest.mark.parametrize("setting", [f.name for f in dataclasses.fields(SegmentConfig)])
def test_config_change_is_a_version_change(pipe, cfg, stream, setting):
    run(pipe, cfg, stream)
    n = len(current_episodes(pipe))
    changed = Config(segment=dataclasses.replace(cfg.segment, **{setting: getattr(cfg.segment, setting) * 2}))
    stats = run(pipe, changed, stream)
    assert stats["episodes_retired"] == n   # every setting that can move a boundary re-derives every episode
    versions = {r[0] for r in table(pipe, "SELECT DISTINCT stage_version FROM im.episodes WHERE current")}
    assert versions == {changed.segment.stage_version}
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


# Hearsay's speaker kinds (its docs/stream.md, "Speakers"), and whether each is me. `person` isn't written any more,
# but format 1 still allows it.
HEARSAY_KINDS = {"owner": True, "anonymous": False, "stranger": False, "unknown": None, "person": False}


def test_every_speaker_kind_hearsay_writes_is_read(pipe, cfg, stream):
    speakers = [fixtures.ME, fixtures.anon("anon A"), fixtures.Speaker("stranger", label="stranger A", basis="named"),
                fixtures.UNKNOWN, fixtures.person("Dana")]
    stream.conversations["c-kinds"] = fixtures.Conversation(
        [fixtures.Turn(20000 + 10 * n, 20005 + 10 * n, f"Turn {n}.", s) for n, s in enumerate(speakers)])
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert dict(table(pipe, "SELECT speaker_kind, is_self FROM im.source_segments WHERE conversation_id = 'c-kinds'")) \
        == HEARSAY_KINDS
    (episode,) = [e for (e,) in table(pipe, """SELECT DISTINCT es.episode_id FROM im.episode_segments es
                                              JOIN im.source_segments s USING (segment_id)
                                              WHERE s.conversation_id = 'c-kinds'""")]
    sent = egress.build(pipe, episode).text
    assert "stranger A" not in sent and "Dana" not in sent and "S3: Turn 4." in sent   # pseudonymized like anyone


def test_a_speaker_kind_hearsay_doesnt_write_fails_the_run_and_writes_nothing(pipe, cfg, stream):
    turns = stream.conversations["c-tv"].turns
    turns[0] = replace(turns[0], speaker=fixtures.Speaker("robot", label="robot A"))
    stream.write(stream.dir)
    with pytest.raises(StreamError):
        run(pipe, cfg, stream)
    assert table(pipe, "SELECT count(*) FROM im.source_segments") == [(0,)]



def _unchanged(conn):
    """What an annotation (affect, places) mustn't touch: which segments exist and are current, and episodes."""
    return (table(conn, "SELECT segment_id FROM im.source_segments ORDER BY 1"),
            table(conn, "SELECT * FROM im.source_members ORDER BY 1, 2"), current_episodes(conn))


LORA = "Idea: the garden sensors could report soil moisture over LoRa."


def test_affect_is_kept_and_a_rescoring_rederives_nothing(pipe, cfg, stream):
    run(pipe, cfg, stream)
    assert table(pipe, "SELECT round(arousal::numeric, 2)::float FROM im.source_segments WHERE text = %s", LORA) \
        == [(0.72,)]
    assert table(pipe, "SELECT count(*) FROM im.source_segments WHERE arousal IS NOT NULL") == [(2,)]
    sid, before = segment_of(pipe, LORA), _unchanged(pipe)
    stream.score_affect("c-mono", 0, 0.35)
    stream.score_affect("c-mono", 1, 0.5)
    stream.write(stream.dir)
    stats = run(pipe, cfg, stream)
    assert segment_of(pipe, LORA) == sid and stats["episodes_retired"] == stats["episodes_created"] == 0
    assert _unchanged(pipe) == before
    assert table(pipe, "SELECT round(arousal::numeric, 2)::float FROM im.source_segments WHERE text = %s", LORA) \
        == [(0.35,)]
    assert table(pipe, "SELECT count(*) FROM im.source_segments WHERE arousal IS NOT NULL") == [(3,)]
    stream.score_affect("c-mono", 0, None)  # no longer scored
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert table(pipe, "SELECT arousal FROM im.source_segments WHERE text = %s", LORA) == [(None,)]
    assert_invariants(pipe)


BATTERY = "Battery life is the hard part, maybe a solar trickle charger."


def test_delivery_is_kept_and_a_remeasurement_rederives_nothing(pipe, cfg, stream):
    run(pipe, cfg, stream)
    wps = "SELECT (delivery->>'words_per_second')::float FROM im.source_segments WHERE text = %s"
    assert table(pipe, wps, BATTERY) == [(3.6,)]
    assert table(pipe, "SELECT count(*) FROM im.source_segments WHERE delivery IS NOT NULL") == [(1,)]
    sid, before = segment_of(pipe, BATTERY), _unchanged(pipe)
    stream.measure_delivery("c-mono", 1, {"words_per_second": 2.9, "pauses": 0, "pause_seconds": None, "stutters": []})
    stream.measure_delivery("c-mono", 0, {"words_per_second": 2.5, "pauses": 2, "pause_seconds": 1.5, "stutters": [
        {"kind": "filler", "at": fixtures.iso(3600), "words_per_second_before": None},   # nothing to time before it
        {"kind": "repeat", "at": fixtures.iso(3601), "words_per_second_before": 2.4}]})
    stream.write(stream.dir)
    stats = run(pipe, cfg, stream)
    assert segment_of(pipe, BATTERY) == sid and stats["episodes_retired"] == stats["episodes_created"] == 0
    assert _unchanged(pipe) == before
    assert table(pipe, wps, BATTERY) == [(2.9,)]
    assert table(pipe, "SELECT delivery->'stutters'->0->>'kind' FROM im.source_segments WHERE text = %s", LORA) \
        == [("filler",)]
    stream.measure_delivery("c-mono", 0, {"words_per_second": 2.5, "pauses": 2, "pause_seconds": 1.5, "stutters": [
        {"kind": "mumble", "at": fixtures.iso(3601), "words_per_second_before": 2.4}]})
    stream.write(stream.dir)
    with pytest.raises(StreamError):
        run(pipe, cfg, stream)
    stream.measure_delivery("c-mono", 0, {"words_per_second": "fast", "pauses": 0, "pause_seconds": None,
                                          "stutters": []})
    stream.write(stream.dir)
    with pytest.raises(StreamError):
        run(pipe, cfg, stream)


def test_a_field_hearsay_adds_keeps_every_segment_and_episode(pipe, cfg, stream, monkeypatch):
    # Hearsay's contract: a new field doesn't bump format_version. It's an annotation, not part of the speech.
    run(pipe, cfg, stream)
    before = _unchanged(pipe)
    plain = fixtures.FixtureStream.utterances
    monkeypatch.setattr(fixtures.FixtureStream, "utterances",
                        lambda self, cid: [u | {"language": "en"} for u in plain(self, cid)])
    stream.write(stream.dir)
    stats = run(pipe, cfg, stream)
    assert stats["segments_new"] == stats["segments_left"] == stats["conversations_changed"] == 0   # nobody relabeled
    assert stats["episodes_retired"] == stats["episodes_created"] == 0
    assert _unchanged(pipe) == before
    assert_invariants(pipe)


def test_places_follow_the_index_and_bad_ones_fail(pipe, cfg, stream):
    run(pipe, cfg, stream)
    assert table(pipe, "SELECT places->0->>'name' FROM im.source_conversations WHERE conversation_id = 'c-conv'") \
        == [("Office",)]
    assert table(pipe, "SELECT places FROM im.source_conversations WHERE conversation_id = 'c-tv'") == [([],)]
    office = table(pipe, "SELECT episode_id::text FROM im.episodes WHERE session_id = 'c-conv' LIMIT 1")[0][0]
    tv = table(pipe, "SELECT episode_id::text FROM im.episodes WHERE session_id = 'c-tv'")[0][0]
    assert "Office" in show.show(pipe, office) and "Office" not in show.show(pipe, tv)
    before = _unchanged(pipe)
    stream.name_place("c-mono", "Home", 3600, 3700)  # named retroactively
    stream.name_place("c-mono", "Garden", 3700, 3837)
    stream.write(stream.dir)
    stats = run(pipe, cfg, stream)
    assert stats["conversations_changed"] == 0 and _unchanged(pipe) == before
    assert table(pipe, """SELECT array_agg(p->>'name' ORDER BY n) FROM im.source_conversations,
                          jsonb_array_elements(places) WITH ORDINALITY AS x(p, n)
                          WHERE conversation_id = 'c-mono'""") == [(["Home", "Garden"],)]
    stream.conversations["c-tv"].places = [{"name": "Den", "start": fixtures.iso(7200)}]
    stream.write(stream.dir)
    with pytest.raises(StreamError):
        run(pipe, cfg, stream)


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


@pytest.fixture
def no_pg_env(monkeypatch):
    for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@192.168.50.99:5432/x",
    "host=nas.example.net dbname=im_dev user=postgres",
    "postgresql:///im_dev?host=nas.example.net",
    "host=localhost hostaddr=192.168.50.99 dbname=x",
    "host=localhost,nas.example.net dbname=x",
    "service=nas dbname=x",
])
def test_dev_writes_refuse_non_local_hosts(no_pg_env, dsn):
    with pytest.raises(SystemExit):
        db.assert_local(dsn)


def test_dev_writes_refuse_a_remote_pghost_when_the_dsn_names_no_host(no_pg_env, monkeypatch):
    monkeypatch.setenv("PGHOST", "nas.example.net")
    with pytest.raises(SystemExit):
        db.assert_local("postgresql:///im_dev")


@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@127.0.0.1:55432/x",
    "postgresql://u@[::1]:55432/x",
    "host=localhost dbname=x",
    "host=/var/run/postgresql dbname=x",
    "postgresql:///x?host=/tmp",
    "postgresql:///x",
])
def test_dev_writes_go_to_this_machine(no_pg_env, dsn):
    db.assert_local(dsn)


@pytest.mark.parametrize("dsn", ["postgresql://u:p@127.0.0.1:55432/im_dev",
                                 "host=127.0.0.1 port=55432 dbname=im_dev user=u password=p"])
def test_with_dbname_swaps_only_the_database(dsn):
    assert conninfo_to_dict(db.with_dbname(dsn, "im_test")) == conninfo_to_dict(dsn) | {"dbname": "im_test"}
