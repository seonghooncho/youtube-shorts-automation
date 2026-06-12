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
        script=["x" * 1401],
    )

    with pytest.raises(ValueError, match="너무 긺"):
        validate_and_parse_metadata(result, 0, {})
