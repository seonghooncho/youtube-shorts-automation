import pytest

from generator.text.generate_script import ReturnScript
from generator.text.generate_scripts_from_filtered import validate_and_parse_metadata


def test_validate_metadata_requires_visual_keywords():
    result = ReturnScript(
        title="A short story",
        description="A concise description",
        tags=["reddit", "storytime"],
        voice="neutral",
        visual_keywords=["phone texting", "couple argument", "nature", "phone texting"],
        source_summary="A date pushes for commitment after one meeting and then lies about what happened.",
        story_beats=[
            "The narrator meets a date for coffee.",
            "The date asks for immediate commitment.",
            "The narrator says they barely know each other.",
            "The date lies to mutual friends afterward.",
        ],
        script=[
            "My date demanded commitment after one dinner, then told everyone I led him on.",
            "We matched on an app and met for coffee. He was nice at first, but halfway through he started talking like we were already a couple. I laughed it off because I thought he was joking.",
            "Then he asked if I would delete the app that night. I told him I barely knew him and wanted to take things slowly. His face changed immediately.",
            "The next morning, three mutual friends messaged me saying he claimed I used him for free food and embarrassed him in public. I sent them screenshots of the conversation, including the part where I paid for my own coffee.",
            "Now he says I humiliated him by correcting the story in the group chat, even though he started it. Was I supposed to let him lie, or did I go too far?",
        ],
    )

    metadata = validate_and_parse_metadata(result, 0, {})

    assert metadata["visual_keywords"] == ["phone texting", "couple argument"]


def test_validate_metadata_rejects_slow_long_script():
    result = ReturnScript(
        title="Too long",
        description="Too long",
        tags=["reddit"],
        voice="neutral",
        visual_keywords=["phone texting"],
        source_summary="A conflict that is long enough to satisfy the schema but not used in this length test.",
        story_beats=[
            "The conflict starts.",
            "The narrator responds.",
            "The situation escalates.",
            "The narrator asks what to do.",
        ],
        script=["x" * 1401],
    )

    with pytest.raises(ValueError, match="너무 긺"):
        validate_and_parse_metadata(result, 0, {})


def test_validate_metadata_rejects_unfaithful_script():
    source = {
        "title": "AITA for refusing to babysit?",
        "content": (
            "My sister asked me to babysit her twins every Saturday after I already work six days a week. "
            "I told her I could help once a month, but she demanded every weekend because she wanted free time. "
            "When I said no, she told our parents I was abandoning family and they started calling me selfish. "
            "I sent everyone screenshots showing I had offered a compromise and that she had rejected it. "
            "Now she says I embarrassed her and made the family take sides, but I feel like she was trying to trap me. "
            "The whole argument started because I used to help occasionally, and she decided that meant I had agreed to "
            "be her regular childcare plan. I still love the kids, but I do not want every weekend of my life assigned "
            "without anyone asking me first."
        ),
    }
    result = ReturnScript(
        title="A fake wedding disaster",
        description="A story that drifts away from the source.",
        tags=["reddit"],
        voice="neutral",
        visual_keywords=["wedding aisle", "bride crying", "party argument", "phone texting"],
        source_summary="A wedding suddenly falls apart after a secret affair is revealed.",
        story_beats=[
            "A bride finds out about an affair.",
            "The groom denies everything.",
            "Guests start arguing.",
            "The narrator leaves the ceremony.",
        ],
        script=[
            "My best friend's wedding exploded when the groom's secret girlfriend walked into the ceremony.",
            "Everyone froze, the bride started crying, and I was the only person close enough to pull her aside.",
            "The groom tried to blame me for causing a scene even though I had nothing to do with it.",
            "Then his mother cornered me near the reception table and said I had ruined a perfect day by helping the bride walk away. I told her the day was ruined before I even stood up.",
            "By dinner, half the guests were whispering that I should have kept smiling for the photos, while the other half said the bride deserved to know immediately.",
            "I ended up driving her back to the hotel, packing her dress bag into my car, and ignoring calls from the groom's friends who kept insisting it was all a misunderstanding.",
            "Now half the family says I should have stayed quiet and let them handle it privately, but would you have let the wedding continue?",
        ],
    )

    with pytest.raises(ValueError, match="low_source_overlap"):
        validate_and_parse_metadata(result, 0, source)
