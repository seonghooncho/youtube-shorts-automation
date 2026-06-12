# shared/jobs/generate_scripts_from_filtered.py

import json
from typing import Any, Dict
from generator.text.generate_script import generate_script, ReturnScript
from generator.text.script_quality import (
    build_source_profile,
    hard_quality_errors,
    quality_issues_to_regenerate_reason,
    script_text,
    source_reject_reason_for_marketability,
    validate_script_quality,
)
from shared.utils.config import VIABLE_POSTS_FILE, FINAL_METADATA_FILE, FAILED_POSTS_FILE
EXAMPLE_JSON = """
{
        "title": "Neighbor's Tenants' Kids Invaded My Property",
        "description": "Dealing with a neighbor's tenants' kids running amok in my driveway. Can you relate to this frustrating situation?",
        "tags": ["storytime", "neighborhood", "drama", "reddit", "beachhouse"],
        "voice": "male",
        "visual_keywords": ["suburban driveway", "security camera", "kids playing", "angry neighbor", "rental house", "phone messages"],
        "source_summary": "The narrator owns a townhouse near a short-term rental and has repeated problems with renters' kids using his driveway and yard without permission.",
        "story_beats": [
                "Neighboring rental guests start cutting through the narrator's property.",
                "Security camera alerts show kids using the driveway as a play area.",
                "The narrator tells them to leave through the camera speaker.",
                "The next-door owner dismisses the complaint instead of apologizing.",
                "The narrator sets a firm boundary and asks if that was too strict."
        ],
        "adaptation_strategy": "Compressed repeated property issues into one escalating driveway incident, sharpened the neighbor's dismissive response, and kept the same boundary dispute and final dilemma.",
        "retention_angle": "The story has a clear property boundary violation, an unreasonable neighbor response, and a final moral split about whether the narrator was too strict.",
        "viewer_question": "Would you have shut it down too?",
        "marketability_score": 5,
        "script": [
                "A dozen kids turned my driveway into their playground, and their parents acted like I was the problem.",
                "I own a small townhouse near the beach, and the unit next door is a short-term rental. At first, the guests were just cutting across my yard. Annoying, but whatever.",
                "Then one night my security camera kept pinging. I opened the app and saw kids running across my driveway, doing flips, screaming, and using my property like a party space.",
                "I waited a few minutes, hoping an adult would step in. Nobody did. When one kid wiped out hard on the concrete, I used the camera speaker and told them they needed to leave.",
                "The next day, I sent screenshots to the owner next door. I expected an apology. Instead, they said the kids were just enjoying the outdoors and I was overreacting.",
                "So I told them clearly: their renters do not get to use my driveway, my yard, or my cameras as free supervision.",
                "Was I too strict, or would you have shut it down too?"
        ]
}
""".strip()

