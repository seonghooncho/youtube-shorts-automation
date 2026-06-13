# shared/jobs/generate_scripts_from_filtered.py

import json
import os
import re
from pathlib import Path
from typing import Any, Dict
from generator.text.content_gate import ensure_content_gate, normalize_narration_fields
from generator.text.failure_policy import FailureAction, classify_failure
from generator.text.generate_script import generate_script, ReturnScript
from generator.text.metadata_repair import repair_metadata
from generator.text.script_fingerprint import (
    STYLE_VARIANTS,
    apply_script_fingerprint,
    batch_diversity_issues,
    diversity_issues_to_reason,
)
from generator.text.script_quality import (
    MAX_SCRIPT_CHARS,
    MIN_SCRIPT_CHARS,
    TARGET_MAX_SCRIPT_CHARS,
    TARGET_MIN_SCRIPT_CHARS,
    build_source_profile,
    hard_quality_errors,
    quality_issues_to_regenerate_reason,
    script_text,
    source_reject_reason_for_marketability,
    validate_script_quality,
)
from generator.text.youtube_metadata import apply_youtube_metadata_style
from shared.state import ContentRepository
from shared.storage import S3Store
from shared.utils.config import VIABLE_POSTS_FILE, FINAL_METADATA_FILE, FAILED_POSTS_FILE

_PERFORMANCE_CONTEXT_CACHE: str | None = None

EXAMPLE_JSON = """
{
        "title": "Neighbor's Tenants' Kids Invaded My Property",
        "description": "Dealing with a neighbor's tenants' kids running amok in my driveway. Can you relate to this frustrating situation?",
        "tags": ["storytime", "neighborhood", "drama", "reddit", "beachhouse"],
        "voice": "male",
        "visual_keywords": ["suburban driveway", "security camera", "kids playing", "angry neighbor", "rental house", "phone messages"],
        "first_frame_text": "KIDS TOOK OVER MY DRIVEWAY",
        "opening_visual_query": "kids playing driveway security camera",
        "visual_beat_queries": [
                {"beat": "hook", "query": "kids playing driveway security camera"},
                {"beat": "receipt", "query": "phone security camera alert"},
                {"beat": "decision", "query": "private driveway parking sign"}
        ],
        "hook_type": "crossed_boundary",
        "first_2_seconds": "A dozen kids turned my driveway into their playground",
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
        "turning_point": "The owner next door dismisses the complaint instead of apologizing.",
        "payoff_line": "I told them my property was not free supervision for their renters.",
        "viewer_question": "Would you have shut it down too?",
        "marketability_score": 5,
        "retention_risk": "The source has repeated incidents, so the rewrite compresses them into one camera-alert scene before the neighbor response.",
        "cut_plan": ["driveway hook", "phone camera alert", "kids running", "owner text message", "final boundary question"],
        "bg_strategy": "hybrid",
        "style_variant": "neighbor_dispute",
        "voiceover_lines": [
                "A dozen kids turned my driveway into their playground.",
                "The unit next door is a short-term rental, so guests change every few days.",
                "One night my security camera kept pinging while I was trying to work.",
                "I opened the app and saw kids doing flips on my driveway.",
                "When one kid fell on the concrete, I used the camera speaker and told them to leave.",
                "The owner texted back that they were just enjoying the outdoors.",
                "I sent screenshots and said my driveway was not free supervision.",
                "Would you have shut it down too?"
        ],
        "tts_text": "A dozen kids turned my driveway into their playground. The unit next door is a short-term rental, so guests change every few days. One night my security camera kept pinging while I was trying to work. I opened the app and saw kids doing flips on my driveway. When one kid fell on the concrete, I used the camera speaker and told them to leave. The owner texted back that they were just enjoying the outdoors. I sent screenshots and said my driveway was not free supervision. Would you have shut it down too?",
        "caption_chunks": ["kids turned my driveway", "security camera kept pinging", "kids doing flips on my driveway", "The owner texted back", "Would you have shut it down too?"],
        "rewrite_notes": "Removed slow vacation-rental context and led with the crossed boundary.",
        "script": [
                "A dozen kids turned my driveway into their playground.",
                "The unit next door is a short-term rental, so guests change every few days.",
                "One night my security camera kept pinging while I was trying to work.",
                "I opened the app and saw kids doing flips on my driveway.",
                "When one kid fell on the concrete, I used the camera speaker and told them to leave.",
                "The owner texted back that they were just enjoying the outdoors.",
                "I sent screenshots and said my driveway was not free supervision.",
                "Would you have shut it down too?"
        ]
}
""".strip()

