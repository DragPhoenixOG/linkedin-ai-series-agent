"""Read and write the post history (`state/history.json`) and the post archive (`posts/`)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from agent.textutil import slugify


@dataclass
class Source:
    """A web page a post or story relies on."""

    url: str
    title: str = ""
    publisher: str = ""
    official: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Source:
        return cls(
            url=str(data.get("url", "")),
            title=str(data.get("title", "") or ""),
            publisher=str(data.get("publisher", "") or ""),
            official=bool(data.get("official", False)),
        )


@dataclass
class NewsStory:
    """A verified news story a news post reacts to."""

    key: str
    title: str
    summary: str
    date: str
    sources: list[Source] = field(default_factory=list)
    angle: str = ""
    details: list[str] = field(default_factory=list)  # specifics the articles state

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NewsStory:
        return cls(
            key=data["key"],
            title=data["title"],
            summary=data.get("summary", ""),
            date=data.get("date", ""),
            sources=[Source.from_dict(s) for s in data.get("sources", [])],
            angle=data.get("angle", ""),
            details=[str(d) for d in data.get("details", [])],
        )


@dataclass
class PostRecord:
    """Everything we keep about one published post."""

    date: str
    post_type: str  # "skill" | "news"
    tone: str  # "straight" | "humorous"
    format: str  # format id from mix.FORMATS
    text: str
    slug: str
    topic_id: str | None = None
    topic_title: str | None = None
    news_story: NewsStory | None = None
    sources: list[Source] = field(default_factory=list)
    editor_score: int | None = None
    humour_score: int | None = None
    attempts: int = 1
    urn: str | None = None
    linkedin_url: str | None = None

    @property
    def posted_on(self) -> date:
        return date.fromisoformat(self.date)

    @property
    def title(self) -> str:
        if self.news_story:
            return self.news_story.title
        return self.topic_title or self.slug

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostRecord:
        story = data.get("news_story")
        return cls(
            date=data["date"],
            post_type=data["post_type"],
            tone=data["tone"],
            format=data["format"],
            text=data["text"],
            slug=data["slug"],
            topic_id=data.get("topic_id"),
            topic_title=data.get("topic_title"),
            news_story=NewsStory.from_dict(story) if story else None,
            sources=[Source.from_dict(s) for s in data.get("sources", [])],
            editor_score=data.get("editor_score"),
            humour_score=data.get("humour_score"),
            attempts=data.get("attempts", 1),
            urn=data.get("urn"),
            linkedin_url=data.get("linkedin_url"),
        )


@dataclass
class PendingDraft:
    """A finished post waiting out the review window (`review_window_hours` > 0)."""

    record: PostRecord
    created_at: str  # ISO datetime, UTC
    publish_after: str  # ISO datetime, UTC
    issue_number: int | None = None
    research_notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingDraft:
        return cls(
            record=PostRecord.from_dict(data["record"]),
            created_at=data["created_at"],
            publish_after=data["publish_after"],
            issue_number=data.get("issue_number"),
            research_notes=data.get("research_notes", ""),
        )


@dataclass
class History:
    """All published posts, oldest first, plus at most one draft awaiting review."""

    posts: list[PostRecord] = field(default_factory=list)
    pending: PendingDraft | None = None

    def last_post(self) -> PostRecord | None:
        """The most recently published post, if any."""
        return max(self.posts, key=lambda p: p.date, default=None)

    def recent(self, n: int) -> list[PostRecord]:
        """Up to `n` posts, most recent first."""
        ordered = sorted(self.posts, key=lambda p: p.date, reverse=True)
        return ordered[:n]

    def recent_texts(self, n: int) -> list[str]:
        """Text of up to `n` posts, most recent first."""
        return [p.text for p in self.recent(n)]

    def news_stories(self) -> list[NewsStory]:
        """Every news story covered so far."""
        return [p.news_story for p in self.posts if p.news_story]


def load_history(path: Path) -> History:
    """Load history from JSON. A missing file means no posts yet."""
    if not path.exists():
        return History()
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    pending = data.get("pending")
    return History(
        posts=[PostRecord.from_dict(p) for p in data.get("posts", [])],
        pending=PendingDraft.from_dict(pending) if pending else None,
    )


def save_history(history: History, path: Path) -> None:
    """Write history as stable, human-readable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "posts": [asdict(p) for p in sorted(history.posts, key=lambda p: p.date)]
    }
    if history.pending:
        payload["pending"] = asdict(history.pending)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def make_slug(record_title: str) -> str:
    """Slug used for archive file names and commit messages."""
    return slugify(record_title, max_len=50)


def archive_post(record: PostRecord, posts_dir: Path) -> Path:
    """Write `posts/YYYY-MM-DD-<slug>.md` for a published post and return its path."""
    posts_dir.mkdir(parents=True, exist_ok=True)
    path = posts_dir / f"{record.date}-{record.slug}.md"
    lines = [
        "---",
        f"date: {record.date}",
        f"type: {record.post_type}",
        f"tone: {record.tone}",
        f"format: {record.format}",
        f"title: {json.dumps(record.title, ensure_ascii=False)}",
    ]
    if record.linkedin_url:
        lines.append(f"linkedin_url: {record.linkedin_url}")
    lines += ["---", "", record.text.strip(), ""]
    if record.sources:
        lines += [
            "<!-- sources",
            *[f"{s.title or s.publisher}: {s.url}" for s in record.sources],
            "-->",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
