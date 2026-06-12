import hashlib
import html
import re
from typing import Any, Dict, Optional, Tuple


_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9']*")
_REMOVED_MARKERS = {"[removed]", "[deleted]", "removed", "deleted"}
_TRUNCATION_MARKERS = (
    "[...]",
    "[…]",
    "read more",
    "continue reading",
    "continued in comments",
)


def normalize_story_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def select_story_content(data: Dict[str, Any]) -> str:
    for key in ("selftext", "body", "text"):
        content = normalize_story_text(data.get(key))
        if content and content.lower() not in _REMOVED_MARKERS:
            return content
    return ""


def content_word_count(content: str) -> int:
    return len(_WORD_RE.findall(content or ""))


def content_hash(content: str) -> str:
    normalized = normalize_story_text(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def detect_truncation(content: str) -> Tuple[bool, str]:
    normalized = normalize_story_text(content)
    lower = normalized.lower()
    if not normalized:
        return True, "empty content"
    if lower in _REMOVED_MARKERS:
        return True, "removed/deleted marker"
    if any(marker in lower[-240:] for marker in _TRUNCATION_MARKERS):
        return True, "explicit continuation marker near end"
    if lower.endswith("...") and len(normalized) > 9000:
        return True, "long content ends with ellipsis"
    return False, ""


def source_integrity_fields(
    content: str,
    *,
    detail_checked: bool = False,
    detail_improved: bool = False,
    truncation_reason: Optional[str] = None,
) -> Dict[str, Any]:
    normalized = normalize_story_text(content)
    is_truncated, detected_reason = detect_truncation(normalized)
    reason = truncation_reason or detected_reason
    return {
        "content_char_count": len(normalized),
        "content_word_count": content_word_count(normalized),
        "content_hash": content_hash(normalized),
        "source_is_truncated": bool(is_truncated or reason),
        "source_truncation_reason": reason,
        "source_detail_checked": detail_checked,
        "source_detail_improved": detail_improved,
    }
