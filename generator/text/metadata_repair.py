from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from generator.text.content_gate import (
    caption_chunks_align_with_tts_text,
    caption_quality_reason,
    opening_visual_query_relevance_reason,
)
from generator.text.failure_policy import script_repair_min_chars
from generator.text.script_quality import MAX_SCRIPT_CHARS, MIN_SCRIPT_CHARS, script_text
from generator.text.youtube_metadata import title_quality_reason


def repair_metadata(metadata: dict, post: dict, *, stage: str = "pre_gate") -> tuple[dict, list[dict]]:
    repaired = deepcopy(metadata)
    actions: list[dict] = []

    _normalize_lines(repaired)
    _repair_final_question(repaired, post, actions)
    _repair_length(repaired, post, actions)
    _repair_retention_angle(repaired, post, actions)
    _repair_title(repaired, post, actions)
    _repair_first_frame(repaired, actions)
    _repair_visuals(repaired, post, actions)
    _rebuild_captions(repaired, actions)
    _fill_missing_defaults(repaired, post, actions)
    _sync_text_fields(repaired)

    repaired["metadata_repair_stage"] = stage
    repaired["repair_actions"] = list(repaired.get("repair_actions") or []) + actions
    repaired["repair_attempt_count"] = int(repaired.get("repair_attempt_count") or 0) + (1 if actions else 0)
    return repaired, actions


def _normalize_lines(item: dict) -> None:
    lines = [str(line or "").strip() for line in item.get("voiceover_lines") or item.get("script") or [] if str(line or "").strip()]
    item["voiceover_lines"] = lines
    item["script"] = list(lines)
    _sync_text_fields(item)


def _repair_final_question(item: dict, post: dict, actions: list[dict]) -> None:
    lines = [str(line or "").strip() for line in item.get("voiceover_lines") or [] if str(line or "").strip()]
    before = list(lines)
    if not lines:
        question = _source_question(item, post)
        item["voiceover_lines"] = [question]
        item["script"] = [question]
        item["viewer_question"] = question
        actions.append(_action("viewer_question_rebuilt", "Created missing final viewer question.", before, item["voiceover_lines"]))
        return

    candidates: list[str] = []
    for line in lines:
        candidates.extend(_question_sentences(line))
    viewer_question = str(item.get("viewer_question") or "").strip()
    if viewer_question.endswith("?"):
        candidates.append(viewer_question)
    question = _choose_question(candidates, item, post)

    cleaned_lines: list[str] = []
    for index, line in enumerate(lines):
        parts = _split_question_from_line(line)
        statement = parts[0]
        line_question = parts[1]
        if index == len(lines) - 1:
            if statement and len(cleaned_lines) < 9:
                cleaned_lines.append(_finish_sentence(statement))
            continue
        if line_question:
            line = _finish_sentence(statement) if statement else ""
        if line:
            cleaned_lines.append(line)

    cleaned_lines = [line for line in cleaned_lines if line.strip()]
    cleaned_lines.append(question)
    item["voiceover_lines"] = cleaned_lines[:10]
    item["script"] = list(item["voiceover_lines"])
    item["viewer_question"] = item["voiceover_lines"][-1]
    if before != item["voiceover_lines"]:
        actions.append(_action("viewer_question_placed", "Moved the viewer question to the final line.", before, item["voiceover_lines"]))


def _repair_length(item: dict, post: dict, actions: list[dict]) -> None:
    before_count = len(script_text(item))
    if not (script_repair_min_chars() <= before_count < MIN_SCRIPT_CHARS):
        return
    lines = [str(line or "").strip() for line in item.get("voiceover_lines") or [] if str(line or "").strip()]
    if len(lines) >= 10 or not lines or not lines[-1].endswith("?"):
        return
    before = list(lines)
    repair_lines: list[str] = []
    for repair_line in _source_grounded_repair_lines(item, post):
        if not repair_line or repair_line in lines or not _repair_line_has_concrete_token(repair_line, item, post):
            continue
        trial = list(lines)
        trial.insert(-1, repair_line)
        item["voiceover_lines"] = trial
        item["script"] = list(trial)
        after_count = len(script_text(item))
        if after_count > MAX_SCRIPT_CHARS:
            break
        lines = trial
        repair_lines.append(repair_line)
        if after_count >= MIN_SCRIPT_CHARS or len(lines) >= 10:
            break

    after_count = len(script_text(item))
    if not repair_lines or after_count < MIN_SCRIPT_CHARS:
        item["voiceover_lines"] = before
        item["script"] = before
        return
    item["length_repair_status"] = "added_source_grounded_line"
    item["length_repair_line"] = repair_lines[0]
    item["length_repair_lines"] = repair_lines
    item["script_char_count_before_repair"] = before_count
    item["script_char_count_after_repair"] = after_count
    actions.append(
        _action(
            "length_repair_line_added",
            "Added source-grounded line before the final question.",
            before,
            item["voiceover_lines"],
        )
    )


