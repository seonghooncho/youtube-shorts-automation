from generator.text.content_gate import caption_chunks_align_with_tts_text, ensure_content_gate
from generator.text.generate_script import ReturnScript
from generator.text.generate_scripts_from_filtered import validate_and_parse_metadata
from generator.text.metadata_repair import repair_metadata


def _cat_post():
    content = (
        "My roommate's cat bit mine twice in two weeks. The first bite left a scab, "
        "then I found a second fresh puncture while holding my cat. I asked if her cat "
        "was vaccinated, and she admitted three shots were expired. She only sent me "
        "the new appointment date. I texted that she needed to pay for my cat's bloodwork, "
        "but she brushed it off and then said she was not renewing our lease. The vet "
        "recommended bloodwork because the bite came from an under-vaccinated cat. I kept "
        "the text where she admitted the expired shots and the appointment date she sent "
        "instead of apologizing or offering to cover the bill."
    )
    return {
        "id": "cat-bite",
        "title": "AITA for asking my roommate to pay after her cat bit mine?",
        "content": content,
        "content_char_count": len(content),
        "content_word_count": len(content.split()),
        "source_provider": "pullpush",
        "source_url": "https://reddit.test/r/AITA/comments/cat-bite",
    }


def _short_cat_metadata():
    lines = [
        "My roommate's cat bit mine again, twice in two weeks.",
        "First it was just a scab, but then I spotted a second fresh puncture during cuddle time.",
        "I asked my roommate if her cat was actually vaccinated, and she stalled before admitting three shots were expired.",
        "She did not even apologize, just told me the new appointment date.",
        "After the second bite, I texted that I needed her to pay for my cat's bloodwork.",
        "She brushed it off and then told me she was not renewing our lease.",
        "Now I am stuck with the bill because her cat was not vaccinated.",
        "Should my roommate have paid for the bloodwork?",
    ]
    return {
        "title": "AITA for asking my roommate to pay",
        "description": "A roommate pet story.",
        "tags": ["storytime"],
        "voice": "neutral",
        "visual_keywords": ["story"],
        "first_frame_text": "AITA CAT STORY THAT IS FAR TOO LONG",
        "opening_visual_query": "story",
        "visual_beat_queries": [],
        "hook_type": "pet_medical_bill",
        "first_2_seconds": lines[0],
        "source_summary": "A roommate's cat bit the narrator's cat twice, and the roommate admitted expired vaccines.",
        "story_beats": [
            "The roommate's cat bit the narrator's cat twice.",
            "The second bite left a fresh puncture.",
            "The roommate admitted expired shots.",
            "The narrator asked her to pay for bloodwork.",
        ],
        "adaptation_strategy": "Compressed the source into the two bites, expired shots, bloodwork request, and lease fallout.",
        "retention_angle": "A pet injury and bloodwork bill create a concrete roommate debate.",
        "turning_point": "The roommate admitted three shots were expired after the second bite.",
        "payoff_line": "Now I am stuck with the bill because her cat was not vaccinated.",
        "viewer_question": lines[-1],
        "marketability_score": 5,
        "retention_risk": "The story could feel small, so the script centers on two bites and expired shots.",
        "cut_plan": ["story"],
        "bg_strategy": "hybrid",
        "rewrite_notes": "Opened on the second bite and bloodwork bill.",
        "style_variant": "receipt_reveal",
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "caption_chunks": ["A cat injury summary", "Should she pay?"],
    }


def test_repair_metadata_fixes_mechanical_fields_and_length():
    metadata, actions = repair_metadata(_short_cat_metadata(), _cat_post())

    assert len(" ".join(metadata["voiceover_lines"])) >= 650
    assert metadata["length_repair_status"] == "added_source_grounded_line"
    assert metadata["public_title"] == "Her Cat Bit Mine Twice, Then She Refused To Pay"
    assert metadata["first_frame_text"] == "HER CAT BIT MINE TWICE"
    assert metadata["opening_visual_query"] == "cat vet clinic"
    assert caption_chunks_align_with_tts_text(metadata)[0] is True
    assert metadata["caption_repair_status"] == "rebuilt_from_voiceover"
    assert {action["code"] for action in actions} >= {
        "length_repair_line_added",
        "public_title_rebuilt",
        "caption_chunks_rebuilt",
    }


