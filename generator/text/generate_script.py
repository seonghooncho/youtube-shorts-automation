import json
import os
import re
from typing import Any, List, Literal, Optional
from dotenv import load_dotenv, find_dotenv
import openai
from pydantic import BaseModel, Field, ValidationError
from shared.llm.circuit_breaker import (
    LlmCircuitOpen,
    assert_llm_circuit_closed,
    is_llm_quota_or_auth_error,
    is_llm_rate_limit_error,
    open_llm_circuit,
)

_LAST_GENERATION_TELEMETRY: dict[str, Any] = {}


class GenerateScriptError(RuntimeError):
    def __init__(self, message: str, telemetry: dict[str, Any], cause: Exception | None = None):
        super().__init__(message)
        self.telemetry = dict(telemetry or {})
        if cause is not None:
            self.__cause__ = cause

# --- ENV & Client ---
def _get_client() -> openai.OpenAI:
    assert_llm_circuit_closed("script_client")
    env_path = find_dotenv(usecwd=True)
    load_dotenv(env_path)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        open_llm_circuit("OPENAI_API_KEY is not configured", "script_client")
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
    return openai.OpenAI(api_key=api_key)

def _default_model() -> str:
    return os.getenv("SCRIPT_MODEL") or os.getenv("OPENAI_MODEL", "gpt-5.5")


def _token_budgets() -> list[int]:
    raw = os.getenv("SCRIPT_OUTPUT_TOKEN_BUDGETS", "").strip()
    if raw:
        budgets: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if value > 0:
                budgets.append(value)
        if budgets:
            return budgets
    return [1600, 2200, 3000]


