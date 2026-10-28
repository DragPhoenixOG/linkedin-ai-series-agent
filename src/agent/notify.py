"""GitHub Issues as the agent's record and alert channel (GitHub emails you about new issues).

- `posted`: one issue per published post, with the text, link, scores and sources.
- `needs-attention`: generation failed, LinkedIn rejected the post, or the token stopped working.
- `token-expiry`: one open reminder (updated, never duplicated) when the token is about to expire.
- `scheduled`: a draft waiting out the optional review window. Close it or add the `reject`
  label to stop it from being published.

In GitHub Actions this uses `GITHUB_TOKEN` and `GITHUB_REPOSITORY`. Without them (local runs),
`LogNotifier` writes the would-be issues to the log instead.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Protocol

import httpx

from agent.linkedin import TokenStatus
from agent.logs import log_event
from agent.state import PostRecord

logger = logging.getLogger(__name__)

POSTED, NEEDS_ATTENTION, SCHEDULED, REJECT, TOKEN_EXPIRY = (
    "posted",
    "needs-attention",
    "scheduled",
    "reject",
    "token-expiry",
)
LABEL_COLORS = {
    POSTED: "0e8a16",
    NEEDS_ATTENTION: "d93f0b",
    SCHEDULED: "fbca04",
    REJECT: "b60205",
    TOKEN_EXPIRY: "5319e7",
}
TOKEN_FAILURE_TITLE = "LinkedIn token rejected: re-run scripts/linkedin_auth.py"


class NotifyError(RuntimeError):
    """The GitHub API call failed."""


class Notifier(Protocol):
    """The issue operations the agent needs."""

    def create_issue(self, title: str, body: str, labels: list[str]) -> int | None: ...
    def find_open_issue(self, label: str, title: str | None = None) -> int | None: ...
    def update_issue(self, number: int, title: str, body: str) -> None: ...
    def comment_and_close(self, number: int, comment: str) -> None: ...
    def issue_state(self, number: int) -> tuple[str, list[str]] | None: ...


class LogNotifier:
    """Used when there is no GitHub token: logs what would have been filed."""

    def create_issue(self, title: str, body: str, labels: list[str]) -> int | None:
        log_event(logger, "notify.issue_skipped", title=title, labels=",".join(labels))
        return None

    def find_open_issue(self, label: str, title: str | None = None) -> int | None:
        return None

    def update_issue(self, number: int, title: str, body: str) -> None:
        log_event(logger, "notify.update_skipped", number=number, title=title)

    def comment_and_close(self, number: int, comment: str) -> None:
        log_event(logger, "notify.close_skipped", number=number)

    def issue_state(self, number: int) -> tuple[str, list[str]] | None:
        return None


class GitHubNotifier:
    """Talks to the GitHub REST API for one repository."""

    def __init__(self, token: str, repo: str, http: httpx.Client | None = None) -> None:
        self.repo = repo
        self._http = http or httpx.Client(base_url="https://api.github.com", timeout=20.0)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._labels_ready: set[str] = set()

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, headers=self._headers, **kwargs)
        except httpx.TransportError as exc:
            raise NotifyError(f"GitHub API unreachable: {type(exc).__name__}") from exc
        if response.status_code >= 400 and not (method == "POST" and path.endswith("/labels")):
            raise NotifyError(f"GitHub API {method} {path} failed: HTTP {response.status_code}")
        return response

    def _ensure_labels(self, labels: list[str]) -> None:
        for label in labels:
            if label not in self._labels_ready:
                # 201 created, or 422 if it already exists. Either way it exists now.
                self._call(
                    "POST",
                    f"/repos/{self.repo}/labels",
                    json={"name": label, "color": LABEL_COLORS.get(label, "ededed")},
                )
                self._labels_ready.add(label)

    def create_issue(self, title: str, body: str, labels: list[str]) -> int | None:
        self._ensure_labels(labels)
        response = self._call(
            "POST",
            f"/repos/{self.repo}/issues",
            json={"title": title, "body": body, "labels": labels},
        )
        number = response.json()["number"]
        log_event(logger, "notify.issue_created", number=number, title=title)
        return number

    def find_open_issue(self, label: str, title: str | None = None) -> int | None:
        response = self._call(
            "GET",
            f"/repos/{self.repo}/issues",
            params={"state": "open", "labels": label, "per_page": 50},
        )
        for issue in response.json():
            if "pull_request" not in issue and (title is None or issue["title"] == title):
                return issue["number"]
        return None

    def update_issue(self, number: int, title: str, body: str) -> None:
        self._call(
            "PATCH", f"/repos/{self.repo}/issues/{number}", json={"title": title, "body": body}
        )
        log_event(logger, "notify.issue_updated", number=number)

    def comment_and_close(self, number: int, comment: str) -> None:
        self._call("POST", f"/repos/{self.repo}/issues/{number}/comments", json={"body": comment})
        self._call("PATCH", f"/repos/{self.repo}/issues/{number}", json={"state": "closed"})

    def issue_state(self, number: int) -> tuple[str, list[str]] | None:
        data = self._call("GET", f"/repos/{self.repo}/issues/{number}").json()
        return data["state"], [label["name"] for label in data.get("labels", [])]


def notifier_from_env() -> Notifier:
    """`GitHubNotifier` in Actions (GITHUB_TOKEN + GITHUB_REPOSITORY), else `LogNotifier`."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if token and repo:
        return GitHubNotifier(token, repo)
    log_event(
        logger, "notify.local", reason="GITHUB_TOKEN or GITHUB_REPOSITORY not set; logging only"
    )
    return LogNotifier()


