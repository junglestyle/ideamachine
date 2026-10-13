"""My vocabulary for Hearsay: rare words from my corrections, my archive and misheard quotes; I approve or remove."""

import json
from types import SimpleNamespace

import psycopg
import pytest

from im import vocab

from helpers import table
from test_review import as_lattice


class FakeFinder:
    """Says quote 1 of every idea mentioning 'Zigbee' misheard it as 'soil moisture'."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        text = kw["messages"][0]["content"]
        if text.startswith("Words:"):   # the archive pick: names only
            answer = {"words": [w for w in text.splitlines()[1:] if w[:1].isupper()]}
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(answer))],
                                   stop_reason="end_turn", model="claude-opus-5-5", _request_id="r",
                                   usage=SimpleNamespace(input_tokens=100, output_tokens=20))
        out = [{"idea": int(block.split(".")[0]), "term": "Zigbee", "heard_as": "soil moisture", "quote": 1}
               for block in text.split("Idea ")[1:] if "Zigbee" in block.split("\n")[0]]
        out.append({"idea": 1, "term": "Zigbee", "heard_as": "not in any quote", "quote": 1})   # refused
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps({"repairs": out}))],
                               stop_reason="end_turn", model="claude-opus-5-5", _request_id="r",
                               usage=SimpleNamespace(input_tokens=400, output_tokens=60))


def vocab_rows(admin):
    return {t: (s, st, h) for t, s, h, st in as_lattice(admin, "SELECT term, source, heard_as, status FROM pub.vocab")}


def test_rare_words():
    assert vocab.rare_words("Gurdjieff's goetia, moderation in salsa; GURDJIEFF again") == ["Gurdjieff", "goetia"]


def test_candidates_approval_and_the_file(seeded_lattice, admin, tmp_path):
    pipe = seeded_lattice
    pipe.execute("""INSERT INTO im.ideas (title, statement, origin, archive_ref)
                    VALUES ('Gurdjieff on attention', 'Self-remembering as definitionally divided.', 'archive', 'x#9')""")
    lora = table(pipe, "SELECT idea_id FROM pub.ideas WHERE title = 'Something new'")[0][0]
    pipe.execute("UPDATE im.ideas SET statement = 'Sensors could report over Zigbee.' WHERE idea_id = %s", (lora,))
    coffee = table(pipe, "SELECT idea_id FROM pub.ideas WHERE statement LIKE '%%coffee%%'")[0][0]
    as_lattice(admin, "INSERT INTO pub.feedback_events (kind, idea_id, payload) VALUES ('idea_correct', %s, %s)",
               coffee, json.dumps({"statement": "The coffee order is the real meeting, as Kierkegaard knew."}))

    claude, path = FakeFinder(), tmp_path / "vocab.json"
    stats = vocab.refresh(pipe, claude, path=str(path))
    assert stats["repair"]["repairs"] == 1 and stats["written"]
    rows = vocab_rows(admin)
    assert rows["Kierkegaard"] == ("correction", "in", None)
    assert rows["Zigbee"] == ("repair", "suggested", "soil moisture")
    assert rows["Gurdjieff"] == ("archive", "suggested", None) and "definitionally" not in rows   # not picked
    assert json.loads(path.read_text()) == {"format_version": 1,
                                            "terms": [{"term": "Kierkegaard", "source": "correction"}]}
    assert oct(path.stat().st_mode & 0o777) == "0o644"

    event = "INSERT INTO pub.feedback_events (kind, payload) VALUES (%s, %s)"
    as_lattice(admin, event, "vocab_add", json.dumps({"term": "zigbee", "source": "repair"}))
    as_lattice(admin, event, "vocab_remove", json.dumps({"term": "GURDJIEFF"}))
    as_lattice(admin, event, "vocab_add", json.dumps({"term": "Bachata"}))
    again = vocab.refresh(pipe, claude, path=str(path))
    assert again["repair"]["asked"] == 0 and again["archive"]["asked"] is False   # checked and picked already
    rows = vocab_rows(admin)
    assert rows["zigbee"] == ("repair", "in", "soil moisture") and rows["Gurdjieff"][1] == "removed"
    assert rows["Bachata"] == ("added", "in", None)
    assert json.loads(path.read_text())["terms"] == [
        {"term": "Bachata", "source": "added"}, {"term": "Kierkegaard", "source": "correction"},
        {"term": "zigbee", "source": "repair", "heard_as": "soil moisture"}]
    assert vocab.refresh(pipe, claude, path=str(path))["written"] is False   # unchanged: not rewritten

    for bad in [{}, {"term": " "}, {"term": "x" * 61}, {"term": "two\nlines"}, {"term": "ok", "source": "guess"}]:
        with pytest.raises(psycopg.errors.CheckViolation):
            as_lattice(admin, event, "vocab_add", json.dumps(bad))
    with pytest.raises(psycopg.errors.CheckViolation):
        as_lattice(admin, event, "vocab_remove", json.dumps({"term": "ok", "source": "added"}))
