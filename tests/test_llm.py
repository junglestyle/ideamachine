"""Other models through OpenRouter: the same Reply as Claude's, the request's own cost, one retry on bad JSON."""

import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from im import egress, jev, llm

SCHEMA = {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}


def answer(content, finish="stop", cost=0.001, refusal=None):
    return {"id": "gen-1", "model": "z-ai/glm-5.3", "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                                              "cost": cost},
            "choices": [{"finish_reason": finish, "message": {"content": content, "refusal": refusal}}]}


def router(*answers):
    sent, queue = [], list(answers)

    def post(body):
        sent.append(body)
        return queue.pop(0)
    return llm.OpenRouter(post=post), sent


def test_a_model_with_a_provider_prefix_goes_to_openrouter_with_the_schema():
    client, sent = router(answer('{"items": []}'))
    r = llm.complete(client, "z-ai/glm-5.3", "low", "system", SCHEMA, "text")
    assert (r.text, r.stop_reason, r.served_by, r.cost_usd, r.input_tokens) == ('{"items": []}', "end_turn",
                                                                                "z-ai/glm-5.3", 0.001, 100)
    body = sent[0]
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA and body["reasoning"] == {"effort": "low"}
    assert body["provider"]["data_collection"] == "deny" and body["messages"][0] == {"role": "system",
                                                                                     "content": "system"}
    assert not llm.via_openrouter("claude-opus-5-5")


def test_under_zero_data_retention_claude_goes_through_openrouter_to_endpoints_that_keep_nothing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert isinstance(llm.client_for("claude-opus-5-5", zdr=True), llm.OpenRouter)
    sent = []
    client = llm.OpenRouter(post=lambda body: sent.append(body) or answer('{"items": []}'), zdr=True)
    llm.complete(client, "claude-opus-5-5", "medium", "system", SCHEMA, "text")
    body = sent[0]
    assert body["model"] == "anthropic/claude-opus-5.5" and body["provider"]["zdr"] is True
    # Its system prompt is cached, as through Anthropic; without the mark, Claude caches nothing.
    assert body["messages"][0]["content"] == [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}]
    # At its list price: the regional endpoints listed 10% above it are left out.
    assert body["provider"]["max_price"] == {"prompt": 4.0, "completion": 20.0}
    llm.complete(client, "z-ai/glm-5.3", "low", "system", SCHEMA, "text")
    assert "max_price" not in sent[-1]["provider"]   # no list price of ours for other models


def test_an_answer_that_isnt_json_is_asked_for_again_and_both_are_paid_for():
    client, sent = router(answer("Sure! Here you go"), answer(json.dumps({"items": [1]})))
    r = llm.complete(client, "openai/gpt-6.1-sol", "medium", "s", SCHEMA, "t")
    assert len(sent) == 2 and json.loads(r.text) == {"items": [1]} and r.cost_usd == 0.002
    client, _ = router(answer("no"), answer("still no"))   # as it came, both paid for: the egress log fails it
    r = llm.complete(client, "openai/gpt-6.1-sol", "medium", "s", SCHEMA, "t")
    assert r.text == "still no" and r.cost_usd == 0.002


def test_a_refusal_and_a_rate_limit():
    client, _ = router(answer(None, finish="content_filter"))
    r = llm.complete(client, "openai/gpt-6.1-terra", "low", "s", SCHEMA, "t")
    assert r.refused and r.text is None
    client, _ = router({"error": {"code": 429, "message": "slow down"}})
    with pytest.raises(llm.Unavailable):
        llm.complete(client, "openai/gpt-6.1-terra", "low", "s", SCHEMA, "t")


def test_a_model_that_takes_no_schema_is_asked_with_it_in_the_prompt():
    llm.NO_SCHEMA.discard("some/no-schema-model")
    sent = []

    def post(body):
        sent.append(body)
        if "response_format" in body:
            raise RuntimeError("OpenRouter HTTP 404: No endpoints found that can handle the requested parameters")
        return answer('```json\n{"items": []}\n```')
    client = llm.OpenRouter(post=post)
    r = llm.complete(client, "some/no-schema-model", "low", "decide", SCHEMA, "text")
    assert r.text == '{"items": []}' and len(sent) == 2
    assert "JSON Schema" in sent[1]["messages"][0]["content"] and not sent[1]["provider"]["require_parameters"]
    assert sent[1]["provider"]["data_collection"] == "deny"
    llm.complete(client, "some/no-schema-model", "low", "decide", SCHEMA, "text")
    assert len(sent) == 3   # remembered: no second try with a schema


