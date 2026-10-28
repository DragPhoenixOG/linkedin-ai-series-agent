"""Writer and editor prompts and parsing."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agent.config import Config
from agent.editor import edit_draft, parse_review
from agent.llm import LLMError
from agent.state import NewsStory, Source
from agent.writer import PostBrief, Voice, extract_post, load_voice, write_draft
from fakes import FakeLLM

REPO = Path(__file__).resolve().parents[1]


def brief(**overrides) -> PostBrief:
    data = {
        "post_type": "skill",
        "tone": "straight",
        "format_id": "observation",
        "format_name": "Observation",
        "format_description": "Something I noticed and what it taught me.",
        "today": date(2026, 10, 28),
        "voice": Voice(style_guide="STYLE GUIDE TEXT", author_facts="AUTHOR FACTS TEXT"),
        "research_notes": "1. (2026-10-20) Thing shipped.",
        "topic_title": "Model Context Protocol (MCP)",
        "topic_angle": "Build once, use with any agent.",
    }
    data.update(overrides)
    return PostBrief(**data)


def test_voice_loads_real_content_files() -> None:
    voice = load_voice(REPO / "content")
    assert "Banned words" in voice.style_guide
    assert "AbsoluteLabs" in voice.author_facts
    assert voice.system_block().startswith("# Style guide")


def test_extract_post_from_tags() -> None:
    assert extract_post("Sure!\n<post>\nHello world\n</post>\nDone") == "Hello world"
    assert extract_post("No tags, just text") == "No tags, just text"
    with pytest.raises(LLMError):
        extract_post("<post>  </post>")


def test_writer_prompt_carries_everything() -> None:
    llm = FakeLLM({"writer": "<post>Draft text</post>"})
    recent = ["Older opener here.\n\n- a\n- b\n\nWhat would you do?"]
    assert write_draft(llm, Config(), brief(tone="humorous", recent_posts=recent)) == "Draft text"
    call = llm.calls[0]
    assert call["system"][0].startswith("# Style guide")
    assert "STYLE GUIDE TEXT" in call["system"][0]
    assert "AUTHOR FACTS TEXT" in call["system"][0]
    prompt = call["prompt"]
    assert "Model Context Protocol (MCP)" in prompt
    assert "Thing shipped." in prompt
    assert "noticeably funny" in prompt
    assert "No bullet or numbered lists" in prompt
    assert "Do not end with a question" in prompt
    assert '"Older opener here."' in prompt
    assert "Revision" not in prompt


def test_writer_revision_includes_feedback_and_previous() -> None:
    llm = FakeLLM({"writer": "<post>v2</post>"})
    write_draft(llm, Config(), brief(), feedback="- too generic", previous="v1 text")
    prompt = llm.calls[0]["prompt"]
    assert "- too generic" in prompt
    assert "v1 text" in prompt


def test_news_subject_includes_story_and_link() -> None:
    story = NewsStory(
        key="k",
        title="Vendor ships agent SDK 2.0",
        summary="Vendor released version 2.0.",
        date="2026-10-24",
        sources=[Source(url="https://vendor.example/news")],
        angle="Versioning matters.",
    )
    llm = FakeLLM({"writer": "<post>x</post>"})
    write_draft(llm, Config(), brief(post_type="news", story=story))
    prompt = llm.calls[0]["prompt"]
    assert "Vendor ships agent SDK 2.0" in prompt
    assert "https://vendor.example/news" in prompt
    assert "whether the hype matches reality" in prompt


def test_parse_review_clamps_and_defaults() -> None:
    review = parse_review(
        {
            "human_score": 14,
            "humour_score": "7",
            "ai_sounding": [{"sentence": "x", "reason": "y"}, {}],
            "rewrite": " r ",
        },
        humorous=True,
    )
    assert review.human_score == 10
    assert review.humour_score == 7
    assert review.rewrite == "r"
    assert review.ai_sounding == [{"sentence": "x", "reason": "y"}]
    assert parse_review({"human_score": 9, "humour_score": 5}, humorous=False).humour_score is None
    assert parse_review(None, humorous=False).human_score == 0


def test_editor_uses_schema_and_humour_instructions() -> None:
    reply = {
        "human_score": 8,
        "humour_score": 7,
        "ai_sounding": [],
        "fact_problems": ["Claims 40% uplift; not in research."],
        "rule_problems": [],
        "verdict": "Fine.",
        "rewrite": "Better text",
    }
    llm = FakeLLM({"editor": reply})
    review = edit_draft(llm, Config(), brief(tone="humorous"), "Draft text")
    assert review.rewrite == "Better text"
    assert review.feedback() == ["Fact problem: Claims 40% uplift; not in research."]
    call = llm.calls[0]
    assert call["json_schema"]["required"]
    assert "is it actually funny" in call["prompt"]
    assert "<draft>\nDraft text\n</draft>" in call["prompt"]
