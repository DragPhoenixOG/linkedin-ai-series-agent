"""Topic backlog: pick the next skill topic and keep the backlog from running dry.

The series is open-ended. When fewer than `min_pending_topics` topics are pending, Claude
proposes new ones in the same theme and they are appended. If the backlog would still be empty
after that, the run fails loudly instead of silently skipping posts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from agent.config import Config
from agent.llm import LLM, LLMError
from agent.logs import log_event
from agent.templates import render
from agent.textutil import jaccard, slugify

logger = logging.getLogger(__name__)

PENDING = "pending"
POSTED = "posted"
NEAR_DUPLICATE = 0.6  # title similarity at or above this counts as a repeat

TOPICS_HEADER = """\
# Topic backlog for skill posts. The agent takes the first `pending` topic in order.
# The series is open-ended: when fewer than `min_pending_topics` remain, the agent asks
# Claude for new ones and appends them here with `source: auto`.
#
# To add a topic by hand, append an entry with a unique id and `status: pending`.
# Fields: id, title, angle, status (pending | posted), posted_on, linkedin_url, source.

"""


class BacklogError(RuntimeError):
    """No topic is available and the backlog could not be refilled."""


@dataclass
class Topic:
    """One skill topic in the backlog."""

    id: str
    title: str
    angle: str
    status: str = PENDING
    posted_on: str | None = None
    linkedin_url: str | None = None
    source: str = "seed"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Topic:
        posted_on = data.get("posted_on")
        return cls(
            id=str(data["id"]),
            title=str(data["title"]).strip(),
            angle=" ".join(str(data.get("angle", "")).split()),
            status=data.get("status", PENDING),
            posted_on=posted_on.isoformat() if isinstance(posted_on, date) else posted_on,
            linkedin_url=data.get("linkedin_url"),
            source=data.get("source", "seed"),
        )


def load_topics(path: Path) -> list[Topic]:
    """Load the backlog from YAML, keeping file order."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    topics = [Topic.from_dict(t) for t in data.get("topics", [])]
    ids = [t.id for t in topics]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate topic ids in {path}")
    return topics


def save_topics(topics: list[Topic], path: Path) -> None:
    """Write the backlog back to YAML with the explanatory header."""
    body = yaml.safe_dump(
        {"topics": [asdict(t) for t in topics]}, sort_keys=False, allow_unicode=True, width=100
    )
    path.write_text(TOPICS_HEADER + body, encoding="utf-8")


def pending_topics(topics: Iterable[Topic]) -> list[Topic]:
    """Pending topics in backlog order."""
    return [t for t in topics if t.status == PENDING]


def next_topic(topics: list[Topic], topic_id: str | None = None) -> Topic | None:
    """The topic to write about: `topic_id` if given, else the first pending one."""
    if topic_id:
        for topic in topics:
            if topic.id == topic_id:
                return topic
        raise KeyError(f"Unknown topic id: {topic_id}")
    pending = pending_topics(topics)
    return pending[0] if pending else None


def mark_posted(
    topics: list[Topic], topic_id: str, posted_on: date, linkedin_url: str | None
) -> None:
    """Record that a topic has been published."""
    for topic in topics:
        if topic.id == topic_id:
            topic.status = POSTED
            topic.posted_on = posted_on.isoformat()
            topic.linkedin_url = linkedin_url
            return
    raise KeyError(f"Unknown topic id: {topic_id}")


def merge_proposals(
    topics: list[Topic], proposals: Iterable[dict[str, Any]], covered_titles: Iterable[str] = ()
) -> list[Topic]:
    """Append proposed topics that aren't repeats or near-repeats. Returns the ones added."""
    known_titles = [t.title for t in topics] + list(covered_titles)
    known_ids = {t.id for t in topics}
    added: list[Topic] = []
    for proposal in proposals:
        title = " ".join(str(proposal.get("title", "")).split())
        angle = " ".join(str(proposal.get("angle", "")).split())
        if not title or not angle:
            continue
        if any(jaccard(title, known) >= NEAR_DUPLICATE for known in known_titles):
            log_event(logger, "topics.skip_duplicate", title=title)
            continue
        base = slugify(title, max_len=50)
        topic_id, n = base, 2
        while topic_id in known_ids:
            topic_id, n = f"{base}-{n}", n + 1
        topic = Topic(id=topic_id, title=title, angle=angle, source="auto")
        topics.append(topic)
        added.append(topic)
        known_titles.append(title)
        known_ids.add(topic_id)
    return added


def needs_extension(topics: list[Topic], min_pending: int) -> bool:
    """True when fewer than `min_pending` topics are pending."""
    return len(pending_topics(topics)) < min_pending


def ensure_backlog(
    topics: list[Topic],
    *,
    enabled: bool,
    min_pending: int,
    propose: Callable[[], list[dict[str, Any]]],
    covered_titles: Iterable[str] = (),
    attempts: int = 2,
) -> list[Topic]:
    """Top up the backlog when it runs low. Returns the topics added.

    Raises `BacklogError` if no topic is pending afterwards, so the run fails visibly (and
    opens a needs-attention issue) rather than silently not posting.
    """
    if not needs_extension(topics, min_pending):
        return []
    covered = list(covered_titles)
    added: list[Topic] = []
    if enabled:
        for attempt in range(1, attempts + 1):
            try:
                added += merge_proposals(topics, propose(), covered)
            except LLMError as exc:
                log_event(
                    logger, "topics.propose_failed", logging.WARNING, attempt=attempt, error=exc
                )
            if not needs_extension(topics, min_pending):
                break
    if not pending_topics(topics):
        hint = "" if enabled else " auto_extend_topics is false;"
        raise BacklogError(f"No pending topics left.{hint} Add topics to content/topics.yaml.")
    log_event(logger, "topics.extended", added=len(added), pending=len(pending_topics(topics)))
    return added


TOPIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "angle": {"type": "string"},
                    "kind": {"type": "string", "enum": ["evergreen", "timely"]},
                },
                "required": ["title", "angle", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["topics"],
    "additionalProperties": False,
}


def propose_topics(
    llm: LLM,
    config: Config,
    topics: list[Topic],
    covered_titles: Iterable[str],
    research_notes: str,
    today: date,
) -> list[dict[str, Any]]:
    """Ask Claude for `new_topics_per_extension` new topics in the series theme."""
    existing = "\n".join(f"- {t.title} ({t.status})" for t in topics) or "- (none)"
    covered = "\n".join(f"- {t}" for t in covered_titles) or "- (none)"
    prompt = render(
        "propose_topics",
        count=config.new_topics_per_extension,
        today=today.isoformat(),
        existing_topics=existing,
        covered_posts=covered,
        research_notes=research_notes.strip() or "(no research from this run)",
    )
    data, _ = llm.complete_json(
        label="topics.propose",
        model=config.research_model,
        system=[render("system_planner")],
        prompt=prompt,
        effort=config.research_effort,
        max_tokens=4000,
        json_schema=TOPIC_SCHEMA,
    )
    proposals = data.get("topics", []) if isinstance(data, dict) else []
    return [p for p in proposals if isinstance(p, dict)]
