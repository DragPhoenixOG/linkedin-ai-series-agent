"""End-to-end runs of `main.run` with a fake Claude API. No network, no writes in dry-run."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from pathlib import Path

import pytest

from agent.main import EXIT_FAILED, EXIT_OK, Services, parse_args, run
from conftest import CLEAN_POST
from fakes import FakeLLM
from test_news import cand, verified

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for name in ("config.yaml",):
        shutil.copy(REPO / name, tmp_path / name)
    for folder in ("content", "state", "posts"):
        shutil.copytree(REPO / folder, tmp_path / folder)
    return tmp_path


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def editor_reply(score: int = 9, humour: int = 8) -> dict:
    return {
        "human_score": score,
        "humour_score": humour,
        "ai_sounding": [],
        "fact_problems": [],
        "rule_problems": [],
        "verdict": "Reads like a person.",
        "rewrite": CLEAN_POST,
    }


def research_reply() -> dict:
    return {
        "developments": [
            {
                "summary": "A vendor shipped an agent runtime.",
                "date": "2026-10-20",
                "why_it_matters": "Reliability.",
                "sources": [{"url": "https://vendor.example/blog", "publisher": "Vendor"}],
            }
        ]
    }


def add_history(root: Path, *posts: dict) -> None:
    (root / "state" / "history.json").write_text(json.dumps({"posts": list(posts)}))


def past_post(
    day: str, post_type: str = "skill", tone: str = "straight", fmt: str = "observation"
) -> dict:
    return {
        "date": day,
        "post_type": post_type,
        "tone": tone,
        "format": fmt,
        "text": f"An older post from {day}.\n\nBody text.",
        "slug": f"old-{day}",
        "topic_title": f"Old topic {day}",
    }


def go(root: Path, llm: FakeLLM | None, *argv: str) -> tuple[int, str]:
    out = io.StringIO()

    def factory(config):
        if llm is None:
            raise AssertionError("the Claude API must not be called in this run")
        return llm

    def forbidden(*_args):
        raise AssertionError("dry runs must not touch LinkedIn or GitHub")

    services = Services(llm_factory=factory, linkedin_factory=forbidden, notifier_factory=forbidden)
    code = run(
        parse_args(["--root", str(root), "--date", "2026-10-28", *argv]),
        services=services,
        out=out,
    )
    return code, out.getvalue()


def test_not_a_posting_day_exits_cleanly_without_calling_claude(root: Path) -> None:
    add_history(root, past_post("2026-10-27"))
    before = snapshot(root)
    code, output = go(root, None, "--dry-run")
    assert code == EXIT_OK
    assert output == ""
    assert snapshot(root) == before


def test_dry_run_skill_post_end_to_end_without_writes(root: Path) -> None:
    llm = FakeLLM(
        {
            "research": research_reply(),
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": editor_reply(),
        }
    )
    before = snapshot(root)
    output_file = root.parent / "report.txt"
    code, output = go(root, llm, "--dry-run", "--force", "--output", str(output_file))
    assert code == EXIT_OK
    assert snapshot(root) == before  # state/, content/, posts/ untouched
    assert "DRY RUN" in output
    assert "Type:     skill" in output
    assert "Subject:  From Generative AI to Agentic AI" in output  # first seed topic
    assert "A vendor shipped an agent runtime." in output
    assert CLEAN_POST in output
    assert "PASSED on attempt 1" in output
    assert "9 topics pending after this post" not in output
    assert "8 topics pending after this post; no top-up needed" in output
    assert output_file.read_text() == output
    labels = [c["label"] for c in llm.calls]
    assert labels == ["research", "writer", "editor"]  # first post: no news scan


def test_dry_run_news_post(root: Path) -> None:
    add_history(root, past_post("2026-10-20"))
    llm = FakeLLM(
        {
            "news.scan": {"candidates": [cand("Regulator publishes AI audit rules", relevance=5)]},
            "news.verify": verified(),
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": editor_reply(),
        }
    )
    code, output = go(root, llm, "--dry-run", "--post-type", "news", "--tone", "humorous")
    assert code == EXIT_OK
    assert "Type:     news" in output
    assert "Tone:     humorous" in output
    assert "Subject:  Regulator publishes AI audit rules" in output
    assert "humour 8/10" in output
    assert "research" not in [c["label"] for c in llm.calls]


def test_news_falls_back_to_skill_when_nothing_qualifies(root: Path) -> None:
    add_history(root, past_post("2026-10-20"))
    llm = FakeLLM(
        {
            "news.scan": {"candidates": [cand("Leak about a model", confirmed=False)]},
            "research": research_reply(),
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": editor_reply(),
        }
    )
    code, output = go(root, llm, "--dry-run", "--post-type", "news")
    assert code == EXIT_OK
    assert "Type:     skill" in output
    assert "fell back to a skill post" in output


def test_major_story_jumps_the_queue(root: Path) -> None:
    add_history(root, past_post("2026-10-20"))
    llm = FakeLLM(
        {
            "news.scan": {"candidates": [cand("Huge model launch", significance="major")]},
            "news.verify": verified(),
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": editor_reply(),
        }
    )
    code, output = go(root, llm, "--dry-run", "--seed", "2")  # seed 2 rolls a skill post
    assert code == EXIT_OK
    assert "Type:     news" in output
    assert "jumped the queue" in output


def test_topic_override(root: Path) -> None:
    llm = FakeLLM(
        {
            "research": {"developments": []},
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": editor_reply(),
        }
    )
    code, output = go(root, llm, "--dry-run", "--force", "--topic-id", "model-context-protocol")
    assert code == EXIT_OK
    assert "Subject:  Model Context Protocol (MCP)" in output
    assert "without referencing recent news" in output


def test_failed_generation_returns_failure_and_shows_reasons(root: Path) -> None:
    llm = FakeLLM(
        {
            "research": research_reply(),
            "writer": "<post>Too short.</post>",
            "editor": editor_reply(score=5) | {"rewrite": "Too short."},
        }
    )
    code, output = go(root, llm, "--dry-run", "--force")
    assert code == EXIT_FAILED
    assert "FAILED after 3 attempts" in output
    assert "[length]" in output


def test_unknown_topic_id_fails_cleanly(root: Path) -> None:
    llm = FakeLLM({})
    code, _ = go(root, llm, "--dry-run", "--force", "--topic-id", "nope")
    assert code == EXIT_FAILED


def test_topic_id_with_news_type_is_rejected() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--topic-id", "x", "--post-type", "news"])
