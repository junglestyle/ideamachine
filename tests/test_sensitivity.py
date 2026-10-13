"""Demo mode: Claude tags what I'd hesitate to show in an interview; my own marks win, at once."""

import json
from types import SimpleNamespace

import psycopg
import pytest

from im import sensitivity, themes

from helpers import table
from test_review import as_lattice


class FakeTagger:
    """Tags any line mentioning `word` as violence."""

    def __init__(self, word="coffee"):
        self.calls, self.word = [], word
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        text = kw["messages"][0]["content"]
        ideas, th = text.split("\n\nThemes:\n")
        def tag(block):
            return [{"n": int(ln.split(".")[0]), "categories": ["violence"] if self.word in ln.lower() else []}
                    for ln in block.splitlines() if ln[:1].isdigit()]
        out = {"ideas": tag(ideas), "themes": tag(th)}
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(out))], stop_reason="end_turn",
                               model="claude-opus-5-5", _request_id="r",
                               usage=SimpleNamespace(input_tokens=800, output_tokens=200))


def shown(admin):
    return {(k, str(i)): (s, c, b) for k, i, s, c, b in as_lattice(
        admin, 'SELECT kind, id, sensitive, categories, "by" FROM pub.sensitivity')}


def test_claude_tags_once_and_again_when_the_text_changes(seeded_lattice, admin):
    pipe = seeded_lattice
    claude = FakeTagger()
    n_ideas = table(pipe, "SELECT count(*) FROM pub.ideas")[0][0]
    n_themes = table(pipe, "SELECT count(*) FROM im.themes")[0][0]
    stats = sensitivity.tag_stage(pipe, claude)
    assert stats["pending"] == stats["tagged"] == n_ideas + n_themes and stats["sensitive"] == 1
    assert len(claude.calls) == 1
    coffee = table(pipe, "SELECT idea_id FROM pub.ideas WHERE statement LIKE '%%coffee%%'")[0][0]
    assert shown(admin) == {("idea", str(coffee)): (True, ["violence"], "claude")}   # untagged: not listed
    assert sensitivity.tag_stage(pipe, claude)["pending"] == 0

    as_lattice(admin, "INSERT INTO pub.feedback_events (kind, idea_id, payload) VALUES ('idea_correct', %s, %s)",
               coffee, json.dumps({"title": "Tea runs it", "statement": "The tea order is the real meeting."}))
    stats = sensitivity.tag_stage(pipe, claude)   # the changed idea alone, read again
    assert (stats["pending"], stats["tagged"]) == (1, 1) and "The tea order" in claude.calls[-1]["messages"][0]["content"]


def test_my_marks_win_at_once_either_way(seeded_lattice, admin):
    pipe = seeded_lattice
    sensitivity.tag_stage(pipe, FakeTagger())
    coffee = table(pipe, "SELECT idea_id FROM pub.ideas WHERE statement LIKE '%%coffee%%'")[0][0]
    jokes = themes.find(pipe, "jokes")
    mark = "INSERT INTO pub.feedback_events (kind, idea_id, theme_id, payload) VALUES (%s, %s, %s, %s)"
    as_lattice(admin, mark, "idea_sensitive", coffee, None, json.dumps({"sensitive": False}))
    as_lattice(admin, mark, "theme_sensitive", None, jokes, json.dumps({"sensitive": True}))
    assert shown(admin) == {("idea", str(coffee)): (False, ["violence"], "owner"),
                            ("theme", str(jokes)): (True, [], "owner")}
    for kind, idea, theme, payload in [("idea_sensitive", coffee, None, {}),
                                       ("idea_sensitive", None, None, {"sensitive": True}),
                                       ("idea_sensitive", coffee, None, {"sensitive": "yes"}),
                                       ("theme_sensitive", None, None, {"sensitive": True})]:
        with pytest.raises(psycopg.errors.CheckViolation):
            as_lattice(admin, mark, kind, idea, theme, json.dumps(payload))
    themes.record(pipe, "reject_theme", jokes)   # not a proposal: stays; a merge would leave the mark harmless
    other = themes.find(pipe, "models")
    as_lattice(admin, "INSERT INTO pub.feedback_events (kind, theme_id, payload) VALUES ('merge_theme', %s, %s)",
               jokes, json.dumps({"into": str(other)}))
    assert ("theme", str(jokes)) not in shown(admin)   # the theme is gone; its old mark stays as history


def test_quotes_are_read_too(seeded_lattice, admin):
    pipe = seeded_lattice
    claude = FakeTagger(word="lora")   # only the LoRa idea's quote says "LoRa"; its title doesn't
    sensitivity.tag_stage(pipe, claude)
    lora = table(pipe, "SELECT idea_id FROM pub.ideas WHERE title = 'Something new'")[0][0]
    assert ("idea", str(lora)) in shown(admin)
    assert '[said: "' in claude.calls[0]["messages"][0]["content"]
