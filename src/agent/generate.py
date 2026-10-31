"""The write -> edit -> lint loop with retries (CLAUDE.md section 4, "Two-pass generation").

Each attempt: the writer drafts (or fixes the flagged sentences of the last version), the editor
scores that exact text and lists its problems, and the deterministic lint checks it. A post
passes when the editor's scores clear the bar and lint is clean. Otherwise the problems are fed
back and the writer tries again, up to `max_attempts`. If nothing passes, the caller gets the best
attempt and the reasons, and must not publish.

Only text the editor has scored and lint has passed can be posted. The editor also returns a
rewrite, but it is kept for reference only: nothing checks it after it's written, and editors
have slipped invented stories and wrong facts into rewrites.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent.config import Config
from agent.editor import EditorReview, edit_draft
from agent.lint import LintLimits, LintResult, lint_post
from agent.llm import LLM
from agent.logs import log_event
from agent.writer import PostBrief, fix_sentences, write_draft

logger = logging.getLogger(__name__)


@dataclass
class Attempt:
    """One write -> edit -> lint round."""

    number: int
    text: str  # the writer's version: the exact text the editor scored and lint checked
    review: EditorReview
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
        return ["The editor returned an incomplete review (no scores)."]
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


def build_feedback(attempt: Attempt) -> str:
    """Everything the writer needs to fix (score gates, lint failures, editor notes)."""
    items = dict.fromkeys([*attempt.problems, *attempt.review.feedback()])
    return "\n".join(f"- {item}" for item in items)


# Sentence-fix rounds allowed on top of `max_attempts` full drafts, so a post that is one
# sentence away from passing always gets its repair, even after the last full draft.
MAX_FIX_ROUNDS = 2


def generate_post(llm: LLM, config: Config, brief: PostBrief) -> GenerationResult:
    """Up to `max_attempts` full drafts (plus sentence-fix rounds); stop at the first pass."""
    limits = LintLimits.from_config(config)
    result = GenerationResult()
    feedback: str | None = None
    previous: str | None = None
    fixed: str | None = None
    drafts = fixes = 0
    while True:
        if fixed:
            text, fixes = fixed, fixes + 1
        else:
            text = write_draft(llm, config, brief, feedback=feedback, previous=previous)
            drafts += 1
        number = len(result.attempts) + 1
        lint = lint_post(text, limits, brief.recent_posts)
        review = edit_draft(llm, config, brief, text, checks=lint)
        problems = score_problems(review, brief, config)
        problems += [f"Lint [{i.rule}]: {i.detail}" for i in lint.issues]
        attempt = Attempt(number, text, review, lint, problems)
        result.attempts.append(attempt)
        log_event(
            logger,
            "generate.attempt",
            attempt=number,
            kind="fix" if fixed else "draft",
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
        fixed = None
        if fixes < MAX_FIX_ROUNDS and only_sentence_problems(attempt, brief, config):
            # Scores already pass: change just the flagged sentences rather than redraft, so the
            # rest of a nearly-there post can't pick up new problems.
            fixed = fix_sentences(llm, config, brief, text, sentence_problems(attempt))
        if not fixed and drafts >= config.max_attempts:
            break
    return result


# Lint rules whose problems sit in one sentence and can be fixed there.
LOCAL_RULES = {"banned_phrase", "em_dash", "not_x_its_y", "repeated_sentence_start", "triad"}


def only_sentence_problems(attempt: Attempt, brief: PostBrief, config: Config) -> bool:
    """True when the editor's scores pass and every remaining problem is in specific sentences."""
    review = attempt.review
    if not review.is_complete(brief.tone == "humorous"):
        return False
    if review.human_score < config.min_editor_score:
        return False
    if brief.tone == "humorous" and (review.humour_score or 0) < config.min_humour_score:
        return False
    return all(i.rule in LOCAL_RULES for i in attempt.lint.issues)


def sentence_problems(attempt: Attempt) -> list[str]:
    """The lint issues and fact problems to fix sentence by sentence."""
    items = [f"Lint [{i.rule}]: {i.detail}" for i in attempt.lint.issues]
    items += [f"Unsupported claim: {p}" for p in attempt.review.fact_problems]
    return items
