# shared/jobs/generate_scripts_from_filtered.py

import json
import os
import re
import hashlib
import random
from pathlib import Path
from typing import Any, Dict
from generator.text.content_gate import ensure_content_gate, normalize_narration_fields
from generator.text.candidate_scoring import score_candidate
from generator.text.failure_policy import FailureAction, classify_failure, script_repair_min_chars
from generator.text.generate_script import (
    DraftScript,
    NativeViewerCritic,
    ReturnScript,
    _critic_hard_failure,
    apply_critic_to_metadata,
    critique_script,
    draft_to_metadata,
    generate_script,
    GenerateScriptError,
)
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
    script_duration_metrics,
    source_reject_reason_for_marketability,
    validate_script_quality,
)
from generator.text.story_card import build_story_card, story_card_hard_errors
from generator.text.youtube_metadata import apply_youtube_metadata_style
from shared.state import ContentRepository
from shared.llm.circuit_breaker import (
    LlmCircuitOpen,
    assert_llm_circuit_closed,
    is_llm_quota_or_auth_error,
    is_llm_rate_limit_error,
    llm_circuit_is_open,
    llm_circuit_summary,
    open_llm_circuit,
)
from shared.storage import S3Store
from shared.utils.config import VIABLE_POSTS_FILE, FINAL_METADATA_FILE, FAILED_POSTS_FILE

_PERFORMANCE_CONTEXT_CACHE: str | None = None

