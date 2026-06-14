# shared/jobs/filter_viable_posts.py

import os
import json
import re
from typing import List, Literal
from dotenv import load_dotenv, find_dotenv
import openai
from pydantic import BaseModel, Field, ValidationError
from generator.text.script_quality import build_source_profile, source_reject_reason_for_marketability
from shared.llm.circuit_breaker import (
    LlmCircuitOpen,
    assert_llm_circuit_closed,
    is_llm_quota_or_auth_error,
    is_llm_rate_limit_error,
    llm_circuit_summary,
    open_llm_circuit,
)
from shared.utils.config import RAW_POSTS_FILE, VIABLE_POSTS_FILE


# -----------------------------
# Env & OpenAI Client
# -----------------------------
def _get_client() -> openai.OpenAI:
    assert_llm_circuit_closed("source_filter_client")
    # 현재 작업 디렉터리 기준으로 .env 탐색/로드
    env_path = find_dotenv(usecwd=True)
    load_dotenv(env_path)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        open_llm_circuit("OPENAI_API_KEY is not configured", "source_filter_client")
        raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다. .env 또는 환경변수를 확인하세요.")
    return openai.OpenAI(api_key=api_key)


# -----------------------------
# Helpers
# -----------------------------
_CODE_FENCE_RE = re.compile(r"^```(?:json|txt|[\w-]+)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


class SourceScorecard(BaseModel):
    decision: Literal["YES", "NO"]
    relatability: int = Field(..., ge=1, le=5)
    conflict_clarity: int = Field(..., ge=1, le=5)
    stakes: int = Field(..., ge=1, le=5)
    debate_potential: int = Field(..., ge=1, le=5)
    safe_adaptability: int = Field(..., ge=1, le=5)
    visualizability: int = Field(..., ge=1, le=5)
    gate_fit_score: int = Field(3, ge=1, le=5)
    hook_in_one_sentence: int = Field(3, ge=1, le=5)
    receipt_strength: int = Field(3, ge=1, le=5)
    visual_matchability: int = Field(3, ge=1, le=5)
    length_fit_score: int = Field(3, ge=1, le=5)
    metadata_repairability: int = Field(3, ge=1, le=5)
    retention_risk: Literal["low", "medium", "high"]
    archetype: str
    reason: str

def _clean_response_text(text: str) -> str:
    """코드펜스/마크다운 블록 제거 및 공백 트리밍."""
    if not text:
        return ""
    text = text.strip()
    m = _CODE_FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    return text.strip()

def _normalize_yes_no(text: str) -> str:
    """
    모델 출력에서 YES/NO 판정:
    - 정확히 YES/NO면 그대로
    - 장황응답이면 'YES'만 포함 → YES, 'NO'만 포함 → NO
    - 그 외/공백 → 빈 문자열(불명)
    """
    if not text:
        return ""
    t = _clean_response_text(text).upper()
    if t in ("YES", "Y"):
        return "YES"
    if t in ("NO", "N"):
        return "NO"
    contains_yes = "YES" in t
    contains_no = "NO" in t
    if contains_yes and not contains_no:
        return "YES"
    if contains_no and not contains_yes:
        return "NO"
    return ""


def _scorecard_average(scorecard: SourceScorecard) -> float:
    fields = [
        scorecard.relatability,
        scorecard.conflict_clarity,
        scorecard.stakes,
        scorecard.debate_potential,
        scorecard.safe_adaptability,
        scorecard.visualizability,
    ]
    return round(sum(fields) / len(fields), 2)


def source_acceptance_score(scorecard: SourceScorecard) -> float:
    fields = [
        scorecard.gate_fit_score,
        scorecard.hook_in_one_sentence,
        scorecard.receipt_strength,
        scorecard.visual_matchability,
        scorecard.length_fit_score,
        scorecard.metadata_repairability,
        scorecard.debate_potential,
        scorecard.conflict_clarity,
    ]
    return round(sum(fields) / len(fields), 2)


def _source_priority_score(scorecard: SourceScorecard) -> float:
    fields = [
        source_acceptance_score(scorecard),
        scorecard.stakes,
        scorecard.safe_adaptability,
        scorecard.visualizability,
    ]
    return round(sum(fields) / len(fields), 2)


def _gate_fit_passes(scorecard: SourceScorecard) -> bool:
    return (
        scorecard.gate_fit_score >= 4
        and scorecard.hook_in_one_sentence >= 4
        and scorecard.visual_matchability >= 4
        and scorecard.length_fit_score >= 4
        and scorecard.receipt_strength >= 3
    )


def _parse_source_scorecard(raw: str) -> SourceScorecard:
    text = _clean_response_text(raw)
    try:
        return SourceScorecard.model_validate_json(text)
    except ValidationError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return SourceScorecard.model_validate_json(text[start : end + 1])
        raise


def _ask_source_scorecard(client: openai.OpenAI, prompt: str, model: str) -> SourceScorecard | None:
    assert_llm_circuit_closed("source_filter")
    system_base = (
        "You are a strict YouTube Shorts source evaluator. "
        "Return only valid JSON matching the requested scorecard schema. "
        "Do not include markdown, code fences, or commentary."
    )
    messages_templates = [
        [
            {"role": "system", "content": system_base},
            {"role": "user", "content": prompt},
        ],
        [
            {
                "role": "system",
                "content": system_base + " Any missing key is an error. Use uppercase YES or NO for decision.",
            },
            {"role": "user", "content": prompt + "\n\nReturn only the JSON object."},
        ],
    ]
    quota_error: Exception | None = None
    for attempt, messages in enumerate(messages_templates, start=1):
        try:
            assert_llm_circuit_closed("source_filter")
            kwargs = {
                "model": model,
                "input": messages,
                "max_output_tokens": 512,
                "text": {"format": {"type": "json_object"}, "verbosity": _text_verbosity(model)},
            }
            kwargs.update(_reasoning_kwargs_for_model(model))
            resp = client.responses.create(**kwargs)
            return _parse_source_scorecard(resp.output_text or "")
        except Exception as e:
            print(f"⚠️ source scorecard 호출 실패 (시도 {attempt}/{len(messages_templates)}): {e}")
            if _is_llm_quota_error(str(e)) or is_llm_quota_or_auth_error(e) or is_llm_rate_limit_error(e):
                open_llm_circuit(str(e), "source_filter")
                quota_error = e
                break
    if quota_error:
        raise RuntimeError(f"llm_quota_unavailable: {quota_error}") from quota_error
    return None


def _ask_yes_no(client: openai.OpenAI, prompt: str, model: str) -> str:
    """
    Chat Completions를 사용해 YES/NO 한 단어를 받아온다.
    최대 2회까지 재시도(지시 강도 증가).
    """
    system_base = (
        "You are a strict validator. "
        "Answer with exactly one word: YES or NO. "
        "Do NOT include punctuation, explanations, or code fences."
    )

    messages_templates = [
        # 1차 시도: 기본 지시
        [
            {"role": "system", "content": system_base},
            {"role": "user", "content": prompt},
        ],
        # 2차 시도: 더 강하게 한정
        [
            {
                "role": "system",
                "content": system_base
                + " If you output anything other than YES or NO, that is an error.",
            },
            {"role": "user", "content": prompt + "\n\n(Answer with only YES or NO.)"},
        ],
    ]

    for attempt, messages in enumerate(messages_templates, start=1):
        try:
            assert_llm_circuit_closed("source_filter_yes_no")
            kwargs = {
                "model": model,
                "input": messages,
                "max_output_tokens": 128,
            }
            kwargs.update(_reasoning_kwargs_for_model(model))
            resp = client.responses.create(**kwargs)
            raw = (resp.output_text or "").strip()
            verdict = _normalize_yes_no(raw)
            if verdict:
                return verdict
        except Exception as e:
            print(f"⚠️ YES/NO 호출 실패 (시도 {attempt}/{len(messages_templates)}): {e}")
            if _is_llm_quota_error(str(e)) or is_llm_quota_or_auth_error(e) or is_llm_rate_limit_error(e):
                open_llm_circuit(str(e), "source_filter_yes_no")
                raise

    return ""  # 불명


def _reasoning_kwargs_for_model(model: str) -> dict:
    configured_effort = os.getenv("FILTER_REASONING_EFFORT", "").strip()
    if configured_effort:
        return {"reasoning": {"effort": configured_effort}}
    if model == "gpt-5.5" or model.startswith("gpt-5.5-"):
        return {"reasoning": {"effort": "low"}}
    if model.startswith("gpt-5") and not model.startswith("gpt-5.4"):
        return {"reasoning": {"effort": "minimal"}}
    return {}


def _text_verbosity(model: str) -> str:
    configured = os.getenv("FILTER_TEXT_VERBOSITY", "").strip().lower()
    if configured in {"low", "medium", "high"}:
        return configured
    if (model or "").lower().startswith("gpt-4.1"):
        return "medium"
    return "low"


def _local_precheck(post: dict) -> str:
    source = build_source_profile(post)
    if source.is_truncated:
        return f"source may be truncated: {source.truncation_reason or 'unknown reason'}"
    marketability_reject = source_reject_reason_for_marketability(post)
    if marketability_reject:
        return marketability_reject
    if source.char_count < 550 or source.word_count < 90:
        return f"source too thin ({source.char_count} chars, {source.word_count} words)"
    return ""


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def local_source_priority(post: dict) -> float:
    source = build_source_profile(post)
    title = str(post.get("title") or "")
    content = str(post.get("content") or "")
    lowered = f"{title} {content}".lower()
    score = 0.0
    words = max(0, source.word_count)
    if words >= 120:
        score += 1.2
    elif words >= 90:
        score += 0.8
    if words > 260:
        score -= 0.2

    conflict_terms = (
        "accused",
        "blamed",
        "charged",
        "demanded",
        "refused",
        "used my",
        "without asking",
        "without permission",
        "put it on my card",
        "parked",
        "locked",
        "reported",
        "spent",
        "took",
        "lied",
    )
    receipt_terms = ("text", "messages", "receipt", "screenshot", "camera", "bill", "appointment", "timestamp", "estimate")
    pressure_terms = (
        "card",
        "deposit",
        "driveway",
        "package",
        "landlord",
        "manager",
        "coworker",
        "roommate",
        "aunt",
        "brother",
        "family",
        "bank",
        "vet",
        "bloodwork",
    )
    visual_terms = (
        "car",
        "door",
        "hallway",
        "phone",
        "chat",
        "camera",
        "receipt",
        "bill",
        "apartment",
        "office",
        "driveway",
        "package",
        "cat",
    )
    unsafe_terms = ("underage", "minor", "teen romance", "sexual", "nude", "graphic", "slur")

    score += min(2.0, sum(0.45 for term in conflict_terms if term in lowered))
    score += min(1.6, sum(0.35 for term in receipt_terms if term in lowered))
    score += min(1.4, sum(0.25 for term in pressure_terms if term in lowered))
    score += min(1.0, sum(0.15 for term in visual_terms if term in lowered))
    if "?" in content[-500:]:
        score += 0.3
    if any(term in lowered for term in unsafe_terms):
        score -= 4.0
    return round(score, 2)


_FEASIBILITY_ACTION_TERMS = (
    "accused",
    "blamed",
    "charged",
    "demanded",
    "refused",
    "parked",
    "locked",
    "reported",
    "spent",
    "took",
    "used",
    "returned",
    "changed",
    "moved",
    "posted",
    "left",
    "ignored",
    "exposed",
    "recorded",
    "showed",
    "blocked",
    "borrowed",
    "deleted",
    "kept",
    "without asking",
    "without permission",
    "put it on my card",
    "lied",
)

_FEASIBILITY_OBJECT_TERMS = (
    "car",
    "driveway",
    "card",
    "bill",
    "receipt",
    "group chat",
    "building chat",
    "camera",
    "package",
    "laundry",
    "washer",
    "machine",
    "storage unit",
    "apartment",
    "landlord",
    "phone",
    "text",
    "message",
    "screenshot",
    "timestamp",
    "bank",
    "vet",
    "bloodwork",
    "cat",
    "van",
    "dinner",
    "manager",
    "office",
    "deposit",
    "keys",
)

_FEASIBILITY_ACTOR_TERMS = (
    "neighbor",
    "roommate",
    "coworker",
    "manager",
    "aunt",
    "brother",
    "sister",
    "landlord",
    "tenant",
    "friend",
    "family",
    "he ",
    "she ",
)

_FEASIBILITY_RECEIPT_TERMS = (
    "receipt",
    "screenshot",
    "camera",
    "door camera",
    "text",
    "message",
    "group chat",
    "bill",
    "invoice",
    "estimate",
    "timestamp",
    "appointment",
    "photo",
    "app",
)


def local_source_feasibility(post: dict) -> dict:
    source = build_source_profile(post)
    title = str(post.get("title") or "")
    content = str(post.get("content") or "")
    lowered = f"{title} {content}".lower()
    has_action = any(term in lowered for term in _FEASIBILITY_ACTION_TERMS)
    has_object = any(term in lowered for term in _FEASIBILITY_OBJECT_TERMS)
    has_actor = any(term in lowered for term in _FEASIBILITY_ACTOR_TERMS)
    has_receipt = any(term in lowered for term in _FEASIBILITY_RECEIPT_TERMS)
    can_make_title = has_action and (has_object or has_actor)
    can_make_opening_visual_query = has_object
    can_make_first_frame_text = has_action and (has_object or has_actor)
    can_make_caption_hooks = has_action and has_object
    estimated_min = min(950, max(0, source.word_count * 5))
    estimated_max = min(1050, max(estimated_min, source.word_count * 8))
    failure_reasons: list[str] = []
    if not can_make_title:
        failure_reasons.append("cannot_make_title")
    if not can_make_opening_visual_query:
        failure_reasons.append("cannot_make_opening_visual_query")
    if not can_make_first_frame_text:
        failure_reasons.append("cannot_make_first_frame_text")
    if not can_make_caption_hooks:
        failure_reasons.append("cannot_make_caption_hooks")
    if not (has_receipt or (has_action and has_object)):
        failure_reasons.append("missing_receipt_or_concrete_detail")
    if estimated_max < 650:
        failure_reasons.append("estimated_narration_too_short")
    likely_fit = not failure_reasons
    return {
        "can_make_title": can_make_title,
        "can_make_opening_visual_query": can_make_opening_visual_query,
        "can_make_first_frame_text": can_make_first_frame_text,
        "can_make_caption_hooks": can_make_caption_hooks,
        "has_receipt_or_concrete_detail": has_receipt or (has_action and has_object),
        "estimated_narration_chars_min": estimated_min,
        "estimated_narration_chars_max": estimated_max,
        "likely_script_gate_fit": likely_fit,
        "failure_reasons": failure_reasons,
    }


def _source_llm_eval_limit() -> int:
    explicit = os.getenv("SOURCE_LLM_EVAL_LIMIT")
    if explicit is not None and explicit.strip():
        return max(0, _int_env("SOURCE_LLM_EVAL_LIMIT", 4))
    default = _int_env("SOURCE_LLM_EVAL_LIMIT_DEFAULT", 4)
    max_limit = _int_env("SOURCE_LLM_EVAL_LIMIT_MAX", 6)
    return max(0, min(default, max_limit))


def _target_accepted_scripts() -> int:
    return max(1, _int_env("TARGET_ACCEPTED_SCRIPTS", 2))


def _preflight_only() -> bool:
    return _truthy_env("GENERATION_PREFLIGHT_ONLY", "0")


def _filter_source_max_chars(content: str) -> int:
    default_limit = _int_env("FILTER_SOURCE_MAX_CHARS", 2500)
    long_limit = _int_env("FILTER_SOURCE_LONG_POST_MAX_CHARS", 1800)
    if len(str(content or "")) > default_limit:
        return max(700, min(default_limit, long_limit))
    return default_limit


def compact_source_for_filter(post: dict, max_chars: int) -> str:
    content = str((post or {}).get("content") or "")
    max_chars = max(500, int(max_chars or 2500))
    if len(content) <= max_chars:
        return content

    normalized = re.sub(r"\s+", " ", content).strip()
    leading = normalized[:1000].rsplit(" ", 1)[0].strip()
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]
    evidence_terms = (
        "bill",
        "receipt",
        "camera",
        "text",
        "message",
        "messages",
        "screenshot",
        "appointment",
        "bloodwork",
        "vet",
        "card",
        "driveway",
        "package",
        "landlord",
        "manager",
        "bank",
        "timestamp",
        "group chat",
        "door camera",
    )
    evidence_sentences: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if _contains_evidence_term(lowered, evidence_terms) and sentence not in evidence_sentences:
            evidence_sentences.append(sentence)
        if len(" ".join(evidence_sentences)) > 700:
            break

    final_question = ""
    for sentence in reversed(sentences):
        if sentence.endswith("?"):
            final_question = sentence
            break
    final_context = final_question or (sentences[-1] if sentences else "")
    if len(final_context) > 420:
        final_context = final_context[:420].rsplit(" ", 1)[0].strip()

    sections = [leading]
    if evidence_sentences:
        sections.append(" ".join(evidence_sentences))
    if final_context and final_context not in sections[-1]:
        sections.append(final_context)
    compacted = "\n...\n".join(section for section in sections if section).strip()
    if len(compacted) <= max_chars:
        return compacted

    evidence_text = " ".join(evidence_sentences).strip()
    if len(evidence_text) > 500:
        head = evidence_text[:240].rsplit(" ", 1)[0].strip()
        tail = evidence_text[-240:].split(" ", 1)[-1].strip()
        evidence_text = f"{head} ... {tail}".strip()
    tail = final_context[-320:] if final_context else ""
    remaining = max_chars - len(evidence_text) - len(tail) - 20
    if remaining <= 0:
        return "\n...\n".join(part for part in (evidence_text, tail) if part)[:max_chars].strip()
    head = leading[:remaining].rsplit(" ", 1)[0].strip()
    return "\n...\n".join(part for part in (head, evidence_text, tail) if part)[:max_chars].strip()


