"""Mishearings: proposed by v5 captures, decided by me, and sent as hints that re-read nothing."""

from im import egress, extract, mishearings

from helpers import run, table
from test_extract import FakeClaude


def episode_saying(conn, words):
    return table(conn, """SELECT DISTINCT es.episode_id FROM im.episode_segments es
                          JOIN im.source_segments s USING (segment_id) JOIN im.episodes e USING (episode_id)
                          WHERE e.current AND s.text LIKE %s""", f"%{words}%")[0][0]


def proposes(heard, meant):
    """v5 capturing my line with `heard` in it, its reading fixing it to `meant`."""
    def respond(text):
        for row in text.splitlines():
            if row.startswith("[") and heard in row and row.split(": ", 1)[0].endswith(" me"):
                said = row.split(": ", 1)[1]
                return [{"quote": said, "lines": [int(row[1:].split("]")[0])], "attributed_to": "",
                         "reading": said.replace(heard, meant), "mishearings": [{"heard": heard, "meant": meant}],
                         "gist": said}]
        return []
    return respond


def test_a_proposed_mishearing_is_a_hint_for_what_comes_next_and_re_reads_nothing(pipe, cfg, stream):
    run(pipe, cfg, stream)
    v5, schema = extract.SYSTEM_V5_3, extract.SCHEMA_V5
    episode = episode_saying(pipe, "lockfile")
    before = egress.build(pipe, episode)
    extract.run_stage(pipe, FakeClaude(proposes("lockfile", "lock file")), system=v5, schema=schema)
    assert mishearings.pairs(pipe) == [("lockfile", "lock file", "provisional", 1)]

    after = egress.build(pipe, episode)
    assert 'Known mishearings: "lockfile" for "lock file" (provisional).' in after.text
    assert after.sha256 == before.sha256   # a hint, not a change to what was read
    assert extract.run_stage(pipe, FakeClaude(), system=v5, schema=schema)["pending"] == 0
    assert "Known mishearings" not in egress.build(pipe, episode_saying(pipe, "LoRa")).text   # not said there

    mishearings.decide(pipe, "LOCKFILE", "lock file", "rejected")   # stays rejected, however often it's proposed
    assert mishearings.pairs(pipe) == [("LOCKFILE", "lock file", "rejected", 1)]
    assert "Known mishearings" not in egress.build(pipe, episode).text


def test_a_pair_i_add_is_confirmed_and_goes_out_as_a_hint(pipe, cfg, stream):
    run(pipe, cfg, stream)
    episode = episode_saying(pipe, "LoRa")
    mishearings.decide(pipe, "LoRa", "LoRaWAN", "confirmed")   # nobody proposed it: added, confirmed
    sent = egress.build(pipe, episode).text
    assert "over LoRa." in sent and 'Known mishearings: "LoRa" for "LoRaWAN" (confirmed).' in sent
