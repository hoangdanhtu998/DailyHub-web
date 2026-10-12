"""Turn raw source data into resource records and classify them."""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

# Topic -> resource kind. The first match in this order wins.
DEFAULT_TOPIC_KINDS = {
    "agent-skills": "skill", "claude-skill": "skill", "claude-skills": "skill",
    "mcp": "toolkit", "mcp-server": "toolkit", "mcp-servers": "toolkit", "rag": "toolkit",
    "agent-framework": "toolkit", "context-engineering": "toolkit", "agent-memory": "toolkit",
    "local-llm": "toolkit", "ollama": "toolkit", "llm-tools": "toolkit",
    "ai-agents": "repo", "llm": "repo", "machine-learning": "repo",
}
KIND_PRIORITY = ["skill", "toolkit", "repo"]


def classify(topics: list[str], topic_kinds: dict[str, str] | None = None) -> str:
    mapping = topic_kinds or DEFAULT_TOPIC_KINDS
    found = {mapping[t] for t in (x.lower() for x in topics) if t in mapping}
    for kind in KIND_PRIORITY:
        if kind in found:
            return kind
    return "repo"


def clean_text(value, limit: int = 400) -> str:
    """Plain text only: strip HTML tags, decode entities, drop control characters, collapse spaces."""
    if not isinstance(value, str):
        return ""
    value = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]{0,500}>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def safe_url(value) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return value.strip()[:500]


def canonical_url(url: str) -> str:
    """Dedupe key: lowercase host, no trailing slash, no fragment."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"
