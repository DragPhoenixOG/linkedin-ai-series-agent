"""Model clients: OpenAI-compatible (Gemini, Groq) and Anthropic, with fake transports."""

from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest

from agent.config import Config
from agent.llm import (
    FALLBACK_BETA,
    AnthropicLLM,
    LLMAuthError,
    LLMError,
    LLMRefusal,
    OpenAICompatLLM,
    create_llm,
    extract_json,
)
from fakes import FakeClient, block, fake_message

PRICING = {
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2},
}


# --- JSON parsing ------------------------------------------------------------------------------


def test_extract_json_prefers_result_tags() -> None:
    text = 'I searched. {"wrong": 1}\n<result>{"right": 2}</result>'
    assert extract_json(text) == {"right": 2}


def test_extract_json_handles_fences_and_prose() -> None:
    assert extract_json('Here:\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}


def test_extract_json_raises_when_missing() -> None:
    with pytest.raises(LLMError):
        extract_json("no json here")


# --- OpenAI-compatible (Gemini, Groq) ----------------------------------------------------------


def chat_reply(content: str | None, finish: str = "stop", prompt_tokens: int = 120) -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 40},
    }


def compat(handler, sleeps: list[float] | None = None, **config) -> OpenAICompatLLM:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatLLM(
        Config(**config),
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key-123",
        http=http,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )


def ask(llm, **overrides):
    kwargs = {
        "label": "test",
        "model": "gemini-3.8-flash",
        "system": ["shared", "role"],
        "prompt": "hi",
    }
    kwargs.update(overrides)
    return llm.complete(**kwargs)


def test_compat_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=chat_reply("Hello"))

    llm = compat(handler, max_output_tokens=4000)
    assert ask(llm, max_tokens=16000).text == "Hello"
    request = seen[0]
    assert (
        str(request.url)
        == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert request.headers["Authorization"] == "Bearer key-123"
    body = json.loads(request.content)
    assert body == {
        "model": "gemini-3.8-flash",
        "messages": [
            {"role": "system", "content": "shared\n\nrole"},
            {"role": "user", "content": "hi"},
        ],
        "max_tokens": 4000,  # capped by max_output_tokens
    }


def test_compat_json_schema_becomes_an_instruction() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=chat_reply('```json\n{"ok": true}\n```'))

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    data, _ = compat(handler).complete_json(
        label="t", model="m", system=["s"], prompt="Judge it.", json_schema=schema
    )
    assert data == {"ok": True}
    prompt = seen[0]["messages"][1]["content"]
    assert prompt.startswith("Judge it.")
    assert "Reply with only one JSON object" in prompt and '"ok"' in prompt
    assert "response_format" not in seen[0]


def test_compat_retries_rate_limits_and_server_errors() -> None:
    replies = iter(
        [
            httpx.Response(
                429, headers={"retry-after": "7"}, json={"error": {"message": "slow down"}}
            ),
            httpx.Response(503, json=[{"error": {"message": "overloaded"}}]),
            httpx.Response(200, json=chat_reply("Done")),
        ]
    )
    sleeps: list[float] = []
    assert ask(compat(lambda r: next(replies), sleeps)).text == "Done"
    assert sleeps == [7.0, 8.0]  # Retry-After honoured, then 2**(attempt+2)


def test_compat_retries_network_errors() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=chat_reply("Done"))

    assert ask(compat(handler)).text == "Done"


def test_compat_gives_up_after_retries() -> None:
    with pytest.raises(LLMError, match="still failing"):
        ask(compat(lambda r: httpx.Response(500)))


@pytest.mark.parametrize(
    ("status", "message", "error_type"),
    [
        (401, "API key was rejected", LLMAuthError),
        (403, "access denied", LLMAuthError),
        (404, "--check", LLMError),
        (400, "rejected", LLMError),
    ],
)
def test_compat_client_errors_fail_fast(status: int, message: str, error_type: type) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(error_type, match=message) as info:
        ask(compat(handler))
    assert len(calls) == 1
    assert "key-123" not in str(info.value)


def test_compat_403_keeps_the_providers_instructions() -> None:
    guidance = (
        "Gemini API has not been used in project 1048725232147 before or it is disabled. Enable it "
        "by visiting https://console.developers.google.com/apis/api/generativelanguage.googleapis.com"
        "/overview?project=1048725232147 then retry. If you enabled this API recently, wait a few "
        "minutes for the action to propagate to our systems and retry."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json=[{"error": {"code": 403, "message": guidance}}])

    with pytest.raises(LLMAuthError) as info:
        ask(compat(handler))
    assert "propagate to our systems and retry" in str(info.value)


def test_compat_empty_and_filtered_replies() -> None:
    with pytest.raises(LLMError, match="empty"):
        ask(compat(lambda r: httpx.Response(200, json=chat_reply(None))))
    with pytest.raises(LLMRefusal):
        ask(compat(lambda r: httpx.Response(200, json=chat_reply("", finish="content_filter"))))


