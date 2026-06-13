from generator.text.youtube_metadata import (
    TITLE_HASHTAGS,
    apply_youtube_metadata_style,
    format_youtube_description,
    format_youtube_title,
    merge_youtube_tags,
    sanitize_upload_metadata,
    unsafe_upload_metadata_reason,
)


def test_format_youtube_title_adds_reference_hashtags_and_keeps_limit():
    title = (
        "My roommate let her cat bite mine twice while his vaccines were expired, "
        "then acted like I was crazy"
    )

    formatted = format_youtube_title(title)

    assert len(formatted) <= 100
    assert formatted.endswith(" ".join(TITLE_HASHTAGS))
    assert formatted.startswith("My roommate let her cat bite mine twice")


def test_format_youtube_title_deduplicates_existing_hashtags():
    formatted = format_youtube_title("My sister stole my rent money #shorts #story")

    assert formatted.count("#shorts") == 1
    assert formatted.endswith(" ".join(TITLE_HASHTAGS))


def test_format_youtube_description_adds_question_and_hashtags():
    description = format_youtube_description(
        "A roommate drama about a vet bill.",
        viewer_question="Would you ask her to pay?",
    )

    assert "A roommate drama about a vet bill." in description
    assert "Would you ask her to pay?" in description
    assert " ".join(TITLE_HASHTAGS) in description


def test_merge_youtube_tags_preserves_specific_tags_and_adds_defaults():
    tags = merge_youtube_tags(["Roommate", "#Cat", "storytime"])

    assert tags[:2] == ["roommate", "cat"]
    assert "shorts" in tags
    assert "reddit" in tags
    assert "viral" in tags
    assert len(tags) <= 15


def test_apply_youtube_metadata_style_updates_upload_fields():
    metadata = {
        "title": "My coworker lied to HR",
        "description": "A workplace conflict.",
        "viewer_question": "Would you show the screenshots?",
        "tags": ["workplace"],
    }

    apply_youtube_metadata_style(metadata)

    assert metadata["title"].endswith(" ".join(TITLE_HASHTAGS))
    assert "Would you show the screenshots?" in metadata["description"]
    assert metadata["tags"][:1] == ["workplace"]
    assert "shorts" in metadata["tags"]


def test_sanitize_upload_metadata_blocks_internal_values():
    reason = unsafe_upload_metadata_reason(
        "PENDING #shorts",
        "A normal description.",
        ["storytime"],
    )

    assert reason == "unsafe_metadata:title:pending"


def test_sanitize_upload_metadata_returns_public_fields():
    metadata = sanitize_upload_metadata(
        "My sister used my apartment as free storage #shorts",
        "A family conflict about a crossed boundary.",
        ["Family!", "#Storytime", "reddit"],
    )

    assert metadata["title"].endswith(" ".join(TITLE_HASHTAGS))
    assert "A family conflict" in metadata["description"]
    assert metadata["tags"][:2] == ["family", "storytime"]
