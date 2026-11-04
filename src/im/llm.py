"""One request to a model, for a JSON answer to a schema: Claude directly, or any other model through OpenRouter.

A model named with a provider prefix (`openai/gpt-6.1-sol`, `z-ai/glm-5.3`) goes to OpenRouter; a bare `claude-*`
name goes to Anthropic. Both come back as a `Reply`, so the callers (extraction, trials, edge drafts) log and cost
every request the same way. OpenRouter reports what each request cost, so its models need no entry in PRICES.
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

FALLBACK_MODEL = "claude-opus-4-8"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# $ per million tokens (input, output) for Anthropic's models; a refusal fallback may answer on another model.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-opus-4-8": (5.0, 25.0), "claude-sonnet-5-5": (2.0, 10.0),
          "claude-haiku-5-5": (0.10, 0.50)}   # Haiku 5.5: prompts up to 100K tokens, which every episode is
# OpenRouter's finish reasons, in Anthropic's stop-reason words (the egress log's vocabulary).
FINISH = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal", "tool_calls": "tool_use"}


class Unavailable(OSError):
    """The provider couldn't answer now (connection, rate limit, server error): try again on a later run."""


@dataclass(frozen=True)
class Reply:
    text: str | None              # the JSON answer; None when refused
    stop_reason: str
    served_by: str
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


def via_openrouter(model: str) -> bool:
    return "/" in model


def cost(model: str, usage) -> float | None:
    """What an Anthropic request cost, from its usage."""
    price = PRICES.get(model)
    if price is None or usage is None:
        return None
    tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25 \
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
    return round(tokens_in * price[0] / 1e6 + (usage.output_tokens or 0) * price[1] / 1e6, 6)


def client_for(model: str):
    if via_openrouter(model):
        return OpenRouter()
    import anthropic
    return anthropic.Anthropic()


def complete(client, model: str, effort: str, system: str, schema: dict, text: str, max_tokens: int = 16000) -> Reply:
    """Ask once. Opus falls back to FALLBACK_MODEL on a refusal; other models don't, so their refusal counts as one.
    Raises Unavailable when it's worth retrying later."""
    if via_openrouter(model):
        return client.complete(model, effort, system, schema, text, max_tokens)
    import anthropic
    fallback = {"betas": ["server-side-fallback-2026-06-01"], "fallbacks": [{"model": FALLBACK_MODEL}]} \
        if model.startswith("claude-opus") else {}
    try:
        r = client.beta.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": text}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}}, **fallback)
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
        raise Unavailable(f"{type(e).__name__}: {e}") from e
    served = getattr(r, "model", model) or model
    answer = None if r.stop_reason == "refusal" else next(b.text for b in r.content if b.type == "text")
    return Reply(answer, r.stop_reason, served, getattr(r, "_request_id", None), r.usage.input_tokens,
                 r.usage.output_tokens, cost(served, r.usage))


class OpenRouter:
    """OpenRouter's chat completions, asking for strict JSON-schema output and the request's cost. An answer that
    isn't JSON is asked for once more: models differ in how well they keep to a schema."""

    def __init__(self, key: str | None = None, post=None):
        self.key = key or os.environ.get("OPENROUTER_API_KEY")
        if not self.key and post is None:
            raise SystemExit("OPENROUTER_API_KEY isn't set: OpenRouter models need it")
        self.post = post or self._post

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(OPENROUTER_URL, data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
            "X-Title": "Idea Machine"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 429 or e.code >= 500:
                raise Unavailable(f"OpenRouter HTTP {e.code}: {detail}") from None
            raise RuntimeError(f"OpenRouter HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise Unavailable(f"OpenRouter: {e}") from None

    def complete(self, model, effort, system, schema, text, max_tokens) -> Reply:
        body = {"model": model, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": "answer", "strict": True, "schema": schema}},
                "reasoning": {"effort": effort}, "usage": {"include": True},
                # Only providers that honour the schema, and none that keep or train on what's sent.
                "provider": {"require_parameters": True, "data_collection": "deny"}}
        spent, tokens_in, tokens_out = 0.0, 0, 0
        for attempt in (1, 2):
            r = self.post(body)
            if "error" in r and "choices" not in r:
                code = r["error"].get("code")
                msg = f"OpenRouter error {code}: {str(r['error'].get('message'))[:300]}"
                raise (Unavailable if code in (429, 502, 503) else RuntimeError)(msg)
            usage = r.get("usage") or {}
            spent += usage.get("cost") or 0.0
            tokens_in += usage.get("prompt_tokens") or 0
            tokens_out += usage.get("completion_tokens") or 0
            choice = r["choices"][0]
            stop = FINISH.get(choice.get("finish_reason"), choice.get("finish_reason") or "end_turn")
            answer = (choice.get("message") or {}).get("content")
            if stop == "refusal" or (choice.get("message") or {}).get("refusal"):
                answer, stop = None, "refusal"
            elif not _is_json(answer) and attempt == 1:
                continue
            elif not _is_json(answer):
                raise RuntimeError(f"{model} didn't answer in JSON twice: {str(answer)[:200]}")
            return Reply(answer, stop, r.get("model") or model, r.get("id"), tokens_in, tokens_out,
                         round(spent, 6))
        raise AssertionError("unreachable")


def _is_json(text) -> bool:
    try:
        json.loads(text)
        return True
    except (TypeError, ValueError):
        return False
