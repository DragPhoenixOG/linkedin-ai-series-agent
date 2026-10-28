"""Drive `AnthropicLLM` through the real Anthropic SDK with a mocked HTTP transport.

This checks what actually goes over the wire (request body and headers) and that real SDK
response objects parse, without any network access.
"""

from __future__ import annotations

import json
from pathlib import Path

import anthropic
import httpx2 as httpx
import pytest

from agent.config import Config
from agent.llm import AnthropicLLM, LLMError, create_llm


def message(content: list[dict], stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 300,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def sdk_llm(responses: list[dict], seen: list[httpx.Request]) -> AnthropicLLM:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=queue.pop(0))

    client = anthropic.Anthropic(
        api_key="test-key",
        base_url="https://api.anthropic.com",
        http_client=anthropic.DefaultHttpxClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    return AnthropicLLM(client, Config(llm_provider="anthropic"))


def test_request_goes_over_the_wire_as_expected() -> None:
    seen: list[httpx.Request] = []
    llm = sdk_llm([message([{"type": "text", "text": "<post>Hello</post>"}])], seen)
    response = llm.complete(
        label="writer", model="claude-sonnet-5-5", system=["shared", "role"], prompt="Write"
    )
    assert response.text == "<post>Hello</post>"
    body = json.loads(seen[0].content)
    assert seen[0].headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "medium"}
    assert body["system"][0] == {
        "type": "text",
        "text": "shared",
        "cache_control": {"type": "ephemeral"},
    }
    assert "tools" not in body
    assert llm.usage.input_tokens == 1200


def test_structured_output_request() -> None:
    seen: list[httpx.Request] = []
    llm = sdk_llm([message([{"type": "text", "text": '{"ok": true}'}])], seen)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    data, _ = llm.complete_json(
        label="editor", model="claude-sonnet-5-5", system=["s"], prompt="p", json_schema=schema
    )
    assert data == {"ok": True}
    body = json.loads(seen[0].content)
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": schema}


def test_missing_credentials_fail_with_guidance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path))  # no profile on disk
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        create_llm(Config(llm_provider="anthropic"))
