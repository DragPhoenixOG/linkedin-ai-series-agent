"""Entry point: decide, research, write, check and (eventually) publish one post.

Usage:
    python -m agent.main --dry-run --force
    python -m agent.main --dry-run --force --post-type news --tone humorous

`--dry-run` prints the final post plus lint and editor results. It never posts, commits, or
writes to `state/`, `content/` or `posts/`.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TextIO

from dotenv import load_dotenv

from agent import mix, news, research, scheduler, topics
from agent.config import Config, load_config
from agent.generate import GenerationResult, generate_post
from agent.llm import LLM, LLMError
from agent.logs import log_event, setup_logging
from agent.state import History, NewsStory, Source, load_history
from agent.writer import PostBrief, load_voice

logger = logging.getLogger("agent")

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


@dataclass
class Plan:
    """What today's post is about, before any writing happens."""

    post_type: str
    tone: str
    format: mix.Format
    topic: topics.Topic | None = None
    story: NewsStory | None = None
    findings: research.Research = field(default_factory=research.Research)
    notes: list[str] = field(default_factory=list)  # decisions worth showing in the report

    @property
    def research_notes(self) -> str:
        return news.story_notes(self.story) if self.story else self.findings.notes()

    @property
    def sources(self) -> list[Source]:
        return self.story.sources if self.story else self.findings.sources

    @property
    def title(self) -> str:
        if self.story:
            return self.story.title
        return self.topic.title if self.topic else "(none)"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="linkedin-agent", description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the post, don't publish")
    parser.add_argument("--force", action="store_true", help="ignore the posting interval")
    parser.add_argument("--topic-id", help="write about this topic id (implies a skill post)")
    parser.add_argument("--post-type", choices=[mix.SKILL, mix.NEWS], help="override post type")
    parser.add_argument("--tone", choices=[mix.STRAIGHT, mix.HUMOROUS], help="override tone")
    parser.add_argument("--format", choices=sorted(mix.FORMATS), help="override post format")
    parser.add_argument("--date", type=date.fromisoformat, help="pretend today is YYYY-MM-DD")
    parser.add_argument("--seed", type=int, help="random seed for the content mix")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--output", type=Path, help="also write the dry-run report to this file")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    if args.topic_id and args.post_type == mix.NEWS:
        parser.error("--topic-id only applies to skill posts")
    return args


def plan_post(
    llm: LLM,
    config: Config,
    history: History,
    backlog: list[topics.Topic],
    today: date,
    rng: random.Random,
    args: argparse.Namespace,
) -> Plan:
    """Decide type, tone, format and subject, and do the research."""
    recent = history.recent(max(config.news_recent_window, 9))
    recent_types = [p.post_type for p in recent]
    recent_tones = [p.tone for p in recent]
    override_type = mix.SKILL if args.topic_id else args.post_type
    notes = [
        f"mix override: {w}"
        for w in mix.check_overrides(recent_types, recent_tones, override_type, args.tone)
    ]

    post_type = override_type or mix.decide_post_type(recent_types, config.news_post_ratio, rng)
    story: NewsStory | None = None
    if post_type == mix.NEWS:
        story = news.pick_story(llm, config, today, history)
        if story is None:
            post_type = mix.SKILL
            notes.append(
                "no news story qualified (fresh, verified, not covered): fell back to a skill post"
            )
    elif (
        override_type is None
        and config.big_news_check
        and recent_types
        and recent_types[0] != mix.NEWS
    ):
        story = news.pick_story(llm, config, today, history, require_major=True)
        if story:
            post_type = mix.NEWS
            notes.append("a major verified story jumped the queue")

    tone = args.tone or mix.decide_tone(recent_tones, config.humour_ratio, rng)
    fmt = (
        mix.FORMATS[args.format]
        if args.format
        else mix.choose_format([p.format for p in recent], post_type, tone, rng)
    )
    plan = Plan(post_type=post_type, tone=tone, format=fmt, story=story, notes=notes)

    if post_type == mix.SKILL:
        if not topics.pending_topics(backlog) and not args.topic_id:
            # Only when the backlog is completely empty. Normal top-ups (fed with this run's
            # research) happen after a post is published, so dry runs never trigger them.
            topics.ensure_backlog(
                backlog,
                enabled=config.auto_extend_topics,
                min_pending=1,
                propose=lambda: topics.propose_topics(
                    llm, config, backlog, [p.title for p in history.posts], "", today
                ),
                covered_titles=[p.title for p in history.posts],
            )
        plan.topic = topics.next_topic(backlog, args.topic_id)
        if plan.topic is None:
            raise topics.BacklogError("No pending topics left. Add topics to content/topics.yaml.")
        plan.findings = research.research_topic(
            llm, config, plan.topic.title, plan.topic.angle, today
        )
    return plan


def build_brief(plan: Plan, history: History, root: Path, today: date) -> PostBrief:
    """Turn a plan into the brief the writer and editor share."""
    return PostBrief(
        post_type=plan.post_type,
        tone=plan.tone,
        format_id=plan.format.id,
        format_name=plan.format.name,
        format_description=plan.format.description,
        today=today,
        voice=load_voice(root / "content"),
        research_notes=plan.research_notes,
        topic_title=plan.topic.title if plan.topic else "",
        topic_angle=plan.topic.angle if plan.topic else "",
        story=plan.story,
        recent_posts=history.recent_texts(5),
    )