EXAMPLE_JSON = """
{
        "title": "Neighbor's Tenants Used My Driveway",
        "voice": "male",
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
        "hook_type": "crossed_boundary",
        "style_variant": "neighbor_dispute",
        "turning_point": "The owner next door dismisses the complaint instead of apologizing.",
        "payoff_line": "I told them my property was not free supervision for their renters.",
        "viewer_question": "Would you have shut it down too?",
        "marketability_score": 5,
        "retention_risk": "The source has repeated incidents, so the rewrite compresses them into one camera-alert scene before the neighbor response.",
        "rewrite_notes": "Removed slow vacation-rental context and led with the crossed boundary.",
        "voiceover_lines": [
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
    assert_llm_circuit_closed("script_generation")
    source = build_source_profile(post or {"title": title, "content": content})
    performance_context = _performance_context()
    target_min_chars, target_max_chars = _script_target_window()
    prompt_post = post or {"title": title, "content": content}
    prompt_content = compact_source_for_prompt(prompt_post, _source_prompt_max_chars(content))
    if post is not None:
        post["source_prompt_compacted"] = prompt_content != str(content or "")
        post["source_prompt_char_count"] = len(prompt_content)
    # 2) f-string은 치환이 필요한 부분(제목/본문)만 사용
    parts = [
        "You are adapting a Reddit story into a YouTube Shorts narration.",
        "Outcome: produce a fast, source-faithful, first-person script with strong Shorts retention and structured signals for future performance learning.",
        "Audience: English-speaking Shorts viewers who decide in the first 2 seconds whether to keep watching.",
        "\n[Instructions]",
        "- Return only the DraftScript JSON structure shown in the example below.",
        '- Detect the main character\'s gender from the original story and set `"voice"` to `"male"`, `"female"`, or `"neutral"`.',
        "- Treat the source as a seed story, not a transcript. Preserve the core conflict, relationship type, narrator's decision, consequence, and final moral question.",
        "- You may adapt the source into a more relatable, realistic Shorts story: compress repeated events into one clear scene, add plausible small dialogue, clarify motives, sharpen embarrassment or stakes, and make the conflict feel like something that could happen to a normal person.",
        "- You may improve weak source material by choosing the most relatable angle and making the narrator's dilemma more concrete, as long as the adapted story still belongs to the same conflict archetype.",
        "- Do not invent major unsafe or high-stakes facts: no new crimes, lawsuits, police, violence, sexual content, cheating, medical emergencies, revenge plans, pregnancy, minors, or job loss unless the source clearly supports them.",
        "- Do not change who was in conflict, the broad setting, the narrator's main action, or the final side-taking question.",
        "- Do not output derived mechanical metadata. The code derives captions, TTS text, title styling, descriptions, tags, first-frame text, stock-video queries, and predicted scores after local repair.",
        "- Fill `source_summary` with the original story's core conflict, not the rewritten script.",
        "- Fill `story_beats` with 4 to 7 source-grounded beats: setup, escalation, decision, consequence, and final dilemma.",
        "- Fill `adaptation_strategy` with a transparent note about what you compressed or plausibly dramatized to make the story more watchable.",
        "- Fill `retention_angle` with the specific reason this story is clickable and watchable: boundary crossed, unfair accusation, betrayal, public embarrassment, money/property conflict, workplace/family pressure, or a hard moral split.",
        "- Fill `hook_type` with a short snake_case label such as unfair_accusation, crossed_boundary, money_pressure, public_embarrassment, betrayal, villain_framing, or family_pressure.",
        f"- Fill `style_variant` with one of: {', '.join(STYLE_VARIANTS)}. Choose the most concrete variant for this source, and avoid repeating the same style in a batch.",
        "- Fill `turning_point` with the moment where the situation gets worse, not just a summary.",
        "- Fill `payoff_line` with the final conflict statement before the viewer question.",
        "- Fill `viewer_question` with the exact final comment-bait question. It must be a real question and should not be generic if the source supports a sharper one.",
        "- Fill `marketability_score` from 1 to 5. Use 4 or 5 only when the story has a concrete unfair action, clear stakes, and a debatable final decision.",
        "- Fill `retention_risk` with the main reason viewers might swipe away and how your rewrite prevents it.",
        "- Fill `rewrite_notes` with one short note about what you tightened for retention.",
        "- `title` may be a plain working title that names the concrete conflict. Do not add hashtags.",
        "- Do not use AITA-style public titles. Never start the title with AITA, Am I the Asshole, Am I wrong, or Did I overreact.",
        "- Write in a **casual, conversational tone**, as if you're sharing a story with a friend.",
        "- Avoid formal or stiff language. Use expressions and tones that are commonly seen in successful YouTube Shorts.",
        "- Avoid generic AI-storytelling phrases: acted like I was the problem, the unreasonable one, people are split, half the people, keep the peace, let it go, crossed a boundary, the situation, the issue, the conflict, the drama, what changed everything, that was when, instead of owning it, I decided to stand my ground, I set a boundary, I held the boundary, The proof was clear, What made it worse was.",
        "- Prefer concrete receipts and actions over abstract labels. Each accepted script needs at least four source-grounded details such as a specific object, place, message, receipt, bill, camera, app, photo, timestamp, money amount, count, or exact action someone took.",
        "- The first sentence must be a strong hook with a concrete crossed line. Start with what someone did wrong, what it cost, or why the narrator looked like the villain. Do not start with age, backstory, relationship length, 'So, get this', or 'A little backstory'.",
        "- The first 3 voiceover lines must follow this rhythm: hook result, quick context, then unexpected escalation. Do not explain every detail chronologically.",
        "- Every voiceover line should either add a new problem, raise the stakes, or move toward the final decision. Cut neutral reflection.",
        "- Keep the pacing fast. Remove filler, repeated setup, and slow explanations. The narration should still be understandable after a moderate speed-up.",
        "- Structure the story in `voiceover_lines` as 7 to 10 complete short lines.",
        f"- The joined `voiceover_lines` narration should be {target_min_chars} to {target_max_chars} characters, including spaces.",
        f"- Drafts with {script_repair_min_chars()}-{MIN_SCRIPT_CHARS - 1} characters may be repaired by code with one source-grounded line; do not pad with filler.",
        f"- Anything over {MAX_SCRIPT_CHARS} characters is invalid. Cut harder instead of explaining more.",
        "- Line limits: first line under 120 characters, no voiceover line over 170 characters.",
        "- The final viewer question must be the separate final line.",
        "- Before returning, silently count the joined narration characters and cut until it fits the target window. Do not reveal the count.",
        "- Prefer 120 to 170 spoken words total. Remove repeated history, extra dialogue, and neutral reflection first.",
        "- The target final narration length is roughly 42 to 65 seconds after a moderate speed-up. Prefer concise sentences over long lines.",
        "- The script should never feel stretched, repetitive, or abruptly shortened; keep only the setup, escalation, decision, and question.",
        "- Keep the final line short. Do not pack new facts and the viewer question into one overloaded sentence.",
        "- Do not mention Reddit, JSON, scripts, AI, viewers, or instructions inside the narration.",
        "- End the script with a question or prompt to encourage **viewer engagement**, such as:",
        '  - "So, what do you think?"',
        '  - "Would you have done the same?"',
        "\n[반환 형식 예시]",
        EXAMPLE_JSON,   # ← 안전: f-string 아님
        "\n[IMPORTANT]",
        "- The response **must strictly follow the DraftScript JSON structure** shown above with no missing keys.",
        "- Any syntax or formatting error in the returned JSON will be considered a failure.",
        f"- **If the joined voiceover contains more than {MAX_SCRIPT_CHARS} characters, it's considered invalid.**",
        "\n[Source metadata]",
        f"- Source provider: {source.provider or 'unknown'}",
        f"- Source URL: {source.source_url or 'unknown'}",
        f"- Source length: {source.char_count} chars, {source.word_count} words",
        f"- Source truncation flag: {source.is_truncated} {source.truncation_reason}".strip(),
        f"- Source scorecard: {json.dumps((post or {}).get('source_scorecard') or {}, ensure_ascii=False)}",
        f"- Recent winning patterns: {performance_context}",
        "\n[Original source]",
        f"Title: {title}",
        f"\nContent:\n{prompt_content}",
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
    return max(1, _int_env("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", 1))


def _target_accepted_scripts() -> int:
    return max(1, _int_env("TARGET_ACCEPTED_SCRIPTS", 2))


def _source_draft_limit() -> int:
    return max(_target_accepted_scripts(), _int_env("SCRIPT_SOURCE_DRAFT_LIMIT", 10))


def _near_miss_rewrite_limit() -> int:
    return max(0, _int_env("SCRIPT_NEAR_MISS_REWRITE_LIMIT", 1))


def _backup_accept_score() -> int:
    return max(0, _int_env("CANDIDATE_BACKUP_ACCEPT_SCORE", 70))


def _stop_after_accepted_target() -> bool:
    return _truthy_env("SCRIPT_STOP_AFTER_ACCEPTED_TARGET", "0")


def _calibration_mode() -> bool:
    return _truthy_env("SCRIPT_CALIBRATION_MODE", "0")


def _allow_llm_rewrite_on_narrative_failure() -> bool:
    return _truthy_env("SCRIPT_ALLOW_LLM_REWRITE_ON_NARRATIVE_FAILURE", "0")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _source_prompt_max_chars(content: str) -> int:
    default_limit = _int_env("SCRIPT_SOURCE_MAX_CHARS", 3500)
    long_limit = _int_env("SCRIPT_SOURCE_LONG_POST_MAX_CHARS", 2200)
    if len(str(content or "")) > default_limit:
        return max(800, min(default_limit, long_limit))
    return default_limit


def compact_source_for_prompt(post: dict, max_chars: int) -> str:
    content = str((post or {}).get("content") or "")
    max_chars = max(500, int(max_chars or 3500))
    if len(content) <= max_chars:
        return content

    normalized = re.sub(r"\s+", " ", content).strip()
    leading = normalized[:1200].rsplit(" ", 1)[0].strip()
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", normalized) if part.strip()]
    keyword_terms = (
        "bill",
        "receipt",
        "camera",
        "text",
        "message",
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
    )
    evidence_sentences: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(term in lowered for term in keyword_terms) and sentence not in evidence_sentences:
            evidence_sentences.append(sentence)
        if len(" ".join(evidence_sentences)) > 850:
            break

    question_sentence = ""
    for sentence in reversed(sentences):
        if sentence.endswith("?"):
            question_sentence = sentence
            break
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", content) if part.strip()]
    final_context = question_sentence or (paragraphs[-1] if paragraphs else (sentences[-1] if sentences else ""))
    final_context = re.sub(r"\s+", " ", final_context).strip()
    if len(final_context) > 500:
        final_context = final_context[:500].rsplit(" ", 1)[0].strip()

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
        evidence_text = evidence_text[:500].rsplit(" ", 1)[0].strip()
    final_budget = min(len(final_context), 350)
    final_tail = final_context[-final_budget:] if final_context else ""
    middle = evidence_text
    remaining = max_chars - len(middle) - len(final_tail) - 20
    if remaining <= 0:
        compacted = "\n...\n".join(part for part in (middle, final_tail) if part)
        return compacted[:max_chars].strip()
    head = leading[:remaining].rsplit(" ", 1)[0].strip()
    compacted = "\n...\n".join(part for part in (head, middle, final_tail) if part)
    return compacted[:max_chars].strip()


def validate_and_parse_metadata(result: DraftScript | ReturnScript | dict[str, Any], idx, post) -> Dict[str, Any]:
    try:
        metadata = _metadata_from_generation_result(result)
        metadata = _normalize_minimal_draft(metadata)
        metadata, repair_actions = repair_metadata(metadata, post or {}, stage="pre_gate")
        if repair_actions:
            print(
                f"🛠️ metadata repair applied (post {idx}): "
                f"{', '.join(action.get('code', 'unknown') for action in repair_actions)}"
            )
        metadata = _attach_source_metadata(metadata, post)
        _validate_full_metadata_after_repair(metadata)

        if post and post.get("content"):
            marketability_reject = source_reject_reason_for_marketability(post)
            if marketability_reject:
                raise ValueError(f"❌ source_marketability_reject: {marketability_reject}")

        script_length = len(script_text(metadata))
        duration_metrics = script_duration_metrics(metadata)
        metadata["script_char_count"] = script_length
        metadata["word_count"] = duration_metrics["word_count"]
        metadata["estimated_seconds"] = duration_metrics["estimated_seconds"]
        if script_length > MAX_SCRIPT_CHARS:
            raise ValueError(f"❌ script가 쇼츠 목표보다 너무 긺 (현재 {script_length}자)")

        metadata["visual_keywords"] = _clean_visual_keywords(metadata["visual_keywords"])

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


def _metadata_from_generation_result(result: DraftScript | ReturnScript | dict[str, Any]) -> dict[str, Any]:
    if isinstance(result, DraftScript):
        data = draft_to_metadata(result)
        telemetry = getattr(result, "_generation_telemetry", None)
        if telemetry:
            data["generation_telemetry"] = dict(telemetry)
            data.update({f"llm_{key}": value for key, value in dict(telemetry).items()})
        return data
    if isinstance(result, ReturnScript):
        data = result.model_dump()
        if not data.get("voiceover_lines") and data.get("script"):
            data["voiceover_lines"] = list(data["script"])
        if not str(data.get("style_variant") or "").strip():
            data["style_variant"] = _style_variant_from_hook(data.get("hook_type"))
        if not str(data.get("rewrite_notes") or "").strip():
            data["rewrite_notes"] = "Legacy full metadata normalized into the repair-first flow."
        return data
    return dict(result)


def _style_variant_from_hook(hook_type: str | None) -> str:
    hook = str(hook_type or "").strip().lower()
    mapping = {
        "money_pressure": "money_trap",
        "crossed_boundary": "last_straw",
        "unfair_accusation": "false_blame",
        "family_pressure": "family_pressure",
        "neighbor_dispute": "neighbor_dispute",
    }
    return mapping.get(hook, "last_straw")


def _normalize_minimal_draft(metadata: dict[str, Any]) -> dict[str, Any]:
    required = [
        "voice",
        "voiceover_lines",
        "source_summary",
        "story_beats",
        "adaptation_strategy",
        "retention_angle",
        "turning_point",
        "payoff_line",
        "viewer_question",
        "marketability_score",
        "retention_risk",
        "hook_type",
        "style_variant",
        "rewrite_notes",
    ]
    missing = [key for key in required if key not in metadata]
    if missing:
        raise ValueError(f"❌ minimal draft 필수 키 누락: {', '.join(missing)}")
    if metadata.get("voice") not in {"male", "female", "neutral"}:
        raise ValueError("❌ voice는 male, female, neutral 중 하나여야 함")
    lines = [str(line or "").strip() for line in metadata.get("voiceover_lines") or metadata.get("script") or [] if str(line or "").strip()]
    if not lines:
        raise ValueError("❌ voiceover_lines는 비어 있을 수 없음")
    if not all(isinstance(line, str) for line in lines):
        raise ValueError("❌ voiceover_lines는 문자열 리스트여야 함")
    story_beats = [str(beat or "").strip() for beat in metadata.get("story_beats") or [] if str(beat or "").strip()]
    if len(story_beats) < 4:
        raise ValueError("❌ story_beats는 최소 4개여야 함")
    for key in ("source_summary", "adaptation_strategy", "retention_angle", "turning_point", "payoff_line", "viewer_question", "retention_risk", "hook_type", "style_variant", "rewrite_notes"):
        if not str(metadata.get(key) or "").strip():
            raise ValueError(f"❌ {key}는 비어 있을 수 없음")
    metadata["voiceover_lines"] = lines
    metadata["script"] = list(lines)
    metadata["story_beats"] = story_beats[:7]
    metadata["viewer_question"] = str(metadata["viewer_question"]).strip()
    metadata["marketability_score"] = int(metadata.get("marketability_score") or 0)
    normalize_narration_fields(metadata)
    return metadata


def _attach_source_metadata(metadata: dict[str, Any], post: dict | None) -> dict[str, Any]:
    post = post or {}
    metadata["source_scorecard"] = post.get("source_scorecard") or {}
    metadata["source_score"] = post.get("source_score")
    metadata["source_archetype"] = post.get("source_archetype") or metadata.get("hook_type") or ""
    metadata["source_provider"] = post.get("source_provider", "")
    metadata["source_authenticity"] = post.get("source_authenticity") or metadata["source_provider"] or "unknown"
    metadata["source_collection_path"] = post.get("source_collection_path", "")
    metadata["source_detail_checked"] = bool(post.get("source_detail_checked", False))
    metadata["source_detail_improved"] = bool(post.get("source_detail_improved", False))
    metadata["source_quality_status"] = post.get("source_quality_status", "")
    metadata["source_rejection_reason"] = post.get("source_rejection_reason", "")
    metadata["source_title"] = post.get("title") or metadata.get("source_title") or metadata.get("title") or ""
    metadata["source_content_excerpt"] = str(post.get("content") or "")[:3000]
    metadata["public_title"] = metadata.get("public_title") or metadata.get("title") or metadata["source_title"]
    metadata["source_subreddit"] = post.get("subreddit", "")
    metadata["source_url"] = post.get("source_url", "")
    metadata["source_hash"] = post.get("content_hash", "")
    metadata["source_prompt_compacted"] = bool(post.get("source_prompt_compacted", False))
    metadata["source_prompt_char_count"] = int(post.get("source_prompt_char_count") or 0)
    return metadata


def _validate_full_metadata_after_repair(metadata: dict[str, Any]) -> None:
    required = [
        "title",
        "public_title",
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
        "voiceover_lines",
        "tts_text",
        "caption_chunks",
    ]
    missing = [key for key in required if key not in metadata]
    if missing:
        raise ValueError(f"❌ repair 후 필수 키 누락: {', '.join(missing)}")
    if not isinstance(metadata["script"], list) or not all(isinstance(line, str) for line in metadata["script"]):
        raise ValueError("❌ script는 문자열 리스트여야 함")
    if not isinstance(metadata["visual_keywords"], list) or not all(isinstance(keyword, str) for keyword in metadata["visual_keywords"]):
        raise ValueError("❌ visual_keywords는 문자열 리스트여야 함")
    if not isinstance(metadata["visual_beat_queries"], list) or not all(isinstance(beat, dict) for beat in metadata["visual_beat_queries"]):
        raise ValueError("❌ visual_beat_queries는 객체 리스트여야 함")
    if not all(isinstance(beat.get("beat"), str) and isinstance(beat.get("query"), str) for beat in metadata["visual_beat_queries"]):
        raise ValueError("❌ visual_beat_queries 항목은 beat/query 문자열을 포함해야 함")
    if not isinstance(metadata["caption_chunks"], list) or not all(isinstance(chunk, str) for chunk in metadata["caption_chunks"]):
        raise ValueError("❌ caption_chunks는 문자열 리스트여야 함")
    if metadata.get("bg_strategy") not in {"story", "asmr", "hybrid"}:
        raise ValueError("❌ bg_strategy는 story, asmr, hybrid 중 하나여야 함")
    metadata["first_frame_text"] = _clean_first_frame_text(metadata.get("first_frame_text"))
    metadata["first_2_seconds"] = _clean_short_hook_text(metadata.get("first_2_seconds"), max_chars=95)


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


def _truthy_env(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


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
    parsed["render_blocked"] = not _truthy_env("ALLOW_LOCAL_TEMPLATE_RENDER")
    parsed["upload_blocked"] = not _truthy_env("ALLOW_LOCAL_TEMPLATE_UPLOAD")
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
    while len(" ".join(script)) > MAX_SCRIPT_CHARS and len(script) > 7:
        script.pop(-2)
    return script


def _accept_metadata(
    metadata: Dict[str, Any],
    metadata_list: list[Dict[str, Any]],
    previous_history: list[Dict[str, Any]],
) -> None:
    _gate_metadata(metadata, metadata_list, previous_history)
    metadata = score_candidate(metadata, {})
    if metadata.get("candidate_bucket") == "accepted":
        metadata_list.append(metadata)


def _gate_metadata(
    metadata: Dict[str, Any],
    metadata_list: list[Dict[str, Any]],
    previous_history: list[Dict[str, Any]],
) -> None:
    apply_script_fingerprint(metadata)
    ensure_content_gate(metadata, stage="script_accept")
    diversity_issues = batch_diversity_issues(metadata, metadata_list, previous_history)
    if diversity_issues:
        raise ValueError(f"batch_diversity_failed: {diversity_issues_to_reason(diversity_issues)}")


def _finalize_candidate(
    metadata: Dict[str, Any],
    metadata_list: list[Dict[str, Any]],
    previous_history: list[Dict[str, Any]],
    post: dict,
    *,
    append: bool = True,
) -> Dict[str, Any]:
    _gate_metadata(metadata, metadata_list, previous_history)
    if _after_local_gate_critic_enabled():
        run_critic, critic_reason = should_run_critic(metadata, post)
        if run_critic:
            if _truthy_env("SCRIPT_CRITIC_ALWAYS"):
                metadata["critic_policy"] = "forced"
            elif critic_reason == "sample_rate":
                metadata["critic_policy"] = "sampled_strong_candidate"
            else:
                metadata["critic_policy"] = "run_borderline_candidate"
            metadata["critic_skipped_reason"] = ""
            metadata["critic_policy_reason"] = critic_reason
            metadata = _run_after_local_gate_critic(metadata, post)
            _gate_metadata(metadata, metadata_list, previous_history)
        else:
            metadata["critic_policy"] = "skipped_strong_candidate"
            metadata["critic_skipped_reason"] = critic_reason
    metadata = score_candidate(metadata, post)
    if append and metadata.get("candidate_bucket") == "accepted":
        metadata_list.append(metadata)
    return metadata


def _after_local_gate_critic_enabled() -> bool:
    if os.getenv("SCRIPT_CRITIC_STAGE", "after_local_gate").strip().lower() != "after_local_gate":
        return False
    return os.getenv("SCRIPT_CRITIC_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def should_run_critic(metadata: dict, post: dict) -> tuple[bool, str]:
    if _truthy_env("SCRIPT_CRITIC_ALWAYS"):
        return True, "SCRIPT_CRITIC_ALWAYS=1"
    quality_warnings = metadata.get("quality_warnings") or []
    if quality_warnings:
        return True, "quality_warnings_present"
    if metadata.get("length_repair_status"):
        return True, "length_repair_status_present"
    if int(metadata.get("script_char_count") or 0) < 700:
        return True, "script_under_700_chars"
    if int(metadata.get("marketability_score") or 0) < 5:
        return True, "marketability_below_5"
    source_priority = _float_value((post or {}).get("source_priority_score", metadata.get("source_priority_score")), 0.0)
    if source_priority < 4.4:
        return True, "source_priority_below_4_4"
    if int(metadata.get("predicted_ai_smell_score") or 0) > 2:
        return True, "predicted_ai_smell_above_2"
    repair_codes = {
        str(action.get("code") or "")
        for action in metadata.get("repair_actions") or []
        if isinstance(action, dict)
    }
    if repair_codes & {"length_repair_line_added", "retention_angle_rebuilt"}:
        return True, "high_risk_repair_action"
    mechanical = {
        "caption_chunks_rebuilt",
        "first_frame_text_rebuilt",
        "metadata_defaults_filled",
        "opening_visual_query_rebuilt",
        "public_title_rebuilt",
    }
    if repair_codes and not repair_codes <= mechanical:
        return True, "non_mechanical_repair_action"
    script_chars = int(metadata.get("script_char_count") or 0)
    if 700 <= script_chars <= 950 and int(metadata.get("marketability_score") or 0) == 5:
        if _sample_strong_candidate_for_critic(metadata, post):
            return True, "sample_rate"
        return False, "strong_candidate_passed_deterministic_gates"
    return True, "borderline_candidate"


def _sample_strong_candidate_for_critic(metadata: dict, post: dict) -> bool:
    rate = max(0.0, min(1.0, _float_env("SCRIPT_CRITIC_SAMPLE_RATE", 0.0)))
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    seed = os.getenv("SCRIPT_CRITIC_SAMPLE_SEED", "").strip()
    if seed:
        stable_key = "|".join(
            [
                seed,
                str((post or {}).get("id") or ""),
                str(metadata.get("script_fingerprint") or ""),
                str(metadata.get("public_title") or metadata.get("title") or ""),
            ]
        )
        digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()
        value = int(digest[:16], 16) / float(0xFFFFFFFFFFFFFFFF)
        return value < rate
    return random.random() < rate


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _run_after_local_gate_critic(metadata: Dict[str, Any], post: dict) -> Dict[str, Any]:
    metadata["critic_stage"] = "after_local_gate"
    assert_llm_circuit_closed("script_critic")
    try:
        critic = critique_script(_source_prompt_for_critic(post), metadata)
    except Exception as exc:
        if is_llm_quota_or_auth_error(exc) or is_llm_rate_limit_error(exc):
            open_llm_circuit(str(exc), "script_critic")
        raise
    failure = _critic_hard_failure(critic)
    if failure:
        metadata["critic_attempt_count"] = int(metadata.get("critic_attempt_count") or 0) + 1
        metadata["critic_scores"] = {
            "ai_smell_score": critic.ai_smell_score,
            "native_naturalness_score": critic.native_naturalness_score,
            "retention_score": critic.retention_score,
            "specificity_score": critic.specificity_score,
            "hook_score": critic.hook_score,
            "payoff_score": critic.payoff_score,
            "comment_potential_score": critic.comment_potential_score,
        }
        metadata["critic_problems"] = list(critic.problems or [])
        metadata["critic_rewrite_instructions"] = list(critic.rewrite_instructions or [])
        metadata["critic_passed"] = False
        metadata["critic_failure_reason"] = failure
        return metadata
    return apply_critic_to_metadata(metadata, critic)


def _source_prompt_for_critic(post: dict) -> str:
    source = build_source_profile(post)
    return "\n".join(
        [
            "[Source metadata]",
            f"- Source provider: {source.provider or 'unknown'}",
            f"- Source URL: {source.source_url or 'unknown'}",
            f"- Source length: {source.char_count} chars, {source.word_count} words",
            "[Original source]",
            f"Title: {post.get('title', '')}",
            f"Content:\n{post.get('content', '')}",
        ]
    )


def _repair_only_retry(
    metadata: Dict[str, Any],
    post: dict,
    metadata_list: list[Dict[str, Any]],
    previous_history: list[Dict[str, Any]],
    *,
    append: bool = True,
) -> tuple[Dict[str, Any] | None, str]:
    retry_metadata, repair_actions = repair_metadata(metadata, post, stage="repair_only_retry")
    retry_metadata["repair_only_retry_attempted"] = True
    retry_metadata["repair_only_retry_passed"] = False
    try:
        retry_metadata = _attach_source_metadata(retry_metadata, post)
        _validate_full_metadata_after_repair(retry_metadata)
        script_length = len(script_text(retry_metadata))
        duration_metrics = script_duration_metrics(retry_metadata)
        retry_metadata["word_count"] = duration_metrics["word_count"]
        retry_metadata["estimated_seconds"] = duration_metrics["estimated_seconds"]
        if script_length > MAX_SCRIPT_CHARS:
            raise ValueError(f"❌ script가 쇼츠 목표보다 너무 긺 (현재 {script_length}자)")
        retry_metadata["visual_keywords"] = _clean_visual_keywords(retry_metadata["visual_keywords"])
        retry_metadata["script_char_count"] = script_length
        if post and post.get("content"):
            quality_issues = validate_script_quality(retry_metadata, post)
            hard_errors = hard_quality_errors(quality_issues)
            if hard_errors:
                raise ValueError(f"❌ 품질검증 실패: {quality_issues_to_regenerate_reason(hard_errors)}")
            retry_metadata["quality_warnings"] = [
                {"code": issue.code, "message": issue.message}
                for issue in quality_issues
                if not issue.hard
            ]
        apply_youtube_metadata_style(retry_metadata)
        apply_script_fingerprint(retry_metadata)
        retry_metadata = _finalize_candidate(retry_metadata, metadata_list, previous_history, post, append=append)
        retry_metadata["repair_only_retry_passed"] = True
        return retry_metadata, ""
    except Exception as exc:
        return None, str(exc)


def _script_chars_from_result(result: DraftScript | ReturnScript | None) -> int | None:
    if result is None:
        return None
    if isinstance(result, DraftScript):
        return len(script_text(draft_to_metadata(result)))
    return len(script_text(result.model_dump()))


def _generation_telemetry_from_result(result: DraftScript | ReturnScript | None) -> dict[str, Any]:
    if result is not None:
        telemetry = getattr(result, "_generation_telemetry", None)
        if telemetry:
            return dict(telemetry)
    return {}


def _failure_record(
    *,
    idx: Any,
    origin_id: Any,
    title: str,
    error: str,
    stage: str,
    generation_attempt_count: int | None = None,
    failure_action: str | None = None,
    result: DraftScript | ReturnScript | None = None,
    metadata: dict | None = None,
    generation_telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "idx": idx,
        "id": origin_id,
        "title": title,
        "error": error,
        "stage": stage,
    }
    category = _failure_category(error, stage=stage)
    record["failure_category"] = category
    if generation_attempt_count is not None:
        record["generation_attempt_count"] = generation_attempt_count
        record["llm_draft_count"] = generation_attempt_count
    if failure_action:
        record["failure_action"] = failure_action
    elif category in {"quota_or_auth", "rate_limit"}:
        record["failure_action"] = "circuit_breaker"
    telemetry = dict(generation_telemetry or {}) or _generation_telemetry_from_result(result)
    if telemetry:
        record["generation_telemetry"] = telemetry
        record.update({f"llm_{key}": value for key, value in telemetry.items()})
    if metadata:
        for key in (
            "critic_attempt_count",
            "critic_passed",
            "critic_policy",
            "critic_skipped_reason",
            "prior_critic_attempt_count",
            "prior_critic_failed_count",
            "repair_only_retry_attempted",
            "repair_only_retry_passed",
        ):
            if key in metadata:
                record[key] = metadata[key]
        record["repair_attempt_count"] = int(bool(metadata.get("repair_only_retry_attempted"))) + int(bool(metadata.get("repair_actions")))
        record["repair_actions"] = list(metadata.get("repair_actions") or [])[:12]
        record["final_gate_errors"] = _gate_errors_from_message(error)
    else:
        record["repair_attempt_count"] = 0
        record["repair_actions"] = []
        record["final_gate_errors"] = _gate_errors_from_message(error)
    return record


def _failure_category(error: str, *, stage: str = "") -> str:
    lowered = str(error or "").lower()
    if any(term in lowered for term in ("insufficient_quota", "quota", "invalid_api_key", "api key", "auth", "permission", "llm_circuit_open")):
        return "quota_or_auth"
    if "rate_limit" in lowered or "rate limit" in lowered or "429" in lowered:
        return "rate_limit"
    if stage == "source_preflight" or any(term in lowered for term in ("source_too_thin", "source_truncated", "source_marketability_reject")):
        return "source_unsuitable"
    if "native_viewer_critic_failed" in lowered:
        return "critic_failed"
    if "batch_diversity_failed" in lowered:
        return "diversity_reject"
    if any(term in lowered for term in ("script가 너무 짧", "script_too_short", "length_repair")):
        return "length_repair_failed"
    if any(term in lowered for term in ("schema", "validation", "output_parsed", "token", "json", "required", "missing key")):
        return "schema_or_token"
    metadata_terms = (
        "title_quality",
        "opening_visual_query",
        "first_caption_hook",
        "caption_chunks",
        "missing_script_fingerprint",
        "weak_retention_angle",
        "weak_viewer_question",
        "missing_first_frame_text",
        "first_frame_text_too_long",
        "missing_concrete_receipt_detail",
    )
    if any(term in lowered for term in metadata_terms):
        return "metadata_repair_failed"
    if any(term in lowered for term in ("weak_market_hook", "generic_reusable_line", "abstract_language_overload", "missing_concrete_details", "low_source_overlap")):
        return "narrative_weak"
    return "metadata_repair_failed" if "content_gate_failed" in lowered else "narrative_weak"


def _gate_errors_from_message(error: str) -> list[str]:
    text = str(error or "")
    matches = re.findall(r"(?:content_gate_failed:[^:]+:)?([a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*)?)", text)
    ignored = {"post", "error", "failed", "script_generation", "source_preflight"}
    errors = []
    for match in matches:
        code = match.strip(":")
        if code in ignored or len(code) < 4:
            continue
        if code not in errors:
            errors.append(code)
    return errors[:10]


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
    candidate_pool: list[Dict[str, Any]] = []
    failed_items = []
    llm_unavailable_reason = None
    previous_history = _load_previous_accepted_metadata()
    drafted_count = 0

    for idx, post in enumerate(posts):
        if _stop_after_accepted_target() and _accepted_candidate_count(candidate_pool) >= _target_accepted_scripts():
            print(f"✅ target accepted scripts reached ({_accepted_candidate_count(candidate_pool)}/{_target_accepted_scripts()}); stopping script generation")
            break
        if drafted_count >= _source_draft_limit():
            print(f"✅ source draft limit reached ({drafted_count}/{_source_draft_limit()}); stopping script generation")
            break
        title = post.get("title", "")
        content = post.get("content", "")
        origin_id = post.get("id", None)
        regenerate_reason = None
        try_count = 0
        prior_critic_attempt_count = 0
        prior_critic_failed_count = 0
        max_retries = _max_llm_drafts_per_source() - 1
        if llm_circuit_is_open():
            failed_items.append(
                _failure_record(
                    idx=idx,
                    origin_id=origin_id,
                    title=title,
                    error=llm_circuit_summary().get("llm_circuit_reason") or "llm_circuit_open",
                    stage="script_generation",
                    generation_attempt_count=0,
                    failure_action="circuit_breaker",
                )
            )
            print(f"🚫 LLM circuit open; skipping post {idx} without LLM call")
            continue
        preflight_error = _source_preflight_error(post)
        if preflight_error:
            failed_items.append(_failure_record(idx=idx, origin_id=origin_id, title=title, error=preflight_error, stage="source_preflight"))
            print(f"🚫 원문 품질 미달로 스킵 (post {idx}): {preflight_error}")
            continue
        story_card = build_story_card(post)
        post["story_card"] = story_card.model_dump()
        story_card_errors = story_card_hard_errors(story_card)
        if story_card_errors:
            failed_items.append(
                _failure_record(
                    idx=idx,
                    origin_id=origin_id,
                    title=title,
                    error="; ".join(story_card_errors),
                    stage="story_card",
                    failure_action=FailureAction.SKIP_SOURCE.value,
                    metadata={"story_card": story_card.model_dump(), "final_gate_errors": story_card_errors},
                )
            )
            print(f"🚫 StoryCard gate failed (post {idx}): {story_card_errors}")
            continue
        if llm_unavailable_reason and _local_fallback_enabled():
            fallback_metadata = None
            try:
                fallback_metadata = _build_local_fallback_metadata(post, llm_unavailable_reason)
                fallback_metadata = _ensure_candidate_scored(
                    _finalize_candidate(fallback_metadata, candidate_pool, previous_history, post, append=False),
                    post,
                )
                candidate_pool.append(fallback_metadata)
                print(f"🧩 로컬 fallback 대본 생성 완료 (post {idx}): {origin_id}")
            except Exception as fallback_error:
                failed_items.append(_failure_record(idx=idx, origin_id=origin_id, title=title, error=str(fallback_error), stage="script_generation", metadata=fallback_metadata))
                print(f"🚫 로컬 fallback 실패 (post {idx}): {fallback_error}")
            continue

        while try_count <= max_retries:
            result = None
            metadata = None
            generation_error_telemetry: dict[str, Any] = {}
            try:
                drafted_count += 1
                if try_count == 0:
                    result: DraftScript = call_gpt_generate_script(title, content, post=post)
                else:
                    result: DraftScript = call_gpt_generate_script(
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
                metadata["source_post_index"] = idx
                metadata["story_card"] = story_card.model_dump()
                metadata["story_card_status"] = "accepted"
                metadata["generation_attempt_count"] = try_count + 1
                metadata["llm_draft_count"] = try_count + 1
                metadata["failure_action"] = ""
                metadata["final_failure_codes"] = []
                if prior_critic_attempt_count:
                    metadata["prior_critic_attempt_count"] = prior_critic_attempt_count
                    metadata["prior_critic_failed_count"] = prior_critic_failed_count

                metadata = _ensure_candidate_scored(
                    _finalize_candidate(metadata, candidate_pool, previous_history, post, append=False),
                    post,
                )
                candidate_pool.append(metadata)
                break  # 성공 시 루프 종료

            except Exception as e:
                if isinstance(e, GenerateScriptError):
                    generation_error_telemetry = dict(e.telemetry or {})
                char_count = _script_chars_from_result(result)
                if result is not None:
                    print(f"⚠️ GPT 응답 검증 실패 (post {idx}, 시도 {try_count+1}, script_chars={char_count}): {e}")
                msg = str(e)
                if metadata and metadata.get("critic_passed") is False:
                    prior_critic_attempt_count += int(metadata.get("critic_attempt_count") or 0)
                    prior_critic_failed_count += 1
                if _is_llm_quota_error(msg):
                    open_llm_circuit(msg, "script_generation")
                if _is_llm_quota_error(msg) and _local_fallback_enabled():
                    llm_unavailable_reason = msg
                    try:
                        metadata = _build_local_fallback_metadata(post, msg)
                        metadata = _ensure_candidate_scored(
                            _finalize_candidate(metadata, candidate_pool, previous_history, post, append=False),
                            post,
                        )
                        candidate_pool.append(metadata)
                        print(f"🧩 OpenAI quota 오류로 로컬 fallback 대본 생성 완료 (post {idx}): {origin_id}")
                        break
                    except Exception as fallback_error:
                        failed_items.append(_failure_record(idx=idx, origin_id=origin_id, title=title, error=str(fallback_error), stage="script_generation", result=result, metadata=metadata, generation_telemetry=generation_error_telemetry))
                        print(f"🚫 로컬 fallback 실패 (post {idx}): {fallback_error}")
                        break
                if _is_llm_quota_error(msg):
                    failed_items.append(
                        _failure_record(
                            idx=idx,
                            origin_id=origin_id,
                            title=title,
                            error=msg,
                            stage="script_generation",
                            generation_attempt_count=try_count + 1,
                            failure_action="circuit_breaker",
                            result=result,
                            metadata=metadata,
                            generation_telemetry=generation_error_telemetry,
                        )
                    )
                    print(f"🚫 LLM circuit opened during script generation (post {idx}): {msg}")
                    break
                failure_action = classify_failure(msg, script_chars=char_count, repeated=try_count >= max_retries)
                if failure_action == FailureAction.LLM_REWRITE_ONCE and not _allow_llm_rewrite_on_narrative_failure():
                    failure_action = FailureAction.SKIP_SOURCE
                if failure_action == FailureAction.REPAIR_ONLY and metadata is not None:
                    retry_metadata, retry_error = _repair_only_retry(metadata, post, candidate_pool, previous_history, append=False)
                    if retry_metadata is not None:
                        retry_metadata = _ensure_candidate_scored(retry_metadata, post)
                        retry_metadata["source_post_index"] = idx
                        retry_metadata["story_card"] = story_card.model_dump()
                        retry_metadata["story_card_status"] = "accepted"
                        candidate_pool.append(retry_metadata)
                        print(f"🛠️ repair-only retry accepted (post {idx}): {origin_id}")
                        break
                    msg = retry_error or msg
                    print(f"🚫 repair-only retry failed (post {idx}): {msg}")
                if failure_action in {FailureAction.REPAIR_ONLY, FailureAction.SKIP_SOURCE}:
                    if metadata and prior_critic_attempt_count:
                        metadata["prior_critic_attempt_count"] = prior_critic_attempt_count
                        metadata["prior_critic_failed_count"] = prior_critic_failed_count
                    failed_items.append(
                        _failure_record(
                            idx=idx,
                            origin_id=origin_id,
                            title=title,
                            error=msg,
                            stage="script_generation",
                            generation_attempt_count=try_count + 1,
                            failure_action=failure_action.value,
                            result=result,
                            metadata=metadata,
                            generation_telemetry=generation_error_telemetry,
                        )
                    )
                    print(f"🚫 repair-first 스킵 (post {idx}): action={failure_action.value} error={msg}")
                    break

                regenerate_reason = _regenerate_reason_from_error(msg)

                try_count += 1
                if try_count > max_retries:
                    if metadata and prior_critic_attempt_count:
                        metadata["prior_critic_attempt_count"] = prior_critic_attempt_count
                        metadata["prior_critic_failed_count"] = prior_critic_failed_count
                    failed_items.append(
                        _failure_record(
                            idx=idx,
                            origin_id=origin_id,
                            title=title,
                            error=msg,
                            stage="script_generation",
                            generation_attempt_count=try_count,
                            failure_action=FailureAction.SKIP_SOURCE.value,
                            result=result,
                            metadata=metadata,
                            generation_telemetry=generation_error_telemetry,
                        )
                    )
                    print(f"🚫 최종 실패 (post {idx}): {msg}")

    _rewrite_near_miss_candidates(candidate_pool, posts, previous_history)
    metadata_list = _select_final_candidates(candidate_pool)

    dry_run = _truthy_env("SCRIPT_DRY_RUN_SUMMARY_ONLY") or _calibration_mode()
    if dry_run:
        for item in metadata_list:
            item["dry_run"] = True
    if _calibration_mode():
        metadata_list = []

    with open(FINAL_METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata_list, f, ensure_ascii=False, indent=2)

    if failed_items:
        with open(FAILED_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(failed_items, f, ensure_ascii=False, indent=2)
        print(f"⚠️ 실패한 포스트 {len(failed_items)}개 → {FAILED_POSTS_FILE}에 저장됨")

    _write_candidate_reports(candidate_pool, failed_items, posts)
    summary = _generation_summary(posts, metadata_list, failed_items, candidate_pool=candidate_pool)
    if dry_run:
        summary["dry_run"] = True
    if _calibration_mode():
        summary["calibration_mode"] = True
    summary_path = FINAL_METADATA_FILE.with_name("generation_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    if dry_run:
        _print_dry_run_summary(summary)
    print(f"🧾 생성 요약 저장 완료 → {summary_path}")
    print(f"📦 최종 메타데이터 저장 완료 → {FINAL_METADATA_FILE}")


def _print_dry_run_summary(summary: dict) -> None:
    top_failures = ",".join(str(item.get("code") or "") for item in summary.get("top_failure_codes", [])[:5])
    print(
        "\n".join(
            [
                "DRY RUN SUMMARY",
                f"raw={summary.get('sources_considered', 0)}",
                f"scorecard_calls={summary.get('source_scorecard_calls', 0)}",
                f"draft_calls={summary.get('llm_drafts', 0)}",
                f"json_fallback={summary.get('json_fallback_attempts', 0)}",
                f"critic_calls={summary.get('critic_calls_attempted', 0)}",
                f"critic_skipped={summary.get('critic_skipped', 0)}",
                f"repair_successes={summary.get('repair_successes', 0)}",
                f"accepted={summary.get('final_accepted', 0)}",
                f"rejected={summary.get('final_rejected', 0)}",
                f"estimated_output_token_budget={summary.get('estimated_output_token_budget_total', 0)}",
                f"top_failures=[{top_failures}]",
            ]
        )
    )


def _select_final_candidates(candidate_pool: list[dict]) -> list[dict]:
    target = _target_accepted_scripts()
    accepted = [item for item in candidate_pool if item.get("candidate_bucket") == "accepted" and not item.get("hard_blockers")]
    accepted.sort(key=lambda item: int(item.get("candidate_score") or 0), reverse=True)
    selected = accepted[:target]
    selected_ids = {str(item.get("id") or id(item)) for item in selected}

    if len(selected) < target:
        backup_candidates = [
            item
            for item in candidate_pool
            if item.get("candidate_bucket") == "near_miss"
            and not item.get("hard_blockers")
            and int(item.get("candidate_score") or 0) >= _backup_accept_score()
            and str(item.get("id") or id(item)) not in selected_ids
        ]
        backup_candidates.sort(key=lambda item: int(item.get("candidate_score") or 0), reverse=True)
        for item in backup_candidates[: max(0, target - len(selected))]:
            item["selected_as_backup_candidate"] = True
            item["candidate_selection_reason"] = "near_miss_backup_above_floor"
            selected.append(item)
            selected_ids.add(str(item.get("id") or id(item)))

    for item in selected:
        item["selected_for_final"] = True
    return selected


def _accepted_candidate_count(candidate_pool: list[dict]) -> int:
    return sum(1 for item in candidate_pool if item.get("candidate_bucket") == "accepted" and not item.get("hard_blockers"))


def _ensure_candidate_scored(metadata: Dict[str, Any], post: dict) -> Dict[str, Any]:
    if not metadata.get("candidate_bucket") or "candidate_score" not in metadata:
        return score_candidate(metadata, post)
    return metadata


def _rewrite_near_miss_candidates(
    candidate_pool: list[Dict[str, Any]],
    posts: list[dict],
    previous_history: list[Dict[str, Any]],
) -> None:
    accepted_count = sum(1 for item in candidate_pool if item.get("candidate_bucket") == "accepted")
    needed = max(0, _target_accepted_scripts() - accepted_count)
    if needed <= 0 or _near_miss_rewrite_limit() <= 0 or llm_circuit_is_open():
        return
    near_misses = [item for item in candidate_pool if item.get("candidate_bucket") == "near_miss"]
    near_misses.sort(key=lambda item: int(item.get("candidate_score") or 0), reverse=True)
    backup_ready = [
        item
        for item in near_misses
        if not item.get("hard_blockers") and int(item.get("candidate_score") or 0) >= _backup_accept_score()
    ]
    if accepted_count + len(backup_ready) >= _target_accepted_scripts():
        print(
            "✅ near-miss rewrite skipped: enough backup candidates "
            f"accepted={accepted_count} backups={len(backup_ready)} target={_target_accepted_scripts()}"
        )
        return
    post_by_id = {str(post.get("id")): post for post in posts}
    rewrites = 0
    for candidate in near_misses:
        if rewrites >= _near_miss_rewrite_limit() or needed <= 0 or llm_circuit_is_open():
            break
        source_id = str(candidate.get("id") or "")
        post = post_by_id.get(source_id)
        if not post:
            continue
        try:
            reason = _near_miss_rewrite_reason(candidate)
            result = call_gpt_generate_script(post.get("title", ""), post.get("content", ""), post=post, regenerate_reason=reason)
            metadata = validate_and_parse_metadata(result, f"near_miss={source_id}", post)
            metadata["id"] = source_id
            metadata["uploaded"] = False
            metadata["near_miss_rewrite"] = True
            metadata["near_miss_rewrite_reason"] = reason
            metadata["llm_draft_count"] = int(candidate.get("llm_draft_count") or 1) + 1
            metadata["generation_attempt_count"] = metadata["llm_draft_count"]
            metadata["story_card"] = (post.get("story_card") or {})
            comparison_pool = [item for item in candidate_pool if str(item.get("id") or "") != source_id]
            metadata = _ensure_candidate_scored(
                _finalize_candidate(metadata, comparison_pool, previous_history, post, append=False),
                post,
            )
            candidate_pool.append(metadata)
            rewrites += 1
            if metadata.get("candidate_bucket") == "accepted":
                needed -= 1
        except Exception as exc:
            rewrites += 1
            print(f"🚫 near-miss rewrite failed id={source_id}: {exc}")


def _near_miss_rewrite_reason(candidate: dict) -> str:
    issues = candidate.get("soft_issues") or []
    if any("hook" in str(issue) for issue in issues):
        return "Rewrite the opening hook to be more concrete and immediate while keeping the same source facts."
    if any("receipt" in str(issue) for issue in issues):
        return "Bring the concrete receipt, proof, bill, text, or camera detail into the first half of the narration."
    if any("duration" in str(issue) or "words" in str(issue) for issue in issues):
        return "Tighten pacing but add one concrete source-grounded beat so the Short has enough spoken substance."
    return "Improve the narration from near-miss to accepted by making the conflict more concrete, specific, and comment-worthy."


def _write_candidate_reports(candidate_pool: list[dict], failed: list[dict], posts: list[dict]) -> None:
    candidate_scores_path = FINAL_METADATA_FILE.with_name("candidate_scores.json")
    near_miss_path = FINAL_METADATA_FILE.with_name("near_miss_candidates.json")
    gate_distribution_path = FINAL_METADATA_FILE.with_name("gate_distribution.json")
    funnel_path = FINAL_METADATA_FILE.with_name("source_to_acceptance_funnel.json")
    candidates = [_candidate_report_item(item) for item in sorted(candidate_pool, key=lambda item: int(item.get("candidate_score") or 0), reverse=True)]
    near_misses = [
        _near_miss_report_item(item)
        for item in candidate_pool
        if item.get("candidate_bucket") == "near_miss"
    ]
    near_misses.sort(key=lambda item: int(item.get("candidate_score") or 0), reverse=True)
    _write_json(candidate_scores_path, candidates)
    _write_json(near_miss_path, near_misses)
    _write_json(gate_distribution_path, _gate_distribution(candidate_pool, failed))
    _write_json(funnel_path, _funnel_report(posts, candidate_pool, failed))


def _candidate_report_item(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "title": item.get("public_title") or item.get("title"),
        "candidate_score": item.get("candidate_score", 0),
        "candidate_bucket": item.get("candidate_bucket", ""),
        "soft_issues": item.get("soft_issues", []),
        "hard_blockers": item.get("hard_blockers", []),
        "script_char_count": item.get("script_char_count", 0),
        "word_count": item.get("word_count", 0),
        "estimated_seconds": item.get("estimated_seconds", 0),
        "selected_for_final": bool(item.get("selected_for_final")),
    }


def _near_miss_report_item(item: dict) -> dict:
    report = _candidate_report_item(item)
    report["top_reason_not_accepted"] = _top_near_miss_reason(item)
    report["recommended_action"] = _recommended_near_miss_action(item)
    return report


def _top_near_miss_reason(item: dict) -> str:
    if item.get("hard_blockers"):
        return "hard_blocker"
    if int(item.get("candidate_score") or 0) < 78:
        return "score_below_threshold"
    return "not_in_top_k"


def _recommended_near_miss_action(item: dict) -> str:
    issues = [str(issue) for issue in item.get("soft_issues") or []]
    if any("hook" in issue for issue in issues):
        return "rewrite_hook"
    if any("title" in issue for issue in issues):
        return "repair_title"
    if int(item.get("candidate_score") or 0) >= 74:
        return "use_as_backup"
    return "discard_source"


def _gate_distribution(candidate_pool: list[dict], failed: list[dict]) -> dict:
    candidate_buckets: dict[str, int] = {}
    hard_blockers: dict[str, int] = {}
    soft_issues: dict[str, int] = {}
    failed_stages: dict[str, int] = {}
    for item in candidate_pool:
        bucket = str(item.get("candidate_bucket") or "unknown")
        candidate_buckets[bucket] = candidate_buckets.get(bucket, 0) + 1
        for blocker in item.get("hard_blockers") or []:
            code = str(blocker).split(":", 1)[0]
            hard_blockers[code] = hard_blockers.get(code, 0) + 1
        for issue in item.get("soft_issues") or []:
            code = str(issue).split(":", 1)[0]
            soft_issues[code] = soft_issues.get(code, 0) + 1
    for item in failed:
        stage = str(item.get("stage") or "failed")
        failed_stages[stage] = failed_stages.get(stage, 0) + 1
    return {
        "candidate_buckets": candidate_buckets,
        "hard_blockers": hard_blockers,
        "soft_issues": soft_issues,
        "failed_stages": failed_stages,
    }


def _funnel_report(posts: list[dict], candidate_pool: list[dict], failed: list[dict]) -> dict:
    story_card_rejected = sum(1 for item in failed if item.get("stage") == "story_card")
    drafted = len(candidate_pool) + sum(1 for item in failed if item.get("stage") == "script_generation")
    hard_pass = sum(1 for item in candidate_pool if not item.get("hard_blockers"))
    return {
        "raw": len(posts),
        "local_precheck": len(posts) - sum(1 for item in failed if item.get("stage") == "source_preflight"),
        "source_scorecard": len(posts),
        "story_card": len(posts) - story_card_rejected,
        "draft": drafted,
        "hard_pass": hard_pass,
        "scored": len(candidate_pool),
        "accepted": sum(1 for item in candidate_pool if item.get("candidate_bucket") == "accepted"),
    }


def _write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _generation_summary(posts: list[dict], accepted: list[dict], failed: list[dict], *, candidate_pool: list[dict] | None = None) -> dict:
    candidate_pool = candidate_pool or []
    all_items = (candidate_pool or accepted) + failed
    generated_items = candidate_pool or accepted
    generation_telemetry = _sum_generation_telemetry(all_items)
    source_filter_summary = _load_source_filter_summary()
    critic_attempted = sum(
        int(item.get("critic_attempt_count") or 0) + int(item.get("prior_critic_attempt_count") or 0)
        for item in all_items
    )
    source_scorecard_calls = int(source_filter_summary.get("source_scorecard_calls") or 0)
    source_scorecard_skipped = int(source_filter_summary.get("source_scorecard_skipped_by_prerank") or 0)
    source_scorecard_skipped_by_local_accept = int(
        source_filter_summary.get("source_scorecard_skipped_by_local_accept") or 0
    )
    local_high_confidence_accepted = int(source_filter_summary.get("local_high_confidence_accepted") or 0)
    critic_budget = _int_env("SCRIPT_CRITIC_MAX_OUTPUT_TOKENS", 1400)
    failure_codes = _failure_code_counts([str(item.get("error") or "") for item in failed])
    llm_calls_by_stage = {
        "source_scorecard": source_scorecard_calls,
        "script_draft": generation_telemetry["structured_attempts"],
        "json_fallback": generation_telemetry["json_fallback_attempts"],
        "critic": critic_attempted,
    }
    token_budget = _token_budget_summary(
        all_items,
        generated_items,
        source_scorecard_calls=source_scorecard_calls,
        critic_attempted=critic_attempted,
        critic_budget=critic_budget,
    )
    summary = {
        "sources_considered": len(posts),
        "sources_skipped_preflight": sum(1 for item in failed if item.get("stage") == "source_preflight"),
        "target_accepted_scripts": _target_accepted_scripts(),
        "stopped_after_target": _stop_after_accepted_target() and len(accepted) >= _target_accepted_scripts(),
        "llm_drafts": sum(int(item.get("llm_draft_count") or item.get("generation_attempt_count") or 0) for item in generated_items)
        + sum(int(item.get("generation_attempt_count") or 0) for item in failed),
        "llm_rewrites": sum(max(0, int(item.get("generation_attempt_count") or 0) - 1) for item in generated_items)
        + sum(max(0, int(item.get("generation_attempt_count") or 0) - 1) for item in failed),
        "critic_calls": critic_attempted,
        "critic_calls_attempted": critic_attempted,
        "critic_passed": sum(1 for item in all_items if item.get("critic_passed") is True),
        "critic_failed": sum(1 for item in all_items if item.get("critic_passed") is False)
        + sum(int(item.get("prior_critic_failed_count") or 0) for item in all_items),
        "critic_skipped": sum(1 for item in all_items if item.get("critic_policy") == "skipped_strong_candidate"),
        "repair_successes": sum(1 for item in generated_items if item.get("repair_actions")),
        "repair_only_retries": sum(1 for item in all_items if item.get("repair_only_retry_attempted")),
        "repair_only_retry_successes": sum(1 for item in all_items if item.get("repair_only_retry_passed")),
        "structured_attempts": generation_telemetry["structured_attempts"],
        "json_fallback_attempts": generation_telemetry["json_fallback_attempts"],
        "structured_failures": generation_telemetry["structured_failures"],
        "json_fallback_failures": generation_telemetry["json_fallback_failures"],
        "source_scorecard_calls": source_scorecard_calls,
        "source_scorecard_skipped_by_prerank": source_scorecard_skipped,
        "source_scorecard_skipped_by_local_accept": source_scorecard_skipped_by_local_accept,
        "local_high_confidence_accepted": local_high_confidence_accepted,
        "source_scorecard_skipped_after_quota": int(source_filter_summary.get("source_scorecard_skipped_after_quota") or 0),
        "local_feasibility_rejected": int(source_filter_summary.get("local_feasibility_rejected") or 0),
        "source_filter_stopped_after_target": bool(source_filter_summary.get("source_filter_stopped_after_target")),
        "llm_calls_by_stage": llm_calls_by_stage,
        "llm_call_estimate_total": (
            generation_telemetry["structured_attempts"]
            + generation_telemetry["json_fallback_attempts"]
            + source_scorecard_calls
            + critic_attempted
        ),
        "estimated_output_token_budget_total": token_budget["actual_token_budget"],
        **token_budget,
        "final_accepted": len(accepted),
        "final_rejected": len(failed),
        "candidate_pool_count": len(candidate_pool),
        "near_miss_count": sum(1 for item in candidate_pool if item.get("candidate_bucket") == "near_miss"),
        "backup_selected_count": sum(1 for item in accepted if item.get("selected_as_backup_candidate")),
        "best_near_miss_score": max([int(item.get("candidate_score") or 0) for item in candidate_pool if item.get("candidate_bucket") == "near_miss"] or [0]),
        "best_near_miss_reasons": _best_near_miss_reasons(candidate_pool),
        "top_failure_codes": failure_codes[:10],
        "failure_category_counts": _failure_category_counts(failed),
        "top_gate_errors": _top_gate_errors(failed),
        **llm_circuit_summary(),
    }
    summary["cost_waste_warning"] = bool(summary["final_accepted"] == 0 and summary["llm_call_estimate_total"] > 0)
    summary["operator_recommendation"] = _operator_recommendation(summary)
    return summary


def _token_budget_summary(
    all_items: list[dict],
    generated_items: list[dict],
    *,
    source_scorecard_calls: int,
    critic_attempted: int,
    critic_budget: int,
) -> dict[str, Any]:
    source_scorecard_tokens = source_scorecard_calls * _source_scorecard_token_budget()
    critic_tokens = critic_attempted * max(0, critic_budget)
    actual_by_stage = {
        "source_scorecard": source_scorecard_tokens,
        "initial_script_draft": 0,
        "same_source_retry": 0,
        "structured_retry": 0,
        "json_fallback": 0,
        "near_miss_rewrite": 0,
        "tts_regenerate": 0,
        "critic": critic_tokens,
    }
    overhead_by_stage = {
        "source_scorecard": 0,
        "initial_script_draft": 0,
        "same_source_retry": 0,
        "structured_retry": 0,
        "json_fallback": 0,
        "near_miss_rewrite": 0,
        "tts_regenerate": 0,
        "critic": 0,
    }
    minimum_once = source_scorecard_tokens + critic_tokens
    generated_ids = {id(item) for item in generated_items}
    seen_item_ids: set[int] = set()
    for item in all_items:
        if not isinstance(item, dict):
            continue
        # Count failed records and generated candidates once. When generated_items
        # comes from candidate_pool, all_items already points at the same objects.
        item_identity = id(item)
        if item_identity in seen_item_ids:
            continue
        seen_item_ids.add(item_identity)
        split = _script_token_budget_split(item)
        if not split["actual"]:
            continue
        if _is_near_miss_rewrite_token_item(item):
            actual_by_stage["near_miss_rewrite"] += split["actual"]
            overhead_by_stage["near_miss_rewrite"] += split["actual"]
            continue
        if _is_tts_regenerate_token_item(item):
            actual_by_stage["tts_regenerate"] += split["actual"]
            overhead_by_stage["tts_regenerate"] += split["actual"]
            continue

        actual_by_stage["initial_script_draft"] += split["initial_script_draft"]
        actual_by_stage["same_source_retry"] += split["same_source_retry"]
        actual_by_stage["structured_retry"] += split["structured_retry"]
        actual_by_stage["json_fallback"] += split["json_fallback"]
        overhead_by_stage["same_source_retry"] += split["same_source_retry"]
        overhead_by_stage["structured_retry"] += split["structured_retry"]
        overhead_by_stage["json_fallback"] += split["json_fallback"]
        if item_identity in generated_ids or int(item.get("generation_attempt_count") or item.get("llm_draft_count") or 0) > 0:
            minimum_once += split["initial_script_draft"]

    actual_total = sum(actual_by_stage.values())
    overhead = max(0, actual_total - minimum_once)
    overhead_rate = round(overhead / minimum_once, 4) if minimum_once > 0 else (0.0 if actual_total == 0 else 1.0)
    return {
        "minimum_once_token_budget": int(round(minimum_once)),
        "actual_token_budget": int(round(actual_total)),
        "token_overhead": int(round(overhead)),
        "token_overhead_rate": overhead_rate,
        "token_overhead_target_rate": _token_overhead_target_rate(),
        "token_overhead_status": _token_overhead_status(overhead_rate),
        "actual_token_budget_by_stage": {key: int(round(value)) for key, value in actual_by_stage.items()},
        "token_overhead_by_stage": {key: int(round(value)) for key, value in overhead_by_stage.items()},
    }


def _script_token_budget_split(item: dict) -> dict[str, float]:
    telemetry = item.get("generation_telemetry") or {}
    structured_attempts = int(item.get("llm_structured_attempts") or telemetry.get("structured_attempts") or 0)
    json_fallback_attempts = int(item.get("llm_json_fallback_attempts") or telemetry.get("json_fallback_attempts") or 0)
    generation_attempts = int(item.get("llm_draft_count") or item.get("generation_attempt_count") or 0)
    observed_attempts = structured_attempts + json_fallback_attempts
    token_total = float(item.get("llm_estimated_output_token_budget_total") or telemetry.get("estimated_output_token_budget_total") or 0)
    default_budget = float(_first_script_token_budget())
    if token_total <= 0 and (observed_attempts > 0 or generation_attempts > 0):
        token_total = max(1, observed_attempts or 1) * default_budget
    if observed_attempts <= 0 and token_total > 0:
        observed_attempts = 1
        structured_attempts = 1
    if token_total <= 0:
        return {
            "actual": 0.0,
            "initial_script_draft": 0.0,
            "same_source_retry": 0.0,
            "structured_retry": 0.0,
            "json_fallback": 0.0,
        }
    token_per_attempt = token_total / max(1, observed_attempts)
    initial = token_per_attempt
    structured_retry = max(0, structured_attempts - 1) * token_per_attempt
    json_fallback = max(0, json_fallback_attempts) * token_per_attempt
    if _is_near_miss_rewrite_token_item(item) or _is_tts_regenerate_token_item(item):
        same_source_retry = 0.0
    else:
        same_source_retry = max(0, generation_attempts - 1) * default_budget
    actual = initial + structured_retry + json_fallback + same_source_retry
    return {
        "actual": actual,
        "initial_script_draft": initial,
        "same_source_retry": same_source_retry,
        "structured_retry": structured_retry,
        "json_fallback": json_fallback,
    }


def _is_near_miss_rewrite_token_item(item: dict) -> bool:
    return bool(item.get("near_miss_rewrite") or item.get("llm_call_stage") == "near_miss_rewrite")


def _is_tts_regenerate_token_item(item: dict) -> bool:
    return bool(item.get("tts_regenerate") or item.get("llm_call_stage") == "tts_regenerate" or item.get("stage") == "tts_regenerate")


def _token_overhead_status(rate: float) -> str:
    target = _token_overhead_target_rate()
    if rate > target:
        return "above_target"
    if rate > target / 2:
        return "watch"
    return "ok"


def _token_overhead_target_rate() -> float:
    raw = os.getenv("TOKEN_OVERHEAD_TARGET_RATE")
    if raw is None:
        raw = os.getenv("TOKEN_OVERHEAD_MAX_RATE")
    try:
        return max(0.0, float(raw)) if raw is not None else 0.10
    except ValueError:
        return 0.10


def _source_scorecard_token_budget() -> int:
    return max(0, _int_env("SOURCE_SCORECARD_OUTPUT_TOKEN_BUDGET", 512))


def _first_script_token_budget() -> int:
    raw = os.getenv("SCRIPT_OUTPUT_TOKEN_BUDGETS", "1600,2200,3000")
    for part in raw.split(","):
        try:
            value = int(part.strip())
        except ValueError:
            continue
        if value > 0:
            return value
    return 1600


def _best_near_miss_reasons(candidate_pool: list[dict]) -> list[str]:
    near_misses = [item for item in candidate_pool if item.get("candidate_bucket") == "near_miss"]
    near_misses.sort(key=lambda item: int(item.get("candidate_score") or 0), reverse=True)
    if not near_misses:
        return []
    return list(near_misses[0].get("soft_issues") or [])[:5]


def _operator_recommendation(summary: dict) -> str:
    if summary.get("llm_circuit_open"):
        return (
            "CHECK_LLM_CIRCUIT: "
            f"{summary.get('llm_circuit_stage') or 'unknown'} opened circuit: "
            f"{str(summary.get('llm_circuit_reason') or '')[:160]}"
        )
    final_accepted = int(summary.get("final_accepted") or 0)
    sources_considered = int(summary.get("sources_considered") or 0)
    source_scorecard_calls = int(summary.get("source_scorecard_calls") or 0)
    json_fallback_attempts = int(summary.get("json_fallback_attempts") or 0)
    structured_attempts = int(summary.get("structured_attempts") or 0)
    structured_failures = int(summary.get("structured_failures") or 0)
    critic_failed = int(summary.get("critic_failed") or 0)
    critic_passed = int(summary.get("critic_passed") or 0)
    critic_skipped = int(summary.get("critic_skipped") or 0)
    token_overhead_status = str(summary.get("token_overhead_status") or "")
    token_overhead_rate = float(summary.get("token_overhead_rate") or 0)

    if final_accepted == 0 and source_scorecard_calls > 0:
        if sources_considered == 0:
            return "CHECK_SOURCE_FILTER: 0 accepted after source scorecard calls."
        return "CHECK_GATE: source filter accepted items but script gate rejected all."
    if token_overhead_status == "above_target":
        return f"CHECK_TOKEN_OVERHEAD: token overhead is {token_overhead_rate:.1%}; review retry mix and source breadth."
    if json_fallback_attempts > 0:
        return "CHECK_COST: json_fallback_attempts are non-zero; review structured output reliability."
    if structured_attempts > 0 and structured_failures > structured_attempts / 2:
        return "CHECK_PROMPT_SCHEMA: structured_failures are high relative to attempts."
    if critic_failed > critic_passed + critic_skipped:
        return "CHECK_SCRIPT_NATURALNESS: critic failures exceed passed and skipped scripts."
    return f"OK: accepted {final_accepted} items with low fallback usage."


def _sum_generation_telemetry(items: list[dict]) -> dict[str, int]:
    totals = {
        "structured_attempts": 0,
        "json_fallback_attempts": 0,
        "structured_failures": 0,
        "json_fallback_failures": 0,
        "estimated_output_token_budget_total": 0,
    }
    for item in items:
        telemetry = item.get("generation_telemetry") or {}
        for key in totals:
            totals[key] += int(item.get(f"llm_{key}") or telemetry.get(key) or 0)
    return totals


def _load_source_filter_summary() -> dict:
    candidates = [
        FINAL_METADATA_FILE.with_name("source_filter_summary.json"),
        VIABLE_POSTS_FILE.with_name("source_filter_summary.json"),
    ]
    for path in candidates:
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


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


def _failure_category_counts(failed: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in failed:
        category = str(item.get("failure_category") or _failure_category(str(item.get("error") or ""), stage=str(item.get("stage") or "")))
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def _top_gate_errors(failed: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    for item in failed:
        for error in item.get("final_gate_errors") or _gate_errors_from_message(str(item.get("error") or "")):
            counts[str(error)] = counts.get(str(error), 0) + 1
    return [
        {"code": code, "count": count}
        for code, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:10]
    ]


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
    max_retries = max(0, min(max_retries, _max_llm_drafts_per_source() - 1))
    previous_history = _load_previous_accepted_metadata()
    scratch_metadata: list[Dict[str, Any]] = []
    preflight_error = _source_preflight_error(target_post)
    if preflight_error:
        print(f"🚫 원문 품질 미달로 재생성 불가 (postId={post_id}): {preflight_error}")
        return None

    while try_count <= max_retries:
        if llm_circuit_is_open():
            print(f"🚫 LLM circuit open; 재생성 스킵 (postId={post_id})")
            return None
        result = None
        metadata = None
        try:
            if try_count == 0:
                result: DraftScript = call_gpt_generate_script(title, content, post=target_post)
            else:
                result: DraftScript = call_gpt_generate_script(
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
            metadata["generation_attempt_count"] = try_count + 1
            metadata["llm_draft_count"] = try_count + 1
            metadata["failure_action"] = ""
            metadata["final_failure_codes"] = []

            finalized = _ensure_candidate_scored(
                _finalize_candidate(metadata, scratch_metadata, previous_history, target_post, append=False),
                target_post,
            )
            if finalized.get("candidate_bucket") != "accepted":
                print(
                    f"🚫 재생성 결과가 최종 후보 기준 미달 (postId={post_id}): "
                    f"bucket={finalized.get('candidate_bucket')} score={finalized.get('candidate_score')}"
                )
                return None
            return finalized

        except Exception as e:
            print(f"⚠️ 오류 (postId={post_id}, 시도 {try_count+1}): {e}")
            if result is not None:
                print(
                    f"🧠 마지막 GPT 응답:\n"
                    f"{json.dumps(result.model_dump(), ensure_ascii=False, indent=2)}\n"
                )
            msg = str(e)
            if _is_llm_quota_error(msg):
                open_llm_circuit(msg, "script_regenerate")
            if _is_llm_quota_error(msg) and _local_fallback_enabled():
                try:
                    metadata = _build_local_fallback_metadata(target_post, msg)
                    print(f"🧩 OpenAI quota 오류로 로컬 fallback 재생성 완료 (postId={post_id})")
                    finalized = _ensure_candidate_scored(
                        _finalize_candidate(metadata, scratch_metadata, previous_history, target_post, append=False),
                        target_post,
                    )
                    return finalized if finalized.get("candidate_bucket") == "accepted" else None
                except Exception as fallback_error:
                    print(f"🚫 로컬 fallback 재생성 실패 (postId={post_id}): {fallback_error}")
                    return None
            if _is_llm_quota_error(msg):
                print(f"🚫 LLM circuit opened during regeneration (postId={post_id}): {msg}")
                return None
            failure_action = classify_failure(msg, script_chars=_script_chars_from_result(result), repeated=try_count >= max_retries)
            if failure_action == FailureAction.LLM_REWRITE_ONCE and not _allow_llm_rewrite_on_narrative_failure():
                failure_action = FailureAction.SKIP_SOURCE
            if failure_action == FailureAction.REPAIR_ONLY and metadata is not None:
                retry_metadata, retry_error = _repair_only_retry(metadata, target_post, scratch_metadata, previous_history, append=False)
                if retry_metadata is not None:
                    print(f"🛠️ repair-only retry 재생성 성공 (postId={post_id})")
                    retry_metadata = _ensure_candidate_scored(retry_metadata, target_post)
                    if retry_metadata.get("candidate_bucket") == "accepted":
                        return retry_metadata
                    print(
                        f"🚫 repair-only retry 결과가 최종 후보 기준 미달 (postId={post_id}): "
                        f"bucket={retry_metadata.get('candidate_bucket')} score={retry_metadata.get('candidate_score')}"
                    )
                    return None
                print(f"🚫 repair-only retry 재생성 실패 (postId={post_id}): {retry_error}")
                return None
            if failure_action == FailureAction.SKIP_SOURCE:
                print(f"🚫 재생성 스킵 (postId={post_id}): action={failure_action.value} error={msg}")
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
