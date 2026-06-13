from generator.text.content_gate import (
    caption_chunks_align_with_tts_text,
    caption_quality_reason,
    evaluate_content_gate,
    normalize_narration_fields,
    opening_visual_query_relevance_reason,
)


def _safe_item(**overrides):
    lines = [
        "He parked in my driveway before sunrise and left my guests on the street.",
        "I texted once because the door camera showed his car across the whole entrance.",
        "He replied in the building chat that I was being petty over empty pavement.",
        "Then I posted the camera timestamp and the message where he admitted it was his car.",
        "My neighbor said screenshots made him look bad in front of everyone on our block.",
        "I told him the driveway was not extra parking for his errands or deliveries.",
        "After that I put up a private parking sign and stopped answering late-night texts.",
        "Was I wrong to post the clip when he made it public first?",
    ]
    item = {
        "id": "safe-1",
        "title": "He Parked In My Driveway, Then Called Me Petty #shorts #story",
        "public_title": "He Parked In My Driveway, Then Called Me Petty",
        "script": lines,
        "voiceover_lines": lines,
        "tts_text": " ".join(lines),
        "caption_chunks": [
            "He parked in my driveway",
            "The door camera showed his car",
            "He replied in the building chat",
            "I posted the timestamp",
            "Was I wrong to post the clip?",
        ],
        "first_frame_text": "HE PARKED IN MY DRIVEWAY",
        "opening_visual_query": "parked car in driveway",
        "visual_beat_queries": [
            {"beat": "hook", "query": "parked car in driveway"},
            {"beat": "receipt", "query": "door camera car timestamp"},
        ],
        "style_variant": "neighbor_dispute",
        "script_fingerprint": "safe-fingerprint",
        "predicted_retention_score": 8,
        "predicted_rewatch_score": 8,
        "predicted_comment_score": 7,
        "predicted_clarity_score": 8,
        "predicted_ai_smell_score": 3,
        "critic_scores": {
            "ai_smell_score": 3,
            "native_naturalness_score": 8,
            "retention_score": 8,
            "specificity_score": 8,
        },
    }
    item.update(overrides)
    return item


def test_content_gate_rejects_synthetic_source_by_default(monkeypatch):
    monkeypatch.delenv("SCRIPT_ALLOW_SYNTHETIC_SOURCES", raising=False)
    monkeypatch.delenv("ALLOW_SYNTHETIC_IN_PRODUCTION", raising=False)

    result = evaluate_content_gate(_safe_item(source_provider="synthetic"))

    assert result["ok"] is False
    assert "synthetic_source_not_allowed" in result["hard_errors"]


def test_content_gate_rejects_local_template_without_upload_override(monkeypatch):
    monkeypatch.setenv("SCRIPT_LOCAL_FALLBACK_ENABLED", "1")
    monkeypatch.delenv("ALLOW_LOCAL_TEMPLATE_UPLOAD", raising=False)

    result = evaluate_content_gate(_safe_item(generation_fallback="local_template"))

    assert result["ok"] is False
    assert "local_template_fallback_not_allowed" in result["hard_errors"]


def test_content_gate_rejects_low_critic_and_predicted_scores():
    item = _safe_item(
        critic_scores={
            "ai_smell_score": 4,
            "native_naturalness_score": 7,
            "retention_score": 7,
            "specificity_score": 7,
        },
        predicted_retention_score=7,
        predicted_clarity_score=7,
        predicted_ai_smell_score=4,
        predicted_comment_score=6,
    )

    result = evaluate_content_gate(item)

    assert result["ok"] is False
    assert "critic_ai_smell_score" in result["hard_errors"]
    assert "predicted_retention_score" in result["hard_errors"]
    assert "predicted_comment_score" in result["hard_errors"]


def test_content_gate_rejects_bad_public_title_markers():
    aita = evaluate_content_gate(_safe_item(public_title="AITA for refusing to move my car"))
    viral = evaluate_content_gate(_safe_item(title="He Parked In My Driveway #viral"))

    assert "aita_title" in aita["hard_errors"]
    assert "viral_hashtag_not_allowed" in viral["hard_errors"]