def backlog_after_post(plan: Plan, backlog: list[topics.Topic], config: Config) -> str:
    """Describe what the backlog top-up would do once this post is published."""
    pending = len(topics.pending_topics(backlog)) - (
        1 if plan.topic and plan.topic.status == "pending" else 0
    )
    if pending >= config.min_pending_topics:
        return f"{pending} topics pending after this post; no top-up needed"
    action = (
        "would ask Claude for" if config.auto_extend_topics else "auto-extend is off; would NOT add"
    )
    count = config.new_topics_per_extension
    return f"{pending} topics pending after this post; {action} {count} new topics"


def render_report(
    plan: Plan, result: GenerationResult, today: date, reason: str, backlog_note: str, usage: str
) -> str:
    """The dry-run report: decisions, research, scores, lint, and the post itself."""
    best = result.best
    lines = [
        "=" * 72,
        "DRY RUN: nothing was posted, committed or written to state",
        "=" * 72,
        f"Date:     {today.isoformat()} ({reason})",
        f"Type:     {plan.post_type}",
        f"Tone:     {plan.tone}",
        f"Format:   {plan.format.name}",
        f"Subject:  {plan.title}",
    ]
    lines += [f"Note:     {n}" for n in plan.notes]
    lines.append("")
    lines.append("Research used:")
    lines += [f"  {line}" for line in plan.research_notes.splitlines()]
    lines.append("")
    status = (
        f"PASSED on attempt {best.number} of {len(result.attempts)}"
        if result.success
        else f"FAILED after {len(result.attempts)} attempts (would open a needs-attention issue)"
    )
    humour = (
        f", humour {best.review.humour_score}/10" if best.review.humour_score is not None else ""
    )
    lines += [
        f"Result:   {status}",
        f"Editor:   human {best.review.human_score}/10{humour}. {best.review.verdict}",
        f"Lint:     {best.lint.summary() if best.lint.passed else 'FAILED'}",
    ]
    if not best.lint.passed:
        lines += [f"  {line}" for line in best.lint.summary().splitlines()]
    lines += ["", "-" * 30 + " POST " + "-" * 36, best.text, "-" * 72, ""]
    lines.append("Attempts:")
    for attempt in result.attempts:
        scores = f"human {attempt.review.human_score}"
        if attempt.review.humour_score is not None:
            scores += f", humour {attempt.review.humour_score}"
        verdict = "pass" if attempt.passed else "; ".join(attempt.problems)
        lines.append(f"  {attempt.number}. {scores}, {attempt.lint.word_count} words: {verdict}")
        for item in attempt.review.feedback()[:6]:
            lines.append(f"       editor: {item}")
    lines += ["", f"Backlog:  {backlog_note}", f"Usage:    {usage}", ""]
    return "\n".join(lines)


def run(
    args: argparse.Namespace,
    llm_factory: Callable[[Config], LLM] = LLM.from_env,
    out: TextIO = sys.stdout,
) -> int:
    """Run the agent once. Returns a process exit code."""
    root: Path = args.root
    load_dotenv(root / ".env")
    config = load_config(root / "config.yaml")
    dry_run = args.dry_run or config.dry_run
    today = args.date or datetime.now(UTC).date()
    history = load_history(root / "state" / "history.json")
    backlog = topics.load_topics(root / "content" / "topics.yaml")

    last = history.last_post()
    decision = scheduler.decide(
        today, last.posted_on if last else None, config.post_interval_days, force=args.force
    )
    log_event(logger, "schedule", should_post=decision.should_post, reason=decision.reason)
    if not decision.should_post:
        log_event(logger, "run.summary", status="skipped", reason=decision.reason)
        return EXIT_OK
    if not dry_run:
        log_event(
            logger,
            "run.summary",
            logging.ERROR,
            status="not_implemented",
            reason="LinkedIn publishing arrives in build step 6; run with --dry-run",
        )
        return EXIT_USAGE

    try:
        llm = llm_factory(config)
    except LLMError as exc:
        log_event(logger, "run.summary", logging.ERROR, status="failed", error=exc)
        return EXIT_USAGE
    rng = random.Random(args.seed if args.seed is not None else int(today.strftime("%Y%m%d")))
    try:
        plan = plan_post(llm, config, history, backlog, today, rng, args)
        log_event(
            logger,
            "plan",
            type=plan.post_type,
            tone=plan.tone,
            format=plan.format.id,
            subject=plan.title,
        )
        result = generate_post(llm, config, build_brief(plan, history, root, today))
    except (LLMError, topics.BacklogError, KeyError) as exc:
        log_event(
            logger,
            "run.summary",
            logging.ERROR,
            status="failed",
            error=exc,
            usage=llm.usage.summary(),
        )
        return EXIT_FAILED

    report = render_report(
        plan,
        result,
        today,
        decision.reason,
        backlog_after_post(plan, backlog, config),
        llm.usage.summary(),
    )
    out.write(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    best = result.best
    log_event(
        logger,
        "run.summary",
        status="dry_run_ok" if result.success else "dry_run_failed",
        type=plan.post_type,
        tone=plan.tone,
        format=plan.format.id,
        attempts=len(result.attempts),
        human=best.review.human_score,
        humour=best.review.humour_score if best.review.humour_score is not None else "-",
        lint="pass" if best.lint.passed else "fail",
        words=best.lint.word_count,
        calls=llm.usage.calls,
        searches=llm.usage.web_searches,
        est_cost_usd=f"{llm.usage.cost_usd:.3f}",
    )
    return EXIT_OK if result.success else EXIT_FAILED


def cli(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    return run(args)


if __name__ == "__main__":
    sys.exit(cli())
