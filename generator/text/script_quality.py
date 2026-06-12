import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from generator.text.source_integrity import (
    content_word_count,
    detect_truncation,
    normalize_story_text,
)


MIN_SCRIPT_CHARS = 750
TARGET_MIN_SCRIPT_CHARS = 800
TARGET_MAX_SCRIPT_CHARS = 1150
MAX_SCRIPT_CHARS = 1400
MIN_SOURCE_WORDS = 90
MIN_SOURCE_CHARS = 550

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']+")
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

    keywords = metadata.get("visual_keywords") or []
    if not isinstance(keywords, list) or len([kw for kw in keywords if str(kw).strip()]) < 4:
        issues.append(ScriptQualityIssue("weak_visual_keywords", "visual_keywords should include at least 4 concrete phrases"))

    overlap = _source_overlap_ratio(source.title, source.content, text)
    if source.word_count >= MIN_SOURCE_WORDS and overlap < 0.12:
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
