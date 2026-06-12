from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


TITLE_HASHTAGS = ("#shorts", "#story", "#reddit", "#viral")
DEFAULT_VIDEO_TAGS = (
    "shorts",
    "story",
    "reddit",
    "viral",
    "storytime",
    "reddit story",
    "aita",
    "drama",
    "short story",
)
MAX_TITLE_CHARS = 100
MAX_TAGS = 15

_HASHTAG_RE = re.compile(r"#\w+")
_SPACE_RE = re.compile(r"\s+")


def apply_youtube_metadata_style(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the channel's Shorts metadata house style in-place and return metadata."""
    viewer_question = str(metadata.get("viewer_question") or "").strip()
    metadata["title"] = format_youtube_title(str(metadata.get("title") or "Untitled Short"))
    metadata["description"] = format_youtube_description(
        str(metadata.get("description") or ""),
        viewer_question=viewer_question,
    )
    metadata["tags"] = merge_youtube_tags(metadata.get("tags") or [])
    return metadata


def format_youtube_title(title: str, hashtags: Iterable[str] = TITLE_HASHTAGS) -> str:
    clean_title = _strip_hashtags(title).strip(" .,-")
    clean_title = _SPACE_RE.sub(" ", clean_title).strip() or "Reddit Story"
    suffix = " ".join(_normalize_hashtag(tag) for tag in hashtags if _normalize_hashtag(tag))
    if not suffix:
        return clean_title[:MAX_TITLE_CHARS]

    budget = MAX_TITLE_CHARS - len(suffix) - 1
    if len(clean_title) > budget:
        clean_title = _truncate_at_word(clean_title, budget).strip(" .,-")
    return f"{clean_title} {suffix}".strip()[:MAX_TITLE_CHARS]


def format_youtube_description(
    description: str,
    viewer_question: str = "",
    hashtags: Iterable[str] = TITLE_HASHTAGS,
) -> str:
    lines: List[str] = []
    clean_description = _strip_hashtags(description).strip()
    if clean_description:
        lines.append(clean_description)
    if viewer_question and viewer_question not in clean_description:
        lines.append(viewer_question)
    hashtag_line = " ".join(_normalize_hashtag(tag) for tag in hashtags if _normalize_hashtag(tag))
    if hashtag_line:
        lines.append(hashtag_line)
    return "\n\n".join(lines).strip()


def merge_youtube_tags(tags: Iterable[str]) -> list[str]:
    merged: list[str] = []
    for tag in list(tags or []) + list(DEFAULT_VIDEO_TAGS):
        normalized = _normalize_tag(tag)
        if not normalized or normalized in merged:
            continue
        merged.append(normalized)
        if len(merged) >= MAX_TAGS:
            break
    return merged


def _strip_hashtags(text: str) -> str:
    return _SPACE_RE.sub(" ", _HASHTAG_RE.sub("", str(text or ""))).strip()


def _normalize_hashtag(tag: str) -> str:
    normalized = _normalize_tag(tag)
    if not normalized:
        return ""
    return "#" + normalized.replace(" ", "")


def _normalize_tag(tag: str) -> str:
    normalized = str(tag or "").strip().lower().lstrip("#")
    normalized = _SPACE_RE.sub(" ", normalized)
    return normalized[:100].strip()


def _truncate_at_word(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    truncated = text[:limit].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated or text[:limit].rstrip()
