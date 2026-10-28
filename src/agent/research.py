"""Find 1-2 recent, credible developments for a skill topic using Claude with web search."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from agent.config import Config
from agent.llm import LLM, LLMError
from agent.logs import log_event
from agent.state import Source
from agent.templates import render

logger = logging.getLogger(__name__)

MAX_DEVELOPMENTS = 2


@dataclass
class Development:
    """One recent development the post may reference."""

    summary: str
    date: str
    why_it_matters: str
    sources: list[Source] = field(default_factory=list)


@dataclass
class Research:
    """Research results for one post. Empty means: write without news references."""

    developments: list[Development] = field(default_factory=list)

    @property
    def sources(self) -> list[Source]:
        seen: dict[str, Source] = {}
        for dev in self.developments:
            for source in dev.sources:
                seen.setdefault(source.url, source)
        return list(seen.values())

    def notes(self) -> str:
        """Research formatted for writer and editor prompts."""
        if not self.developments:
            return (
                "No solid recent developments were found for this topic. Write the post without "
                "referencing recent news, launches or announcements."
            )
        lines = []
        for i, dev in enumerate(self.developments, 1):
            refs = "; ".join(f"{s.publisher or s.title or 'source'} <{s.url}>" for s in dev.sources)
            lines.append(
                f"{i}. ({dev.date}) {dev.summary}\n   Why it matters: {dev.why_it_matters}\n"
                f"   Sources: {refs}"
            )
        return "\n".join(lines)


def parse_date(value: str) -> date | None:
    """Parse 'YYYY-MM-DD' (or 'YYYY-MM', read as the 1st). Returns None if unparseable."""
    value = (value or "").strip()
    for candidate in (value[:10], value[:7] + "-01"):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def parse_sources(raw: Any) -> list[Source]:
    """Keep only sources with an http(s) URL."""
    sources = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and str(item.get("url", "")).startswith(("http://", "https://")):
            sources.append(Source.from_dict(item))
    return sources


def parse_developments(data: Any, today: date, lookback_days: int) -> list[Development]:
    """Validate the model's JSON: each item needs a source and a date inside the window."""
    since = today - timedelta(days=lookback_days)
    items = data.get("developments", []) if isinstance(data, dict) else []
    kept: list[Development] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sources = parse_sources(item.get("sources"))
        when = parse_date(str(item.get("date", "")))
        summary = str(item.get("summary", "")).strip()
        if not summary or not sources:
            log_event(logger, "research.drop", reason="no summary or source")
            continue
        if when is None or not since <= when <= today + timedelta(days=1):
            log_event(logger, "research.drop", reason="outside date window", date=item.get("date"))
            continue
        kept.append(
            Development(
                summary=summary,
                date=when.isoformat(),
                why_it_matters=str(item.get("why_it_matters", "")).strip(),
                sources=sources,
            )
        )
    return kept[:MAX_DEVELOPMENTS]


def research_topic(llm: LLM, config: Config, title: str, angle: str, today: date) -> Research:
    """Search the web for recent developments on a topic. Never raises on bad results."""
    since = today - timedelta(days=config.research_lookback_days)
    prompt = render(
        "research",
        today=today.isoformat(),
        since=since.isoformat(),
        lookback_days=config.research_lookback_days,
        title=title,
        angle=angle,
    )
    try:
        data, _ = llm.complete_json(
            label="research",
            model=config.research_model,
            system=[render("system_researcher")],
            prompt=prompt,
            effort=config.research_effort,
            web_search_max_uses=config.research_max_searches,
        )
    except LLMError as exc:
        log_event(logger, "research.failed", logging.WARNING, error=exc)
        return Research()
    research = Research(parse_developments(data, today, config.research_lookback_days))
    log_event(logger, "research.done", developments=len(research.developments))
    return research
