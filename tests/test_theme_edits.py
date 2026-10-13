"""Shaping themes from Lattice: merge, split, file and unfile one idea; all applied the moment they're sent, and
held against the filer and re-filing."""

import json
import uuid

import psycopg
import pytest

from im import themes

from helpers import table
from test_review import as_lattice
from test_themes import FakeThemer, lat, results, themed  # noqa: F401  (lat is a fixture)

EVENT = "INSERT INTO pub.feedback_events (kind, theme_id, idea_id, payload) VALUES (%s, %s, %s, %s)"


def send(admin, kind, theme, idea=None, payload=None):
    return as_lattice(admin, EVENT, kind, theme, idea, json.dumps(payload or {}))


def idea(conn, title_like):
    return table(conn, "SELECT idea_id FROM im.ideas WHERE title LIKE %s", title_like)[0][0]


def members(conn, theme):
    return sorted(t for (t,) in table(conn, """SELECT d.title FROM im.idea_themes it JOIN im.ideas d USING (idea_id)
                                              WHERE it.theme_id = %s""", theme))


def test_a_merge_moves_ideas_and_a_folded_proposal_isnt_proposed_again(lat, admin):
    models, jokes = themes.find(lat, "models"), themes.find(lat, "jokes")
    gadgets = lat.execute("INSERT INTO im.themes (name, origin) VALUES ('Gadgets', 'clustered') RETURNING theme_id"
                          ).fetchone()[0]
    lat.execute("INSERT INTO im.idea_themes (idea_id, theme_id, origin) SELECT idea_id, %s, 'proposed' "
                "FROM im.idea_themes WHERE theme_id = %s", (gadgets, jokes))
    before = members(lat, models) + members(lat, jokes)
    send(admin, "merge_theme", jokes, payload={"into": str(models)})
    send(admin, "merge_theme", gadgets, payload={"into": str(models)})
    send(admin, "merge_theme", jokes, payload={"into": str(models)})   # a resend: already gone
    assert results(lat) == ["merged", "merged", "no such theme"]
    assert members(lat, models) == sorted(set(before))
    assert as_lattice(admin, "SELECT name, pinned FROM pub.themes ORDER BY name") == [("Models and signals", True)]
    assert table(lat, "SELECT name FROM im.theme_rejections") == [("Gadgets",)]   # the archive's isn't a proposal


def test_a_split_makes_my_theme_under_lattices_id(lat, admin):
    models = themes.find(lat, "models")
    inside = members(lat, models)
    moving = idea(lat, inside[0])
    new = uuid.uuid4()
    split = {"new_theme_id": str(new), "name": "Signals proper", "idea_ids": [str(moving), str(uuid.uuid4())]}
    send(admin, "split_theme", models, payload=split)
    send(admin, "split_theme", models, payload=split)   # a resend
    send(admin, "pin_theme", new)   # Lattice can act on the new theme at once
    assert results(lat) == ["split: 1 moved", "repeat", "pinned"]
    assert as_lattice(admin, "SELECT name, origin, pinned, n_ideas FROM pub.themes WHERE theme_id = %s", new) == \
        [("Signals proper", "mine", True, 1)]
    assert members(lat, models) == inside[1:]
    assert table(lat, "SELECT origin FROM im.idea_themes WHERE theme_id = %s", new) == [("mine",)]


def test_filing_by_hand_holds_against_the_filer_and_refiling(lat, admin):
    claude = FakeThemer()
    themes.file_stage(lat, claude)
    models, jokes = themes.find(lat, "models"), themes.find(lat, "jokes")
    builds = idea(lat, "Broken builds%")
    assert themed(lat, "Broken builds%") == ["Models and signals"]   # the filer's
    send(admin, "unfile_idea", models, builds)
    send(admin, "file_idea", jokes, builds)
    assert results(lat) == ["unfiled", "filed"]
    send(admin, "unfile_idea", jokes, builds)   # now it has no theme: the filer will look at it again
    lat.execute("INSERT INTO im.themes (name, origin) VALUES ('Another', 'mine')")   # a new set of themes
    themes.file_stage(lat, claude)   # would file it under signals again
    assert themed(lat, "Broken builds%") == []   # but I unfiled it from there

    send(admin, "file_idea", models, builds)   # filing it back lifts the block
    as_lattice(admin, "INSERT INTO pub.feedback_events (kind, idea_id, payload) VALUES ('idea_correct', %s, %s)",
               builds, json.dumps({"title": "Builds break on Fridays"}))
    assert themes.refile_corrected(lat) == 1   # a corrected idea loses only the filer's placements
    assert themed(lat, "Broken builds%") == ["Models and signals"]


def test_theme_events_are_checked(lat, admin):
    models = themes.find(lat, "models")
    builds = idea(lat, "Broken builds%")
    for kind, theme, i, payload in [
            ("pin_theme", None, None, {}), ("rename_theme", models, None, {"name": " "}),
            ("rename_theme", models, None, {"name": "x" * 81}), ("merge_theme", models, None, {"into": str(models)}),
            ("merge_theme", models, None, {"into": "nope"}), ("merge_theme", models, None, {}),
            ("split_theme", models, None, {"name": "A", "idea_ids": [str(builds)]}),   # no new_theme_id
            ("split_theme", models, None, {"new_theme_id": str(uuid.uuid4()), "name": "A", "idea_ids": []}),
            ("file_idea", models, None, {}), ("unfile_idea", None, builds, {})]:
        with pytest.raises(psycopg.errors.CheckViolation):
            send(admin, kind, theme, i, payload)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        as_lattice(admin, "DELETE FROM im.idea_themes")


class FakeDescriber:
    def __init__(self):
        from types import SimpleNamespace
        self.calls, self.ns = [], SimpleNamespace
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        out = {"themes": [{"theme": 1, "description": "Signals in the narrow sense."}]}
        ns = self.ns
        return ns(content=[ns(type="text", text=json.dumps(out))], stop_reason="end_turn", model="claude-opus-5-5",
                  _request_id="r", usage=ns(input_tokens=300, output_tokens=40))


def test_a_theme_i_split_off_gets_a_description_on_the_run(lat, admin):
    models = themes.find(lat, "models")
    new = uuid.uuid4()
    send(admin, "split_theme", models, payload={"new_theme_id": str(new), "name": "Signals proper",
                                                "idea_ids": [str(idea(lat, members(lat, models)[0]))]})
    claude = FakeDescriber()
    assert themes.describe_stage(lat, claude) == {"pending": 1, "described": 1, "stopped": None}
    assert "Signals proper" in claude.calls[0]["messages"][0]["content"]
    assert table(lat, "SELECT description FROM im.themes WHERE theme_id = %s", new) == [("Signals in the narrow sense.",)]
    assert themes.describe_stage(lat, claude)["pending"] == 0 and len(claude.calls) == 1