def call_gpt_generate_script(title, content, post=None, regenerate_reason=None):
    source = build_source_profile(post or {"title": title, "content": content})
    # 2) f-string은 치환이 필요한 부분(제목/본문)만 사용
    parts = [
        "You are adapting a Reddit story into a YouTube Shorts narration.",
        "Outcome: produce a fast, source-faithful, first-person script with strong Shorts retention.",
        "Audience: English-speaking Shorts viewers who decide in the first 2 seconds whether to keep watching.",
        "\n[Instructions]",
        "- Return the response in the exact same JSON structure shown in the example below.",
        '- **Detect the main character\'s gender** from the original story. Add a new key `"voice"` to the output JSON, whose value is either `"male"` or `"female"`, based on the main character’s gender for TTS selection. If gender is ambiguous, return `"neutral"`.',
        "- Treat the source as a seed story, not a transcript. Preserve the core conflict, relationship type, narrator's decision, consequence, and final moral question.",
        "- You may adapt the source into a more relatable, realistic Shorts story: compress repeated events into one clear scene, add plausible small dialogue, clarify motives, sharpen embarrassment or stakes, and make the conflict feel like something that could happen to a normal person.",
        "- You may improve weak source material by choosing the most relatable angle and making the narrator's dilemma more concrete, as long as the adapted story still belongs to the same conflict archetype.",
        "- Do not invent major unsafe or high-stakes facts: no new crimes, lawsuits, police, violence, sexual content, cheating, medical emergencies, revenge plans, pregnancy, minors, or job loss unless the source clearly supports them.",
        "- Do not change who was in conflict, the broad setting, the narrator's main action, or the final side-taking question.",
        "- Fill `source_summary` with the original story's core conflict, not the rewritten script.",
        "- Fill `story_beats` with 4 to 7 source-grounded beats: setup, escalation, decision, consequence, and final dilemma.",
        "- Fill `adaptation_strategy` with a transparent note about what you compressed or plausibly dramatized to make the story more watchable.",
        "- Fill `retention_angle` with the specific reason this story is clickable and watchable: boundary crossed, unfair accusation, betrayal, public embarrassment, money/property conflict, workplace/family pressure, or a hard moral split.",
        "- Fill `viewer_question` with the exact final comment-bait question. It must be a real question and should not be generic if the source supports a sharper one.",
        "- Fill `marketability_score` from 1 to 5. Use 4 or 5 only when the story has a concrete unfair action, clear stakes, and a debatable final decision.",
        "- Use a title that names the concrete conflict. Avoid generic titles like 'Did I Overreact?' unless paired with the specific action.",
        "- Write in a **casual, conversational tone**, as if you're sharing a story with a friend.",
        "- Avoid formal or stiff language. Use expressions and tones that are commonly seen in successful YouTube Shorts.",
        "- The first sentence must be a strong hook with a concrete crossed line. Start with what someone did wrong, what it cost, or why the narrator looked like the villain. Do not start with age, backstory, relationship length, 'So, get this', or 'A little backstory'.",
        "- The first 3 paragraphs must create an open loop: hook, quick context, then escalation. Do not explain every detail chronologically.",
        "- Every paragraph should either add a new problem, raise the stakes, or move toward the final decision. Cut neutral reflection.",
        "- Keep the pacing fast. Remove filler, repeated setup, and slow explanations. The narration should still be understandable after a moderate speed-up.",
        "- Structure the story in a `script` array of 5 to 7 short paragraphs. Never use more than 9 paragraphs.",
        "- The entire script should target 780 to 1080 characters, with an ideal landing point around 900 characters.",
        "- Anything over 1150 characters is invalid. Cut harder instead of explaining more.",
        "- The target final narration length is roughly 45 to 75 seconds after a moderate speed-up. Prefer concise sentences over long paragraphs.",
        "- The script should never feel stretched, repetitive, or abruptly shortened; keep only the setup, escalation, decision, and question.",
        "- Add a `visual_keywords` array with 5 to 8 short English stock-video search phrases that match the story's setting and emotion.",
        "- Visual keywords should be concrete and searchable, such as 'phone texting', 'couple argument', 'apartment hallway', 'office conversation', 'security camera', or 'angry neighbor'. Avoid generic terms like 'nature', 'background', or 'landscape' unless the story truly needs them.",
        "- Avoid visual keywords that imply minors, teenagers, school romance, sexual content, or anything that would make stock footage unsafe.",
        "- Do not mention Reddit, JSON, scripts, AI, viewers, or instructions inside the narration.",
        "- End the script with a question or prompt to encourage **viewer engagement**, such as:",
        '  - "So, what do you think?"',
        '  - "Would you have done the same?"',
        "\n[반환 형식 예시]",
        EXAMPLE_JSON,   # ← 안전: f-string 아님
        "\n[IMPORTANT]",
        "- The response **must strictly follow the JSON structure** shown above with no missing keys.",
        "- Any syntax or formatting error in the returned JSON will be considered a failure.",
        "- **If the script contains fewer than 750 characters or more than 1150 characters, it's considered invalid.**",
        "\n[Source metadata]",
        f"- Source provider: {source.provider or 'unknown'}",
        f"- Source URL: {source.source_url or 'unknown'}",
        f"- Source length: {source.char_count} chars, {source.word_count} words",
        f"- Source truncation flag: {source.is_truncated} {source.truncation_reason}".strip(),
        "\n[Original source]",
        f"Title: {title}",
        f"\nContent:\n{content}",
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

        # 기존 검증 스펙에 원문 충실도 검증용 필드를 추가한다.
        required_keys = [
            "title",
            "description",
            "tags",
            "voice",
            "visual_keywords",
            "source_summary",
            "story_beats",
            "adaptation_strategy",
            "retention_angle",
            "viewer_question",
            "marketability_score",
            "script",
        ]
        if not all(k in metadata for k in required_keys):
            raise ValueError("❌ 필수 키 누락")

        if not isinstance(metadata["script"], list) or not all(isinstance(line, str) for line in metadata["script"]):
            raise ValueError("❌ script는 문자열 리스트여야 함")
        if not isinstance(metadata["visual_keywords"], list) or not all(isinstance(keyword, str) for keyword in metadata["visual_keywords"]):
            raise ValueError("❌ visual_keywords는 문자열 리스트여야 함")
        if not isinstance(metadata["story_beats"], list) or not all(isinstance(beat, str) for beat in metadata["story_beats"]):
            raise ValueError("❌ story_beats는 문자열 리스트여야 함")
        if not isinstance(metadata["viewer_question"], str) or not metadata["viewer_question"].strip():
            raise ValueError("❌ viewer_question은 문자열이어야 함")
        if not isinstance(metadata["retention_angle"], str) or not metadata["retention_angle"].strip():
            raise ValueError("❌ retention_angle은 문자열이어야 함")
        if not isinstance(metadata["adaptation_strategy"], str) or not metadata["adaptation_strategy"].strip():
            raise ValueError("❌ adaptation_strategy는 문자열이어야 함")

        metadata["script"] = [line.strip() for line in metadata["script"] if line.strip()]
        metadata["story_beats"] = [beat.strip() for beat in metadata["story_beats"] if beat.strip()]

        if post and post.get("content"):
            marketability_reject = source_reject_reason_for_marketability(post)
            if marketability_reject:
                raise ValueError(f"❌ source_marketability_reject: {marketability_reject}")

        script_length = len(script_text(metadata))
        if script_length < 750:
            raise ValueError(f"❌ script가 너무 짧음 (현재 {script_length}자)")
        if script_length > 1150:
            raise ValueError(f"❌ script가 쇼츠 목표보다 너무 긺 (현재 {script_length}자)")

        metadata["visual_keywords"] = _clean_visual_keywords(metadata["visual_keywords"])
        metadata["script_char_count"] = script_length

        if post and post.get("content"):
            quality_issues = validate_script_quality(metadata, post)
            hard_errors = hard_quality_errors(quality_issues)
            if hard_errors:
                raise ValueError(f"❌ 품질검증 실패: {quality_issues_to_regenerate_reason(hard_errors)}")
            metadata["quality_warnings"] = [
                {"code": issue.code, "message": issue.message}
                for issue in quality_issues
                if not issue.hard
            ]

        return metadata
    except Exception as e:
        raise ValueError(f"post {idx} 오류: {e}")


def _source_preflight_error(post: Dict[str, Any]) -> str:
    source = build_source_profile(post)
    if source.is_truncated:
        return f"source content may be truncated: {source.truncation_reason or 'unknown reason'}"
    marketability_reject = source_reject_reason_for_marketability(post)
    if marketability_reject:
        return marketability_reject
    if source.char_count < 550 or source.word_count < 90:
        return f"source is too thin for faithful adaptation ({source.char_count} chars, {source.word_count} words)"
    return ""


def _regenerate_reason_from_error(message: str) -> str:
    if "너무 짧음" in message or "너무 긺" in message or "character" in message:
        return (
            "The script's length was out of bounds. Please revise it to 780 to 1080 "
            "characters, ideally around 900 characters, with a concrete conflict hook and no filler."
        )
    if "품질검증 실패" in message:
        return (
            "The previous script failed local quality validation. Fix these issues exactly: "
            f"{message}"
        )
    if (
        "필수 키 누락" in message
        or "script는 문자열 리스트" in message
        or "story_beats" in message
        or "viewer_question" in message
        or "retention_angle" in message
        or "adaptation_strategy" in message
    ):
        return "The response did not follow the required JSON structure. Please strictly follow the JSON example format."
    return f"Other error: {message}"


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
        preflight_error = _source_preflight_error(post)
        if preflight_error:
            failed_items.append({"idx": idx, "id": origin_id, "title": title, "error": preflight_error})
            print(f"🚫 원문 품질 미달로 스킵 (post {idx}): {preflight_error}")
            continue

        while try_count <= max_retries:
            result = None
            try:
                if try_count == 0:
                    result: ReturnScript = call_gpt_generate_script(title, content, post=post)
                else:
                    result: ReturnScript = call_gpt_generate_script(
                        title,
                        content,
                        post=post,
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
                if isinstance(result, ReturnScript):
                    print(
                        f"🧠 GPT 응답 (post {idx}, 시도 {try_count+1}):\n"
                        f"{json.dumps(result.model_dump(), ensure_ascii=False, indent=2)}\n"
                    )
                msg = str(e)
                regenerate_reason = _regenerate_reason_from_error(msg)

                try_count += 1
                if try_count > max_retries:
                    failed_items.append({"idx": idx, "id": origin_id, "title": title, "error": msg})
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
    preflight_error = _source_preflight_error(target_post)
    if preflight_error:
        print(f"🚫 원문 품질 미달로 재생성 불가 (postId={post_id}): {preflight_error}")
        return None

    while try_count <= max_retries:
        try:
            if try_count == 0:
                result: ReturnScript = call_gpt_generate_script(title, content, post=target_post)
            else:
                result: ReturnScript = call_gpt_generate_script(
                    title,
                    content,
                    post=target_post,
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
            regenerate_reason = _regenerate_reason_from_error(msg)
            try_count += 1

    print(f"🚫 최종 실패 (postId={post_id})")
    return None


def _clean_visual_keywords(keywords: list[str]) -> list[str]:
    cleaned = []
    blocked = {"nature", "background", "landscape"}
    for keyword in keywords:
        normalized = " ".join(str(keyword or "").lower().split())
        if not normalized or normalized in blocked:
            continue
        if normalized not in cleaned:
            cleaned.append(normalized)
        if len(cleaned) >= 8:
            break
    return cleaned[:8] or ["phone texting", "person thinking", "city street", "apartment hallway", "people talking"]


if __name__ == "__main__":
    generate_scripts_from_filtered()
