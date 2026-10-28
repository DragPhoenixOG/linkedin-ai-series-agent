"""News gathering, scanning, filtering, de-duplication, scoring and verification."""

from __future__ import annotations

from datetime import date

import pytest

from agent.config import Config
from agent.llm import LLMAuthError, LLMError
from agent.news import (
    gather_headlines,
    has_enough_sources,
    is_already_covered,
    parse_candidates,
    pick_story,
    rank_candidates,
    score,
)
from agent.state import History, NewsStory, PostRecord, Source
from fakes import FakeLLM, FakeSources, article, seq

TODAY = date(2026, 10, 28)

RULES_OFFICIAL = article(
    "Regulator publishes AI audit rules",
    "https://regulator.example/rules",
    "2026-10-26",
    "Regulator",
    official=True,
)
RULES_PRESS = article("AI audit rules land", "https://press.example/audit", "2026-10-26", "Press")
SDK_NEWS = article("Vendor ships agent SDK 2.0", "https://news.example/sdk", "2026-10-27", "News")
SDK_PRESS = article("Agent SDK 2.0 is out", "https://press.example/sdk", "2026-10-25", "Press")
LONE_BLOG = article("A single blog post", "https://blog.example/x", "2026-10-24", "Blog")
HEADLINES = [RULES_OFFICIAL, RULES_PRESS, SDK_NEWS, SDK_PRESS, LONE_BLOG]


def news_sources(**kwargs) -> FakeSources:
    return FakeSources(feed=list(HEADLINES), **kwargs)


def ids(*articles) -> list[str]:
    """Headline ids as the scan prompt numbers them."""
    order = [a.url for a in gather_headlines(news_sources(), TODAY, 7)]
    return [f"A{order.index(a.url) + 1}" for a in articles]


def cand(title: str, headlines: list | None = None, **overrides) -> dict:
    data = {
        "title": title,
        "summary": f"{title} happened.",
        "key_facts": [f"{title} is real"],
        "headline_ids": ids(*(headlines or [SDK_NEWS, SDK_PRESS])),
        "confirmed": True,
        "significance": "normal",
        "relevance": 4,
        "angle_strength": 4,
        "angle": "Reliability angle.",
    }
    data.update(overrides)
    return data


def verified(**overrides) -> dict:
    data = {
        "verified": True,
        "status": "confirmed",
        "facts": [{"fact": "x", "supported": True, "article_ids": ["A1", "A2"]}],
        "summary": "Verified summary.",
    }
    data.update(overrides)
    return data


def news_post(day: str, story: NewsStory) -> PostRecord:
    return PostRecord(
        date=day,
        post_type="news",
        tone="straight",
        format="news_reaction",
        text="t",
        slug="s",
        news_story=story,
    )


def parsed(*candidates: dict):
    return parse_candidates(
        {"candidates": list(candidates)}, gather_headlines(news_sources(), TODAY, 7)
    )


def test_gather_headlines_merges_dedupes_and_filters_window() -> None:
    old = article("Old story", "https://old.example/x", "2026-10-10")
    hn = article("HN copy", "https://news.example/sdk/?ref=hn", "2026-10-27", points=300)
    sources = FakeSources(
        feed=[*HEADLINES, old],
        hn={"*": [hn, article("HN only", "https://hn.example/a", "2026-10-28")]},
    )
    headlines = gather_headlines(sources, TODAY, 7)
    urls = [a.url for a in headlines]
    assert urls[0] == "https://hn.example/a"  # newest first
    assert "https://old.example/x" not in urls
    assert sum("news.example/sdk" in u for u in urls) == 1
    assert sources.searches == ["AI"]


def test_parse_candidates_maps_ids_and_drops_bad_items() -> None:
    [good] = parsed(
        cand("SDK story"),
        {"title": "No headlines", "headline_ids": ["A99"]},
        {"title": "", "headline_ids": ["A1"]},
    )
    assert [a.url for a in good.articles] == [SDK_NEWS.url, SDK_PRESS.url]
    assert good.date == "2026-10-25"  # earliest cited headline
    assert good.confirmed is True


def test_source_rule_two_domains_or_one_official() -> None:
    assert has_enough_sources([Source("https://a.example/1"), Source("https://b.example/2")])
    assert not has_enough_sources(
        [Source("https://a.example/1"), Source("https://www.a.example/2")]
    )
    assert has_enough_sources([Source("https://vendor.example/blog", official=True)])
    assert not has_enough_sources([])


def test_dedup_by_key_title_similarity_and_url() -> None:
    story = NewsStory(
        key="vendor-ships-agent-sdk-2-0",
        title="Vendor ships agent SDK 2.0",
        summary="",
        date="2026-10-01",
        sources=[Source("https://news.example/sdk")],
    )
    same_title, similar, same_url, different = parsed(
        cand("Vendor ships agent SDK 2.0", [LONE_BLOG]),
        cand("Vendor ships its agent SDK 2.0 for developers", [LONE_BLOG]),
        cand("Totally new name", [SDK_NEWS]),
        cand("Regulator publishes AI audit rules", [RULES_OFFICIAL]),
    )
    assert is_already_covered(same_title, [story])
    assert is_already_covered(similar, [story])
    assert is_already_covered(same_url, [story])
    assert not is_already_covered(different, [story])


