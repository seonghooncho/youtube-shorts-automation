from generator.text.generate_scripts_from_filtered import compact_source_for_prompt


def test_short_source_is_unchanged():
    post = {"title": "Short", "content": "My neighbor parked in my driveway. Would you have complained?"}

    assert compact_source_for_prompt(post, 3500) == post["content"]


def test_long_source_is_compacted_and_preserves_receipt_and_question():
    filler = " ".join(["This was extra background about the apartment routine."] * 90)
    receipt = "The receipt and door camera timestamp showed the landlord entered at 7:12 pm."
    question = "Would you have used the chain lock after that?"
    post = {
        "title": "Landlord entered my apartment",
        "content": f"{filler} {receipt} More slow context. {question}",
    }

    compacted = compact_source_for_prompt(post, 900)

    assert len(compacted) <= 900
    assert "receipt and door camera timestamp" in compacted
    assert question in compacted
