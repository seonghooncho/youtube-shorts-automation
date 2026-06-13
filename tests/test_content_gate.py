from generator.text.content_gate import evaluate_content_gate, normalize_narration_fields


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
            "He blamed me in the building chat",
            "I posted the timestamp",
            "Was I wrong to post the clip?",
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
