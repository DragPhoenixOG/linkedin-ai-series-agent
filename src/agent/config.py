"""Load `config.yaml` into a typed, read-only settings object."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Config:
    """All tunable settings. Defaults mirror the documented `config.yaml` defaults."""

    post_interval_days: int = 3
    post_time_utc: str = "03:30"

    llm_provider: str = "gemini"
    writer_model: str = "gemini-3.6-flash"
    editor_model: str = "gemini-3.6-flash"
    research_model: str = "gemini-3.5-flash-lite"
    llm_base_url: str = ""
    llm_api_key_env: str = ""
    max_output_tokens: int = 8192
    fallback_model: str = "gemini-3.5-flash-lite"
    aws_region: str = "us-east-1"
    llm_timeout_seconds: int = 90
    writer_effort: str = "high"
    editor_effort: str = "high"
    research_effort: str = "medium"
    refusal_fallback: bool = True
    pricing: dict[str, Any] = field(default_factory=dict)

    research_lookback_days: int = 45
    max_articles_to_read: int = 6
    article_max_chars: int = 5000
    fetch_timeout_seconds: int = 15

    min_words: int = 120
    max_words: int = 250
    max_hashtags: int = 3
    max_emojis: int = 1
    max_em_dashes: int = 1
    max_bullets: int = 4
    max_links: int = 1

    min_editor_score: int = 8
    min_humour_score: int = 7
    max_attempts: int = 5

    news_post_ratio: float = 0.33
    news_lookback_days: int = 7
    news_recent_window: int = 10
    news_max_headlines: int = 60
    big_news_check: bool = True
    humour_ratio: float = 0.33

    auto_extend_topics: bool = True
    min_pending_topics: int = 3
    new_topics_per_extension: int = 5

    review_window_hours: int = 0
    linkedin_api_version: str = "202610"
    token_warning_days: int = 7
    dry_run: bool = False


def load_config(path: Path) -> Config:
    """Read a YAML config file. Unknown keys are rejected so typos fail loudly."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    known = {f.name for f in fields(Config)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"Unknown config keys in {path}: {', '.join(unknown)}")
    return Config(**raw)
