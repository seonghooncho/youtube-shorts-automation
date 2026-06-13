from generator.text.script_fingerprint import apply_script_fingerprint, batch_diversity_issues


def _metadata(index: int, **overrides):
    metadata = {
        "title": f"Neighbor Used My Driveway {index}",
        "public_title": f"Neighbor Used My Driveway {index}",
        "hook_type": "neighbor_dispute",
        "style_variant": "neighbor_dispute",
        "viewer_question": f"Was I wrong, or was he using my spot {index}?",
        "script": [
            f"My neighbor parked in my driveway for six hours on day {index}.",
            "The door camera showed his car sitting there while my guests parked down the street.",
            "He complained in the building chat after I asked him to move.",
            "I posted the clip and the text where he admitted it was his car.",
            f"Was I wrong, or was he using my spot {index}?",
        ],
    }
    metadata.update(overrides)
    return apply_script_fingerprint(metadata)


def test_script_fingerprint_adds_required_metadata_fields():
    metadata = _metadata(1)

    assert metadata["hook_pattern"] == "neighbor_dispute"
    assert metadata["ending_pattern"] == "was_i_x_or_y"
    assert metadata["style_variant"] == "neighbor_dispute"
    assert metadata["script_fingerprint"]


def test_batch_diversity_rejects_duplicate_hook_ending_style_and_title():
    accepted = [
        _metadata(1, public_title="Neighbor Used My Driveway Again"),
        _metadata(2, public_title="Neighbor Used My Driveway Twice"),
    ]
    candidate = _metadata(3, public_title="Neighbor Used My Driveway Today")

    issues = batch_diversity_issues(candidate, accepted, [])
    codes = {issue.code for issue in issues}

    assert "duplicate_hook_type" in codes
    assert "duplicate_ending_pattern" in codes
    assert "duplicate_style_variant" in codes
    assert "duplicate_title_start" in codes
