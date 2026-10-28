"""The one-time OAuth helper script, with LinkedIn mocked."""

from __future__ import annotations

import socket
import threading
import time
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from linkedin_auth import (
    AuthError,
    build_authorize_url,
    exchange_code,
    fetch_person_urn,
    parse_callback,
    render_instructions,
    wait_for_callback,
)


def test_authorize_url_requests_the_right_scopes() -> None:
    url = urlparse(build_authorize_url("client-1", "state-xyz"))
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.netloc == "www.linkedin.com"
    assert params == {
        "response_type": "code",
        "client_id": "client-1",
        "redirect_uri": "http://localhost:8000/callback",
        "state": "state-xyz",
        "scope": "openid profile w_member_social",
    }


def test_parse_callback_checks_state() -> None:
    assert parse_callback("/callback?code=abc&state=s1", "s1") == "abc"
    with pytest.raises(AuthError, match="State mismatch"):
        parse_callback("/callback?code=abc&state=evil", "s1")
    with pytest.raises(AuthError, match="user cancelled"):
        parse_callback(
            "/callback?error=access_denied&error_description=user+cancelled&state=s1", "s1"
        )
    with pytest.raises(AuthError, match="No authorization code"):
        parse_callback("/callback?state=s1", "s1")


def test_exchange_code_posts_form_and_reads_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 5184000})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        token = exchange_code(http, "cid", "csecret", "the-code")
    assert token.access_token == "tok"
    assert token.expires_on(date(2026, 10, 28)) == date(2026, 12, 27)
    form = {k: v[0] for k, v in parse_qs(seen[0].content.decode()).items()}
    assert str(seen[0].url) == "https://www.linkedin.com/oauth/v2/accessToken"
    assert form == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": "http://localhost:8000/callback",
        "client_id": "cid",
        "client_secret": "csecret",
    }


def test_exchange_code_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http, pytest.raises(AuthError):
        exchange_code(http, "cid", "csecret", "bad-code")


def test_fetch_person_urn_uses_userinfo_sub() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        assert str(request.url) == "https://api.linkedin.com/v2/userinfo"
        return httpx.Response(200, json={"sub": "AbC-123", "name": "Ashish Ranjan"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert fetch_person_urn(http, "tok") == "urn:li:person:AbC-123"


def test_render_instructions_lists_all_values() -> None:
    text = render_instructions("tok", "urn:li:person:1", date(2026, 12, 27))
    for expected in (
        "LINKEDIN_ACCESS_TOKEN",
        "tok",
        "urn:li:person:1",
        "2026-12-27",
        "gh secret set",
    ):
        assert expected in text


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def test_wait_for_callback_ignores_stray_requests_and_returns_code() -> None:
    port = _free_port()
    result: dict[str, str] = {}
    thread = threading.Thread(
        target=lambda: result.update(code=wait_for_callback("s1", port=port, timeout=10))
    )
    thread.start()
    for _ in range(50):  # wait for the server to start listening
        try:
            httpx.get(f"http://localhost:{port}/favicon.ico", timeout=1)
            break
        except httpx.TransportError:
            time.sleep(0.05)
    response = httpx.get(f"http://localhost:{port}/callback?code=abc&state=s1", timeout=5)
    thread.join(timeout=5)
    assert response.status_code == 200
    assert result == {"code": "abc"}
