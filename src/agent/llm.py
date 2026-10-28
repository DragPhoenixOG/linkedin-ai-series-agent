"""Language-model clients behind one small interface.

- `OpenAICompatLLM` talks to any OpenAI-compatible chat endpoint. Gemini and Groq both offer one
  (with free tiers), so switching between them is a config change.
- `AnthropicLLM` uses the official Anthropic SDK for Claude (paid).

Both return plain text, track token usage, and share forgiving JSON parsing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx

from agent.config import Config
from agent.logs import log_event
from agent.templates import render

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# provider -> (OpenAI-compatible base URL, env var holding the API key)
PROVIDERS: dict[str, tuple[str, str]] = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "anthropic": ("", "ANTHROPIC_API_KEY"),
}


class LLMError(RuntimeError):
    """The model call failed or returned something we can't use."""


class LLMRefusal(LLMError):
    """The model declined the request."""


@dataclass
class Usage:
    """Token and cost totals for one run."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    def add(
        self,
        model: str,
        pricing: dict[str, Any],
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_write: int = 0,
        cache_read: int = 0,
    ) -> dict[str, Any]:
        """Add one call's usage; return that call's numbers for logging."""
        rates = pricing.get(model, {})
        cost = (
            input_tokens * rates.get("input", 0)
            + output_tokens * rates.get("output", 0)
            + cache_write * rates.get("cache_write", 0)
            + cache_read * rates.get("cache_read", 0)
        ) / 1_000_000
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_write_tokens += cache_write
        self.cache_read_tokens += cache_read
        self.cost_usd += cost
        return {"input": input_tokens, "output": output_tokens, "cost_usd": round(cost, 4)}

    def summary(self) -> str:
        return (
            f"calls={self.calls} input={self.input_tokens} output={self.output_tokens} "
            f"cache_read={self.cache_read_tokens} cache_write={self.cache_write_tokens} "
            f"est_cost_usd={self.cost_usd:.3f}"
        )


@dataclass
class LLMResponse:
    """The model's final text."""

    text: str
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


class LLM(ABC):
    """What the agent needs from a model: text in, text out, usage tracked."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.usage = Usage()

    @abstractmethod
    def complete(
        self,
        *,
        label: str,
        model: str,
        system: list[str],
        prompt: str,
        effort: str = "medium",
        max_tokens: int = 16000,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send one user prompt with system instructions and return the reply.

        `system` is a list of text blocks; put stable, shared content (style guide, author facts)
        first. With `json_schema`, the reply must be a JSON object matching it.
        """

    def complete_json(self, **kwargs: Any) -> tuple[Any, LLMResponse]:
        """Like `complete`, then parse JSON. Asks once more if the first reply isn't valid JSON."""
        response = self.complete(**kwargs)
        try:
            return extract_json(response.text), response
        except LLMError:
            log_event(logger, "llm.bad_json", logging.WARNING, label=kwargs.get("label"))
        retry = dict(kwargs)
        retry["label"] = f"{kwargs['label']}.retry"
        retry["prompt"] = (
            f"{kwargs['prompt']}\n\nYour previous reply was not valid JSON. "
            "Reply again with only the JSON, nothing else."
        )
        response = self.complete(**retry)
        return extract_json(response.text), response


