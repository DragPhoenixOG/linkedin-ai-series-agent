"""Entry point: decide, research, write, check and publish one post.

Usage:
    python -m agent.main                      # what the daily workflow runs
    python -m agent.main --dry-run --force
    python -m agent.main --dry-run --force --post-type news --tone humorous

`--dry-run` prints the final post plus lint and editor results. It never posts, opens issues,
commits, or writes to `state/`, `content/` or `posts/`.
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TextIO

from dotenv import load_dotenv

from agent import mix, news, notify, research, scheduler, templates, topics
from agent.config import Config, load_config
from agent.generate import GenerationResult, generate_post
from agent.linkedin import (
    LinkedInAuthError,
    LinkedInClient,
    LinkedInError,
    PostContent,
    token_status,
)
from agent.llm import (
    LLM,
    AnthropicLLM,
    LLMError,
    OpenAICompatLLM,
    create_llm,
    provider_settings,
)
from agent.logs import log_event, setup_logging
from agent.notify import Notifier, NotifyError
from agent.series import load_series
from agent.sources import SourceClient, load_source_settings
from agent.state import (
    History,
    NewsStory,
    PendingDraft,
    PostRecord,
    Source,
    archive_post,
    load_history,
    make_slug,
    save_history,
)
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
    parser.add_argument(
        "--check", action="store_true", help="check API key, models and feeds, then exit"
    )
    args = parser.parse_args(argv)
    if args.topic_id and args.post_type == mix.NEWS:
        parser.error("--topic-id only applies to skill posts")
    return args


def plan_post(
    llm: LLM,
    sources: SourceClient,
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
        story = news.pick_story(llm, config, sources, today, history)
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
        story = news.pick_story(llm, config, sources, today, history, require_major=True)
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
            llm, config, sources, plan.topic.title, plan.topic.angle, today
        )
    return plan


def build_brief(plan: Plan, history: History, root: Path, today: date) -> PostBrief:
    """Turn a plan into the brief the writer and editor share."""
    return PostBrief(
        post_type=plan.post_type,
        tone=plan.tone,
        format_id=plan.format.id,
        format_name=plan.format.name,
        format_description=templates.fill(plan.format.description),
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


def linkedin_from_env(config: Config) -> LinkedInClient:
    """LinkedIn client from `LINKEDIN_ACCESS_TOKEN` and `LINKEDIN_PERSON_URN`."""
    return LinkedInClient(
        os.environ.get("LINKEDIN_ACCESS_TOKEN", ""),
        os.environ.get("LINKEDIN_PERSON_URN", ""),
        config.linkedin_api_version,
    )


def sources_from_config(config: Config, root: Path) -> SourceClient:
    """Feeds and Hacker News, as listed in `content/feeds.yaml`."""
    return SourceClient(
        load_source_settings(root / "content" / "feeds.yaml"),
        timeout=config.fetch_timeout_seconds,
        max_chars=config.article_max_chars,
    )


@dataclass
class Services:
    """External systems, swappable in tests."""

    llm_factory: Callable[[Config], LLM] = create_llm
    linkedin_factory: Callable[[Config], LinkedInClient] = linkedin_from_env
    notifier_factory: Callable[[], Notifier] = notify.notifier_from_env
    sources_factory: Callable[[Config, Path], SourceClient] = sources_from_config


@dataclass
class Context:
    """Everything one run works with."""

    args: argparse.Namespace
    config: Config
    root: Path
    today: date
    now: datetime
    history: History
    backlog: list[topics.Topic]
    services: Services
    llm: LLM | None = None
    sources: SourceClient | None = None

    @property
    def history_path(self) -> Path:
        return self.root / "state" / "history.json"

    @property
    def topics_path(self) -> Path:
        return self.root / "content" / "topics.yaml"

    def get_llm(self) -> LLM:
        if self.llm is None:
            self.llm = self.services.llm_factory(self.config)
        return self.llm

    def get_sources(self) -> SourceClient:
        if self.sources is None:
            self.sources = self.services.sources_factory(self.config, self.root)
        return self.sources

    def usage(self) -> str:
        return self.llm.usage.summary() if self.llm else "calls=0"


def make_record(plan: Plan, result: GenerationResult, today: date) -> PostRecord:
    """The history entry for the chosen post (before it has a LinkedIn URN)."""
    best = result.best
    return PostRecord(
        date=today.isoformat(),
        post_type=plan.post_type,
        tone=plan.tone,
        format=plan.format.id,
        text=best.text,
        slug=make_slug(plan.title),
        topic_id=plan.topic.id if plan.topic else None,
        topic_title=plan.topic.title if plan.topic else None,
        news_story=plan.story,
        sources=plan.sources,
        editor_score=best.review.human_score,
        humour_score=best.review.humour_score,
        attempts=len(result.attempts),
    )


def write_outputs(**values: str) -> None:
    """Expose values to later workflow steps (GitHub Actions `GITHUB_OUTPUT`)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def safely(action: Callable[..., Any], *args: Any) -> Any:
    """Run a notification; a GitHub hiccup must never undo or block a published post."""
    try:
        return action(*args)
    except NotifyError as exc:
        log_event(logger, "notify.failed", logging.WARNING, error=exc)
        return None