def test_narration_fields_derive_from_script():
    item = {
        "script": [
            "My roommate put twelve dinners on my card.",
            "Would you have disputed the charge?",
        ]
    }

    normalized = normalize_narration_fields(item)

    assert normalized["voiceover_lines"] == item["script"]
    assert normalized["tts_text"] == "My roommate put twelve dinners on my card. Would you have disputed the charge?"
    assert all(len(chunk) <= 42 for chunk in normalized["caption_chunks"])
    assert normalized["caption_chunks"][-1].endswith("?")


def test_narration_normalization_repairs_bad_generated_caption_chunks():
    item = {
        "script": [
            "My dad gave my number to every single bank and neighbor he knows.",
            "Now my phone never stops buzzing with his calls and other people’s problems.",
            "Am I wrong to finally walk away from my own dad?",
        ],
        "caption_chunks": [
            "My dad handed my phone number to every single bank and neighbor",
            "Everyone kept calling about his problems",
            "Should I stop helping him?",
        ],
    }

    normalized = normalize_narration_fields(item)

    assert normalized["caption_chunks_repaired"] is True
    assert all(len(chunk) <= 42 for chunk in normalized["caption_chunks"])
    assert caption_chunks_align_with_tts_text(normalized)[0] is True
    assert normalized["caption_chunks"][-1].endswith("?")


def test_reddit_item_without_source_context_fails_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ALLOW_MISSING_SOURCE_CONTEXT", raising=False)

    result = evaluate_content_gate(_safe_item(source_provider="reddit", source_url="https://reddit.test/post"))

    assert result["ok"] is False
    assert "missing_source_context" in result["hard_errors"]


def test_pullpush_item_without_source_context_fails_in_production(monkeypatch):
    monkeypatch.setenv("YT_ENV", "production")
    monkeypatch.delenv("ALLOW_MISSING_SOURCE_CONTEXT", raising=False)

    result = evaluate_content_gate(_safe_item(source_provider="pullpush", source_url="https://reddit.test/post"))

    assert result["ok"] is False
    assert "missing_source_context" in result["hard_errors"]


def test_unknown_provider_fails_in_production_unless_allowed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ALLOW_UNKNOWN_SOURCE_PROVIDER", raising=False)

    blocked = evaluate_content_gate(_safe_item(source_provider=""))
    assert "unknown_source_provider" in blocked["hard_errors"]

    monkeypatch.setenv("ALLOW_UNKNOWN_SOURCE_PROVIDER", "1")
    allowed = evaluate_content_gate(_safe_item(source_provider=""))
    assert "unknown_source_provider" not in allowed["hard_errors"]