class OpenAICompatLLM(LLM):
    """Any OpenAI-compatible `/chat/completions` endpoint (Gemini, Groq, ...).

    Retries rate limits (429) and server errors with backoff, honouring `Retry-After`, because
    free tiers hit per-minute limits quickly.
    """

    def __init__(
        self,
        config: Config,
        base_url: str,
        api_key: str,
        http: httpx.Client | None = None,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(config)
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = http or httpx.Client(timeout=180.0)
        self.max_retries = max_retries
        self._sleep = sleep

    def _post(self, label: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        for attempt in range(self.max_retries + 1):
            try:
                response = self._http.post(
                    f"{self.base_url}/chat/completions", json=body, headers=headers
                )
            except httpx.TransportError as exc:
                error, delay = f"network error: {type(exc).__name__}", 2.0 ** (attempt + 1)
            else:
                if response.status_code == 200:
                    return response.json()
                error = f"HTTP {response.status_code}: {_error_message(response)}"
                if response.status_code in (401, 403):
                    raise LLMError(f"{label}: the API key was rejected ({error}). Check the key.")
                if response.status_code == 404:
                    raise LLMError(
                        f"{label}: {error}. Is the model name right? "
                        "Run `python -m agent.main --check` to list available models."
                    )
                if response.status_code != 429 and response.status_code < 500:
                    raise LLMError(f"{label}: request rejected ({error})")
                retry_after = response.headers.get("retry-after", "")
                delay = float(retry_after) if _is_number(retry_after) else 2.0 ** (attempt + 2)
            if attempt == self.max_retries:
                raise LLMError(f"{label}: still failing after retries ({error})")
            log_event(logger, "llm.retry", logging.WARNING, label=label, error=error, wait_s=delay)
            self._sleep(min(delay, 90.0))
        raise AssertionError("unreachable")

    def complete(
        self,
        *,
        label: str,
        model: str,
        system: list[str],
        prompt: str,
        effort: str = "medium",
        max_tokens: int = 16000,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        if json_schema is not None:
            prompt = f"{prompt}\n\n{render('json_reply', schema=json.dumps(json_schema, indent=1))}"
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "\n\n".join(s for s in system if s)},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": min(max_tokens, self.config.max_output_tokens),
        }
        data = self._post(label, body)
        usage = data.get("usage") or {}
        call = self.usage.add(
            model,
            self.config.pricing,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )
        choice = (data.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or "stop"
        log_event(logger, "llm.call", label=label, model=model, stop=finish, **call)
        text = (choice.get("message") or {}).get("content") or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if finish == "length":
            log_event(logger, "llm.truncated", logging.WARNING, label=label, max_tokens=max_tokens)
        if finish == "content_filter":
            raise LLMRefusal(f"{label}: the provider's safety filter blocked the reply")
        if not text:
            raise LLMError(f"{label}: empty response (finish_reason={finish})")
        return LLMResponse(text=text, stop_reason=finish)

    def list_models(self) -> list[str]:
        """Model ids this key can use (for `--check`)."""
        response = self._http.get(
            f"{self.base_url}/models", headers={"Authorization": f"Bearer {self._api_key}"}
        )
        if response.status_code != 200:
            raise LLMError(f"Listing models failed: HTTP {response.status_code}")
        return sorted(m["id"].removeprefix("models/") for m in response.json().get("data", []))


class AnthropicLLM(LLM):
    """Claude through the official Anthropic SDK."""

    def __init__(self, client: Any, config: Config) -> None:
        super().__init__(config)
        self.client = client
        self._fallback_enabled = config.refusal_fallback

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
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": s} for s in system if s]
        if system_blocks:
            system_blocks[0]["cache_control"] = {"type": "ephemeral"}
        output_config: dict[str, Any] = {"effort": effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        try:
            response = self._create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                output_config=output_config,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIError as exc:
            raise LLMError(f"{label}: API call failed: {exc}") from exc
        usage = response.usage
        call = self.usage.add(
            model,
            self.config.pricing,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
        log_event(logger, "llm.call", label=label, model=model, stop=response.stop_reason, **call)
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMRefusal(f"{label}: model declined the request (category={category})")
        if response.stop_reason == "max_tokens":
            log_event(logger, "llm.truncated", logging.WARNING, label=label, max_tokens=max_tokens)
        text = "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        if not text:
            raise LLMError(f"{label}: empty response")
        return LLMResponse(text=text, stop_reason=response.stop_reason)


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(data, list) and data:
        data = data[0]
    error = data.get("error", data) if isinstance(data, dict) else {}
    message = error.get("message", "") if isinstance(error, dict) else str(error)
    return str(message)[:300]


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def provider_settings(config: Config) -> tuple[str, str]:
    """(base URL, API key env var) for the configured provider, with config overrides."""
    if config.llm_provider not in PROVIDERS and not config.llm_base_url:
        raise LLMError(
            f"Unknown llm_provider {config.llm_provider!r}; use one of {', '.join(PROVIDERS)} "
            "or set llm_base_url and llm_api_key_env."
        )
    base_url, key_env = PROVIDERS.get(config.llm_provider, ("", ""))
    return config.llm_base_url or base_url, config.llm_api_key_env or key_env


def create_llm(config: Config) -> LLM:
    """Build the client for `config.llm_provider`, reading the API key from the environment."""
    base_url, key_env = provider_settings(config)
    if config.llm_provider == "anthropic":
        hint = f"Set {key_env} (in .env locally, or as a GitHub Actions secret)."
        try:
            client = anthropic.Anthropic(max_retries=4)
        except anthropic.AnthropicError as exc:
            raise LLMError(f"Could not load Claude API credentials ({exc}). {hint}") from exc
        if not (client.api_key or client.auth_token or client.credentials):
            raise LLMError(f"No Claude API credentials found. {hint}")
        return AnthropicLLM(client, config)
    api_key = os.environ.get(key_env, "")
    if not api_key:
        raise LLMError(
            f"{key_env} is not set. Add your {config.llm_provider} API key to .env locally, "
            "or as a GitHub Actions secret."
        )
    return OpenAICompatLLM(config, base_url, api_key)
