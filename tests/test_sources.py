"""Free research sources: feed parsing, Hacker News search, article text, soft failures."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx

from agent.sources import (
    Feed,
    SourceClient,
    SourceSettings,
    by_ids,
    dedupe,
    format_articles,
    html_to_text,
    is_relevant,
    load_source_settings,
    parse_feed,
    relevance,
)
from fakes import article

REPO = Path(__file__).resolve().parents[1]

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Blog</title>
  <item>
    <title>Agents get &amp; keep permissions</title>
    <link>https://vendor.example/agents</link>
    <pubDate>Mon, 26 Oct 2026 09:00:00 GMT</pubDate>
    <description>&lt;p&gt;New &lt;b&gt;permission&lt;/b&gt; model for agents.&lt;/p&gt;</description>
  </item>
  <item><title>No date</title><link>https://vendor.example/x</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title>
  <entry>
    <title>MCP spec update</title>
    <link rel="alternate" href="https://spec.example/mcp"/>
    <published>2026-10-20T10:00:00Z</published>
    <summary>Remote servers can now use OAuth.</summary>
  </entry>
</feed>"""

HTML = """<html><head><script>var x = 1;</script><style>p{}</style></head>
<body><nav>Home | About</nav><article><h1>Big launch</h1>
<p>The company   shipped version 2.</p><p>It adds retries.</p></article>
<footer>Copyright</footer></body></html>"""


def client(handler, **settings) -> SourceClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return SourceClient(SourceSettings(**settings), http=http, max_chars=200)


def test_parse_rss() -> None:
    [item] = parse_feed(RSS, Feed("Vendor Blog", "https://vendor.example/feed", official=True))
    assert item.title == "Agents get & keep permissions"
    assert item.url == "https://vendor.example/agents"
    assert item.published == date(2026, 10, 26)
    assert item.summary == "New permission model for agents."
    assert item.official is True and item.source == "Vendor Blog"


def test_parse_atom() -> None:
    [entry] = parse_feed(ATOM, Feed("Spec", "https://spec.example/feed"))
    assert (entry.title, entry.url, entry.published) == (
        "MCP spec update",
        "https://spec.example/mcp",
        date(2026, 10, 20),
    )
    assert entry.summary == "Remote servers can now use OAuth."
    assert entry.official is False


def test_parse_bad_xml_returns_nothing() -> None:
    assert parse_feed("<rss><not-closed>", Feed("Broken", "https://x.example")) == []


def test_html_to_text_keeps_content_and_drops_chrome() -> None:
    text = html_to_text(HTML)
    assert text == "Big launch\nThe company shipped version 2.\nIt adds retries."


def test_feed_items_skip_dead_feeds_and_filter_dates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "dead" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, text=RSS)

    sources = client(
        handler,
        feeds=[
            Feed("Good", "https://good.example/feed"),
            Feed("Dead", "https://dead.example/feed"),
        ],
    )
    assert [a.title for a in sources.feed_items(date(2026, 10, 25))] == [
        "Agents get & keep permissions"
    ]
    assert sources.feed_items(date(2026, 10, 27)) == []
    assert sources.feed_status == {"Good": "1 items", "Dead": "unreachable"}


def test_network_errors_are_soft() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    sources = client(handler, feeds=[Feed("Down", "https://down.example/feed")])
    assert sources.all_feed_items() == []
    assert sources.search_hn("AI", date(2026, 10, 21), 100) == []
    assert sources.fetch_text("https://down.example/a") == ""


def test_search_hn_builds_query_and_parses_hits() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "title": "OpenAI ships X",
                        "url": "https://openai.com/index/x",
                        "created_at_i": 1793260800,
                        "points": 420,
                    },
                    {
                        "title": "Ask HN: anyone?",
                        "url": None,
                        "created_at_i": 1793260800,
                        "points": 50,
                    },
                ]
            },
        )

    sources = client(handler, official_domains=["openai.com"])
    [hit] = sources.search_hn("OpenAI", date(2026, 10, 21), 100)
    params = seen[0].url.params
    assert seen[0].url.host == "hn.algolia.com"
    assert params["query"] == "OpenAI" and params["tags"] == "story"
    assert params["numericFilters"] == "created_at_i>1792540800,points>=100"
    assert hit.title == "OpenAI ships X" and hit.points == 420
    assert hit.published == date(2026, 10, 29)
    assert hit.official is True
    assert hit.source == "openai.com (via Hacker News)"


def test_fetch_text_trims_and_skips_non_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        return httpx.Response(200, text=HTML * 20, headers={"content-type": "text/html"})

    sources = client(handler)
    text = sources.fetch_text("https://news.example/a")
    assert text.startswith("Big launch") and len(text) == 200
    assert sources.fetch_text("https://news.example/report.pdf") == ""


def test_read_falls_back_to_summary() -> None:
    sources = client(lambda r: httpx.Response(404))
    [a] = sources.read([article("T", "https://x.example/a", summary="Feed summary.")])
    assert a.text == "Feed summary."


def test_is_official_matches_subdomains_only() -> None:
    sources = client(lambda r: httpx.Response(404), official_domains=["microsoft.com"])
    assert sources.is_official("https://devblogs.microsoft.com/foundry/x")
    assert sources.is_official("https://microsoft.com/a")
    assert not sources.is_official("https://notmicrosoft.com/a")


def test_dedupe_ignores_tracking_and_trailing_slash() -> None:
    items = [
        article("a", "https://x.example/p"),
        article("b", "https://x.example/p/?utm=1"),
        article("c", "https://y.example/p"),
    ]
    assert [a.title for a in dedupe(items)] == ["a", "c"]


def test_by_ids_and_format_articles() -> None:
    items = [
        article("One", "https://a.example/1", official=True),
        article("Two", "https://b.example/2"),
    ]
    assert [a.title for a in by_ids(items, ["A2", "a1", "A9", 2, "junk"])] == ["Two", "One"]
    assert by_ids(items, "A1") == []
    items[0].text = "Body one."
    text = format_articles(items)
    assert (
        "[A1] One\n     Example News (official), 2026-10-26, https://a.example/1\nBody one." in text
    )
    assert "[A2] Two" in text


def test_relevance_counts_shared_words() -> None:
    item = article(
        "Model Context Protocol adds auth", "https://x.example", summary="OAuth for MCP servers"
    )
    assert relevance(item, ["Model Context Protocol (MCP)"]) == 4
    assert relevance(item, ["quarterly earnings"]) == 0
    generic = article("AI changes banking", "https://y.example", summary="Banks use AI tools.")
    assert relevance(generic, ["From Generative AI to Agentic AI"]) == 0  # "AI" alone doesn't count
    assert not is_relevant(generic, ["From Generative AI to Agentic AI"])
    titled = article("Agentic workflows in retail", "https://z.example")
    assert is_relevant(titled, ["From Generative AI to Agentic AI"])  # specific word in the title
    summary_only = article(
        "Weekly roundup", "https://w.example", summary="MCP servers and model context"
    )
    assert is_relevant(summary_only, ["Model Context Protocol (MCP)"])  # two words anywhere


def test_real_feed_config_loads() -> None:
    settings = load_source_settings(REPO / "content" / "feeds.yaml")
    assert len(settings.feeds) >= 10
    assert any(f.official for f in settings.feeds)
    assert "openai.com" in settings.official_domains
    assert settings.news_queries and settings.hn_min_points_news == 100
