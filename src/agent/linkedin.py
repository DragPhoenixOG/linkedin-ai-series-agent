"""LinkedIn Posts API client. Official API only: no scraping, no browser automation.

Text-only for v1. `PostContent.media_urn` is the hook for images later: upload the image with
LinkedIn's Images API, then pass the returned `urn:li:image:...` here.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import httpx

from agent.logs import log_event

logger = logging.getLogger(__name__)

API_BASE = "https://api.linkedin.com"
POSTS_PATH = "/rest/posts"
MAX_COMMENTARY_CHARS = 3000

# Characters with meaning in LinkedIn's "little text format". Unescaped, they can truncate the
# post or make the API reject it, so every one is backslash-escaped in plain text.
_RESERVED_RE = re.compile(r"([\\|{}@\[\]()<>#*_~])")
# A hashtag is turned into LinkedIn's hashtag template so it still renders as a hashtag.
# "C#" and "&#" are left alone (the '#' is escaped instead).
_HASHTAG_RE = re.compile(r"(?<![\w#&])#([A-Za-z][A-Za-z0-9]*)")
_PERSON_URN_RE = re.compile(r"^urn:li:person:[A-Za-z0-9_-]+$")


class LinkedInError(RuntimeError):
    """The LinkedIn API rejected the request or could not be reached."""


class LinkedInAuthError(LinkedInError):
    """HTTP 401: the access token is invalid or expired and must be refreshed."""


def escape_little_text(text: str) -> str:
    """Escape `text` for the `commentary` field, keeping hashtags as real hashtags."""
    out: list[str] = []
    last = 0
    for match in _HASHTAG_RE.finditer(text):
        out.append(_RESERVED_RE.sub(r"\\\1", text[last : match.start()]))
        out.append("{hashtag|\\#|" + match.group(1) + "}")
        last = match.end()
    out.append(_RESERVED_RE.sub(r"\\\1", text[last:]))
    return "".join(out)


@dataclass(frozen=True)
class PostContent:
    """What to publish. Only `text` is used in v1."""

    text: str
    media_urn: str | None = None  # future: an uploaded image, e.g. "urn:li:image:..."


@dataclass(frozen=True)
class PublishedPost:
    """The created post's id and public URL."""

    urn: str

    @property
    def url(self) -> str:
        return f"https://www.linkedin.com/feed/update/{self.urn}/"


def build_post_body(author_urn: str, content: PostContent) -> dict:
    """The JSON body for `POST /rest/posts`: public, main feed, published, reshares allowed."""
    body: dict = {
        "author": author_urn,
        "commentary": escape_little_text(content.text),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if content.media_urn:
        body["content"] = {"media": {"id": content.media_urn}}
    return body


def _error_message(response: httpx.Response) -> str:
    """A short, token-free description of an error response."""
    try:
        message = response.json().get("message", "")
    except ValueError:
        message = response.text[:200]
    return f"HTTP {response.status_code}: {message}".strip()


class LinkedInClient:
    """Creates posts as one member. Retries 429 and 5xx with exponential backoff."""

    def __init__(
        self,
        access_token: str,
        person_urn: str,
        api_version: str,
        http: httpx.Client | None = None,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not access_token:
            raise LinkedInAuthError("LINKEDIN_ACCESS_TOKEN is not set")
        if not _PERSON_URN_RE.match(person_urn or ""):
            raise LinkedInError("LINKEDIN_PERSON_URN must look like urn:li:person:<id>")
        if not re.fullmatch(r"\d{6}", api_version):
            raise LinkedInError(f"linkedin_api_version must be YYYYMM, got {api_version!r}")
        self._token = access_token
        self.person_urn = person_urn
        self.api_version = api_version
        self._http = http or httpx.Client(base_url=API_BASE, timeout=30.0)
        self.max_retries = max_retries
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "LinkedIn-Version": self.api_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }

    def create_post(self, content: PostContent) -> PublishedPost:
        """Publish a post and return its URN (from the `x-restli-id` response header)."""
        body = build_post_body(self.person_urn, content)
        if len(body["commentary"]) > MAX_COMMENTARY_CHARS:
            raise LinkedInError(f"Post is longer than {MAX_COMMENTARY_CHARS} characters")
        for attempt in range(self.max_retries + 1):
            try:
                response = self._http.post(POSTS_PATH, json=body, headers=self._headers())
            except httpx.TransportError as exc:
                error = f"network error: {type(exc).__name__}"
                delay = 2.0**attempt
            else:
                if response.status_code == 201:
                    urn = response.headers.get("x-restli-id", "")
                    if not urn:
                        raise LinkedInError("Post created but no x-restli-id header returned")
                    log_event(logger, "linkedin.posted", urn=urn, attempts=attempt + 1)
                    return PublishedPost(urn=urn)
                if response.status_code == 401:
                    raise LinkedInAuthError(
                        "LinkedIn rejected the access token (401). It has probably expired: "
                        "re-run scripts/linkedin_auth.py and update the LINKEDIN_ACCESS_TOKEN "
                        "secret and LINKEDIN_TOKEN_EXPIRES_ON variable."
                    )
                if response.status_code != 429 and response.status_code < 500:
                    raise LinkedInError(f"LinkedIn rejected the post: {_error_message(response)}")
                error = _error_message(response)
                retry_after = response.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else 2.0**attempt
            if attempt == self.max_retries:
                raise LinkedInError(f"LinkedIn API still failing after retries: {error}")
            log_event(
                logger,
                "linkedin.retry",
                logging.WARNING,
                attempt=attempt + 1,
                error=error,
                wait_s=delay,
            )
            self._sleep(min(delay, 60.0))
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class TokenStatus:
    """How close the LinkedIn token is to expiring."""

    expires_on: date
    days_left: int
    warn: bool

    @property
    def expired(self) -> bool:
        return self.days_left < 0


def token_status(expires_on: str | None, today: date, warn_days: int = 7) -> TokenStatus | None:
    """Parse `LINKEDIN_TOKEN_EXPIRES_ON` (YYYY-MM-DD). None if unset or unparseable."""
    try:
        when = date.fromisoformat((expires_on or "").strip()[:10])
    except ValueError:
        return None
    days_left = (when - today).days
    return TokenStatus(expires_on=when, days_left=days_left, warn=days_left <= warn_days)
