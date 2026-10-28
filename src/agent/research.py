"""Find 1-2 recent, credible developments for a skill topic from free sources.

1. The model suggests a few search queries for the topic.
2. Hacker News search and the RSS feeds supply recent articles; the best few are fetched.
3. The model picks 1-2 developments, citing articles by id. Only those articles become sources,
   and their dates come from the feeds, not the model, so a post can't cite something made up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from agent.config import Config
from agent.llm import LLM, LLMAuthError, LLMError
from agent.logs import log_event
from agent.sources import (
    Article,
    SourceClient,
    by_ids,
    dedupe,
    format_articles,
    is_relevant,
    relevance,
)
from agent.state import Source
from agent.templates import render

logger = logging.getLogger(__name__)

MAX_DEVELOPMENTS = 2
MAX_QUERIES = 3


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


def to_source(article: Article) -> Source:
    return Source(
        url=article.url, title=article.title, publisher=article.source, official=article.official
    )


def suggest_queries(llm: LLM, config: Config, title: str, angle: str) -> list[str]:
    """Up to 3 short search queries for the topic (falls back to the title)."""
    try:
        data, _ = llm.complete_json(
            label="research.queries",
            model=config.research_model,
            system=[render("system_researcher")],
            prompt=render("research_queries", title=title, angle=angle),
            effort="low",
            max_tokens=4000,
            json_schema=QUERY_SCHEMA,
        )
    except LLMAuthError:
        raise
    except LLMError as exc:
        log_event(logger, "research.queries_failed", logging.WARNING, error=exc)
        return [title]
    queries = [str(q).strip() for q in (data or {}).get("queries", []) if str(q).strip()]
    return queries[:MAX_QUERIES] or [title]


def gather_articles(
    sources: SourceClient, queries: list[str], title: str, since: date, today: date, limit: int
) -> list[Article]:
    """Recent articles for the queries: Hacker News hits plus relevant feed items, best first."""
    phrases = [title, *queries]
    # Feed items first: when the same article also turns up on Hacker News, keep the feed copy,
    # which has a summary and the right "official" flag.
    found = [a for a in sources.feed_items(since, today) if is_relevant(a, phrases)]
    for query in queries:
        found += sources.search_hn(query, since, sources.settings.hn_min_points_research)
    ranked = sorted(
        dedupe(found),
        key=lambda a: (relevance(a, phrases), a.official, a.points, a.published),
        reverse=True,
    )
    return ranked[:limit]


def parse_developments(
    data: Any, articles: list[Article], today: date, lookback_days: int
) -> list[Development]:
    """Keep developments that cite known articles dated inside the window."""
    since = today - timedelta(days=lookback_days)
    items = data.get("developments", []) if isinstance(data, dict) else []
    kept: list[Development] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cited = [
            a for a in by_ids(articles, item.get("article_ids")) if since <= a.published <= today
        ]
        summary = str(item.get("summary", "")).strip()
        if not summary or not cited:
            log_event(logger, "research.drop", reason="no summary or no valid cited article")
            continue
        kept.append(
            Development(
                summary=summary,
                date=max(a.published for a in cited).isoformat(),
                why_it_matters=str(item.get("why_it_matters", "")).strip(),
                sources=[to_source(a) for a in cited],
            )
        )
    return kept[:MAX_DEVELOPMENTS]


def research_topic(
    llm: LLM, config: Config, sources: SourceClient, title: str, angle: str, today: date
) -> Research:
    """Recent developments on a topic. On a failure the post goes without, except a bad API key."""
    since = today - timedelta(days=config.research_lookback_days)
    queries = suggest_queries(llm, config, title, angle)
    articles = gather_articles(sources, queries, title, since, today, config.max_articles_to_read)
    log_event(logger, "research.articles", queries=len(queries), articles=len(articles))
    if not articles:
        return Research()
    sources.read(articles)
    prompt = render(
        "research",
        today=today.isoformat(),
        since=since.isoformat(),
        title=title,
        angle=angle,
        articles=format_articles(articles),
    )
    try:
        data, _ = llm.complete_json(
            label="research",
            model=config.research_model,
            system=[render("system_researcher")],
            prompt=prompt,
            effort=config.research_effort,
        )
    except LLMAuthError:
        raise
    except LLMError as exc:
        log_event(logger, "research.failed", logging.WARNING, error=exc)
        return Research()
    research = Research(parse_developments(data, articles, today, config.research_lookback_days))
    log_event(logger, "research.done", developments=len(research.developments))
    return research


QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
    "additionalProperties": False,
}
