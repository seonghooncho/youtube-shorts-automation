from __future__ import annotations

import os
import re
from typing import Any

from generator.text.script_quality import (
    hard_quality_errors,
    quality_issues_to_regenerate_reason,
    validate_script_quality,
)
from generator.text.youtube_metadata import title_quality_reason


_CAPTION_ACTOR_TERMS = {
    "dad",
    "daughter",
    "he",
    "landlord",
    "she",
    "neighbor",
    "roommate",
    "coworker",
    "aunt",
    "brother",
    "manager",
    "owner",
}
_CAPTION_OBJECT_TERMS = {
    "apartment",
    "bank",
    "banks",
    "bill",
    "bloodwork",
    "building chat",
    "camera",
    "car",
    "card",
    "cat",
    "childcare",
    "daycare",
    "dent",
    "door camera",
    "driveway",
    "group chat",
    "home",
    "kids",
    "laundry",
    "machine",
    "number",
    "package",
    "phone",
    "receipt",
    "rent",
    "screenshot",
    "storage unit",
    "text",
    "timestamp",
    "van",
    "vet",
}
_CAPTION_ACTION_TERMS = {
    "accused",
    "bit",
    "bites",
    "blocked",
    "called",
    "calls",
    "charged",
    "changed",
    "dented",
    "left",
    "locked",
    "moved",
    "parked",
    "posted",
    "refused",
    "returned",
    "snapped",
    "spent",
    "took",
    "used",
    "walk",
    "walked",
    "walking",
}
_CAPTION_BAD_PREFIXES = ("so ", "for context", "a little backstory")
_AI_TEMPLATE_CAPTION_PHRASES = {
    "acted like i was the problem",
    "the unreasonable one",
    "people are split",
    "keep the peace",
    "let it go",
    "crossed a boundary",
    "the situation",
    "the issue",
    "the conflict",
    "the drama",
    "what changed everything",
    "the proof was clear",
    "the boundary was simple",
}
_GENERIC_OPENING_QUERIES = {
    "background",
    "drama",
    "generic story",
    "landscape",
    "nature",
    "people",
    "story",
    "viral story",
}
_BAD_FIRST_FRAME_PREFIXES = ("aita", "story", "drama", "did i overreact")
_OPENING_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "before",
    "for",
    "her",
    "his",
    "i",
    "in",
    "into",
    "it",
    "me",
    "my",
    "of",
    "on",
    "our",
    "she",
    "the",
    "then",
    "to",
    "was",
    "with",
}
_OPENING_QUERY_STRONG_TOKENS = {
    "apartment",
    "bank",
    "banks",
    "bill",
    "bloodwork",
    "building",
    "camera",
    "car",
    "card",
    "cat",
    "chat",
    "dent",
    "dented",
    "dinner",
    "driveway",
    "group",
    "kids",
    "laundry",
    "machine",
    "manager",
    "office",
    "package",
    "phone",
    "receipt",
    "restaurant",
    "rent",
    "screenshot",
    "storage",
    "text",
    "timestamp",
    "unit",
    "van",
    "vet",
    "washer",
}
_RECEIPT_TERMS = {
    "app",
    "bill",
    "camera",
    "chat",
    "group chat",
    "message",
    "messages",
    "photo",
    "receipt",
    "screenshot",
    "screenshots",
    "timestamp",
}


_DRY_RUN_BLOCKED_STAGES = {"tts", "render", "finalize", "publish_ready", "upload", "publisher"}


