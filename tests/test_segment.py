"""The heuristic on its own, no database."""

import uuid
from datetime import UTC, datetime, timedelta

from im.config import SegmentConfig
from im.segment import Seg, segment

T0 = datetime(2026, 9, 1, tzinfo=UTC)
CFG = SegmentConfig(gap_s=60, mode_gap_s=5, min_monologue_s=30, self_conf_min=0.6)


def seg(n, start, dur, me, conf=0.9, label=None):
    return Seg(uuid.uuid5(uuid.NAMESPACE_OID, str(n)), "s", "test", T0 + timedelta(seconds=start),
               T0 + timedelta(seconds=start + dur), label or ("A" if me else "B"), me, conf)


def shape(eps):
    return [[s.segment_id for s in e.segments] for e in eps]


def test_silence_gap_splits():
    a, b, c = seg(1, 0, 5, True), seg(2, 6, 5, False), seg(3, 72, 5, True)  # 61 s gap
    assert shape(segment([c, a, b], CFG)) == [[a.segment_id, b.segment_id], [c.segment_id]]


def test_gap_of_exactly_g_does_not_split():
    a, b = seg(1, 0, 5, True), seg(2, 65, 5, True)
    assert len(segment([a, b], CFG)) == 1


def test_long_monologue_is_cut_off_across_a_pause():
    mono = [seg(i, i * 10, 10, True) for i in range(4)]  # 40 s of me
    other = [seg(10, 46, 4, False), seg(11, 51, 4, True)]  # 6 s pause before
    eps = segment(mono + other, CFG)
    assert [e.kind for e in eps] == ["monologue", "conversation"]


def test_long_monologue_without_a_pause_stays_in_the_conversation():
    mono = [seg(i, i * 10, 10, True) for i in range(4)]
    other = [seg(10, 42, 4, False)]  # 2 s pause, under mode_gap_s
    assert len(segment(mono + other, CFG)) == 1


def test_unknown_and_low_confidence_speakers_fail_closed():
    eps = segment([seg(1, 0, 5, None, None), seg(2, 6, 5, True, 0.3), seg(3, 12, 5, True, None)], CFG)
    assert [(e.kind, e.self_segments) for e in eps] == [("others_only", 0)]


def test_ids_depend_on_inputs_and_version_only():
    segs = [seg(1, 0, 5, True), seg(2, 6, 5, False)]
    first, again = segment(segs, CFG), segment(list(reversed(segs)), CFG)
    assert [e.episode_id for e in first] == [e.episode_id for e in again]
    other = segment(segs, SegmentConfig(gap_s=30))
    assert first[0].input_hash == other[0].input_hash
    assert first[0].episode_id != other[0].episode_id