def check_token_expiry(ctx: Context, notifier: Notifier) -> None:
    """Every live run: open or update the reminder issue when the token is about to expire."""
    status = token_status(
        os.environ.get("LINKEDIN_TOKEN_EXPIRES_ON"), ctx.today, ctx.config.token_warning_days
    )
    if status is None:
        log_event(
            logger,
            "token.unknown",
            logging.WARNING,
            reason="LINKEDIN_TOKEN_EXPIRES_ON is not set (YYYY-MM-DD); no expiry reminders",
        )
        return
    log_event(logger, "token", expires_on=status.expires_on, days_left=status.days_left)
    if status.warn:
        safely(notify.remind_token_expiry, notifier, status)


def top_up_backlog(ctx: Context, notifier: Notifier, research_notes: str) -> None:
    """After a post: ask Claude for new topics if fewer than `min_pending_topics` remain."""
    if not topics.needs_extension(ctx.backlog, ctx.config.min_pending_topics):
        return
    covered = [p.title for p in ctx.history.posts]
    try:
        llm = ctx.get_llm()
        topics.ensure_backlog(
            ctx.backlog,
            enabled=ctx.config.auto_extend_topics,
            min_pending=ctx.config.min_pending_topics,
            propose=lambda: topics.propose_topics(
                llm, ctx.config, ctx.backlog, covered, research_notes, ctx.today
            ),
            covered_titles=covered,
        )
    except (topics.BacklogError, LLMError) as exc:
        safely(
            notify.notify_failure,
            notifier,
            f"Topic backlog needs attention ({ctx.today.isoformat()})",
            [str(exc), "Add topics to content/topics.yaml or check the model API key."],
        )


def publish_record(
    ctx: Context,
    record: PostRecord,
    research_notes: str,
    linkedin: LinkedInClient,
    notifier: Notifier,
    scheduled_issue: int | None = None,
) -> int:
    """Post to LinkedIn, then record it everywhere. State is saved before anything optional."""
    record.date = ctx.today.isoformat()
    try:
        post = linkedin.create_post(PostContent(record.text))
    except LinkedInAuthError as exc:
        safely(notify.notify_token_failure, notifier, str(exc), ctx.today)
        log_event(logger, "run.summary", logging.ERROR, status="failed", error=exc)
        return EXIT_FAILED
    except LinkedInError as exc:
        safely(
            notify.notify_failure,
            notifier,
            f"LinkedIn rejected the post: {record.title} ({record.date})",
            [str(exc)],
            record,
        )
        log_event(logger, "run.summary", logging.ERROR, status="failed", error=exc)
        return EXIT_FAILED

    record.urn, record.linkedin_url = post.urn, post.url
    ctx.history.posts.append(record)
    ctx.history.pending = None
    save_history(ctx.history, ctx.history_path)  # first, so a later failure can't cause a repost
    archive_post(record, ctx.root / "posts")
    if record.topic_id:
        topics.mark_posted(ctx.backlog, record.topic_id, ctx.today, post.url)
    top_up_backlog(ctx, notifier, research_notes)
    topics.save_topics(ctx.backlog, ctx.topics_path)
    safely(notify.notify_posted, notifier, record)
    if scheduled_issue is not None:
        safely(notifier.comment_and_close, scheduled_issue, f"Published: {post.url}")
    write_outputs(slug=record.slug, commit_message=f"chore: post {record.slug}", url=post.url)
    log_event(
        logger,
        "run.summary",
        status="posted",
        type=record.post_type,
        tone=record.tone,
        format=record.format,
        human=record.editor_score,
        url=post.url,
        usage=ctx.usage(),
    )
    return EXIT_OK