def test_missing_source_context_override_allows_real_provider(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_MISSING_SOURCE_CONTEXT", "1")

    result = evaluate_content_gate(_safe_item(source_provider="reddit", source_url="https://reddit.test/post"))

    assert "missing_source_context" not in result["hard_errors"]


def test_caption_retention_policy_rejects_generic_and_long_chunks():
    result = evaluate_content_gate(
        _safe_item(
            caption_chunks=[
                "The boundary was simple",
                "This caption is far too long for the current Shorts caption style",
                "Was I wrong to post the clip?",
            ]
        )
    )

    assert any(error.startswith("first_caption_hook") for error in result["hard_errors"])
    assert any(error.startswith("caption_chunk_too_long") for error in result["hard_errors"])
    assert any(error.startswith("generic_caption_chunk") for error in result["hard_errors"])


def test_caption_chunks_must_align_with_tts_text():
    exact = _safe_item(caption_chunks=["He parked in my driveway", "Was I wrong to post the clip?"])
    paraphrase = _safe_item(caption_chunks=["His car stayed there all morning", "Was I wrong to post the clip?"])
    final_question_mismatch = _safe_item(caption_chunks=["He parked in my driveway", "Would you call him out?"])

    assert caption_chunks_align_with_tts_text(exact)[0] is True
    assert caption_chunks_align_with_tts_text(paraphrase)[0] is False
    result = evaluate_content_gate(final_question_mismatch)
    assert any(error.startswith("caption_chunks_not_in_tts_text") for error in result["hard_errors"])


def test_caption_chunks_must_be_near_contiguous(monkeypatch):
    monkeypatch.delenv("CAPTION_CHUNK_MAX_TOKEN_GAP", raising=False)
    exact = _safe_item(caption_chunks=["He parked in my driveway", "Was I wrong to post the clip?"])
    loose = _safe_item(caption_chunks=["He driveway", "Was I wrong to post the clip?"])
    punctuation = _safe_item(caption_chunks=["He parked in my driveway.", "Was I wrong to post the clip?"])

    assert caption_chunks_align_with_tts_text(exact)[0] is True
    assert caption_chunks_align_with_tts_text(punctuation)[0] is True
    ok, reason = caption_chunks_align_with_tts_text(loose)
    assert ok is False
    assert "caption_chunk_not_contiguous" in reason


def test_caption_quality_uses_caption_specific_rules():
    assert caption_quality_reason("His car sat there for six hours", is_first=True) == ""
    assert caption_quality_reason("The door camera showed his car", is_first=True) == ""
    assert caption_quality_reason("My dad gave my number to every bank", is_first=True) == ""
    assert caption_quality_reason("landlord walking right into my apartment", is_first=True) == ""
    assert caption_quality_reason("Things got worse", is_first=True) == "generic_filler"
    assert caption_quality_reason("The boundary was simple", is_first=True) == "generic_filler"


def test_opening_visual_and_first_frame_are_required():
    generic_query = evaluate_content_gate(_safe_item(opening_visual_query="story"))
    long_frame = evaluate_content_gate(_safe_item(first_frame_text="THIS FIRST FRAME TEXT IS FAR TOO LONG FOR SHORTS"))
    card_lines = [
        "My aunt put twelve dinners on my card before I even sat down.",
        "The receipt showed every extra entree under my name.",
        "She told the group chat I was being cheap for asking her to fix it.",
        "I posted the receipt and asked her to pay me back before dessert.",
        "Was I wrong to dispute the dinner bill?",
    ]
    strong_frame = evaluate_content_gate(
        _safe_item(
            script=card_lines,
            voiceover_lines=card_lines,
            tts_text=" ".join(card_lines),
            caption_chunks=["My aunt put twelve dinners on my card", "The receipt showed every extra entree", "Was I wrong to dispute the dinner bill?"],
            public_title="My Aunt Put Twelve Dinners On My Card",
            title="My Aunt Put Twelve Dinners On My Card #shorts #story",
            first_frame_text="12 DINNERS ON MY CARD",
            opening_visual_query="restaurant bill credit card",
        )
    )

    assert "generic_opening_visual_query" in generic_query["hard_errors"]
    assert "first_frame_text_too_long" in long_frame["hard_errors"]
    assert strong_frame["ok"] is True


def test_opening_visual_query_must_match_hook():
    relevant = _safe_item(opening_visual_query="parked car driveway")
    mismatched = _safe_item(opening_visual_query="phone texting")

    assert opening_visual_query_relevance_reason(relevant) == ""
    assert "opening_visual_query_mismatch" in evaluate_content_gate(mismatched)["hard_errors"]


def test_childcare_opening_visual_query_is_not_generic():
    lines = [
        "He left me with four kids while I handled daycare and every bill.",
        "Would you stay with someone like this?",
    ]
    result = evaluate_content_gate(
        _safe_item(
            script=lines,
            voiceover_lines=lines,
            tts_text=" ".join(lines),
            caption_chunks=["He left me with four kids", "Would you stay with someone like this?"],
            public_title="He Left Me With Four Kids",
            title="He Left Me With Four Kids #shorts #story",
            first_frame_text="HE LEFT ME WITH FOUR KIDS",
            opening_visual_query="four kids home childcare",
        )
    )

    assert "generic_opening_visual_query" not in result["hard_errors"]
