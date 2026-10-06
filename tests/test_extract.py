"""Idea extraction with Claude, against a fake client: what goes out, what comes back, and what's logged."""

import json
import time
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from im import egress, extract, router

from helpers import assert_invariants, episodes_with_text, run, table
from test_label import script
from test_pipeline import COFFEE, CORRECTIONS, SECRET
from test_triage import fixtures_iso

class FakeClaude:
    """Answers with the items `respond(payload_text)` returns, and records every request."""

    def __init__(self, respond=lambda text: [], stop_reason="end_turn", error=None):
        self.calls, self.respond, self.stop_reason, self.error = [], respond, stop_reason, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        text = kw["messages"][0]["content"]
        content = [SimpleNamespace(type="text", text=json.dumps({"items": self.respond(text)}))]
        return SimpleNamespace(content=content, stop_reason=self.stop_reason, model="claude-opus-5-5",
                               _request_id="req_1", usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def coffee_item(text):
    """An item for the line where S1 (Carol, anon A in the fixtures) asks about coffee."""
    for line in text.splitlines():
        if "coffee" in line:
            n, rest = line[1:].split("] ", 1)
            return [{"kind": "joke", "said_by": rest.split(":")[0], "quote": "coffee order", "lines": [int(n), 999],
                     "gist": "who orders the coffee", "themes": ["office"], "confidence": 0.8}]
    return []


def test_payload_pseudonymizes_speakers_and_numbers_lines(pipe, cfg, stream):
    stream.conversations["c-conv"].taps = [fixtures_iso(20)]
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    (eid,) = episodes_with_text(pipe, COFFEE)
    p = egress.build(pipe, eid)
    assert "Alice" not in p.text and "anon A" not in p.text
    assert "[1] me: Did you see the build broke again?" in p.text
    assert "[2] S1: Yeah, the cache key changed." in p.text and "[3] S2: Who's got the coffee order?" in p.text
    assert "Speakers: me, S1, S2." in p.text and "I tapped the pendant at" in p.text
    assert p.speakers == {"me": "me", "S1": "Alice", "S2": "anon A"} and len(p.segment_ids) == 6


def test_extraction_maps_items_back_and_logs_what_went_out(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = FakeClaude(coffee_item)
    stats = extract.run_stage(pipe, claude)
    assert stats["read"] == 9 and stats["items"] == 1 and len(claude.calls) == 9
    (kind, said_by, segs), = table(pipe, "SELECT kind, said_by, source_segment_ids FROM im.items")
    assert (kind, said_by) == ("joke", "anon A") and len(segs) == 1  # line 999 didn't exist: dropped
    assert table(pipe, "SELECT text FROM im.source_segments WHERE segment_id = %s", segs[0]) == [(COFFEE,)]
    logged = table(pipe, "SELECT count(*), sum(cost_usd)::float, bool_and(payload IS NOT NULL) FROM im.egress_log")
    assert logged == [(9, pytest.approx(9 * (1000 * 4 + 200 * 20) / 1e6), True)]
    call = claude.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == [{"model": "claude-opus-4-8"}]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "Alice" not in json.dumps(claude.calls)  # names never leave, whatever the episode

    assert extract.run_stage(pipe, claude)["read"] == 0  # nothing changed: nothing sent again


def test_only_changed_episodes_go_out_again(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = FakeClaude()
    extract.run_stage(pipe, claude)
    CORRECTIONS["split a turn"][0](stream)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert extract.run_stage(pipe, claude)["read"] == 1


def test_budget_cap_refusals_and_outages(pipe, cfg, stream):
    run(pipe, cfg, stream)
    assert extract.run_stage(pipe, FakeClaude(), monthly_cap_usd=0)["stopped"].startswith("monthly cap")
    assert table(pipe, "SELECT count(*) FROM im.egress_log") == [(0,)]

    down = FakeClaude(error=anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")))
    stats = extract.run_stage(pipe, down)
    assert stats["stopped"].startswith("API unavailable") and stats["read"] == 0
    assert table(pipe, "SELECT count(*) FROM im.extractions") == [(0,)]  # retried next run
    assert table(pipe, "SELECT error IS NOT NULL FROM im.egress_log") == [(True,)]

    stats = extract.run_stage(pipe, FakeClaude(coffee_item, stop_reason="refusal"))
    assert stats["refused"] == 9 and stats["items"] == 0
    assert table(pipe, "SELECT count(*), sum(n_items) FROM im.extractions") == [(9, 0)]


def test_forgetting_purges_items_and_payloads_but_reports_what_was_sent(pipe, cfg, stream):
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(lambda t: [{"kind": "idea", "said_by": "me", "quote": "x", "lines": [2],
                                                    "gist": "x", "themes": [], "confidence": 0.9}]
                                       if "Private detail" in t else []))
    (item,) = [r[0] for r in table(pipe, "SELECT item_id FROM im.items")]
    extract.decide(pipe, item, "keep")
    stream.forget(14406, 14412)
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert table(pipe, "SELECT count(*) FROM im.items") == [(0,)]
    assert table(pipe, "SELECT count(*) FROM im.item_verdicts") == [(0,)]
    assert table(pipe, "SELECT count(*) FROM im.egress_log WHERE payload LIKE %s", f"%{SECRET}%") == [(0,)]
    (sent,) = extract.forgotten_sent(pipe)
    assert sent[3] == 1
    assert_invariants(pipe)


def test_router_v2_routes_on_items_taps_and_notes_and_respects_discards(pipe, cfg, stream):
    stream.conversations["c-forget"].taps = [fixtures_iso(14408)]
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    first = router.run_stage(pipe)
    assert first["review"] == 1 and first["waiting_for_claude"] == 8
    extract.run_stage(pipe, FakeClaude(coffee_item))
    stats = router.run_stage(pipe)
    assert stats["review"] == 2 and stats["auto_file"] == 7 and stats["waiting_for_claude"] == 0
    assert router.run_stage(pipe)["changed"] == 0
    (coffee,) = episodes_with_text(pipe, COFFEE)
    assert table(pipe, "SELECT reasons FROM im.routes WHERE episode_id = %s", coffee) == [(["claude:joke"],)]
    (item,) = [r[0] for r in table(pipe, "SELECT item_id FROM im.items")]
    extract.decide(pipe, item, "discard")
    router.run_stage(pipe)
    assert table(pipe, "SELECT route FROM im.routes WHERE episode_id = %s", coffee) == [("auto_file",)]


def test_reviewing_items(pipe, cfg, stream):
    from im import cli

    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(coffee_item))
    read, write, out = script("d", "a song lyric, not an idea", "q")
    assert cli._review_items(pipe, extract, read, write) == 1
    assert table(pipe, "SELECT verdict, quote, note FROM im.item_verdicts") == \
        [("discard", "coffee order", "a song lyric, not an idea")]
    assert extract.review_items(pipe) == []
    (d,) = extract.discards(pipe)
    assert d[2] == "coffee order" and d[6] == "a song lyric, not an idea"


@pytest.mark.parametrize("state, items, expect", [
    ("me: nothing much", [], ("auto_file", [])),
    ("me: Note to self, buy batteries", None, ("review", ["note_to_self"])),
    ("Alice: note to self, she said", [], ("auto_file", [])),
    ("me: hm", ["idea", "joke", "idea"], ("review", ["claude:idea", "claude:joke"])),
])
def test_router_rules(state, items, expect):
    from im.triage import EpisodeState

    assert router.route(EpisodeState(1, state), items) == expect
    assert router.route(EpisodeState(1, state, taps=("t",)), items)[1][0] == "tap"


def test_im_run_goes_on_without_claude(pipe, cfg, stream, monkeypatch, capsys):
    from im import cli

    run(pipe, cfg, stream)
    monkeypatch.setattr(cli.db, "pipeline_dsn", lambda: pipe.info.dsn + " password=" + pipe.info.password)
    monkeypatch.setattr(cli.db, "stream_dir", lambda: stream.dir)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_PROFILE", "im-test-no-such-profile")
    cli.main(["run"])
    out = json.loads(capsys.readouterr().out)
    assert out["extract"]["stopped"].startswith("Claude")
    assert out["route"]["waiting_for_claude"] == 9


def test_pending_reports_what_would_be_resent(pipe, cfg, stream, monkeypatch, capsys):
    from im import cli

    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude())
    monkeypatch.setattr(cli.db, "pipeline_dsn", lambda: pipe.info.dsn + " password=" + pipe.info.password)
    cli.main(["pending", "--expect-none-resent"])
    assert json.loads(capsys.readouterr().out)["already_extracted_would_resend"] == 0
    monkeypatch.setenv("TZ", "Asia/Tokyo")  # a machine in another time zone renders different payloads
    time.tzset()
    try:
        with pytest.raises(SystemExit):
            cli.main(["pending", "--expect-none-resent"])
        assert json.loads(capsys.readouterr().out)["already_extracted_would_resend"] == 9
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
