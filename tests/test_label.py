"""Labels and the projects registry."""

import pytest

from im import label, pipeline, projects
from im.config import Config, SegmentConfig

from helpers import episodes_with_text, run, table
from test_pipeline import BACKUP, COFFEE, CORRECTIONS, HOSTING, SECRET

ANSWERS = {"boundaries": "ok", "is_self_thinking": False, "kind": "chatter", "project": None, "keep_score": 2}


def script(*lines):
    """A `read` that answers prompts in order, and a `write` that records output."""
    it, out = iter(lines), []
    return (lambda prompt="": next(it)), out.append, out


def label_text(pipe, text, **answers):
    (eid,) = episodes_with_text(pipe, text)
    return label.save(pipe, eid, ANSWERS | answers, None)


def status_of(pipe, label_id):
    st, eps = label.resolve_all(pipe)[label_id]
    return st, eps


def test_a_scripted_session_saves_labels(pipe, cfg, stream):
    run(pipe, cfg, stream)
    projects.add(pipe, "garden", "Garden sensors", ["lora"])
    read, write, out = script(
        "",                              # label the first episode
        "o", "maybe", "y", "i", "+backups", "Backup verification", "4", "worth a task", "y",
        "s",                             # skip the second
        "", "m", "n", "c", "0", "1", "", "r",   # redo...
        "s", "n", "n", "1", "2", "", "y",       # ...then save
        "q")
    assert label.session(pipe, read, write) == 2
    rows = table(pipe, "SELECT answers, note FROM im.labels ORDER BY label_id")
    assert rows[0] == ({"boundaries": "ok", "is_self_thinking": True, "kind": "idea", "project": "backups",
                        "keep_score": 4}, "worth a task")
    assert rows[1] == ({"boundaries": "split", "is_self_thinking": False, "kind": "noise", "project": "backups",
                        "keep_score": 2}, None)
    assert any("one of: y, n" in line for line in out)  # "maybe" was re-asked
    assert "labeled episodes: 2 / 50" in label.status(pipe)


def test_next_episode_balances_episode_kinds(pipe, cfg, stream):
    run(pipe, cfg, stream)
    seen = []
    while (eid := label.next_episode(pipe)) is not None:
        seen.append(table(pipe, "SELECT kind FROM im.episodes WHERE episode_id = %s", eid)[0][0])
        label.save(pipe, eid, ANSWERS, None)
    # 9 episodes: 5 conversations, 3 monologues, 1 others_only. Each kind comes up before any repeats.
    assert set(seen[:3]) == {"conversation", "monologue", "others_only"}
    assert len(seen) == 9 and label.next_episode(pipe) is None


@pytest.mark.parametrize("name", ["name a speaker", "split a turn", "split a conversation",
                                  "merge conversations", "file as noise"])
def test_labels_follow_corrections(pipe, cfg, stream, name):
    run(pipe, cfg, stream)
    correct, (text,), _ = CORRECTIONS[name]
    lid = label_text(pipe, text)
    other = label_text(pipe, "Decided then.")
    correct(stream)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    st, eps = status_of(pipe, lid)
    assert st == "exact" and eps.isdisjoint({table(pipe, "SELECT episode_id FROM im.labels WHERE label_id = %s",
                                                    lid)[0][0]})
    assert status_of(pipe, other)[0] == "exact"
    assert label.next_episode(pipe) not in eps


def test_labels_survive_reset_and_go_partial_when_boundaries_move(pipe, cfg, stream):
    run(pipe, cfg, stream)
    lid = label_text(pipe, COFFEE)
    pipeline.reset(pipe, "segment")
    run(pipe, cfg, stream)
    assert status_of(pipe, lid)[0] == "exact"
    run(pipe, Config(segment=SegmentConfig(gap_s=600)), stream)  # c-conv's two episodes merge
    st, eps = status_of(pipe, lid)
    assert st == "partial" and len(eps) == 1
    assert label.next_episode(pipe) is not None


def test_forgetting_deletes_labels_and_nothing_else(pipe, cfg, stream):
    run(pipe, cfg, stream)
    forgotten = label_text(pipe, SECRET)
    kept = label_text(pipe, BACKUP)
    stream.forget(14406, 14412)
    stream.write(stream.dir)
    stats = run(pipe, cfg, stream)
    assert stats["labels_purged"] == 1
    assert [r[0] for r in table(pipe, "SELECT label_id FROM im.labels")] == [kept]
    assert forgotten not in label.resolve_all(pipe)


def test_answers_are_validated(pipe, cfg, stream):
    run(pipe, cfg, stream)
    (eid,) = episodes_with_text(pipe, HOSTING)
    for bad in [{"kind": "rant"}, {"keep_score": 0}, {"project": "nope"}, {"is_self_thinking": "y"}]:
        with pytest.raises(ValueError):
            label.save(pipe, eid, ANSWERS | bad, None)
    with pytest.raises(ValueError):
        label.save(pipe, eid, {k: v for k, v in ANSWERS.items() if k != "kind"}, None)
    assert table(pipe, "SELECT count(*) FROM im.labels") == [(0,)]


def test_projects_registry(pipe):
    assert projects.add(pipe, "garden", "Garden sensors", ["lora"]) == "added"
    assert projects.add(pipe, "garden", None, ["soil", "lora"]) == "updated"
    assert projects.active(pipe) == [("garden", ["lora", "soil"], "Garden sensors")]
    with pytest.raises(ValueError):
        projects.add(pipe, "new-one", None, [])
    projects.retire(pipe, "garden")
    assert projects.active(pipe) == []
    assert "[retired]" in projects.listing(pipe)
    projects.add(pipe, "garden", None, [])  # adding again un-retires
    assert [p[0] for p in projects.active(pipe)] == ["garden"]
