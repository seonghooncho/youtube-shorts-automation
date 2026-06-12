import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from generator.text.source_integrity import (
    content_word_count,
    detect_truncation,
    normalize_story_text,
)


MIN_SCRIPT_CHARS = 750
TARGET_MIN_SCRIPT_CHARS = 780
TARGET_MAX_SCRIPT_CHARS = 1080
MAX_SCRIPT_CHARS = 1150
MIN_SOURCE_WORDS = 90
MIN_SOURCE_CHARS = 550

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']+")
_AGE_RE = re.compile(r"\b(?:1[0-7])\s*(?:f|m|yo|y/o|year old|years old)?\b|\b(?:minor|underage|teen|teenage|high school)\b")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
_SLOW_HOOK_PREFIXES = (
    "so get this",
    "a little backstory",
    "okay so",
    "for context",
    "let me explain",
    "this happened",
    "i never thought",
    "i just need",
)
_HOOK_STAKES_TERMS = {
    "acted",
    "accused",
    "blamed",
    "banned",
    "borrowed",
    "broke",
    "called me",
    "caught",
    "charged",
    "cut off",
    "deleted",
    "demanded",
    "denied",
    "destroyed",
    "dumped",
    "embarrassed",
    "exposed",
    "forced",
    "hid",
    "humiliated",
    "ignored",
    "invited",
    "kept",
    "kicked",
    "left",
    "lied",
    "locked",
    "made me pay",
    "opened",
    "pressured",
    "promised",
    "punctured",
    "refused",
    "reported",
    "ruined",
    "secret",
    "shared",
    "single",
    "spent",
    "spread",
    "stole",
    "threatened",
    "told everyone",
    "took",
    "trashed",
    "used",
    "without asking",
    "wouldn't",
}
_ROMANTIC_OR_SEXUAL_TERMS = {
    "boyfriend",
    "girlfriend",
    "dating",
    "relationship",
    "crush",
    "kiss",
    "hookup",
    "sex",
    "sexual",
    "slept",
    "nude",
    "intimate",
}
_WEAK_VIEWER_QUESTIONS = {
    "so, what do you think?",
    "what do you think?",
    "am i wrong?",
    "was i wrong?",
}
_ADAPTATION_SIGNAL_TERMS = {
    "adapt",
    "clarif",
    "combined",
    "compressed",
    "dramatized",
    "kept",
    "plausible",
    "preserved",
    "sharpened",
    "without changing",
}
_UNSUPPORTED_HIGH_STAKES_GROUPS = {
    "police_or_legal": (
        "911",
        "arrest",
        "charged with",
        "court",
        "criminal",
        "lawsuit",
        "lawyer",
        "police",
        "restraining order",
        "sue",
    ),
    "violence": (
        "assault",
        "gun",
        "hit me",
        "knife",
        "punched",
        "violent",
        "weapon",
    ),
    "sexual_or_cheating": (
        "affair",
        "cheat",
        "cheated",
        "hookup",
        "mistress",
        "nude",
        "sexual",
        "slept with",
    ),
    "pregnancy_or_medical_emergency": (
        "ambulance",
        "emergency room",
        "hospital",
        "miscarriage",
        "pregnancy",
        "pregnant",
    ),
    "minors": (
        "high school",
        "minor",
        "teen",
        "teenage",
        "underage",
    ),
    "job_loss": (
        "fired",
        "lost my job",
        "terminated",
    ),
}
_SCRIPT_META_PHRASES = (
    "as an ai",
    "json",
    "script",
    "this reddit post",
    "the original post",
    "viewer engagement",
)
_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "asked",
    "because",
    "before",
    "being",
    "could",
    "every",
    "everyone",
    "from",
    "have",
    "just",
    "like",
    "more",
    "most",
    "said",
    "says",
    "should",
    "started",
    "that",
    "their",
    "them",
    "then",
    "there",
    "they",
    "this",
    "told",
    "with",
    "would",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "your",
}