def evaluate_content_gate(item: dict[str, Any], *, stage: str = "") -> dict[str, Any]:
    hard_errors: list[str] = []
    warnings: list[str] = []

    normalized_stage = str(stage or "").strip().lower()
    source_provider = str(item.get("source_provider") or item.get("source_authenticity") or "").strip().lower()
    generation_fallback = str(item.get("generation_fallback") or "").strip().lower()
    script = _script_lines(item)
    public_title = str(item.get("public_title") or "").strip()
    title = str(item.get("title") or "").strip()
    raw_source_text = _raw_source_text(item)

    if item.get("dry_run") is True and normalized_stage in _DRY_RUN_BLOCKED_STAGES:
        hard_errors.append("dry_run_item_not_allowed_downstream")
    if _is_production_env() and source_provider not in {"reddit", "pullpush", "synthetic"} and not _allow_unknown_source_provider():
        hard_errors.append("unknown_source_provider")
    if source_provider == "synthetic" and not _allow_synthetic_source():
        hard_errors.append("synthetic_source_not_allowed")
    if generation_fallback == "local_template" and not _allow_local_template_render():
        hard_errors.append("local_template_render_not_allowed")
    if generation_fallback == "local_template" and not _allow_local_template_upload():
        hard_errors.append("local_template_fallback_not_allowed")
    if source_provider in {"reddit", "pullpush"} and not str(item.get("source_url") or "").strip() and not _allow_missing_source_url():
        hard_errors.append("missing_source_url")
    if source_provider in {"reddit", "pullpush"} and not raw_source_text and _is_production_env() and not _allow_missing_source_context():
        hard_errors.append("missing_source_context")
    if not script:
        hard_errors.append("missing_script")
    if _starts_with_aita(public_title or title):
        hard_errors.append("aita_title")
    if "#viral" in title.lower() or "#viral" in public_title.lower():
        hard_errors.append("viral_hashtag_not_allowed")
    if not public_title:
        hard_errors.append("missing_public_title")
    else:
        title_reason = title_quality_reason(public_title)
        if title_reason:
            hard_errors.append(f"title_quality:{title_reason}")
    if not str(item.get("style_variant") or "").strip():
        hard_errors.append("missing_style_variant")
    if not str(item.get("script_fingerprint") or "").strip():
        hard_errors.append("missing_script_fingerprint")
    hard_errors.extend(_opening_visual_errors(item))

    hard_errors.extend(_caption_errors(item))
    captions_align, caption_alignment_reason = caption_chunks_align_with_tts_text(item)
    if not captions_align:
        hard_errors.append(f"caption_chunks_not_in_tts_text:{caption_alignment_reason}")
    hard_errors.extend(_critic_score_errors(item.get("critic_scores") or {}))
    hard_errors.extend(_predicted_score_errors(item))

    source_text = _source_text(item)
    if source_text:
        post = {
            "title": item.get("source_title") or item.get("public_title") or item.get("title") or "",
            "content": source_text,
            "source_url": item.get("source_url") or "",
            "source_provider": source_provider,
            "content_char_count": len(source_text),
            "content_word_count": len(source_text.split()),
            "source_is_truncated": item.get("source_is_truncated", False),
            "source_truncation_reason": item.get("source_truncation_reason", ""),
        }
        issues = validate_script_quality(item, post)
        hard = hard_quality_errors(issues)
        if hard:
            hard_errors.append("script_quality:" + quality_issues_to_regenerate_reason(hard))
        warnings.extend(f"{issue.code}:{issue.message}" for issue in issues if not issue.hard)
    else:
        warnings.append("missing_source_content_context")

    if _source_contains_receipt(source_text) and not _script_contains_receipt(item):
        hard_errors.append("missing_concrete_receipt_detail")

    return {"ok": not hard_errors, "hard_errors": hard_errors, "warnings": warnings}


def ensure_content_gate(item: dict[str, Any], *, stage: str = "") -> None:
    result = evaluate_content_gate(item, stage=stage)
    if result["ok"]:
        return
    prefix = f"content_gate_failed:{stage}:" if stage else "content_gate_failed:"
    raise ValueError(prefix + "; ".join(result["hard_errors"]))


