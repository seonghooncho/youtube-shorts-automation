import os
from typing import Any, Dict, List, Literal, Optional
from dotenv import load_dotenv, find_dotenv
import openai
from pydantic import BaseModel, Field, ValidationError

# --- ENV & Client ---
def _get_client() -> openai.OpenAI:
    env_path = find_dotenv(usecwd=True)
    load_dotenv(env_path)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
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
    return [3200, 4200, 5200]

# --- Schema ---
class ReturnScript(BaseModel):
    title: str
    description: str
    tags: List[str]
    voice: Literal["male", "female", "neutral"]
    visual_keywords: List[str]
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
    critic_scores: Dict[str, Any] = Field(default_factory=dict)
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


def _assert_no_nulls(rs: ReturnScript) -> None:
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
def _try_structured(client: openai.OpenAI, prompt: str, max_output_tokens: int) -> ReturnScript:
    resp = client.responses.parse(
        model=_default_model(),
        input=[
            {"role": "system", "content": "You are a YouTube Shorts script writer."},
            {"role": "user", "content": prompt},
        ],
        text_format=ReturnScript,
        max_output_tokens=max_output_tokens,
        text={"verbosity": "low"},
    )
    parsed: ReturnScript = resp.output_parsed
    if parsed is None:
        raise ValueError("output_parsed가 비었습니다.")
    _assert_no_nulls(parsed)
    return parsed

def _fallback_json_mode(client: openai.OpenAI, prompt: str, max_output_tokens: int) -> ReturnScript:
    """
    Structured Outputs 실패 시 JSON 모드로 재시도 후 Pydantic 검증.
    """
    resp = client.responses.create(
        model=_default_model(),
        input=[
            {"role": "developer",
             "content": (
                  "You are a YouTube Shorts script writer."
                  "Return content that will be used for TTS. "
                  "No code fences, no commentary. "
                  "Do NOT include any control characters (U+0000–U+001F). "
                  "Do NOT escape them as \\u0001, \\u0002, etc. "
                  "Output must be clean UTF-8 text only, "
                  "with plain ASCII quotes and parentheses. "
              )},
            {"role": "user", "content": prompt},
        ],
        text={"format": {"type": "json_object"}, "verbosity": "low"},
        max_output_tokens=max_output_tokens,
    )
    raw = (resp.output_text or "").strip()
    try:
        return ReturnScript.model_validate_json(raw)
    except ValidationError:
        sliced = _json_slice(raw)
        if sliced:
            return ReturnScript.model_validate_json(sliced)
        raise


def _critic_enabled() -> bool:
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


def _critic_prompt(source_prompt: str, draft: ReturnScript) -> str:
    return (
        "Evaluate this draft as a native English-speaking YouTube Shorts viewer.\n"
        "Return only JSON matching the requested schema.\n\n"
        "Hard-check whether it sounds like a real person, has a clear first sentence, avoids generic AI Reddit phrasing, "
        "uses concrete source-grounded details, creates a visual picture each line, and has a payoff before the final question.\n\n"
        "[Source and generation brief]\n"
        f"{source_prompt[-6000:]}\n\n"
        "[Draft JSON]\n"
        f"{draft.model_dump_json()}"
    )


def critique_script(prompt: str, draft: ReturnScript) -> NativeViewerCritic:
    client = _get_client()
    resp = client.responses.parse(
        model=os.getenv("SCRIPT_CRITIC_MODEL") or _default_model(),
        input=[
            {"role": "system", "content": "You are a ruthless native English Shorts viewer critic."},
            {"role": "user", "content": _critic_prompt(prompt, draft)},
        ],
        text_format=NativeViewerCritic,
        max_output_tokens=int(os.getenv("SCRIPT_CRITIC_MAX_OUTPUT_TOKENS", "1400")),
        text={"verbosity": "low"},
    )
    parsed: NativeViewerCritic = resp.output_parsed
    if parsed is None:
        raise ValueError("critic output_parsed가 비었습니다.")
    return parsed


def _apply_critic_metadata(script: ReturnScript, critic: NativeViewerCritic) -> ReturnScript:
    data = script.model_dump()
    data["critic_scores"] = _critic_scores(critic)
    data["critic_problems"] = list(critic.problems or [])
    data["critic_rewrite_instructions"] = list(critic.rewrite_instructions or [])
    return ReturnScript.model_validate(data)


def _rewrite_prompt(prompt: str, critic: NativeViewerCritic) -> str:
    instructions = "\n".join(f"- {item}" for item in critic.rewrite_instructions or critic.problems or [])
    return (
        f"{prompt}\n\n[NATIVE VIEWER CRITIC REWRITE]\n"
        "The previous draft failed the native-viewer critic. Regenerate the full JSON using the same source conflict.\n"
        "Make it more concrete, native-sounding, fast, specific, and payoff-driven. Do not invent unsupported major facts.\n"
        f"Critic failures: {_critic_hard_failure(critic)}\n"
        f"Rewrite instructions:\n{instructions}\n"
    )

# --- Public API ---
def generate_script(prompt: str) -> ReturnScript:
    """
    1) Structured Outputs (responses.parse) 우선
    2) 실패 시 JSON 모드 폴백 (responses.create + model_validate_json)
    3) 시도별로 토큰 예산 상향 (잘림 방지)
    """
    budgets = _token_budgets()
    last_err: Optional[Exception] = None
    client = _get_client()

    for i, max_tokens in enumerate(budgets, start=1):
        try:
            # 1차: Structured
            draft = _try_structured(client, prompt, max_tokens)
            return _run_critic_rewrite_flow(prompt, draft, max_tokens)
        except Exception as e1:
            last_err = e1
            print(f"⚠️ Structured 실패 (시도 {i}/{len(budgets)} | tokens={max_tokens}): {e1}")
            try:
                # 2차: 폴백(JSON 모드)
                rs = _fallback_json_mode(client, prompt, max_tokens)
                _assert_no_nulls(rs)
                return _run_critic_rewrite_flow(prompt, rs, max_tokens)
            except Exception as e2:
                last_err = e2
                print(f"⚠️ JSON 폴백 실패 (시도 {i}/{len(budgets)} | tokens={max_tokens}): {e2}")

    raise RuntimeError(f"GPT 호출/검증 최종 실패: {last_err}")


def _run_critic_rewrite_flow(prompt: str, draft: ReturnScript, max_output_tokens: int) -> ReturnScript:
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