def test_compat_strips_think_blocks_and_keeps_truncated_text() -> None:
    reply = chat_reply("<think>reasoning here</think>\nThe answer.", finish="length")
    assert ask(compat(lambda r: httpx.Response(200, json=reply))).text == "The answer."


def test_compat_tracks_usage() -> None:
    llm = compat(lambda r: httpx.Response(200, json=chat_reply("x", prompt_tokens=500)))
    ask(llm)
    ask(llm)
    assert (llm.usage.calls, llm.usage.input_tokens, llm.usage.output_tokens) == (2, 1000, 80)
    assert llm.usage.cost_usd == 0  # free tier: no pricing entry


def test_complete_json_asks_again_after_bad_json() -> None:
    replies = iter([chat_reply("Sorry, here goes: not json"), chat_reply('{"fixed": 1}')])
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=next(replies))

    data, _ = compat(handler).complete_json(label="t", model="m", system=["s"], prompt="Go")
    assert data == {"fixed": 1}
    assert "not valid JSON" in seen[1]["messages"][1]["content"]


def test_list_models_strips_prefix() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "models/gemini-3.8-flash"}, {"id": "b"}]})

    assert compat(handler).list_models() == ["b", "gemini-3.8-flash"]


# --- Provider selection -------------------------------------------------------------------------


def test_create_llm_gemini_needs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        create_llm(Config(llm_provider="gemini"))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    llm = create_llm(Config(llm_provider="gemini"))
    assert isinstance(llm, OpenAICompatLLM)
    assert llm.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"


def test_create_llm_groq_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert create_llm(Config(llm_provider="groq")).base_url == "https://api.groq.com/openai/v1"
    monkeypatch.setenv("MY_KEY", "k2")
    custom = create_llm(
        Config(
            llm_provider="other", llm_base_url="https://llm.example/v1/", llm_api_key_env="MY_KEY"
        )
    )
    assert custom.base_url == "https://llm.example/v1"


def test_create_llm_rejects_unknown_provider() -> None:
    with pytest.raises(LLMError, match="Unknown llm_provider"):
        create_llm(Config(llm_provider="mystery"))


# --- Anthropic --------------------------------------------------------------------------------


def claude(handler, **config) -> tuple[AnthropicLLM, FakeClient]:
    client = FakeClient(handler)
    return AnthropicLLM(client, Config(pricing=PRICING, **config)), client


def test_anthropic_request_shape_and_caching() -> None:
    llm, client = claude(lambda req: fake_message([block("text", text="ok")]))
    ask(llm, model="claude-sonnet-5-5", effort="low")
    request = client.messages.requests[0]
    assert request["output_config"] == {"effort": "low"}
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in request["system"][1]
    assert "tools" not in request
    assert request["betas"] == [FALLBACK_BETA]
    assert request["fallbacks"] == "default"


def test_anthropic_structured_output() -> None:
    llm, client = claude(lambda req: fake_message([block("text", text='{"x": 1}')]))
    data, _ = llm.complete_json(
        label="t",
        model="claude-sonnet-5-5",
        system=["s"],
        prompt="p",
        json_schema={"type": "object"},
    )
    assert data == {"x": 1}
    assert client.messages.requests[0]["output_config"]["format"]["type"] == "json_schema"


def test_anthropic_fallback_can_be_turned_off() -> None:
    llm, client = claude(
        lambda req: fake_message([block("text", text="ok")]), refusal_fallback=False
    )
    ask(llm)
    assert "fallbacks" not in client.messages.requests[0]


def test_anthropic_fallback_rejected_by_api_is_dropped() -> None:
    def handler(req):
        if "fallbacks" in req:
            response = httpx2.Response(
                400, request=httpx2.Request("POST", "https://api.anthropic.com")
            )
            raise anthropic.BadRequestError(
                "fallbacks is not supported", response=response, body=None
            )
        return fake_message([block("text", text="ok")])

    llm, client = claude(handler)
    assert ask(llm).text == "ok"
    assert ask(llm).text == "ok"
    assert ["fallbacks" in r for r in client.messages.requests] == [True, False, False]


def test_anthropic_refusal_raises() -> None:
    message = fake_message([], stop_reason="refusal")
    message.stop_details = SimpleNamespace(category="cyber")
    llm, _ = claude(lambda req: message)
    with pytest.raises(LLMRefusal, match="cyber"):
        ask(llm)


def test_anthropic_api_error_becomes_llm_error() -> None:
    def handler(req):
        raise anthropic.APIConnectionError(
            request=httpx2.Request("POST", "https://api.anthropic.com")
        )

    llm, _ = claude(handler, refusal_fallback=False)
    with pytest.raises(LLMError):
        ask(llm)


def test_anthropic_usage_and_cost() -> None:
    llm, _ = claude(
        lambda req: fake_message(
            [block("text", text="ok")], input_tokens=1_000_000, output_tokens=100_000
        )
    )
    ask(llm, model="claude-sonnet-5-5")
    assert llm.usage.cost_usd == pytest.approx(3.0)  # $2 input + $1 output