def test_score_prefers_relevant_fresh_major_and_penalises_recent_overlap() -> None:
    fresh, stale, major = parsed(
        cand("Fresh one", [SDK_NEWS]),
        cand("Stale one", [LONE_BLOG]),
        cand("Major one", [LONE_BLOG], significance="major"),
    )
    assert score(fresh, TODAY, 7, []) > score(stale, TODAY, 7, [])
    assert score(major, TODAY, 7, []) > score(stale, TODAY, 7, [])
    assert score(fresh, TODAY, 7, ["Fresh one again"]) < score(fresh, TODAY, 7, [])


def test_rank_drops_unconfirmed_thin_and_covered_stories() -> None:
    covered = NewsStory(key="old-news", title="Old news story", summary="", date="2026-10-01")
    history = History(posts=[news_post("2026-10-02", covered)])
    candidates = parsed(
        cand("Leaked roadmap", confirmed=False),
        cand("Single blog only", [LONE_BLOG]),
        cand("Old news story"),
        cand("Good story", relevance=5),
        cand("Official only story", [RULES_OFFICIAL], relevance=2),
    )
    ranked = rank_candidates(candidates, history, Config(), TODAY)
    assert [c.title for c in ranked] == ["Good story", "Official only story"]


def test_pick_story_verifies_against_fetched_text() -> None:
    llm = FakeLLM(
        {"news.scan": {"candidates": [cand("Good story", relevance=5)]}, "news.verify": verified()}
    )
    sources = news_sources(texts={SDK_NEWS.url: "Vendor released SDK 2.0 on Monday."})
    story = pick_story(llm, Config(), sources, TODAY, History())
    assert story is not None
    assert story.title == "Good story"
    assert story.summary == "Verified summary."
    assert story.key == "good-story"
    assert [s.url for s in story.sources] == [SDK_NEWS.url, SDK_PRESS.url]
    assert [c["label"] for c in llm.calls] == ["news.scan", "news.verify"]
    assert "Vendor released SDK 2.0 on Monday." in llm.calls[1]["prompt"]
    assert sources.fetched == [SDK_NEWS.url, SDK_PRESS.url]


def test_official_source_is_listed_first() -> None:
    llm = FakeLLM(
        {
            "news.scan": {"candidates": [cand("Audit rules", [RULES_PRESS, RULES_OFFICIAL])]},
            "news.verify": verified(),
        }
    )
    story = pick_story(llm, Config(), news_sources(), TODAY, History())
    assert story.sources[0].official is True


def test_pick_story_moves_to_next_candidate_when_verification_fails() -> None:
    llm = FakeLLM(
        {
            "news.scan": {
                "candidates": [
                    cand("Top story", relevance=5),
                    cand("Second story", [RULES_OFFICIAL], relevance=3),
                ]
            },
            "news.verify": seq(verified(status="unconfirmed", verified=False), verified()),
        }
    )
    story = pick_story(llm, Config(), news_sources(), TODAY, History())
    assert story is not None and story.title == "Second story"


def test_pick_story_rejects_unsupported_facts() -> None:
    llm = FakeLLM(
        {
            "news.scan": {"candidates": [cand("Only story")]},
            "news.verify": verified(
                facts=[{"fact": "x", "supported": False, "article_ids": ["A1"]}]
            ),
        }
    )
    assert pick_story(llm, Config(), news_sources(), TODAY, History()) is None


def test_pick_story_needs_enough_supporting_articles() -> None:
    llm = FakeLLM(
        {
            "news.scan": {"candidates": [cand("Only story")]},
            "news.verify": verified(
                facts=[{"fact": "x", "supported": True, "article_ids": ["A1"]}]
            ),
        }
    )
    assert pick_story(llm, Config(), news_sources(), TODAY, History()) is None


def test_no_headlines_means_no_model_calls() -> None:
    llm = FakeLLM({})
    assert pick_story(llm, Config(), FakeSources(), TODAY, History()) is None
    assert llm.calls == []


def test_pick_story_survives_api_failure() -> None:
    llm = FakeLLM({"news.scan": LLMError("rate limited")})
    assert pick_story(llm, Config(), news_sources(), TODAY, History()) is None


def test_require_major_ignores_normal_stories() -> None:
    llm = FakeLLM({"news.scan": {"candidates": [cand("Normal story")]}, "news.verify": verified()})
    assert pick_story(llm, Config(), news_sources(), TODAY, History(), require_major=True) is None
    assert [c["label"] for c in llm.calls] == ["news.scan"]


def test_rejected_api_key_is_not_swallowed() -> None:
    llm = FakeLLM({"news.scan": LLMAuthError("API key was rejected")})
    with pytest.raises(LLMAuthError):
        pick_story(llm, Config(), news_sources(), TODAY, History())
