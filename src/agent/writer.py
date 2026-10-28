"""Writer pass: Claude drafts the post in the author's voice."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agent.config import Config
from agent.lint import ends_with_question, has_bullets, opening_line
from agent.llm import LLM, LLMError
from agent.state import NewsStory
from agent.templates import fragment, render


@dataclass(frozen=True)
class Voice:
    """The style guide and author facts, included in every writer and editor prompt."""

    style_guide: str
    author_facts: str

    def system_block(self) -> str:
        return f"# Style guide\n\n{self.style_guide}\n\n# Author facts\n\n{self.author_facts}"


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


def extract_post(text: str) -> str:
    """Pull the post out of `<post>` tags (or use the whole reply if the tags are missing)."""
    match = re.search(r"<post>\s*(.*?)\s*</post>", text, re.DOTALL)
    post = match.group(1) if match else text
    post = post.strip()
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
