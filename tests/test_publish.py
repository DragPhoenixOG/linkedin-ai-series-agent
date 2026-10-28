"""Live runs end to end, with the model, research sources, LinkedIn and GitHub all faked."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from agent.linkedin import LinkedInAuthError, LinkedInError
from agent.llm import LLMError
from agent.main import EXIT_FAILED, EXIT_OK, Services, parse_args, run
from agent.notify import NEEDS_ATTENTION, POSTED, SCHEDULED, TOKEN_EXPIRY, NotifyError
from agent.topics import load_topics, save_topics
from conftest import CLEAN_POST
from fakes import FakeLinkedIn, FakeLLM, FakeNotifier
from test_main import (
    add_history,
    editor_reply,
    fake_sources,
    past_post,
    research_replies,
    root,
    snapshot,
)

__all__ = ["root"]  # pytest fixture re-exported from test_main


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    output = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("LINKEDIN_TOKEN_EXPIRES_ON", "2026-12-20")
    return output


def happy_llm() -> FakeLLM:
    return FakeLLM(
        {
            **research_replies(),
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": editor_reply(),
            "topics.propose": {
                "topics": [
                    {"title": f"Fresh idea {w}", "angle": "x", "kind": "evergreen"}
                    for w in ("alpha", "beta", "gamma", "delta", "epsilon")
                ]
            },
        }
    )


def live(
    root: Path,
    *argv: str,
    llm: FakeLLM | None = None,
    linkedin: FakeLinkedIn | None = None,
    notifier: FakeNotifier | None = None,
    date: str = "2026-10-28",
) -> int:
    def llm_factory(config):
        if llm is None:
            raise AssertionError("the model must not be called in this run")
        return llm

    def linkedin_factory(config):
        return linkedin or FakeLinkedIn()

    services = Services(
        llm_factory=llm_factory,
        linkedin_factory=linkedin_factory,
        notifier_factory=lambda: notifier or FakeNotifier(),
        sources_factory=lambda config, root: fake_sources(),
    )
    return run(parse_args(["--root", str(root), "--date", date, *argv]), services, io.StringIO())


def history(root: Path) -> dict:
    return json.loads((root / "state" / "history.json").read_text())


def set_review_window(root: Path, hours: int) -> None:
    config = (
        (root / "config.yaml")
        .read_text()
        .replace("review_window_hours: 0", f"review_window_hours: {hours}")
    )
    (root / "config.yaml").write_text(config)


def test_first_post_is_published_and_recorded(root: Path, env: Path) -> None:
    linkedin, notifier = FakeLinkedIn(), FakeNotifier()
    code = live(root, llm=happy_llm(), linkedin=linkedin, notifier=notifier)
    assert code == EXIT_OK
    assert linkedin.posts == [CLEAN_POST]

    [post] = history(root)["posts"]
    assert post["date"] == "2026-10-28"
    assert post["urn"] == "urn:li:share:1"
    assert post["linkedin_url"] == "https://www.linkedin.com/feed/update/urn:li:share:1/"
    assert post["topic_id"] == "generative-to-agentic-ai"
    assert post["editor_score"] == 9
    assert post["sources"][0]["url"] == "https://vendor.example/blog"
    assert "pending" not in history(root)

    archive = root / "posts" / "2026-10-28-from-generative-ai-to-agentic-ai.md"
    assert CLEAN_POST in archive.read_text()

    topics = load_topics(root / "content" / "topics.yaml")
    assert topics[0].status == "posted"
    assert topics[0].linkedin_url == post["linkedin_url"]

    assert notifier.titles(POSTED) == ["Posted: From Generative AI to Agentic AI (2026-10-28)"]
    body = notifier.issues[1]["body"]
    assert post["linkedin_url"] in body and "human 9/10" in body and "vendor.example" in body

    outputs = env.read_text()
    assert "commit_message=chore: post from-generative-ai-to-agentic-ai" in outputs


def test_not_a_posting_day_does_nothing(root: Path) -> None:
    add_history(root, past_post("2026-10-27"))
    before = snapshot(root)
    linkedin, notifier = FakeLinkedIn(), FakeNotifier()
    assert live(root, linkedin=linkedin, notifier=notifier) == EXIT_OK
    assert snapshot(root) == before
    assert linkedin.posts == [] and notifier.issues == {}


def test_token_reminder_opens_once_then_updates(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_history(root, past_post("2026-10-27"))  # not a posting day: the check runs anyway
    monkeypatch.setenv("LINKEDIN_TOKEN_EXPIRES_ON", "2026-11-02")
    notifier = FakeNotifier()
    live(root, notifier=notifier)
    live(root, notifier=notifier, date="2026-10-29")
    assert notifier.titles(TOKEN_EXPIRY) == [
        "LinkedIn token expires on 2026-11-02: re-run scripts/linkedin_auth.py"
    ]
    assert "4 days" in notifier.issues[1]["body"]


def test_no_token_reminder_when_far_from_expiry(root: Path) -> None:
    add_history(root, past_post("2026-10-27"))
    notifier = FakeNotifier()
    live(root, notifier=notifier)
    assert notifier.issues == {}


def test_quality_failure_opens_needs_attention_and_posts_nothing(root: Path) -> None:
    before = snapshot(root)
    llm = FakeLLM(
        {
            **research_replies(),
            "writer": "<post>Too short.</post>",
            "editor": editor_reply(score=5) | {"rewrite": "Too short."},
        }
    )
    linkedin, notifier = FakeLinkedIn(), FakeNotifier()
    assert live(root, llm=llm, linkedin=linkedin, notifier=notifier) == EXIT_FAILED
    assert linkedin.posts == []
    assert snapshot(root) == before
    [title] = notifier.titles(NEEDS_ATTENTION)
    assert title == "Post needs attention: From Generative AI to Agentic AI (2026-10-28)"
    body = notifier.issues[1]["body"]
    assert "Too short." in body and "[length]" in body


def test_expired_token_opens_one_issue_and_keeps_state(root: Path) -> None:
    before = snapshot(root)
    notifier = FakeNotifier()
    linkedin = FakeLinkedIn(error=LinkedInAuthError("token expired"))
    assert live(root, llm=happy_llm(), linkedin=linkedin, notifier=notifier) == EXIT_FAILED
    assert live(root, llm=happy_llm(), linkedin=linkedin, notifier=notifier) == EXIT_FAILED
    assert snapshot(root) == before
    assert notifier.titles(NEEDS_ATTENTION) == [
        "LinkedIn token rejected: re-run scripts/linkedin_auth.py"
    ]


def test_linkedin_rejection_opens_issue_with_draft(root: Path) -> None:
    notifier = FakeNotifier()
    linkedin = FakeLinkedIn(error=LinkedInError("LinkedIn rejected the post: HTTP 422"))
    assert live(root, llm=happy_llm(), linkedin=linkedin, notifier=notifier) == EXIT_FAILED
    [title] = notifier.titles(NEEDS_ATTENTION)
    assert title.startswith("LinkedIn rejected the post")
    assert CLEAN_POST in notifier.issues[1]["body"]
    assert history(root)["posts"] == []


def test_missing_linkedin_settings_fail_before_spending_on_claude(root: Path) -> None:
    def broken(config):
        raise LinkedInError("LINKEDIN_PERSON_URN must look like urn:li:person:<id>")

    notifier = FakeNotifier()
    services = Services(
        llm_factory=lambda c: (_ for _ in ()).throw(AssertionError("no Claude calls")),
        linkedin_factory=broken,
        notifier_factory=lambda: notifier,
    )
    code = run(parse_args(["--root", str(root), "--date", "2026-10-28"]), services, io.StringIO())
    assert code == EXIT_FAILED
    assert notifier.titles(NEEDS_ATTENTION) == ["LinkedIn settings missing or invalid (2026-10-28)"]


def test_claude_failure_opens_needs_attention(root: Path) -> None:
    llm = FakeLLM({**research_replies(), "writer": LLMError("overloaded")})
    notifier = FakeNotifier()
    assert live(root, llm=llm, notifier=notifier) == EXIT_FAILED
    assert notifier.titles(NEEDS_ATTENTION) == ["Post needs attention (2026-10-28)"]
    assert "overloaded" in notifier.issues[1]["body"]


def test_github_outage_never_blocks_a_post(root: Path) -> None:
    class BrokenNotifier(FakeNotifier):
        def create_issue(self, title, body, labels):
            raise NotifyError("GitHub API unreachable")

    linkedin = FakeLinkedIn()
    assert live(root, llm=happy_llm(), linkedin=linkedin, notifier=BrokenNotifier()) == EXIT_OK
    assert linkedin.posts == [CLEAN_POST]
    assert history(root)["posts"][0]["urn"] == "urn:li:share:1"


def test_backlog_is_topped_up_after_a_post(root: Path) -> None:
    path = root / "content" / "topics.yaml"
    topics = load_topics(path)[:3]  # exactly 3 pending; this post leaves 2
    save_topics(topics, path)
    llm = happy_llm()
    assert live(root, llm=llm) == EXIT_OK
    after = load_topics(path)
    assert [t.status for t in after[:3]] == ["posted", "pending", "pending"]
    assert [t.source for t in after[3:]] == ["auto"] * 5
    propose_call = next(c for c in llm.calls if c["label"] == "topics.propose")
    assert "A vendor shipped an agent runtime." in propose_call["prompt"]  # research feeds ideas


def test_review_window_schedules_then_publishes(root: Path) -> None:
    set_review_window(root, 24)
    linkedin, notifier = FakeLinkedIn(), FakeNotifier()

    assert live(root, llm=happy_llm(), linkedin=linkedin, notifier=notifier) == EXIT_OK
    assert linkedin.posts == []
    pending = history(root)["pending"]
    assert pending["publish_after"] == "2026-10-29T12:00:00+00:00"
    assert notifier.titles(SCHEDULED) == [
        "Scheduled: From Generative AI to Agentic AI (2026-10-28)"
    ]

    # Same day again: still waiting, no second draft.
    assert live(root, linkedin=linkedin, notifier=notifier) == EXIT_OK
    assert linkedin.posts == []

    # Next day, after the window: published, scheduled issue closed.
    assert (
        live(root, llm=happy_llm(), linkedin=linkedin, notifier=notifier, date="2026-10-29")
        == EXIT_OK
    )
    assert linkedin.posts == [CLEAN_POST]
    data = history(root)
    assert "pending" not in data
    assert data["posts"][0]["date"] == "2026-10-29"
    assert notifier.issues[1]["state"] == "closed"
    assert notifier.titles(POSTED) == ["Posted: From Generative AI to Agentic AI (2026-10-29)"]


def test_rejected_draft_is_dropped(root: Path) -> None:
    set_review_window(root, 24)
    notifier = FakeNotifier()
    live(root, llm=happy_llm(), notifier=notifier)
    notifier.issues[1]["labels"].append("reject")

    linkedin = FakeLinkedIn()
    add_history_keep_pending = history(root)
    assert add_history_keep_pending["pending"]
    # The next day the draft is dropped and a fresh one is scheduled instead of publishing.
    assert (
        live(root, llm=happy_llm(), linkedin=linkedin, notifier=notifier, date="2026-10-29")
        == EXIT_OK
    )
    assert linkedin.posts == []
    assert history(root)["pending"]["record"]["date"] == "2026-10-29"
    assert len(notifier.titles(SCHEDULED)) == 2
