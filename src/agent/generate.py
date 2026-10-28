"""The write -> edit -> lint loop with retries (CLAUDE.md section 4, "Two-pass generation").

Each attempt: the writer drafts (or revises the last version using the feedback), the editor
scores the draft and rewrites it, and the deterministic lint checks the result. A post passes
when the editor's scores clear the bar and lint is clean. Otherwise the failures are fed back and
the loop tries again, up to `max_attempts`. If nothing passes, the caller gets the best attempt
and the reasons, and must not publish.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from agent.config import Config
from agent.editor import EditorReview, edit_draft
from agent.lint import LintLimits, LintResult, lint_post
from agent.llm import LLM
from agent.logs import log_event
from agent.writer import PostBrief, write_draft

logger = logging.getLogger(__name__)


@dataclass
class Attempt:
    """One write -> edit -> lint round."""

    number: int
    draft: str
    review: EditorReview
    text: str  # the candidate chosen from this round (editor rewrite, or the draft)
    lint: LintResult
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems

    def rank(self) -> tuple[int, int, int]:
        """Sort key for picking the best failed attempt: fewer problems, then higher scores."""
        return (-len(self.problems), self.review.human_score, self.review.humour_score or 0)


@dataclass
class GenerationResult:
    """All attempts for one post."""

    attempts: list[Attempt] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].passed

    @property
    def best(self) -> Attempt:
        """The passing attempt, or the most promising failed one."""
        passing = [a for a in self.attempts if a.passed]
        return passing[-1] if passing else max(self.attempts, key=Attempt.rank)


def score_problems(review: EditorReview, brief: PostBrief, config: Config) -> list[str]:
    """Editor-score gates as human-readable problems."""
    if not review.is_complete(brief.tone == "humorous"):
        return ["The editor returned an incomplete review (no scores or no rewrite)."]
    problems = []
    if review.human_score < config.min_editor_score:
        problems.append(
            f'Editor scored "sounds like a real person" {review.human_score}/10; '
            f"needs {config.min_editor_score}."
        )
    if brief.tone == "humorous" and (review.humour_score or 0) < config.min_humour_score:
        problems.append(
            f'Editor scored "actually funny and still professional" {review.humour_score}/10; '
            f"needs {config.min_humour_score}."
        )
    if review.fact_problems:
        # Unsupported claims (above all invented personal stories) are a hard rule, so a good
        # score isn't enough: the next round must come back clean.
        problems.append(
            f"Editor found {len(review.fact_problems)} unsupported claim(s); "
            "the next version must come back with none."
        )
    return problems


def choose_candidate(
    draft: str, review: EditorReview, limits: LintLimits, recent: Sequence[str]
) -> tuple[str, LintResult]:
    """Prefer the editor's rewrite; use the draft only if it lints clean and the rewrite doesn't."""
    candidates = [t for t in (review.rewrite, draft) if t]
    results = [(text, lint_post(text, limits, recent)) for text in candidates]
    for text, result in results:
        if result.passed:
            return text, result
    return results[0]


def build_feedback(attempt: Attempt) -> str:
    """Everything the writer needs to fix (score gates, lint failures, editor notes)."""
    items = dict.fromkeys([*attempt.problems, *attempt.review.feedback()])
    return "\n".join(f"- {item}" for item in items)


def generate_post(llm: LLM, config: Config, brief: PostBrief) -> GenerationResult:
    """Run up to `max_attempts` rounds and stop at the first one that passes."""
    limits = LintLimits.from_config(config)
    result = GenerationResult()
    feedback: str | None = None
    previous: str | None = None
    for number in range(1, config.max_attempts + 1):
        draft = write_draft(llm, config, brief, feedback=feedback, previous=previous)
        review = edit_draft(llm, config, brief, draft)
        text, lint = choose_candidate(draft, review, limits, brief.recent_posts)
        problems = score_problems(review, brief, config)
        problems += [f"Lint [{i.rule}]: {i.detail}" for i in lint.issues]
        attempt = Attempt(number, draft, review, text, lint, problems)
        result.attempts.append(attempt)
        log_event(
            logger,
            "generate.attempt",
            attempt=number,
            human_score=review.human_score,
            humour_score=review.humour_score,
            lint_issues=len(lint.issues),
            words=lint.word_count,
            passed=attempt.passed,
        )
        if attempt.passed:
            break
        feedback = build_feedback(attempt)
        previous = text
    return result