def filter_content_gate_items(items: list[dict[str, Any]], *, stage: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in items:
        result = evaluate_content_gate(item, stage=stage)
        item["content_gate"] = result
        if result["ok"]:
            accepted.append(item)
            continue
        rejected.append(
            {
                "id": item.get("id"),
                "title": item.get("title") or item.get("public_title"),
                "stage": stage,
                "hard_errors": result["hard_errors"],
                "warnings": result["warnings"],
            }
        )
    return accepted, rejected


def voiceover_lines(item: dict[str, Any]) -> list[str]:
    lines = [str(line or "").strip() for line in item.get("voiceover_lines") or [] if str(line or "").strip()]
    if lines:
        return lines
    return _script_lines(item)


def tts_text(item: dict[str, Any]) -> str:
    explicit = str(item.get("tts_text") or "").strip()
    if explicit:
        return explicit
    return _join_tts_lines(voiceover_lines(item))


def caption_chunks(item: dict[str, Any]) -> list[str]:
    chunks = [str(chunk or "").strip() for chunk in item.get("caption_chunks") or [] if str(chunk or "").strip()]
    if chunks:
        return chunks
    return derive_caption_chunks(voiceover_lines(item))


def derive_caption_chunks(lines: list[str], max_chars: int = 42) -> list[str]:
    chunks: list[str] = []
    for line in lines:
        cleaned = re.sub(r"\s+", " ", str(line or "")).strip()
        if not cleaned:
            continue
        if cleaned.endswith("?"):
            chunks.append(_truncate_question_chunk(cleaned, max_chars))
            continue
        words = cleaned.split()
        current = ""
        for word in words:
            trial = f"{current} {word}".strip()
            if len(trial) <= max_chars:
                current = trial
            else:
                if current:
                    chunks.append(current)
                current = word
        if current:
            chunks.append(current[:max_chars].rstrip())
    return chunks


def caption_chunks_align_with_tts_text(item: dict[str, Any]) -> tuple[bool, str]:
    chunks = caption_chunks(item)
    if not chunks:
        return False, "missing_caption_chunks"
    narration = tts_text(item) or _join_tts_lines(voiceover_lines(item))
    narration_tokens = _caption_tokens(narration)
    if not narration_tokens:
        return False, "missing_tts_text"

    position = 0
    max_gap = _caption_chunk_max_token_gap()
    for chunk_index, chunk in enumerate(chunks, start=1):
        tokens = _caption_tokens(chunk)
        if not tokens:
            return False, f"empty_chunk_{chunk_index}"
        start, end, reason = find_caption_chunk_span(tokens, narration_tokens, position, max_gap)
        if start < 0:
            return False, f"chunk_{chunk_index}_{reason}"
        position = end + 1

    final_line = voiceover_lines(item)[-1] if voiceover_lines(item) else ""
    if final_line.rstrip().endswith("?"):
        final_tokens = _caption_tokens(final_line)
        final_chunk_tokens = _caption_tokens(chunks[-1])
        start, _end, _reason = find_caption_chunk_span(final_chunk_tokens, final_tokens, 0, max_gap)
        if final_chunk_tokens and start < 0:
            return False, "final_question_chunk_not_in_final_line"
    return True, ""


def _truncate_question_chunk(text: str, max_chars: int) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= max_chars:
        return cleaned
    body = cleaned.rstrip("?").strip()
    budget = max(1, max_chars - 1)
    truncated = body[:budget].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return f"{truncated.rstrip(' .,;:')}?"


def normalize_narration_fields(item: dict[str, Any]) -> dict[str, Any]:
    lines = voiceover_lines(item)
    item["voiceover_lines"] = lines
    item["script"] = list(lines)
    item["tts_text"] = tts_text(item)
    provided_chunks = [str(chunk or "").strip() for chunk in item.get("caption_chunks") or [] if str(chunk or "").strip()]
    item["caption_chunks"] = provided_chunks or derive_caption_chunks(lines)
    if provided_chunks and _caption_chunks_need_repair(item):
        item["caption_chunks"] = derive_caption_chunks(lines)
        item["caption_chunks_repaired"] = True
    _apply_caption_policy_metadata(item)
    return item


def _join_tts_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    parts: list[str] = []
    for index, line in enumerate(lines):
        cleaned = str(line or "").strip()
        if not cleaned:
            continue
        if index == len(lines) - 2 and not cleaned.endswith((".", "?", "!")):
            cleaned += "."
        parts.append(cleaned)
    text = " ".join(parts)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _script_lines(item: dict[str, Any]) -> list[str]:
    return [str(line or "").strip() for line in item.get("script") or [] if str(line or "").strip()]


def _critic_score_errors(scores: dict[str, Any]) -> list[str]:
    if not scores:
        return ["missing_critic_scores"]
    errors = []
    if _num(scores.get("ai_smell_score")) > 3:
        errors.append("critic_ai_smell_score")
    if _num(scores.get("native_naturalness_score")) < 8:
        errors.append("critic_native_naturalness_score")
    if _num(scores.get("retention_score")) < 8:
        errors.append("critic_retention_score")
    if _num(scores.get("specificity_score")) < 8:
        errors.append("critic_specificity_score")
    return errors


def _caption_errors(item: dict[str, Any]) -> list[str]:
    chunks = caption_chunks(item)
    errors: list[str] = []
    if not chunks:
        errors.append("missing_caption_chunks")
        return errors
    _apply_caption_policy_metadata(item)
    first_caption_reason = caption_quality_reason(chunks[0], is_first=True)
    if first_caption_reason:
        errors.append(f"first_caption_hook:{first_caption_reason}")
    long_chunks = [idx + 1 for idx, chunk in enumerate(chunks) if len(chunk) > 42]
    if long_chunks:
        errors.append(f"caption_chunk_too_long:{long_chunks[:3]}")
    multi_line_chunks = [idx + 1 for idx, chunk in enumerate(chunks) if len(str(chunk).splitlines()) > 2]
    if multi_line_chunks:
        errors.append(f"caption_chunk_too_many_lines:{multi_line_chunks[:3]}")
    generic_chunks = [idx + 1 for idx, chunk in enumerate(chunks) if _is_generic_caption(chunk)]
    if generic_chunks:
        errors.append(f"generic_caption_chunk:{generic_chunks[:3]}")
    multi_sentence_chunks = [
        idx + 1
        for idx, chunk in enumerate(chunks)
        if len(re.findall(r"[.!?]", str(chunk))) > 1 and len(str(chunk)) > 32
    ]
    if multi_sentence_chunks:
        errors.append(f"caption_chunk_multi_sentence:{multi_sentence_chunks[:3]}")
    lines = voiceover_lines(item)
    final_line = lines[-1] if lines else ""
    if final_line.endswith("?") and not chunks[-1].rstrip().endswith("?"):
        errors.append("final_question_caption_not_separate")
    return errors


def caption_quality_reason(chunk: str, *, is_first: bool = False) -> str:
    lowered = re.sub(r"\s+", " ", str(chunk or "").strip().lower())
    if not lowered:
        return "empty"
    if _is_generic_caption(lowered):
        return "generic_filler"
    if lowered.startswith(_CAPTION_BAD_PREFIXES):
        return "slow_context_prefix"
    for phrase in _AI_TEMPLATE_CAPTION_PHRASES:
        if phrase in lowered:
            return "ai_template_phrase"
    if not is_first:
        return ""

    has_actor = any(term in lowered.split() for term in _CAPTION_ACTOR_TERMS)
    has_object = any(term in lowered for term in _CAPTION_OBJECT_TERMS)
    has_action = any(term in lowered.split() for term in _CAPTION_ACTION_TERMS)
    if not (has_actor or has_object or has_action):
        return "no_concrete_actor_object_or_action"
    if not (has_object or has_action):
        return "no_concrete_object_or_action"
    return ""


def _caption_chunks_need_repair(item: dict[str, Any]) -> bool:
    chunks = [str(chunk or "").strip() for chunk in item.get("caption_chunks") or [] if str(chunk or "").strip()]
    if not chunks:
        return True
    if any(len(chunk) > 42 for chunk in chunks):
        return True
    if caption_quality_reason(chunks[0], is_first=True):
        return True
    if any(_is_generic_caption(chunk) for chunk in chunks):
        return True
    lines = voiceover_lines(item)
    final_line = lines[-1] if lines else ""
    if final_line.endswith("?") and not chunks[-1].rstrip().endswith("?"):
        return True
    aligned, _reason = caption_chunks_align_with_tts_text(item)
    return not aligned


def _opening_visual_errors(item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    first_frame_text = str(item.get("first_frame_text") or "").strip()
    opening_visual_query = str(item.get("opening_visual_query") or "").strip()
    if not first_frame_text:
        errors.append("missing_first_frame_text")
    elif len(first_frame_text) > 38:
        errors.append("first_frame_text_too_long")
    elif first_frame_text.lower().startswith(_BAD_FIRST_FRAME_PREFIXES):
        errors.append("generic_first_frame_text")
    if not opening_visual_query:
        errors.append("missing_opening_visual_query")
    elif _is_generic_opening_query(opening_visual_query):
        errors.append("generic_opening_visual_query")
    else:
        relevance_reason = opening_visual_query_relevance_reason(item)
        if relevance_reason:
            errors.append(relevance_reason)
    return errors


def opening_visual_query_relevance_reason(item: dict[str, Any]) -> str:
    query_tokens = _meaningful_opening_tokens(str(item.get("opening_visual_query") or ""))
    if not query_tokens:
        return "opening_visual_query_mismatch"
    reference_text = " ".join(
        [
            voiceover_lines(item)[0] if voiceover_lines(item) else "",
            str(item.get("first_2_seconds") or ""),
            str(item.get("first_frame_text") or ""),
            str(item.get("source_title") or ""),
            str(item.get("public_title") or ""),
        ]
    )
    reference_tokens = _meaningful_opening_tokens(reference_text)
    overlap = query_tokens & reference_tokens
    strong_overlap = overlap & _OPENING_QUERY_STRONG_TOKENS
    if strong_overlap or len(overlap) >= 2:
        return ""
    return "opening_visual_query_mismatch"


def _predicted_score_errors(item: dict[str, Any]) -> list[str]:
    errors = []
    if _num(item.get("predicted_retention_score")) < 8:
        errors.append("predicted_retention_score")
    if _num(item.get("predicted_clarity_score")) < 8:
        errors.append("predicted_clarity_score")
    if _num(item.get("predicted_ai_smell_score")) > 3:
        errors.append("predicted_ai_smell_score")
    if _num(item.get("predicted_comment_score")) < 7 and not _allow_low_comment_score():
        errors.append("predicted_comment_score")
    return errors


def _source_text(item: dict[str, Any]) -> str:
    raw = _raw_source_text(item)
    if raw:
        return raw
    beats = item.get("story_beats") or []
    return " ".join(str(beat or "").strip() for beat in beats if str(beat or "").strip())


def _raw_source_text(item: dict[str, Any]) -> str:
    return str(item.get("source_content_excerpt") or item.get("source_content") or "").strip()


def _source_contains_receipt(source_text: str) -> bool:
    lowered = str(source_text or "").lower()
    return any(term in lowered for term in _RECEIPT_TERMS)


def _script_contains_receipt(item: dict[str, Any]) -> bool:
    text = " ".join(_script_lines(item) + caption_chunks(item)).lower()
    return any(term in text for term in _RECEIPT_TERMS)


def _starts_with_aita(title: str) -> bool:
    return bool(re.match(r"^\s*(?:aita\b|am\s+i\s+the\s+asshole\b|am\s+i\s+wrong\b|did\s+i\s+overreact\b)", str(title or ""), flags=re.I))


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _allow_synthetic_source() -> bool:
    if _is_production_env():
        return _truthy("ALLOW_SYNTHETIC_IN_PRODUCTION")
    return _truthy("SCRIPT_ALLOW_SYNTHETIC_SOURCES") or _truthy("ALLOW_SYNTHETIC_IN_PRODUCTION")


def _allow_local_template_upload() -> bool:
    return _truthy("ALLOW_LOCAL_TEMPLATE_UPLOAD")


def _allow_local_template_render() -> bool:
    return _truthy("ALLOW_LOCAL_TEMPLATE_RENDER")


def _allow_missing_source_url() -> bool:
    return _truthy("ALLOW_MISSING_SOURCE_URL")


def _allow_low_comment_score() -> bool:
    return _truthy("ALLOW_LOW_PREDICTED_COMMENT_SCORE")


def _allow_missing_source_context() -> bool:
    return _truthy("ALLOW_MISSING_SOURCE_CONTEXT")


def _allow_unknown_source_provider() -> bool:
    return _truthy("ALLOW_UNKNOWN_SOURCE_PROVIDER")


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_production_env() -> bool:
    return any(os.getenv(name, "").strip().lower() == "production" for name in ("APP_ENV", "YT_ENV"))


def _apply_caption_policy_metadata(item: dict[str, Any]) -> None:
    chunks = caption_chunks(item)
    total_chars = sum(len(chunk) for chunk in chunks)
    density = "sparse"
    if total_chars >= 180 or len(chunks) >= 8:
        density = "dense"
    elif total_chars >= 90 or len(chunks) >= 5:
        density = "medium"
    item["first_caption_hook_score"] = 1 if chunks and not caption_quality_reason(chunks[0], is_first=True) else 0
    item["caption_density_bucket"] = density
    item["caption_reveal_policy"] = "sync_with_narration"


def _is_generic_caption(chunk: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(chunk or "").strip().lower())
    generic = {
        "the boundary was simple",
        "the proof was clear",
        "what happened next",
        "things got worse",
        "people are split",
        "now everyone is mad",
        "this caused drama",
        "i tried to keep it calm",
        "so what do you think",
    }
    return lowered in generic


def _caption_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").lower())


def find_caption_chunk_span(
    tokens: list[str],
    narration_tokens: list[str],
    start_position: int = 0,
    max_gap: int | None = None,
) -> tuple[int, int, str]:
    if max_gap is None:
        max_gap = _caption_chunk_max_token_gap()
    if not tokens:
        return -1, -1, "empty_chunk"
    first_token = tokens[0]
    position = max(0, start_position)
    saw_first_token = False
    while position < len(narration_tokens):
        start_index = _find_token(narration_tokens, first_token, position)
        if start_index < 0:
            break
        saw_first_token = True
        previous_index = start_index
        failed_reason = ""
        for token in tokens[1:]:
            found_at = _find_token(narration_tokens, token, previous_index + 1)
            if found_at < 0:
                failed_reason = f"token_not_found:{token}"
                break
            if found_at - previous_index - 1 > max_gap:
                failed_reason = "caption_chunk_not_contiguous"
                break
            previous_index = found_at
        if not failed_reason:
            return start_index, previous_index, ""
        position = start_index + 1
    if saw_first_token:
        return -1, -1, "caption_chunk_not_contiguous"
    return -1, -1, f"token_not_found:{first_token}"


def _find_token(tokens: list[str], needle: str, start: int) -> int:
    for index in range(start, len(tokens)):
        if tokens[index] == needle:
            return index
    return -1


def _tokens_are_ordered_subset(subset: list[str], tokens: list[str]) -> bool:
    position = 0
    for token in subset:
        found_at = _find_token(tokens, token, position)
        if found_at < 0:
            return False
        position = found_at + 1
    return True


def _caption_chunk_max_token_gap() -> int:
    try:
        return max(0, int(os.getenv("CAPTION_CHUNK_MAX_TOKEN_GAP", "2")))
    except ValueError:
        return 2


def _is_generic_opening_query(query: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(query or "").strip().lower())
    if lowered in _GENERIC_OPENING_QUERIES:
        return True
    tokens = set(_caption_tokens(lowered))
    if not tokens:
        return True
    concrete_tokens = set(_CAPTION_OBJECT_TERMS) | set(_CAPTION_ACTION_TERMS)
    split_concrete = {token for phrase in concrete_tokens for token in phrase.split()}
    return not bool(tokens & split_concrete)


def _meaningful_opening_tokens(text: str) -> set[str]:
    return {
        token
        for token in _caption_tokens(text)
        if len(token) >= 3 and token not in _OPENING_QUERY_STOPWORDS
    }
