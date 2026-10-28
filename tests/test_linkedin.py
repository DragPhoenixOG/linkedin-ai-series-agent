"""LinkedIn Posts API client: escaping, request shape, retries, auth failures, token expiry."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from agent.linkedin import (
    MAX_COMMENTARY_CHARS,
    LinkedInAuthError,
    LinkedInClient,
    LinkedInError,
    PostContent,
    build_post_body,
    escape_little_text,
    token_status,
)

URN = "urn:li:person:abc123"


def client(handler, sleeps: list[float] | None = None, **kwargs) -> LinkedInClient:
    http = httpx.Client(base_url="https://api.linkedin.com", transport=httpx.MockTransport(handler))
    return LinkedInClient(
        "secret-token",
        URN,
        "202610",
        http=http,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        **kwargs,
    )


def created(urn: str = "urn:li:share:7000") -> httpx.Response:
    return httpx.Response(201, headers={"x-restli-id": urn})


# --- escaping ------------------------------------------------------------------------------


@pytest.mark.parametrize("char", list("\\|{}@[]()<>*_~"))
def test_every_reserved_character_is_escaped(char: str) -> None:
    assert escape_little_text(f"a {char} b") == f"a \\{char} b"


def test_parentheses_and_brackets_in_prose() -> None:
    text = "Retries (with backoff) beat [hope] <usually>."
    assert escape_little_text(text) == "Retries \\(with backoff\\) beat \\[hope\\] \\<usually\\>."


def test_hashtags_become_templates_and_still_render() -> None:
    text = "Plain text.\n\n#AgenticAI #EnterpriseAI #MCP2"
    assert escape_little_text(text) == (
        "Plain text.\n\n{hashtag|\\#|AgenticAI} {hashtag|\\#|EnterpriseAI} {hashtag|\\#|MCP2}"
    )


def test_hash_that_is_not_a_hashtag_is_escaped() -> None:
    assert escape_little_text("Half of it is C# and #1 priority") == (
        "Half of it is C\\# and \\#1 priority"
    )


def test_at_sign_and_email_are_escaped() -> None:
    assert escape_little_text("ping @team or a@b.com") == "ping \\@team or a\\@b.com"


def test_links_survive_escaping() -> None:
    assert escape_little_text("Source: https://example.com/a_b") == (
        "Source: https://example.com/a\\_b"
    )


def test_plain_text_is_unchanged() -> None:
    text = "Nothing special here, just words. And a question?"
    assert escape_little_text(text) == text


# --- request shape -----------------------------------------------------------------------------


def test_post_body_shape() -> None:
    body = build_post_body(URN, PostContent("Hello (world) #AI"))
    assert body == {
        "author": URN,
        "commentary": "Hello \\(world\\) {hashtag|\\#|AI}",
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def test_media_hook_for_future_images() -> None:
    body = build_post_body(URN, PostContent("Hi", media_urn="urn:li:image:1"))
    assert body["content"] == {"media": {"id": "urn:li:image:1"}}


def test_create_post_sends_headers_and_reads_urn() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return created()

    post = client(handler).create_post(PostContent("Hello #AI"))
    assert post.urn == "urn:li:share:7000"
    assert post.url == "https://www.linkedin.com/feed/update/urn:li:share:7000/"
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/rest/posts"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["LinkedIn-Version"] == "202610"
    assert request.headers["X-Restli-Protocol-Version"] == "2.0.0"
    assert json.loads(request.content)["commentary"] == "Hello {hashtag|\\#|AI}"


# --- retries and failures ------------------------------------------------------------------------


def test_retries_429_and_5xx_with_backoff() -> None:
    responses = iter(
        [
            httpx.Response(429, headers={"retry-after": "5"}),
            httpx.Response(503, json={"message": "busy"}),
            created(),
        ]
    )
    sleeps: list[float] = []
    post = client(lambda r: next(responses), sleeps).create_post(PostContent("Hi"))
    assert post.urn == "urn:li:share:7000"
    assert sleeps == [5.0, 2.0]  # Retry-After honoured, then 2**attempt


def test_retries_network_errors() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return created()

    assert client(handler).create_post(PostContent("Hi")).urn == "urn:li:share:7000"


def test_gives_up_after_max_retries() -> None:
    sleeps: list[float] = []
    with pytest.raises(LinkedInError, match="still failing"):
        client(lambda r: httpx.Response(500), sleeps, max_retries=2).create_post(PostContent("Hi"))
    assert len(sleeps) == 2


def test_401_raises_auth_error_without_retry() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"message": "Expired access token"})

    with pytest.raises(LinkedInAuthError, match=r"linkedin_auth\.py"):
        client(handler).create_post(PostContent("Hi"))
    assert len(calls) == 1


def test_other_4xx_fails_fast_with_message_but_no_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "commentary is invalid"})

    with pytest.raises(LinkedInError) as info:
        client(handler).create_post(PostContent("Hi"))
    assert "422" in str(info.value) and "commentary is invalid" in str(info.value)
    assert "secret-token" not in str(info.value)


def test_missing_urn_header_is_an_error() -> None:
    with pytest.raises(LinkedInError, match="x-restli-id"):
        client(lambda r: httpx.Response(201)).create_post(PostContent("Hi"))


def test_too_long_post_is_rejected_before_sending() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    with pytest.raises(LinkedInError, match=str(MAX_COMMENTARY_CHARS)):
        client(handler).create_post(PostContent("x" * (MAX_COMMENTARY_CHARS + 1)))


@pytest.mark.parametrize(
    ("token", "urn", "version"),
    [("", URN, "202610"), ("t", "abc123", "202610"), ("t", URN, "YYYYMM")],
)
def test_bad_configuration_is_rejected(token: str, urn: str, version: str) -> None:
    with pytest.raises(LinkedInError):
        LinkedInClient(token, urn, version)


# --- token expiry ----------------------------------------------------------------------------------


def test_token_status_warns_within_seven_days() -> None:
    today = date(2026, 10, 28)
    assert token_status("2026-12-01", today).warn is False
    status = token_status("2026-11-04", today)
    assert status.warn is True and status.days_left == 7 and not status.expired
    assert token_status("2026-10-27", today).expired is True


def test_token_status_handles_missing_or_bad_values() -> None:
    today = date(2026, 10, 28)
    assert token_status(None, today) is None
    assert token_status("", today) is None
    assert token_status("next month", today) is None
    assert token_status("2026-11-30T00:00:00Z", today).days_left == 33
