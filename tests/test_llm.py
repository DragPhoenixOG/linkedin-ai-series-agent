"""The Claude API wrapper, driven by a fake client."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

from agent.config import Config
from agent.llm import FALLBACK_BETA, LLM, LLMError, LLMRefusal, extract_json
from fakes import FakeClient, block, fake_message

PRICING = {
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.2},
    "web_search_per_1k": 10.0,
}


def make_llm(handler, **config) -> tuple[LLM, FakeClient]:
    client = FakeClient(handler)
    return LLM(client, Config(pricing=PRICING, **config)), client


def call(llm: LLM, **overrides):
    kwargs = {
        "label": "test",
        "model": "claude-sonnet-5-5",
        "system": ["shared", "role"],
        "prompt": "hi",
    }
    kwargs.update(overrides)
    return llm.complete(**kwargs)


def test_extract_json_prefers_result_tags() -> None:
    text = 'I searched. {"wrong": 1}\n<result>{"right": 2}</result>'
    assert extract_json(text) == {"right": 2}


def test_extract_json_handles_fences_and_prose() -> None:
    assert extract_json('Here:\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}


def test_extract_json_raises_when_missing() -> None:
    with pytest.raises(LLMError):
        extract_json("no json here")


def test_request_shape_with_web_search_and_caching() -> None:
    llm, client = make_llm(lambda req: fake_message([block("text", text="ok", citations=None)]))
    call(llm, web_search_max_uses=3, effort="low")
    request = client.messages.requests[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["output_config"] == {"effort": "low"}
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in request["system"][1]
    tool = request["tools"][0]
    assert tool["type"] == "web_search_20260318"
    assert tool["max_uses"] == 3
    assert tool["response_inclusion"] == "excluded"
    assert request["betas"] == [FALLBACK_BETA]
    assert request["fallbacks"] == "default"


def test_structured_output_uses_output_config_format() -> None:
    llm, client = make_llm(lambda req: fake_message([block("text", text='{"x": 1}')]))
    data, _ = llm.complete_json(
        label="t",
        model="claude-sonnet-5-5",
        system=["s"],
        prompt="p",
        json_schema={"type": "object"},
    )
    assert data == {"x": 1}
    assert client.messages.requests[0]["output_config"]["format"]["type"] == "json_schema"
    assert "tools" not in client.messages.requests[0]


def test_fallback_can_be_turned_off() -> None:
    llm, client = make_llm(
        lambda req: fake_message([block("text", text="ok")]), refusal_fallback=False
    )
    call(llm)
    assert "fallbacks" not in client.messages.requests[0]


def test_fallback_rejected_by_api_is_dropped_and_retried() -> None:
    def handler(req):
        if "fallbacks" in req:
            response = httpx.Response(
                400, request=httpx.Request("POST", "https://api.anthropic.com")
            )
            raise anthropic.BadRequestError(
                "fallbacks is not supported", response=response, body=None
            )
        return fake_message([block("text", text="ok")])

    llm, client = make_llm(handler)
    assert call(llm).text == "ok"
    assert call(llm).text == "ok"
    assert ["fallbacks" in r for r in client.messages.requests] == [True, False, False]


def test_pause_turn_is_continued_with_the_paused_content() -> None:
    replies = iter(
        [
            fake_message(
                [block("text", text="Searching... ")], stop_reason="pause_turn", searches=2
            ),
            fake_message([block("text", text='<result>{"done": true}</result>')], searches=1),
        ]
    )
    llm, client = make_llm(lambda req: next(replies))
    data, _ = llm.complete_json(label="t", model="claude-sonnet-5-5", system=["s"], prompt="p")
    assert data == {"done": True}
    second = client.messages.requests[1]["messages"]
    assert second[0]["role"] == "user"
    assert second[1]["role"] == "assistant"
    assert second[1]["content"][0].text == "Searching... "
    assert llm.usage.web_searches == 3
    assert llm.usage.calls == 2


def test_refusal_raises() -> None:
    message = fake_message([], stop_reason="refusal")
    message.stop_details = SimpleNamespace(category="cyber")
    llm, _ = make_llm(lambda req: message)
    with pytest.raises(LLMRefusal, match="cyber"):
        call(llm)


def test_api_error_becomes_llm_error() -> None:
    def handler(req):
        raise anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.anthropic.com")
        )

    llm, _ = make_llm(handler, refusal_fallback=False)
    with pytest.raises(LLMError):
        call(llm)


def test_empty_response_raises() -> None:
    llm, _ = make_llm(lambda req: fake_message([block("thinking", thinking="")]))
    with pytest.raises(LLMError, match="empty"):
        call(llm)


def test_citations_are_collected_once_per_url() -> None:
    cite = SimpleNamespace(url="https://a.example/x", title="A")
    content = [
        block("text", text="One ", citations=[cite]),
        block("text", text="two", citations=[cite]),
    ]
    llm, _ = make_llm(lambda req: fake_message(content))
    response = call(llm)
    assert response.text == "One two"
    assert [c.url for c in response.citations] == ["https://a.example/x"]


def test_usage_and_cost_are_accumulated() -> None:
    llm, _ = make_llm(
        lambda req: fake_message(
            [block("text", text="ok")], input_tokens=1_000_000, output_tokens=100_000, searches=10
        )
    )
    call(llm)
    # $2 input + $1 output + 10 searches * $0.01
    assert llm.usage.cost_usd == pytest.approx(3.10)
    assert "searches=10" in llm.usage.summary()