def handle_pending(ctx: Context, linkedin: LinkedInClient, notifier: Notifier) -> int | None:
    """Deal with a draft in the review window. Returns an exit code, or None to carry on."""
    pending = ctx.history.pending
    assert pending is not None
    try:
        rejected = notify.draft_rejected(notifier, pending.issue_number)
    except NotifyError as exc:
        log_event(logger, "run.summary", logging.WARNING, status="waiting", error=exc)
        return EXIT_OK  # can't confirm it wasn't rejected: don't publish
    if rejected:
        ctx.history.pending = None
        save_history(ctx.history, ctx.history_path)
        write_outputs(commit_message=f"chore: drop rejected draft {pending.record.slug}")
        log_event(logger, "review.rejected", slug=pending.record.slug)
        return None
    publish_after = datetime.fromisoformat(pending.publish_after)
    if ctx.now < publish_after:
        log_event(logger, "run.summary", status="waiting", publish_after=pending.publish_after)
        return EXIT_OK
    return publish_record(
        ctx, pending.record, pending.research_notes, linkedin, notifier, pending.issue_number
    )


def generate(ctx: Context) -> tuple[Plan, GenerationResult]:
    """Plan and write today's post. Raises LLMError/BacklogError/KeyError on failure."""
    llm = ctx.get_llm()
    seed = ctx.args.seed if ctx.args.seed is not None else int(ctx.today.strftime("%Y%m%d"))
    plan = plan_post(
        llm,
        ctx.get_sources(),
        ctx.config,
        ctx.history,
        ctx.backlog,
        ctx.today,
        random.Random(seed),
        ctx.args,
    )
    log_event(
        logger,
        "plan",
        type=plan.post_type,
        tone=plan.tone,
        format=plan.format.id,
        subject=plan.title,
    )
    result = generate_post(llm, ctx.config, build_brief(plan, ctx.history, ctx.root, ctx.today))
    return plan, result


def dry_run(ctx: Context, out: TextIO) -> int:
    """Write a post and print the report. No posting, no issues, no state changes."""
    decision = _schedule(ctx)
    if not decision.should_post:
        return EXIT_OK
    try:
        plan, result = generate(ctx)
    except (LLMError, topics.BacklogError, KeyError) as exc:
        log_event(
            logger, "run.summary", logging.ERROR, status="failed", error=exc, usage=ctx.usage()
        )
        return EXIT_FAILED
    report = render_report(
        plan,
        result,
        ctx.today,
        decision.reason,
        backlog_after_post(plan, ctx.backlog, ctx.config),
        ctx.usage(),
    )
    out.write(report)
    if ctx.args.output:
        ctx.args.output.parent.mkdir(parents=True, exist_ok=True)
        ctx.args.output.write_text(report, encoding="utf-8")
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
        usage=ctx.usage(),
    )
    return EXIT_OK if result.success else EXIT_FAILED


def live_run(ctx: Context) -> int:
    """The real thing: check the token, handle any scheduled draft, write and publish."""
    notifier = ctx.services.notifier_factory()
    check_token_expiry(ctx, notifier)
    try:
        linkedin = ctx.services.linkedin_factory(ctx.config)
    except LinkedInError as exc:
        safely(
            notify.notify_failure,
            notifier,
            f"LinkedIn settings missing or invalid ({ctx.today.isoformat()})",
            [str(exc)],
        )
        log_event(logger, "run.summary", logging.ERROR, status="failed", error=exc)
        return EXIT_FAILED

    if ctx.history.pending:
        outcome = handle_pending(ctx, linkedin, notifier)
        if outcome is not None:
            return outcome

    decision = _schedule(ctx)
    if not decision.should_post:
        return EXIT_OK
    try:
        plan, result = generate(ctx)
    except (LLMError, topics.BacklogError, KeyError) as exc:
        safely(
            notify.notify_failure,
            notifier,
            f"Post needs attention ({ctx.today.isoformat()})",
            [f"{type(exc).__name__}: {exc}"],
        )
        log_event(
            logger, "run.summary", logging.ERROR, status="failed", error=exc, usage=ctx.usage()
        )
        return EXIT_FAILED

    record = make_record(plan, result, ctx.today)
    if not result.success:
        safely(
            notify.notify_failure,
            notifier,
            f"Post needs attention: {plan.title} ({ctx.today.isoformat()})",
            [
                f"Failed quality checks after {len(result.attempts)} attempts.",
                *result.best.problems,
            ],
            record,
        )
        log_event(logger, "run.summary", logging.ERROR, status="quality_failed", usage=ctx.usage())
        return EXIT_FAILED

    if ctx.config.review_window_hours > 0:
        publish_after = ctx.now + timedelta(hours=ctx.config.review_window_hours)
        issue = safely(notify.notify_scheduled, notifier, record, publish_after.isoformat())
        ctx.history.pending = PendingDraft(
            record=record,
            created_at=ctx.now.isoformat(),
            publish_after=publish_after.isoformat(),
            issue_number=issue,
            research_notes=plan.research_notes,
        )
        save_history(ctx.history, ctx.history_path)
        write_outputs(slug=record.slug, commit_message=f"chore: schedule {record.slug}")
        log_event(
            logger, "run.summary", status="scheduled", publish_after=publish_after.isoformat()
        )
        return EXIT_OK
    return publish_record(ctx, record, plan.research_notes, linkedin, notifier)


