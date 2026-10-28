"""News scanning, filtering, de-duplication, scoring and verification."""

from __future__ import annotations

from datetime import date

from agent.config import Config
from agent.llm import LLMError
from agent.news import (
    has_enough_sources,
    is_already_covered,
    parse_candidates,
    pick_story,
    rank_candidates,
    score,
)
from agent.state import History, NewsStory, PostRecord, Source
from fakes import FakeLLM, seq

TODAY = date(2026, 10, 28)


def cand(title: str, day: str = "2026-10-26", **overrides) -> dict:
    data = {
        "title": title,
        "summary": f"{title} happened.",
        "date": day,
        "key_facts": [f"{title} is real"],
        "sources": [
            {"url": f"https://press.example/{len(title)}", "publisher": "Press"},
            {"url": f"https://news.example/{len(title)}", "publisher": "News"},
        ],
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
        "facts": [{"fact": "x", "supported": True, "sources": ["https://press.example/1"]}],
        "summary": "Verified summary.",
        "sources": [],
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


def test_parse_candidates_filters_dates_and_bad_items() -> None:
    data = {
        "candidates": [
            cand("Fresh story"),
            cand("Too old", day="2026-10-10"),
            cand("From the future", day="2026-11-02"),
            cand("No date", day="soon"),
            {"title": ""},
            "junk",
        ]
    }
    parsed = parse_candidates(data, TODAY, 7)
    assert [c.title for c in parsed] == ["Fresh story"]
    assert parsed[0].confirmed is True


def test_source_rule_two_domains_or_one_official() -> None:
    assert has_enough_sources([Source("https://a.example/1"), Source("https://b.example/2")])
    assert not has_enough_sources(
        [Source("https://a.example/1"), Source("https://www.a.example/2")]
    )
    assert has_enough_sources([Source("https://vendor.example/blog", official=True)])
    assert not has_enough_sources([])


def test_dedup_by_key_title_similarity_and_url() -> None:
    story = NewsStory(
        key="vendor-launches-agent-sdk-2-0",
        title="Vendor launches Agent SDK 2.0",
        summary="",
        date="2026-10-01",
        sources=[Source("https://vendor.example/sdk2")],
    )
    [same_title, similar, same_url, different] = parse_candidates(
        {
            "candidates": [
                cand("Vendor launches Agent SDK 2.0"),
                cand("Vendor launches its Agent SDK 2.0 for developers"),
                cand("Totally new name", sources=[{"url": "https://vendor.example/sdk2/"}]),
                cand("Regulator publishes AI audit rules"),
            ]
        },
        TODAY,
        7,
    )
    assert is_already_covered(same_title, [story])
    assert is_already_covered(similar, [story])
    assert is_already_covered(same_url, [story])
    assert not is_already_covered(different, [story])


def test_score_prefers_relevant_fresh_major_and_penalises_recent_overlap() -> None:
    [fresh, stale, major] = parse_candidates(
        {
            "candidates": [
                cand("Fresh one", day="2026-10-27"),
                cand("Stale one", day="2026-10-22"),
                cand("Major one", day="2026-10-22", significance="major"),
            ]
        },
        TODAY,
        7,
    )
    assert score(fresh, TODAY, 7, []) > score(stale, TODAY, 7, [])
    assert score(major, TODAY, 7, []) > score(stale, TODAY, 7, [])
    assert score(fresh, TODAY, 7, ["Fresh one again"]) < score(fresh, TODAY, 7, [])


def test_rank_drops_unconfirmed_thin_and_covered_stories() -> None:
    covered = NewsStory(key="old-news", title="Old news story", summary="", date="2026-10-01")
    history = History(posts=[news_post("2026-10-02", covered)])
    candidates = parse_candidates(
        {
            "candidates": [
                cand("Leaked roadmap", confirmed=False),
                cand("Single blog only", sources=[{"url": "https://blog.example/x"}]),
                cand("Old news story"),
                cand("Good story", relevance=5),
                cand("Okay story", relevance=2),
            ]
        },
        TODAY,
        7,
    )
    ranked = rank_candidates(candidates, history, Config(), TODAY)
    assert [c.title for c in ranked] == ["Good story", "Okay story"]


def test_pick_story_verifies_top_candidate() -> None:
    llm = FakeLLM(
        {"news.scan": {"candidates": [cand("Good story", relevance=5)]}, "news.verify": verified()}
    )
    story = pick_story(llm, Config(), TODAY, History())
    assert story is not None
    assert story.title == "Good story"
    assert story.summary == "Verified summary."
    assert story.key == "good-story"
    assert [c["label"] for c in llm.calls] == ["news.scan", "news.verify"]
    assert llm.calls[0]["web_search_max_uses"] == Config().news_max_searches


def test_pick_story_moves_to_next_candidate_when_verification_fails() -> None:
    llm = FakeLLM(
        {
            "news.scan": {
                "candidates": [cand("Top story", relevance=5), cand("Second story", relevance=3)]
            },
            "news.verify": seq(
                verified(status="unconfirmed", verified=False),
                verified(),
            ),
        }
    )
    story = pick_story(llm, Config(), TODAY, History())
    assert story is not None and story.title == "Second story"


def test_pick_story_rejects_unsupported_facts() -> None:
    llm = FakeLLM(
        {
            "news.scan": {"candidates": [cand("Only story")]},
            "news.verify": verified(facts=[{"fact": "x", "supported": False}]),
        }
    )
    assert pick_story(llm, Config(), TODAY, History()) is None


def test_pick_story_returns_none_when_nothing_qualifies() -> None:
    llm = FakeLLM({"news.scan": {"candidates": []}})
    assert pick_story(llm, Config(), TODAY, History()) is None


def test_pick_story_survives_api_failure() -> None:
    llm = FakeLLM({"news.scan": LLMError("search down")})
    assert pick_story(llm, Config(), TODAY, History()) is None


def test_require_major_ignores_normal_stories() -> None:
    llm = FakeLLM({"news.scan": {"candidates": [cand("Normal story")]}, "news.verify": verified()})
    assert pick_story(llm, Config(), TODAY, History(), require_major=True) is None
    assert [c["label"] for c in llm.calls] == ["news.scan"]
