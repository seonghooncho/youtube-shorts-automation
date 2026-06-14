from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from generator.text.script_quality import build_source_profile, source_reject_reason_for_marketability


class StoryCard(BaseModel):
    source_id: str = ""
    protagonist: str = "narrator"
    opponent: str = ""
    relationship: str = ""
    setting: str = ""
    crossed_line: str = ""
    escalation: str = ""
    receipt_or_proof: str = ""
    stakes: str = ""
    narrator_decision: str = ""
    consequence: str = ""
    comment_question: str = ""
    visual_objects: list[str] = Field(default_factory=list)
    source_archetype: str = ""
    story_strength_score: int = Field(1, ge=1, le=5)
    scriptability_score: int = Field(1, ge=1, le=5)
    debate_score: int = Field(1, ge=1, le=5)
    visual_score: int = Field(1, ge=1, le=5)
    risk_score: int = Field(1, ge=1, le=5)
    skip_reason: str = ""


def build_story_card(post: dict) -> StoryCard:
    source = build_source_profile(post or {})
    combined = f"{source.title}. {source.content}".strip()
    lowered = combined.lower()
    opponent = _first_term(lowered, _OPPONENT_TERMS)
    relationship = opponent or _relationship_from_title(source.title)
    setting = _first_term(lowered, _SETTING_TERMS)
    crossed_line = _best_sentence(combined, _CROSSED_LINE_TERMS) or source.title
    escalation = _best_sentence(combined, _ESCALATION_TERMS)
    receipt = _best_sentence(combined, _RECEIPT_TERMS)
    decision = _best_sentence(combined, _DECISION_TERMS)
    consequence = _best_sentence(combined, _CONSEQUENCE_TERMS)
    question = _last_question(combined) or _question_from_decision(decision, crossed_line)
    visual_objects = _visual_objects(lowered)
    risk_score = 5 if source_reject_reason_for_marketability(post or {}) else (4 if _has_risk_terms(lowered) else 1)
    concrete_count = sum(1 for term in _CONCRETE_TERMS if term in lowered)
    has_conflict = bool(crossed_line and any(term in crossed_line.lower() for term in _CROSSED_LINE_TERMS))
    story_strength = _score(has_conflict, bool(opponent), bool(escalation or consequence), concrete_count >= 2, bool(question))
    scriptability = _score(len(source.content.split()) >= 90, bool(crossed_line), bool(decision), bool(visual_objects), concrete_count >= 2)
    debate = _score(bool(question), any(term in lowered for term in _DEBATE_TERMS), bool(decision), bool(consequence), bool(opponent))
    visual_score = min(5, max(1, len(visual_objects)))
    skip_reason = ""
    if risk_score >= 5:
        skip_reason = "story_card_high_risk"
    elif not crossed_line:
        skip_reason = "story_card_missing_crossed_line"
    elif not opponent:
        skip_reason = "story_card_missing_opponent"
    elif not decision:
        skip_reason = "story_card_missing_narrator_decision"
    elif not question:
        skip_reason = "story_card_missing_comment_question"
    elif scriptability < 4:
        skip_reason = "story_card_low_scriptability"
    elif story_strength < 4:
        skip_reason = "story_card_low_story_strength"

    return StoryCard(
        source_id=str(post.get("id") or ""),
        protagonist="narrator",
        opponent=opponent,
        relationship=relationship,
        setting=setting,
        crossed_line=_clean(crossed_line),
        escalation=_clean(escalation),
        receipt_or_proof=_clean(receipt),
        stakes=_stakes_from_text(lowered),
        narrator_decision=_clean(decision),
        consequence=_clean(consequence),
        comment_question=_clean(question),
        visual_objects=visual_objects,
        source_archetype=str(post.get("source_archetype") or _archetype(lowered)),
        story_strength_score=story_strength,
        scriptability_score=scriptability,
        debate_score=debate,
        visual_score=visual_score,
        risk_score=risk_score,
        skip_reason=skip_reason,
    )


def story_card_hard_errors(card: StoryCard) -> list[str]:
    errors: list[str] = []
    if not card.crossed_line:
        errors.append("story_card_missing_crossed_line")
    if not card.opponent:
        errors.append("story_card_missing_opponent")
    if not card.narrator_decision:
        errors.append("story_card_missing_narrator_decision")
    if not card.comment_question:
        errors.append("story_card_missing_comment_question")
    if card.scriptability_score < 4:
        errors.append("story_card_low_scriptability")
    if card.story_strength_score < 4:
        errors.append("story_card_low_story_strength")
    if card.risk_score >= 5:
        errors.append("story_card_high_risk")
    if card.skip_reason and card.skip_reason not in errors:
        errors.append(card.skip_reason)
    return errors


