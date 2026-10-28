"""Topic selection, backlog persistence and auto-extension."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from agent.config import Config
from agent.llm import LLMError
from agent.topics import (
    BacklogError,
    Topic,
    ensure_backlog,
    load_topics,
    mark_posted,
    merge_proposals,
    next_topic,
    pending_topics,
    propose_topics,
    save_topics,
)
from fakes import FakeLLM

REPO = Path(__file__).resolve().parents[1]


def topic(i: int, status: str = "pending", title: str | None = None) -> Topic:
    return Topic(id=f"t{i}", title=title or f"Topic number {i}", angle="angle", status=status)


def test_seed_backlog_loads_in_order() -> None:
    topics = load_topics(REPO / "content" / "topics.yaml")
    assert len(topics) == 9
    assert topics[0].id == "generative-to-agentic-ai"
    assert topics[-1].title == "From prototype to production"
    assert all(t.status == "pending" for t in topics)
    assert "\n" not in topics[1].angle


def test_save_and_reload_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    shutil.copy(REPO / "content" / "topics.yaml", path)
    topics = load_topics(path)
    mark_posted(topics, "multi-agent-systems", date(2026, 10, 28), "https://linkedin.example/1")
    save_topics(topics, path)
    reloaded = load_topics(path)
    assert reloaded == topics
    assert path.read_text().startswith("# Topic backlog")
    assert reloaded[1].posted_on == "2026-10-28"


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    path = tmp_path / "topics.yaml"
    path.write_text("topics:\n  - {id: a, title: A, angle: x}\n  - {id: a, title: B, angle: y}\n")
    with pytest.raises(ValueError):
        load_topics(path)


def test_next_topic_is_first_pending() -> None:
    topics = [topic(1, "posted"), topic(2), topic(3)]
    assert next_topic(topics).id == "t2"


def test_next_topic_override_by_id() -> None:
    topics = [topic(1), topic(2), topic(3)]
    assert next_topic(topics, "t3").id == "t3"
    with pytest.raises(KeyError):
        next_topic(topics, "missing")


def test_next_topic_none_when_backlog_empty() -> None:
    assert next_topic([topic(1, "posted")]) is None


def test_merge_skips_near_duplicates_and_assigns_unique_ids() -> None:
    topics = [Topic(id="mcp", title="Model Context Protocol (MCP)", angle="a")]
    added = merge_proposals(
        topics,
        [
            {"title": "The Model Context Protocol", "angle": "dupe"},
            {"title": "Agent memory design", "angle": "What to remember."},
            {"title": "Agent memory design", "angle": "exact repeat"},
            {"title": "", "angle": "no title"},
            {"title": "Evals in CI", "angle": ""},
        ],
        covered_titles=["Retries and idempotency"],
    )
    assert [t.title for t in added] == ["Agent memory design"]
    assert added[0].id == "agent-memory-design"
    assert added[0].source == "auto"


def test_merge_skips_titles_already_posted() -> None:
    added = merge_proposals(
        [], [{"title": "Retries and idempotency", "angle": "x"}], ["Retries and idempotency"]
    )
    assert added == []


def test_merge_suffixes_colliding_ids() -> None:
    topics = [Topic(id="agent-memory", title="Something else entirely", angle="a")]
    added = merge_proposals(topics, [{"title": "Agent memory", "angle": "b"}])
    assert added[0].id == "agent-memory-2"


def test_backlog_not_extended_when_enough_pending() -> None:
    topics = [topic(1), topic(2), topic(3)]
    calls = []
    assert (
        ensure_backlog(topics, enabled=True, min_pending=3, propose=lambda: calls.append(1) or [])
        == []
    )
    assert calls == []


def test_backlog_extends_when_low() -> None:
    topics = [topic(1, "posted"), topic(2)]
    proposals = [{"title": f"Fresh idea {w}", "angle": "x"} for w in ("alpha", "beta", "gamma")]
    added = ensure_backlog(topics, enabled=True, min_pending=3, propose=lambda: proposals)
    assert len(added) == 3
    assert len(pending_topics(topics)) == 4


def test_backlog_retries_after_proposal_failure() -> None:
    topics: list[Topic] = []
    responses = iter([LLMError("boom"), [{"title": "Agent memory", "angle": "x"}]])

    def propose() -> list[dict]:
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return item

    added = ensure_backlog(topics, enabled=True, min_pending=3, propose=propose)
    assert [t.title for t in added] == ["Agent memory"]


def test_empty_backlog_fails_loudly_when_extension_fails() -> None:
    def propose() -> list[dict]:
        raise LLMError("API down")

    with pytest.raises(BacklogError):
        ensure_backlog([topic(1, "posted")], enabled=True, min_pending=3, propose=propose)


def test_empty_backlog_fails_loudly_when_auto_extend_disabled() -> None:
    with pytest.raises(BacklogError, match="auto_extend_topics is false"):
        ensure_backlog([], enabled=False, min_pending=3, propose=lambda: [])


def test_low_backlog_with_extension_disabled_keeps_going() -> None:
    topics = [topic(1)]
    assert ensure_backlog(topics, enabled=False, min_pending=3, propose=lambda: []) == []


def test_propose_topics_uses_structured_output() -> None:
    llm = FakeLLM(
        {
            "topics.propose": {
                "topics": [{"title": "Agent memory", "angle": "x", "kind": "evergreen"}]
            }
        }
    )
    proposals = propose_topics(
        llm, Config(), [topic(1)], ["Posted thing"], "Some research note", date(2026, 10, 28)
    )
    assert proposals == [{"title": "Agent memory", "angle": "x", "kind": "evergreen"}]
    call = llm.calls[0]
    assert call["json_schema"] is not None
    assert "Some research note" in call["prompt"]
    assert "Posted thing" in call["prompt"]
