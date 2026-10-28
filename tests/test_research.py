"""Research parsing and validation."""

from __future__ import annotations

from datetime import date

from agent.config import Config
from agent.llm import LLMError
from agent.research import Research, parse_date, parse_developments, research_topic
from fakes import FakeLLM

TODAY = date(2026, 10, 28)


def dev(
    day: str, url: str = "https://vendor.example/blog", summary: str = "Thing shipped."
) -> dict:
    return {
        "summary": summary,
        "date": day,
        "why_it_matters": "Reliability.",
        "sources": [{"url": url, "title": "Blog", "publisher": "Vendor"}],
    }


def test_parse_date_variants() -> None:
    assert parse_date("2026-10-14") == date(2026, 10, 14)
    assert parse_date("2026-10-14T09:00:00Z") == date(2026, 10, 14)
    assert parse_date("2026-10") == date(2026, 10, 1)
    assert parse_date("last Tuesday") is None


def test_keeps_valid_and_caps_at_two() -> None:
    data = {"developments": [dev("2026-10-20"), dev("2026-10-10"), dev("2026-10-01")]}
    kept = parse_developments(data, TODAY, 45)
    assert [d.date for d in kept] == ["2026-10-20", "2026-10-10"]


def test_drops_items_without_sources_or_outside_window() -> None:
    data = {
        "developments": [
            dev("2026-08-01"),  # too old
            dev("2026-12-01"),  # future
            dev("2026-10-20", url="not-a-url"),
            {"summary": "", "date": "2026-10-20", "sources": [{"url": "https://x.example"}]},
            "garbage",
        ]
    }
    assert parse_developments(data, TODAY, 45) == []


def test_research_notes_mention_sources() -> None:
    research = Research(parse_developments({"developments": [dev("2026-10-20")]}, TODAY, 45))
    notes = research.notes()
    assert "2026-10-20" in notes
    assert "https://vendor.example/blog" in notes
    assert [s.url for s in research.sources] == ["https://vendor.example/blog"]


def test_empty_research_tells_writer_not_to_reference_news() -> None:
    assert "without referencing recent news" in Research().notes()


def test_research_topic_uses_web_search() -> None:
    llm = FakeLLM({"research": {"developments": [dev("2026-10-20")]}})
    research = research_topic(llm, Config(), "MCP", "Build once", TODAY)
    assert len(research.developments) == 1
    call = llm.calls[0]
    assert call["web_search_max_uses"] == Config().research_max_searches
    assert "2026-09-13" in call["prompt"]  # 45 days before today


def test_research_failure_degrades_to_empty() -> None:
    llm = FakeLLM({"research": LLMError("timeout")})
    assert research_topic(llm, Config(), "MCP", "Build once", TODAY).developments == []