def call_gpt_generate_script(title, content, post=None, regenerate_reason=None):
    source = build_source_profile(post or {"title": title, "content": content})
    performance_context = _performance_context()
    target_min_chars, target_max_chars = _script_target_window()
    # 2) f-string은 치환이 필요한 부분(제목/본문)만 사용
    parts = [
        "You are adapting a Reddit story into a YouTube Shorts narration.",
        "Outcome: produce a fast, source-faithful, first-person script with strong Shorts retention and structured signals for future performance learning.",
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
        "- Fill `hook_type` with a short snake_case label such as unfair_accusation, crossed_boundary, money_pressure, public_embarrassment, betrayal, villain_framing, or family_pressure.",
        f"- Fill `style_variant` with one of: {', '.join(STYLE_VARIANTS)}. Choose the most concrete variant for this source, and avoid repeating the same style in a batch.",
        "- Fill `first_2_seconds` with the exact opening phrase that carries the first two seconds of attention. It must be concrete, not context.",
        "- Fill `turning_point` with the moment where the situation gets worse, not just a summary.",
        "- Fill `payoff_line` with the final conflict statement before the viewer question.",
        "- Fill `viewer_question` with the exact final comment-bait question. It must be a real question and should not be generic if the source supports a sharper one.",
        "- Fill `marketability_score` from 1 to 5. Use 4 or 5 only when the story has a concrete unfair action, clear stakes, and a debatable final decision.",
        "- Fill predicted performance scores honestly on a 1-10 scale: retention and clarity must be 8 or higher, comment score must be 7 or higher, and `predicted_ai_smell_score` is reversed where 1 means fully human/native and 10 means very AI/template. Accepted scripts must keep `predicted_ai_smell_score` at 3 or lower.",
        "- Fill `critic_scores.ai_smell_score` using the same reversed AI-smell scale: 1 is best, 10 is worst. Keep it at 3 or lower for a script you believe should publish.",
        "- Fill `retention_risk` with the main reason viewers might swipe away and how your rewrite prevents it.",
        "- Fill `cut_plan` with 4 to 6 short visual cut intentions. Use concrete settings, hands, phones, bills, hallway, kitchen, office, vet, car, or message shots.",
        "- Fill `bg_strategy` as `story`, `asmr`, or `hybrid`. Use `hybrid` for most stories, `story` when concrete visual scenes matter, and `asmr` only when the source is mostly emotional or abstract.",
        "- Fill `rewrite_notes` with one short note about what you tightened for retention.",
        "- Use a title that names the concrete conflict. Avoid generic titles like 'Did I Overreact?' unless paired with the specific action. Do not add hashtags; the uploader adds the channel hashtag style.",
        "- Do not use AITA-style public titles. Never start the title with AITA, Am I the Asshole, Am I wrong, or Did I overreact.",
        "- Write in a **casual, conversational tone**, as if you're sharing a story with a friend.",
        "- Avoid formal or stiff language. Use expressions and tones that are commonly seen in successful YouTube Shorts.",
        "- Avoid generic AI-storytelling phrases: acted like I was the problem, the unreasonable one, people are split, half the people, keep the peace, let it go, crossed a boundary, the situation, the issue, the conflict, the drama, what changed everything, that was when, instead of owning it, I decided to stand my ground, I set a boundary, I held the boundary, The proof was clear, What made it worse was.",
        "- Prefer concrete receipts and actions over abstract labels. Each accepted script needs at least four source-grounded details such as a specific object, place, message, receipt, bill, camera, app, photo, timestamp, money amount, count, or exact action someone took.",
        "- The first sentence must be a strong hook with a concrete crossed line. Start with what someone did wrong, what it cost, or why the narrator looked like the villain. Do not start with age, backstory, relationship length, 'So, get this', or 'A little backstory'.",
        "- The first 3 voiceover lines must follow this rhythm: hook result, quick context, then unexpected escalation. Do not explain every detail chronologically.",
        "- Every voiceover line should either add a new problem, raise the stakes, or move toward the final decision. Cut neutral reflection.",
        "- Keep the pacing fast. Remove filler, repeated setup, and slow explanations. The narration should still be understandable after a moderate speed-up.",
        "- Structure the story in `voiceover_lines` as 7 to 10 complete short lines. Keep `script` identical to `voiceover_lines` for compatibility.",
        "- Fill `tts_text` as the complete narration joined from `voiceover_lines`, with natural punctuation pauses before the receipt/reveal and before the final question.",
        "- Fill `caption_chunks` as short retention captions: max 42 characters per chunk, final question as its own chunk, first caption clearly shows the conflict, and do not reveal the twist too early.",
        "- `caption_chunks` must use exact words from `voiceover_lines`/`tts_text` in the same order. They are display chunks from the spoken narration, not summaries or paraphrases.",
        f"- The joined `script` narration must be {target_min_chars} to {target_max_chars} characters, including spaces.",
        f"- Anything over {MAX_SCRIPT_CHARS} characters is invalid. Cut harder instead of explaining more.",
        "- Line limits: first line under 120 characters, no voiceover line over 170 characters.",
        "- The final viewer question must be the separate final line.",
        "- Before returning, silently count the joined `script` characters and cut until it fits the target window. Do not reveal the count.",
        "- Prefer 120 to 170 spoken words total. Remove repeated history, extra dialogue, and neutral reflection first.",
        "- The target final narration length is roughly 42 to 65 seconds after a moderate speed-up. Prefer concise sentences over long lines.",
        "- The script should never feel stretched, repetitive, or abruptly shortened; keep only the setup, escalation, decision, and question.",
        "- Keep the final line short. Do not pack new facts and the viewer question into one overloaded sentence.",
        "- Add `first_frame_text` as max 38 characters of all-caps hook text that shows the core conflict immediately. Do not use generic text like Story, Drama, AITA, or Did I Overreact.",
        "- Add `opening_visual_query` as the first background-video search query. It must match the first spoken line and `first_frame_text` with concrete overlapping words, not generic mood footage.",
        "- Add `visual_beat_queries` as ordered objects with `beat` and `query` keys. The first beat should be hook; receipt/reveal beats should prefer camera, phone, receipt, bill, app, screenshot, timestamp, or group chat when the source supports them.",
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
        f"- **If the joined voiceover contains fewer than {MIN_SCRIPT_CHARS} characters or more than {MAX_SCRIPT_CHARS} characters, it's considered invalid.**",
        "\n[Source metadata]",
        f"- Source provider: {source.provider or 'unknown'}",
        f"- Source URL: {source.source_url or 'unknown'}",
        f"- Source length: {source.char_count} chars, {source.word_count} words",
        f"- Source truncation flag: {source.is_truncated} {source.truncation_reason}".strip(),
        f"- Source scorecard: {json.dumps((post or {}).get('source_scorecard') or {}, ensure_ascii=False)}",
        f"- Recent winning patterns: {performance_context}",
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


def _performance_context() -> str:
    global _PERFORMANCE_CONTEXT_CACHE
    if _PERFORMANCE_CONTEXT_CACHE is not None:
        return _PERFORMANCE_CONTEXT_CACHE
    try:
        patterns = ContentRepository().winning_patterns()
    except Exception as exc:
        print(f"⚠️ performance context unavailable: {exc}")
        patterns = {}
    _PERFORMANCE_CONTEXT_CACHE = json.dumps(patterns or {}, ensure_ascii=False)
    return _PERFORMANCE_CONTEXT_CACHE


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _script_target_window() -> tuple[int, int]:
    target_min = _int_env("SCRIPT_TARGET_MIN_CHARS", TARGET_MIN_SCRIPT_CHARS)
    target_max = _int_env("SCRIPT_TARGET_MAX_CHARS", TARGET_MAX_SCRIPT_CHARS)
    return target_min, max(target_min, min(target_max, MAX_SCRIPT_CHARS))


def _max_llm_drafts_per_source() -> int:
    return max(1, _int_env("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", 2))


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
            "first_frame_text",
            "opening_visual_query",
            "visual_beat_queries",
            "hook_type",
            "first_2_seconds",
            "source_summary",
            "story_beats",
            "adaptation_strategy",
            "retention_angle",
            "turning_point",
            "payoff_line",
            "viewer_question",
            "marketability_score",
            "retention_risk",
            "cut_plan",
            "bg_strategy",
            "rewrite_notes",
            "style_variant",
            "script",
        ]
        if not all(k in metadata for k in required_keys):
            raise ValueError("❌ 필수 키 누락")

        if not isinstance(metadata["script"], list) or not all(isinstance(line, str) for line in metadata["script"]):
            raise ValueError("❌ script는 문자열 리스트여야 함")
        if not isinstance(metadata["visual_keywords"], list) or not all(isinstance(keyword, str) for keyword in metadata["visual_keywords"]):
            raise ValueError("❌ visual_keywords는 문자열 리스트여야 함")
        if not isinstance(metadata["first_frame_text"], str) or not metadata["first_frame_text"].strip():
            raise ValueError("❌ first_frame_text는 문자열이어야 함")
        metadata["first_frame_text"] = _clean_first_frame_text(
            metadata.get("first_frame_text")
            or metadata.get("first_2_seconds")
            or metadata.get("title")
        )
        if not isinstance(metadata["opening_visual_query"], str) or not metadata["opening_visual_query"].strip():
            raise ValueError("❌ opening_visual_query는 문자열이어야 함")
        if not isinstance(metadata["visual_beat_queries"], list) or not all(isinstance(beat, dict) for beat in metadata["visual_beat_queries"]):
            raise ValueError("❌ visual_beat_queries는 객체 리스트여야 함")
        if not all(isinstance(beat.get("beat"), str) and isinstance(beat.get("query"), str) for beat in metadata["visual_beat_queries"]):
            raise ValueError("❌ visual_beat_queries 항목은 beat/query 문자열을 포함해야 함")
        if not isinstance(metadata["story_beats"], list) or not all(isinstance(beat, str) for beat in metadata["story_beats"]):
            raise ValueError("❌ story_beats는 문자열 리스트여야 함")
        if not isinstance(metadata["viewer_question"], str) or not metadata["viewer_question"].strip():
            raise ValueError("❌ viewer_question은 문자열이어야 함")
        metadata["first_2_seconds"] = _clean_short_hook_text(metadata.get("first_2_seconds"), max_chars=95)
        if not isinstance(metadata["retention_angle"], str) or not metadata["retention_angle"].strip():
            raise ValueError("❌ retention_angle은 문자열이어야 함")
        if not isinstance(metadata["adaptation_strategy"], str) or not metadata["adaptation_strategy"].strip():
            raise ValueError("❌ adaptation_strategy는 문자열이어야 함")
        if not isinstance(metadata["cut_plan"], list) or not all(isinstance(cut, str) for cut in metadata["cut_plan"]):
            raise ValueError("❌ cut_plan은 문자열 리스트여야 함")
        if metadata.get("bg_strategy") not in {"story", "asmr", "hybrid"}:
            raise ValueError("❌ bg_strategy는 story, asmr, hybrid 중 하나여야 함")

        metadata["script"] = [line.strip() for line in metadata["script"] if line.strip()]
        metadata["voiceover_lines"] = [line.strip() for line in metadata.get("voiceover_lines") or [] if str(line).strip()]
        metadata["story_beats"] = [beat.strip() for beat in metadata["story_beats"] if beat.strip()]
        metadata["cut_plan"] = [cut.strip() for cut in metadata["cut_plan"] if cut.strip()][:6]
        metadata.setdefault("critic_scores", {})
        metadata.setdefault("critic_problems", [])
        metadata.setdefault("critic_rewrite_instructions", [])
        metadata.setdefault("critic_attempt_count", 0)
        normalize_narration_fields(metadata)
        if post and post.get("content"):
            metadata, repair_actions = repair_metadata(metadata, post, stage="pre_gate")
            if repair_actions:
                print(
                    f"🛠️ metadata repair applied (post {idx}): "
                    f"{', '.join(action.get('code', 'unknown') for action in repair_actions)}"
                )

        if post and post.get("content"):
            marketability_reject = source_reject_reason_for_marketability(post)
            if marketability_reject:
                raise ValueError(f"❌ source_marketability_reject: {marketability_reject}")

        script_length = len(script_text(metadata))
        if script_length < MIN_SCRIPT_CHARS:
            raise ValueError(f"❌ script가 너무 짧음 (현재 {script_length}자)")
        if script_length > MAX_SCRIPT_CHARS:
            raise ValueError(f"❌ script가 쇼츠 목표보다 너무 긺 (현재 {script_length}자)")

        metadata["visual_keywords"] = _clean_visual_keywords(metadata["visual_keywords"])
        metadata["script_char_count"] = script_length
        metadata["source_scorecard"] = (post or {}).get("source_scorecard") or {}
        metadata["source_score"] = (post or {}).get("source_score")
        metadata["source_archetype"] = (post or {}).get("source_archetype") or metadata.get("hook_type") or ""
        metadata["source_provider"] = (post or {}).get("source_provider", "")
        metadata["source_authenticity"] = (post or {}).get("source_authenticity") or metadata["source_provider"] or "unknown"
        metadata["source_collection_path"] = (post or {}).get("source_collection_path", "")
        metadata["source_detail_checked"] = bool((post or {}).get("source_detail_checked", False))
        metadata["source_detail_improved"] = bool((post or {}).get("source_detail_improved", False))
        metadata["source_quality_status"] = (post or {}).get("source_quality_status", "")
        metadata["source_rejection_reason"] = (post or {}).get("source_rejection_reason", "")
        metadata["source_title"] = (post or {}).get("title") or metadata.get("source_title") or metadata.get("title") or ""
        metadata["source_content_excerpt"] = str((post or {}).get("content") or "")[:3000]
        metadata["public_title"] = metadata.get("public_title") or metadata.get("title") or metadata["source_title"]
        metadata["source_subreddit"] = (post or {}).get("subreddit", "")
        metadata["source_url"] = (post or {}).get("source_url", "")
        metadata["source_hash"] = (post or {}).get("content_hash", "")

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

        apply_youtube_metadata_style(metadata)
        apply_script_fingerprint(metadata)
        return metadata
    except Exception as e:
        raise ValueError(f"post {idx} 오류: {e}")


