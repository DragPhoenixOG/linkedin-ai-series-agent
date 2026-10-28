"""One-time local helper: get a LinkedIn access token and your person URN.

Run it on your own computer (not in CI):

    python scripts/linkedin_auth.py

It reads LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET from the environment or `.env` (or asks
for them), opens LinkedIn's consent page in your browser, catches the redirect on
http://localhost:8000/callback, exchanges the code for an access token, looks up your member
id, and prints the values to store as GitHub Secrets and Variables.

Your LinkedIn app needs the products "Sign In with LinkedIn using OpenID Connect" and
"Share on LinkedIn", and http://localhost:8000/callback listed as an authorized redirect URL.
"""

from __future__ import annotations

import getpass
import os
import secrets
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from dotenv import load_dotenv

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
REDIRECT_URI = "http://localhost:8000/callback"
SCOPES = "openid profile w_member_social"
DEFAULT_TOKEN_LIFETIME_S = 60 * 24 * 3600


class AuthError(RuntimeError):
    """Something in the OAuth flow failed."""


@dataclass(frozen=True)
class TokenResult:
    access_token: str
    expires_in: int  # seconds

    def expires_on(self, today: date) -> date:
        return today + timedelta(seconds=self.expires_in)


def build_authorize_url(client_id: str, state: str, redirect_uri: str = REDIRECT_URI) -> str:
    """LinkedIn's consent page URL for the scopes this agent needs."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": SCOPES,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def parse_callback(path: str, expected_state: str) -> str:
    """Return the authorization code from the redirect URL, after checking `state`."""
    params = {k: v[0] for k, v in parse_qs(urlparse(path).query).items()}
    if "error" in params:
        detail = params.get("error_description", params["error"])
        raise AuthError(f"LinkedIn returned an error: {detail}")
    if not secrets.compare_digest(params.get("state", ""), expected_state):
        raise AuthError("State mismatch: the redirect did not come from this login attempt")
    code = params.get("code")
    if not code:
        raise AuthError("No authorization code in the redirect")
    return code


def wait_for_callback(expected_state: str, port: int = 8000, timeout: float = 300.0) -> str:
    """Serve http://localhost:<port>/callback until LinkedIn redirects back. Returns the code."""
    result: dict[str, str | AuthError] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if urlparse(self.path).path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            try:
                result["code"] = parse_callback(self.path, expected_state)
                message = "Done. You can close this tab and go back to the terminal."
                self.send_response(200)
            except AuthError as exc:
                result["error"] = exc
                message = f"Login failed: {exc}"
                self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(message.encode())

        def log_message(self, format: str, *args: object) -> None:  # keep the code out of logs
            pass

    server = HTTPServer(("localhost", port), Handler)
    deadline = time.monotonic() + timeout
    try:
        while not result:  # ignore stray requests such as /favicon.ico
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthError(f"No redirect received within {int(timeout)} seconds")
            server.timeout = remaining
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return str(result["code"])


def exchange_code(
    http: httpx.Client,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str = REDIRECT_URI,
) -> TokenResult:
    """Swap the authorization code for an access token."""
    response = http.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code != 200:
        raise AuthError(
            f"Token exchange failed (HTTP {response.status_code}): {response.text[:200]}"
        )
    data = response.json()
    if "access_token" not in data:
        raise AuthError("Token exchange response had no access_token")
    # LinkedIn member tokens normally last 60 days; fall back to that if expires_in is missing.
    expires_in = int(data.get("expires_in") or DEFAULT_TOKEN_LIFETIME_S)
    return TokenResult(access_token=data["access_token"], expires_in=expires_in)


def fetch_person_urn(http: httpx.Client, access_token: str) -> str:
    """Look up the member id (`sub`) and return `urn:li:person:<sub>`."""
    response = http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code != 200:
        raise AuthError(f"userinfo failed (HTTP {response.status_code}): {response.text[:200]}")
    sub = response.json().get("sub")
    if not sub:
        raise AuthError("userinfo response had no 'sub' (member id)")
    return f"urn:li:person:{sub}"


def render_instructions(token: str, person_urn: str, expires_on: date) -> str:
    """What to copy into GitHub, and how."""
    return f"""
LinkedIn authorisation complete.

  LINKEDIN_ACCESS_TOKEN      {token}
  LINKEDIN_PERSON_URN        {person_urn}
  LINKEDIN_TOKEN_EXPIRES_ON  {expires_on.isoformat()}

Store them in GitHub: repository > Settings > Secrets and variables > Actions.
  Secrets:   LINKEDIN_ACCESS_TOKEN, LINKEDIN_PERSON_URN
  Variables: LINKEDIN_TOKEN_EXPIRES_ON

Or with the GitHub CLI, from the repository folder:
  gh secret set LINKEDIN_ACCESS_TOKEN
  gh secret set LINKEDIN_PERSON_URN --body "{person_urn}"
  gh variable set LINKEDIN_TOKEN_EXPIRES_ON --body "{expires_on.isoformat()}"
(the first command prompts for the token so it doesn't end up in your shell history)

The token lasts until {expires_on.isoformat()}. The agent opens a GitHub issue 7 days before
that; re-run this script then. Treat the token like a password: don't commit or share it.
"""


def main() -> int:
    load_dotenv()
    client_id = os.environ.get("LINKEDIN_CLIENT_ID") or input("LinkedIn app Client ID: ").strip()
    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET") or getpass.getpass(
        "LinkedIn app Client Secret (hidden): "
    )
    state = secrets.token_urlsafe(24)
    url = build_authorize_url(client_id, state)
    print(f"Opening LinkedIn in your browser. If it doesn't open, visit:\n{url}\n")
    webbrowser.open(url)
    try:
        code = wait_for_callback(state)
        with httpx.Client(timeout=30.0) as http:
            token = exchange_code(http, client_id, client_secret, code)
            person_urn = fetch_person_urn(http, token.access_token)
    except AuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(render_instructions(token.access_token, person_urn, token.expires_on(date.today())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
