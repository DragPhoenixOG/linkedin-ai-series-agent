"""Test doubles for the Claude API. No test ever talks to a real external service."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from agent.llm import LLMResponse, Usage, extract_json
from agent.state import Source

Reply = Any  # dict/list (JSON), str (text), Exception, or a callable(call) -> one of those


class FakeLLM:
    """Stands in for `agent.llm.LLM`. Replies are keyed by call label.

    A label maps to a single reply (reused every call) or `seq(...)` (one per call, last repeats).
    """

    def __init__(self, replies: dict[str, Reply | list[Reply]] | None = None) -> None:
        self.replies = dict(replies or {})
        self.calls: list[dict[str, Any]] = []
        self.usage = Usage()

    def _next(self, label: str) -> Reply:
        if label not in self.replies:
            raise AssertionError(f"FakeLLM has no reply for label {label!r}")
        reply = self.replies[label]
        if isinstance(reply, ReplySeq):
            return reply.pop(0) if len(reply) > 1 else reply[0]
        return reply

    def complete(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        reply = self._next(kwargs["label"])
        if callable(reply) and not isinstance(reply, type):
            reply = reply(kwargs)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, LLMResponse):
            return reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        if not isinstance(reply, str) and kwargs.get("json_schema") is None:
            text = f"<result>{text}</result>"
        return LLMResponse(text=text, citations=[Source(url="https://cited.example/a")])

    def complete_json(self, **kwargs: Any) -> tuple[Any, LLMResponse]:
        response = self.complete(**kwargs)
        return extract_json(response.text), response


class ReplySeq(list):
    """Marks a list of replies to be returned one per call."""


def seq(*replies: Reply) -> ReplySeq:
    """Replies returned in order across calls; the last one repeats."""
    return ReplySeq(replies)


def block(type_: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=type_, **fields)


def fake_message(
    content: list[Any], stop_reason: str = "end_turn", searches: int = 0, **usage: int
) -> SimpleNamespace:
    """A minimal object shaped like an SDK `Message`."""
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(
            input_tokens=usage.get("input_tokens", 100),
            output_tokens=usage.get("output_tokens", 50),
            cache_creation_input_tokens=usage.get("cache_write", 0),
            cache_read_input_tokens=usage.get("cache_read", 0),
            server_tool_use=SimpleNamespace(web_search_requests=searches) if searches else None,
        ),
    )


class FakeMessages:
    def __init__(self, handler: Callable[[dict[str, Any]], Any]) -> None:
        self.handler = handler
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self.handler(kwargs)


class FakeClient:
    """Shaped like `anthropic.Anthropic` for the two endpoints `LLM` uses."""

    def __init__(self, handler: Callable[[dict[str, Any]], Any]) -> None:
        self.messages = FakeMessages(handler)
        self.beta = SimpleNamespace(messages=self.messages)
