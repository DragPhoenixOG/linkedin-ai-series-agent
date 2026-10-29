"""Writer and editor prompts and parsing."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agent.config import Config
from agent.editor import edit_draft, is_real_problem, parse_review
from agent.llm import LLMError
from agent.state import NewsStory, Source
from agent.writer import PostBrief, Voice, extract_post, load_voice, tidy_post, write_draft
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
    assert "Rewording it is not enough" in prompt


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


def test_tidy_post_spaces_paragraphs_and_keeps_lists_together() -> None:
    raw = "First line.\nSecond paragraph.\n\n\n\nThree things:\n- one\n- two\nAfter the list.\n#AI #Agents"
    assert tidy_post(raw) == (
        "First line.\n\nSecond paragraph.\n\nThree things:\n\n- one\n- two\n\nAfter the list.\n\n#AI #Agents"
    )


def test_writer_output_and_editor_rewrite_are_tidied() -> None:
    assert extract_post("<post>One.\nTwo.</post>") == "One.\n\nTwo."
    review = parse_review({"human_score": 9, "rewrite": "One.\nTwo."}, humorous=False)
    assert review.rewrite == "One.\n\nTwo."


def test_prompts_spell_out_personal_claims_and_humour_limits() -> None:
    llm = FakeLLM({"writer": "<post>x</post>", "editor": {"human_score": 9, "rewrite": "x"}})
    write_draft(llm, Config(), brief())
    edit_draft(llm, Config(), brief(), "x")
    writer_system = llm.calls[0]["system"][1]
    assert "personal claims" in writer_system and "interns" in writer_system
    editor_prompt = llm.calls[1]["prompt"]
    assert "made-up personal story" in editor_prompt
    assert "must fix every item you listed" in editor_prompt


EMPTY_TEMPLATE = {
    "human_score": 0,
    "humour_score": 0,
    "ai_sounding": [],
    "fact_problems": [],
    "rule_problems": [],
    "verdict": "",
    "rewrite": "",
}


def test_empty_template_review_is_asked_again() -> None:
    real = EMPTY_TEMPLATE | {"human_score": 8, "verdict": "Fine.", "rewrite": "Better text"}
    llm = FakeLLM({"editor": EMPTY_TEMPLATE, "editor.retry": real})
    review = edit_draft(llm, Config(), brief(), "Draft text")
    assert review.human_score == 8 and review.rewrite == "Better text"
    assert [c["label"] for c in llm.calls] == ["editor", "editor.retry"]
    assert "empty template" in llm.calls[1]["prompt"]


def test_humorous_review_needs_a_humour_score() -> None:
    no_humour = EMPTY_TEMPLATE | {"human_score": 8, "rewrite": "x"}
    assert parse_review(no_humour, humorous=False).is_complete(humorous=False)
    assert not parse_review(no_humour, humorous=True).is_complete(humorous=True)


@pytest.mark.parametrize(
    "note",
    [
        "",
        "None.",
        "N/A",
        "Nothing to flag",
        "No factual problems found; the AWS Bedrock AgentCore reference is correctly cited from"
        " the research.",
        "No issues.",
        "No unsupported claims.",
        "One em dash, which is allowed (max 1).",
        "The single emoji is acceptable here.",
        "Hashtag count (3) is within the limit.",
        "All claims are supported by the research.",
    ],
)
def test_notes_that_nothing_is_wrong_are_not_problems(note: str) -> None:
    assert not is_real_problem(note)


@pytest.mark.parametrize(
    "problem",
    [
        "Claims 40% uplift; not in research.",
        "'We spent a few years...' implies his team built this, violating the author facts.",
        "'We spent the last two years focusing on how well models can talk.'",
        "No factual problems except 'we broke our test data', which is invented.",
        "Two em dashes, but only 1 is allowed.",
        "None of the research mentions AgentCore pricing.",
        "'Agents are fine for low-risk work' overstates what the research says.",
        "The closing line is a bit too punchy/slogan-like.",
    ],
)
def test_real_problems_are_kept(problem: str) -> None:
    assert is_real_problem(problem)


def test_review_drops_notes_that_nothing_is_wrong() -> None:
    review = parse_review(
        {
            "human_score": 8,
            "rewrite": "x",
            "fact_problems": ["No factual problems found; the reference is correctly cited."],
            "rule_problems": ["Contains one em dash, which is allowed (max 1).", "Too long."],
        },
        humorous=False,
    )
    assert review.fact_problems == []
    assert review.rule_problems == ["Too long."]
    single = parse_review({"human_score": 8, "fact_problems": "Invented 40%."}, humorous=False)
    assert single.fact_problems == ["Invented 40%."]


def test_prompts_cover_the_industry_we_and_empty_lists() -> None:
    llm = FakeLLM({"writer": "<post>x</post>", "editor": {"human_score": 9, "rewrite": "x"}})
    write_draft(llm, Config(), brief())
    edit_draft(llm, Config(), brief(), "x")
    assert 'industry "we"' in llm.calls[0]["system"][1]
    editor_prompt = llm.calls[1]["prompt"]
    assert 'industry "we"' in editor_prompt
    assert "return an empty list" in editor_prompt
