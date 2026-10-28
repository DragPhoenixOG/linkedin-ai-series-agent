"""Small text helpers shared by topics, news de-duplication and the post archive."""

from __future__ import annotations

import re
import unicodedata

_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "from", "at",
    "is", "are", "its", "it", "as", "into", "how", "why", "what", "new", "now", "your", "our",
}  # fmt: skip


def slugify(text: str, max_len: int = 60) -> str:
    """Lowercase ASCII slug: 'MCP: build once!' -> 'mcp-build-once'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "post"


def significant_tokens(text: str) -> set[str]:
    """Lowercase content words, for rough similarity checks."""
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS}


def jaccard(a: str, b: str) -> float:
    """Jaccard similarity of the significant tokens in two strings (0.0 to 1.0)."""
    ta, tb = significant_tokens(a), significant_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def domain_of(url: str) -> str:
    """Registrable-ish domain of a URL: 'https://www.blog.example.com/x' -> 'example.com'."""
    host = re.sub(r"^[a-z]+://", "", url.strip().lower()).split("/", 1)[0].split(":", 1)[0]
    host = host.removeprefix("www.")
    parts = host.split(".")
    if len(parts) > 2 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:  # e.g. bbc.co.uk
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])
