"""Idea extraction with Claude, against a fake client: what goes out, what comes back, and what's logged."""

import json
import time
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from im import egress, extract, feedback, importer, router

from helpers import assert_invariants, episodes_with_text, run, table
from test_label import script
from test_pipeline import COFFEE, CORRECTIONS, SECRET, V3_IDENTITY
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
    def respond(text):   # the coffee capture, citing lines 0 and 999 too; and one citing only line 0
        return [dict(c, lines=[0, *c["lines"]]) for c in coffee_item(text)] + \
            [dict(c, quote="nothing on line 0", lines=[0]) for c in coffee_item(text)]
    claude = FakeClaude(respond)
    stats = extract.run_stage(pipe, claude)
    assert stats["read"] == 9 and stats["items"] == 1 and len(claude.calls) == 9   # citing no real line: dropped
    (kind, said_by, segs), = table(pipe, "SELECT kind, said_by, source_segment_ids FROM im.items")
    assert (kind, said_by) == ("joke", "anon A") and len(segs) == 1  # lines 0 and 999 don't exist: dropped
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


def test_naming_a_speaker_sends_nothing_and_renames_the_capture(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = FakeClaude(coffee_item)
    extract.run_stage(pipe, claude)
    sent = len(claude.calls)
    (old_item,), = table(pipe, "SELECT item_id FROM im.items")
    (idea,), = table(pipe, """INSERT INTO im.ideas (title, statement, origin) VALUES ('Coffee', 'Who orders it', 'captured')
                              RETURNING idea_id""")
    pipe.execute("INSERT INTO im.item_matches (item_id, decision, idea_id, judged_by) VALUES (%s, 'new', %s, 'test')",
                 (old_item, idea))
    pipe.execute("INSERT INTO im.idea_evidence VALUES (%s, %s, 'origin', 'test')", (idea, old_item))
    stream.name_speaker("c-conv", "anon A", "Carol")
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    stats = extract.run_stage(pipe, claude)
    assert len(claude.calls) == sent and stats["read"] == stats["reused"] == 0   # same episode, same payload
    assert table(pipe, "SELECT item_id FROM im.items JOIN im.episodes USING (episode_id) WHERE current") == \
        [(old_item,)]
    # the capture now says who spoke by their new name, and its match and evidence never moved
    assert table(pipe, "SELECT said_by FROM pub.evidence WHERE item_id = %s", old_item) == [("Carol",)]
    assert table(pipe, "SELECT said_by FROM im.items WHERE item_id = %s", old_item) == [("anon A",)]  # as recorded
    assert table(pipe, "SELECT decision, idea_id FROM im.item_matches WHERE item_id = %s", old_item) == \
        [("new", idea)]
    assert_invariants(pipe)


def test_merging_voices_reads_that_episode_again(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = FakeClaude(coffee_item)
    extract.run_stage(pipe, claude)
    sent = len(claude.calls)
    stream.name_speaker("c-conv", "anon A", "Alice")   # S2 is S1: what would be sent changed
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert extract.run_stage(pipe, claude)["read"] == 1 and len(claude.calls) == sent + 1
    assert "[3] S1: Who's got the coffee order?" in claude.calls[-1]["messages"][0]["content"]
    assert table(pipe, """SELECT w.said_by FROM im.items i JOIN im.item_said_by w USING (item_id)
                          JOIN im.episodes e USING (episode_id) WHERE e.current""") == [("Alice",)]
    assert_invariants(pipe)


def test_reuse_after_a_rekey_sends_nothing(pipe, cfg, stream, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(importer, "STAGE_VERSION", "import/stream-v3")
        m.setattr(importer, "IDENTITY", V3_IDENTITY)
        run(pipe, cfg, stream)
        claude = FakeClaude(coffee_item)
        extract.run_stage(pipe, claude)
    sent = len(claude.calls)
    run(pipe, cfg, stream)   # every segment rekeyed, so every episode re-derives
    stats = extract.run_stage(pipe, claude, monthly_cap_usd=0)
    assert len(claude.calls) == sent and stats["read"] == 0 and stats["reused"] == 9 and stats["items"] == 1
    assert table(pipe, """SELECT w.said_by FROM im.items i JOIN im.item_said_by w USING (item_id)
                          JOIN im.episodes e USING (episode_id) WHERE e.current""") == [("anon A",)]
    assert table(pipe, """SELECT i.source_segment_ids <@ ARRAY(SELECT es.segment_id FROM im.episode_segments es
                                                              WHERE es.episode_id = i.episode_id)
                          FROM im.items i JOIN im.episodes e USING (episode_id) WHERE e.current""") == [(True,)]
    assert_invariants(pipe)


def test_reuse_keeps_each_duplicate_captures_own_verdict(pipe, cfg, stream, monkeypatch):
    # Claude sometimes captures the same line twice; quote matching alone would carry only one of the verdicts.
    twice = lambda text: coffee_item(text) * 2  # noqa: E731
    with monkeypatch.context() as m:
        m.setattr(importer, "STAGE_VERSION", "import/stream-v3")
        m.setattr(importer, "IDENTITY", V3_IDENTITY)
        run(pipe, cfg, stream)
        extract.run_stage(pipe, FakeClaude(twice))
    a, b = (r[0] for r in table(pipe, "SELECT item_id FROM im.items ORDER BY item_id"))
    extract.decide(pipe, a, "keep")
    extract.decide(pipe, b, "discard")
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(twice), monthly_cap_usd=0)
    feedback.carry_verdicts(pipe)
    assert sorted(table(pipe, """SELECT (SELECT v.verdict FROM im.item_verdicts v WHERE v.item_id = i.item_id
                                         ORDER BY v.decided_at DESC, v.verdict_id DESC LIMIT 1)
                                 FROM im.items i JOIN im.episodes e USING (episode_id) WHERE e.current""")) == \
        [("discard",), ("keep",)]
    assert table(pipe, "SELECT method, count(*) FROM im.verdict_carries GROUP BY 1") == [("reuse", 2)]
    assert_invariants(pipe)


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


def test_a_capture_on_a_replaced_episode_isnt_offered_for_review(pipe, cfg, stream):
    run(pipe, cfg, stream)
    extract.run_stage(pipe, FakeClaude(coffee_item))
    assert len(extract.review_items(pipe)) == 1
    stream.name_speaker("c-conv", "anon A", "Alice")   # S2 is S1: the coffee episode is replaced, and not read yet
    stream.write(stream.dir)
    run(pipe, cfg, stream)
    assert extract.review_items(pipe) == []


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
    cli.main(["pending", "--prompt", "v3"])   # what switching prompts would re-read, and what those reads cost
    out = json.loads(capsys.readouterr().out)
    assert out["episodes_to_extract"] == 9 and out["already_extracted_would_resend"] == 0
    assert out["their_last_reads_cost_usd"] > 0
    monkeypatch.setenv("TZ", "Asia/Tokyo")  # a machine in another time zone renders different payloads
    time.tzset()
    try:
        with pytest.raises(SystemExit):
            cli.main(["pending", "--expect-none-resent"])
        assert json.loads(capsys.readouterr().out)["already_extracted_would_resend"] == 9
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


class FailsOn(FakeClaude):
    """FakeClaude, except that the episode where `needle` was said comes back as `how`: a 400 (no usage), a reply
    that spent its tokens thinking and wrote no text, or JSON cut off at max_tokens (both at 1000 in, 16000 out)."""

    def __init__(self, needle, how, respond=lambda text: []):
        super().__init__(respond)
        self.needle, self.how = needle, how

    def create(self, **kw):
        if self.needle not in kw["messages"][0]["content"]:
            return super().create(**kw)
        self.calls.append(kw)
        if self.how == "400":
            raise anthropic.BadRequestError("prompt is too long", body=None,
                                            response=httpx2.Response(400, request=httpx2.Request("POST", "https://x")))
        content = [SimpleNamespace(type="thinking", thinking="hmm")] if self.how == "no text" else \
            [SimpleNamespace(type="text", text='{"items": [{"kind": "jo')]
        return SimpleNamespace(content=content, stop_reason="max_tokens", model="claude-opus-5-5", _request_id="req_x",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=16000))


@pytest.mark.parametrize("how", ["400", "no text", "cut off"])
def test_a_reply_that_fails_is_logged_and_counted_and_the_next_episodes_are_read(pipe, cfg, stream, how):
    run(pipe, cfg, stream)
    claude = FailsOn("coffee", how)
    stats = extract.run_stage(pipe, claude)
    assert (stats["read"], stats["failed"], len(claude.calls)) == (8, 1, 9)
    (failed,) = table(pipe, "SELECT cardinality(segment_ids), cost_usd, payload LIKE '%%coffee%%' FROM im.egress_log "
                            "WHERE error IS NOT NULL")
    paid = 0.0 if how == "400" else 1000 * 4.0 / 1e6 + 16000 * 20.0 / 1e6   # Opus 5.5
    assert failed[0] > 0 and float(failed[1]) == pytest.approx(paid) and failed[2]
    assert egress.spent_this_month(pipe) == pytest.approx(8 * (1000 * 4.0 + 200 * 20.0) / 1e6 + paid)
    again = extract.run_stage(pipe, claude)   # only the failed episode is asked again
    assert (again["pending"], again["failed"], len(claude.calls)) == (1, 1, 10)


def test_im_run_goes_on_past_a_reply_that_fails_to_the_stages_after_extraction(pipe, cfg, stream, monkeypatch,
                                                                               capsys):
    from im import backends, cli
    from test_triage import HashEmbedder

    run(pipe, cfg, stream)
    monkeypatch.setattr(cli.db, "pipeline_dsn", lambda: pipe.info.dsn + " password=" + pipe.info.password)
    monkeypatch.setattr(cli.db, "stream_dir", lambda: stream.dir)
    monkeypatch.setattr(anthropic, "Anthropic", lambda: FailsOn("coffee", "no text"))
    monkeypatch.setattr(backends, "SentenceEmbedder", lambda model: HashEmbedder())
    cli.main(["run"])
    out = json.loads(capsys.readouterr().out)
    assert (out["extract"]["read"], out["extract"]["failed"]) == (8, 1)
    assert "lattice" in out and "digest" in out and out["route"]["waiting_for_claude"] == 1   # the failed one waits


def test_a_model_with_no_known_price_is_never_sent_to(pipe, cfg, stream):
    run(pipe, cfg, stream)
    claude = FakeClaude()
    stats = extract.run_stage(pipe, claude, model="claude-opus-9")
    assert stats["stopped"] and stats["read"] == 0 and claude.calls == []
    assert table(pipe, "SELECT count(*) FROM im.egress_log") == [(0,)]


def status_error(cls, code, body=None):
    return cls("rejected", body=body, response=httpx2.Response(code, request=httpx2.Request("POST", "https://x")))


@pytest.mark.parametrize("error", [
    status_error(anthropic.NotFoundError, 404),   # a retired model id
    status_error(anthropic.APIStatusError, 402, {"type": "error", "error": {"type": "billing_error"}}),
    status_error(anthropic.AuthenticationError, 401)])
def test_an_error_every_request_would_get_stops_extraction_after_the_first(pipe, cfg, stream, error):
    run(pipe, cfg, stream)
    claude = FakeClaude(error=error)
    stats = extract.run_stage(pipe, claude)
    assert len(claude.calls) == 1 and stats["stopped"] and stats["failed"] == 0
    assert table(pipe, "SELECT count(*), count(error) FROM im.egress_log") == [(1, 1)]   # it went, so it's logged