def test_no_provider_that_keeps_nothing_is_said_plainly():
    def post(body):
        raise RuntimeError("OpenRouter HTTP 404: No endpoints found matching your data policy")
    with pytest.raises(RuntimeError, match="keeps nothing sent"):
        llm.complete(llm.OpenRouter(post=post), "some/model", "low", "s", SCHEMA, "t")


def test_claudes_default_budget_is_one_the_sdk_sends_and_one_it_wouldnt_is_never_tried():
    import anthropic
    sdk = anthropic.Anthropic(api_key="x")
    sdk._calculate_nonstreaming_timeout(llm.max_tokens_for("claude-opus-5-5"), None)   # raises past 10 minutes
    with pytest.raises(ValueError):
        sdk._calculate_nonstreaming_timeout(llm.CLAUDE_CEILING + 1, None)
    with pytest.raises(llm.Stop, match="streamed"):
        llm.complete(SimpleNamespace(), "claude-opus-5-5", "low", "s", SCHEMA, "t", llm.CLAUDE_CEILING + 1)
    assert llm.max_tokens_for("deepseek/deepseek-v4.1-flash") == 32000


def test_a_dated_model_id_is_priced_as_its_model_and_an_unknown_one_never_as_free():
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0)
    assert llm.cost("claude-opus-5-5-20261015", usage) == llm.cost("claude-opus-5-5", usage) == 4.0
    assert llm.cost("claude-mystery-9", usage) == max(p[0] for p in llm.PRICES.values())


@pytest.mark.parametrize("code, raised, tries", [(429, llm.Unavailable, 4), (503, llm.Unavailable, 4),
                                                 (402, llm.Stop, 1), (400, RuntimeError, 1)])
def test_a_rate_limit_or_server_error_is_retried_and_other_http_errors_arent(monkeypatch, code, raised, tries):
    sent, waited = [], []

    def fail(req, timeout):
        sent.append(req)
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(b"detail"))
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    monkeypatch.setattr(llm.time, "sleep", waited.append)
    with pytest.raises(raised):
        llm.complete(llm.OpenRouter(key="k"), "z-ai/glm-5.3", "low", "s", SCHEMA, "t")
    assert len(sent) == tries and waited == list(llm.RETRY_DELAYS)[:tries - 1]
    with pytest.raises(raised):
        jev.Jev(key="k", route="typesafe").complete({"questions": {}})


def test_a_hiccup_that_clears_is_retried_and_answered(monkeypatch):
    replies = [urllib.error.URLError("handshake timed out"),
               io.BytesIO(json.dumps({"error": {"code": 503, "message": "provider down"}}).encode()),
               io.BytesIO(json.dumps(answer('{"items": []}')).encode())]

    def flaky(req, timeout):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    r = llm.complete(llm.OpenRouter(key="k"), "z-ai/glm-5.3", "low", "s", SCHEMA, "t")
    assert r.text == '{"items": []}' and not replies


@pytest.mark.parametrize("second, stops", [({"error": {"code": 503, "message": "down"}}, True),
                                           ({"error": {"code": 400, "message": "bad"}}, False),
                                           ({"id": "no choices"}, False)])
def test_a_retry_that_fails_still_logs_and_counts_what_the_first_attempt_cost(pipe, second, stops):
    client, sent = router(answer("Sure! Here you go", cost=0.003), second)
    try:
        a = egress.ask(pipe, client, "z-ai/glm-5.3", "low", "s", SCHEMA, "t", segment_ids=[], prompt_version="v",
                       monthly_cap_usd=1)
        assert not stops and a.failed and a.cost_usd == 0.003
    except llm.Unavailable:
        assert stops
    assert len(sent) == 2 and egress.spent_this_month(pipe) == pytest.approx(0.003)