def test_validate_and_parse_metadata_repairs_before_content_gate(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    result = ReturnScript.model_validate(_short_cat_metadata())

    metadata = validate_and_parse_metadata(result, 0, _cat_post())

    assert metadata["script_char_count"] >= 650
    assert metadata["caption_repair_status"] == "rebuilt_from_voiceover"
    ensure_content_gate(metadata, stage="script_accept")


def test_repair_title_does_not_map_childcare_bills_to_restaurant_card():
    lines = [
        "He spends all week away while I do everything for four kids.",
        "I cover daycare runs, meals, bath time, and school pickups alone.",
        "He pays rent, but I pay for food, bills, and every extra expense.",
        "Mother's Day came, and all I got was a text at 9 PM.",
        "Would you stay with a partner like this?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA? Am I asking the impossible?",
        "viewer_question": "Would you stay with a partner like this?",
    }
    post = {
        "title": "AITA? Am I asking the impossible?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] == "He Left Me With Four Kids"
    assert repaired["opening_visual_query"] == "four kids home childcare"


def test_cat_sitter_does_not_trigger_pet_medical_title():
    lines = [
        "I caught my landlord barging into my apartment without warning.",
        "My cat sitter texted that painters walked in while I was away.",
        "Now I want to chain the door shut unless they warn me first.",
        "Would you lock out your landlord?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "WIBTA for locking out landlord out of my apartment with a chain?",
        "viewer_question": "Would you lock out your landlord?",
    }
    post = {
        "title": "WIBTA for locking out landlord out of my apartment with a chain?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] == "My Landlord Walked Into My Apartment"
    assert repaired["opening_visual_query"] == "apartment landlord door"


def test_cat_litter_does_not_override_dad_family_pressure_title():
    lines = [
        "My dad gave my number to every bank and old neighbor.",
        "He ran out of milk and cat litter so I would drive across town.",
        "When I suggested the VA ride, he screamed at me over the phone.",
        "Would you stop helping him?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA if I want to quit helping my elderly father?",
        "viewer_question": "Would you stop helping him?",
    }
    post = {
        "title": "AITA if I want to quit helping my elderly father?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] == "My Dad Gave My Number To Every Bank"
    assert repaired["opening_visual_query"] == "phone bank paperwork"


def test_van_damage_mapping_wins_over_family_terms():
    lines = [
        "My daughter dented my van after I told her to only drive to work.",
        "My wife said I was too harsh for asking her to pay toward repairs.",
        "Would you make her pay for the damage?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA for making my daughter pay for wrecking my van?",
        "viewer_question": "Would you make her pay for the damage?",
    }
    post = {
        "title": "AITA for making my daughter pay for wrecking my van?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] == "My Daughter Dented My Van"
    assert repaired["opening_visual_query"] == "dented van parking lot"


def test_caption_repair_keeps_readable_phrases_not_single_word_fragments():
    lines = [
        "My dad called me selfish because I would not drop everything for his appointment.",
        "He handed out my number to his bank, the VA, and old neighbors.",
        "Now he is waiting for me to cave instead of calling the VA ride.",
        "Do you think I should cut him off?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA if I want to quit helping my elderly father?",
        "viewer_question": "Do you think I should cut him off?",
        "caption_chunks": ["dad summary", "bank"],
    }
    post = {
        "title": "AITA if I want to quit helping my elderly father?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["caption_chunks"][:3] == [
        "My dad called me selfish",
        "He handed out my number to his bank",
        "Now he is waiting for me to cave instead",
    ]
    assert all(len(chunk) <= 42 for chunk in repaired["caption_chunks"])
    assert all(len(chunk.split()) >= 4 for chunk in repaired["caption_chunks"][:-1])


def test_length_repair_does_not_add_generic_filler_without_concrete_source():
    lines = [
        "Something awkward happened after a small disagreement.",
        "I answered calmly, but the other person kept pushing.",
        "The conversation kept going until I felt worn down.",
        "I finally said no because it did not feel right.",
        "Now I am wondering if I handled it badly.",
        "Would you have done the same?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "A vague story",
        "viewer_question": "Would you have done the same?",
    }
    post = {
        "title": "A vague story",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, actions = repair_metadata(metadata, post)

    assert "length_repair_status" not in repaired
    assert all(action["code"] != "length_repair_line_added" for action in actions)
    assert "not just a misunderstanding" not in " ".join(repaired["voiceover_lines"])


def test_missing_subject_then_title_is_repaired():
    metadata = _short_cat_metadata() | {
        "public_title": "Her Cat Bit Mine Twice, Then Refused To Pay",
        "title": "Her Cat Bit Mine Twice, Then Refused To Pay",
        "first_frame_text": "HER CAT BIT MINE TWICE REFUSED TO PAY",
    }

    repaired, _actions = repair_metadata(metadata, _cat_post())

    assert repaired["public_title"] == "Her Cat Bit Mine Twice, Then She Refused To Pay"
    assert repaired["first_frame_text"] == "HER CAT BIT MINE TWICE"


def test_overpacked_vet_first_frame_is_simplified():
    metadata = _short_cat_metadata() | {
        "public_title": "She Refused The Vet Bill After The Bite",
        "title": "She Refused The Vet Bill After The Bite",
        "first_frame_text": "HER CAT BIT MINE TWICE REFUSED TO PAY",
    }

    repaired, _actions = repair_metadata(metadata, _cat_post())

    assert repaired["first_frame_text"] in {"HER CAT BIT MINE TWICE", "SHE REFUSED THE VET BILL"}
