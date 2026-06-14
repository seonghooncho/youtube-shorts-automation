from generator.text.candidate_scoring import score_candidate
from generator.text.content_gate import evaluate_content_gate, normalize_narration_fields


CAT_VET_LINES = [
    "My roommate’s cat bit mine again—twice in two weeks.",
    "First it was just a scab, but then I spotted a second fresh puncture during cuddle time.",
    "I asked my roommate if her cat was actually vaccinated—she stalled, then finally said three shots were expired.",
    "She didn't even apologize, just told me the new appointment date.",
    "After the second bite, I texted her that I’d need her to pay for my cat's blood test.",
    "She brushed it off and then told me she’s not renewing our lease.",
    "Now I’m stuck with the bill because her cat wasn’t vaccinated.",
    "Should my roommate have paid for the bloodwork?",
]


def _cat_vet_item():
    source = (
        "My roommate's cat bit mine twice in two weeks. The second bite left a fresh puncture. "
        "I asked if her cat was vaccinated, and she admitted in a text that three shots were expired. "
        "The vet recommended bloodwork and wrote possible exposure from an under-vaccinated cat on the estimate. "
        "I sent her the estimate and asked for only the bloodwork portion. She brushed it off and only sent the "
        "new appointment date for her own cat. After I repeated that the bloodwork was because of the expired "
        "shots, she said she was not renewing our lease. I still had the text thread, the estimate, the vet note, "
        "and the date of the second bite when I asked her again."
    )
    item = normalize_narration_fields(
        {
            "id": "cat-vet-582",
            "title": "My Roommate's Cat Bit Mine Twice #shorts #story",
            "public_title": "My Roommate's Cat Bit Mine Twice",
            "script": CAT_VET_LINES,
            "voiceover_lines": CAT_VET_LINES,
            "source_provider": "pullpush",
            "source_url": "https://reddit.test/cat-vet",
            "source_title": "AITA for asking my roommate to pay for my cat's blood test?",
            "source_content_excerpt": source,
            "source_summary": "A roommate's under-vaccinated cat bit the narrator's cat twice, leading to a bloodwork dispute.",
            "story_beats": [
                "The roommate's cat bit the narrator's cat twice.",
                "The roommate admitted three shots were expired.",
                "The vet recommended bloodwork.",
                "The narrator asked for only the bloodwork portion.",
            ],
            "adaptation_strategy": "Compressed the source into the repeat bites, expired-shot text, vet estimate, and bloodwork bill without changing the conflict.",
            "retention_angle": "A repeat pet bite, expired shots, and a vet bill make the roommate dispute immediately debatable.",
            "viewer_question": "Should my roommate have paid for the bloodwork?",
            "first_frame_text": "HER CAT BIT MINE TWICE",
            "opening_visual_query": "cat vet bill",
            "visual_beat_queries": [
                {"beat": "hook", "query": "cat bite vet"},
                {"beat": "receipt", "query": "vet bill estimate text"},
            ],
            "style_variant": "receipt_reveal",
            "script_fingerprint": "cat-vet-fixture",
            "predicted_retention_score": 8,
            "predicted_rewatch_score": 8,
            "predicted_comment_score": 8,
            "predicted_clarity_score": 8,
            "predicted_ai_smell_score": 2,
            "critic_scores": {
                "ai_smell_score": 2,
                "native_naturalness_score": 8,
                "retention_score": 8,
                "specificity_score": 8,
            },
        }
    )
    return item


def test_cat_vet_582_char_script_is_not_hard_rejected_for_legacy_char_count():
    item = _cat_vet_item()
    assert 580 <= len(" ".join(item["script"])) < 650

    gate = evaluate_content_gate(item, stage="script_accept")

    assert gate["ok"] is True
    assert not any("script_too_short" in error for error in gate["hard_errors"])
    assert not any(error.startswith("title_quality") for error in gate["hard_errors"])
    assert item["word_count"] >= 75
    assert item["estimated_seconds"] >= 28


def test_candidate_score_buckets_coherent_cat_vet_script_as_accepted_or_near_miss():
    item = _cat_vet_item()

    scored = score_candidate(item, {"content": item["source_content_excerpt"], "title": item["source_title"]})

    assert scored["hard_blockers"] == []
    assert scored["candidate_score"] >= 68
    assert scored["candidate_bucket"] in {"accepted", "near_miss"}
    assert scored["candidate_score_breakdown"]["conflict_strength"] == 20


def test_hard_blocker_overrides_candidate_score():
    item = _cat_vet_item()
    item["public_title"] = "AITA for asking my roommate to pay?"

    scored = score_candidate(item, {"content": item["source_content_excerpt"], "title": item["source_title"]})

    assert scored["candidate_score"] == 0
    assert scored["candidate_bucket"] == "rejected"
    assert "aita_title" in scored["hard_blockers"]
