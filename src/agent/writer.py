"""Writer pass: Claude drafts the post in the author's voice."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from agent.config import Config
from agent.lint import ends_with_question, has_bullets, opening_line
from agent.llm import LLM, LLMAuthError, LLMError
from agent.logs import log_event
from agent.state import NewsStory
from agent.templates import fragment, render

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Voice:
    """The style guide and author facts, included in every writer and editor prompt."""

    style_guide: str
    author_facts: str

    def system_block(self) -> str:
        return f"# Style guide\n\n{self.style_guide}\n\n# Author facts\n\n{self.author_facts}"


_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def relative_date(day: date, today: date) -> str:
    """How long before `today` a date was, in the words a post should use."""
    days = (today - day).days
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if day.isocalendar()[:2] == today.isocalendar()[:2]:
        return f"{days} days ago, earlier this week"
    if day.isocalendar()[:2] == (today - timedelta(days=7)).isocalendar()[:2]:
        return f"{days} days ago, last week"
    if days < 31:
        return f"{days} days ago, about {max(2, round(days / 7))} weeks ago"
    months = round(days / 30)
    return f"{days} days ago, about {'a month' if months <= 1 else f'{months} months'} ago"


def describe_dates(research_notes: str, today: date) -> str:
    """Each date in the research, relative to today (weeks start on Monday)."""
    lines = []
    for text in dict.fromkeys(_ISO_DATE.findall(research_notes)):
        try:
            day = date.fromisoformat(text)
        except ValueError:
            continue
        if day <= today:
            lines.append(f"- {text}: {relative_date(day, today)}")
    return "\n".join(lines) or "- No dates in the research."


def load_voice(content_dir: Path) -> Voice:
    """Read `content/style.md` and `content/author.md`."""
    return Voice(
        style_guide=(content_dir / "style.md").read_text(encoding="utf-8").strip(),
        author_facts=(content_dir / "author.md").read_text(encoding="utf-8").strip(),
    )


@dataclass
class PostBrief:
    """Everything the writer and editor need to know about the post to write."""

    post_type: str  # "skill" | "news"
    tone: str  # "straight" | "humorous"
    format_id: str
    format_name: str
    format_description: str
    today: date
    voice: Voice
    research_notes: str
    topic_title: str = ""
    topic_angle: str = ""
    story: NewsStory | None = None
    recent_posts: list[str] = field(default_factory=list)  # most recent first

    @property
    def subject_line(self) -> str:
        if self.story:
            return f"News: {self.story.title} ({self.story.date})"
        return f"Skill: {self.topic_title}"

    def constraints(self) -> list[str]:
        """Limits that depend on recent posts, phrased as instructions."""
        rules = []
        if any(has_bullets(p) for p in self.recent_posts[:2]):
            rules.append("No bullet or numbered lists in this post (a recent post used them).")
        else:
            rules.append(
                "Bullets are allowed (max 4) only if they genuinely help. "
                "Most posts don't need them."
            )
        if self.recent_posts and ends_with_question(self.recent_posts[0]):
            rules.append("Do not end with a question (the previous post did).")
        openings = [opening_line(p) for p in self.recent_posts[:5] if opening_line(p)]
        if openings:
            quoted = "\n".join(f'  - "{o}"' for o in openings)
            rules.append(
                "Your first line must not copy the structure or first words of these recent "
                f"opening lines:\n{quoted}"
            )
        return rules


def format_recent_posts(posts: list[str], limit: int = 5) -> str:
    """Recent posts as numbered blocks for prompts."""
    if not posts:
        return "(none yet: this is the first post)"
    return "\n\n".join(
        f"--- Post {i} (most recent first) ---\n{p.strip()}" for i, p in enumerate(posts[:limit], 1)
    )


def subject_block(brief: PostBrief, config: Config) -> str:
    """The 'what is this post about' section for the writer prompt."""
    if brief.post_type == "news" and brief.story:
        link = brief.story.sources[0].url if brief.story.sources else "(none)"
        return render(
            "subject_news",
            lookback_days=config.news_lookback_days,
            title=brief.story.title,
            date=brief.story.date,
            summary=brief.story.summary,
            angle=brief.story.angle or "(none suggested)",
            link=link,
        )
    return render("subject_skill", title=brief.topic_title, angle=brief.topic_angle)


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*\u2022\u2013]|\d{1,2}[.)])\s+\S")


def tidy_post(text: str) -> str:
    """One blank line between paragraphs (and before the hashtags); list items stay together."""
    out: list[str] = []
    for line in (raw.rstrip() for raw in text.strip().splitlines()):
        if not line.strip():
            if out and out[-1]:
                out.append("")
            continue
        previous = out[-1] if out else ""
        same_list = bool(_LIST_ITEM_RE.match(line) and _LIST_ITEM_RE.match(previous))
        if previous and not same_list:
            out.append("")
        out.append(line)
    return "\n".join(out).strip()


def extract_post(text: str) -> str:
    """Pull the post out of `<post>` tags (or use the whole reply if the tags are missing)."""
    match = re.search(r"<post>\s*(.*?)\s*</post>", text, re.DOTALL)
    post = tidy_post(match.group(1) if match else text)
    if not post:
        raise LLMError("Writer returned an empty post")
    return post


def write_draft(
    llm: LLM,
    config: Config,
    brief: PostBrief,
    feedback: str | None = None,
    previous: str | None = None,
) -> str:
    """Draft the post. With `feedback` and `previous`, revise the previous version instead."""
    revision = ""
    if feedback and previous:
        revision = render("revision", feedback=feedback, previous=previous)
    prompt = render(
        "writer",
        today=brief.today.isoformat(),
        subject=subject_block(brief, config),
        research=brief.research_notes,
        research_dates=describe_dates(brief.research_notes, brief.today),
        format_name=brief.format_name,
        format_description=brief.format_description,
        tone_name=brief.tone,
        tone_guidance=fragment(f"tone_{brief.tone}"),
        min_words=config.min_words,
        max_words=config.max_words,
        max_hashtags=config.max_hashtags,
        constraints="\n".join(f"- {c}" for c in brief.constraints()),
        recent_posts=format_recent_posts(brief.recent_posts),
        revision=revision,
    )
    response = llm.complete(
        label="writer",
        model=config.writer_model,
        system=[brief.voice.system_block(), render("system_writer")],
        prompt=prompt,
        effort=config.writer_effort,
        max_tokens=16000,
    )
    return extract_post(response.text)


FIX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"find": {"type": "string"}, "replace": {"type": "string"}},
                "required": ["find", "replace"],
            },
        }
    },
    "required": ["edits"],
}


def fix_sentences(
    llm: LLM, config: Config, brief: PostBrief, post: str, problems: list[str]
) -> str | None:
    """Rewrite only the sentences with problems, spliced in by code so nothing else changes.

    Returns the edited post, or None if no edit could be applied (the caller then revises the
    whole post instead).
    """
    prompt = render("fix_sentences", post=post, problems="\n".join(f"- {p}" for p in problems))
    try:
        data, _ = llm.complete_json(
            label="writer.fix",
            model=config.writer_model,
            system=[brief.voice.system_block(), render("system_writer")],
            prompt=prompt,
            effort=config.writer_effort,
            max_tokens=4000,
            json_schema=FIX_SCHEMA,
        )
    except LLMAuthError:
        raise
    except LLMError:
        return None
    edits = data.get("edits", []) if isinstance(data, dict) else []
    text, applied = post, 0
    for item in edits:
        if not isinstance(item, dict):
            continue
        find, replace = str(item.get("find", "")).strip(), str(item.get("replace", "")).strip()
        if find and text.count(find) == 1:
            text, applied = text.replace(find, replace), applied + 1
    log_event(logger, "writer.fix", edits=len(edits), applied=applied)
    return tidy_post(re.sub(r"[ \t]{2,}", " ", text)) if applied else None