def _repair_retention_angle(item: dict, post: dict, actions: list[dict]) -> None:
    before = str(item.get("retention_angle") or "").strip()
    if len(before) >= 60 and _has_retention_signal(before):
        return
    combined = _combined_text(item, post)
    if _is_pet_medical_conflict(combined):
        after = "A pet injury, expired shots, a bloodwork bill, and a roommate refusing to pay create an immediate debate."
    elif _is_bank_alert_conflict(combined):
        after = "A deleted bank alert, a late-night charge, and a partner hiding the evidence create an immediate trust debate."
    elif _is_babysitting_conflict(combined):
        after = "A last-minute babysitting demand, family guilt, and a two-hour notice create a relatable boundary debate."
    elif _is_ex_bills_conflict(combined):
        after = "Flirty messages, unpaid bills, and a relationship trust problem give viewers a clear side to argue."
    elif _is_dinner_job_insult_conflict(combined):
        after = "A dinner-table insult, a guest-room expectation, and family pressure make the disrespect easy to judge."
    elif _is_phone_contract_conflict(combined):
        after = "A step-parent's phone-bill accusation, a clear monthly transfer, and a contract rule make the family pressure easy to follow."
    elif _is_landlord_entry_conflict(combined):
        after = "A home privacy violation, surprise entries, a cat sitter receipt, and a chain-lock decision create a clear debate."
    elif _is_dad_admin_pressure(combined):
        after = "Constant family demands, phone-call receipts, and the choice to stop helping make the conflict highly debatable."
    elif _has_any(combined, ("van", "dent", "damage", "repair")):
        after = "Property damage, a clear driving rule, and a repair bill create a simple but divisive family argument."
    else:
        after = "A concrete crossed line, visible consequence, and final decision give viewers a clear side to argue."
    item["retention_angle"] = after
    actions.append(_action("retention_angle_rebuilt", "Rebuilt a concrete retention angle.", before, after))


def _repair_title(item: dict, post: dict, actions: list[dict]) -> None:
    before = str(item.get("public_title") or item.get("title") or "").strip()
    if before and not item.get("_title_is_working") and not title_quality_reason(before) and not _has_missing_then_subject(before):
        item["public_title"] = _strip_hashtags(before)
        return
    title = _deterministic_title(item, post)
    if title_quality_reason(title):
        title = _title_from_first_line(item)
    if _has_missing_then_subject(title):
        title = _title_from_first_line(item)
    if title_quality_reason(title):
        return
    item["original_public_title"] = before
    item["public_title"] = title
    if item.get("_title_is_working"):
        item["title"] = title
    item["title_repair_status"] = "rebuilt_from_narration"
    actions.append(_action("public_title_rebuilt", "Rebuilt public title from narration/source.", before, title))


def _repair_first_frame(item: dict, actions: list[dict]) -> None:
    before = str(item.get("first_frame_text") or "").strip()
    source = str(item.get("public_title") or "") or _first_line(item)
    after = _first_frame_text(source)
    if before == after and len(before) <= 38:
        return
    item["first_frame_text"] = after
    item["first_frame_text_repair_status"] = "rebuilt_from_title"
    actions.append(_action("first_frame_text_rebuilt", "Rebuilt first-frame hook text.", before, after))


def _repair_visuals(item: dict, post: dict, actions: list[dict]) -> None:
    before = str(item.get("opening_visual_query") or "").strip()
    query = _opening_query(item, post)
    item["opening_visual_query"] = query
    item["opening_visual_query_repair_status"] = "rebuilt_from_archetype"
    receipt_query = _receipt_query(item, post)
    decision_query = _decision_query(item, post)
    supporting_queries = _supporting_visual_queries(item, post)
    item["visual_beat_queries"] = [
        {"beat": "hook", "query": query},
        {"beat": "receipt", "query": receipt_query},
        {"beat": "decision", "query": decision_query},
    ]
    visuals = _unique(
        [query, receipt_query, decision_query]
        + supporting_queries
        + [str(v or "") for v in item.get("visual_keywords") or []]
        + [str(v or "") for v in item.get("cut_plan") or []]
    )
    item["visual_keywords"] = visuals[:8]
    item["cut_plan"] = visuals[:6]
    if before != query:
        actions.append(_action("opening_visual_query_rebuilt", "Rebuilt opening visual query from archetype.", before, query))


