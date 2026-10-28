"""Drive `LLM` through the real Anthropic SDK with a mocked HTTP transport.

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
from agent.llm import LLM, LLMError


def message(content: list[dict], stop_reason: str = "end_turn", searches: int = 0) -> dict:
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
            "server_tool_use": {"web_search_requests": searches, "web_fetch_requests": 0},
        },
    }


def sdk_llm(responses: list[dict], seen: list[httpx.Request]) -> LLM:
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
    return LLM(client, Config())


def test_web_search_request_and_pause_turn_round_trip() -> None:
    seen: list[httpx.Request] = []
    cite = {
        "type": "web_search_result_location",
        "url": "https://vendor.example/post",
        "title": "Vendor post",
        "encrypted_index": "abc",
        "cited_text": "The vendor shipped it.",
    }
    llm = sdk_llm(
        [
            message([{"type": "text", "text": "Looking that up. "}], "pause_turn", searches=2),
            message(
                [
                    {
                        "type": "text",
                        "text": '<result>{"developments": []}</result>',
                        "citations": [cite],
                    }
                ],
                searches=1,
            ),
        ],
        seen,
    )
    data, response = llm.complete_json(
        label="research",
        model="claude-sonnet-5-5",
        system=["shared", "role"],
        prompt="Find things",
        effort="medium",
        web_search_max_uses=5,
    )
    assert data == {"developments": []}
    assert [c.url for c in response.citations] == ["https://vendor.example/post"]

    first = json.loads(seen[0].content)
    assert seen[0].headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"}
    assert first["tools"] == [
        {
            "type": "web_search_20260318",
            "name": "web_search",
            "max_uses": 5,
            "response_inclusion": "excluded",
        }
    ]
    assert first["system"][0] == {
        "type": "text",
        "text": "shared",
        "cache_control": {"type": "ephemeral"},
    }

    second = json.loads(seen[1].content)
    assert second["messages"][1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Looking that up. "}],
    }
    assert llm.usage.web_searches == 3
    assert llm.usage.input_tokens == 2400


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
    assert "tools" not in body


def test_missing_credentials_fail_with_guidance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path))  # no profile on disk
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        LLM.from_env(Config())
