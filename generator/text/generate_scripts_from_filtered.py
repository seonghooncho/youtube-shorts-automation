# shared/jobs/generate_scripts_from_filtered.py

import json
from typing import Any, Dict
from generator.text.generate_script import generate_script, ReturnScript
from shared.utils.config import VIABLE_POSTS_FILE, FINAL_METADATA_FILE, FAILED_POSTS_FILE
EXAMPLE_JSON = """
{
        "title": "Neighbor's Tenants' Kids Invaded My Property",
        "description": "Dealing with a neighbor's tenants' kids running amok in my driveway. Can you relate to this frustrating situation?",
        "tags": ["storytime", "neighborhood", "drama", "reddit", "beachhouse"],
        "voice": "male",
        "script": [
                "So, get this - I have this sweet little townhouse by the beach, perfect for relaxing weekends. But things took a crazy turn when my neighbor's short-term tenants' kids decided to turn my driveway into a playground!",
                "At first, it was just a mild annoyance seeing them walking through the yard. But one wild night, my security camera went nuts with alerts. I check it out, and there they were - more than a dozen kids, having a full-blown party on my property!",
                "Backflips, running around, you name it. It was chaos! I waited a bit, hoping they'd clear out, but when some poor kid face-planted on my driveway, I had to step in.",
                "I politely told them through the camera to scram, and they did. Thought that would be the end of it, right? Wrong!",
                "I sent a few screenshots to the short-term rental owner, thinking they'd apologize and handle it. But nope, they defended the invasion, called it 'kids being kids' and 'a family enjoying the outdoors.' What a joke!",
                "I made it clear - no way I was gonna let their tenants and their wild kids mess up my peace. End of the story.",
                "Can you believe the nerve? What would you have done in my shoes?"
        ]
}
""".strip()

def call_gpt_generate_script(title, content, regenerate_reason=None):
    # 2) f-string은 치환이 필요한 부분(제목/본문)만 사용
    parts = [
        "You're helping me create a short video script for social media.",
        "This script should be written **in first-person**, like I'm telling my own story to a friend.",
        "Here is a story originally posted on Reddit.",
        f"\nTitle: {title}\n",
        f"\nContent:\n{content}\n",
        "Based on this story, create a **video script in JSON format** that would feel natural and engaging for an English-speaking audience.",
        "\n[Instructions]",
        "- Return the response in the **exact same JSON structure** shown in the example below.",
        '- **Detect the main character\'s gender** from the original story. Add a new key `"voice"` to the output JSON, whose value is either `"male"` or `"female"`, based on the main character’s gender for TTS selection. If gender is ambiguous, return `"neutral"`.',
        "- The story should be rewritten with natural flow and clarity, including context **before and after the main event**, so the viewer can easily understand what happened.",
        "- If the original story lacks detail, you’re encouraged to **creatively expand the scenario** with logical background, emotions, or interactions.",
        "- You may modify, rewrite, or dramatize the story to make it more **relatable and engaging**.",
        "- Write in a **casual, conversational tone**, as if you're sharing a story with a friend.",
        "- Avoid formal or stiff language. Use expressions and tones that are commonly seen in successful YouTube Shorts.",
        "- Structure the story in **paragraph-style script**.",
        "- The entire script must be between 900 and 1100 characters (with an absolute minimum of 750 characters, and must NOT exceed 1200 characters).",
        "- **However, do NOT sacrifice the natural story flow, emotional build-up, or essential details just to fit the character count.**",
        "- If you cannot meet the character requirement *without harming the story’s quality or flow*, always prioritize a complete, immersive, and logically satisfying script—even if it means being slightly outside the target range.",
        "- The script should never feel stretched, repetitive, or abruptly shortened; the most important thing is that it feels engaging, natural, and well-paced.",
        "- The entire script should be at least 1500 characters long, but don't just stretch it — instead, feel free to expand the story naturally, adding background, emotions, or dialogue to help the viewer stay engaged.",
        "- If the original story lacks enough detail, you're encouraged to creatively fill in the gaps to make it feel complete and immersive.",
        "- End the script with a question or prompt to encourage **viewer engagement**, such as:",
        '  - "So, what do you think?"',
        '  - "Would you have done the same?"',
        "\n[반환 형식 예시]",
        EXAMPLE_JSON,   # ← 안전: f-string 아님
        "\n[IMPORTANT]",
        "- The response **must strictly follow the JSON structure** shown above with no missing keys.",
        "- Any syntax or formatting error in the returned JSON will be considered a failure.",
        "- **If the script contains fewer than 1400 characters, it's also considered invalid.**",
    ]
    prompt = "\n".join(parts)

    if regenerate_reason:
        prompt += (
            "\n\n[ADDITIONAL INSTRUCTIONS]\n"
            "- The script needs to be regenerated for the following reason:\n"
            f"  **{regenerate_reason}**\n"
            "- Please revise or rewrite the script accordingly, while still following all instructions above.\n"
        )

    return generate_script(prompt)


