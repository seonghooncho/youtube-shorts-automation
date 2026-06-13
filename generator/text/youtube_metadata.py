from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


TITLE_HASHTAGS = ("#shorts", "#story")
DEFAULT_VIDEO_TAGS = (
    "shorts",
    "story",
    "storytime",
    "drama",
    "short story",
)
MAX_TITLE_CHARS = 100
MAX_TITLE_CONFLICT_CHARS = 68
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
_BANNED_PUBLIC_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:aita\b|am\s+i\s+the\s+asshole\b|am\s+i\s+wrong\b|did\s+i\s+overreact\b)",
    re.IGNORECASE,
)
_AITA_CLEANUP_RE = re.compile(
    r"^\s*(?:aita|am\s+i\s+the\s+asshole|am\s+i\s+wrong|did\s+i\s+overreact)\s*(?:for|because|when|after|about)?\s*",
    re.IGNORECASE,
)
_GENERIC_TITLE_RE = re.compile(
    r"^(?:the argument started|a family bill turned|story\b|a fast storytime|did i overreact|was i wrong)",
    re.IGNORECASE,
)
_TITLE_ACTOR_TERMS = {
    "aunt",
    "brother",
    "boyfriend",
    "cousin",
    "coworker",
    "dad",
    "daughter",
    "ex",
    "family",
    "fiance",
    "fiancee",
    "fiancée",
    "friend",
    "girlfriend",
    "he",
    "landlord",
    "manager",
    "mom",
    "mother",
    "neighbor",
    "parents",
    "roommate",
    "she",
    "sister",
    "stepmum",
    "stepmom",
    "uncle",
}
_TITLE_OBJECT_TERMS = {
    "account",
    "alert",
    "alerts",
    "app",
    "bank",
    "banks",
    "babysitter",
    "babysitting",
    "bill",
    "bills",
    "bloodwork",
    "camera",
    "card",
    "cat",
    "chat",
    "childcare",
    "contract",
    "contracts",
    "coffee",
    "dinner",
    "dinners",
    "driveway",
    "ex",
    "food",
    "garage",
    "groceries",
    "hallway",
    "invoice",
    "keys",
    "kids",
    "job",
    "laundry",
    "mail",
    "manager",
    "machine",
    "messages",
    "notice",
    "number",
    "package",
    "password",
    "phone",
    "printer",
    "receipt",
    "rental",
    "reservation",
    "rent",
    "scratches",
    "screenshot",
    "shelf",
    "sign",
    "storage",
    "text",
    "timestamp",
    "toner",
    "unit",
    "van",
    "vet",
    "washer",
}
_TITLE_OBJECT_PHRASES = {
    "building chat",
    "dinner bill",
    "dinner reservation",
    "door camera",
    "grocery fund",
    "group chat",
    "guest room",
    "hallway camera",
    "lunch order",
    "mail shelf",
    "office coffee fund",
    "parking sign",
    "rental deposit",
    "shared balcony",
    "storage unit",
    "streaming password",
}
_TITLE_ACTION_TERMS = {
    "accused",
    "bit",
    "bites",
    "blamed",
    "blocked",
    "borrowed",
    "called",
    "charged",
    "changed",
    "deleted",
    "demanded",
    "dented",
    "drained",
    "entered",
    "exposed",
    "filled",
    "gave",
    "hid",
    "ignored",
    "kept",
    "left",
    "lied",
    "locked",
    "moved",
    "parked",
    "paid",
    "posted",
    "put",
    "recorded",
    "refused",
    "reported",
    "returned",
    "rang",
    "shared",
    "sending",
    "showed",
    "shared",
    "spent",
    "stole",
    "took",
    "trashed",
    "used",
    "walked",
    "walks",
    "babysit",
    "babysitting",
    "erased",
    "expected",
    "mocked",
    "ordered",
    "slept",
    "sleep",
    "texted",
}
_TITLE_DANGLING_WORDS = {"a", "an", "and", "for", "from", "his", "her", "my", "of", "on", "the", "to", "with"}