def _json_fallback_enabled() -> bool:
    return os.getenv("SCRIPT_ENABLE_JSON_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}


def _max_structured_attempts() -> int:
    raw = os.getenv("SCRIPT_MAX_STRUCTURED_ATTEMPTS", "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _retry_on_token_limit_only() -> bool:
    return os.getenv("SCRIPT_RETRY_ON_TOKEN_LIMIT_ONLY", "1").strip().lower() not in {"0", "false", "no", "off"}


def _new_generation_telemetry() -> dict[str, Any]:
    return {
        "structured_attempts": 0,
        "json_fallback_attempts": 0,
        "structured_failures": 0,
        "json_fallback_failures": 0,
        "estimated_output_token_budget_total": 0,
        "structured_retry_reason": "",
        "structured_retry_skipped_reason": "",
    }


def get_last_generation_telemetry() -> dict[str, Any]:
    return dict(_LAST_GENERATION_TELEMETRY)


def _record_last_generation_telemetry(telemetry: dict[str, Any]) -> None:
    global _LAST_GENERATION_TELEMETRY
    _LAST_GENERATION_TELEMETRY = dict(telemetry)


def _attach_generation_telemetry(draft: BaseModel, telemetry: dict[str, Any]) -> BaseModel:
    object.__setattr__(draft, "_generation_telemetry", dict(telemetry))
    return draft


def _text_verbosity(model: str | None = None) -> str:
    raw = os.getenv("SCRIPT_TEXT_VERBOSITY", "").strip().lower()
    if raw in {"low", "medium", "high"}:
        return raw
    selected_model = (model or _default_model()).lower()
    if selected_model.startswith("gpt-4.1"):
        return "medium"
    return "low"

# --- Schema ---
class VisualBeatQuery(BaseModel):
    beat: str = ""
    query: str = ""


class ScriptCriticScores(BaseModel):
    ai_smell_score: int = Field(3, ge=1, le=10)
    native_naturalness_score: int = Field(8, ge=1, le=10)
    retention_score: int = Field(8, ge=1, le=10)
    specificity_score: int = Field(8, ge=1, le=10)
    hook_score: int = Field(8, ge=1, le=10)
    payoff_score: int = Field(8, ge=1, le=10)
    comment_potential_score: int = Field(7, ge=1, le=10)


class DraftScript(BaseModel):
    title: str = ""
    voice: Literal["male", "female", "neutral"]
    source_summary: str
    story_beats: List[str] = Field(..., min_length=4, max_length=7)
    adaptation_strategy: str
    retention_angle: str
    turning_point: str
    payoff_line: str
    viewer_question: str
    marketability_score: int = Field(..., ge=1, le=5)
    retention_risk: str
    rewrite_notes: str
    hook_type: str
    style_variant: str
    voiceover_lines: List[str] = Field(..., min_length=1)


class ReturnScript(BaseModel):
    title: str
    description: str
    tags: List[str]
    voice: Literal["male", "female", "neutral"]
    visual_keywords: List[str]
    first_frame_text: str = Field("", description="Max 38 characters of on-screen hook text for the first frame.")
    opening_visual_query: str = Field("", description="The first stock-video query, matched to the first spoken line.")
    visual_beat_queries: List[VisualBeatQuery] = Field(default_factory=list, description="Ordered beat/query pairs for hook, receipt/reveal, decision, and question visuals.")
    hook_type: str = Field("", description="The Shorts hook pattern used, such as unfair accusation, crossed boundary, cost, betrayal, or villain framing.")
    first_2_seconds: str = Field("", description="The exact opening phrase that should be compelling within the first two seconds.")
    source_summary: str = Field(..., description="One or two sentences summarizing the source conflict.")
    story_beats: List[str] = Field(..., min_length=4, max_length=7)
    adaptation_strategy: str = Field(..., description="How the source was compressed or plausibly dramatized without changing the core conflict.")
    retention_angle: str = Field(..., description="Why this story should hold Shorts viewers past the opening hook.")
    turning_point: str = Field("", description="The moment where the story gets worse or flips expectations.")
    payoff_line: str = Field("", description="The short final conflict statement immediately before the viewer question.")
    viewer_question: str = Field(..., description="A concise final engagement question for comments.")
    marketability_score: int = Field(..., ge=1, le=5)
    retention_risk: str = Field("", description="The main risk that could make viewers swipe away, and how the script reduces it.")
    cut_plan: List[str] = Field(default_factory=list, description="A concise list of visual cut intentions for hook, context, escalation, decision, and question.")
    bg_strategy: Literal["story", "asmr", "hybrid"] = "hybrid"
    rewrite_notes: str = Field("", description="Short note explaining what was tightened for retention.")
    style_variant: str = Field("", description="The concrete storytelling style variant selected for this source.")
    voiceover_lines: List[str] = Field(default_factory=list)
    tts_text: str = ""
    caption_chunks: List[str] = Field(default_factory=list)
    predicted_retention_score: int = Field(8, ge=1, le=10)
    predicted_rewatch_score: int = Field(7, ge=1, le=10)
    predicted_comment_score: int = Field(7, ge=1, le=10)
    predicted_clarity_score: int = Field(8, ge=1, le=10)
    predicted_ai_smell_score: int = Field(3, ge=1, le=10)
    skip_reason: str = ""
    critic_scores: ScriptCriticScores = Field(default_factory=ScriptCriticScores)
    critic_problems: List[str] = Field(default_factory=list)
    critic_rewrite_instructions: List[str] = Field(default_factory=list)
    script: List[str]


class NativeViewerCritic(BaseModel):
    ai_smell_score: int = Field(..., ge=1, le=10)
    native_naturalness_score: int = Field(..., ge=1, le=10)
    retention_score: int = Field(..., ge=1, le=10)
    specificity_score: int = Field(..., ge=1, le=10)
    hook_score: int = Field(..., ge=1, le=10)
    payoff_score: int = Field(..., ge=1, le=10)
    comment_potential_score: int = Field(..., ge=1, le=10)
    problems: List[str] = Field(default_factory=list)
    rewrite_instructions: List[str] = Field(default_factory=list)


def _assert_no_nulls(rs: BaseModel) -> None:
    data = rs.model_dump(exclude_none=False)
    for k, v in data.items():
        if v is None:
            raise ValueError(f"필드 '{k}'가 null 입니다.")
        if isinstance(v, list) and any(x is None for x in v):
            raise ValueError(f"리스트 필드 '{k}'에 null 항목이 있습니다.")

# --- Helpers: 폴백용 클린업 ---
def _strip_code_fence(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        # ```xxx\n ... \n``` 형태 제거
        idx = s.find("\n")
        if idx != -1:
            s = s[idx + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()

def _json_slice(s: str) -> Optional[str]:
    """
    불완전 JSON일 때 마지막 '}' 까지만 잘라서 복구 시도.
    (완벽 보장은 없지만 많은 케이스를 구제)
    """
    s = _strip_code_fence(s)
    last = s.rfind("}")
    return s[: last + 1] if last != -1 else None

# --- Core calls ---
def draft_to_metadata(draft: DraftScript) -> dict[str, Any]:
    data = draft.model_dump()
    lines = [str(line or "").strip() for line in data.get("voiceover_lines") or [] if str(line or "").strip()]
    data["working_title"] = str(data.get("title") or "").strip()
    data["_title_is_working"] = True
    data["public_title"] = ""
    data["voiceover_lines"] = lines
    data["script"] = list(lines)
    return data


def _try_structured(client: openai.OpenAI, prompt: str, max_output_tokens: int) -> DraftScript:
    assert_llm_circuit_closed("script_generation")
    try:
        resp = client.responses.parse(
            model=_default_model(),
            input=[
                {"role": "system", "content": _script_system_message()},
                {"role": "user", "content": prompt},
            ],
            text_format=DraftScript,
            max_output_tokens=max_output_tokens,
            text={"verbosity": _text_verbosity()},
        )
    except Exception as exc:
        if is_llm_quota_or_auth_error(exc) or is_llm_rate_limit_error(exc):
            open_llm_circuit(str(exc), "script_generation")
        raise
    parsed: DraftScript = resp.output_parsed
    if parsed is None:
        raise ValueError("output_parsed가 비었습니다.")
    _assert_no_nulls(parsed)
    return parsed

def _fallback_json_mode(client: openai.OpenAI, prompt: str, max_output_tokens: int) -> DraftScript:
    """
    Structured Outputs 실패 시 JSON 모드로 재시도 후 Pydantic 검증.
    """
    assert_llm_circuit_closed("script_json_fallback")
    try:
        resp = client.responses.create(
            model=_default_model(),
            input=[
                {"role": "developer",
                 "content": _script_system_message() + (
                      " Return content that will be used for TTS. No code fences, no commentary. "
                      "Do NOT include any control characters (U+0000-U+001F). "
                      "Do NOT escape them as \\u0001, \\u0002, etc. "
                      "Output must be clean UTF-8 text only, with plain ASCII quotes and parentheses."
                  )},
                {"role": "user", "content": prompt},
            ],
            text={"format": {"type": "json_object"}, "verbosity": _text_verbosity()},
            max_output_tokens=max_output_tokens,
        )
    except Exception as exc:
        if is_llm_quota_or_auth_error(exc) or is_llm_rate_limit_error(exc):
            open_llm_circuit(str(exc), "script_json_fallback")
        raise
    raw = (resp.output_text or "").strip()
    try:
        return DraftScript.model_validate_json(raw)
    except ValidationError:
        sliced = _json_slice(raw)
        if sliced:
            return DraftScript.model_validate_json(sliced)
        raise


def _critic_enabled() -> bool:
    if os.getenv("SCRIPT_CRITIC_STAGE", "after_local_gate").strip().lower() == "after_local_gate":
        return False
    return os.getenv("SCRIPT_CRITIC_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def _critic_hard_failure(critic: NativeViewerCritic) -> str:
    failures = []
    if critic.ai_smell_score > 3:
        failures.append(f"ai_smell_score>{3} ({critic.ai_smell_score})")
    if critic.native_naturalness_score < 8:
        failures.append(f"native_naturalness_score<8 ({critic.native_naturalness_score})")
    if critic.retention_score < 8:
        failures.append(f"retention_score<8 ({critic.retention_score})")
    if critic.specificity_score < 8:
        failures.append(f"specificity_score<8 ({critic.specificity_score})")
    if critic.hook_score < 8:
        failures.append(f"hook_score<8 ({critic.hook_score})")
    if critic.payoff_score < 8:
        failures.append(f"payoff_score<8 ({critic.payoff_score})")
    return "; ".join(failures)


def _critic_scores(critic: NativeViewerCritic) -> dict:
    return {
        "ai_smell_score": critic.ai_smell_score,
        "native_naturalness_score": critic.native_naturalness_score,
        "retention_score": critic.retention_score,
        "specificity_score": critic.specificity_score,
        "hook_score": critic.hook_score,
        "payoff_score": critic.payoff_score,
        "comment_potential_score": critic.comment_potential_score,
    }


def _draft_payload(draft: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(draft, BaseModel):
        return draft.model_dump()
    return dict(draft)


def _critic_prompt(source_prompt: str, draft: BaseModel | dict[str, Any]) -> str:
    payload = {
        "source": _compact_source_from_prompt(source_prompt),
        "draft": _draft_payload(draft),
        "thresholds": {
            "ai_smell_score_max": 3,
            "native_naturalness_score_min": 8,
            "retention_score_min": 8,
            "specificity_score_min": 8,
            "hook_score_min": 8,
            "payoff_score_min": 8,
        },
        "hard_rules": [
            "Sounds like a real native English speaker, not a template.",
            "First sentence is immediately clear and concrete.",
            "Avoid abstract moral framing and banned AI-template phrases.",
            "Every line creates a visible action, object, message, receipt, bill, camera, app, or place when the source supports it.",
            "The payoff or receipt appears before the final question.",
            "The final question is specific, not generic.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def critique_script(prompt: str, draft: BaseModel | dict[str, Any]) -> NativeViewerCritic:
    assert_llm_circuit_closed("script_critic")
    client = _get_client()
    model = os.getenv("SCRIPT_CRITIC_MODEL") or _default_model()
    try:
        resp = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": "You are a ruthless native English Shorts viewer critic."},
                {"role": "user", "content": _critic_prompt(prompt, draft)},
            ],
            text_format=NativeViewerCritic,
            max_output_tokens=int(os.getenv("SCRIPT_CRITIC_MAX_OUTPUT_TOKENS", "1400")),
            text={"verbosity": _text_verbosity(model)},
        )
    except Exception as exc:
        if is_llm_quota_or_auth_error(exc) or is_llm_rate_limit_error(exc):
            open_llm_circuit(str(exc), "script_critic")
        raise
    parsed: NativeViewerCritic = resp.output_parsed
    if parsed is None:
        raise ValueError("critic output_parsed가 비었습니다.")
    return parsed


def _script_system_message() -> str:
    return (
        "You are a native-English Shorts story editor. Write like a real person, not a template. "
        "Start with a concrete conflict. Do not summarize the structure of the conflict. "
        "Show concrete actions. Avoid abstract moral framing. Use specific receipts, timestamps, messages, bills, photos, cameras, apps, and group chats when the source supports them. "
        "Never use banned AI-template phrases such as acted like I was the problem, people are split, keep the peace, let it go, the situation, the issue, what changed everything, The proof was clear, or The boundary was simple. "
        "Every line must be complete. Weak or generic scripts should fail, not be softened."
    )


def _compact_source_from_prompt(prompt: str) -> dict[str, str]:
    title_match = re.search(r"Title:\s*(.+)", prompt)
    provider_match = re.search(r"Source provider:\s*(.+)", prompt)
    url_match = re.search(r"Source URL:\s*(.+)", prompt)
    content_match = re.search(r"\[Original source\][\s\S]*?Content:\s*([\s\S]+)$", prompt)
    content = (content_match.group(1) if content_match else "").strip()
    return {
        "source_title": (title_match.group(1) if title_match else "").strip()[:240],
        "source_provider": (provider_match.group(1) if provider_match else "").strip()[:80],
        "source_url": (url_match.group(1) if url_match else "").strip()[:500],
        "source_content_excerpt": content[:3000],
    }


def apply_critic_to_metadata(metadata: dict[str, Any], critic: NativeViewerCritic) -> dict[str, Any]:
    metadata["critic_scores"] = _critic_scores(critic)
    metadata["critic_problems"] = list(critic.problems or [])
    metadata["critic_rewrite_instructions"] = list(critic.rewrite_instructions or [])
    metadata["critic_attempt_count"] = int(metadata.get("critic_attempt_count") or 0) + 1
    metadata["critic_stage"] = "after_local_gate"
    metadata["critic_passed"] = True
    metadata["predicted_retention_score"] = critic.retention_score
    metadata["predicted_rewatch_score"] = max(1, min(10, round((critic.retention_score + critic.hook_score) / 2)))
    metadata["predicted_comment_score"] = critic.comment_potential_score
    metadata["predicted_clarity_score"] = critic.native_naturalness_score
    metadata["predicted_ai_smell_score"] = critic.ai_smell_score
    return metadata


def _apply_critic_metadata(script: BaseModel, critic: NativeViewerCritic) -> BaseModel:
    data = script.model_dump()
    if isinstance(script, ReturnScript):
        data["critic_scores"] = _critic_scores(critic)
        data["critic_problems"] = list(critic.problems or [])
        data["critic_rewrite_instructions"] = list(critic.rewrite_instructions or [])
        data["predicted_retention_score"] = critic.retention_score
        data["predicted_rewatch_score"] = max(1, min(10, round((critic.retention_score + critic.hook_score) / 2)))
        data["predicted_comment_score"] = critic.comment_potential_score
        data["predicted_clarity_score"] = critic.native_naturalness_score
        data["predicted_ai_smell_score"] = critic.ai_smell_score
        return ReturnScript.model_validate(data)
    return DraftScript.model_validate(data)


def _rewrite_prompt(prompt: str, critic: NativeViewerCritic) -> str:
    instructions = "\n".join(f"- {item}" for item in critic.rewrite_instructions or critic.problems or [])
    return (
        f"{prompt}\n\n[NATIVE VIEWER CRITIC REWRITE]\n"
        "The previous draft failed the native-viewer critic. Regenerate the DraftScript JSON using the same source conflict.\n"
        "Make it more concrete, native-sounding, fast, specific, and payoff-driven. Do not invent unsupported major facts.\n"
        f"Critic failures: {_critic_hard_failure(critic)}\n"
        f"Rewrite instructions:\n{instructions}\n"
    )


def _token_limit_failure_reason(exc: Exception) -> str:
    message = str(exc or "").lower()
    token_signals = (
        "max output",
        "max_output",
        "max tokens",
        "max_tokens",
        "token limit",
        "output token",
        "truncated",
        "cutoff",
        "cut off",
        "incomplete json",
        "unterminated string",
        "unexpected eof",
        "end of data",
        "finish_reason",
        "length",
        "output too short",
    )
    if any(signal in message for signal in token_signals):
        return "token_limit_or_truncated_output"
    return ""


def _non_retryable_failure_reason(exc: Exception) -> str:
    message = str(exc or "").lower()
    if _token_limit_failure_reason(exc):
        return ""
    if any(term in message for term in ("insufficient_quota", "quota", "auth", "api key", "permission")):
        return "auth_or_quota_failure"
    if any(term in message for term in ("rate limit", "rate_limit", "429")):
        return "rate_limit_failure"
    if any(term in message for term in ("unsafe", "safety", "policy")):
        return "unsafe_content_failure"
    if any(term in message for term in ("validation", "required", "missing", "schema")):
        return "schema_validation_failure"
    if "none" in message or "null" in message:
        return "null_output_without_token_signal"
    return "non_token_failure"


def _raise_generate_script_error(message: str, telemetry: dict[str, Any], cause: Exception | None = None) -> None:
    _record_last_generation_telemetry(telemetry)
    raise GenerateScriptError(message, telemetry, cause)


# --- Public API ---
def generate_script(prompt: str) -> DraftScript:
    """
    1) Structured Outputs (responses.parse) 우선
    2) 실패 시 JSON 모드 폴백 (responses.create + model_validate_json)
    3) 시도별로 토큰 예산 상향 (잘림 방지)
    """
    budgets = _token_budgets()[: _max_structured_attempts()]
    last_err: Optional[Exception] = None
    telemetry = _new_generation_telemetry()
    _record_last_generation_telemetry(telemetry)

    for i, max_tokens in enumerate(budgets, start=1):
        try:
            assert_llm_circuit_closed("script_generation")
            client = _get_client()
            # 1차: Structured
            telemetry["structured_attempts"] += 1
            telemetry["estimated_output_token_budget_total"] += max_tokens
            _record_last_generation_telemetry(telemetry)
            draft = _try_structured(client, prompt, max_tokens)
            draft = _run_critic_rewrite_flow(prompt, draft, max_tokens)
            return _attach_generation_telemetry(draft, telemetry)
        except Exception as e1:
            if isinstance(e1, LlmCircuitOpen):
                _raise_generate_script_error(str(e1), telemetry, e1)
            telemetry["structured_failures"] += 1
            _record_last_generation_telemetry(telemetry)
            if is_llm_quota_or_auth_error(e1) or is_llm_rate_limit_error(e1):
                open_llm_circuit(str(e1), "script_generation")
                _raise_generate_script_error(f"GPT 호출/검증 최종 실패: {e1}", telemetry, e1)
            last_err = e1
            print(f"⚠️ Structured 실패 (시도 {i}/{len(budgets)} | tokens={max_tokens}): {e1}")
            token_reason = _token_limit_failure_reason(e1)
            has_next_budget = i < len(budgets)
            if has_next_budget and (token_reason or not _retry_on_token_limit_only()):
                telemetry["structured_retry_reason"] = token_reason or "retry_on_any_failure_enabled"
                _record_last_generation_telemetry(telemetry)
                continue

            telemetry["structured_retry_skipped_reason"] = (
                "max_structured_attempts_reached"
                if not has_next_budget
                else _non_retryable_failure_reason(e1)
            )
            if not _json_fallback_enabled():
                _raise_generate_script_error(f"GPT 호출/검증 최종 실패: {last_err}", telemetry, e1)
            try:
                telemetry["json_fallback_attempts"] += 1
                telemetry["estimated_output_token_budget_total"] += max_tokens
                _record_last_generation_telemetry(telemetry)
                rs = _fallback_json_mode(client, prompt, max_tokens)
                _assert_no_nulls(rs)
                rs = _run_critic_rewrite_flow(prompt, rs, max_tokens)
                return _attach_generation_telemetry(rs, telemetry)
            except Exception as e2:
                last_err = e2
                telemetry["json_fallback_failures"] += 1
                _record_last_generation_telemetry(telemetry)
                print(f"⚠️ JSON 폴백 실패 (시도 {i}/{len(budgets)} | tokens={max_tokens}): {e2}")
                _raise_generate_script_error(f"GPT 호출/검증 최종 실패: {last_err}", telemetry, e2)

    _raise_generate_script_error(f"GPT 호출/검증 최종 실패: {last_err}", telemetry, last_err)


def _run_critic_rewrite_flow(prompt: str, draft: BaseModel, max_output_tokens: int) -> BaseModel:
    if not _critic_enabled():
        return draft
    critic = critique_script(prompt, draft)
    failure = _critic_hard_failure(critic)
    if not failure:
        return _apply_critic_metadata(draft, critic)

    print(f"⚠️ native-viewer critic failed draft: {failure}")
    client = _get_client()
    rewrite = _try_structured(client, _rewrite_prompt(prompt, critic), max_output_tokens)
    rewrite_critic = critique_script(prompt, rewrite)
    rewrite_failure = _critic_hard_failure(rewrite_critic)
    if rewrite_failure:
        raise ValueError(
            "native_viewer_critic_failed: "
            f"{rewrite_failure}; problems={rewrite_critic.problems}; rewrite_instructions={rewrite_critic.rewrite_instructions}"
        )
    return _apply_critic_metadata(rewrite, rewrite_critic)
