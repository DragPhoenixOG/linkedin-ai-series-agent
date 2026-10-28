"""Skill-topic research from free sources, with the model and the network faked."""

from __future__ import annotations

from datetime import date

import pytest

from agent.config import Config
from agent.llm import LLMAuthError, LLMError
from agent.research import Research, gather_articles, parse_developments, research_topic
from fakes import FakeLLM, FakeSources, article

TODAY = date(2026, 10, 28)

MCP_BLOG = article(
    "Model Context Protocol adds remote server auth",
    "https://modelcontextprotocol.io/blog/auth",
    day="2026-10-20",
    source="MCP Blog",
    official=True,
    summary="The Model Context Protocol spec now covers authorization for remote servers.",
)
MCP_HN = article(
    "Show HN: an MCP server for order management",
    "https://example.dev/mcp-oms",
    day="2026-10-22",
    source="example.dev (via Hacker News)",
    points=80,
)
OLD = article("Model Context Protocol launch", "https://old.example/mcp", day="2026-06-01")
UNRELATED = article("Quarterly earnings roundup", "https://news.example/earnings", day="2026-10-25")


def test_gather_ranks_relevant_recent_articles_and_dedupes() -> None:
    sources = FakeSources(
        feed=[MCP_BLOG, UNRELATED, OLD],
        hn={"*": [MCP_HN, article("dup", "https://modelcontextprotocol.io/blog/auth/?utm=x")]},
    )
    found = gather_articles(
        sources,
        ["model context protocol"],
        "Model Context Protocol (MCP)",
        date(2026, 9, 13),
        TODAY,
        5,
    )
    urls = [a.url for a in found]
    assert urls[0] == "https://modelcontextprotocol.io/blog/auth"  # most relevant, official
    assert "https://example.dev/mcp-oms" in urls
    assert "https://news.example/earnings" not in urls  # irrelevant feed item
    assert "https://old.example/mcp" not in urls  # outside the window
    assert len(urls) == len({u.split("?")[0].rstrip("/") for u in urls})


def test_parse_developments_maps_ids_to_real_sources() -> None:
    articles = [MCP_BLOG, MCP_HN]
    data = {
        "developments": [
            {"summary": "MCP added auth.", "why_it_matters": "Permissions.", "article_ids": ["A1"]},
            {"summary": "Made-up thing.", "why_it_matters": "x", "article_ids": ["A9"]},
            {"summary": "", "article_ids": ["A2"]},
        ]
    }
    [dev] = parse_developments(data, articles, TODAY, 45)
    assert dev.summary == "MCP added auth."
    assert dev.date == "2026-10-20"  # from the feed, not the model
    assert dev.sources[0].url == "https://modelcontextprotocol.io/blog/auth"
    assert dev.sources[0].official is True


def test_parse_developments_caps_at_two() -> None:
    articles = [MCP_BLOG, MCP_HN]
    item = {"summary": "s", "why_it_matters": "w", "article_ids": ["A1"]}
    assert len(parse_developments({"developments": [item, item, item]}, articles, TODAY, 45)) == 2


def test_research_topic_end_to_end() -> None:
    llm = FakeLLM(
        {
            "research.queries": {"queries": ["model context protocol", "MCP server"]},
            "research": {
                "developments": [
                    {"summary": "MCP added auth.", "why_it_matters": "w", "article_ids": ["A1"]}
                ]
            },
        }
    )
    sources = FakeSources(feed=[MCP_BLOG], hn={"*": [MCP_HN]})
    research = research_topic(
        llm, Config(), sources, "Model Context Protocol (MCP)", "Build once", TODAY
    )
    assert [d.summary for d in research.developments] == ["MCP added auth."]
    assert sources.searches == ["model context protocol", "MCP server"]
    assert set(sources.fetched) == {MCP_BLOG.url, MCP_HN.url}
    prompt = next(c for c in llm.calls if c["label"] == "research")["prompt"]
    assert "[A1]" in prompt and "Full article text from" in prompt and "(official)" in prompt
    notes = research.notes()
    assert "2026-10-20" in notes and MCP_BLOG.url in notes


def test_no_articles_means_no_research_call() -> None:
    llm = FakeLLM({"research.queries": {"queries": ["nothing"]}})
    research = research_topic(llm, Config(), FakeSources(), "Obscure topic", "x", TODAY)
    assert research.developments == []
    assert [c["label"] for c in llm.calls] == ["research.queries"]
    assert "without referencing recent news" in Research().notes()


def test_query_failure_falls_back_to_title() -> None:
    llm = FakeLLM({"research.queries": LLMError("rate limited"), "research": {"developments": []}})
    sources = FakeSources(hn={"*": [MCP_HN]})
    research_topic(llm, Config(), sources, "Model Context Protocol (MCP)", "x", TODAY)
    assert sources.searches == ["Model Context Protocol (MCP)"]


def test_research_failure_degrades_to_empty() -> None:
    llm = FakeLLM({"research.queries": {"queries": ["mcp"]}, "research": LLMError("timeout")})
    sources = FakeSources(hn={"*": [MCP_HN]})
    assert research_topic(llm, Config(), sources, "MCP", "x", TODAY).developments == []


def test_rejected_api_key_stops_the_run() -> None:
    llm = FakeLLM({"research.queries": LLMAuthError("access denied for this API key")})
    sources = FakeSources(hn={"*": [MCP_HN]})
    with pytest.raises(LLMAuthError):
        research_topic(llm, Config(), sources, "MCP", "x", TODAY)
    assert sources.fetched == []  # no point reading articles the model can't use
