"""Observations that aren't speech (migration 036): my messages to the ChatGPT Idea Machine, and ideas I typed."""

import uuid

import pytest

from im import archive_observations as ao, observations, weight
from im.config import WeightConfig

from helpers import assert_invariants, table
from test_idea_add import add
from test_lattice import seeded  # noqa: F401

# Shaped like the real export (ChatGPT Exporter): ideas #1 and #2 of test_lattice's ARCHIVE, an instruction, a
# refinement weeks later, one message feeding two ideas, and a number the archive doesn't have.
EXPORT = """# Idea Machine

**User:** Anonymous
**Link:** [https://chatgpt.com/c/6a68b7be-0000](https://chatgpt.com/c/6a68b7be-0000)

## Prompt:
7/28/2026, 10:08:02 AM

This chat is my Idea Machine. Capture ideas, don't evaluate them.

## Response:
7/28/2026, 10:08:06 AM · gpt-5-6-thinking

Idea Machine is active.

## Prompt:
7/28/2026, 10:08:30 AM

A solitary unsatisfying grape

## Response:
7/28/2026, 10:08:32 AM · gpt-5-5

Captured.

**Idea #1:** *A solitary unsatisfying grape.*

## Prompt:
8/19/2026, 9:56:14 PM

signal may not be true, but it is always correct. and the grape was alone on the plate

## Response:
8/19/2026, 9:56:14 PM · gpt-5-6

Captured as a correction to **Idea #1**, and a new one.

**Idea #1, refined:** the grape, alone.

**Idea #2:** *Signal may not be true, but it is always correct.* This connects to **Idea #1**.

## Prompt:
8/20/2026, 2:37:47 AM

my brother says the meeting is the coffee

## Response:
8/20/2026, 2:37:47 AM · gpt-5-6

Captured.

**Idea #9:** *The meeting is the coffee.*

---
Powered by [ChatGPT Exporter](https://www.chatgptexporter.com)
"""


@pytest.fixture
def planned(seeded):
    return seeded, ao.plan(seeded, EXPORT)


def mine(conn):
    return table(conn, """SELECT d.archive_ref, o.said_by, (o.said_at AT TIME ZONE 'America/New_York')::date::text FROM im.observations o
                          JOIN im.ideas d USING (idea_id) WHERE o.source = 'archive' ORDER BY 1, 3""")


def test_each_message_goes_to_the_idea_chatgpt_numbered_it(planned):
    conn, p = planned
    assert p["messages"] == 4 and len(p["observations"]) == 3
    assert [(r["idea"], r["said_at"].isoformat()) for r in p["observations"]] == [
        (1, "2026-07-28T10:08:30-04:00"), (1, "2026-08-19T21:56:14-04:00"), (2, "2026-08-19T21:56:14-04:00")]
    assert p["not_ideas"] == ["2026-07-28 10:08: This chat is my Idea Machine. Capture ideas, don't evaluate them."]
    assert p["numbers_not_in_archive"] == [9] and p["archive_ideas_without_a_message"] == [3]
    assert p["observations"][0]["matches_archive"] == 1.0   # my words, the summary's cleaned-up quote
    assert table(conn, "SELECT count(*) FROM im.observations") == [(0,)]   # planning writes nothing


def test_writing_adds_them_once_and_edges_cite_them(planned):
    conn, p = planned
    assert ao.write(conn, p) == {"added": 3, "already_there": 0, "cited_links": 1}
    assert ao.write(conn, ao.plan(conn, EXPORT))["added"] == 0
    assert mine(conn) == [("chatgpt#1", "me", "2026-07-28"), ("chatgpt#1", "me", "2026-08-19"),
                          ("chatgpt#2", "me", "2026-08-19")]
    ((cited,),) = table(conn, "SELECT cited_observation_ids FROM im.idea_links WHERE kind = 'archive'")
    assert len(cited) == 3   # the archive's #2-to-#1 link now cites what I said behind both
    assert_invariants(conn)


def test_my_messages_recur_and_someone_elses_dont(seeded):
    ao.write(seeded, ao.plan(seeded, EXPORT, not_me={2}))
    by_ref = {ref: sig for (ref,), sig in
              ((table(seeded, "SELECT archive_ref FROM im.ideas WHERE idea_id = %s", i)[0], s)
               for i, (_, s) in weight.weights(seeded, WeightConfig()).items())}
    assert by_ref["chatgpt#1"]["recurrences"] == 2   # weeks apart in one conversation: two
    assert (by_ref["chatgpt#2"]["recurrences"], by_ref["chatgpt#2"]["others"]) == (0, 1)
    speakers = table(seeded, """SELECT s.speaker FROM im.observation_speaker s
                                JOIN im.observations o USING (observation_id) ORDER BY 1""")
    assert speakers == [("me",), ("me",), ("not_me",)]


def test_the_cards_show_my_words(seeded):
    ao.write(seeded, ao.plan(seeded, EXPORT))
    shown = table(seeded, """SELECT c.quote, c.source, c.speaker FROM pub.card_evidence c
                             JOIN im.ideas d USING (idea_id) WHERE d.archive_ref = 'chatgpt#1' ORDER BY c.said_at""")
    assert shown == [("A solitary unsatisfying grape", "archive", "me"),
                     ("signal may not be true, but it is always correct. and the grape was alone on the plate",
                      "archive", "me")]


def test_an_idea_i_add_gets_what_i_typed(seeded, admin):
    idea = uuid.uuid4()
    payload = {"title": "Calm is a signal", "statement": "Others read your calm more than your words."}
    add(admin, idea, payload)
    add(admin, idea, payload)   # a resend: still one
    assert observations.assign_stage(seeded)["typed"] == 1
    assert observations.assign_stage(seeded)["typed"] == 0
    assert table(seeded, "SELECT quote, said_by, source FROM im.observations WHERE idea_id = %s", idea) == [
        ("Others read your calm more than your words.", "me", "typed")]
    assert_invariants(seeded)


def test_not_an_export():
    with pytest.raises(ValueError, match="not a ChatGPT export"):
        ao.parse("## Prompt:\n7/28/2026, 10:08:02 AM\n\nhello\n")
