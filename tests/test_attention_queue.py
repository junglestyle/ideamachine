"""The attention queue: a pendant tap or my saying "note to self" queues the lines, and nothing else changes."""

import json
from dataclasses import replace

from im import attention, cli, extract
from im.fixtures import iso

from helpers import assert_invariants, run, table
from test_extract import FakeClaude
from test_lattice import _captures_of

BACKUP = "Another thought: the backup job should verify restores weekly."
NOTE = "Note to self: the backup job should verify restores weekly."


def use_test_db(monkeypatch, pipe, stream):
    monkeypatch.setattr(cli.db, "pipeline_dsn", lambda: pipe.info.dsn + " password=" + pipe.info.password)
    monkeypatch.setattr(cli.db, "stream_dir", lambda: stream.dir)


def say_note_to_self(stream, text=NOTE):
    """I say "note to self" where I said BACKUP, and Bob says it too, about himself."""
    turns = stream.conversations["c-mono"].turns
    turns[9] = replace(turns[9], text=text)
    turns[4] = replace(turns[4], text="Note to self, he says. Are you talking to yourself again?")


def marked(item):
    return [ln.text for ln in item.lines if ln.marked]


def everything_but_the_queue(conn):
    """Every row of every table, but the queue's."""
    tables = table(conn, """SELECT table_schema || '.' || table_name FROM information_schema.tables
                            WHERE table_schema IN ('im', 'pub') AND table_type = 'BASE TABLE'
                              AND table_name <> 'attention_items' ORDER BY 1""")
    return {t: sorted(map(repr, table(conn, f"SELECT * FROM {t}"))) for (t,) in tables}


def test_im_run_queues_a_tap_with_my_lines_from_30_s_before_to_30_s_after(pipe, cfg, stream, monkeypatch, capsys):
    stream.conversations["c-mono"].taps = [iso(3660)]
    stream.write(stream.dir)
    use_test_db(monkeypatch, pipe, stream)
    cli.main(["run"])
    assert json.loads(capsys.readouterr().out)["attention"] == {"new": 1, "reanchored": 0, "open": 1}
    (item,) = attention.open_items(pipe)
    assert item.trigger == "tap" and item.conversation_id == "c-mono"
    # Mine within 30 s of the tap, and only mine; everyone's lines 30 s either side are context.
    assert marked(item) == ["Check what the cheap ESP32 boards draw in deep sleep.", "Write that down for the weekend.",
                            "Thinking out loud about the garden thing."]
    context = [ln.text for ln in item.lines if not ln.marked]
    assert "Are you talking to yourself again?" in context and "Maybe, not sure." in context
    assert BACKUP not in context
    cli.main(["queue"])
    out = capsys.readouterr().out
    assert f"#{item.attention_id}  tap" in out and "» me: Write that down for the weekend." in out
    assert out.rstrip().endswith("1 in the queue")


def test_a_note_to_self_in_my_line_queues_it_and_my_next_lines_but_not_in_someone_elses(pipe, cfg, stream):
    say_note_to_self(stream)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert attention.stage(pipe)["new"] == 1
    (item,) = attention.open_items(pipe)
    assert item.trigger == "note_to_self"
    assert marked(item) == [NOTE, "A restore that's never been tested isn't a backup.", "Add a task for that."]


def test_running_again_or_re_transcribing_makes_no_duplicates(pipe, cfg, stream):
    stream.conversations["c-mono"].taps = [iso(3605)]
    say_note_to_self(stream)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert attention.stage(pipe) == {"new": 2, "reanchored": 0, "open": 2}
    ids = [it.attention_id for it in attention.open_items(pipe)]
    run(pipe, cfg, stream)
    assert attention.stage(pipe) == {"new": 0, "reanchored": 0, "open": 2}
    # Hearsay hears the note's line again, differently: the new line takes the note over.
    say_note_to_self(stream, "Note to self: the backup job should verify restores every week.")
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert attention.stage(pipe) == {"new": 0, "reanchored": 1, "open": 2}
    items = attention.open_items(pipe)
    assert [it.attention_id for it in items] == ids
    assert marked(items[0])[0] == "Note to self: the backup job should verify restores every week."
    assert table(pipe, "SELECT count(*) FROM im.attention_items") == [(2,)]


def test_removing_an_item_hides_it_from_im_queue_and_changes_nothing_else(pipe, cfg, stream, monkeypatch, capsys):
    stream.conversations["c-mono"].taps = [iso(3605)]
    say_note_to_self(stream)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(_captures_of("LoRa", "restores weekly")))
    attention.stage(pipe)
    note, tap = attention.open_items(pipe)
    assert table(pipe, "SELECT count(*) FROM im.items") == [(2,)]
    before = everything_but_the_queue(pipe)
    use_test_db(monkeypatch, pipe, stream)
    capsys.readouterr()
    cli.main(["queue", "remove", str(tap.attention_id)])
    assert capsys.readouterr().out == f"removed #{tap.attention_id}\n"
    assert everything_but_the_queue(pipe) == before   # the captures, and everything else, as they were
    run(pipe, cfg, stream)
    assert attention.stage(pipe) == {"new": 0, "reanchored": 0, "open": 1}   # the tap is still listed: still removed
    cli.main(["queue"])
    out = capsys.readouterr().out
    assert f"#{note.attention_id}  note to self" in out and f"#{tap.attention_id} " not in out
    assert out.rstrip().endswith("1 in the queue")


def test_forgetting_takes_lines_out_of_the_queue(pipe, cfg, stream):
    stream.conversations["c-mono"].taps = [iso(3620)]
    say_note_to_self(stream)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    attention.stage(pipe)
    stream.forget(3613, 3628)   # one line the tap marks
    stream.forget(3800, 3814)   # the note's own line
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    attention.stage(pipe)
    (item,) = attention.open_items(pipe)
    assert item.trigger == "tap" and "Battery life is the hard part, maybe a solar trickle charger." not in \
        [ln.text for ln in item.lines]
    assert table(pipe, """SELECT count(*) FROM im.attention_items a JOIN im.source_tombstones t
                          ON t.segment_id = ANY(a.segment_ids)""") == [(0,)]
    assert_invariants(pipe)
