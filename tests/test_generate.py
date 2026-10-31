"""The write -> edit -> lint retry loop."""

from __future__ import annotations

from agent.config import Config
from agent.generate import generate_post
from agent.lint import LintLimits, lint_post
from conftest import CLEAN_POST
from fakes import FakeLLM, seq
from test_writer_editor import EMPTY_TEMPLATE, brief

NO_EDITS = {"edits": []}
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


def test_the_editor_rewrite_is_never_posted() -> None:
    invented = "I spent last month watching three teams ship agents. " + CLEAN_POST
    llm = FakeLLM({"writer": f"<post>{CLEAN_POST}</post>", "editor": review(9, invented)})
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert result.best.text == CLEAN_POST


def test_a_failing_draft_is_not_rescued_by_a_clean_rewrite() -> None:
    llm = FakeLLM(
        {
            "writer.fix": NO_EDITS,  # nothing applies: fall back to a full revision
            "writer": seq(f"<post>{BAD_POST}</post>", f"<post>{CLEAN_POST}</post>"),
            "editor": review(9, CLEAN_POST),
        }
    )
    result = generate_post(llm, Config(), brief())
    assert not result.attempts[0].passed
    assert any("banned_phrase" in p for p in result.attempts[0].problems)
    assert result.success and result.best.number == 2


def test_lint_failure_feeds_back_rule() -> None:
    llm = FakeLLM(
        {
            "writer.fix": NO_EDITS,  # nothing applies: fall back to a full revision
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
            "editor": seq(
                review(5, BAD_POST),
                review(7, BAD_POST),
                review(6, BAD_POST),
                review(6, BAD_POST),
                review(5, BAD_POST),
            ),
        }
    )
    result = generate_post(llm, Config(), brief())
    assert not result.success
    assert len(result.attempts) == Config().max_attempts == 5
    assert result.best.number == 2  # same lint problems, highest editor score
    assert any("banned_phrase" in p for p in result.best.problems)


def test_unsupported_claims_force_another_round_even_with_a_good_score() -> None:
    invented = review(9, CLEAN_POST) | {
        "fact_problems": ["'We broke our test data' is not in the author facts."]
    }
    llm = FakeLLM(
        {
            "writer.fix": NO_EDITS,  # nothing applies: fall back to a full revision
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": seq(invented, review(9, CLEAN_POST)),
        }
    )
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert len(result.attempts) == 2
    assert "unsupported claim" in result.attempts[0].problems[0]
    second_prompt = [c for c in llm.calls if c["label"] == "writer"][1]["prompt"]
    assert "not in the author facts" in second_prompt


def test_incomplete_review_is_reported_as_such() -> None:
    llm = FakeLLM(
        {
            "writer": f"<post>{CLEAN_POST}</post>",
            "editor": seq(EMPTY_TEMPLATE, review(9, CLEAN_POST)),
            "editor.retry": EMPTY_TEMPLATE,
        }
    )
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert result.attempts[0].problems == ["The editor returned an incomplete review (no scores)."]


def test_a_note_that_nothing_is_wrong_does_not_block_a_good_post() -> None:
    fine = review(8, CLEAN_POST) | {
        "fact_problems": ["No factual problems found; the reference is correctly cited."]
    }
    llm = FakeLLM({"writer": f"<post>{CLEAN_POST}</post>", "editor": fine})
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert len(result.attempts) == 1


def test_editor_sees_the_code_checks_for_the_exact_text() -> None:
    llm = FakeLLM(
        {
            "writer.fix": NO_EDITS,  # nothing applies: fall back to a full revision
            "writer": seq(f"<post>{BAD_POST}</post>", f"<post>{CLEAN_POST}</post>"),
            "editor": review(9, ""),
        }
    )
    generate_post(llm, Config(), brief())
    first, second = [c["prompt"] for c in llm.calls if c["label"] == "editor"]
    words = lint_post(CLEAN_POST, LintLimits.from_config(Config()), []).word_count
    assert "robust" in first and "Banned phrase" in first
    assert f"The draft has {words} words" in second
    assert "Nothing: all checks passed." in second


def test_a_nearly_there_post_only_gets_its_flagged_sentence_replaced() -> None:
    bad_sentence = next(line for line in BAD_POST.splitlines() if "robust" in line)
    good_sentence = bad_sentence.replace("robust engineering", "boring engineering")
    llm = FakeLLM(
        {
            "writer": f"<post>{BAD_POST}</post>",
            "editor": review(9, ""),
            "writer.fix": {"edits": [{"find": bad_sentence, "replace": good_sentence}]},
        }
    )
    result = generate_post(llm, Config(), brief())
    assert result.success and len(result.attempts) == 2
    assert result.best.text == CLEAN_POST  # everything else untouched
    assert [c["label"] for c in llm.calls].count("writer") == 1  # no full redraft
    fix_prompt = next(c["prompt"] for c in llm.calls if c["label"] == "writer.fix")
    assert "banned_phrase" in fix_prompt and bad_sentence in fix_prompt


def test_low_scores_still_get_a_full_revision() -> None:
    llm = FakeLLM(
        {
            "writer": seq(f"<post>{BAD_POST}</post>", f"<post>{CLEAN_POST}</post>"),
            "editor": seq(review(6, ""), review(9, "")),
        }
    )
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert "writer.fix" not in [c["label"] for c in llm.calls]


def test_a_near_miss_on_the_last_draft_still_gets_its_fix() -> None:
    bad_sentence = next(line for line in BAD_POST.splitlines() if "robust" in line)
    good_sentence = bad_sentence.replace("robust engineering", "boring engineering")
    llm = FakeLLM(
        {
            "writer": f"<post>{BAD_POST}</post>",
            # Low scores on the first four drafts, then a pass on everything but one sentence.
            "editor": seq(*[review(6, "")] * 4, review(9, "")),
            "writer.fix": {"edits": [{"find": bad_sentence, "replace": good_sentence}]},
        }
    )
    result = generate_post(llm, Config(), brief())
    assert result.success
    assert len(result.attempts) == 6  # 5 drafts + 1 fix round
    assert result.best.text == CLEAN_POST


def test_fix_rounds_are_capped() -> None:
    llm = FakeLLM(
        {
            "writer": f"<post>{BAD_POST}</post>",
            "editor": review(9, ""),
            # The "fix" keeps the banned word, so it never passes.
            "writer.fix": lambda req: {"edits": [{"find": "robust", "replace": "robust"}]},
        }
    )
    result = generate_post(llm, Config(), brief())
    assert not result.success
    assert [c["label"] for c in llm.calls].count("writer.fix") == 2
    assert len(result.attempts) == 5 + 2  # 5 full drafts, 2 fix rounds, then it stops
