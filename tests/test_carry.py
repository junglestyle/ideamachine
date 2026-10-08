"""Carrying my verdicts forward when the speech they're on is re-extracted on a new episode."""

from im import extract, feedback
from im.fixtures import _me

from helpers import assert_invariants, run, table
from test_extract import FakeClaude
from test_pipeline import CORRECTIONS

DATABASE = "managed database"


def capture(needle, kind="idea"):
    """Claude captures every line containing `needle`, quoting the line."""
    def respond(text):
        hits = [(int(line[1:].split("]")[0]), line.split(": ", 1)[1])
                for line in text.splitlines() if line.startswith("[") and needle in line]
        return [{"kind": kind, "said_by": "me", "quote": q, "lines": [n], "gist": needle, "themes": [],
                 "confidence": 0.9} for n, q in hits]
    return respond


def current_items(pipe):
    return table(pipe, """SELECT i.item_id, i.quote FROM im.items i JOIN im.episodes e USING (episode_id)
                          WHERE e.current ORDER BY i.quote""")


def retranscribe(pipe, cfg, stream, claude):
    CORRECTIONS["split a turn"][0](stream)   # the database line becomes its own turn: a new segment and episode
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    extract.run_stage(pipe, claude)


def test_a_verdict_follows_its_capture_to_the_new_episode(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = FakeClaude(capture(DATABASE))
    extract.run_stage(pipe, claude)
    ((old, _),) = current_items(pipe)
    extract.decide(pipe, old, "discard", "already on the list")
    assert feedback.carry_verdicts(pipe) == {"carried": 0, "already_decided": 0, "waiting": 0}

    retranscribe(pipe, cfg, stream, claude)
    ((new, quote),) = current_items(pipe)
    assert new != old and quote == "I think we should move off the managed database."
    assert [r[0] for r in extract.review_items(pipe)] == [new]
    assert feedback.carry_verdicts(pipe) == {"carried": 1, "already_decided": 0, "waiting": 0}
    assert not extract.review_items(pipe)
    (copy,) = table(pipe, """SELECT n.verdict, n.note, n.decided_at = o.decided_at, n.quote, n.carried_from = o.verdict_id
                             FROM im.item_verdicts n JOIN im.item_verdicts o ON o.item_id = %s
                             WHERE n.item_id = %s""", old, new)
    assert copy == ("discard", "already on the list", True, quote, True)
    assert feedback.carry_verdicts(pipe) == {"carried": 0, "already_decided": 0, "waiting": 0}  # settled once

    # And on again: the copy is a verdict like any other.
    stream.conversations["c-edits"].turns[2] = _me(10810.5, 10816.5, quote)   # re-transcribed again: new times
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    extract.run_stage(pipe, claude)
    assert feedback.carry_verdicts(pipe)["carried"] == 1
    assert table(pipe, "SELECT count(*) FROM im.item_verdicts WHERE verdict = 'discard'") == [(3,)]
    assert not extract.review_items(pipe)
    assert_invariants(pipe)


def test_my_own_verdict_on_the_new_item_stands(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = FakeClaude(capture(DATABASE))
    extract.run_stage(pipe, claude)
    ((old, _),) = current_items(pipe)
    extract.decide(pipe, old, "discard")
    retranscribe(pipe, cfg, stream, claude)
    ((new, _),) = current_items(pipe)
    extract.decide(pipe, new, "star")
    assert feedback.carry_verdicts(pipe) == {"carried": 0, "already_decided": 1, "waiting": 0}
    assert table(pipe, "SELECT verdict FROM im.item_verdicts WHERE item_id = %s", new) == [("star",)]


def test_a_different_capture_of_the_same_speech_isnt_carried(pipe, cfg, stream):
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(capture(DATABASE)))
    ((old, _),) = current_items(pipe)
    extract.decide(pipe, old, "keep")
    reworded = lambda text: [dict(i, quote="hosting costs") for i in capture(DATABASE)(text)]  # noqa: E731
    retranscribe(pipe, cfg, stream, FakeClaude(reworded))
    assert feedback.carry_verdicts(pipe) == {"carried": 0, "already_decided": 0, "waiting": 1}
    assert len(extract.review_items(pipe)) == 1


def test_forgetting_takes_the_copies_too(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = FakeClaude(capture(DATABASE))
    extract.run_stage(pipe, claude)
    extract.decide(pipe, current_items(pipe)[0][0], "keep")
    retranscribe(pipe, cfg, stream, claude)
    feedback.carry_verdicts(pipe)
    assert table(pipe, "SELECT count(*) FROM im.item_verdicts") == [(2,)]
    stream.forget(10806, 10816)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert table(pipe, "SELECT count(*) FROM im.item_verdicts") == [(0,)]


def test_quote_score_reads_past_fillers_and_excerpts():
    assert feedback.quote_score("You can't micromanage people's care.",
                                "You have a choice to show up, and you can't micromanage people's care.") == 1.0
    assert feedback.quote_score("are there existing open source out there?",
                                "what I'm wondering is are there existing uh open source out there? Um") == 1.0
    assert feedback.quote_score("Most of the battles are playing it cool.",
                                "If I see a bartender freaking out I think this place sucks.") < 0.3
    assert feedback.quote_score("", "anything") == 0.0