def _source_preflight_error(post: Dict[str, Any]) -> str:
    source = build_source_profile(post)
    if str(post.get("source_provider") or "").strip().lower() == "synthetic" and not _synthetic_sources_allowed():
        return "synthetic source is disabled in production; set SCRIPT_ALLOW_SYNTHETIC_SOURCES=1 to allow it explicitly"
    if post.get("generation_fallback") == "local_template" and not _local_fallback_enabled():
        return "local-template script fallback is disabled in production; set SCRIPT_LOCAL_FALLBACK_ENABLED=1 to allow it explicitly"
    if source.is_truncated:
        return f"source content may be truncated: {source.truncation_reason or 'unknown reason'}"
    marketability_reject = source_reject_reason_for_marketability(post)
    if marketability_reject:
        return marketability_reject
    if source.char_count < 550 or source.word_count < 90:
        return f"source is too thin for faithful adaptation ({source.char_count} chars, {source.word_count} words)"
    return ""


def _regenerate_reason_from_error(message: str) -> str:
    if "batch_diversity_failed" in message:
        return (
            "The previous draft was too similar to another accepted script in this batch or recent history. "
            "Change the opening structure, transition rhythm, ending question, title opening, and style_variant. "
            f"Use a different style_variant from this list: {', '.join(STYLE_VARIANTS)}. "
            f"Details: {message}"
        )
    if "너무 짧음" in message or "너무 긺" in message or "character" in message:
        target_min_chars, target_max_chars = _script_target_window()
        current_chars = _extract_current_char_count(message)
        current_phrase = f"The previous joined script was {current_chars} characters. " if current_chars else ""
        return (
            f"{current_phrase}Return the full JSON again, but rewrite the `script` to "
            f"{target_min_chars}-{target_max_chars} characters total, hard max {MAX_SCRIPT_CHARS}. "
            "Aim for the middle of that range, not the lower edge. Use 8 to 10 complete voiceover lines. "
            "Add one or two concrete, source-grounded beats before the final question when the draft is short. "
            "Keep the same source conflict, hook, turning point, payoff, and final question. "
            "Cut only repeated backstory, extra dialogue, and neutral reflection. "
            "The first line must stay under 120 characters and no line may exceed 170 characters."
        )
    if "품질검증 실패" in message:
        return (
            "The previous script failed local quality validation. Fix these issues exactly: "
            f"{message}"
        )
    if any(code in message for code in ("weak_first_2_seconds", "weak_turning_point", "weak_payoff_line", "late_drag")):
        return (
            "The script needs a sharper first-two-seconds hook, a clearer turning point, "
            "and a shorter payoff before the viewer question. Do not add filler."
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


def _extract_current_char_count(message: str) -> int | None:
    match = re.search(r"(?:현재|chars?|characters?)\s*(\d+)|\((\d+)\s*(?:자|chars?)\)", message)
    if not match:
        return None
    for group in match.groups():
        if group:
            return int(group)
    return None


def _local_fallback_enabled() -> bool:
    return _truthy_env("SCRIPT_LOCAL_FALLBACK_ENABLED")


def _synthetic_sources_allowed() -> bool:
    return _truthy_env("SCRIPT_ALLOW_SYNTHETIC_SOURCES") or _truthy_env("ALLOW_SYNTHETIC_SOURCES")


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_llm_quota_error(message: str) -> bool:
    lowered = (message or "").lower()
    return "insufficient_quota" in lowered or "exceeded your current quota" in lowered or "rate_limit_exceeded" in lowered


def _build_local_fallback_metadata(post: Dict[str, Any], reason: str = "") -> Dict[str, Any]:
    """Create a conservative script from source text when the LLM API is unavailable."""
    if not _local_fallback_enabled():
        raise RuntimeError("local-template script fallback is disabled in production; set SCRIPT_LOCAL_FALLBACK_ENABLED=1 to allow it explicitly")
    if str(post.get("source_provider") or "").strip().lower() == "synthetic" and not _synthetic_sources_allowed():
        raise RuntimeError("synthetic source is disabled in production; set SCRIPT_ALLOW_SYNTHETIC_SOURCES=1 to allow it explicitly")
    source_title = _clean_sentence(post.get("title") or "Boundary Story", max_chars=92)
    content = str(post.get("content") or "")
    title = _fallback_public_title(source_title, content)
    parts = _extract_source_parts(content)
    boundary = _sanitize_fallback_detail(parts.get("boundary") or "I had already said yes only to a small favor")
    setup = parts.get("setup") or _fallback_setup_sentence(content) or "At first, I tried to handle it calmly."
    crossed_line = _sanitize_fallback_detail(parts.get("crossed_line") or _sentence_at(content, 1) or "someone turned the favor into a demand")
    public_pressure = _sanitize_fallback_detail(parts.get("public_pressure") or _sentence_at(content, 2) or "people around us started texting me about it")
    escalation = _sanitize_fallback_detail(parts.get("escalation") or _sentence_at(content, 3) or "the pressure kept building after I said no")
    proof = _sanitize_fallback_detail(parts.get("proof") or _sentence_at(content, 4) or "the messages showed exactly what I had agreed to")
    consequence = _sanitize_fallback_detail(parts.get("consequence") or _sentence_at(content, 5) or "I said no and stopped covering for it")
    debate = _sanitize_fallback_detail(parts.get("debate") or "Was I wrong to say no?")

    hook = _fallback_opening_hook(source_title, crossed_line, content)
    if not hook.endswith((".", "?", "!")):
        hook = f"{hook}."
    first_two = _fallback_first_two_seconds(hook, crossed_line)
    viewer_question = _ensure_question(_clean_sentence(debate, max_chars=150))
    payoff = _clean_sentence(consequence, max_chars=120)
    source_summary = _clean_sentence(f"{setup} {crossed_line}", max_chars=220)
    story_beats = [
        _clean_sentence(setup, max_chars=120),
        f"The original agreement was: {_clean_sentence(boundary, max_chars=110)}",
        _clean_sentence(crossed_line, max_chars=130),
        _clean_sentence(public_pressure, max_chars=130),
        _clean_sentence(proof, max_chars=130),
        _clean_sentence(consequence, max_chars=130),
    ]
    visual_keywords = _fallback_visual_keywords(title, content)

    script = [
        _finish_sentence(hook),
        _finish_sentence(_clean_sentence(f"I had already told them the agreement: {boundary}.", max_chars=185)),
        _finish_sentence(_clean_sentence(f"At first, {_sentence_fragment(setup)}", max_chars=185)),
        _finish_sentence(_clean_sentence(f"Then {crossed_line}.", max_chars=185)),
        _finish_sentence(_clean_sentence(f"After that, {public_pressure}.", max_chars=185)),
        _finish_sentence(_clean_sentence(f"By then, {_sentence_fragment(escalation)}", max_chars=185)),
        _finish_sentence(_clean_sentence(f"The receipt I had was simple: {proof}.", max_chars=185)),
        _finish_sentence(_clean_sentence(f"So {consequence}.", max_chars=185)),
        viewer_question,
    ]
    script = _fit_fallback_script(script, story_beats)

    metadata = ReturnScript(
        title=title,
        description=f"A fast storytime about a crossed boundary and the fallout after {title.lower()}.",
        tags=["storytime", "boundaries", "redditstories", "aita", "familydrama"],
        voice="neutral",
        visual_keywords=visual_keywords,
        first_frame_text=_clean_first_frame_text(first_two),
        opening_visual_query=visual_keywords[0] if visual_keywords else "phone message receipt",
        visual_beat_queries=[
            {"beat": "hook", "query": visual_keywords[0] if visual_keywords else "phone message receipt"},
            {"beat": "receipt", "query": visual_keywords[1] if len(visual_keywords) > 1 else "phone screenshot receipt"},
            {"beat": "decision", "query": visual_keywords[2] if len(visual_keywords) > 2 else "person texting decision"},
        ],
        hook_type=_fallback_hook_type(content),
        first_2_seconds=first_two,
        source_summary=source_summary,
        story_beats=story_beats[:6],
        adaptation_strategy="Compressed the source into a boundary, escalation, proof, decision, and final dilemma while preserving the core conflict.",
        retention_angle="The story has a concrete boundary crossed, public pressure, proof, and a debatable final decision.",
        turning_point=_clean_sentence(public_pressure if public_pressure else proof, max_chars=140),
        payoff_line=payoff,
        viewer_question=viewer_question,
        marketability_score=4,
        retention_risk="A local fallback can feel summarized, so the script opens on the crossed line and moves quickly to proof.",
        cut_plan=visual_keywords[:6],
        bg_strategy="hybrid",
        style_variant=_fallback_style_variant(title, content),
        rewrite_notes=f"Local fallback used after LLM generation became unavailable: {_clean_sentence(reason, max_chars=120)}",
        script=script,
    )
    parsed = validate_and_parse_metadata(metadata, "local_fallback", post)
    parsed["generation_fallback"] = "local_template"
    parsed["generation_fallback_reason"] = _clean_sentence(reason, max_chars=240)
    if post.get("id") is not None:
        parsed["id"] = post.get("id")
        parsed["uploaded"] = False
    return parsed


def _extract_source_parts(content: str) -> Dict[str, str]:
    text = " ".join(str(content or "").split())
    patterns = {
        "boundary": r"one clear boundary in this situation:\s*(.*?)(?:\.|$)",
        "crossed_line": r"Then\s+(.*?)(?:,\s*and acted like| and acted like|\.|$)",
        "public_pressure": r"The part that made people take sides was\s+(.*?)(?:\.|$)",
        "escalation": r"I tried to keep it calm.*?,\s*but\s+(.*?)(?:\.|$)",
        "proof": r"What changed everything was\s+(.*?)(?:\.|$)",
        "consequence": r"After that,\s+(.*?)(?:\.|$)",
    }
    parts = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            parts[key] = _clean_sentence(match.group(1), max_chars=180).rstrip(".")
    questions = re.findall(r"[^.!?]*\?", text)
    if questions:
        parts["debate"] = _clean_sentence(questions[-1], max_chars=160)
    return parts


def _sanitize_fallback_detail(text: str) -> str:
    cleaned = str(text or "")
    replacements = {
        "acted like I was the problem": "complained when I pushed back",
        "the unreasonable one": "too picky",
        "people are split": "people keep arguing about it",
        "half the people": "some people",
        "keep the peace": "avoid another argument",
        "let it go": "drop it",
        "crossed a boundary": "ignored what I had already said",
        "crossed the line": "went too far",
        "the situation": "what happened",
        "the issue": "the actual problem",
        "the conflict": "the argument",
        "the drama": "the mess",
        "what changed everything": "the detail that mattered",
        "instead of owning it": "instead of apologizing",
        "I decided to stand my ground": "I said no",
        "I set a boundary": "I said no",
        "I held the boundary": "I stuck with my no",
        "The boundary was simple": "The original agreement was simple",
        "I had one clear boundary in this situation": "I had already said exactly what I would allow",
        "What made it worse was": "The worst part was",
        "The proof was clear": "The receipt was right there",
        "Now people are split": "Now people keep arguing",
        "smooth it over": "drop it",
        "without asking me first": "before I said yes",
        "I tried to keep it calm": "I answered once without raising my voice",
        "my limit did not matter": "what I had said did not matter",
        "in this situation": "here",
    }
    for old, new in replacements.items():
        cleaned = re.sub(re.escape(old), new, cleaned, flags=re.IGNORECASE)
    return _clean_sentence(cleaned, max_chars=190).rstrip(".")


def _fallback_setup_sentence(content: str) -> str:
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", str(content or "")) if item.strip()]
    for sentence in sentences:
        if "one clear boundary in this situation" in sentence.lower():
            continue
        return sentence
    return ""


def _fallback_public_title(title: str, content: str) -> str:
    lowered = f"{title} {content}".lower()
    patterns = (
        (("driveway",), "Neighbor Used My Driveway"),
        (("grocery", "shared fund"), "Roommate Spent Our Grocery Fund"),
        (("birthday", "whole table", "my card"), "Family Tried To Put Dinner On My Card"),
        (("lunch", "pocketing"), "Coworker Accused Me Over A Lunch Order"),
        (("family trip", "paid extra", "room"), "Cousin Took My Reserved Trip Room"),
        (("trash bins", "walkway"), "Neighbor Blocked My Walkway With Trash Bins"),
        (("deposit", "rental"), "Friends Trashed The Rental Deposit"),
        (("car", "scratches"), "Cousin Returned My Car Empty And Scratched"),
        (("package",), "Neighbor Accused Me Of Taking Her Package"),
        (("storage unit",), "Brother Filled My Storage Unit"),
        (("coffee fund",), "Coworkers Drained The Office Coffee Fund"),
        (("laundry", "washer"), "Neighbor Blocked Both Laundry Machines"),
        (("bedroom", "couch"), "Family Tried To Take My Bedroom"),
        (("wedding", "invoice"), "Friend Put A Wedding Invoice In My Name"),
        (("outfit", "stain"), "Friend Returned My Borrowed Outfit Stained"),
        (("credit for my work", "file history"), "Coworker Took Credit For My Work"),
        (("streaming", "password"), "Sister Shared My Streaming Password"),
    )
    for terms, public_title in patterns:
        if all(term in lowered for term in terms):
            return public_title
    return _clean_sentence(title, max_chars=58).rstrip(".!?")


def _fallback_opening_hook(title: str, crossed_line: str, content: str) -> str:
    action = _fallback_hook_action(title, crossed_line, content)
    return _clean_sentence(f"{action}, then complained when I asked them to stop", max_chars=116)


def _fallback_hook_action(title: str, crossed_line: str, content: str) -> str:
    lowered = f"{title} {content}".lower()
    patterns = (
        (("driveway",), "My neighbor treated my driveway like his extra parking spot"),
        (("grocery", "shared fund"), "My roommate spent our grocery fund on snacks for friends"),
        (("birthday", "whole table", "my card"), "My aunt tried to put the whole birthday dinner on my card"),
        (("lunch", "pocketing"), "My coworker changed his lunch order and accused me of pocketing money"),
        (("family trip", "paid extra", "room"), "My cousin took the room I paid extra for and called it first come first served"),
        (("trash bins", "walkway"), "My neighbor kept blocking my walkway with his trash bins"),
        (("deposit", "rental"), "My friends trashed the rental and demanded their deposit back"),
        (("car", "scratches"), "My cousin returned my car late, empty, and scratched"),
        (("package",), "My neighbor accused me of stealing her package in the building chat"),
        (("storage unit",), "My brother filled my storage unit, then blamed me for needing my space"),
        (("coffee fund",), "Coworkers used the coffee fund without paying and blamed me when it ran out"),
        (("laundry", "washer"), "My neighbor blocked both washers and accused me of crossing a line"),
        (("bedroom", "couch"), "My uncle assigned my bedroom to a guest without asking me"),
        (("wedding", "invoice"), "A friend ordered wedding decorations in my name and expected me to pay"),
        (("outfit", "stain"), "My friend returned my borrowed outfit late, stained, and damaged"),
        (("credit for my work", "file history"), "A coworker presented my work as hers in front of our manager"),
        (("streaming", "password"), "My sister shared my streaming password and blamed me when it locked"),
    )
    for terms, action in patterns:
        if all(term in lowered for term in terms):
            return action
    action = _clean_clause(crossed_line, max_chars=72)
    return _sentence_case(action or "Someone crossed the one boundary I had made clear")


def _fallback_first_two_seconds(hook: str, crossed_line: str) -> str:
    raw = str(hook or "").rstrip(".!?")
    first_two = _clean_sentence(raw, max_chars=95).rstrip(".!?")
    if len(raw) > 95 or _is_dangling_phrase(first_two):
        action = _clean_clause(raw.split(", then acted", 1)[0], max_chars=95)
        if not action:
            action = _clean_clause(crossed_line, max_chars=54) or "someone crossed my boundary"
        first_two = _sentence_case(action)
    return first_two[:95].rstrip(" .,;:")


def _sentence_at(content: str, index: int) -> str:
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", str(content or "")) if item.strip()]
    return sentences[index] if 0 <= index < len(sentences) else ""


_DANGLING_TRAILING_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "because",
    "but",
    "by",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "her",
    "his",
    "in",
    "into",
    "i",
    "is",
    "like",
    "my",
    "of",
    "on",
    "our",
    "or",
    "should",
    "that",
    "the",
    "their",
    "then",
    "to",
    "was",
    "were",
    "with",
    "without",
    "would",
    "your",
}


