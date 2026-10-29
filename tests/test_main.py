"""End-to-end dry runs of `main.run` with the model and network faked. No writes in dry-run."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest

from agent.config import Config
from agent.llm import AnthropicLLM, LLMError, OpenAICompatLLM
from agent.main import EXIT_FAILED, EXIT_OK, EXIT_USAGE, Services, parse_args, run
from conftest import CLEAN_POST
from fakes import FakeClient, FakeLLM, FakeSources, article
from test_news import HEADLINES, RULES_OFFICIAL, cand, verified

REPO = Path(__file__).resolve().parents[1]

AGENT_RUNTIME = article(
    "Agentic AI moves from chat to actions with a new agent runtime",
    "https://vendor.example/blog",
    day="2026-10-20",
    source="Vendor Blog",
    official=True,
    summary="A vendor shipped an agent runtime for agentic AI workflows.",
)


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


def research_replies(found: bool = True) -> dict:
    """Model replies for the two research calls (queries, then the pick)."""
    developments = [
        {
            "summary": "A vendor shipped an agent runtime.",
            "why_it_matters": "Reliability.",
            "article_ids": ["A1"],
        }
    ]
    return {
        "research.queries": {"queries": ["agentic AI"]},
        "research": {"developments": developments if found else []},
    }


def fake_sources() -> FakeSources:
    return FakeSources(feed=[AGENT_RUNTIME, *HEADLINES])


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


def go(
    root: Path, llm: FakeLLM | None, *argv: str, sources: FakeSources | None = None
) -> tuple[int, str]:
    out = io.StringIO()

    def factory(config):
        if llm is None:
            raise AssertionError("the model must not be called in this run")
        return llm

    def forbidden(*_args):
        raise AssertionError("dry runs must not touch LinkedIn or GitHub")

    services = Services(
        llm_factory=factory,
        linkedin_factory=forbidden,
        notifier_factory=forbidden,
        sources_factory=lambda config, root: sources or fake_sources(),
    )
    code = run(
        parse_args(["--root", str(root), "--date", "2026-10-28", *argv]),
        services=services,
        out=out,
    )
    return code, out.getvalue()


def test_not_a_posting_day_exits_cleanly_without_calling_the_model(root: Path) -> None:
    add_history(root, past_post("2026-10-27"))
    before = snapshot(root)
    code, output = go(root, None, "--dry-run")
    assert code == EXIT_OK
    assert output == ""
    assert snapshot(root) == before


def test_dry_run_skill_post_end_to_end_without_writes(root: Path) -> None:
    llm = FakeLLM(
        {**research_replies(), "writer": f"<post>{CLEAN_POST}</post>", "editor": editor_reply()}
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
    assert "https://vendor.example/blog" in output  # the real article, cited by id
    assert CLEAN_POST in output
    assert "PASSED on attempt 1" in output
    assert "8 topics pending after this post; no top-up needed" in output
    assert output_file.read_text() == output
    labels = [c["label"] for c in llm.calls]
    assert labels == ["research.queries", "research", "writer", "editor"]  # first post: no news


def test_dry_run_news_post(root: Path) -> None:
    add_history(root, past_post("2026-10-20"))
    llm = FakeLLM(
        {
            "news.scan": {
                "candidates": [
                    cand("Regulator publishes AI audit rules", [RULES_OFFICIAL], relevance=5)
                ]
            },
            "news.verify": verified(
                facts=[{"fact": "x", "supported": True, "article_ids": ["A1"]}]
            ),
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": editor_reply(),
        }
    )
    code, output = go(
        root,
        llm,
        "--dry-run",
        "--post-type",
        "news",
        "--tone",
        "humorous",
        sources=FakeSources(feed=list(HEADLINES)),
    )
    assert code == EXIT_OK
    assert "Type:     news" in output
    assert "Tone:     humorous" in output
    assert "Subject:  Regulator publishes AI audit rules" in output
    assert "https://regulator.example/rules" in output
    assert "humour 8/10" in output
    assert not any(c["label"].startswith("research") for c in llm.calls)


def test_news_falls_back_to_skill_when_nothing_qualifies(root: Path) -> None:
    add_history(root, past_post("2026-10-20"))
    llm = FakeLLM(
        {
            "news.scan": {"candidates": [cand("Leak about a model", confirmed=False)]},
            **research_replies(),
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
            "news.scan": {
                "candidates": [cand("Huge model launch", [RULES_OFFICIAL], significance="major")]
            },
            "news.verify": verified(
                facts=[{"fact": "x", "supported": True, "article_ids": ["A1"]}]
            ),
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": editor_reply(),
        }
    )
    code, output = go(
        root, llm, "--dry-run", "--seed", "2", sources=FakeSources(feed=list(HEADLINES))
    )  # seed 2 rolls a skill post
    assert code == EXIT_OK
    assert "Type:     news" in output
    assert "jumped the queue" in output


def test_topic_override_without_research(root: Path) -> None:
    llm = FakeLLM(
        {
            **research_replies(found=False),
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
            **research_replies(),
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


# --- --check ------------------------------------------------------------------------------------


def checker(models: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": f"models/{m}"} for m in models]})

    def factory(config: Config) -> OpenAICompatLLM:
        http = httpx.Client(transport=httpx.MockTransport(handler))
        return OpenAICompatLLM(config, "https://llm.example/v1", "k", http=http)

    return factory


def run_check(root: Path, llm_factory, sources: FakeSources) -> tuple[int, str]:
    out = io.StringIO()
    services = Services(llm_factory=llm_factory, sources_factory=lambda c, r: sources)
    code = run(parse_args(["--root", str(root), "--date", "2026-10-28", "--check"]), services, out)
    return code, out.getvalue()


def test_check_reports_models_and_feeds(root: Path) -> None:
    sources = fake_sources()
    sources.feed_status = {"Vendor Blog": "12 items", "Dead Feed": "unreachable"}
    sources.hn = {"*": [article("AI thing", "https://x.example", "2026-10-27")]}
    code, output = run_check(root, checker(["gemini-3.6-flash", "gemini-3.5-flash-lite"]), sources)
    assert code == EXIT_OK
    assert "model gemini-3.6-flash: available" in output
    assert "available models: gemini-3.5-flash-lite, gemini-3.6-flash" in output
    assert "Dead Feed: unreachable" in output
    assert "Hacker News search: 1 results" in output
    assert output.strip().endswith("All good.")


def test_check_counts_tokens_for_claude_models(root: Path) -> None:
    def handler(req):
        if req["model"] == "gemini-3.5-flash-lite":
            response = httpx2.Response(404, request=httpx2.Request("POST", "https://x.example"))
            raise anthropic.NotFoundError("model not found", response=response, body=None)
        return SimpleNamespace(input_tokens=3)

    def factory(config: Config) -> AnthropicLLM:
        return AnthropicLLM(FakeClient(handler), config, server_fallbacks=False)

    code, output = run_check(root, factory, fake_sources())
    assert code == EXIT_USAGE
    assert "model gemini-3.6-flash: available" in output
    assert "model gemini-3.5-flash-lite: PROBLEM: model not found" in output


def test_check_flags_missing_model_and_key(root: Path) -> None:
    code, output = run_check(root, checker(["gemini-3.6-flash"]), fake_sources())
    assert code == EXIT_USAGE
    assert "model gemini-3.5-flash-lite: NOT FOUND" in output
    assert "available models: gemini-3.6-flash" in output

    def no_key(config):
        raise LLMError("GEMINI_API_KEY is not set.")

    code, output = run_check(root, no_key, fake_sources())
    assert code == EXIT_USAGE
    assert "API key: PROBLEM: GEMINI_API_KEY is not set." in output


def test_dry_run_through_the_openai_compatible_client(root: Path) -> None:
    """The real Gemini/Groq client, prompts and parsing, with a scripted HTTP transport."""
    prompts: list[str] = []

    def reply(prompt: str) -> str:
        if "short search queries" in prompt:
            return '{"queries": ["agentic AI"]}'
        if "Pick 1 or 2 credible" in prompt:
            return '<result>{"developments": [{"summary": "A vendor shipped an agent runtime.", "why_it_matters": "w", "article_ids": ["A1"]}]}</result>'
        if "Edit this LinkedIn draft" in prompt:
            return "```json\n" + json.dumps(editor_reply()) + "\n```"
        if "Write today's LinkedIn post" in prompt:
            return f"<post>{CLEAN_POST}</post>"
        raise AssertionError(f"unexpected prompt: {prompt[:80]}")

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][1]["content"]
        prompts.append(prompt)
        message = {"role": "assistant", "content": reply(prompt)}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            },
        )

    def factory(config: Config) -> OpenAICompatLLM:
        http = httpx.Client(transport=httpx.MockTransport(handler))
        return OpenAICompatLLM(config, "https://llm.example/v1", "k", http=http)

    out = io.StringIO()
    services = Services(llm_factory=factory, sources_factory=lambda c, r: fake_sources())
    code = run(
        parse_args(["--root", str(root), "--date", "2026-10-28", "--dry-run", "--force"]),
        services,
        out,
    )
    report = out.getvalue()
    assert code == EXIT_OK, report
    assert "PASSED on attempt 1" in report
    assert "A vendor shipped an agent runtime." in report
    assert "calls=4" in report
    assert "Reply with only one JSON object" in prompts[-1]  # editor got the schema instruction
