"""Free, keyless research sources: RSS/Atom feeds and Hacker News search.

The feed list lives in `content/feeds.yaml`. Every network call fails softly: a dead feed or a
page that won't load is logged and skipped, never fatal. The model only ever sees articles
fetched here, so every fact in a post can be traced to a real URL.
"""

from __future__ import annotations

import contextlib
import email.utils
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from html.parser import HTMLParser
from pathlib import Path

import httpx
import yaml

from agent.logs import log_event
from agent.textutil import domain_of, significant_tokens

logger = logging.getLogger(__name__)

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
USER_AGENT = (
    "linkedin-ai-series-agent/1.0 (+https://github.com/DragPhoenixOG/linkedin-ai-series-agent)"
)
_ATOM = "{http://www.w3.org/2005/Atom}"


# The default for `generic_words` in content/feeds.yaml (an AI and tech series).
DEFAULT_GENERIC_WORDS = frozenset({"ai", "artificial", "intelligence", "tech", "technology"})


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    official: bool = False


@dataclass
class SourceSettings:
    """What `content/feeds.yaml` configures."""

    feeds: list[Feed] = field(default_factory=list)
    official_domains: list[str] = field(default_factory=list)
    news_queries: list[str] = field(default_factory=list)
    hn_min_points_news: int = 100
    hn_min_points_research: int = 10
    # Words in almost every headline on the subject: matching on them says nothing about relevance.
    generic_words: frozenset[str] = DEFAULT_GENERIC_WORDS


@dataclass
class Article:
    """One item from a feed or Hacker News, optionally with its fetched text."""

    title: str
    url: str
    published: date
    source: str
    official: bool = False
    summary: str = ""
    text: str = ""
    points: int = 0

    @property
    def domain(self) -> str:
        return domain_of(self.url)


