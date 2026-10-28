"""GitHub Issues client and issue bodies, with the GitHub API mocked."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from agent.linkedin import token_status
from agent.notify import (
    GitHubNotifier,
    LogNotifier,
    NotifyError,
    draft_rejected,
    notifier_from_env,
    notify_failure,
    notify_posted,
    remind_token_expiry,
)
from agent.state import PostRecord, Source
from fakes import FakeNotifier


def record() -> PostRecord:
    return PostRecord(
        date="2026-10-28",
        post_type="skill",
        tone="humorous",
        format="dry_take",
        text="Post body here.\n\n#AI",
        slug="mcp",
        topic_title="Model Context Protocol (MCP)",
        editor_score=9,
        humour_score=8,
        attempts=2,
        linkedin_url="https://www.linkedin.com/feed/update/urn:li:share:1/",
        sources=[Source(url="https://vendor.example/blog", title="Vendor blog")],
    )


def github(handler) -> GitHubNotifier:
    http = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    return GitHubNotifier("gh-token", "owner/repo", http=http)


def test_create_issue_ensures_labels_once_and_tolerates_existing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/labels"):
            return httpx.Response(422, json={"message": "already_exists"})
        return httpx.Response(201, json={"number": 42})

    notifier = github(handler)
    assert notifier.create_issue("Posted: x", "body", ["posted"]) == 42
    assert notifier.create_issue("Posted: y", "body", ["posted"]) == 42
    paths = [r.url.path for r in seen]
    assert paths == [
        "/repos/owner/repo/labels",
        "/repos/owner/repo/issues",
        "/repos/owner/repo/issues",
    ]
    assert seen[0].headers["Authorization"] == "Bearer gh-token"
    assert json.loads(seen[1].content) == {
        "title": "Posted: x",
        "body": "body",
        "labels": ["posted"],
    }


def test_find_open_issue_skips_pull_requests_and_matches_title() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["state"] == "open"
        assert request.url.params["labels"] == "needs-attention"
        return httpx.Response(
            200,
            json=[
                {"number": 1, "title": "Token", "pull_request": {}},
                {"number": 2, "title": "Other"},
                {"number": 3, "title": "Token"},
            ],
        )

    notifier = github(handler)
    assert notifier.find_open_issue("needs-attention", "Token") == 3
    assert notifier.find_open_issue("needs-attention") == 2


def test_issue_state_and_close() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"state": "open", "labels": [{"name": "reject"}]})
        return httpx.Response(200, json={})

    notifier = github(handler)
    assert notifier.issue_state(7) == ("open", ["reject"])
    notifier.comment_and_close(7, "Published")
    assert seen[1:] == [
        ("POST", "/repos/owner/repo/issues/7/comments"),
        ("PATCH", "/repos/owner/repo/issues/7"),
    ]


def test_api_errors_raise_notify_error() -> None:
    notifier = github(lambda r: httpx.Response(500))
    with pytest.raises(NotifyError):
        notifier.find_open_issue("posted")


def test_notifier_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert isinstance(notifier_from_env(), LogNotifier)
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    assert isinstance(notifier_from_env(), GitHubNotifier)


def test_posted_issue_title_and_body() -> None:
    notifier = FakeNotifier()
    notify_posted(notifier, record())
    issue = notifier.issues[1]
    assert issue["title"] == "Posted: Model Context Protocol (MCP) (2026-10-28)"
    assert issue["labels"] == ["posted"]
    for expected in (
        "https://www.linkedin.com/feed/update/urn:li:share:1/",
        "**Type:** skill",
        "**Tone:** humorous",
        "**Format:** dry_take",
        "human 9/10, humour 8/10",
        "Post body here.",
        "[Vendor blog](https://vendor.example/blog)",
    ):
        assert expected in issue["body"]


def test_failure_issue_lists_reasons_and_draft() -> None:
    notifier = FakeNotifier()
    notify_failure(notifier, "Post needs attention", ["Editor scored 6/10"], record())
    body = notifier.issues[1]["body"]
    assert notifier.issues[1]["labels"] == ["needs-attention"]
    assert (
        "Nothing was posted" in body and "Editor scored 6/10" in body and "Post body here." in body
    )


def test_expired_token_reminder_wording() -> None:
    notifier = FakeNotifier()
    remind_token_expiry(notifier, token_status("2026-10-20", date(2026, 10, 28)))
    assert notifier.titles() == [
        "LinkedIn token expired on 2026-10-20: re-run scripts/linkedin_auth.py"
    ]


def test_draft_rejected_rules() -> None:
    notifier = FakeNotifier()
    number = notifier.create_issue("Scheduled: x", "b", ["scheduled"])
    assert not draft_rejected(notifier, number)
    notifier.issues[number]["labels"].append("reject")
    assert draft_rejected(notifier, number)
    notifier.issues[number]["labels"].remove("reject")
    notifier.issues[number]["state"] = "closed"
    assert draft_rejected(notifier, number)
    assert not draft_rejected(notifier, None)
    assert not draft_rejected(LogNotifier(), 5)
