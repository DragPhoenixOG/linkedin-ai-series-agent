"""History persistence and the post archive."""

from __future__ import annotations

import json
from pathlib import Path

from agent.state import (
    History,
    NewsStory,
    PostRecord,
    Source,
    archive_post,
    load_history,
    save_history,
)


def make_record(day: str, **overrides: object) -> PostRecord:
    data: dict = {
        "date": day,
        "post_type": "skill",
        "tone": "straight",
        "format": "observation",
        "text": f"Post from {day}",
        "slug": f"post-{day}",
        "topic_id": "mcp",
        "topic_title": "Model Context Protocol (MCP)",
    }
    data.update(overrides)
    return PostRecord(**data)


def test_missing_history_file_means_no_posts(tmp_path: Path) -> None:
    history = load_history(tmp_path / "nope.json")
    assert history.posts == []
    assert history.last_post() is None


def test_round_trip_keeps_nested_story(tmp_path: Path) -> None:
    story = NewsStory(
        key="acme-agent-launch",
        title="Acme launches agent toolkit",
        summary="Acme released a toolkit.",
        date="2026-10-20",
        sources=[Source(url="https://acme.example/blog", official=True)],
    )
    history = History(posts=[make_record("2026-10-22", post_type="news", news_story=story)])
    path = tmp_path / "state" / "history.json"
    save_history(history, path)
    loaded = load_history(path)
    assert loaded == history
    assert json.loads(path.read_text())["posts"][0]["news_story"]["sources"][0]["official"] is True


def test_recent_is_most_recent_first_regardless_of_file_order() -> None:
    history = History(
        posts=[make_record("2026-10-22"), make_record("2026-10-16"), make_record("2026-10-19")]
    )
    assert [p.date for p in history.recent(2)] == ["2026-10-22", "2026-10-19"]
    assert history.last_post().date == "2026-10-22"
    assert history.recent_texts(1) == ["Post from 2026-10-22"]


def test_news_stories_lists_only_news_posts() -> None:
    story = NewsStory(key="k", title="t", summary="s", date="2026-10-20")
    history = History(
        posts=[make_record("2026-10-19"), make_record("2026-10-22", news_story=story)]
    )
    assert history.news_stories() == [story]


def test_archive_post_writes_markdown(tmp_path: Path) -> None:
    record = make_record(
        "2026-10-22",
        linkedin_url="https://www.linkedin.com/feed/update/urn:li:share:1/",
        sources=[Source(url="https://example.com/a", title="A")],
    )
    path = archive_post(record, tmp_path / "posts")
    assert path.name == "2026-10-22-post-2026-10-22.md"
    content = path.read_text()
    assert "type: skill" in content
    assert "Post from 2026-10-22" in content
    assert "https://example.com/a" in content
