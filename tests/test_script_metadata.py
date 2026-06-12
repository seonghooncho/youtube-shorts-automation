import pytest

from generator.text.generate_script import ReturnScript
from generator.text.generate_scripts_from_filtered import validate_and_parse_metadata


def _market_fields(**overrides):
    fields = {
        "adaptation_strategy": "Compressed repeated conflict into a few clear beats and added plausible connective dialogue without changing the core dilemma.",
        "retention_angle": "The story has a concrete unfair accusation, public embarrassment, and a final boundary decision that viewers can debate.",
        "viewer_question": "Would you have corrected the lie too?",
        "marketability_score": 5,
    }
    fields.update(overrides)
    return fields


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
        **_market_fields(),
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
        **_market_fields(),
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
        **_market_fields(
            adaptation_strategy="Changed the source into an unrelated wedding scene, which should fail source overlap validation despite being dramatic.",
            retention_angle="The story has a concrete public betrayal and a final boundary decision that viewers can debate.",
            viewer_question="Would you have let the wedding continue?",
        ),
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


def test_validate_metadata_rejects_minor_romance_source():
    source = {
        "title": "AITA for asking my girlfriend to be public?",
        "content": (
            "I am 18 and my girlfriend is 17. We were dating, but she kept saying she was single in public. "
            "I asked her to treat me like her boyfriend around other people, and she said I was being dramatic. "
            "We fought over whether our relationship was private or public, and I wondered if I was asking too much. "
            "The whole thing became a relationship argument with friends taking sides."
        ),
    }
    result = ReturnScript(
        title="She Wanted Me Secret",
        description="A relationship conflict about public commitment.",
        tags=["relationship", "storytime"],
        voice="male",
        visual_keywords=["phone texting", "couple argument", "person thinking", "city street"],
        source_summary="A young couple argues about whether their relationship should be public.",
        story_beats=[
            "The narrator is dating someone who acts single in public.",
            "He asks for public consistency.",
            "She calls him dramatic.",
            "Friends take sides over whether he asked too much.",
        ],
        **_market_fields(
            adaptation_strategy="Compressed the relationship dispute into a clearer public-versus-private commitment conflict.",
            retention_angle="The story has a public/private relationship boundary and a final decision that could split viewers.",
            viewer_question="Was asking to be public too much?",
        ),
        script=[
            "My girlfriend treated me like a secret, then acted like I was crazy for asking to be public.",
            "In private, she wanted the relationship, the calls, and all the emotional support. But around other people, she kept saying she was single.",
            "I finally told her I was not asking for a huge announcement. I just did not want to feel hidden while still doing boyfriend things.",
            "She said I was being dramatic, and somehow our friends started acting like I pressured her.",
            "I told her if she wanted me in private but not in public, then we were not together. Was asking to be public too much?",
        ],
    )

    with pytest.raises(ValueError, match="source_marketability_reject"):
        validate_and_parse_metadata(result, 0, source)