def load_source_settings(path: Path) -> SourceSettings:
    """Read `content/feeds.yaml`."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    hn = data.get("hacker_news", {}) or {}
    generic = data.get("generic_words", DEFAULT_GENERIC_WORDS)
    if isinstance(generic, str):  # a single word written without list brackets
        generic = [generic]
    return SourceSettings(
        feeds=[
            Feed(f["name"], f["url"], bool(f.get("official", False))) for f in data.get("feeds", [])
        ],
        official_domains=[d.lower() for d in data.get("official_domains", [])],
        news_queries=list(hn.get("news_queries", [])),
        hn_min_points_news=int(hn.get("min_points_news", 100)),
        hn_min_points_research=int(hn.get("min_points_research", 10)),
        generic_words=frozenset(str(w).lower() for w in generic or []),
    )


# --- Parsing ----------------------------------------------------------------------------------


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    try:
        return email.utils.parsedate_to_datetime(value).date()  # RSS: RFC 822
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()  # Atom: ISO 8601
    except ValueError:
        return None


def _clean(text: str | None, limit: int = 500) -> str:
    return " ".join(html_to_text(text or "").split())[:limit]


def parse_feed(xml_text: str, feed: Feed, is_official: bool = False) -> list[Article]:
    """Parse RSS 2.0 or Atom. Items without a title, link or date are skipped."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        log_event(logger, "sources.bad_feed", logging.WARNING, feed=feed.name)
        return []
    articles = []
    for item in root.iter("item"):  # RSS
        link = (item.findtext("link") or "").strip()
        when = _parse_date(
            item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
        )
        title = _clean(item.findtext("title"), 300)
        if title and link and when:
            articles.append(
                Article(
                    title,
                    link,
                    when,
                    feed.name,
                    feed.official or is_official,
                    _clean(item.findtext("description")),
                )
            )
    for entry in root.iter(f"{_ATOM}entry"):  # Atom
        link_el = entry.find(f"{_ATOM}link[@rel='alternate']")
        if link_el is None:
            link_el = entry.find(f"{_ATOM}link")
        link = (link_el.get("href", "") if link_el is not None else "").strip()
        when = _parse_date(entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated"))
        title = _clean(entry.findtext(f"{_ATOM}title"), 300)
        summary = entry.findtext(f"{_ATOM}summary") or entry.findtext(f"{_ATOM}content")
        if title and link and when:
            articles.append(
                Article(title, link, when, feed.name, feed.official or is_official, _clean(summary))
            )
    return articles


_SKIP_TAGS = frozenset(
    ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"]
)
_BLOCK_TAGS = frozenset(
    ["p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "br", "div", "section", "article"]
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Readable text from HTML: drops scripts, navigation and markup, keeps paragraphs."""
    if "<" not in html:
        return html.strip()
    parser = _TextExtractor()
    with contextlib.suppress(Exception):  # malformed HTML: keep whatever was parsed
        parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)


def _wanted(phrases: list[str], generic: frozenset[str]) -> set[str]:
    return set().union(*(significant_tokens(p) for p in phrases)) - generic if phrases else set()


def relevance(
    article: Article, phrases: list[str], generic: frozenset[str] = DEFAULT_GENERIC_WORDS
) -> int:
    """How many specific words from `phrases` appear in the article's title and summary."""
    text = f"{article.title} {article.summary}"
    return len(significant_tokens(text) & _wanted(phrases, generic))


def is_relevant(
    article: Article, phrases: list[str], generic: frozenset[str] = DEFAULT_GENERIC_WORDS
) -> bool:
    """A specific topic word in the title, or two anywhere in title and summary."""
    in_title = significant_tokens(article.title) & _wanted(phrases, generic)
    return bool(in_title) or relevance(article, phrases, generic) >= 2


# --- Network ----------------------------------------------------------------------------------


class SourceClient:
    """Reads feeds, searches Hacker News and fetches article text. Never raises on bad sources."""

    def __init__(
        self,
        settings: SourceSettings,
        http: httpx.Client | None = None,
        timeout: float = 15.0,
        max_chars: int = 5000,
    ) -> None:
        self.settings = settings
        self.max_chars = max_chars
        self._http = http or httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self._feed_cache: list[Article] | None = None
        self.feed_status: dict[str, str] = {}

    def is_official(self, url: str) -> bool:
        host = re.sub(r"^[a-z]+://", "", url.lower()).split("/", 1)[0]
        return any(host == d or host.endswith(f".{d}") for d in self.settings.official_domains)

    def _get(self, url: str, **params: object) -> httpx.Response | None:
        try:
            response = self._http.get(url, params=params or None)
        except httpx.HTTPError as exc:
            log_event(
                logger,
                "sources.fetch_failed",
                logging.WARNING,
                url=url[:100],
                error=type(exc).__name__,
            )
            return None
        if response.status_code != 200:
            log_event(
                logger,
                "sources.fetch_failed",
                logging.WARNING,
                url=url[:100],
                status=response.status_code,
            )
            return None
        return response

    def all_feed_items(self) -> list[Article]:
        """Every item from every feed (fetched once per run)."""
        if self._feed_cache is None:
            items: list[Article] = []
            for feed in self.settings.feeds:
                response = self._get(feed.url)
                parsed = (
                    parse_feed(response.text, feed, self.is_official(feed.url)) if response else []
                )
                self.feed_status[feed.name] = f"{len(parsed)} items" if response else "unreachable"
                items += parsed
            self._feed_cache = items
            log_event(logger, "sources.feeds", feeds=len(self.settings.feeds), items=len(items))
        return self._feed_cache

    def feed_items(self, since: date, until: date | None = None) -> list[Article]:
        """Feed items published between `since` and `until` (inclusive)."""
        until = until or date.max
        return [a for a in self.all_feed_items() if since <= a.published <= until]

    def search_hn(self, query: str, since: date, min_points: int, limit: int = 20) -> list[Article]:
        """Hacker News stories matching `query` since `since`, with at least `min_points`."""
        timestamp = int(datetime.combine(since, time(0, 0), tzinfo=UTC).timestamp())
        response = self._get(
            HN_SEARCH_URL,
            query=query,
            tags="story",
            numericFilters=f"created_at_i>{timestamp},points>={min_points}",
            hitsPerPage=limit,
        )
        if response is None:
            return []
        articles = []
        for hit in response.json().get("hits", []):
            url, title = hit.get("url"), hit.get("title")
            created = hit.get("created_at_i")
            if not (url and title and created):
                continue
            articles.append(
                Article(
                    title=title,
                    url=url,
                    published=datetime.fromtimestamp(created, tz=UTC).date(),
                    source=f"{domain_of(url)} (via Hacker News)",
                    official=self.is_official(url),
                    points=int(hit.get("points") or 0),
                )
            )
        return articles

    def fetch_text(self, url: str) -> str:
        """Readable text of a page, trimmed to `max_chars`. Empty string if it can't be read."""
        response = self._get(url)
        if response is None or "html" not in response.headers.get("content-type", "html"):
            return ""
        return html_to_text(response.text)[: self.max_chars]

    def read(self, articles: list[Article]) -> list[Article]:
        """Fill in `text` for each article (falling back to its feed summary)."""
        for article in articles:
            article.text = self.fetch_text(article.url) or article.summary
        return articles


def dedupe(articles: list[Article]) -> list[Article]:
    """Drop repeats of the same URL (ignoring tracking params and trailing slashes)."""
    seen: set[str] = set()
    unique = []
    for article in articles:
        key = article.url.split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            unique.append(article)
    return unique


def format_articles(articles: list[Article], with_text: bool = True) -> str:
    """Articles as numbered blocks for prompts. Ids are `A1`, `A2`, ..."""
    blocks = []
    for i, a in enumerate(articles, 1):
        official = " (official)" if a.official else ""
        header = f"[A{i}] {a.title}\n     {a.source}{official}, {a.published.isoformat()}, {a.url}"
        body = a.text if with_text else a.summary[:200]
        blocks.append(f"{header}\n{body.strip()}" if body.strip() else header)
    return "\n\n".join(blocks)


def clean_details(items: object, limit: int = 4) -> list[str]:
    """The specific facts a model pulled from articles: non-empty strings, at most `limit`."""
    if not isinstance(items, list):
        return []
    return [str(d).strip() for d in items if str(d).strip()][:limit]


def by_ids(articles: list[Article], ids: object) -> list[Article]:
    """Map ids like "A2" (or 2) from a model reply back to articles, ignoring unknown ids."""
    picked = []
    for raw in ids if isinstance(ids, list) else []:
        match = re.fullmatch(r"\s*A?(\d+)\s*", str(raw), re.IGNORECASE)
        if match and 1 <= int(match.group(1)) <= len(articles):
            article = articles[int(match.group(1)) - 1]
            if article not in picked:
                picked.append(article)
    return picked
