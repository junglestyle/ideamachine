"""Other models through OpenRouter: the same Reply as Claude's, the request's own cost, one retry on bad JSON."""

import json

import pytest

from im import llm

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


def test_an_answer_that_isnt_json_is_asked_for_again_and_both_are_paid_for():
    client, sent = router(answer("Sure! Here you go"), answer(json.dumps({"items": [1]})))
    r = llm.complete(client, "openai/gpt-6.1-sol", "medium", "s", SCHEMA, "t")
    assert len(sent) == 2 and json.loads(r.text) == {"items": [1]} and r.cost_usd == 0.002
    client, _ = router(answer("no"), answer("still no"))
    with pytest.raises(RuntimeError):
        llm.complete(client, "openai/gpt-6.1-sol", "medium", "s", SCHEMA, "t")


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