def _contains_evidence_term(lowered_sentence: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        if " " in term:
            if term in lowered_sentence:
                return True
            continue
        if re.search(rf"\b{re.escape(term)}\b", lowered_sentence):
            return True
    return False


def _source_scorecard_prompt(post: dict, content_override: str | None = None) -> str:
    title = post.get("title", "")
    content = str(content_override if content_override is not None else post.get("content", ""))
    return f"""
        Evaluate whether this source can become a high-retention 42-65 second YouTube Shorts story.

        Return JSON with this exact schema:
        {{
          "decision": "YES" or "NO",
          "relatability": integer 1-5,
          "conflict_clarity": integer 1-5,
          "stakes": integer 1-5,
          "debate_potential": integer 1-5,
          "safe_adaptability": integer 1-5,
          "visualizability": integer 1-5,
          "gate_fit_score": integer 1-5,
          "hook_in_one_sentence": integer 1-5,
          "receipt_strength": integer 1-5,
          "visual_matchability": integer 1-5,
          "length_fit_score": integer 1-5,
          "metadata_repairability": integer 1-5,
          "retention_risk": "low" or "medium" or "high",
          "archetype": short snake_case label such as roommate_money, family_boundary, pet_medical_bill, workplace_accusation, neighbor_property, wedding_drama,
          "reason": one concise sentence
        }}

        Use YES only if all conditions are true:
        - The story is complete enough to adapt without inventing major facts.
        - There is a clear first-person conflict with a concrete crossed line, unfair accusation,
          betrayal, property/money pressure, family/workplace pressure, or public embarrassment.
        - The story has enough concrete detail for 4-7 fast narration beats.
        - The content can be made broadly safe for YouTube Shorts.
        - The likely narration will not need filler to reach 42 seconds.
        - The ending naturally creates a comment debate where reasonable viewers could disagree.
        - The story feels like a 4/5 or 5/5 retention candidate, not merely "understandable."
        - gate_fit_score, hook_in_one_sentence, visual_matchability, and length_fit_score are all 4 or 5.
        - receipt_strength is at least 3.
        - The story can produce 650-950 narration characters without filler.

        Use NO if the source is mostly contextless, a question without a story, rage bait without events,
        low-stakes relationship ambiguity, graphic/sexual content involving minors, teen/high-school romance,
        explicit hate or slurs, a post that requires major fabrication, or a source that is understandable
        but likely to fail title/caption/opening-visual/length gates.

        Title: {title}
        Source metadata:
        - chars: {post.get('content_char_count', len(content))}
        - words: {post.get('content_word_count', len(content.split()))}
        - provider: {post.get('source_provider', 'unknown')}

        Content:
        {content}
        """


def _source_filter_summary_path():
    return VIABLE_POSTS_FILE.with_name("source_filter_summary.json")


def _is_llm_quota_error(message: str) -> bool:
    lowered = (message or "").lower()
    return (
        "llm_quota_unavailable" in lowered
        or "llm_circuit_open" in lowered
        or "insufficient_quota" in lowered
        or "exceeded your current quota" in lowered
        or "rate_limit_exceeded" in lowered
        or is_llm_quota_or_auth_error(message)
        or is_llm_rate_limit_error(message)
    )


def _local_fallback_enabled() -> bool:
    return os.getenv("FILTER_LOCAL_FALLBACK_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _local_source_scorecard(post: dict, reason: str = "") -> SourceScorecard:
    title = str(post.get("title") or "")
    content = str(post.get("content") or "")
    lowered = f"{title} {content}".lower()
    archetype = _local_archetype(lowered)
    has_boundary = any(
        term in lowered
        for term in (
            "boundary",
            "without asking",
            "without permission",
            "my driveway",
            "my room",
            "my card",
            "bill",
            "invoice",
            "accused",
            "blamed",
            "proof",
            "group chat",
        )
    )
    base = 4 if has_boundary else 3
    high = base
    decision = "YES" if base >= 4 else "NO"
    return SourceScorecard(
        decision=decision,
        relatability=high,
        conflict_clarity=high,
        stakes=base,
        debate_potential=high,
        safe_adaptability=high,
        visualizability=4 if archetype != "abstract_boundary" else 3,
        gate_fit_score=4 if has_boundary else 3,
        hook_in_one_sentence=4 if has_boundary else 3,
        receipt_strength=4 if any(term in lowered for term in ("receipt", "message", "text", "camera", "bill", "photo")) else 3,
        visual_matchability=4 if archetype != "abstract_boundary" else 3,
        length_fit_score=4 if len(content.split()) >= 120 else 3,
        metadata_repairability=4 if has_boundary else 3,
        retention_risk="medium" if decision == "YES" else "high",
        archetype=archetype,
        reason=(
            "Local fallback scorecard used because the LLM evaluator was unavailable; "
            f"source has {'clear' if has_boundary else 'weak'} boundary/conflict signals. {reason[:120]}"
        ).strip(),
    )


def _local_archetype(lowered: str) -> str:
    if any(term in lowered for term in ("bill", "card", "invoice", "deposit", "pay")):
        return "money_pressure"
    if any(term in lowered for term in ("driveway", "parking", "gate", "car")):
        return "neighbor_property"
    if any(term in lowered for term in ("bedroom", "apartment", "room", "couch")):
        return "family_boundary"
    if any(term in lowered for term in ("coworker", "office", "manager", "team")):
        return "workplace_accusation"
    if any(term in lowered for term in ("roommate", "housemate", "shared")):
        return "roommate_boundary"
    return "abstract_boundary"


def _source_rejection_reason_counts(posts: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for post in posts:
        reason = str(post.get("source_rejection_reason") or "").strip()
        if not reason:
            continue
        key = reason[:100]
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True)[:10])


def _accepted_source_archetypes(posts: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for post in posts:
        archetype = str(post.get("source_archetype") or "unknown").strip() or "unknown"
        counts[archetype] = counts.get(archetype, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def _accepted_examples(posts: list[dict]) -> list[dict]:
    examples: list[dict] = []
    sorted_posts = sorted(
        posts,
        key=lambda post: float(post.get("source_priority_score") or post.get("source_acceptance_score") or 0),
        reverse=True,
    )
    for post in sorted_posts[:5]:
        scorecard = post.get("source_scorecard") if isinstance(post.get("source_scorecard"), dict) else {}
        examples.append(
            {
                "id": post.get("id", ""),
                "title": str(post.get("title") or "")[:120],
                "local_source_priority": float(post.get("local_source_priority") or 0),
                "source_acceptance_score": float(post.get("source_acceptance_score") or 0),
                "source_priority_score": float(post.get("source_priority_score") or 0),
                "archetype": str(post.get("source_archetype") or scorecard.get("archetype") or "unknown")[:80],
                "reason": str(scorecard.get("reason") or post.get("source_rejection_reason") or "")[:180],
            }
        )
    return examples


# -----------------------------
# Main
# -----------------------------
def filter_viable_posts():
    try:
        client = _get_client()
        client_unavailable_reason = ""
    except Exception as e:
        client = None
        client_unavailable_reason = str(e)
        print(f"⚠️ source evaluator unavailable: {client_unavailable_reason}")
    model = os.getenv("FILTER_MODEL") or os.getenv("OPENAI_MODEL", "gpt-5.4-nano")

    if not RAW_POSTS_FILE.exists():
        print("❌ raw_posts.json이 없습니다.")
        return

    with open(RAW_POSTS_FILE, "r", encoding="utf-8") as f:
        raw_posts = json.load(f)

    selected_posts: List[dict] = []
    llm_unavailable_reason = ""
    source_scorecard_calls = 0
    source_scorecard_skipped_by_prerank = 0
    source_precheck_rejected = 0
    filter_prompt_compacted_count = 0
    filter_prompt_total_chars_before = 0
    filter_prompt_total_chars_after = 0
    source_scorecard_skipped_after_quota = 0
    local_feasibility_rejected = 0
    local_feasibility_rejection_reasons: dict[str, int] = {}
    local_feasibility_rejected_examples: list[dict] = []
    source_filter_stopped_after_target = False
    candidate_posts: List[dict] = []

    for post in raw_posts:
        local_reject_reason = _local_precheck(post)
        if local_reject_reason:
            post["source_quality_status"] = "rejected"
            post["source_rejection_reason"] = local_reject_reason
            source_precheck_rejected += 1
            print(f"⏭️ 로컬 precheck 실패로 스킵: {post.get('id', 'unknown')} - {local_reject_reason}")
            continue
        feasibility = local_source_feasibility(post)
        post["local_feasibility"] = feasibility
        if not feasibility.get("likely_script_gate_fit"):
            post["source_quality_status"] = "skipped"
            post["source_rejection_reason"] = "local_feasibility_failed:" + ",".join(feasibility.get("failure_reasons") or [])
            local_feasibility_rejected += 1
            for reason in feasibility.get("failure_reasons") or []:
                local_feasibility_rejection_reasons[reason] = local_feasibility_rejection_reasons.get(reason, 0) + 1
            if len(local_feasibility_rejected_examples) < 5:
                local_feasibility_rejected_examples.append(
                    {
                        "id": post.get("id", ""),
                        "title": str(post.get("title") or "")[:120],
                        "reasons": list(feasibility.get("failure_reasons") or [])[:5],
                    }
                )
            print(
                f"⏭️ local feasibility 실패로 LLM scorecard 스킵: "
                f"{post.get('id', 'unknown')} - {post['source_rejection_reason']}"
            )
            continue
        post["local_source_priority"] = local_source_priority(post)
        candidate_posts.append(post)

    if _preflight_only():
        candidate_posts.sort(key=lambda item: float(item.get("local_source_priority") or 0), reverse=True)
        likely_viable = [post for post in candidate_posts if (post.get("local_feasibility") or {}).get("likely_script_gate_fit")]
        expected_source_calls = min(len(likely_viable), _source_llm_eval_limit())
        expected_script_calls = min(len(likely_viable), _target_accepted_scripts())
        summary = {
            "preflight_only": True,
            "raw_posts": len(raw_posts),
            "local_precheck_rejected": source_precheck_rejected,
            "local_feasibility_rejected": local_feasibility_rejected,
            "local_feasibility_rejection_reasons": dict(sorted(local_feasibility_rejection_reasons.items(), key=lambda item: item[1], reverse=True)[:10]),
            "local_feasibility_rejected_examples": local_feasibility_rejected_examples,
            "likely_viable_sources": len(likely_viable),
            "target_accepted_scripts": _target_accepted_scripts(),
            "expected_source_scorecard_calls": expected_source_calls,
            "expected_script_draft_calls": expected_script_calls,
            "estimated_zero_acceptance_risk": "high" if len(likely_viable) < _target_accepted_scripts() else "medium",
            "candidate_examples": [
                {
                    "id": post.get("id", ""),
                    "title": str(post.get("title") or "")[:120],
                    "local_source_priority": float(post.get("local_source_priority") or 0),
                    "local_feasibility": post.get("local_feasibility") or {},
                }
                for post in likely_viable[:5]
            ],
            **llm_circuit_summary(),
        }
        with open(VIABLE_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        with open(_source_filter_summary_path(), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        with open(VIABLE_POSTS_FILE.with_name("source_preflight_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(
            "PREFLIGHT SUMMARY "
            f"raw={len(raw_posts)} likely_viable={len(likely_viable)} "
            f"expected_scorecard_calls={expected_source_calls} expected_script_draft_calls={expected_script_calls}"
        )
        return

    if _truthy_env("SOURCE_LOCAL_PRERANK_ENABLED", "1"):
        candidate_posts.sort(key=lambda item: float(item.get("local_source_priority") or 0), reverse=True)
        eval_limit = _source_llm_eval_limit()
        posts_to_evaluate = candidate_posts[:eval_limit] if eval_limit else []
        skipped_by_prerank = candidate_posts[eval_limit:] if eval_limit else candidate_posts
        for post in skipped_by_prerank:
            post["source_quality_status"] = "skipped"
            post["source_rejection_reason"] = "below_local_prerank_cutoff"
        source_scorecard_skipped_by_prerank = len(skipped_by_prerank)
    else:
        posts_to_evaluate = candidate_posts
        skipped_by_prerank = []

    for post_index, post in enumerate(posts_to_evaluate):
        title = post.get("title", "")
        original_content = str(post.get("content") or "")
        compacted_content = compact_source_for_filter(post, _filter_source_max_chars(original_content))
        filter_prompt_total_chars_before += len(original_content)
        filter_prompt_total_chars_after += len(compacted_content)
        if compacted_content != original_content:
            filter_prompt_compacted_count += 1
            post["filter_prompt_compacted"] = True
            post["filter_prompt_char_count"] = len(compacted_content)
        else:
            post["filter_prompt_compacted"] = False
            post["filter_prompt_char_count"] = len(compacted_content)
        prompt = _source_scorecard_prompt(post, compacted_content)

        try:
            if client is None:
                if not _local_fallback_enabled():
                    print(
                        "⏭️ source evaluator unavailable and local fallback disabled; "
                        f"skipping id={post.get('id', 'unknown')}"
                    )
                    post["source_quality_status"] = "skipped"
                    post["source_rejection_reason"] = client_unavailable_reason or "source_evaluator_unavailable"
                    continue
                scorecard = _local_source_scorecard(post, client_unavailable_reason)
                print(f"🧩 로컬 source scorecard 사용: id={post.get('id', 'unknown')} reason=client_unavailable")
            if llm_unavailable_reason and _local_fallback_enabled():
                scorecard = _local_source_scorecard(post, llm_unavailable_reason)
                print(f"🧩 로컬 source scorecard 사용: id={post.get('id', 'unknown')} reason=llm_unavailable")
            elif client is not None:
                source_scorecard_calls += 1
                scorecard = _ask_source_scorecard(client, prompt, model)
            if not scorecard:
                print("⚠️ GPT scorecard 응답 없음/불명. 해당 포스트 스킵")
                post["source_quality_status"] = "skipped"
                post["source_rejection_reason"] = "source_scorecard_empty"
                continue

            source_score = _scorecard_average(scorecard)
            acceptance_score = source_acceptance_score(scorecard)
            min_score = float(os.getenv("SOURCE_ACCEPTANCE_MIN_SCORE", os.getenv("SOURCE_SCORE_MIN_AVG", "4.0")))
            if (
                scorecard.decision == "YES"
                and scorecard.retention_risk != "high"
                and acceptance_score >= min_score
                and _gate_fit_passes(scorecard)
            ):
                post["source_scorecard"] = scorecard.model_dump()
                post["source_score"] = source_score
                post["source_acceptance_score"] = acceptance_score
                post["source_priority_score"] = _source_priority_score(scorecard)
                post["source_archetype"] = scorecard.archetype.strip().lower()[:80]
                post["source_quality_status"] = "accepted"
                post["source_rejection_reason"] = ""
                selected_posts.append(post)
                if len(selected_posts) >= _target_accepted_scripts():
                    source_filter_stopped_after_target = True
                    remaining = posts_to_evaluate[post_index + 1 :]
                    for remaining_post in remaining:
                        remaining_post["source_quality_status"] = "skipped"
                        remaining_post["source_rejection_reason"] = "source_filter_stopped_after_target"
                    print(
                        "✅ target accepted sources reached; "
                        f"stopping source scorecard after {len(selected_posts)} accepted"
                    )
                    break
            else:
                post["source_quality_status"] = "rejected"
                post["source_rejection_reason"] = scorecard.reason
                print(
                    "⏭️ source scorecard reject: "
                    f"id={post.get('id', 'unknown')} decision={scorecard.decision} "
                    f"score={source_score} acceptance={acceptance_score} gate_fit={scorecard.gate_fit_score} hook={scorecard.hook_in_one_sentence} "
                    f"visual={scorecard.visual_matchability} length={scorecard.length_fit_score} "
                    f"risk={scorecard.retention_risk} reason={scorecard.reason}"
                )

        except (RuntimeError, LlmCircuitOpen) as e:
            if _is_llm_quota_error(str(e)) and _local_fallback_enabled():
                llm_unavailable_reason = str(e)
                scorecard = _local_source_scorecard(post, llm_unavailable_reason)
                print(f"🧩 OpenAI quota 오류로 로컬 source scorecard 사용: id={post.get('id', 'unknown')}")
                source_score = _scorecard_average(scorecard)
                acceptance_score = source_acceptance_score(scorecard)
                min_score = float(os.getenv("SOURCE_ACCEPTANCE_MIN_SCORE", os.getenv("SOURCE_SCORE_MIN_AVG", "4.0")))
                if (
                    scorecard.decision == "YES"
                    and scorecard.retention_risk != "high"
                    and acceptance_score >= min_score
                    and _gate_fit_passes(scorecard)
                ):
                    post["source_scorecard"] = scorecard.model_dump()
                    post["source_score"] = source_score
                    post["source_acceptance_score"] = acceptance_score
                    post["source_priority_score"] = _source_priority_score(scorecard)
                    post["source_archetype"] = scorecard.archetype.strip().lower()[:80]
                    post["source_quality_status"] = "accepted"
                    post["source_rejection_reason"] = ""
                    selected_posts.append(post)
                else:
                    post["source_quality_status"] = "rejected"
                    post["source_rejection_reason"] = scorecard.reason
                    print(
                        "⏭️ local source scorecard reject: "
                        f"id={post.get('id', 'unknown')} decision={scorecard.decision} "
                        f"score={source_score} acceptance={acceptance_score} risk={scorecard.retention_risk} reason={scorecard.reason}"
                    )
            else:
                if _is_llm_quota_error(str(e)) or isinstance(e, LlmCircuitOpen):
                    llm_unavailable_reason = str(e)
                    open_llm_circuit(str(e), "source_filter")
                    post["source_quality_status"] = "skipped"
                    post["source_rejection_reason"] = llm_unavailable_reason
                    remaining = posts_to_evaluate[post_index + 1 :]
                    for remaining_post in remaining:
                        remaining_post["source_quality_status"] = "skipped"
                        remaining_post["source_rejection_reason"] = llm_unavailable_reason
                    source_scorecard_skipped_after_quota = len(remaining)
                    print(
                        "🚫 source scorecard quota unavailable; "
                        f"skipping remaining {source_scorecard_skipped_after_quota} candidate(s) without more LLM calls"
                    )
                    break
                post["source_quality_status"] = "skipped"
                post["source_rejection_reason"] = str(e)
                print(f"GPT 판단 오류: {e}")
            continue
        except Exception as e:
            post["source_quality_status"] = "skipped"
            post["source_rejection_reason"] = str(e)
            print(f"GPT 판단 오류: {e}")
            continue

    selected_posts.sort(key=lambda item: float(item.get("source_priority_score") or item.get("source_acceptance_score") or item.get("source_score") or 0), reverse=True)
    with open(VIABLE_POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(selected_posts, f, ensure_ascii=False, indent=2)
    local_priority_scores = [float(post.get("local_source_priority") or 0) for post in candidate_posts]
    evaluated_priority_scores = [float(post.get("local_source_priority") or 0) for post in posts_to_evaluate]
    skipped_examples = [
        {
            "id": post.get("id", ""),
            "title": str(post.get("title") or "")[:120],
            "local_source_priority": float(post.get("local_source_priority") or 0),
            "reason": "below_local_prerank_cutoff",
        }
        for post in skipped_by_prerank[:5]
    ]
    summary = {
        "raw_posts": len(raw_posts),
        "local_precheck_rejected": source_precheck_rejected,
        "local_feasibility_rejected": local_feasibility_rejected,
        "local_feasibility_rejection_reasons": dict(sorted(local_feasibility_rejection_reasons.items(), key=lambda item: item[1], reverse=True)[:10]),
        "local_feasibility_rejected_examples": local_feasibility_rejected_examples,
        "local_prerank_enabled": _truthy_env("SOURCE_LOCAL_PRERANK_ENABLED", "1"),
        "source_llm_eval_limit": _source_llm_eval_limit(),
        "target_accepted_scripts": _target_accepted_scripts(),
        "source_filter_stopped_after_target": source_filter_stopped_after_target,
        "source_scorecard_calls": source_scorecard_calls,
        "source_scorecard_skipped_by_prerank": source_scorecard_skipped_by_prerank,
        "source_scorecard_skipped_after_quota": source_scorecard_skipped_after_quota,
        "llm_unavailable_reason": llm_unavailable_reason[:240],
        "filter_prompt_compacted_count": filter_prompt_compacted_count,
        "filter_prompt_total_chars_before": filter_prompt_total_chars_before,
        "filter_prompt_total_chars_after": filter_prompt_total_chars_after,
        "local_priority_cutoff_score": min(evaluated_priority_scores) if evaluated_priority_scores else 0,
        "local_priority_top_scores": sorted(local_priority_scores, reverse=True)[:10],
        "local_priority_min_evaluated": min(evaluated_priority_scores) if evaluated_priority_scores else 0,
        "prerank_skipped_examples": skipped_examples,
        "source_rejection_reason_counts": _source_rejection_reason_counts(raw_posts),
        "accepted_source_archetypes": _accepted_source_archetypes(selected_posts),
        "accepted_examples": _accepted_examples(selected_posts),
        "accepted": len(selected_posts),
        **llm_circuit_summary(),
    }
    with open(_source_filter_summary_path(), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"✅ 적합한 게시물 {len(selected_posts)}개 저장됨 → {VIABLE_POSTS_FILE}")


if __name__ == "__main__":
    filter_viable_posts()