@dataclass(frozen=True)
class ScriptQualityIssue:
    code: str
    message: str
    hard: bool = True


@dataclass(frozen=True)
class SourceProfile:
    title: str
    content: str
    char_count: int
    word_count: int
    is_truncated: bool
    truncation_reason: str
    source_url: str
    provider: str


def build_source_profile(post: Dict[str, Any]) -> SourceProfile:
    title = normalize_story_text(post.get("title", ""))
    content = normalize_story_text(post.get("content", ""))
    detected_truncated, detected_reason = detect_truncation(content)
    explicit_reason = str(post.get("source_truncation_reason") or "").strip()
    return SourceProfile(
        title=title,
        content=content,
        char_count=int(post.get("content_char_count") or len(content)),
        word_count=int(post.get("content_word_count") or content_word_count(content)),
        is_truncated=bool(post.get("source_is_truncated") or detected_truncated),
        truncation_reason=explicit_reason or detected_reason,
        source_url=str(post.get("source_url") or ""),
        provider=str(post.get("source_provider") or ""),
    )


def script_text(metadata: Dict[str, Any]) -> str:
    return " ".join(str(line or "").strip() for line in metadata.get("script") or []).strip()


def validate_script_quality(metadata: Dict[str, Any], post: Dict[str, Any]) -> List[ScriptQualityIssue]:
    issues: List[ScriptQualityIssue] = []
    source = build_source_profile(post)
    lines = [str(line or "").strip() for line in metadata.get("script") or [] if str(line or "").strip()]
    text = script_text(metadata)
    lower_text = text.lower()
    char_count = len(text)
    source_reject_reason = source_reject_reason_for_marketability(post)
    if source_reject_reason:
        issues.append(ScriptQualityIssue("source_marketability_reject", source_reject_reason))

    if source.is_truncated:
        issues.append(
            ScriptQualityIssue(
                "source_truncated",
                f"source content may be truncated: {source.truncation_reason or 'unknown reason'}",
            )
        )
    if source.char_count < MIN_SOURCE_CHARS or source.word_count < MIN_SOURCE_WORDS:
        issues.append(
            ScriptQualityIssue(
                "source_too_thin",
                f"source is too thin for faithful adaptation ({source.char_count} chars, {source.word_count} words)",
            )
        )

    if char_count < MIN_SCRIPT_CHARS:
        issues.append(ScriptQualityIssue("script_too_short", f"script is too short ({char_count} chars)"))
    if char_count > MAX_SCRIPT_CHARS:
        issues.append(ScriptQualityIssue("script_too_long", f"script is too long ({char_count} chars)"))
    if len(lines) < 4:
        issues.append(ScriptQualityIssue("too_few_beats", "script should have at least 4 paragraph beats"))
    if len(lines) > 9:
        issues.append(ScriptQualityIssue("too_many_beats", "script should stay within 4 to 9 paragraph beats"))
    elif len(lines) > 7:
        issues.append(
            ScriptQualityIssue(
                "many_script_paragraphs",
                f"script is valid but has {len(lines)} paragraphs; 5 to 7 is usually tighter for Shorts",
                hard=False,
            )
        )

    first_sentence = _first_sentence(text)
    first_lower = first_sentence.lower()
    if not first_sentence:
        issues.append(ScriptQualityIssue("missing_hook", "first sentence is missing"))
    elif len(first_sentence) > 170:
        issues.append(ScriptQualityIssue("hook_too_long", f"first hook sentence is too long ({len(first_sentence)} chars)"))
    elif first_lower.startswith(_SLOW_HOOK_PREFIXES):
        issues.append(ScriptQualityIssue("slow_hook", f"hook starts too slowly: {first_sentence[:80]}"))
    elif not _has_hook_stakes(first_lower):
        issues.append(
            ScriptQualityIssue(
                "weak_market_hook",
                "first sentence should include a concrete crossed line, accusation, consequence, or unfair action",
            )
        )

    if "?" not in " ".join(lines[-2:]):
        issues.append(ScriptQualityIssue("missing_engagement_question", "script should end with a direct question"))

    for phrase in _SCRIPT_META_PHRASES:
        if _contains_meta_phrase(lower_text, phrase):
            issues.append(ScriptQualityIssue("meta_language", f"script contains meta phrase: {phrase}"))
            break

    summary = str(metadata.get("source_summary") or "").strip()
    if len(summary) < 40:
        issues.append(ScriptQualityIssue("weak_source_summary", "source_summary should summarize the original conflict"))

    story_beats = metadata.get("story_beats") or []
    if not isinstance(story_beats, list) or len([beat for beat in story_beats if str(beat).strip()]) < 4:
        issues.append(ScriptQualityIssue("weak_story_beats", "story_beats should include at least 4 source-grounded beats"))

    adaptation_strategy = str(metadata.get("adaptation_strategy") or "").strip()
    if len(adaptation_strategy) < 50:
        issues.append(
            ScriptQualityIssue(
                "weak_adaptation_strategy",
                "adaptation_strategy should explain what was compressed or plausibly dramatized",
            )
        )
    elif not _has_adaptation_signal(adaptation_strategy):
        issues.append(
            ScriptQualityIssue(
                "weak_adaptation_strategy",
                "adaptation_strategy should transparently describe the compression or plausible dramatization",
            )
        )

    retention_angle = str(metadata.get("retention_angle") or "").strip()
    if len(retention_angle) < 60:
        issues.append(ScriptQualityIssue("weak_retention_angle", "retention_angle should explain the concrete clickable conflict"))
    elif not _has_marketability_signal(retention_angle):
        issues.append(ScriptQualityIssue("weak_retention_angle", "retention_angle lacks a concrete retention signal"))

    viewer_question = str(metadata.get("viewer_question") or "").strip()
    if not viewer_question.endswith("?"):
        issues.append(ScriptQualityIssue("weak_viewer_question", "viewer_question should be a direct question"))
    elif viewer_question.lower() in _WEAK_VIEWER_QUESTIONS:
        issues.append(ScriptQualityIssue("weak_viewer_question", "viewer_question is too generic for a marketable Shorts ending"))

    marketability_score = _safe_int(metadata.get("marketability_score"), 0)
    if marketability_score < 4:
        issues.append(ScriptQualityIssue("low_marketability_score", f"marketability_score should be 4 or 5, got {marketability_score}"))

    keywords = metadata.get("visual_keywords") or []
    if not isinstance(keywords, list) or len([kw for kw in keywords if str(kw).strip()]) < 4:
        issues.append(ScriptQualityIssue("weak_visual_keywords", "visual_keywords should include at least 4 concrete phrases"))
    if _visual_keywords_imply_minor_risk(keywords):
        issues.append(ScriptQualityIssue("unsafe_visual_keywords", "visual_keywords should not imply minors, teens, or school romance"))

    unsupported_facts = _unsupported_high_stakes_facts(source.title, source.content, text)
    if unsupported_facts:
        issues.append(
            ScriptQualityIssue(
                "unsupported_high_stakes_fact",
                "script invents high-stakes facts not present in source: " + ", ".join(unsupported_facts),
            )
        )

    overlap = _source_overlap_ratio(source.title, source.content, text)
    if source.word_count >= MIN_SOURCE_WORDS and overlap < 0.08:
        issues.append(
            ScriptQualityIssue(
                "low_source_overlap",
                f"script has weak lexical overlap with source ({overlap:.2f}); possible hallucinated adaptation",
            )
        )

    repeated_start_count = _repeated_sentence_start_count(lines)
    if repeated_start_count >= 3:
        issues.append(
            ScriptQualityIssue(
                "repetitive_sentence_starts",
                f"too many paragraphs start similarly ({repeated_start_count} repeated starts)",
                hard=False,
            )
        )

    if not (TARGET_MIN_SCRIPT_CHARS <= char_count <= TARGET_MAX_SCRIPT_CHARS):
        issues.append(
            ScriptQualityIssue(
                "outside_target_length",
                f"script is valid but outside the preferred {TARGET_MIN_SCRIPT_CHARS}-{TARGET_MAX_SCRIPT_CHARS} char target ({char_count})",
                hard=False,
            )
        )

    return issues


