from __future__ import annotations

import os
import re
from enum import Enum
from typing import Iterable


class FailureAction(Enum):
    REPAIR_ONLY = "repair_only"
    LLM_REWRITE_ONCE = "llm_rewrite_once"
    SKIP_SOURCE = "skip_source"


_REPAIR_ONLY_CODES = {
    "caption_chunks_not_in_tts_text",
    "final_question_caption_not_separate",
    "first_caption_hook",
    "first_frame_text_too_long",
    "generic_opening_visual_query",
    "missing_first_frame_text",
    "missing_script_fingerprint",
    "opening_visual_query_mismatch",
    "title_quality",
    "weak_retention_angle",
    "weak_viewer_question",
}
_REWRITE_ONCE_CODES = {
    "abstract_language_overload",
    "generic_reusable_line",
    "low_source_overlap",
    "missing_concrete_details",
    "native_viewer_critic_failed",
    "weak_market_hook",
}
_SKIP_CODES = {
    "source_marketability_reject",
    "source_too_thin",
    "source_truncated",
    "unsupported_high_stakes_fact",
    "unsafe_visual_keywords",
}


def script_repair_min_chars() -> int:
    try:
        return int(os.getenv("SCRIPT_REPAIR_MIN_CHARS", "540"))
    except ValueError:
        return 540


def classify_failure(message: str | Iterable[str], *, script_chars: int | None = None, repeated: bool = False) -> FailureAction:
    text = " ".join(message) if not isinstance(message, str) else message
    lowered = text.lower()
    if repeated:
        return FailureAction.SKIP_SOURCE
    if any(code in lowered for code in _SKIP_CODES):
        return FailureAction.SKIP_SOURCE
    if "script가 너무 짧" in lowered or "script_too_short" in lowered:
        count = script_chars if script_chars is not None else _extract_char_count(lowered)
        if count is not None and count >= script_repair_min_chars():
            return FailureAction.REPAIR_ONLY
        return FailureAction.LLM_REWRITE_ONCE
    if any(code in lowered for code in _REPAIR_ONLY_CODES):
        return FailureAction.REPAIR_ONLY
    if any(code in lowered for code in _REWRITE_ONCE_CODES):
        return FailureAction.LLM_REWRITE_ONCE
    return FailureAction.LLM_REWRITE_ONCE


def _extract_char_count(text: str) -> int | None:
    match = re.search(r"(?:현재|chars?|characters?)\s*(\d+)|\((\d+)\s*(?:자|chars?)\)", text)
    if not match:
        return None
    for group in match.groups():
        if group:
            return int(group)
    return None