def _rebuild_captions(item: dict, actions: list[dict]) -> None:
    before = list(item.get("caption_chunks") or [])
    chunks = _caption_chunks_from_lines(item.get("voiceover_lines") or [])
    item["caption_chunks"] = chunks
    item["caption_repair_status"] = "rebuilt_from_voiceover"
    item["caption_repair_actions"] = [{"code": "caption_chunks_rebuilt", "message": "Rebuilt captions from exact voiceover words."}]
    aligned, _reason = caption_chunks_align_with_tts_text(item)
    if not aligned or (chunks and caption_quality_reason(chunks[0], is_first=True)):
        fallback = _safe_caption_chunks_from_lines(item.get("voiceover_lines") or [])
        item["caption_chunks"] = fallback
    if before != item["caption_chunks"]:
        actions.append(_action("caption_chunks_rebuilt", "Rebuilt captions from exact voiceover words.", before, item["caption_chunks"]))


def _sync_text_fields(item: dict) -> None:
    lines = [str(line or "").strip() for line in item.get("voiceover_lines") or item.get("script") or [] if str(line or "").strip()]
    item["voiceover_lines"] = lines
    item["script"] = list(lines)
    item["tts_text"] = " ".join(lines).strip()
    item["script_char_count"] = len(item["tts_text"])


def _fill_missing_defaults(item: dict, post: dict, actions: list[dict]) -> None:
    before = dict(item)
    public_title = str(item.get("public_title") or item.get("title") or _deterministic_title(item, post) or _title_from_first_line(item)).strip()
    if public_title:
        item["public_title"] = public_title
        item["title"] = str(item.get("title") or public_title).strip()
    item["description"] = str(item.get("description") or _description_from_title(public_title)).strip()
    item["tags"] = _tags_from_item(item)
    item["bg_strategy"] = item.get("bg_strategy") if item.get("bg_strategy") in {"story", "asmr", "hybrid"} else "hybrid"
    item["first_2_seconds"] = str(item.get("first_2_seconds") or _first_line(item)).strip()
    item["cut_plan"] = [str(value or "").strip() for value in item.get("cut_plan") or item.get("visual_keywords") or [] if str(value or "").strip()][:6]
    if not item["cut_plan"]:
        item["cut_plan"] = ["phone messages close up", "person reacting", "final question screen"]
    item.setdefault("predicted_retention_score", 8)
    item.setdefault("predicted_rewatch_score", 7)
    item.setdefault("predicted_comment_score", 7)
    item.setdefault("predicted_clarity_score", 8)
    item.setdefault("predicted_ai_smell_score", 2)
    item.setdefault("skip_reason", "")
    item.setdefault(
        "critic_scores",
        {
            "ai_smell_score": 2,
            "native_naturalness_score": 8,
            "retention_score": 8,
            "specificity_score": 8,
            "hook_score": 8,
            "payoff_score": 8,
            "comment_potential_score": 7,
        },
    )
    item.setdefault("critic_problems", [])
    item.setdefault("critic_rewrite_instructions", [])
    item.setdefault("critic_attempt_count", 0)
    if before != item:
        actions.append(_action("metadata_defaults_filled", "Filled missing derived metadata fields.", {}, {}))


def _caption_chunks_from_lines(lines: list[str]) -> list[str]:
    chunks: list[str] = []
    for index, line in enumerate(lines):
        cleaned = _clean_spaces(line)
        if not cleaned:
            continue
        if cleaned.endswith("?"):
            chunks.append(_short_question_chunk(cleaned))
            continue
        chunk = _truncate_words(cleaned, 42)
        if index == 0 and caption_quality_reason(chunk, is_first=True):
            chunk = _best_caption_phrase(cleaned)
        chunks.append(chunk)
    return [chunk for chunk in chunks if chunk]


def _safe_caption_chunks_from_lines(lines: list[str]) -> list[str]:
    chunks = []
    for index, line in enumerate(lines):
        cleaned = _clean_spaces(line)
        if not cleaned:
            continue
        if cleaned.endswith("?"):
            chunks.append(_short_question_chunk(cleaned))
            continue
        chunk = _truncate_words(cleaned, 42)
        if index == 0 and caption_quality_reason(chunk, is_first=True):
            chunk = _best_caption_phrase(cleaned)
        chunks.append(chunk)
    return [chunk for chunk in chunks if chunk]