def _schedule(ctx: Context) -> scheduler.ScheduleDecision:
    last = ctx.history.last_post()
    decision = scheduler.decide(
        ctx.today,
        last.posted_on if last else None,
        ctx.config.post_interval_days,
        force=ctx.args.force,
    )
    log_event(logger, "schedule", should_post=decision.should_post, reason=decision.reason)
    if not decision.should_post:
        log_event(logger, "run.summary", status="skipped", reason=decision.reason)
    return decision


def run(
    args: argparse.Namespace,
    services: Services | None = None,
    out: TextIO = sys.stdout,
) -> int:
    """Run the agent once. Returns a process exit code."""
    root: Path = args.root
    load_dotenv(root / ".env")
    config = load_config(root / "config.yaml")
    templates.use_series(load_series(root / "content"))
    if args.date:
        today, now = args.date, datetime.combine(args.date, time(12, 0), tzinfo=UTC)
    else:
        now = datetime.now(UTC)
        today = now.date()
    ctx = Context(
        args=args,
        config=config,
        root=root,
        today=today,
        now=now,
        history=load_history(root / "state" / "history.json"),
        backlog=topics.load_topics(root / "content" / "topics.yaml"),
        services=services or Services(),
    )
    try:
        if args.check:
            return check(ctx, out)
        if args.dry_run or config.dry_run:
            return dry_run(ctx, out)
        return live_run(ctx)
    except LLMError as exc:  # e.g. no API key: nothing else can happen this run
        log_event(logger, "run.summary", logging.ERROR, status="failed", error=exc)
        return EXIT_USAGE


def check(ctx: Context, out: TextIO) -> int:
    """Setup check: API key, model names and feeds. Changes nothing."""
    config = ctx.config
    _, key_env = provider_settings(config)
    lines = [
        f"Series: {templates.setting('series_theme')}, by {templates.setting('author_name')}",
        f"Provider: {config.llm_provider} (key in {key_env})",
    ]
    problems = 0
    try:
        llm = ctx.services.llm_factory(config)
        lines.append(f"  API key: found in {key_env}")
    except LLMError as exc:
        llm = None
        problems += 1
        lines.append(f"  API key: PROBLEM: {exc}")
    models = {config.writer_model, config.editor_model, config.research_model}
    if config.fallback_model and config.llm_provider != "anthropic":
        models.add(config.fallback_model)
    if isinstance(llm, AnthropicLLM):
        for model in sorted(models):
            error = llm.check_model(model)
            problems += 1 if error else 0
            lines.append(f"  model {model}: {'available' if not error else f'PROBLEM: {error}'}")
    elif isinstance(llm, OpenAICompatLLM):
        try:
            available = set(llm.list_models())
            for model in sorted(models):
                found = model in available
                problems += 0 if found else 1
                lines.append(
                    f"  model {model}: {'available' if found else 'NOT FOUND for this key'}"
                )
            lines.append(f"  available models: {', '.join(sorted(available)) or '(none)'}")
        except LLMError as exc:
            problems += 1
            lines.append(f"  models: could not list ({exc})")
    else:
        reason = "no API key" if llm is None else "not checked for this provider"
        lines.append(f"  models: {', '.join(sorted(models))} ({reason})")

    sources = ctx.get_sources()
    sources.all_feed_items()
    lines.append("Feeds:")
    for name, status in sources.feed_status.items():
        lines.append(f"  {name}: {status}")
    reachable = sum(1 for s in sources.feed_status.values() if s != "unreachable")
    hn = sources.search_hn("AI", ctx.today - timedelta(days=7), 100, limit=5)
    lines.append(f"Hacker News search: {len(hn)} results for 'AI' in the last 7 days")
    if not reachable and not hn:
        problems += 1
        lines.append("  PROBLEM: no research source is reachable")
    lines.append("All good." if not problems else f"{problems} problem(s) found.")
    out.write("\n".join(lines) + "\n")
    return EXIT_OK if not problems else EXIT_USAGE


def cli(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    return run(args)


if __name__ == "__main__":
    sys.exit(cli())
