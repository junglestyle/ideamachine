"""The merge screen, `im merges`: pairs of clusters that may be one point, likeliest first by Jev's same_v2."""

import json
import re

import pytest

from im import connect, jev

from helpers import table
from test_merge import EVENT, idea


def fake_jev(same_v2):
    """Jev answering the match questions, `same_v2(text)` for that one (text: both sides' summaries), recording every
    request."""
    sent = []

    def post(body):
        sent.append(body)
        text = json.dumps(body["state"])
        return {"model": jev.MODEL, "usage": {"input_tokens": 300, "output_tokens": 4},
                "answers": {q: {"type": "noul", "noul": same_v2(text) if q == "same_v2" else 0.5}
                            for q in body["questions"]}}
    return jev.Jev(post=post), sent


def likelier(text: str) -> float:
    if "grape" in text and "Signal" in text:
        return 0.95
    return 0.6 if "coffee" in text and "Broken builds" in text else 0.1


@pytest.fixture
def screened(seeded_lattice, monkeypatch, capsys):
    """`im merges` on the seeded lattice, every pair of its five cards near; returns a function running it with args,
    which gives what it printed, and the list of what was sent to Jev."""
    from im import cli

    monkeypatch.setattr(connect, "FLOOR", -1.0)
    monkeypatch.setattr(cli.db, "pipeline_dsn",
                        lambda: seeded_lattice.info.dsn + " password=" + seeded_lattice.info.password)
    client, sent = fake_jev(likelier)
    monkeypatch.setattr(jev, "Jev", lambda: client)

    def merges(*args):
        cli.main(["merges", *args])
        return capsys.readouterr().out
    return merges, sent


def blocks(out: str) -> list[tuple[str, str]]:
    """(same_v2, the two titles) of each pair printed, in order."""
    found = re.findall(r"^same_v2 (\S+(?: asked)?) .*\n  \S+  (.*)\n.*\n  \S+  (.*)$", out, re.M)
    return [(s, " | ".join(sorted((a, b)))) for s, a, b in found]


def test_the_likeliest_duplicates_come_first_and_nothing_but_jevs_answers_is_written(screened, seeded_lattice):
    merges, sent = screened
    grape, coffee = idea(seeded_lattice, "grape"), idea(seeded_lattice, "coffee")
    seeded_lattice.execute("""INSERT INTO im.merge_review (kind, idea_ids, method)
                              VALUES ('judged_same', %s, 'connect-1:test')""", (sorted([grape, coffee]),))
    out = merges("--top", "3")
    assert len(sent) == 10   # every pair of five cards, once
    assert all(set(b["questions"]) == set(jev.QUESTIONS["match"]) for b in sent)
    shown = blocks(out)
    assert [s for s, _ in shown] == ["0.95", "0.60", "0.10"]
    assert "grape" in shown[0][1] and "Signal" in shown[0][1]
    assert "coffee" in shown[1][1] and "Broken builds" in shown[1][1]
    assert "3 of 10 pairs shown; 0 not asked yet" in out
    same = merges("--top", "10", "--limit", "0")   # the review row: listed as such, at its own same_v2
    assert re.search(r"^same_v2 0\.10 .*merge review: judged_same\n  \S+  (A solitary|The coffee)", same, re.M)
    assert table(seeded_lattice, "SELECT count(*), count(merge_id) FROM im.merge_review") == [(1, 0)]
    assert table(seeded_lattice, "SELECT count(*) FROM im.idea_merges") == [(0,)]


def test_a_pair_is_asked_once_until_either_text_changes(screened, seeded_lattice):
    merges, sent = screened
    merges()
    assert len(sent) == 10
    assert "0 not asked yet" in merges() and len(sent) == 10   # all kept: nothing sent
    grape = idea(seeded_lattice, "grape")
    seeded_lattice.execute(EVENT, ("idea_correct", grape, None, json.dumps({"statement": "One grape, let down."})))
    assert "4 not asked yet" in merges("--limit", "0") and len(sent) == 10
    merges()
    assert len(sent) == 14 and all("One grape, let down." in json.dumps(b["state"]) for b in sent[10:])


def test_merged_clusters_and_pairs_i_set_apart_are_left_out(screened, seeded_lattice, monkeypatch, capsys):
    from test_merge import merge

    merges, sent = screened
    builds, lora, grape, signal, coffee = (idea(seeded_lattice, w)
                                           for w in ("Broken builds", "Something new", "grape", "Signal", "coffee"))
    merge(seeded_lattice, monkeypatch, capsys, builds, lora)
    seeded_lattice.execute(EVENT, ("unlink", grape, signal, "{}"))
    merge(seeded_lattice, monkeypatch, capsys, "--distinct", coffee, grape)
    out = merges("--top", "10", "--limit", "0")   # sends nothing, and needs no key
    assert "4 of 4 pairs shown; 4 not asked yet" in out and sent == []
    assert str(lora) not in out and "Something new" not in out
    titles = [t for _, t in blocks(out)]
    assert not any("grape" in t and ("Signal" in t or "coffee" in t) for t in titles)
    with pytest.raises(SystemExit, match="distinct from itself"):
        merge(seeded_lattice, monkeypatch, capsys, "--distinct", coffee, coffee)