def hard_quality_errors(issues: Iterable[ScriptQualityIssue]) -> List[ScriptQualityIssue]:
    return [issue for issue in issues if issue.hard]


def source_reject_reason_for_marketability(post: Dict[str, Any]) -> str:
    source = build_source_profile(post)
    combined = f"{source.title} {source.content}".lower()
    if _has_minor_romance_or_sexual_context(combined):
        return "source involves minors or teen/high-school context in romantic or sexual conflict"
    return ""


def quality_issues_to_regenerate_reason(issues: Iterable[ScriptQualityIssue]) -> str:
    hard = hard_quality_errors(issues)
    selected = hard or list(issues)
    if not selected:
        return "The previous script did not meet quality requirements."
    return "; ".join(f"{issue.code}: {issue.message}" for issue in selected[:5])


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        return ""
    parts = _SENTENCE_END_RE.split(text, maxsplit=1)
    return parts[0].strip()


def _source_overlap_ratio(title: str, content: str, script: str) -> float:
    source_tokens = _significant_tokens(f"{title} {content}")
    if not source_tokens:
        return 0.0
    script_tokens = _significant_tokens(script)
    if not script_tokens:
        return 0.0
    denominator = min(len(source_tokens), 120)
    return len(source_tokens & script_tokens) / denominator


