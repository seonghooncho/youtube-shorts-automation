# shared/jobs/generate_scripts_from_filtered.py

import json
import os
import re
from typing import Any, Dict
from generator.text.generate_script import generate_script, ReturnScript
from generator.text.script_quality import (
    MAX_SCRIPT_CHARS,
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
from shared.utils.config import VIABLE_POSTS_FILE, FINAL_METADATA_FILE, FAILED_POSTS_FILE

_PERFORMANCE_CONTEXT_CACHE: str | None = None

EXAMPLE_JSON = """
{
        "title": "Neighbor's Tenants' Kids Invaded My Property",
        "description": "Dealing with a neighbor's tenants' kids running amok in my driveway. Can you relate to this frustrating situation?",
        "tags": ["storytime", "neighborhood", "drama", "reddit", "beachhouse"],
        "voice": "male",
        "visual_keywords": ["suburban driveway", "security camera", "kids playing", "angry neighbor", "rental house", "phone messages"],
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
        "rewrite_notes": "Removed slow vacation-rental context and led with the crossed boundary.",
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
        "- Fill `first_2_seconds` with the exact opening phrase that carries the first two seconds of attention. It must be concrete, not context.",
        "- Fill `turning_point` with the moment where the situation gets worse, not just a summary.",
        "- Fill `payoff_line` with the final conflict statement before the viewer question.",
        "- Fill `viewer_question` with the exact final comment-bait question. It must be a real question and should not be generic if the source supports a sharper one.",
        "- Fill `marketability_score` from 1 to 5. Use 4 or 5 only when the story has a concrete unfair action, clear stakes, and a debatable final decision.",
        "- Fill `retention_risk` with the main reason viewers might swipe away and how your rewrite prevents it.",
        "- Fill `cut_plan` with 4 to 6 short visual cut intentions. Use concrete settings, hands, phones, bills, hallway, kitchen, office, vet, car, or message shots.",
        "- Fill `bg_strategy` as `story`, `asmr`, or `hybrid`. Use `hybrid` for most stories, `story` when concrete visual scenes matter, and `asmr` only when the source is mostly emotional or abstract.",
        "- Fill `rewrite_notes` with one short note about what you tightened for retention.",
        "- Use a title that names the concrete conflict. Avoid generic titles like 'Did I Overreact?' unless paired with the specific action. Do not add hashtags; the uploader adds the channel hashtag style.",
        "- Write in a **casual, conversational tone**, as if you're sharing a story with a friend.",
        "- Avoid formal or stiff language. Use expressions and tones that are commonly seen in successful YouTube Shorts.",
        "- The first sentence must be a strong hook with a concrete crossed line. Start with what someone did wrong, what it cost, or why the narrator looked like the villain. Do not start with age, backstory, relationship length, 'So, get this', or 'A little backstory'.",
        "- The first 3 paragraphs must follow this rhythm: hook result, quick context, then unexpected escalation. Do not explain every detail chronologically.",
        "- Every paragraph should either add a new problem, raise the stakes, or move toward the final decision. Cut neutral reflection.",
        "- Keep the pacing fast. Remove filler, repeated setup, and slow explanations. The narration should still be understandable after a moderate speed-up.",
        "- Structure the story in a `script` array of exactly 5 or 6 short paragraphs.",
        f"- The joined `script` narration must be {target_min_chars} to {target_max_chars} characters, including spaces.",
        f"- Anything over {MAX_SCRIPT_CHARS} characters is invalid. Cut harder instead of explaining more.",
        "- Paragraph length limits: hook under 135 characters, middle paragraphs under 185 characters each, final paragraph under 170 characters.",
        "- Before returning, silently count the joined `script` characters and cut until it fits the target window. Do not reveal the count.",
        "- Prefer 145 to 185 spoken words total. Remove repeated history, extra dialogue, and neutral reflection first.",
        "- The target final narration length is roughly 42 to 65 seconds after a moderate speed-up. Prefer concise sentences over long paragraphs.",
        "- The script should never feel stretched, repetitive, or abruptly shortened; keep only the setup, escalation, decision, and question.",
        "- Keep the final paragraph short. Do not pack new facts and the viewer question into one overloaded sentence.",
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
        if not isinstance(metadata["cut_plan"], list) or not all(isinstance(cut, str) for cut in metadata["cut_plan"]):
            raise ValueError("❌ cut_plan은 문자열 리스트여야 함")
        if metadata.get("bg_strategy") not in {"story", "asmr", "hybrid"}:
            raise ValueError("❌ bg_strategy는 story, asmr, hybrid 중 하나여야 함")

        metadata["script"] = [line.strip() for line in metadata["script"] if line.strip()]
        metadata["story_beats"] = [beat.strip() for beat in metadata["story_beats"] if beat.strip()]
        metadata["cut_plan"] = [cut.strip() for cut in metadata["cut_plan"] if cut.strip()][:6]

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
        metadata["source_scorecard"] = (post or {}).get("source_scorecard") or {}
        metadata["source_score"] = (post or {}).get("source_score")
        metadata["source_archetype"] = (post or {}).get("source_archetype") or metadata.get("hook_type") or ""
        metadata["source_provider"] = (post or {}).get("source_provider", "")
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
        target_min_chars, target_max_chars = _script_target_window()
        current_chars = _extract_current_char_count(message)
        current_phrase = f"The previous joined script was {current_chars} characters. " if current_chars else ""
        return (
            f"{current_phrase}Return the full JSON again, but rewrite the `script` to "
            f"{target_min_chars}-{target_max_chars} characters total, hard max {MAX_SCRIPT_CHARS}. "
            "Use exactly 5 short paragraphs. Keep the same source conflict, hook, turning point, payoff, "
            "and final question. Remove repeated backstory, extra dialogue, and neutral reflection. "
            "No paragraph may exceed 185 characters."
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
    return os.getenv("SCRIPT_LOCAL_FALLBACK_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def _is_llm_quota_error(message: str) -> bool:
    lowered = (message or "").lower()
    return "insufficient_quota" in lowered or "exceeded your current quota" in lowered or "rate_limit_exceeded" in lowered


def _build_local_fallback_metadata(post: Dict[str, Any], reason: str = "") -> Dict[str, Any]:
    """Create a conservative script from source text when the LLM API is unavailable."""
    title = _clean_sentence(post.get("title") or "Boundary Story", max_chars=92)
    content = str(post.get("content") or "")
    parts = _extract_source_parts(content)
    boundary = parts.get("boundary") or "I was clear about the boundary before anything happened"
    setup = parts.get("setup") or _sentence_at(content, 0) or "At first, I tried to handle it calmly."
    crossed_line = parts.get("crossed_line") or _sentence_at(content, 1) or "someone crossed the line and acted like I was the problem"
    public_pressure = parts.get("public_pressure") or _sentence_at(content, 2) or "people around us started taking sides"
    escalation = parts.get("escalation") or _sentence_at(content, 3) or "the pressure kept building instead of anyone owning the mistake"
    proof = parts.get("proof") or _sentence_at(content, 4) or "the messages showed exactly what I had agreed to"
    consequence = parts.get("consequence") or _sentence_at(content, 5) or "I held the boundary and stopped covering for it"
    debate = parts.get("debate") or "Was I wrong to hold the boundary?"

    hook = _clean_sentence(f"{_sentence_case(boundary)}, but {crossed_line}", max_chars=132)
    if not hook.endswith((".", "?", "!")):
        hook = f"{hook}."
    first_two = _clean_sentence(hook, max_chars=96).rstrip(".!?")
    viewer_question = _ensure_question(_clean_sentence(debate, max_chars=150))
    payoff = _clean_sentence(consequence, max_chars=120)
    source_summary = _clean_sentence(f"{setup} {crossed_line}", max_chars=220)
    story_beats = [
        _clean_sentence(setup, max_chars=120),
        f"The boundary was: {_clean_sentence(boundary, max_chars=110)}",
        _clean_sentence(crossed_line, max_chars=130),
        _clean_sentence(public_pressure, max_chars=130),
        _clean_sentence(proof, max_chars=130),
        _clean_sentence(consequence, max_chars=130),
    ]
    visual_keywords = _fallback_visual_keywords(title, content)

    script = [
        hook,
        _clean_sentence(f"The boundary was simple: {boundary}. {setup}", max_chars=185),
        _clean_sentence(f"Then {crossed_line}. I tried to keep it calm, but it already felt like my limit did not matter.", max_chars=185),
        _clean_sentence(f"What made it worse was {public_pressure}. {escalation}", max_chars=185),
        _clean_sentence(f"The proof was clear: {proof}. So {consequence}.", max_chars=185),
        _clean_sentence(f"Now people are split because I refused to smooth it over. {viewer_question}", max_chars=185),
    ]
    script = _fit_fallback_script(script, story_beats)

    metadata = ReturnScript(
        title=title,
        description=f"A fast storytime about a crossed boundary and the fallout after {title.lower()}.",
        tags=["storytime", "boundaries", "redditstories", "aita", "familydrama"],
        voice="neutral",
        visual_keywords=visual_keywords,
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
        "escalation": r"but\s+(.*?)(?:\.|$)",
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


def _sentence_at(content: str, index: int) -> str:
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", str(content or "")) if item.strip()]
    return sentences[index] if 0 <= index < len(sentences) else ""


def _clean_sentence(text: str, *, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip(" -")
    if len(cleaned) <= max_chars:
        return cleaned
    truncated = cleaned[: max(0, max_chars - 1)].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return f"{truncated.rstrip('.,;:')}."


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


def _fit_fallback_script(script: list[str], story_beats: list[str]) -> list[str]:
    joined_len = len(" ".join(script))
    if joined_len < TARGET_MIN_SCRIPT_CHARS:
        script.insert(
            -1,
            _clean_sentence(
                "The frustrating part was not just the mistake. It was being expected to absorb it quietly so nobody else had to feel uncomfortable.",
                max_chars=185,
            ),
        )
    if len(" ".join(script)) < TARGET_MIN_SCRIPT_CHARS and story_beats:
        script.insert(-1, _clean_sentence(f"That is why this felt bigger than one awkward moment: {story_beats[0]}", max_chars=185))
    while len(" ".join(script)) > TARGET_MAX_SCRIPT_CHARS and len(script) > 5:
        script.pop(-2)
    return script


def generate_scripts_from_filtered():
    if not VIABLE_POSTS_FILE.exists():
        print("❌ viable_posts.json이 없습니다.")
        return

    with open(VIABLE_POSTS_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)

    metadata_list = []
    failed_items = []
    llm_unavailable_reason = None

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
        if llm_unavailable_reason and _local_fallback_enabled():
            try:
                metadata_list.append(_build_local_fallback_metadata(post, llm_unavailable_reason))
                print(f"🧩 로컬 fallback 대본 생성 완료 (post {idx}): {origin_id}")
            except Exception as fallback_error:
                failed_items.append({"idx": idx, "id": origin_id, "title": title, "error": str(fallback_error)})
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

                metadata_list.append(metadata)
                break  # 성공 시 루프 종료

            except Exception as e:
                if isinstance(result, ReturnScript):
                    char_count = len(script_text(result.model_dump()))
                    print(f"⚠️ GPT 응답 검증 실패 (post {idx}, 시도 {try_count+1}, script_chars={char_count}): {e}")
                msg = str(e)
                if _is_llm_quota_error(msg) and _local_fallback_enabled():
                    llm_unavailable_reason = msg
                    try:
                        metadata_list.append(_build_local_fallback_metadata(post, msg))
                        print(f"🧩 OpenAI quota 오류로 로컬 fallback 대본 생성 완료 (post {idx}): {origin_id}")
                        break
                    except Exception as fallback_error:
                        failed_items.append({"idx": idx, "id": origin_id, "title": title, "error": str(fallback_error)})
                        print(f"🚫 로컬 fallback 실패 (post {idx}): {fallback_error}")
                        break
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