# --- Issue bodies ------------------------------------------------------------------------------


def _scores(record: PostRecord) -> str:
    humour = f", humour {record.humour_score}/10" if record.humour_score is not None else ""
    return f"human {record.editor_score}/10{humour}"


def _sources(record: PostRecord) -> str:
    if not record.sources:
        return "_None: written without recent-news references._"
    return "\n".join(f"- [{s.title or s.publisher or s.url}]({s.url})" for s in record.sources)


def post_details(record: PostRecord) -> str:
    """Shared body: metadata, the post text, and research sources."""
    return "\n".join(
        [
            f"**Type:** {record.post_type} · **Tone:** {record.tone} · **Format:** {record.format}",
            f"**Editor scores:** {_scores(record)} · **Attempts:** {record.attempts}",
            "",
            "### Post",
            "",
            "```text",
            record.text,
            "```",
            "",
            "### Research sources",
            "",
            _sources(record),
        ]
    )


def notify_posted(notifier: Notifier, record: PostRecord) -> int | None:
    """Open the `Posted: <title> (<date>)` record issue."""
    body = f"**LinkedIn:** {record.linkedin_url}\n\n{post_details(record)}"
    return notifier.create_issue(f"Posted: {record.title} ({record.date})", body, [POSTED])


def notify_failure(
    notifier: Notifier, title: str, reasons: list[str], record: PostRecord | None = None
) -> int | None:
    """Open a `needs-attention` issue. Nothing was posted."""
    lines = ["Nothing was posted. What went wrong:", ""]
    lines += [f"- {r}" for r in reasons] or ["- (no details)"]
    if record:
        lines += ["", "## Best draft", "", post_details(record)]
    lines += ["", "The next scheduled run will try again on its own."]
    return notifier.create_issue(title, "\n".join(lines), [NEEDS_ATTENTION])


def notify_token_failure(notifier: Notifier, detail: str, today: date) -> int | None:
    """401 from LinkedIn: open (or refresh) one needs-attention issue about the token."""
    body = (
        f"LinkedIn rejected the access token on {today.isoformat()}: {detail}\n\n"
        "To fix it:\n"
        "1. On your computer, run `python scripts/linkedin_auth.py`.\n"
        "2. Update the `LINKEDIN_ACCESS_TOKEN` secret and the `LINKEDIN_TOKEN_EXPIRES_ON` variable "
        "(Settings > Secrets and variables > Actions).\n"
        "3. Close this issue. The next run posts as normal."
    )
    existing = notifier.find_open_issue(NEEDS_ATTENTION, TOKEN_FAILURE_TITLE)
    if existing:
        notifier.update_issue(existing, TOKEN_FAILURE_TITLE, body)
        return existing
    return notifier.create_issue(TOKEN_FAILURE_TITLE, body, [NEEDS_ATTENTION])


def remind_token_expiry(notifier: Notifier, status: TokenStatus) -> int | None:
    """Open, or update, the single reminder issue when the token is close to expiring."""
    when = status.expires_on.isoformat()
    if status.expired:
        title = f"LinkedIn token expired on {when}: re-run scripts/linkedin_auth.py"
    else:
        title = f"LinkedIn token expires on {when}: re-run scripts/linkedin_auth.py"
    body = (
        f"The LinkedIn access token {'expired' if status.expired else 'expires'} on {when} "
        f"({status.days_left} days from the last check). Member tokens can't be refreshed "
        "automatically.\n\n"
        "1. On your computer, run `python scripts/linkedin_auth.py`.\n"
        "2. Update the `LINKEDIN_ACCESS_TOKEN` secret and set the `LINKEDIN_TOKEN_EXPIRES_ON` "
        "variable to the new date.\n"
        "3. Close this issue."
    )
    existing = notifier.find_open_issue(TOKEN_EXPIRY)
    if existing:
        notifier.update_issue(existing, title, body)
        return existing
    return notifier.create_issue(title, body, [TOKEN_EXPIRY])


def notify_scheduled(notifier: Notifier, record: PostRecord, publish_after: str) -> int | None:
    """Open the `scheduled` issue for a draft in the review window."""
    body = (
        f"This post will be published after **{publish_after} UTC** unless you close this issue "
        f"or add the `{REJECT}` label.\n\n{post_details(record)}"
    )
    return notifier.create_issue(f"Scheduled: {record.title} ({record.date})", body, [SCHEDULED])


def draft_rejected(notifier: Notifier, issue_number: int | None) -> bool:
    """True if the scheduled draft's issue was closed or labelled `reject`."""
    if issue_number is None:
        return False
    state = notifier.issue_state(issue_number)
    if state is None:
        return False
    status, labels = state
    return status == "closed" or REJECT in labels