def test_validate_metadata_rejects_unsupported_high_stakes_invention():
    source = {
        "title": "AITA for refusing to babysit every Saturday?",
        "content": (
            "My sister asked me to babysit her twins every Saturday after I already work six days a week. "
            "I told her I could help once a month, but she demanded every weekend because she wanted free time. "
            "When I said no, she told our parents I was abandoning family and they started calling me selfish. "
            "I sent everyone screenshots showing I had offered a compromise and that she had rejected it. "
            "Now she says I embarrassed her and made the family take sides, but I feel like she was trying to trap me. "
            "I still love the kids, but I do not want every weekend of my life assigned without anyone asking me first."
        ),
    }
    result = ReturnScript(
        title="My Sister Turned Babysitting Into a Family Trial",
        description="A family argument about free babysitting.",
        tags=["family", "storytime"],
        voice="neutral",
        visual_keywords=["phone texting", "family argument", "tired worker", "living room conversation"],
        source_summary="The narrator refuses a sister's demand for free babysitting every Saturday and the family takes sides.",
        story_beats=[
            "The sister asks for weekly babysitting.",
            "The narrator offers a smaller compromise.",
            "The sister accuses the narrator of abandoning family.",
            "Screenshots show the compromise was rejected.",
            "The narrator asks if refusing was selfish.",
        ],
        **_market_fields(
            adaptation_strategy="Compressed the repeated family pressure into one clear weekend demand, sharpened the accusation, and kept the same babysitting boundary conflict.",
            retention_angle="The story has family pressure, an unfair accusation, and a clear boundary decision viewers can debate.",
            viewer_question="Would you have refused the weekly babysitting too?",
        ),
        script=[
            "My sister demanded free babysitting every Saturday, then threatened to call the police when I said no.",
            "I already work six days a week, so when she asked me to watch her twins every weekend, I told her I could help once a month. She acted like I had betrayed the entire family.",
            "By dinner, my parents were texting me that I was abandoning her. I sent screenshots showing I had offered a compromise and she had rejected it because she wanted every Saturday free.",
            "That is the part that made me snap. I was not refusing an emergency or pretending the kids were a burden. I was refusing to become her permanent weekend plan because she did not want to pay a sitter.",
            "Instead of backing off, she said I embarrassed her and warned me she would make this a legal problem if I kept refusing.",
            "I still love the kids, but I do not want my whole weekend assigned without anyone asking. Would you have refused the weekly babysitting too?",
        ],
    )

    with pytest.raises(ValueError, match="unsupported_high_stakes_fact"):
        validate_and_parse_metadata(result, 0, source)


def test_validate_metadata_requires_transparent_adaptation_strategy():
    source = {
        "title": "AITA for correcting my roommate?",
        "content": (
            "My roommate borrowed my blender and broke the lid, then told our friends I was being dramatic "
            "for asking her to replace it. I showed the group chat that she had borrowed it without asking "
            "and admitted it cracked while she was using it. Now she says I embarrassed her over a cheap kitchen item, "
            "but I feel like she made me look greedy first. We have lived together for a year and this is not the first "
            "time she has used my things and acted annoyed when I asked for basic respect."
            " I have replaced small things quietly before, but this time she made it a group argument before I even had a chance "
            "to talk to her privately about paying for a replacement lid."
        ),
    }
    result = ReturnScript(
        title="My Roommate Broke My Blender and Blamed Me",
        description="A roommate conflict about replacing a broken item.",
        tags=["roommate", "storytime"],
        voice="neutral",
        visual_keywords=["apartment kitchen", "broken blender", "phone texting", "roommate argument"],
        source_summary="A roommate breaks a borrowed blender lid and makes the narrator look dramatic for asking for a replacement.",
        story_beats=[
            "The roommate borrows the blender without asking.",
            "The lid cracks while she is using it.",
            "She tells friends the narrator is being dramatic.",
            "The narrator shares proof in the group chat.",
            "The roommate says she was embarrassed over a cheap item.",
        ],
        **_market_fields(
            adaptation_strategy="This version is more interesting for audiences and should keep people watching because the conflict is clear.",
            retention_angle="The story has property damage, an unfair accusation, and a roommate boundary decision viewers can debate.",
            viewer_question="Would you have posted the screenshots too?",
        ),
        script=[
            "My roommate broke my blender, then acted like I was greedy for asking her to replace it.",
            "She borrowed it without asking while I was at work. When I got home, the lid was cracked on the counter and she casually said it still worked if I held it down.",
            "I told her I needed a replacement lid. Instead of apologizing, she told our friends I was starting drama over a cheap kitchen item.",
            "So I sent the group chat the messages where she admitted it broke while she was using it. Suddenly everyone understood why I was annoyed.",
            "The weird part is I probably would have dropped it if she had talked to me privately. But once she made me sound greedy to people we both live around, I felt like I had to correct the story.",
            "Now she says I humiliated her, but she made me look greedy first. Would you have posted the screenshots too?",
        ],
    )

    with pytest.raises(ValueError, match="weak_adaptation_strategy"):
        validate_and_parse_metadata(result, 0, source)
