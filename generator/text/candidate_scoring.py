from __future__ import annotations

import os
from typing import Any

from generator.text.content_gate import evaluate_content_gate, voiceover_lines
from generator.text.script_quality import script_duration_metrics


def score_candidate(item: dict[str, Any], post: dict | None = None, *, gate_result: dict[str, Any] | None = None) -> dict[str, Any]:
    gate = gate_result or evaluate_content_gate(item, stage="script_accept")
    hard_blockers = list(gate.get("hard_errors") or [])
    soft_issues = list(gate.get("soft_issues") or [])
    warnings = list(gate.get("warnings") or [])
    metrics = script_duration_metrics(item)
    item["word_count"] = metrics["word_count"]
    item["estimated_seconds"] = metrics["estimated_seconds"]

    if hard_blockers:
        score = 0
        breakdown = {"hard_blockers": -100}
        bucket = "rejected"
    else:
        breakdown = _score_breakdown(item, post or {}, soft_issues)
        score = max(0, min(100, round(sum(breakdown.values()))))
        bucket = _bucket_for_score(score)

    item["candidate_score"] = score
    item["candidate_score_breakdown"] = breakdown
    item["candidate_bucket"] = bucket
    item["soft_issues"] = _dedupe(soft_issues + _soft_issues_from_warnings(warnings))
    item["hard_blockers"] = hard_blockers
    item["content_gate"] = gate
    return item


def accepted_threshold() -> int:
    return _int_env("CANDIDATE_ACCEPT_SCORE", 78)


def near_miss_threshold() -> int:
    return _int_env("CANDIDATE_NEAR_MISS_SCORE", 68)


def draft_pool_threshold() -> int:
    return _int_env("CANDIDATE_DRAFT_POOL_SCORE", 55)


def _bucket_for_score(score: int) -> str:
    if score >= accepted_threshold():
        return "accepted"
    if score >= near_miss_threshold():
        return "near_miss"
    if score >= draft_pool_threshold():
        return "draft_pool"
    return "rejected"


def _score_breakdown(item: dict[str, Any], post: dict, soft_issues: list[str]) -> dict[str, int]:
    lines = voiceover_lines(item)
    text = " ".join(lines).lower()
    source_text = f"{post.get('title', '')} {post.get('content', '')}".lower()
    first_line = lines[0].lower() if lines else ""
    final_line = lines[-1].lower() if lines else ""
    receipt_terms = ("receipt", "screenshot", "camera", "bill", "text", "message", "chat", "timestamp", "estimate", "photo", "app")
    conflict_terms = (
        "accused",
        "bit",
        "blamed",
        "card",
        "charged",
        "demanded",
        "dented",
        "parked",
        "refused",
        "spent",
        "took",
        "used",
        "without asking",
    )
    detail_terms = (
        "apartment",
        "bank",
        "bill",
        "bloodwork",
        "camera",
        "car",
        "card",
        "cat",
        "driveway",
        "group chat",
        "manager",
        "receipt",
        "roommate",
        "text",
        "vet",
    )
    breakdown = {
        "hook_clarity": 20 if any(term in first_line for term in conflict_terms) else 11,
        "conflict_strength": 20 if any(term in text for term in conflict_terms) else 10,
        "specificity": min(15, 5 + sum(2 for term in detail_terms if term in text)),
        "receipt_payoff": 15 if any(term in text for term in receipt_terms) else (9 if any(term in source_text for term in receipt_terms) else 7),
        "native_naturalness": _critic_component(item),
        "comment_potential": 10 if final_line.endswith("?") and len(final_line) > 24 else 5,
        "pacing_length": _pacing_component(item),
        "visual_matchability": _visual_component(item, source_text),
        "soft_penalties": -_soft_penalty(soft_issues),
    }
    return breakdown


def _critic_component(item: dict[str, Any]) -> int:
    scores = item.get("critic_scores") or {}
    if not scores:
        return 8
    natural = int(scores.get("native_naturalness_score") or 8)
    ai_smell = int(scores.get("ai_smell_score") or 3)
    value = max(1, min(10, natural))
    if ai_smell > 3:
        value -= min(5, ai_smell - 3)
    return max(1, min(10, value))


def _pacing_component(item: dict[str, Any]) -> int:
    words = int(item.get("word_count") or script_duration_metrics(item)["word_count"])
    seconds = float(item.get("estimated_seconds") or script_duration_metrics(item)["estimated_seconds"])
    if 100 <= words <= 170 and 35 <= seconds <= 65:
        return 5
    if 75 <= words < 100 or 28 <= seconds < 35:
        return 3
    return 2


def _visual_component(item: dict[str, Any], source_text: str) -> int:
    queries = [str(item.get("opening_visual_query") or "").lower()]
    queries.extend(str(query.get("query") or "").lower() for query in item.get("visual_beat_queries") or [] if isinstance(query, dict))
    combined = " ".join(queries)
    visual_terms = ("car", "card", "receipt", "camera", "phone", "bill", "driveway", "cat", "vet", "apartment", "office", "restaurant")
    if any(term in combined for term in visual_terms):
        return 5
    if any(term in source_text for term in visual_terms):
        return 3
    return 2


def _soft_penalty(soft_issues: list[str]) -> int:
    penalty = 0
    for issue in soft_issues:
        lowered = str(issue or "").lower()
        if lowered.startswith("title_quality"):
            penalty += 3
        elif "opening_visual_query" in lowered:
            penalty += 3
        elif lowered.startswith("first_caption_hook"):
            penalty += 3
        elif "below_legacy_char_target" in lowered:
            penalty += 3
        elif "critic_" in lowered or "predicted_" in lowered:
            penalty += 5
        elif "missing_concrete_receipt_detail" in lowered:
            penalty += 5
        elif "weak_viewer_question" in lowered:
            penalty += 5
        else:
            penalty += 2
    return min(25, penalty)


def _soft_issues_from_warnings(warnings: list[str]) -> list[str]:
    issues = []
    for warning in warnings:
        code = str(warning or "").split(":", 1)[0]
        if code:
            issues.append(f"script_quality:{code}")
    return issues


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _dedupe(items: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        if item and item not in deduped:
            deduped.append(item)
    return deduped
