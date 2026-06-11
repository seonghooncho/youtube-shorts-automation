import os
from typing import List, Literal, Optional
from dotenv import load_dotenv, find_dotenv
import openai
from pydantic import BaseModel, ValidationError

# --- ENV & Client ---
def _get_client() -> openai.OpenAI:
    env_path = find_dotenv(usecwd=True)
    load_dotenv(env_path)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
    return openai.OpenAI(api_key=api_key)

def _default_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-5-mini")

# --- Schema ---
class ReturnScript(BaseModel):
    title: str
    description: str
    tags: List[str]
    voice: Literal["male", "female", "neutral"]
    script: List[str]

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
        text={"verbosity": "medium"},
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
        text={"format": {"type": "json_object"}, "verbosity": "medium"},
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

# --- Public API ---
def generate_script(prompt: str) -> ReturnScript:
    """
    1) Structured Outputs (responses.parse) 우선
    2) 실패 시 JSON 모드 폴백 (responses.create + model_validate_json)
    3) 시도별로 토큰 예산 상향 (잘림 방지)
    """
    budgets = [1800, 2400, 3000]  # 상황에 맞게 조정 가능
    last_err: Optional[Exception] = None
    client = _get_client()

    for i, max_tokens in enumerate(budgets, start=1):
        try:
            # 1차: Structured
            return _try_structured(client, prompt, max_tokens)
        except Exception as e1:
            last_err = e1
            print(f"⚠️ Structured 실패 (시도 {i}/{len(budgets)} | tokens={max_tokens}): {e1}")
            try:
                # 2차: 폴백(JSON 모드)
                rs = _fallback_json_mode(client, prompt, max_tokens)
                _assert_no_nulls(rs)
                return rs
            except Exception as e2:
                last_err = e2
                print(f"⚠️ JSON 폴백 실패 (시도 {i}/{len(budgets)} | tokens={max_tokens}): {e2}")

    raise RuntimeError(f"GPT 호출/검증 최종 실패: {last_err}")