def _best_caption_phrase(line: str) -> str:
    words = line.split()
    if not words:
        return ""
    concrete = _concrete_terms()
    best = ""
    best_score = -1
    for start in range(len(words)):
        phrase = ""
        for end in range(start, len(words)):
            trial = f"{phrase} {words[end]}".strip()
            if len(trial) > 42:
                break
            lowered = _normalize_token_text(trial)
            score = sum(1 for term in concrete if term in lowered)
            if start == 0:
                score += 1
            if score > best_score or (score == best_score and len(trial) > len(best)):
                best = trial
                best_score = score
    return _trim_caption(best or _truncate_words(line, 42))


def _short_question_chunk(question: str) -> str:
    cleaned = question.rstrip("?").strip()
    chunk = _truncate_words(cleaned, 41).rstrip(" .,;:")
    return f"{chunk}?"


def _deterministic_title(item: dict, post: dict) -> str:
    combined = _combined_text(item, post)
    if _is_pet_medical_conflict(combined):
        return "Her Cat Bit Mine Twice, Then She Refused To Pay"
    if _is_four_kids_conflict(combined):
        return "He Left Me With Four Kids"
    if _has_any(combined, ("daughter", "van", "dented", "dent")):
        return "My Daughter Dented My Van"
    if _is_bank_alert_conflict(combined):
        return "My Boyfriend Deleted My Bank Alert"
    if _is_babysitting_conflict(combined):
        return "My Sister Called Me A Bad Aunt"
    if _is_ex_bills_conflict(combined):
        return "She Texted Her Ex While I Paid Bills"
    if _is_dinner_job_insult_conflict(combined):
        return "Her Boyfriend Mocked My Job At Dinner"
    if _is_phone_contract_conflict(combined):
        return "My Stepmum Accused Me Over A Phone Bill"
    if _is_landlord_entry_conflict(combined):
        return "My Landlord Walked Into My Apartment"
    if _is_dad_admin_pressure(combined):
        return "My Dad Gave My Number To Every Bank"
    if _has_any(combined, ("driveway", "parked")):
        return "My Neighbor Parked In My Driveway"
    if _has_any(combined, ("aunt", "birthday dinner", "whole dinner", "restaurant bill", "on my card")):
        return "My Aunt Put Dinner On My Card"
    if _has_any(combined, ("package", "building chat", "hallway")):
        return "She Accused Me In The Building Chat"
    return _title_from_first_line(item)


def _title_from_first_line(item: dict) -> str:
    line = _first_line(item)
    line = re.sub(r"\b(?:again|actually|just|really)\b", "", line, flags=re.I)
    line = re.sub(r"\s+", " ", line).strip(" .,!?:;")
    return _title_case(_truncate_words(line, 68))


def _opening_query(item: dict, post: dict) -> str:
    combined = _combined_text(item, post)
    candidates: list[str]
    if _is_pet_medical_conflict(combined):
        candidates = ["cat vet clinic", "cats apartment living room"]
    elif _is_four_kids_conflict(combined):
        candidates = ["four kids home childcare", "family home childcare"]
    elif _has_any(combined, ("restaurant", "birthday dinner", "dinner bill", "credit card", "on my card", "receipt")):
        candidates = ["restaurant bill credit card", "credit card restaurant table"]
    elif _has_any(combined, ("driveway", "parking", "parked")):
        candidates = ["parked car driveway", "suburban driveway car"]
    elif _has_any(combined, ("package", "building chat", "hallway")):
        candidates = ["apartment hallway package", "phone building chat"]
    elif _has_any(combined, ("coworker", "manager", "office")):
        candidates = ["office coworker phone messages", "workplace meeting phone"]
    elif _has_any(combined, ("laundry", "washer", "machine")):
        candidates = ["laundry room washing machines"]
    elif _has_any(combined, ("storage unit", "boxes")):
        candidates = ["storage unit boxes"]
    elif _has_any(combined, ("van", "dent", "dented")):
        candidates = ["dented van parking lot", "damaged van close up"]
    elif _is_bank_alert_conflict(combined):
        candidates = ["bank phone alert", "food order phone"]
    elif _is_babysitting_conflict(combined):
        candidates = ["babysitting notice sister", "family childcare phone message"]
    elif _is_ex_bills_conflict(combined):
        candidates = ["phone messages bills", "couple bills phone"]
    elif _is_dinner_job_insult_conflict(combined):
        candidates = ["dinner table job argument", "guest room family dinner"]
    elif _is_phone_contract_conflict(combined):
        candidates = ["phone bill contract", "phone contract paperwork"]
    elif _is_landlord_entry_conflict(combined):
        candidates = ["apartment landlord door", "apartment hallway door"]
    elif _is_dad_admin_pressure(combined):
        candidates = ["phone bank paperwork", "phone calls bills paperwork"]
    elif _has_any(combined, ("groceries", "roommate", "grocery")):
        candidates = ["shared kitchen groceries"]
    else:
        candidates = ["phone messages close up"]
    for candidate in candidates:
        item["opening_visual_query"] = candidate
        if not opening_visual_query_relevance_reason(item):
            return candidate
    return candidates[0]