def story_card_status(card: StoryCard) -> Literal["accepted", "rejected"]:
    return "rejected" if story_card_hard_errors(card) else "accepted"


_OPPONENT_TERMS = (
    "roommate",
    "neighbor",
    "coworker",
    "manager",
    "landlord",
    "aunt",
    "brother",
    "sister",
    "dad",
    "mom",
    "friend",
    "owner",
    "tenant",
)
_SETTING_TERMS = (
    "apartment",
    "driveway",
    "office",
    "restaurant",
    "group chat",
    "building chat",
    "laundry",
    "storage unit",
    "car",
    "vet",
    "bank",
)
_CROSSED_LINE_TERMS = (
    "accused",
    "bit",
    "blamed",
    "charged",
    "demanded",
    "dented",
    "ignored",
    "parked",
    "posted",
    "refused",
    "spent",
    "took",
    "used",
    "without asking",
    "without permission",
)
_ESCALATION_TERMS = ("then", "after", "again", "second", "instead", "worse", "brushed", "told everyone", "group chat")
_RECEIPT_TERMS = ("receipt", "screenshot", "camera", "text", "message", "bill", "estimate", "timestamp", "photo", "app")
_DECISION_TERMS = ("refused", "stopped", "asked", "told", "sent", "disputed", "locked", "blocked", "reported")
_CONSEQUENCE_TERMS = ("now", "then", "after", "because", "stuck", "bill", "lease", "friends", "family", "manager")
_DEBATE_TERMS = ("should", "wrong", "refused", "pay", "cover", "overreact", "petty", "fair")
_CONCRETE_TERMS = _SETTING_TERMS + _RECEIPT_TERMS + ("card", "cat", "bloodwork", "package", "keys", "deposit")
_RISK_TERMS = ("minor", "underage", "teen", "sexual", "nude", "police", "lawsuit", "weapon")


def _score(*signals: bool) -> int:
    return max(1, min(5, 1 + sum(1 for signal in signals if signal)))


def _first_term(text: str, terms: tuple[str, ...]) -> str:
    for term in terms:
        if term in text:
            return term
    return ""


def _relationship_from_title(title: str) -> str:
    lowered = str(title or "").lower()
    return _first_term(lowered, _OPPONENT_TERMS)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", str(text or "")) if part.strip()]


def _best_sentence(text: str, terms: tuple[str, ...]) -> str:
    for sentence in _sentences(text):
        lowered = sentence.lower()
        if any(term in lowered for term in terms):
            return sentence
    return ""


def _last_question(text: str) -> str:
    for sentence in reversed(_sentences(text)):
        if sentence.endswith("?"):
            return sentence
    return ""


def _question_from_decision(decision: str, crossed_line: str) -> str:
    basis = (decision or crossed_line or "").lower()
    if "pay" in basis or "bill" in basis or "charged" in basis:
        return "Should I have refused to pay?"
    if "park" in basis or "driveway" in basis:
        return "Would you have made them move too?"
    if "cat" in basis or "vet" in basis:
        return "Should they have covered the vet bill?"
    return "Would you have done the same?"


def _visual_objects(text: str) -> list[str]:
    objects: list[str] = []
    for term in _CONCRETE_TERMS:
        if term in text and term not in objects:
            objects.append(term)
        if len(objects) >= 6:
            break
    return objects


def _stakes_from_text(text: str) -> str:
    if any(term in text for term in ("bill", "card", "pay", "deposit", "bank")):
        return "money"
    if any(term in text for term in ("car", "driveway", "apartment", "storage", "package")):
        return "property"
    if any(term in text for term in ("manager", "office", "coworker")):
        return "workplace reputation"
    if any(term in text for term in ("cat", "vet", "bloodwork")):
        return "pet medical bill"
    return "social pressure"


def _archetype(text: str) -> str:
    if "cat" in text or "vet" in text:
        return "pet_medical_bill"
    if "card" in text or "bill" in text or "receipt" in text:
        return "money_pressure"
    if "driveway" in text or "neighbor" in text:
        return "neighbor_property"
    if "office" in text or "manager" in text:
        return "workplace_accusation"
    return "boundary_conflict"


def _has_risk_terms(text: str) -> bool:
    return any(term in text for term in _RISK_TERMS)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:260]