def _clean_clause(text: str, *, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip(" -.,;:")
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip()
        if " " in cleaned:
            cleaned = cleaned.rsplit(" ", 1)[0]
    return _strip_dangling_tail(cleaned).strip(" -.,;:")


def _clean_sentence(text: str, *, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip(" -")
    if len(cleaned) <= max_chars:
        return cleaned
    truncated = cleaned[: max(0, max_chars - 1)].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    truncated = _strip_dangling_tail(truncated)
    return f"{truncated.rstrip('.,;:')}."


def _clean_first_frame_text(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 $'&-]+", "", str(text or "")).upper()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\bJUST\s+", "", cleaned)
    cleaned = re.sub(r"\bRIGHT\s+INTO\b", "INTO", cleaned)
    if len(cleaned) <= 38:
        return cleaned
    truncated = cleaned[:38].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return _strip_dangling_tail(truncated).strip()


def _clean_short_hook_text(text: str, *, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip(" -.,;:")
    if len(cleaned) <= max_chars:
        return cleaned
    truncated = cleaned[:max_chars].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return _strip_dangling_tail(truncated).strip(" -.,;:")


def _strip_dangling_tail(text: str) -> str:
    words = str(text or "").split()
    while words and words[-1].strip(".,;:!?").lower() in _DANGLING_TRAILING_WORDS:
        words.pop()
    return " ".join(words)


def _is_dangling_phrase(text: str) -> bool:
    last = str(text or "").strip().split(" ")[-1].strip(".,;:!?").lower()
    return last in _DANGLING_TRAILING_WORDS


def _finish_sentence(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith((".", "?", "!")) else f"{cleaned}."


def _sentence_fragment(text: str) -> str:
    fragment = str(text or "").strip()
    if not fragment:
        return ""
    if fragment.startswith("I "):
        return fragment
    return fragment[:1].lower() + fragment[1:]


def _sentence_case(text: str) -> str:
    text = str(text or "").strip()
    return text[:1].upper() + text[1:] if text else text


def _ensure_question(text: str) -> str:
    text = text.strip()
    if not text:
        return "Was I wrong to hold the boundary?"
    return text if text.endswith("?") else f"{text.rstrip('.!')}?"


def _fallback_visual_keywords(title: str, content: str) -> list[str]:
    lowered = f"{title} {content}".lower()
    if any(term in lowered for term in ("bill", "card", "invoice", "restaurant", "receipt")):
        return ["credit card close up", "restaurant bill", "phone group chat", "receipt on table", "awkward dinner table", "person leaving restaurant"]
    if any(term in lowered for term in ("driveway", "parking", "car", "gate")):
        return ["suburban driveway", "doorbell camera", "parked car", "phone neighborhood chat", "private parking sign", "package by gate"]
    if any(term in lowered for term in ("apartment", "room", "bedroom", "couch")):
        return ["apartment hallway", "bedroom door", "family dinner table", "phone messages", "couch in living room", "tense conversation"]
    if any(term in lowered for term in ("office", "coworker", "team", "manager")):
        return ["office conversation", "workplace break room", "phone screenshots", "desk close up", "team chat messages", "tense meeting"]
    return ["phone messages", "tense conversation", "kitchen table", "receipt close up", "person thinking", "final question screen"]


def _fallback_hook_type(content: str) -> str:
    lowered = str(content or "").lower()
    if any(term in lowered for term in ("bill", "card", "invoice", "deposit", "pay")):
        return "money_pressure"
    if any(term in lowered for term in ("driveway", "bedroom", "apartment", "car", "property")):
        return "crossed_boundary"
    if any(term in lowered for term in ("accused", "blamed", "called me")):
        return "unfair_accusation"
    return "boundary_conflict"


def _fallback_style_variant(title: str, content: str) -> str:
    lowered = f"{title} {content}".lower()
    if any(term in lowered for term in ("receipt", "screenshot", "camera", "message", "chat")):
        return "receipt_reveal"
    if any(term in lowered for term in ("bill", "card", "invoice", "deposit", "pay")):
        return "money_trap"
    if any(term in lowered for term in ("coworker", "office", "manager", "work")):
        return "workplace_receipt"
    if any(term in lowered for term in ("neighbor", "driveway", "walkway", "parking")):
        return "neighbor_dispute"
    if any(term in lowered for term in ("family", "aunt", "uncle", "sister", "brother", "cousin")):
        return "family_pressure"
    if any(term in lowered for term in ("accused", "blamed")):
        return "false_blame"
    return "last_straw"


def _fit_fallback_script(script: list[str], story_beats: list[str]) -> list[str]:
    joined_len = len(" ".join(script))
    if joined_len < TARGET_MIN_SCRIPT_CHARS and len(script) < 8:
        script.insert(
            -1,
            _clean_sentence(
                "The frustrating part was not just the mistake. It was being expected to absorb it quietly so nobody else had to feel uncomfortable.",
                max_chars=185,
            ),
        )
    if len(" ".join(script)) < TARGET_MIN_SCRIPT_CHARS and story_beats and len(script) < 8:
        script.insert(-1, _clean_sentence(f"That is why this felt bigger than one awkward moment: {story_beats[0]}", max_chars=185))
    while len(" ".join(script)) > MAX_SCRIPT_CHARS and len(script) > 7:
        script.pop(-2)
    return script


def _accept_metadata(
    metadata: Dict[str, Any],
    metadata_list: list[Dict[str, Any]],
    previous_history: list[Dict[str, Any]],
) -> None:
    apply_script_fingerprint(metadata)
    ensure_content_gate(metadata, stage="script_accept")
    diversity_issues = batch_diversity_issues(metadata, metadata_list, previous_history)
    if diversity_issues:
        raise ValueError(f"batch_diversity_failed: {diversity_issues_to_reason(diversity_issues)}")
    metadata_list.append(metadata)


def _load_previous_accepted_metadata(limit: int = 50) -> list[Dict[str, Any]]:
    if os.getenv("SCRIPT_DIVERSITY_HISTORY_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return []
    local_candidates = [
        Path(os.getenv("SCRIPT_DIVERSITY_HISTORY_FILE", "")),
        FINAL_METADATA_FILE,
    ]
    for path in local_candidates:
        if path and str(path) != "." and path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    items = json.load(f)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)][-limit:]
            except Exception as exc:
                print(f"⚠️ previous metadata history load failed: {path}: {exc}")

    try:
        from shared.utils.config import get_temp_file

        tmp_path = get_temp_file("previous_publish_ready_metadata.json")
        if S3Store().download_file("publish-ready/final_metadata.json", tmp_path):
            with open(tmp_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)][-limit:]
    except Exception as exc:
        print(f"⚠️ previous S3 metadata history unavailable: {exc}")
    return []


def generate_scripts_from_filtered():
    if not VIABLE_POSTS_FILE.exists():
        print("❌ viable_posts.json이 없습니다.")
        return

    with open(VIABLE_POSTS_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)

    metadata_list = []
    failed_items = []
    llm_unavailable_reason = None
    previous_history = _load_previous_accepted_metadata()

    for idx, post in enumerate(posts):
        title = post.get("title", "")
        content = post.get("content", "")
        origin_id = post.get("id", None)
        regenerate_reason = None
        try_count = 0
        max_retries = _max_llm_drafts_per_source() - 1
        preflight_error = _source_preflight_error(post)
        if preflight_error:
            failed_items.append({"idx": idx, "id": origin_id, "title": title, "error": preflight_error, "stage": "source_preflight"})
            print(f"🚫 원문 품질 미달로 스킵 (post {idx}): {preflight_error}")
            continue
        if llm_unavailable_reason and _local_fallback_enabled():
            try:
                metadata = _build_local_fallback_metadata(post, llm_unavailable_reason)
                _accept_metadata(metadata, metadata_list, previous_history)
                print(f"🧩 로컬 fallback 대본 생성 완료 (post {idx}): {origin_id}")
            except Exception as fallback_error:
                failed_items.append({"idx": idx, "id": origin_id, "title": title, "error": str(fallback_error), "stage": "script_generation"})
                print(f"🚫 로컬 fallback 실패 (post {idx}): {fallback_error}")
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
                metadata["generation_attempt_count"] = try_count + 1
                metadata["llm_draft_count"] = try_count + 1
                metadata["failure_action"] = ""
                metadata["final_failure_codes"] = []

                _accept_metadata(metadata, metadata_list, previous_history)
                break  # 성공 시 루프 종료

            except Exception as e:
                char_count = None
                if isinstance(result, ReturnScript):
                    char_count = len(script_text(result.model_dump()))
                    print(f"⚠️ GPT 응답 검증 실패 (post {idx}, 시도 {try_count+1}, script_chars={char_count}): {e}")
                msg = str(e)
                if _is_llm_quota_error(msg) and _local_fallback_enabled():
                    llm_unavailable_reason = msg
                    try:
                        metadata = _build_local_fallback_metadata(post, msg)
                        _accept_metadata(metadata, metadata_list, previous_history)
                        print(f"🧩 OpenAI quota 오류로 로컬 fallback 대본 생성 완료 (post {idx}): {origin_id}")
                        break
                    except Exception as fallback_error:
                        failed_items.append({"idx": idx, "id": origin_id, "title": title, "error": str(fallback_error), "stage": "script_generation"})
                        print(f"🚫 로컬 fallback 실패 (post {idx}): {fallback_error}")
                        break
                failure_action = classify_failure(msg, script_chars=char_count, repeated=try_count >= max_retries)
                if failure_action in {FailureAction.REPAIR_ONLY, FailureAction.SKIP_SOURCE}:
                    failed_items.append(
                        {
                            "idx": idx,
                            "id": origin_id,
                            "title": title,
                            "error": msg,
                            "stage": "script_generation",
                            "generation_attempt_count": try_count + 1,
                            "failure_action": failure_action.value,
                        }
                    )
                    print(f"🚫 repair-first 스킵 (post {idx}): action={failure_action.value} error={msg}")
                    break

                regenerate_reason = _regenerate_reason_from_error(msg)

                try_count += 1
                if try_count > max_retries:
                    failed_items.append(
                        {
                            "idx": idx,
                            "id": origin_id,
                            "title": title,
                            "error": msg,
                            "stage": "script_generation",
                            "generation_attempt_count": try_count,
                            "failure_action": FailureAction.SKIP_SOURCE.value,
                        }
                    )
                    print(f"🚫 최종 실패 (post {idx}): {msg}")

    with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata_list, f, ensure_ascii=False, indent=2)

    if failed_items:
        with open(FAILED_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(failed_items, f, ensure_ascii=False, indent=2)
        print(f"⚠️ 실패한 포스트 {len(failed_items)}개 → {FAILED_POSTS_FILE}에 저장됨")

    summary = _generation_summary(posts, metadata_list, failed_items)
    summary_path = FINAL_METADATA_FILE.with_name("generation_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"🧾 생성 요약 저장 완료 → {summary_path}")
    print(f"📦 최종 메타데이터 저장 완료 → {FINAL_METADATA_FILE}")


def _generation_summary(posts: list[dict], accepted: list[dict], failed: list[dict]) -> dict:
    failure_codes = _failure_code_counts([str(item.get("error") or "") for item in failed])
    return {
        "sources_considered": len(posts),
        "sources_skipped_preflight": sum(1 for item in failed if item.get("stage") == "source_preflight"),
        "llm_drafts": sum(int(item.get("llm_draft_count") or item.get("generation_attempt_count") or 0) for item in accepted)
        + sum(int(item.get("generation_attempt_count") or 0) for item in failed),
        "llm_rewrites": sum(max(0, int(item.get("generation_attempt_count") or 0) - 1) for item in accepted)
        + sum(max(0, int(item.get("generation_attempt_count") or 0) - 1) for item in failed),
        "critic_calls": sum(int(item.get("critic_attempt_count") or 0) for item in accepted),
        "repair_successes": sum(1 for item in accepted if item.get("repair_actions")),
        "final_accepted": len(accepted),
        "final_rejected": len(failed),
        "top_failure_codes": failure_codes[:10],
    }


def _failure_code_counts(errors: list[str]) -> list[dict]:
    counts: dict[str, int] = {}
    known = [
        "title_quality",
        "generic_opening_visual_query",
        "opening_visual_query_mismatch",
        "first_caption_hook",
        "caption_chunks_not_in_tts_text",
        "script가 너무 짧음",
        "script_too_short",
        "weak_market_hook",
        "weak_first_2_seconds",
        "question_not_separate",
        "unsafe_visual_keywords",
        "missing_concrete_receipt_detail",
    ]
    for error in errors:
        for code in known:
            if code in error:
                normalized = "script_too_short" if code == "script가 너무 짧음" else code
                counts[normalized] = counts.get(normalized, 0) + 1
    return [{"code": code, "count": count} for code, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)]


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
            if _is_llm_quota_error(msg) and _local_fallback_enabled():
                try:
                    metadata = _build_local_fallback_metadata(target_post, msg)
                    print(f"🧩 OpenAI quota 오류로 로컬 fallback 재생성 완료 (postId={post_id})")
                    return metadata
                except Exception as fallback_error:
                    print(f"🚫 로컬 fallback 재생성 실패 (postId={post_id}): {fallback_error}")
                    return None
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