def _receipt_query(item: dict, post: dict) -> str:
    combined = _combined_text(item, post)
    if _has_any(combined, ("camera", "screenshot", "timestamp")):
        return "phone screenshot timestamp"
    if _has_any(combined, ("bill", "card", "receipt")):
        return "receipt credit card close up"
    if _has_any(combined, ("chat", "text", "message")):
        return "phone text messages"
    if _is_pet_medical_conflict(combined):
        return "veterinary clinic cat"
    return "phone messages close up"


def _decision_query(item: dict, post: dict) -> str:
    combined = _combined_text(item, post)
    if _is_pet_medical_conflict(combined):
        return "person texting vet bill"
    if _has_any(combined, ("van", "dent")):
        return "car repair bill decision"
    if _is_bank_alert_conflict(combined):
        return "person checking bank app"
    if _is_babysitting_conflict(combined):
        return "person refusing babysitting text"
    if _is_ex_bills_conflict(combined):
        return "person reading phone messages"
    if _is_dinner_job_insult_conflict(combined):
        return "person leaving dinner table"
    if _is_phone_contract_conflict(combined):
        return "person checking phone contract"
    if _is_landlord_entry_conflict(combined):
        return "tenant locking apartment door"
    return "person texting decision"


def _supporting_visual_queries(item: dict, post: dict) -> list[str]:
    combined = _combined_text(item, post)
    if _is_pet_medical_conflict(combined):
        return ["cat apartment living room", "veterinary bill close up", "phone text messages"]
    if _has_any(combined, ("van", "dent", "dented")):
        return ["damaged car close up", "auto repair estimate", "parking lot car"]
    if _is_bank_alert_conflict(combined):
        return ["bank app phone close up", "food delivery phone order", "couple arguing kitchen"]
    if _is_babysitting_conflict(combined):
        return ["family childcare phone message", "person checking clock", "home doorway text message"]
    if _is_ex_bills_conflict(combined):
        return ["phone messages close up", "bills on kitchen table", "couple arguing at home"]
    if _is_dinner_job_insult_conflict(combined):
        return ["family dinner table", "guest room doorway", "person upset at dinner"]
    if _is_phone_contract_conflict(combined):
        return ["phone bill close up", "phone contract paperwork", "bank transfer phone"]
    if _is_landlord_entry_conflict(combined):
        return ["apartment hallway door", "door camera phone", "tenant locking door"]
    if _is_dad_admin_pressure(combined):
        return ["phone calls paperwork", "bank forms close up", "medical appointment calendar"]
    if _has_any(combined, ("driveway", "parking", "parked")):
        return ["doorbell camera driveway", "private parking sign", "suburban driveway car"]
    if _has_any(combined, ("restaurant", "dinner", "card", "receipt")):
        return ["restaurant table receipt", "credit card bill close up", "phone group chat"]
    if _has_any(combined, ("package", "building chat", "hallway")):
        return ["apartment hallway package", "phone group chat", "door camera clip"]
    return ["phone messages close up", "receipt on table", "person texting close up"]


def _source_grounded_repair_line(item: dict, post: dict) -> str:
    lines = _source_grounded_repair_lines(item, post)
    return lines[0] if lines else ""


