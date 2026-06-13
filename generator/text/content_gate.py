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


def evaluate_content_gate(item: dict[str, Any]) -> dict[str, Any]:
    hard_errors: list[str] = []
    warnings: list[str] = []

    source_provider = str(item.get("source_provider") or item.get("source_authenticity") or "").strip().lower()
    generation_fallback = str(item.get("generation_fallback") or "").strip().lower()
    script = _script_lines(item)
    public_title = str(item.get("public_title") or "").strip()
    title = str(item.get("title") or "").strip()

    if source_provider == "synthetic" and not _allow_synthetic_source():
        hard_errors.append("synthetic_source_not_allowed")
    if generation_fallback == "local_template" and not _allow_local_template_upload():
        hard_errors.append("local_template_fallback_not_allowed")
    if source_provider in {"reddit", "pullpush"} and not str(item.get("source_url") or "").strip() and not _allow_missing_source_url():
        hard_errors.append("missing_source_url")
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

    hard_errors.extend(_caption_errors(item))
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
    result = evaluate_content_gate(item)
    if result["ok"]:
        return
    prefix = f"content_gate_failed:{stage}:" if stage else "content_gate_failed:"
    raise ValueError(prefix + "; ".join(result["hard_errors"]))


def filter_content_gate_items(items: list[dict[str, Any]], *, stage: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in items:
        result = evaluate_content_gate(item)
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
    item["caption_chunks"] = caption_chunks(item)
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
    long_chunks = [idx + 1 for idx, chunk in enumerate(chunks) if len(chunk) > 42]
    if long_chunks:
        errors.append(f"caption_chunk_too_long:{long_chunks[:3]}")
    multi_line_chunks = [idx + 1 for idx, chunk in enumerate(chunks) if len(str(chunk).splitlines()) > 2]
    if multi_line_chunks:
        errors.append(f"caption_chunk_too_many_lines:{multi_line_chunks[:3]}")
    lines = voiceover_lines(item)
    final_line = lines[-1] if lines else ""
    if final_line.endswith("?") and not chunks[-1].rstrip().endswith("?"):
        errors.append("final_question_caption_not_separate")
    return errors


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
    raw = str(item.get("source_content_excerpt") or item.get("source_content") or "").strip()
    if raw:
        return raw
    beats = item.get("story_beats") or []
    return " ".join(str(beat or "").strip() for beat in beats if str(beat or "").strip())


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


def _allow_missing_source_url() -> bool:
    return _truthy("ALLOW_MISSING_SOURCE_URL")


def _allow_low_comment_score() -> bool:
    return _truthy("ALLOW_LOW_PREDICTED_COMMENT_SCORE")


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_production_env() -> bool:
    return any(os.getenv(name, "").strip().lower() == "production" for name in ("APP_ENV", "YT_ENV"))
