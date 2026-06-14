from generator.text.story_card import build_story_card, story_card_hard_errors, story_card_status


def test_story_card_accepts_concrete_receipt_backed_source():
    post = {
        "id": "driveway-camera",
        "title": "My Neighbor Parked In My Driveway And Called Me Petty",
        "content": (
            "My neighbor parked in my driveway before sunrise without asking. "
            "The door camera showed his car blocking the entrance for six hours. "
            "I texted him once, then sent the timestamp in the building chat when he denied it. "
            "After that I put up a private parking sign and stopped answering his late-night messages. "
            "Was I wrong to post the clip when he made it public first?"
        ),
        "source_provider": "reddit",
        "source_url": "https://reddit.test/driveway-camera",
    }

    card = build_story_card(post)

    assert story_card_status(card) == "accepted"
    assert story_card_hard_errors(card) == []
    assert card.crossed_line
    assert card.narrator_decision
    assert "camera" in card.visual_objects
    assert card.scriptability_score >= 4


def test_story_card_rejects_vague_source_before_script_generation():
    post = {
        "id": "vague",
        "title": "A confusing family disagreement",
        "content": (
            "We had a long emotional disagreement at home. "
            "Everyone felt differently and nobody was sure what the real issue was. "
            "There were no texts, bills, photos, receipts, timestamps, or specific actions. "
            "What should I do?"
        ),
        "source_provider": "reddit",
        "source_url": "https://reddit.test/vague",
    }

    card = build_story_card(post)
    errors = story_card_hard_errors(card)

    assert story_card_status(card) == "rejected"
    assert errors
    assert any(error.startswith("story_card_") for error in errors)
