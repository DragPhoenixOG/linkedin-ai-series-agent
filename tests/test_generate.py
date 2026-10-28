"""The write -> edit -> lint retry loop."""

from __future__ import annotations

from agent.config import Config
from agent.generate import generate_post
from conftest import CLEAN_POST
from fakes import FakeLLM, seq
from test_writer_editor import brief

BAD_POST = CLEAN_POST.replace("boring engineering", "robust engineering")


def review(score: int, rewrite: str, humour: int = 0, ai: list | None = None) -> dict:
    return {
        "human_score": score,
        "humour_score": humour,
        "ai_sounding": ai or [],
        "fact_problems": [],
        "rule_problems": [],
        "verdict": "v",
        "rewrite": rewrite,
    }


def test_passes_first_time() -> None:
    llm = FakeLLM({"writer": f"<post>{CLEAN_POST}</post>", "editor": review(9, CLEAN_POST)})
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert len(result.attempts) == 1
    assert result.best.text == CLEAN_POST


def test_low_score_retries_with_feedback() -> None:
    llm = FakeLLM(
        {
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": seq(
                review(
                    6, CLEAN_POST, ai=[{"sentence": "The plumbing decides", "reason": "slogan"}]
                ),
                review(8, CLEAN_POST),
            ),
        }
    )
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert len(result.attempts) == 2
    second_prompt = [c for c in llm.calls if c["label"] == "writer"][1]["prompt"]
    assert "scored" in second_prompt and "6/10" in second_prompt
    assert '"The plumbing decides" (slogan)' in second_prompt
    assert CLEAN_POST.splitlines()[0] in second_prompt  # previous version is passed back


def test_lint_failure_on_rewrite_falls_back_to_clean_draft() -> None:
    llm = FakeLLM({"writer": f"<post>{CLEAN_POST}</post>", "editor": review(9, BAD_POST)})
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert result.best.text == CLEAN_POST


def test_lint_failure_feeds_back_rule() -> None:
    llm = FakeLLM(
        {
            "writer": seq(f"<post>{BAD_POST}</post>", f"<post>{CLEAN_POST}</post>"),
            "editor": seq(review(9, BAD_POST), review(9, CLEAN_POST)),
        }
    )
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert len(result.attempts) == 2
    second_prompt = [c for c in llm.calls if c["label"] == "writer"][1]["prompt"]
    assert "banned_phrase" in second_prompt and "robust" in second_prompt


def test_humorous_post_needs_humour_score() -> None:
    llm = FakeLLM(
        {
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": seq(review(9, CLEAN_POST, humour=5), review(9, CLEAN_POST, humour=8)),
        }
    )
    result = generate_post(llm, Config(), brief(tone="humorous"))
    assert result.success
    assert len(result.attempts) == 2
    assert "actually funny" in result.attempts[0].problems[0]


def test_gives_up_after_max_attempts_and_returns_best() -> None:
    llm = FakeLLM(
        {
            "writer": f"<post>{BAD_POST}</post>",
            "editor": seq(review(5, BAD_POST), review(7, BAD_POST), review(6, BAD_POST)),
        }
    )
    result = generate_post(llm, Config(), brief())
    assert not result.success
    assert len(result.attempts) == 3
    assert result.best.number == 2  # same lint problems, highest editor score
    assert any("banned_phrase" in p for p in result.best.problems)
