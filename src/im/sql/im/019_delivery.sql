-- Hearsay's measured delivery of my turns (ROADMAP §3.1): {words_per_second, pauses, pause_seconds, stutters:
-- [{kind: repeat | restart | filler, at, words_per_second_before}]}. Measured from word timings, not judged; for
-- trends and feedback on pace, not a measure of confidence or assertiveness. NULL where not measured. Like
-- arousal, an annotation kept up to date in place and not part of segment_id, so a re-measurement re-derives
-- nothing and sends nothing to Claude again.
ALTER TABLE im.source_segments ADD COLUMN delivery jsonb NULL;
