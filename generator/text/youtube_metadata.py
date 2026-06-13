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
MAX_TITLE_CONFLICT_CHARS = 64
MAX_DESCRIPTION_CHARS = 4800
MAX_TAGS = 15

_HASHTAG_RE = re.compile(r"#\w+")
_SPACE_RE = re.compile(r"\s+")
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16}|hf_[A-Za-z0-9]{20,})"
)
_INTERNAL_MARKERS = (
    "OPENAI_API_KEY",
    "HF_TOKEN",
    "PIXABAY_API_KEY",
    "SLACK_WEBHOOK_URL",
    "AWS_ACCESS_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
    "YOUTUBE_TOKEN_URI",
    "SSM_PARAMETER",
    "PARAMETER STORE",
    "DYNAMODB",
    "TERRAFORM",
    "EVENTBRIDGE",
    "STEP FUNCTIONS",
    "LAMBDA",
    "PENDING",
    "UNDEFINED",
    "NULL",
    "NAN",
)


def apply_youtube_metadata_style(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the channel's Shorts metadata house style in-place and return metadata."""
    viewer_question = str(metadata.get("viewer_question") or "").strip()
    styled = sanitize_upload_metadata(
        title=format_youtube_title(str(metadata.get("title") or "Untitled Short")),
        description=format_youtube_description(
            str(metadata.get("description") or ""),
            viewer_question=viewer_question,
        ),
        tags=merge_youtube_tags(metadata.get("tags") or []),
    )
    metadata["title"] = styled["title"]
    metadata["description"] = styled["description"]
    metadata["tags"] = styled["tags"]
    return metadata


def sanitize_upload_metadata(title: str, description: str, tags: Iterable[str]) -> Dict[str, Any]:
    """Return upload-safe YouTube metadata or raise when internal values are present."""
    unsafe_field = unsafe_upload_metadata_reason(title, description, tags)
    if unsafe_field:
        raise ValueError(unsafe_field)
    clean_title = format_youtube_title(_clean_public_text(title) or "Reddit Story")
    clean_description = _clean_public_text(description)[:MAX_DESCRIPTION_CHARS].strip()
    clean_tags = merge_youtube_tags(_clean_public_text(tag) for tag in tags or [])
    if not clean_description:
        clean_description = format_youtube_description(
            "A fast storytime Short about a relatable everyday conflict."
        )
    return {
        "title": clean_title,
        "description": clean_description,
        "tags": clean_tags,
    }


def unsafe_upload_metadata_reason(title: str, description: str, tags: Iterable[str]) -> str:
    fields = {
        "title": title,
        "description": description,
        "tags": " ".join(str(tag or "") for tag in tags or []),
    }
    for field_name, value in fields.items():
        reason = _unsafe_public_text_reason(str(value or ""))
        if reason:
            return f"unsafe_metadata:{field_name}:{reason}"
    return ""


def format_youtube_title(title: str, hashtags: Iterable[str] = TITLE_HASHTAGS) -> str:
    clean_title = _strip_hashtags(_clean_public_text(title)).strip(" .,-")
    clean_title = _SPACE_RE.sub(" ", clean_title).strip() or "Reddit Story"
    suffix = " ".join(_normalize_hashtag(tag) for tag in hashtags if _normalize_hashtag(tag))
    if not suffix:
        return clean_title[:MAX_TITLE_CHARS]

    budget = min(MAX_TITLE_CONFLICT_CHARS, MAX_TITLE_CHARS - len(suffix) - 1)
    if len(clean_title) > budget:
        clean_title = _truncate_at_word(clean_title, budget).strip(" .,-")
    return f"{clean_title} {suffix}".strip()[:MAX_TITLE_CHARS]


def format_youtube_description(
    description: str,
    viewer_question: str = "",
    hashtags: Iterable[str] = TITLE_HASHTAGS,
) -> str:
    lines: List[str] = []
    clean_description = _strip_hashtags(_clean_public_text(description)).strip()
    clean_question = _strip_hashtags(_clean_public_text(viewer_question)).strip()
    if clean_description:
        lines.append(clean_description)
    if clean_question and clean_question not in clean_description:
        lines.append(clean_question)
    hashtag_line = " ".join(_normalize_hashtag(tag) for tag in hashtags if _normalize_hashtag(tag))
    if hashtag_line:
        lines.append(hashtag_line)
    return "\n\n".join(lines).strip()[:MAX_DESCRIPTION_CHARS]


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
    normalized = _clean_public_text(tag).strip().lower().lstrip("#")
    normalized = _SPACE_RE.sub(" ", normalized)
    normalized = re.sub(r"[^a-z0-9 #_-]", "", normalized)
    return normalized[:100].strip()


def _clean_public_text(text: Any) -> str:
    cleaned = str(text or "")
    cleaned = cleaned.replace("\x00", " ")
    cleaned = _SECRET_VALUE_RE.sub("[removed]", cleaned)
    cleaned = _SPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _unsafe_public_text_reason(text: str) -> str:
    if _SECRET_VALUE_RE.search(text):
        return "secret_like_value"
    upper = text.upper()
    for marker in _INTERNAL_MARKERS:
        if "_" in marker or " " in marker:
            matched = marker in upper
        else:
            matched = bool(re.search(rf"\b{re.escape(marker)}\b", upper))
        if matched:
            return marker.lower().replace(" ", "_")
    return ""


def _truncate_at_word(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    truncated = text[:limit].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated or text[:limit].rstrip()
