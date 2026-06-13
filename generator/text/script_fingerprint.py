from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable


STYLE_VARIANTS = (
    "receipt_reveal",
    "public_accusation",
    "dinner_table_pressure",
    "group_chat_exposure",
    "money_trap",
    "favor_taken_too_far",
    "false_blame",
    "last_straw",
    "quiet_refusal",
    "unexpected_receipt",
    "awkward_silence",
    "family_pressure",
    "workplace_receipt",
    "neighbor_dispute",
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']+")


@dataclass(frozen=True)
class BatchDiversityIssue:
    code: str
    message: str


def apply_script_fingerprint(metadata: dict[str, Any]) -> dict[str, Any]:
    metadata["hook_pattern"] = hook_pattern(metadata)
    metadata["ending_pattern"] = ending_pattern(metadata)
    metadata["style_variant"] = style_variant(metadata)
    metadata["script_fingerprint"] = script_fingerprint(metadata)
    return metadata


def batch_diversity_issues(
    candidate: dict[str, Any],
    accepted_batch: Iterable[dict[str, Any]],
    history: Iterable[dict[str, Any]] | None = None,
) -> list[BatchDiversityIssue]:
    apply_script_fingerprint(candidate)
    batch = [apply_script_fingerprint(dict(item)) for item in accepted_batch]
    previous = [apply_script_fingerprint(dict(item)) for item in (history or [])][-50:]
    issues: list[BatchDiversityIssue] = []

    if _count_matching(batch, "first_line_structure", first_line_structure(candidate)) >= 1:
        issues.append(BatchDiversityIssue("duplicate_first_line_structure", "same batch already has this first-line structure"))
    if _count_matching(batch, "hook_type", _norm(candidate.get("hook_type"))) >= 2:
        issues.append(BatchDiversityIssue("duplicate_hook_type", "same batch would use this hook_type more than twice"))
    if _count_matching(batch, "ending_pattern", candidate["ending_pattern"]) >= 2:
        issues.append(BatchDiversityIssue("duplicate_ending_pattern", "same batch would use this ending pattern more than twice"))
    if _count_matching(batch, "transition_pattern", transition_pattern(candidate)) >= 2:
        issues.append(BatchDiversityIssue("duplicate_transition_pattern", "same batch would use this transition pattern more than twice"))
    if _count_matching(batch, "title_start", title_start(candidate)) >= 2:
        issues.append(BatchDiversityIssue("duplicate_title_start", "same batch would use this title opening more than twice"))
    if _count_matching(batch, "style_variant", candidate["style_variant"]) >= 2:
        issues.append(BatchDiversityIssue("duplicate_style_variant", "same batch would use this style_variant more than twice"))

    for prior in batch + previous:
        if _ngram_similarity(candidate, prior) >= 0.56:
            issues.append(BatchDiversityIssue("high_script_similarity", "script is too similar to an accepted script"))
            break
    return issues


def diversity_issues_to_reason(issues: Iterable[BatchDiversityIssue]) -> str:
    selected = list(issues)
    if not selected:
        return "Batch diversity requirements were not met."
    return "; ".join(f"{issue.code}: {issue.message}" for issue in selected[:5])


def script_fingerprint(metadata: dict[str, Any]) -> str:
    parts = [
        first_line_structure(metadata),
        "|".join(first_three_words_per_line(metadata)),
        "|".join(sorted(_common_ngrams(script_text(metadata), (4, 5)))[:12]),
        _norm(metadata.get("hook_type")),
        ending_pattern(metadata),
        style_variant(metadata),
        title_start(metadata),
    ]
    digest = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()[:16]
    return digest


def first_line_structure(metadata: dict[str, Any]) -> str:
    lines = _script_lines(metadata)
    return " ".join(_words(lines[0])[:8]) if lines else ""


def first_three_words_per_line(metadata: dict[str, Any]) -> list[str]:
    return [" ".join(_words(line)[:3]) for line in _script_lines(metadata) if _words(line)]


def hook_pattern(metadata: dict[str, Any]) -> str:
    return _norm(metadata.get("hook_type")) or first_line_structure(metadata)


def ending_pattern(metadata: dict[str, Any]) -> str:
    question = _norm(metadata.get("viewer_question") or (_script_lines(metadata)[-1] if _script_lines(metadata) else ""))
    if re.search(r"\bwas i\b.+\bor\b.+\?", question):
        return "was_i_x_or_y"
    if question.startswith("would you have"):
        return "would_you_have"
    if question.startswith("was i wrong"):
        return "was_i_wrong"
    if question.startswith("did i"):
        return "did_i"
    return " ".join(question.rstrip("?").split()[:5])


def style_variant(metadata: dict[str, Any]) -> str:
    explicit = _norm(metadata.get("style_variant")).replace(" ", "_")
    if explicit in STYLE_VARIANTS:
        return explicit
    text = _norm(" ".join([
        str(metadata.get("public_title") or metadata.get("title") or ""),
        str(metadata.get("hook_type") or ""),
        script_text(metadata),
    ]))
    if any(term in text for term in ("receipt", "screenshot", "camera", "photo", "messages", "file history")):
        return "receipt_reveal"
    if "group chat" in text or "building chat" in text:
        return "group_chat_exposure"
    if any(term in text for term in ("bill", "card", "invoice", "deposit", "paid", "pay")):
        return "money_trap"
    if any(term in text for term in ("coworker", "manager", "office", "work")):
        return "workplace_receipt"
    if any(term in text for term in ("neighbor", "driveway", "parking", "walkway")):
        return "neighbor_dispute"
    if any(term in text for term in ("family", "parents", "aunt", "uncle", "sister", "brother", "cousin")):
        return "family_pressure"
    if any(term in text for term in ("accused", "blamed", "called me")):
        return "false_blame"
    return "last_straw"


def transition_pattern(metadata: dict[str, Any]) -> str:
    lines = _script_lines(metadata)
    starts = []
    for line in lines[1:4]:
        words = _words(line)
        if words:
            starts.append(" ".join(words[:2]))
    return "|".join(starts)


def title_start(metadata: dict[str, Any]) -> str:
    title = str(metadata.get("public_title") or metadata.get("title") or "")
    return " ".join(_words(title)[:3])


def script_text(metadata: dict[str, Any]) -> str:
    return " ".join(_script_lines(metadata)).strip()


def _script_lines(metadata: dict[str, Any]) -> list[str]:
    return [str(line or "").strip() for line in metadata.get("script") or [] if str(line or "").strip()]


def _common_ngrams(text: str, sizes: tuple[int, ...]) -> set[str]:
    words = _words(text)
    grams: set[str] = set()
    for size in sizes:
        for idx in range(0, max(0, len(words) - size + 1)):
            grams.add(" ".join(words[idx : idx + size]))
    return grams


def _ngram_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_grams = _common_ngrams(script_text(left), (4, 5))
    right_grams = _common_ngrams(script_text(right), (4, 5))
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _count_matching(items: list[dict[str, Any]], field: str, value: str) -> int:
    if not value:
        return 0
    count = 0
    for item in items:
        if field == "first_line_structure":
            item_value = first_line_structure(item)
        elif field == "transition_pattern":
            item_value = transition_pattern(item)
        elif field == "title_start":
            item_value = title_start(item)
        else:
            item_value = _norm(item.get(field))
        if item_value == value:
            count += 1
    return count


def _words(text: str) -> list[str]:
    return [match.group(0).lower().strip("'") for match in _WORD_RE.finditer(str(text or ""))]


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().split())
