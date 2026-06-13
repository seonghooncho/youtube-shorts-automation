import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from generator.text.source_integrity import (
    content_word_count,
    detect_truncation,
    normalize_story_text,
)


MIN_SCRIPT_CHARS = 650
TARGET_MIN_SCRIPT_CHARS = 650
TARGET_MAX_SCRIPT_CHARS = 950
MAX_SCRIPT_CHARS = 1050
MIN_SOURCE_WORDS = 90
MIN_SOURCE_CHARS = 550

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']+")
_AGE_RE = re.compile(r"\b(?:1[0-7])\s*(?:f|m|yo|y/o|year old|years old)?\b|\b(?:minor|underage|teen|teenage|high school)\b")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])[\"'”’)]*\s+")
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
    "barged",
    "bit",
    "bite",
    "bitten",
    "bill",
    "borrowed",
    "blocked",
    "broke",
    "called me",
    "calls nonstop",
    "caught",
    "charged",
    "charge",
    "cost",
    "cover the cost",
    "covering the cost",
    "covers rent",
    "cut off",
    "deleted",
    "demanded",
    "denied",
    "destroyed",
    "dented",
    "dent",
    "drank",
    "drop everything",
    "dumped",
    "embarrassed",
    "entered",
    "exposed",
    "forced",
    "gave away",
    "gave out my number",
    "gave my number",
    "handed my number",
    "hands out",
    "hands out my phone",
    "hid",
    "humiliated",
    "ignored",
    "in my name",
    "invited",
    "invoice",
    "kept",
    "kicked",
    "left",
    "lied",
    "locked",
    "made me pay",
    "mocked",
    "nightmare",
    "nightmares",
    "opened",
    "paid for",
    "pay it",
    "pay the",
    "paying",
    "parked",
    "pressured",
    "presented",
    "promised",
    "punctured",
    "refused",
    "reported",
    "ruined",
    "secret",
    "shared",
    "selfish",
    "saying no",
    "single",
    "snap",
    "snapped",
    "snaps",
    "told me i was selfish",
    "spent",
    "spread",
    "stole",
    "strolling into",
    "threatened",
    "told everyone",
    "took",
    "took credit",
    "trashed",
    "triple my income",
    "turned",
    "unvaccinated",
    "used",
    "vet bill",
    "walked in",
    "walks in",
    "walked into",
    "walking in",
    "whole birthday dinner",
    "whole bill",
    "entire birthday dinner",
    "entire bill",
    "without a yes",
    "without asking",
    "without consent",
    "without my",
    "without permission",
    "without telling",
    "without their",
    "wouldn't",
    "my card",
    "my driveway",
    "on my card",
    "volunteered to pay",
    "wound",
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
    "condensed",
    "compressed",
    "cut",
    "dramatized",
    "focused",
    "kept",
    "plausible",
    "preserved",
    "sharpened",
    "streamlined",
    "tightened",
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
_AI_TEMPLATE_PHRASES = (
    "acted like i was the problem",
    "the unreasonable one",
    "people are split",
    "half the people",
    "keep the peace",
    "let it go",
    "crossed a boundary",
    "crossed the line",
    "the situation",
    "the issue",
    "the conflict",
    "the drama",
    "what changed everything",
    "that was when",
    "instead of owning it",
    "i decided to stand my ground",
    "i set a boundary",
    "i held the boundary",
    "the boundary was simple",
    "i had one clear boundary in this situation",
    "what made it worse was",
    "the proof was clear",
    "now people are split",
    "smooth it over",
    "without asking me first",
    "i tried to keep it calm",
    "my limit did not matter",
    "in this situation",
)
_ABSTRACT_CONFLICT_TERMS = {
    "boundary",
    "situation",
    "issue",
    "conflict",
    "drama",
    "proof",
    "evidence",
    "disrespect",
    "respect",
    "uncomfortable",
    "unreasonable",
    "overreacting",
    "consequence",
    "decision",
    "pressure",
}
_CONCRETE_SIGNAL_TERMS = {
    "app",
    "bill",
    "birthday",
    "blender",
    "camera",
    "car",
    "card",
    "chat",
    "coffee",
    "concrete",
    "counter",
    "deposit",
    "dinner",
    "door",
    "doorbell",
    "driveway",
    "email",
    "file",
    "food",
    "fund",
    "gate",
    "group chat",
    "hallway",
    "invoice",
    "kitchen",
    "laundry",
    "lid",
    "manager",
    "message",
    "messages",
    "medicare",
    "parking",
    "photo",
    "receipt",
    "rental",
    "restaurant",
    "room",
    "screenshot",
    "screenshots",
    "server",
    "storage",
    "table",
    "text",
    "texts",
    "timestamp",
    "van",
    "video",
    "washer",
}
_CONCRETE_ACTION_TERMS = {
    "accused",
    "asked",
    "borrowed",
    "broke",
    "called",
    "charged",
    "complained",
    "deleted",
    "demanded",
    "dented",
    "entered",
    "exploded",
    "gave",
    "left",
    "lied",
    "messaged",
    "paid",
    "parked",
    "posted",
    "refused",
    "sent",
    "showed",
    "snapped",
    "spent",
    "told",
    "took",
    "trashed",
    "used",
}
_DANGLING_TRAILING_WORDS = {
    "a",
    "an",
    "and",
    "because",
    "but",
    "her",
    "his",
    "like",
    "my",
    "our",
    "the",
    "their",
    "without",
    "your",
}
_INCOMPLETE_TRAILING_PHRASES = (
    "used my",
    "felt like",
    "made the",
    "stopped taking",
    "answering his",
    "answering her",
    "answering their",
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
    lines = metadata.get("voiceover_lines") or metadata.get("script") or []
    return " ".join(str(line or "").strip() for line in lines).strip()


def validate_script_quality(metadata: Dict[str, Any], post: Dict[str, Any]) -> List[ScriptQualityIssue]:
    issues: List[ScriptQualityIssue] = []
    source = build_source_profile(post)
    lines = [str(line or "").strip() for line in (metadata.get("voiceover_lines") or metadata.get("script") or []) if str(line or "").strip()]
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
    if len(lines) < 7:
        issues.append(ScriptQualityIssue("too_few_beats", "script should have 7 to 10 complete voiceover lines"))
    if len(lines) > 10:
        issues.append(ScriptQualityIssue("too_many_beats", "script should stay within 7 to 10 complete voiceover lines"))
    if lines and len(lines[0]) > 120:
        issues.append(ScriptQualityIssue("hook_too_long", f"first line is too long ({len(lines[0])} chars)"))
    long_lines = [idx + 1 for idx, line in enumerate(lines) if len(line) > 170]
    if long_lines:
        issues.append(ScriptQualityIssue("line_too_long", f"voiceover lines exceed 170 chars: {long_lines[:3]}"))

    ai_template_phrase = _first_ai_template_phrase(metadata, lines)
    if ai_template_phrase:
        issues.append(
            ScriptQualityIssue(
                "ai_template_phrase",
                f"script uses generic AI-storytelling phrase: {ai_template_phrase}",
            )
        )

    dangling = _first_incomplete_sentence_issue(metadata, lines)
    if dangling:
        issues.append(ScriptQualityIssue("template_storytelling", f"incomplete or dangling sentence: {dangling}"))

    abstract_count, concrete_signal_count, abstract_ratio = _abstract_language_profile(text)
    if abstract_count >= 5 and (
        abstract_ratio > 0.045
        or abstract_count > max(3, concrete_signal_count + 2)
    ):
        issues.append(
            ScriptQualityIssue(
                "abstract_language_overload",
                f"abstract conflict wording dominates concrete detail ({abstract_count} abstract terms, {concrete_signal_count} concrete signals)",
            )
        )

    if source.content:
        concrete_detail_count = _source_grounded_detail_count(source.title, source.content, text)
        if concrete_detail_count < 4:
            issues.append(
                ScriptQualityIssue(
                    "missing_concrete_details",
                    f"script includes too few source-grounded concrete details ({concrete_detail_count}/4)",
                )
            )
        elif concrete_detail_count < 6:
            issues.append(
                ScriptQualityIssue(
                    "low_specificity",
                    f"script has limited source-specific detail density ({concrete_detail_count} concrete details)",
                )
            )

    generic_line_count = _generic_reusable_line_count(lines, source.title, source.content)
    if lines and generic_line_count / len(lines) > 0.4:
        issues.append(
            ScriptQualityIssue(
                "generic_reusable_line",
                f"too many lines could be reused for any generic conflict ({generic_line_count}/{len(lines)})",
            )
        )

    first_sentence = _first_sentence(text)
    first_lower = first_sentence.lower()
    if not first_sentence:
        issues.append(ScriptQualityIssue("missing_hook", "first sentence is missing"))
    elif len(first_sentence) > 150:
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

    if not lines or not lines[-1].strip().endswith("?"):
        issues.append(ScriptQualityIssue("missing_engagement_question", "final voiceover line should be a separate direct question"))
    elif any("?" in line for line in lines[:-1]):
        issues.append(ScriptQualityIssue("question_not_separate", "only the final voiceover line should contain the viewer question"))

    hook_type = str(metadata.get("hook_type") or "").strip()
    if len(hook_type) < 3:
        issues.append(ScriptQualityIssue("missing_hook_type", "hook_type should label the opening pattern"))

    first_two_seconds = str(metadata.get("first_2_seconds") or "").strip()
    first_two_lower = first_two_seconds.lower()
    if len(first_two_seconds) < 12:
        issues.append(ScriptQualityIssue("weak_first_2_seconds", "first_2_seconds should contain the concrete opening phrase"))
    elif len(first_two_seconds) > 95:
        issues.append(ScriptQualityIssue("first_2_seconds_too_long", "first_2_seconds should stay short enough to land immediately"))
    elif first_two_lower.startswith(_SLOW_HOOK_PREFIXES) or not _has_hook_stakes(first_two_lower):
        issues.append(ScriptQualityIssue("weak_first_2_seconds", "first_2_seconds should carry the crossed line, cost, accusation, or villain framing"))

    turning_point = str(metadata.get("turning_point") or "").strip()
    if len(turning_point) < 30:
        issues.append(ScriptQualityIssue("weak_turning_point", "turning_point should name the moment the story gets worse"))

    payoff_line = str(metadata.get("payoff_line") or "").strip()
    if len(payoff_line) < 24:
        issues.append(ScriptQualityIssue("weak_payoff_line", "payoff_line should be a short final conflict statement before the question"))

    retention_risk = str(metadata.get("retention_risk") or "").strip()
    if len(retention_risk) < 45:
        issues.append(ScriptQualityIssue("weak_retention_risk", "retention_risk should explain the likely swipe-away risk and mitigation"))

    cut_plan = metadata.get("cut_plan") or []
    if not isinstance(cut_plan, list) or len([cut for cut in cut_plan if str(cut).strip()]) < 4:
        issues.append(ScriptQualityIssue("weak_cut_plan", "cut_plan should include at least 4 concrete visual beats"))

    if metadata.get("bg_strategy") not in {"story", "asmr", "hybrid"}:
        issues.append(ScriptQualityIssue("invalid_bg_strategy", "bg_strategy must be story, asmr, or hybrid"))

    if lines:
        final_paragraph_len = len(lines[-1])
        if final_paragraph_len > 260:
            issues.append(
                ScriptQualityIssue(
                    "late_drag",
                    f"final line is overloaded ({final_paragraph_len} chars); split payoff and viewer question earlier",
                    hard=False,
                )
            )

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

    target_min_chars = _int_env("SCRIPT_TARGET_MIN_CHARS", TARGET_MIN_SCRIPT_CHARS)
    target_max_chars = max(target_min_chars, _int_env("SCRIPT_TARGET_MAX_CHARS", TARGET_MAX_SCRIPT_CHARS))
    if not (target_min_chars <= char_count <= target_max_chars):
        issues.append(
            ScriptQualityIssue(
                "outside_target_length",
                f"script is valid but outside the preferred {target_min_chars}-{target_max_chars} char target ({char_count})",
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
        "bills",
        "consequence",
        "conflict",
        "damage",
        "daycare",
        "decision",
        "debate",
        "dilemma",
        "entitlement",
        "groceries",
        "household",
        "household imbalance",
        "invasion",
        "kids",
        "moral split",
        "money tension",
        "privacy",
        "pressure",
        "property",
        "rent",
        "stakes",
        "unfair",
        "walk away",
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


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
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


def _first_ai_template_phrase(metadata: Dict[str, Any], lines: List[str]) -> str:
    fields = [
        str(metadata.get("title") or ""),
        str(metadata.get("public_title") or ""),
        str(metadata.get("first_2_seconds") or ""),
        str(metadata.get("payoff_line") or ""),
        str(metadata.get("viewer_question") or ""),
        " ".join(lines),
    ]
    combined = "\n".join(fields).lower()
    for phrase in _AI_TEMPLATE_PHRASES:
        if phrase == "crossed the line":
            if _phrase_outside_dialogue(combined, phrase):
                return phrase
            continue
        if phrase in combined:
            return phrase
    return ""


def _phrase_outside_dialogue(text: str, phrase: str) -> bool:
    search_from = 0
    while True:
        idx = text.find(phrase, search_from)
        if idx == -1:
            return False
        before = text[:idx]
        double_quotes = before.count('"')
        in_quote = double_quotes % 2 == 1
        if not in_quote:
            return True
        search_from = idx + len(phrase)


def _first_incomplete_sentence_issue(metadata: Dict[str, Any], lines: List[str]) -> str:
    candidates = [
        str(metadata.get("title") or ""),
        str(metadata.get("public_title") or ""),
        str(metadata.get("first_2_seconds") or ""),
        str(metadata.get("turning_point") or ""),
        str(metadata.get("payoff_line") or ""),
        str(metadata.get("viewer_question") or ""),
        *lines,
    ]
    for candidate in candidates:
        if _has_incomplete_sentence(candidate):
            return candidate.strip()
    return ""


def _has_incomplete_sentence(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return False
    for segment in re.split(r"(?<=[.!?])\s+|$", cleaned):
        segment = segment.strip()
        if not segment:
            continue
        tail = segment.rstrip(" .,!?:;").lower()
        if not tail:
            continue
        if any(tail.endswith(phrase) for phrase in _INCOMPLETE_TRAILING_PHRASES):
            return True
        last_word = tail.split()[-1]
        if last_word in _DANGLING_TRAILING_WORDS:
            return True
        if re.search(r"\b(?:made|used|took|paid|charged|asked for|posted)\s+(?:the|my|his|her|their|our)\s*,", tail):
            return True
    return False


def _abstract_language_profile(text: str) -> tuple[int, int, float]:
    lowered = str(text or "").lower()
    words = _WORD_RE.findall(lowered)
    word_count = max(1, len(words))
    abstract_count = sum(len(re.findall(rf"\b{re.escape(term)}\b", lowered)) for term in _ABSTRACT_CONFLICT_TERMS)
    concrete_count = _concrete_signal_count(lowered)
    return abstract_count, concrete_count, abstract_count / word_count


def _concrete_signal_count(text: str) -> int:
    lowered = str(text or "").lower()
    count = 0
    count += sum(len(re.findall(rf"\b{re.escape(term)}\b", lowered)) for term in _CONCRETE_SIGNAL_TERMS)
    count += len(re.findall(r"\$?\b\d+(?:\.\d+)?\b|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b", lowered))
    count += len(re.findall(r"\b(?:am|pm|minutes?|hours?|days?|weeks?|months?|years?)\b", lowered))
    count += sum(len(re.findall(rf"\b{re.escape(term)}\b", lowered)) for term in _CONCRETE_ACTION_TERMS)
    return count


def _source_grounded_detail_count(title: str, content: str, script: str) -> int:
    source_text = f"{title} {content}".lower()
    script_lower = str(script or "").lower()
    details: set[str] = set()
    for term in _CONCRETE_SIGNAL_TERMS | _CONCRETE_ACTION_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", source_text) and re.search(rf"\b{re.escape(term)}\b", script_lower):
            details.add(term)
    for token in _source_concrete_tokens(source_text):
        if re.search(rf"\b{re.escape(token)}\b", script_lower):
            details.add(token)
    for match in re.finditer(r"\$?\b\d+(?:\.\d+)?\b|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b", source_text):
        if match.group(0) in script_lower:
            details.add(match.group(0))
    return len(details)


def _source_concrete_tokens(source_text: str) -> set[str]:
    tokens: set[str] = set()
    for match in _WORD_RE.finditer(source_text.lower()):
        token = match.group(0).strip("'")
        if len(token) < 5:
            continue
        if token in _STOPWORDS or token in _ABSTRACT_CONFLICT_TERMS:
            continue
        tokens.add(token)
    return tokens


def _generic_reusable_line_count(lines: List[str], title: str, content: str) -> int:
    source_tokens = _source_concrete_tokens(f"{title} {content}")
    generic_count = 0
    for line in lines:
        lowered = str(line or "").lower()
        if not lowered.strip():
            continue
        concrete_signals = _concrete_signal_count(lowered)
        grounded_tokens = sum(1 for token in source_tokens if re.search(rf"\b{re.escape(token)}\b", lowered))
        abstract_terms = sum(1 for term in _ABSTRACT_CONFLICT_TERMS if re.search(rf"\b{re.escape(term)}\b", lowered))
        line_words = _WORD_RE.findall(lowered)
        if concrete_signals + grounded_tokens >= 2:
            continue
        if abstract_terms >= 1 or len(line_words) < 9:
            generic_count += 1
    return generic_count
