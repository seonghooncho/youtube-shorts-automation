import json

from generator.text.content_gate import ensure_content_gate
from generator.text.generate_script import DraftScript, draft_to_metadata
from generator.text.generate_scripts_from_filtered import EXAMPLE_JSON, call_gpt_generate_script, validate_and_parse_metadata


def _draft_lines():
    return [
        "My roommate's cat bit mine again, twice in two weeks.",
        "The first bite looked small, but the second one left a fresh puncture.",
        "I asked if her cat was vaccinated, and she admitted three shots were expired.",
        "Instead of apologizing, she only sent me the new appointment date.",
        "After the vet suggested bloodwork, I texted that she needed to cover the bill.",
        "She brushed it off and then told me she was not renewing our lease.",
        "Now I have the text about the expired shots and the bloodwork bill in front of me.",
        "Should my roommate have paid for the bloodwork?",
    ]


def _cat_post():
    content = (
        "My roommate's cat bit mine twice in two weeks. The first bite left a scab, "
        "then I found a second fresh puncture while holding my cat. I asked if her cat "
        "was vaccinated, and she admitted three shots were expired. She only sent me "
        "the new appointment date. I texted that she needed to pay for my cat's bloodwork, "
        "but she brushed it off and then said she was not renewing our lease. The vet "
        "recommended bloodwork because the bite came from an under-vaccinated cat. The bite "
        "happened on Sunday night, and the vet office wrote possible exposure from an "
        "under-vaccinated cat on the estimate. I sent her the estimate and the text where "
        "she admitted the shots were expired, then asked for only the bloodwork portion, "
        "not the whole visit."
    )
    return {
        "id": "cat-bite",
        "title": "AITA for asking my roommate to pay for my cats blood test?",
        "content": content,
        "source_provider": "pullpush",
        "source_url": "https://reddit.test/r/AITA/comments/cat-bite",
    }


def _draft():
    return DraftScript(
        title="Roommate Refused My Cat's Bloodwork Bill",
        voice="neutral",
        source_summary="A roommate's under-vaccinated cat bites the narrator's cat twice, leading to a bloodwork bill.",
        story_beats=[
            "The roommate's cat bites the narrator's cat twice.",
            "The second bite leaves a fresh puncture.",
            "The roommate admits three shots are expired.",
            "The narrator asks her to cover bloodwork.",
            "The roommate refuses and talks about ending the lease.",
        ],
        adaptation_strategy="Compressed the source into the two bites, expired shots, vet recommendation, bill request, and lease fallout.",
        retention_angle="A pet injury, expired shots, a vet bill, and a roommate refusing to pay create an immediate debate.",
        turning_point="The roommate admitted three shots were expired after the second bite.",
        payoff_line="Now I have the expired-shot text and the bloodwork bill in front of me.",
        viewer_question="Should my roommate have paid for the bloodwork?",
        marketability_score=5,
        retention_risk="The story could feel small, so the narration centers on the second bite and expired shots.",
        rewrite_notes="Opened on the repeat bite and kept the vet bill as the receipt.",
        hook_type="pet_medical_bill",
        style_variant="receipt_reveal",
        voiceover_lines=_draft_lines(),
    )


def test_draft_script_expands_to_full_metadata_after_repair(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)

    metadata = validate_and_parse_metadata(_draft(), 0, _cat_post())

    assert metadata["public_title"] == "Her Cat Bit Mine Twice, Then She Refused To Pay"
    assert metadata["first_frame_text"] == "HER CAT BIT MINE TWICE"
    assert metadata["opening_visual_query"] == "cat vet clinic"
    assert metadata["description"]
    assert metadata["tags"]
    assert metadata["tts_text"] == " ".join(metadata["voiceover_lines"])
    assert metadata["caption_chunks"][-1].endswith("?")
    ensure_content_gate(metadata, stage="script_accept")


def test_draft_to_metadata_does_not_require_mechanical_fields():
    metadata = draft_to_metadata(_draft())

    assert "caption_chunks" not in metadata
    assert "opening_visual_query" not in metadata
    assert "first_frame_text" not in metadata
    assert metadata["script"] == metadata["voiceover_lines"]


def test_generation_prompt_does_not_ask_llm_for_mechanical_fields(monkeypatch):
    captured = {}

    def fake_generate_script(prompt):
        captured["prompt"] = prompt
        return _draft()

    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.generate_script", fake_generate_script)

    call_gpt_generate_script("Cat bill", _cat_post()["content"], post=_cat_post())

    prompt = captured["prompt"]
    for forbidden in (
        "Fill `caption_chunks`",
        "Fill `tts_text`",
        "Add `first_frame_text`",
        "Add `opening_visual_query`",
        "Add `visual_beat_queries`",
        "Add a `visual_keywords`",
        "Fill predicted performance scores",
    ):
        assert forbidden not in prompt


def test_prompt_example_is_minimal_draft_json():
    example = json.loads(EXAMPLE_JSON)

    DraftScript.model_validate(example)
    assert "caption_chunks" not in example
    assert "opening_visual_query" not in example
