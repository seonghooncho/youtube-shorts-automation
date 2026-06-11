# shared/jobs/filter_viable_posts.py

import os
import json
import re
from typing import List
from dotenv import load_dotenv, find_dotenv
import openai
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
            resp = client.responses.create(
                model=model,
                input=messages,
                max_output_tokens=16,
            )
            raw = (resp.output_text or "").strip()
            verdict = _normalize_yes_no(raw)
            if verdict:
                return verdict
        except Exception as e:
            print(f"⚠️ YES/NO 호출 실패 (시도 {attempt}/{len(messages_templates)}): {e}")

    return ""  # 불명


# -----------------------------
# Main
# -----------------------------
def filter_viable_posts():
    client = _get_client()
    model = os.getenv("FILTER_MODEL") or os.getenv("OPENAI_MODEL", "gpt-5-mini")

    if not RAW_POSTS_FILE.exists():
        print("❌ raw_posts.json이 없습니다.")
        return

    with open(RAW_POSTS_FILE, "r", encoding="utf-8") as f:
        raw_posts = json.load(f)

    selected_posts: List[dict] = []

    for post in raw_posts:
        title, content = post.get("title", ""), post.get("content", "")

        prompt = f"""
        제목: {title}
        내용: {content}

        위 이야기가 유튜브 쇼츠 영상으로 적절한지 평가해줘. 너무 짧거나, 지루하거나, 부적절하면 'NO'로, 가능하면 'YES'로만 답해.
        """

        try:
            verdict = _ask_yes_no(client, prompt, model)
            if not verdict:
                print("⚠️ GPT 응답 없음/불명(공백·장황·해석불가). 해당 포스트 스킵")
                continue

            if verdict == "YES":
                selected_posts.append(post)
            # NO면 추가하지 않음

        except Exception as e:
            print(f"GPT 판단 오류: {e}")
            continue

    with open(VIABLE_POSTS_FILE, "w", encoding="utf-8") as f:
        json.dump(selected_posts, f, ensure_ascii=False, indent=2)

    print(f"✅ 적합한 게시물 {len(selected_posts)}개 저장됨 → {VIABLE_POSTS_FILE}")


if __name__ == "__main__":
    filter_viable_posts()
