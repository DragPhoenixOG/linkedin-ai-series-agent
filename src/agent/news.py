"""Find, score, verify and pick one recent news story worth reacting to (CLAUDE.md section 2a).

1. Gather: headlines from the last N days, from the RSS feeds and well-discussed Hacker News
   stories (free, no keys).
2. Scan: the model groups headlines into 5-10 candidate stories, citing headline ids.
3. Filter: drop unconfirmed stories, ones without enough sourcing (2 sites, or 1 official), and
   any story already covered (ever).
4. Score: relevance, how strong an angle Ashish has, freshness, significance, and a penalty for
   overlapping with the last 10 posts.
5. Verify: the cited articles are fetched and the model checks each key fact against their text.
   The first candidate that holds up wins. If none does, the caller falls back to a skill post.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from agent.config import Config
from agent.llm import LLM, LLMAuthError, LLMError
from agent.logs import log_event
from agent.research import to_source
from agent.sources import Article, SourceClient, by_ids, dedupe, format_articles
from agent.state import History, NewsStory, Source
from agent.templates import render
from agent.textutil import domain_of, jaccard, slugify

logger = logging.getLogger(__name__)

SAME_STORY = 0.5  # title similarity treated as the same story
RECENT_OVERLAP = 0.3  # title similarity to a recent post that earns a penalty
MAX_VERIFY = 2


@dataclass
class NewsCandidate:
    """A story found by the scan, before verification."""

    title: str
    summary: str
    date: str
    articles: list[Article] = field(default_factory=list)
    significance: str = "normal"  # "major" | "normal"
    relevance: int = 3  # 1-5: fit with the series theme
    angle_strength: int = 3  # 1-5: how much Ashish has a real take on it
    angle: str = ""
    confirmed: bool = True
    key_facts: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return slugify(self.title, max_len=80)

    @property
    def sources(self) -> list[Source]:
        return [to_source(a) for a in self.articles]


def _int_1_to_5(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 1


def gather_headlines(
    sources: SourceClient, today: date, lookback_days: int, limit: int = 60
) -> list[Article]:
    """Recent feed items plus well-discussed Hacker News stories, newest first."""
    since = today - timedelta(days=lookback_days)
    items = list(sources.feed_items(since, today))
    for query in sources.settings.news_queries:
        items += sources.search_hn(query, since, sources.settings.hn_min_points_news)
    ranked = sorted(dedupe(items), key=lambda a: (a.published, a.official, a.points), reverse=True)
    return ranked[:limit]


def parse_candidates(data: Any, headlines: list[Article]) -> list[NewsCandidate]:
    """Turn the scan's JSON into candidates; each must cite at least one known headline."""
    items = data.get("candidates", []) if isinstance(data, dict) else []
    out: list[NewsCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        articles = by_ids(headlines, item.get("headline_ids"))
        title = str(item.get("title", "")).strip()
        if not title or not articles:
            continue
        out.append(
            NewsCandidate(
                title=title,
                summary=str(item.get("summary", "")).strip(),
                date=min(a.published for a in articles).isoformat(),
                articles=articles,
                significance="major" if item.get("significance") == "major" else "normal",
                relevance=_int_1_to_5(item.get("relevance")),
                angle_strength=_int_1_to_5(item.get("angle_strength")),
                angle=str(item.get("angle", "")).strip(),
                confirmed=item.get("confirmed") is True,
                key_facts=[str(f) for f in item.get("key_facts", []) if str(f).strip()],
            )
        )
    return out


def has_enough_sources(sources: list[Source]) -> bool:
    """At least 2 sources on different domains, or 1 official source."""
    return any(s.official for s in sources) or len({domain_of(s.url) for s in sources}) >= 2


def is_already_covered(candidate: NewsCandidate, stories: list[NewsStory]) -> bool:
    """True if this looks like a story we have already posted about (at any time)."""
    urls = {s.url.rstrip("/") for s in candidate.sources}
    for story in stories:
        if candidate.key == story.key or jaccard(candidate.title, story.title) >= SAME_STORY:
            return True
        if urls & {s.url.rstrip("/") for s in story.sources}:
            return True
    return False


def score(
    candidate: NewsCandidate, today: date, lookback_days: int, recent_titles: list[str]
) -> float:
    """Higher is better. Used only to order candidates that already passed the filters."""
    age = (today - date.fromisoformat(candidate.date)).days
    freshness = max(0.0, (lookback_days - age) / lookback_days) * 3
    total = candidate.relevance * 2 + candidate.angle_strength * 2 + freshness
    if candidate.significance == "major":
        total += 2
    if any(jaccard(candidate.title, t) >= RECENT_OVERLAP for t in recent_titles):
        total -= 4
    return total


def rank_candidates(
    candidates: list[NewsCandidate], history: History, config: Config, today: date
) -> list[NewsCandidate]:
    """Apply the hard filters, then sort by score (best first)."""
    covered = history.news_stories()
    recent = history.recent(config.news_recent_window)
    recent_titles = [p.title for p in recent] + [p.topic_title or "" for p in recent]
    kept = []
    for c in candidates:
        if not c.confirmed:
            reason = "unconfirmed"
        elif not has_enough_sources(c.sources):
            reason = "not enough sources"
        elif is_already_covered(c, covered):
            reason = "already covered"
        else:
            kept.append(c)
            continue
        log_event(logger, "news.drop", title=c.title[:60], reason=reason)
    return sorted(
        kept,
        key=lambda c: score(c, today, config.news_lookback_days, recent_titles),
        reverse=True,
    )


def scan_news(
    llm: LLM, config: Config, sources: SourceClient, today: date, history: History
) -> list[NewsCandidate]:
    """Group recent headlines into candidate stories."""
    headlines = gather_headlines(
        sources, today, config.news_lookback_days, config.news_max_headlines
    )
    log_event(logger, "news.headlines", count=len(headlines))
    if not headlines:
        return []
    covered = "\n".join(f"- {s.title} ({s.date})" for s in history.news_stories()) or "- (none)"
    prompt = render(
        "news_scan",
        today=today.isoformat(),
        lookback_days=config.news_lookback_days,
        covered=covered,
        headlines=format_articles(headlines, with_text=False),
    )
    data, _ = llm.complete_json(
        label="news.scan",
        model=config.research_model,
        system=[render("system_researcher")],
        prompt=prompt,
        effort=config.research_effort,
    )
    candidates = parse_candidates(data, headlines)
    log_event(logger, "news.scan", candidates=len(candidates))
    return candidates


def verify_candidate(
    llm: LLM, config: Config, sources: SourceClient, candidate: NewsCandidate, today: date
) -> NewsStory | None:
    """Fetch the story's articles and check its key facts against their text."""
    articles = sources.read(candidate.articles[:4])
    facts = candidate.key_facts or [candidate.summary]
    prompt = render(
        "news_verify",
        today=today.isoformat(),
        title=candidate.title,
        date=candidate.date,
        summary=candidate.summary,
        facts="\n".join(f"- {f}" for f in facts),
        articles=format_articles(articles),
    )
    data, _ = llm.complete_json(
        label="news.verify",
        model=config.research_model,
        system=[render("system_researcher")],
        prompt=prompt,
        effort=config.research_effort,
    )
    if not isinstance(data, dict):
        return None
    checked = [f for f in data.get("facts", []) if isinstance(f, dict)]
    supporting = by_ids(articles, [i for f in checked for i in f.get("article_ids", [])])
    ok = (
        data.get("status") == "confirmed"
        and data.get("verified") is True
        and bool(checked)
        and all(f.get("supported") is True for f in checked)
        and has_enough_sources([to_source(a) for a in supporting])
    )
    log_event(
        logger, "news.verify", title=candidate.title[:60], verified=ok, status=data.get("status")
    )
    if not ok:
        return None
    official_first = sorted(supporting, key=lambda a: not a.official)
    return NewsStory(
        key=candidate.key,
        title=candidate.title,
        summary=str(data.get("summary") or candidate.summary).strip(),
        date=candidate.date,
        sources=[to_source(a) for a in official_first],
        angle=candidate.angle,
    )


def pick_story(
    llm: LLM,
    config: Config,
    sources: SourceClient,
    today: date,
    history: History,
    require_major: bool = False,
) -> NewsStory | None:
    """Gather, scan, filter, score and verify. Returns None if no story qualifies.

    Only a rejected API key raises (`LLMAuthError`); any other failure means no news today.
    """
    try:
        ranked = rank_candidates(
            scan_news(llm, config, sources, today, history), history, config, today
        )
        if require_major:
            ranked = [c for c in ranked if c.significance == "major"]
        for candidate in ranked[:MAX_VERIFY]:
            story = verify_candidate(llm, config, sources, candidate, today)
            if story:
                return story
    except LLMAuthError:
        raise
    except LLMError as exc:
        log_event(logger, "news.failed", logging.WARNING, error=exc)
    return None


def story_notes(story: NewsStory) -> str:
    """The verified story formatted for writer and editor prompts."""
    refs = "\n".join(
        f"   - {s.publisher or s.title or 'source'}{' (official)' if s.official else ''} <{s.url}>"
        for s in story.sources
    )
    return f"Verified story ({story.date}): {story.title}\n{story.summary}\n   Sources:\n{refs}"
