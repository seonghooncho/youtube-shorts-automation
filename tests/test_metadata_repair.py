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


def test_bank_alert_story_does_not_trigger_dad_bank_template():
    lines = [
        "At 1 a.m., my boyfriend used my bank account for food, then deleted the bank alert from my phone.",
        "I woke up to the charge because the app still showed the food order and the missing notification.",
        "When I asked, he said it was only a small mistake and I was making money more important than trust.",
        "Then he admitted he erased the alert because he knew I would be upset before work.",
        "I changed my password and told him he could not touch my account again.",
        "Now he says I am treating him like a thief over one late-night order.",
        "Would you stay with someone who deleted a bank alert?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA for being mad about food?",
        "public_title": "AITA for being mad about food?",
        "viewer_question": lines[-1],
    }
    post = {
        "title": "AITA for being mad about food?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] == "My Boyfriend Deleted My Bank Alert"
    assert repaired["first_frame_text"] == "MY BOYFRIEND DELETED MY BANK ALERT"
    assert repaired["opening_visual_query"] == "bank phone alert"
    assert "Dad Gave My Number" not in repaired["public_title"]


def test_babysitting_story_repairs_to_family_pressure_metadata():
    lines = [
        "My sister gave me two hours notice to babysit, then called me a bad aunt when I said no.",
        "She said her dinner plans were already paid for and I was the only person close enough to help.",
        "I told her I had work messages open and could not suddenly take over bedtime for my niece.",
        "Then she sent the family chat a screenshot of my no and left out the two-hour notice.",
        "My mom said I should have helped because family does not need a calendar invite.",
        "I muted the chat and told my sister she needed to ask before making me the backup plan.",
        "Would you say no with only two hours notice?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA for saying no?",
        "public_title": "AITA for saying no?",
        "viewer_question": lines[-1],
    }
    post = {
        "title": "AITA for saying no?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] == "My Sister Called Me A Bad Aunt"
    assert repaired["first_frame_text"] == "MY SISTER CALLED ME A BAD AUNT"
    assert repaired["opening_visual_query"] == "babysitting notice sister"


def test_ex_bills_story_repairs_to_message_and_bill_metadata():
    lines = [
        "She was sending flirty messages to her ex while I was paying most of our bills.",
        "The phone lit up on the counter right after I sent the rent transfer.",
        "I asked why his name was still pinned, and she said I was insecure for reading the preview.",
        "Then I saw she had complained to him that I was boring because I cared about money.",
        "I stopped covering the extra bills until she could explain why he knew more than I did.",
        "I packed a bag and left our apartment before the argument turned into another loop.",
        "Now she says I am punishing her for a harmless conversation.",
        "Would you keep paying the bills after seeing those messages?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA for checking a phone?",
        "public_title": "AITA for checking a phone?",
        "viewer_question": lines[-1],
    }
    post = {
        "title": "AITA for checking a phone?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] == "She Texted Her Ex While I Paid Bills"
    assert repaired["first_frame_text"] == "SHE TEXTED HER EX WHILE I PAID BILLS"
    assert repaired["opening_visual_query"] == "phone messages bills"


def test_dress_party_story_does_not_match_vehicle_damage_template():
    lines = [
        "My boyfriend's sister spilled red wine on my dress at our engagement party.",
        "She laughed it off in front of everyone and said accidents happen at crowded tables.",
        "The receipt showed the dress alteration was paid for two days before the party.",
        "I asked her to cover the cleaning bill, but she told the family I was being dramatic.",
        "Then his mom said I should forgive it because the photos already looked fine.",
        "I sent the cleaning quote and stopped letting them rewrite what happened.",
        "Now they say I ruined the party by caring more about a dress than family.",
        "Would you ask her to pay for the dress cleaning?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA for asking about the dress?",
        "public_title": "AITA for asking about the dress?",
        "viewer_question": lines[-1],
    }
    post = {
        "title": "AITA for asking my boyfriend's sister to pay for my dress?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] != "My Daughter Dented My Van"
    assert repaired["opening_visual_query"] != "dented van parking lot"
    assert "VAN" not in repaired["first_frame_text"]


def test_dinner_job_story_repairs_to_insult_metadata():
    lines = [
        "Her boyfriend mocked my job at dinner, then expected to sleep in our guest room.",
        "He joked that my work was not a real career while I was clearing plates from the table.",
        "My sister laughed until I said he could find a hotel if my house was so unimpressive.",
        "Then he told everyone I was humiliating him over one joke.",
        "I said the guest room was for people who could be polite in the home they wanted to use.",
        "Now my family says I made the whole dinner awkward by refusing to host him.",
        "Would you still let him stay after that dinner?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA for asking him to leave?",
        "public_title": "AITA for asking him to leave?",
        "viewer_question": lines[-1],
    }
    post = {
        "title": "AITA for asking him to leave?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] == "Her Boyfriend Mocked My Job At Dinner"
    assert repaired["first_frame_text"] == "HE MOCKED MY JOB AT DINNER"
    assert repaired["opening_visual_query"] == "dinner table job argument"


def test_phone_contract_story_repairs_to_phone_bill_metadata():
    lines = [
        "My stepmum rang me raging because she thought my dad was secretly paying my £20 phone bill.",
        "Since I was 18, my mobile contract has stayed in my dad's name because his account gets better deals.",
        "Every month I send him the balance, usually about £20, so nobody is covering my bill.",
        "She demanded I switch the contract into my name, so I asked my dad about doing it.",
        "He said it was a credit agreement and he could not just change it over.",
        "Now she says I am out of order for keeping it there, even though my dad wants it left alone.",
        "Am I wrong for leaving my phone contract in my dad's name?",
    ]
    metadata = _short_cat_metadata() | {
        "voiceover_lines": lines,
        "script": lines,
        "tts_text": " ".join(lines),
        "title": "AITA for keeping a phone contract?",
        "public_title": "AITA for keeping a phone contract?",
        "viewer_question": lines[-1],
    }
    post = {
        "title": "AITA for keeping my phone contract in my dad's name?",
        "content": " ".join(lines),
        "source_provider": "pullpush",
    }

    repaired, _actions = repair_metadata(metadata, post)

    assert repaired["public_title"] == "My Stepmum Accused Me Over A Phone Bill"
    assert repaired["first_frame_text"] == "SHE ACCUSED ME OVER A PHONE BILL"
    assert repaired["opening_visual_query"] == "phone bill contract"
    assert repaired["caption_chunks"][0] == "My stepmum rang me raging"
    assert caption_chunks_align_with_tts_text(repaired)[0] is True
