# shared/jobs/filter_viable_posts.py

import os
import json
import re
from typing import List, Literal
from dotenv import load_dotenv, find_dotenv
import openai
from pydantic import BaseModel, Field, ValidationError
from generator.text.script_quality import build_source_profile, source_reject_reason_for_marketability
from shared.utils.config import RAW_POSTS_FILE, VIABLE_POSTS_FILE


# -----------------------------
# Env & OpenAI Client
# -----------------------------
def _get_client() -> openai.OpenAI:
    # 현재 작업 디렉터리 기준으로 .env 탐색/로드
    env_path = find_dotenv(usecwd=True)
    load_dotenv(env_path)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
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
    for attempt, messages in enumerate(messages_templates, start=1):
        try:
            kwargs = {
                "model": model,
                "input": messages,
                "max_output_tokens": 512,
                "text": {"format": {"type": "json_object"}, "verbosity": "low"},
            }
            kwargs.update(_reasoning_kwargs_for_model(model))
            resp = client.responses.create(**kwargs)
            return _parse_source_scorecard(resp.output_text or "")
        except Exception as e:
            print(f"⚠️ source scorecard 호출 실패 (시도 {attempt}/{len(messages_templates)}): {e}")
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


# -----------------------------
# Main
# -----------------------------
def filter_viable_posts():
    client = _get_client()
    model = os.getenv("FILTER_MODEL") or os.getenv("OPENAI_MODEL", "gpt-5.4-nano")

    if not RAW_POSTS_FILE.exists():
        print("❌ raw_posts.json이 없습니다.")
        return

    with open(RAW_POSTS_FILE, "r", encoding="utf-8") as f:
        raw_posts = json.load(f)

    selected_posts: List[dict] = []

    for post in raw_posts:
        title, content = post.get("title", ""), post.get("content", "")
        local_reject_reason = _local_precheck(post)
        if local_reject_reason:
            print(f"⏭️ 로컬 precheck 실패로 스킵: {post.get('id', 'unknown')} - {local_reject_reason}")
            continue

        prompt = f"""
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

        Use NO if the source is mostly contextless, a question without a story, rage bait without events,
        low-stakes relationship ambiguity, graphic/sexual content involving minors, teen/high-school romance,
        explicit hate or slurs, or a post that requires major fabrication.

        Title: {title}
        Source metadata:
        - chars: {post.get('content_char_count', len(content))}
        - words: {post.get('content_word_count', len(content.split()))}
        - provider: {post.get('source_provider', 'unknown')}

        Content:
        {content}
        """

        try:
            scorecard = _ask_source_scorecard(client, prompt, model)
            if not scorecard:
                print("⚠️ GPT scorecard 응답 없음/불명. 해당 포스트 스킵")
                continue

            source_score = _scorecard_average(scorecard)
            min_score = float(os.getenv("SOURCE_SCORE_MIN_AVG", "4.0"))
            if scorecard.decision == "YES" and scorecard.retention_risk != "high" and source_score >= min_score:
                post["source_scorecard"] = scorecard.model_dump()
                post["source_score"] = source_score
                post["source_archetype"] = scorecard.archetype.strip().lower()[:80]
                selected_posts.append(post)
            else:
                print(
                    "⏭️ source scorecard reject: "
                    f"id={post.get('id', 'unknown')} decision={scorecard.decision} "
                    f"score={source_score} risk={scorecard.retention_risk} reason={scorecard.reason}"
                )

        except Exception as e:
            print(f"GPT 판단 오류: {e}")
            continue

    selected_posts.sort(key=lambda item: float(item.get("source_score") or 0), reverse=True)
    with open(VIABLE_POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(selected_posts, f, ensure_ascii=False, indent=2)

    print(f"✅ 적합한 게시물 {len(selected_posts)}개 저장됨 → {VIABLE_POSTS_FILE}")


if __name__ == "__main__":
    filter_viable_posts()