def validate_and_parse_metadata(result: ReturnScript, idx, post) -> Dict[str, Any]:
    """
    ReturnScript(객체) → dict로 변환하고, 기존 검증 로직(키/타입/길이)을 유지.
    최종적으로 기존과 동일한 JSON(dict) 형태를 반환.
    """
    try:
        # Pydantic → dict
        metadata: Dict[str, Any] = result.model_dump()

        # 기존 검증 스펙 유지
        required_keys = ["title", "description", "tags", "script"]
        if not all(k in metadata for k in required_keys):
            raise ValueError("❌ 필수 키 누락")

        if not isinstance(metadata["script"], list) or not all(isinstance(line, str) for line in metadata["script"]):
            raise ValueError("❌ script는 문자열 리스트여야 함")

        script_length = sum(len(line) for line in metadata["script"])
        if script_length < 500:
            raise ValueError(f"❌ script가 너무 짧음 (현재 {script_length}자)")
        if script_length > 2000:
            raise ValueError(f"❌ script가 너무 긺 (현재 {script_length}자)")

        return metadata
    except Exception as e:
        raise ValueError(f"post {idx} 오류: {e}")


def generate_scripts_from_filtered():
    if not VIABLE_POSTS_FILE.exists():
        print("❌ viable_posts.json이 없습니다.")
        return

    with open(VIABLE_POSTS_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)

    metadata_list = []
    failed_items = []

    for idx, post in enumerate(posts):
        title = post.get("title", "")
        content = post.get("content", "")
        origin_id = post.get("id", None)
        regenerate_reason = None
        try_count = 0
        max_retries = 2  # 최대 2회까지 재생성 (기존 유지)

        while try_count <= max_retries:
            try:
                if try_count == 0:
                    result: ReturnScript = call_gpt_generate_script(title, content)
                else:
                    result: ReturnScript = call_gpt_generate_script(
                        title,
                        content,
                        regenerate_reason=regenerate_reason or "The previous script did not meet character length or structure requirements. Please revise accordingly."
                    )

                # 디버그 출력(가독성 위해 JSON 문자열로 출력)
                print(
                    f"🧠 GPT 응답 (post {idx}, 시도 {try_count+1}):\n"
                    f"{json.dumps(result.model_dump(), ensure_ascii=False, indent=2)}\n"
                )

                # [ADDED] 빈 응답/None 방어
                if result is None:
                    raise ValueError("GPT 응답 없음 (None)")

                # 검증 및 dict로 변환
                metadata = validate_and_parse_metadata(result, idx, post)

                # 기존 필드(id, uploaded) 부가
                if origin_id is not None:
                    metadata["id"] = origin_id
                    metadata["uploaded"] = False

                metadata_list.append(metadata)
                break  # 성공 시 루프 종료

            except Exception as e:
                print(
                    f"🧠 GPT 응답 (post {idx}, 시도 {try_count+1}):\n"
                    f"{json.dumps(result.model_dump(), ensure_ascii=False, indent=2)}\n"
                )
                msg = str(e)

                # 에러 사유별 재생성 가이드 유지
                if "너무 짧음" in msg or "너무 긺" in msg or "character" in msg:
                    regenerate_reason = "The script's length was out of bounds. Please revise the story so the script is between 900 and 1100 characters, but do not sacrifice natural flow or emotional build-up."
                elif "필수 키 누락" in msg or "script는 문자열 리스트" in msg:
                    regenerate_reason = "The script did not follow the required JSON structure. Please strictly follow the JSON example format."
                else:
                    regenerate_reason = f"Other error: {msg}"

                try_count += 1
                if try_count > max_retries:
                    failed_items.append({"idx": idx, "title": title, "error": msg})
                    print(f"🚫 최종 실패 (post {idx}): {msg}")

    with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata_list, f, ensure_ascii=False, indent=2)

    if failed_items:
        with open(FAILED_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(failed_items, f, ensure_ascii=False, indent=2)
        print(f"⚠️ 실패한 포스트 {len(failed_items)}개 → {FAILED_POSTS_FILE}에 저장됨")

    print(f"📦 최종 메타데이터 저장 완료 → {FINAL_METADATA_FILE}")


# 게시물 id로 재생성하는 함수(tts생성시 사용) — 이름/기능 변경 금지
def regenerate_post_by_id(post_id, regenerate_reason=None, max_retries=2):
    """
    특정 post_id에 해당하는 viable_posts.json 항목만 재생성.
    - 성공 시 dict(최종 metadata) 반환
    - 실패 시 None 반환
    """
    with open(VIABLE_POSTS_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)

    target_post = None
    for post in posts:
        if str(post.get("id")) == str(post_id):
            target_post = post
            break

    if target_post is None:
        print(f"❌ postId={post_id} 에 해당하는 항목 없음!")
        return None

    title = target_post.get("title", "")
    content = target_post.get("content", "")
    origin_id = target_post.get("id")
    try_count = 0

    while try_count <= max_retries:
        try:
            if try_count == 0:
                result: ReturnScript = call_gpt_generate_script(title, content)
            else:
                result: ReturnScript = call_gpt_generate_script(
                    title,
                    content,
                    regenerate_reason=regenerate_reason or "The previous script did not meet character length or structure requirements. Please revise accordingly."
                )

            print(
                f"🧠 GPT 응답 (postId={post_id}, 시도 {try_count+1}):\n"
                f"{json.dumps(result.model_dump(), ensure_ascii=False, indent=2)}\n"
            )

            if result is None:
                raise ValueError("GPT 응답 없음 (None)")

            metadata = validate_and_parse_metadata(result, f"postId={post_id}", target_post)

            if origin_id is not None:
                metadata["id"] = origin_id
                metadata["uploaded"] = False

            # ★ final_metadata와 동일 구조(dict) 반환
            return metadata

        except Exception as e:
            print(f"⚠️ 오류 (postId={post_id}, 시도 {try_count+1}): {e}")
            if 'result' in locals() and isinstance(result, ReturnScript):
                print(
                    f"🧠 마지막 GPT 응답:\n"
                    f"{json.dumps(result.model_dump(), ensure_ascii=False, indent=2)}\n"
                )
            msg = str(e)
            if "너무 짧음" in msg or "너무 긺" in msg or "character" in msg:
                regenerate_reason = "The script's length was out of bounds. Please revise the story so the script is between 900 and 1100 characters, but do not sacrifice natural flow or emotional build-up."
            elif "필수 키 누락" in msg or "script는 문자열 리스트" in msg:
                regenerate_reason = "The script did not follow the required JSON structure. Please strictly follow the JSON example format."
            else:
                regenerate_reason = f"Other error: {msg}"
            try_count += 1

    print(f"🚫 최종 실패 (postId={post_id})")
    return None


if __name__ == "__main__":
    generate_scripts_from_filtered()
