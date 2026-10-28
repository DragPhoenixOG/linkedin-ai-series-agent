"""Thin wrapper around the Anthropic Messages API.

Handles the parts every caller needs: server-side web search (including `pause_turn`
continuations), JSON parsing, refusals, the server-side refusal fallback, and per-run token and
cost accounting.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic

from agent.config import Config
from agent.logs import log_event
from agent.state import Source

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_CONTINUATIONS = 4


class LLMError(RuntimeError):
    """The model call failed or returned something we can't use."""


class LLMRefusal(LLMError):
    """The model declined the request (`stop_reason == "refusal"`)."""


@dataclass
class Usage:
    """Token, search and cost totals for one run."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0

    def add(self, model: str, usage: Any, pricing: dict[str, Any]) -> dict[str, Any]:
        """Add one response's usage; return that call's numbers for logging."""
        server = getattr(usage, "server_tool_use", None)
        call = {
            "input": getattr(usage, "input_tokens", 0) or 0,
            "output": getattr(usage, "output_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "searches": (getattr(server, "web_search_requests", 0) or 0) if server else 0,
        }
        rates = pricing.get(model, {})
        cost = (
            call["input"] * rates.get("input", 0)
            + call["output"] * rates.get("output", 0)
            + call["cache_write"] * rates.get("cache_write", 0)
            + call["cache_read"] * rates.get("cache_read", 0)
        ) / 1_000_000 + call["searches"] * pricing.get("web_search_per_1k", 0) / 1000
        self.calls += 1
        self.input_tokens += call["input"]
        self.output_tokens += call["output"]
        self.cache_write_tokens += call["cache_write"]
        self.cache_read_tokens += call["cache_read"]
        self.web_searches += call["searches"]
        self.cost_usd += cost
        return {**call, "cost_usd": round(cost, 4)}

    def summary(self) -> str:
        return (
            f"calls={self.calls} input={self.input_tokens} output={self.output_tokens} "
            f"cache_read={self.cache_read_tokens} cache_write={self.cache_write_tokens} "
            f"searches={self.web_searches} est_cost_usd={self.cost_usd:.3f}"
        )


@dataclass
class LLMResponse:
    """Text of the final answer plus any web citations."""

    text: str
    citations: list[Source] = field(default_factory=list)
    stop_reason: str = "end_turn"


def extract_json(text: str) -> Any:
    """Parse JSON from model text: `<result>` tags first, then fenced blocks, then raw braces."""
    candidates = re.findall(r"<result>\s*(.*?)\s*</result>", text, re.DOTALL)[::-1]
    candidates += re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)[::-1]
    candidates.append(text)
    decoder = json.JSONDecoder()
    for candidate in candidates:
        candidate = candidate.strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        for start in (m.start() for m in re.finditer(r"[{\[]", candidate)):
            try:
                value, _ = decoder.raw_decode(candidate[start:])
                return value
            except json.JSONDecodeError:
                continue
    raise LLMError("Model response did not contain valid JSON")


def _collect_citations(blocks: list[Any]) -> list[Source]:
    seen: dict[str, Source] = {}
    for block in blocks:
        if getattr(block, "type", None) != "text":
            continue
        for citation in getattr(block, "citations", None) or []:
            url = getattr(citation, "url", None)
            if url and url not in seen:
                seen[url] = Source(url=url, title=getattr(citation, "title", "") or "")
    return list(seen.values())


class LLM:
    """Calls Claude with consistent settings and records usage for the run summary."""

    def __init__(self, client: Any, config: Config) -> None:
        self.client = client
        self.config = config
        self.usage = Usage()
        self._fallback_enabled = config.refusal_fallback

    @classmethod
    def from_env(cls, config: Config) -> LLM:
        """Create a client from `ANTHROPIC_API_KEY` (or another SDK credential source)."""
        return cls(anthropic.Anthropic(max_retries=4), config)

    def _create(self, **kwargs: Any) -> Any:
        if self._fallback_enabled:
            try:
                return self.client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **kwargs
                )
            except anthropic.BadRequestError as exc:
                if "fallback" not in str(exc).lower():
                    raise
                log_event(logger, "llm.fallback_unavailable", logging.WARNING, error=str(exc)[:200])
                self._fallback_enabled = False
        return self.client.messages.create(**kwargs)

    def complete(
        self,
        *,
        label: str,
        model: str,
        system: list[str],
        prompt: str,
        effort: str = "medium",
        max_tokens: int = 16000,
        web_search_max_uses: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send one user prompt and return the final text.

        `system` is a list of text blocks. The first one is marked for prompt caching, so put the
        stable, shared content (style guide, author facts) there.
        """
        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": s} for s in system if s]
        if system_blocks:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}
        output_config: dict[str, Any] = {"effort": effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "output_config": output_config,
        }
        if web_search_max_uses:
            tool: dict[str, Any] = {
                "type": self.config.web_search_tool,
                "name": "web_search",
                "max_uses": web_search_max_uses,
            }
            if self.config.web_search_tool >= "web_search_20260318":
                # Raw results already filtered by dynamic filtering aren't needed in the reply.
                tool["response_inclusion"] = "excluded"
            request["tools"] = [tool]

        user_message = {"role": "user", "content": prompt}
        blocks: list[Any] = []
        response = None
        for _ in range(MAX_CONTINUATIONS + 1):
            messages = [user_message]
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            try:
                response = self._create(messages=messages, **request)
            except anthropic.APIError as exc:
                raise LLMError(f"{label}: API call failed: {exc}") from exc
            call = self.usage.add(model, response.usage, self.config.pricing)
            log_event(
                logger, "llm.call", label=label, model=model, stop=response.stop_reason, **call
            )
            blocks = blocks + list(response.content)
            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None) if details else None
                raise LLMRefusal(f"{label}: model declined the request (category={category})")
            if response.stop_reason != "pause_turn":
                break
        assert response is not None
        if response.stop_reason == "pause_turn":
            raise LLMError(f"{label}: still paused after {MAX_CONTINUATIONS} continuations")
        if response.stop_reason == "max_tokens":
            log_event(logger, "llm.truncated", logging.WARNING, label=label, max_tokens=max_tokens)

        text = "".join(b.text for b in blocks if getattr(b, "type", None) == "text").strip()
        if not text:
            raise LLMError(f"{label}: empty response")
        return LLMResponse(
            text=text, citations=_collect_citations(blocks), stop_reason=response.stop_reason
        )

    def complete_json(self, **kwargs: Any) -> tuple[Any, LLMResponse]:
        """Like `complete`, then parse JSON from the reply. Returns (data, raw response)."""
        response = self.complete(**kwargs)
        if kwargs.get("json_schema") is not None:
            try:
                return json.loads(response.text), response
            except json.JSONDecodeError:
                pass  # fall through to the forgiving parser
        return extract_json(response.text), response
