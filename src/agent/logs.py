"""Structured logging: one `event key=value ...` line per step, easy to grep in Actions logs."""

from __future__ import annotations

import logging
import sys
from typing import Any


def setup_logging(level: str = "INFO") -> None:
    """Send logs to stderr so dry-run output on stdout stays clean."""
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )
    # The SDK's HTTP client logs every request at INFO; keep the run log readable.
    for noisy in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _fmt(value: Any) -> str:
    text = str(value)
    if not text or any(c.isspace() for c in text) or '"' in text:
        return '"' + text.replace('"', '\\"') + '"'
    return text


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log `event` with sorted key=value fields, e.g. `research.done developments=2`."""
    parts = [event, *(f"{k}={_fmt(v)}" for k, v in fields.items())]
    logger.log(level, " ".join(parts))