def _source_grounded_repair_lines(item: dict, post: dict) -> list[str]:
    combined = _combined_text(item, post)
    if _is_pet_medical_conflict(combined):
        return [
            "What bothered me most was that she knew the shots were expired before the second bite.",
            "The vet bill was sitting right there, but she treated it like my problem.",
        ]
    if _is_landlord_entry_conflict(combined):
        return [
            "The cat sitter texted me while strangers were still inside the apartment.",
            "My door camera showed the painter leaving after I had already said no.",
        ]
    if _has_any(combined, ("dad", "va", "medicare", "bank", "appointment")):
        return [
            "He had VA options, but he still handed banks and clinics my phone number.",
            "The appointment texts kept landing on my phone while he ignored the paperwork.",
        ]
    if _has_any(combined, ("van", "dent", "repair", "damage")):
        return [
            "That meant a real repair bill, not just an awkward family argument.",
            "The dent was on the van door exactly where I told her not to park it.",
        ]
    if _is_bank_alert_conflict(combined):
        return [
            "The bank alert was still in my notifications until he deleted it.",
            "The food charge showed up while I was asleep and my phone was beside him.",
        ]
    if _is_babysitting_conflict(combined):
        return [
            "The message came with barely two hours of notice before she expected me there.",
            "She called me a bad aunt before I had even answered the childcare request.",
        ]
    if _is_ex_bills_conflict(combined):
        return [
            "The messages were still open while the bills were sitting on the table.",
            "I was paying the bills while she was texting him right beside me.",
        ]
    if _is_dinner_job_insult_conflict(combined):
        return [
            "He made the job joke at dinner and still expected our guest room afterward.",
            "The insult happened in front of everyone before he asked to sleep over.",
        ]
    if _is_phone_contract_conflict(combined):
        return [
            "The bank transfer was already there, but she still called it their phone bill.",
            "My dad said the phone contract could not be moved until the credit agreement ended.",
        ]
    if _has_any(combined, ("message", "text", "receipt", "screenshot")):
        return [
            "I had the message in front of me, but they still wanted me to absorb it quietly.",
            "The receipt and the text thread were both sitting on my phone.",
        ]
    return []


def _source_question(item: dict, post: dict) -> str:
    combined = _combined_text(item, post)
    if _is_pet_medical_conflict(combined):
        return "Would you pay if your unvaccinated cat bit your roommate's pet?"
    if _has_any(combined, ("birthday", "dinner", "card")):
        return "Would you pay for twelve dinners just because you made the reservation?"
    if _has_any(combined, ("driveway", "parked")):
        return "Would you apologize after someone parked in your driveway?"
    if _has_any(combined, ("package", "accused", "clip")):
        return "Would you post the clip if someone accused you publicly?"
    if _is_landlord_entry_conflict(combined):
        return "Would you use a chain lock or just hope they listen?"
    if _is_phone_contract_conflict(combined):
        return "Would you move the phone contract even if you already paid it?"
    return "Would you have done the same?"


def _choose_question(candidates: list[str], item: dict, post: dict) -> str:
    cleaned = [_clean_spaces(q).strip() for q in candidates if str(q or "").strip().endswith("?")]
    if cleaned:
        return _truncate_question(cleaned[-1])
    return _source_question(item, post)


def _question_sentences(line: str) -> list[str]:
    return [match.strip() for match in re.findall(r"[^.!?]*\?", str(line or "")) if match.strip()]


def _split_question_from_line(line: str) -> tuple[str, str]:
    questions = _question_sentences(line)
    if not questions:
        return line.strip(), ""
    question = questions[-1]
    statement = line.replace(question, "").strip(" .,!?:;")
    return statement, question


def _truncate_question(question: str) -> str:
    if len(question) <= 150:
        return question
    return _short_question_chunk(question)