def apply_youtube_metadata_style(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the channel's Shorts metadata house style in-place and return metadata."""
    viewer_question = str(metadata.get("viewer_question") or "").strip()
    source_title = str(metadata.get("source_title") or metadata.get("title") or "").strip()
    metadata["source_title"] = source_title
    metadata["public_title"] = build_public_title(
        str(metadata.get("public_title") or metadata.get("title") or source_title),
        source_title=source_title,
    )
    hashtags = title_hashtags_for_source(str(metadata.get("source_provider") or ""))
    styled = sanitize_upload_metadata(
        title=format_youtube_title(metadata["public_title"], hashtags=hashtags),
        description=format_youtube_description(
            str(metadata.get("description") or ""),
            viewer_question=viewer_question,
            hashtags=hashtags,
        ),
        tags=merge_youtube_tags(metadata.get("tags") or []),
        title_hashtags=hashtags,
    )
    metadata["title"] = styled["title"]
    metadata["description"] = styled["description"]
    metadata["tags"] = styled["tags"]
    return metadata


def sanitize_upload_metadata(
    title: str,
    description: str,
    tags: Iterable[str],
    title_hashtags: Iterable[str] = TITLE_HASHTAGS,
) -> Dict[str, Any]:
    """Return upload-safe YouTube metadata or raise when internal values are present."""
    unsafe_field = unsafe_upload_metadata_reason(title, description, tags)
    if unsafe_field:
        raise ValueError(unsafe_field)
    clean_title = format_youtube_title(_clean_public_text(title) or "Story", hashtags=title_hashtags)
    clean_description = _clean_public_text(description)[:MAX_DESCRIPTION_CHARS].strip()
    clean_tags = merge_youtube_tags(_clean_public_text(tag) for tag in tags or [])
    if not clean_description:
        clean_description = format_youtube_description(
            "A fast storytime Short about a relatable everyday conflict.",
            hashtags=title_hashtags,
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
    clean_title = build_public_title(_SPACE_RE.sub(" ", clean_title).strip() or "Story")
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


def build_public_title(candidate: str, source_title: str = "") -> str:
    """Return a human-facing title without hashtags or AITA-style framing."""
    title = _strip_hashtags(_clean_public_text(candidate)).strip(" .,-")
    source = _strip_hashtags(_clean_public_text(source_title)).strip(" .,-")
    if _is_banned_public_title(title):
        title = _AITA_CLEANUP_RE.sub("", title).strip(" .,-")
    if not title and source:
        title = _AITA_CLEANUP_RE.sub("", source).strip(" .,-")
    if _is_banned_public_title(title) and source:
        title = _AITA_CLEANUP_RE.sub("", source).strip(" .,-")
    title = _SPACE_RE.sub(" ", title).strip(" .,-")
    title = _sentence_title_case(title)
    if title.isupper():
        title = title.title()
    if not title:
        title = "A Family Bill Turned Into A Group Argument"
    if len(title) > MAX_TITLE_CONFLICT_CHARS:
        title = _truncate_at_word(title, MAX_TITLE_CONFLICT_CHARS).strip(" .,-")
    if _is_banned_public_title(title):
        title = _fallback_public_title_from_source(source) or "He Used My Storage Unit Like A Free Garage"
    if title_quality_reason(title):
        title = _fallback_public_title_from_source(source) or title
    return title


def title_quality_reason(title: str) -> str:
    clean = _strip_hashtags(_clean_public_text(title)).strip(" .,-")
    lowered = clean.lower()
    words = set(re.findall(r"[a-z][a-z']+", lowered))
    if not clean:
        return "empty"
    if _is_banned_public_title(clean):
        return "aita_prefix"
    if _GENERIC_TITLE_RE.search(clean):
        return "generic_title"
    if len(clean) > MAX_TITLE_CONFLICT_CHARS:
        return "too_long"
    if clean.rstrip(" .,!?:;").split(" ")[-1].lower() in _TITLE_DANGLING_WORDS:
        return "dangling_title"
    has_object = bool(words & _TITLE_ACTOR_TERMS or words & _TITLE_OBJECT_TERMS or _has_phrase(lowered, _TITLE_OBJECT_PHRASES))
    if not has_object:
        return "missing_actor_or_object"
    if not (words & _TITLE_ACTION_TERMS):
        return "missing_conflict_action"
    return ""


def title_hashtags_for_source(source_provider: str) -> tuple[str, ...]:
    if str(source_provider or "").strip().lower() == "reddit" and _include_reddit_hashtag():
        return ("#shorts", "#story", "#reddit")
    return TITLE_HASHTAGS


def _include_reddit_hashtag() -> bool:
    # Default channel style intentionally avoids Reddit-looking titles.
    return False


def _is_banned_public_title(title: str) -> bool:
    return bool(_BANNED_PUBLIC_TITLE_PREFIX_RE.search(str(title or "")))


def _sentence_title_case(title: str) -> str:
    title = str(title or "").strip()
    if not title:
        return ""
    if title == title.upper() or title == title.lower():
        return title.title()
    return title[:1].upper() + title[1:]


def _fallback_public_title_from_source(source_title: str) -> str:
    lowered = str(source_title or "").lower()
    patterns = (
        (("driveway",), "He Parked In My Driveway, Then Called Me Petty"),
        (("birthday", "bill"), "She Put Twelve Dinners On My Card"),
        (("coworker", "credit"), "My Coworker Lied In Front Of Our Manager"),
        (("package", "accused"), "She Accused Me In The Building Chat"),
        (("storage", "unit"), "He Used My Storage Unit Like A Free Garage"),
        (("laundry", "machine"), "She Left Her Laundry In Both Machines"),
        (("grocery", "fund"), "My Roommate Spent Our Grocery Fund"),
        (("lunch", "order"), "My Coworker Changed His Lunch Order"),
        (("rental", "deposit"), "They Kept My Rental Deposit"),
        (("borrowed", "car"), "He Returned My Car Empty And Scratched"),
        (("office", "coffee", "fund"), "They Drained The Office Coffee Fund"),
        (("streaming", "password"), "She Shared My Streaming Password"),
        (("shared", "balcony"), "She Filled Our Shared Balcony With Plants"),
        (("dinner", "reservation"), "She Put Twelve Dinners On My Card"),
        (("printer", "toner"), "He Hid My Printer Toner"),
    )
    for terms, title in patterns:
        if all(term in lowered for term in terms):
            return title
    return ""


def _has_phrase(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)


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