def _significant_tokens(text: str) -> set[str]:
    tokens = set()
    for match in _WORD_RE.finditer(text.lower()):
        token = match.group(0).strip("'")
        if len(token) < 4 or token in _STOPWORDS:
            continue
        tokens.add(token)
    return tokens


def _has_hook_stakes(first_sentence: str) -> bool:
    return any(term in first_sentence for term in _HOOK_STAKES_TERMS)


def _has_marketability_signal(text: str) -> bool:
    lowered = text.lower()
    signal_terms = _HOOK_STAKES_TERMS | {
        "boundary",
        "betrayal",
        "consequence",
        "conflict",
        "decision",
        "dilemma",
        "moral split",
        "pressure",
        "property",
        "stakes",
        "unfair",
    }
    return any(term in lowered for term in signal_terms)


def _has_adaptation_signal(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _ADAPTATION_SIGNAL_TERMS)


def _has_minor_romance_or_sexual_context(text: str) -> bool:
    return bool(_AGE_RE.search(text)) and any(term in text for term in _ROMANTIC_OR_SEXUAL_TERMS)


def _unsupported_high_stakes_facts(title: str, content: str, script: str) -> List[str]:
    source_text = f"{title} {content}".lower()
    script_text_lower = script.lower()
    unsupported = []
    for label, terms in _UNSUPPORTED_HIGH_STAKES_GROUPS.items():
        script_has_group = any(term in script_text_lower for term in terms)
        source_has_group = any(term in source_text for term in terms)
        if script_has_group and not source_has_group:
            unsupported.append(label)
    return unsupported


def _visual_keywords_imply_minor_risk(keywords: list[str]) -> bool:
    risky_terms = {"teen", "teenage", "minor", "high school", "school romance", "student couple"}
    normalized = " | ".join(str(keyword or "").lower() for keyword in keywords)
    return any(term in normalized for term in risky_terms)


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _contains_meta_phrase(text: str, phrase: str) -> bool:
    if " " in phrase:
        return phrase in text
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _repeated_sentence_start_count(lines: List[str]) -> int:
    starts: Dict[str, int] = {}
    for line in lines:
        words = _WORD_RE.findall(line.lower())
        if len(words) < 3:
            continue
        key = " ".join(words[:3])
        starts[key] = starts.get(key, 0) + 1
    return max(starts.values(), default=0)