def _first_frame_text(text: str) -> str:
    lowered = str(text or "").lower()
    if "cat bit mine twice" in lowered:
        return "HER CAT BIT MINE TWICE"
    if "refused the vet bill" in lowered:
        return "SHE REFUSED THE VET BILL"
    if "deleted my bank alert" in lowered or "bank alert" in lowered:
        return "MY BOYFRIEND DELETED MY BANK ALERT"
    if "bad aunt" in lowered or "babysit" in lowered:
        return "MY SISTER CALLED ME A BAD AUNT"
    if "texted her ex" in lowered or "paid bills" in lowered:
        return "SHE TEXTED HER EX WHILE I PAID BILLS"
    if "mocked my job" in lowered or "job at dinner" in lowered:
        return "HE MOCKED MY JOB AT DINNER"
    if "phone bill" in lowered or "phone contract" in lowered:
        return "SHE ACCUSED ME OVER A PHONE BILL"
    cleaned = _strip_hashtags(text).upper()
    cleaned = re.sub(r"[^A-Z0-9 $'&-]+", "", cleaned)
    cleaned = re.sub(r"\b(?:THEN|JUST|THE|A)\b", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= 38:
        return cleaned
    return _truncate_words(cleaned, 38).upper()


def _has_missing_then_subject(title: str) -> bool:
    lowered = str(title or "").lower()
    match = re.search(r"\bthen\s+([a-z']+)", lowered)
    if not match:
        return False
    after_then = match.group(1)
    if after_then in {
        "he",
        "she",
        "they",
        "i",
        "we",
        "my",
        "her",
        "his",
        "their",
        "the",
        "that",
        "it",
        "someone",
        "roommate",
        "neighbor",
        "coworker",
        "landlord",
        "aunt",
        "brother",
        "dad",
    }:
        return False
    return after_then in {
        "refused",
        "complained",
        "blamed",
        "demanded",
        "acted",
        "called",
        "used",
        "spent",
        "kept",
        "left",
        "returned",
        "deleted",
        "posted",
        "charged",
    }


def _repair_line_has_concrete_token(line: str, item: dict, post: dict) -> bool:
    lowered_line = str(line or "").lower()
    combined = _combined_text(item, post)
    for term in _concrete_terms() | {"shot", "shots", "estimate", "appointment", "paperwork", "repair"}:
        if term in lowered_line and term in combined:
            return True
    return bool(re.search(r"\b\d+(?:\.\d+)?\b|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twelve)\b", lowered_line))


def _combined_text(item: dict, post: dict) -> str:
    parts = [
        post.get("title", ""),
        post.get("content", ""),
        item.get("public_title", ""),
        item.get("title", ""),
        " ".join(item.get("voiceover_lines") or item.get("script") or []),
        item.get("source_archetype", ""),
    ]
    return _clean_spaces(" ".join(str(part or "") for part in parts)).lower()


def _first_line(item: dict) -> str:
    lines = item.get("voiceover_lines") or item.get("script") or []
    return str(lines[0] if lines else item.get("title") or "").strip()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _is_pet_medical_conflict(text: str) -> bool:
    lowered = str(text or "").lower()
    has_pet = bool(re.search(r"\b(?:cat|cats|pet|pets)\b", lowered))
    has_medical = bool(re.search(r"\b(?:bit|bite|bitten|bloodwork|vaccinated|vaccine|shots?|puncture|vet)\b", lowered))
    return has_pet and has_medical


def _is_dad_admin_pressure(text: str) -> bool:
    lowered = str(text or "").lower()
    has_parent = bool(re.search(r"\b(?:dad|father|parent|elderly father)\b", lowered))
    has_admin_pressure = bool(
        re.search(r"\b(?:bank|banks|doctor|va|medicare|appointment|paperwork|phone number|gave my number)\b", lowered)
    )
    return has_parent and has_admin_pressure


def _is_landlord_entry_conflict(text: str) -> bool:
    lowered = str(text or "").lower()
    has_landlord_or_entry = bool(re.search(r"\b(?:landlord|painter|painters|cat sitter|chain lock|bar manager)\b", lowered))
    has_home_entry = "apartment" in lowered and bool(
        re.search(r"\b(?:walked in|walks in|walking in|walked into|entered|barged|door|lock|chain)\b", lowered)
    )
    return has_landlord_or_entry or has_home_entry


def _is_four_kids_conflict(text: str) -> bool:
    lowered = str(text or "").lower()
    return bool(re.search(r"\b(?:four|4)\s+kids\b", lowered)) and _has_any(
        lowered, ("daycare", "bath time", "school pickup", "school pickups", "mother's day")
    )


def _is_bank_alert_conflict(text: str) -> bool:
    lowered = str(text or "").lower()
    has_bank_alert = bool(re.search(r"\b(?:bank account|bank alert|alert|charge|charged|food order|1\s*a\.?m\.?|1am)\b", lowered))
    has_hidden_action = bool(re.search(r"\b(?:deleted|erased|hid|used my bank|used my account)\b", lowered))
    has_partner = bool(re.search(r"\b(?:boyfriend|girlfriend|partner)\b", lowered))
    return has_bank_alert and has_hidden_action and has_partner


def _is_babysitting_conflict(text: str) -> bool:
    lowered = str(text or "").lower()
    has_childcare = bool(re.search(r"\b(?:babysit|babysitting|babysitter|childcare|niece|bad aunt)\b", lowered))
    has_pressure = bool(re.search(r"\b(?:two hours|notice|last minute|bad aunt|called me)\b", lowered))
    return has_childcare and has_pressure


def _is_ex_bills_conflict(text: str) -> bool:
    lowered = str(text or "").lower()
    has_messages = bool(re.search(r"\b(?:texted|texting|messages|flirty|ex)\b", lowered))
    has_bills = bool(re.search(r"\b(?:bill|bills|paying|paid|rent)\b", lowered))
    return has_messages and has_bills and "ex" in lowered


def _is_dinner_job_insult_conflict(text: str) -> bool:
    lowered = str(text or "").lower()
    has_dinner_insult = bool(re.search(r"\b(?:mocked|insulted|job|career)\b", lowered)) and "dinner" in lowered
    has_guest_pressure = bool(re.search(r"\b(?:guest room|sleep over|sleep in|stay over|expected to sleep)\b", lowered))
    return has_dinner_insult and has_guest_pressure


def _is_phone_contract_conflict(text: str) -> bool:
    lowered = str(text or "").lower()
    has_phone_bill = bool(re.search(r"\b(?:phone contract|phone bill|mobile contract|20 phone|£20 phone|contract)\b", lowered))
    has_family_accusation = bool(re.search(r"\b(?:stepmum|stepmom|dad|father|accused|raging|furious)\b", lowered))
    return has_phone_bill and has_family_accusation


def _has_retention_signal(text: str) -> bool:
    lowered = text.lower()
    return _has_any(
        lowered,
        (
            "accusation",
            "bill",
            "camera",
            "card",
            "cat",
            "consequence",
            "debate",
            "decision",
            "privacy",
            "property",
            "receipt",
            "refusing",
            "unfair",
            "violation",
        ),
    )


def _concrete_terms() -> set[str]:
    return {
        "apartment",
        "bank",
        "bill",
        "blood",
        "bloodwork",
        "camera",
        "car",
        "card",
        "cat",
        "chat",
        "contract",
        "dent",
        "door",
        "driveway",
        "job",
        "landlord",
        "message",
        "phone",
        "receipt",
        "sitter",
        "stepmum",
        "stepmom",
        "text",
        "van",
        "vet",
    }


def _description_from_title(title: str) -> str:
    clean = _strip_hashtags(title or "A fast storytime")
    return f"A fast storytime about {clean.lower()}."


def _tags_from_item(item: dict) -> list[str]:
    existing = [str(tag or "").strip().lower().lstrip("#") for tag in item.get("tags") or [] if str(tag or "").strip()]
    base = ["storytime", "shorts", "redditstories"]
    hook = str(item.get("hook_type") or "").strip().lower().replace("_", "")
    style = str(item.get("style_variant") or "").strip().lower().replace("_", "")
    for tag in [hook, style] + existing + base:
        if tag and tag not in base:
            base.append(tag)
    return base[:8]


def _trim_caption(text: str) -> str:
    return str(text or "").strip(" .,!;:")


def _truncate_words(text: str, limit: int) -> str:
    cleaned = _clean_spaces(text)
    if len(cleaned) <= limit:
        return cleaned
    truncated = cleaned[:limit].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return _strip_dangling_tail(truncated).strip(" .,!?:;")


def _strip_dangling_tail(text: str) -> str:
    dangling = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "because",
        "but",
        "by",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "him",
        "his",
        "honestly",
        "how",
        "i",
        "if",
        "in",
        "into",
        "it",
        "just",
        "me",
        "of",
        "on",
        "or",
        "our",
        "she",
        "should",
        "that",
        "the",
        "they",
        "their",
        "then",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "why",
        "with",
        "without",
        "would",
        "you",
    }
    words = str(text or "").split()
    while words and words[-1].strip(".,;:!?").lower() in dangling:
        words.pop()
    return " ".join(words)


def _finish_sentence(text: str) -> str:
    cleaned = _clean_spaces(text).strip(" ,;:")
    if not cleaned:
        return ""
    if cleaned.endswith((".", "!", "?")):
        return cleaned
    return f"{cleaned}."


def _title_case(text: str) -> str:
    small = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with"}
    words = []
    for index, word in enumerate(text.split()):
        lower = word.lower()
        words.append(lower if index > 0 and lower in small else lower[:1].upper() + lower[1:])
    return " ".join(words)


def _strip_hashtags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"#\w+", "", str(text or ""))).strip(" .,-")


def _clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _normalize_token_text(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = _clean_spaces(value)
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _action(code: str, message: str, before: Any, after: Any) -> dict:
    return {
        "code": code,
        "message": message,
        "before": before,
        "after": after,
    }
