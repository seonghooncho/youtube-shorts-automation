from generator.text.generate_script import _token_budgets
from generator.text.generate_scripts_from_filtered import (
    _build_local_fallback_metadata,
    _is_llm_quota_error,
    _regenerate_reason_from_error,
)
from generator.text.reddit_sources import _SYNTHETIC_SCENARIOS, _synthetic_story_content


def test_token_budgets_default_are_high_enough_for_full_schema(monkeypatch):
    monkeypatch.delenv("SCRIPT_OUTPUT_TOKEN_BUDGETS", raising=False)

    assert _token_budgets() == [3200, 4200, 5200]


def test_token_budgets_accept_env_override(monkeypatch):
    monkeypatch.setenv("SCRIPT_OUTPUT_TOKEN_BUDGETS", "2600, bad, 3600, 0")

    assert _token_budgets() == [2600, 3600]


def test_regenerate_reason_for_overlength_is_strict(monkeypatch):
    monkeypatch.setenv("SCRIPT_TARGET_MIN_CHARS", "820")
    monkeypatch.setenv("SCRIPT_TARGET_MAX_CHARS", "980")

    reason = _regenerate_reason_from_error("post 0 오류: ❌ script가 쇼츠 목표보다 너무 긺 (현재 1418자)")

    assert "1418 characters" in reason
    assert "820-980 characters" in reason
    assert "exactly 5 short paragraphs" in reason
    assert "hard max 1150" in reason


def test_llm_quota_error_detection():
    assert _is_llm_quota_error("Error code: 429 - insufficient_quota")
    assert _is_llm_quota_error("You exceeded your current quota")


def test_local_fallback_metadata_passes_quality_validation():
    content = " ".join(
        [
            "I had one clear boundary in this situation: my driveway is not shared parking, even if I am not home.",
            "My neighbor had been asking to use it for quick errands, and I said yes twice because it was only a few minutes.",
            "Then he started treating it like his extra spot and told delivery drivers to leave packages by my side door without asking me first, and acted like I was the unreasonable one for noticing.",
            "The part that made people take sides was he complained in the neighborhood chat that I was being petty over empty pavement.",
            "I tried to keep it calm and said I was not paying for a problem I did not create, but he demanded I apologize for embarrassing him in front of the whole block.",
            "What changed everything was my door camera showed his car there for six hours while guests had to park down the street.",
            "After that, I put up a small private parking sign and stopped answering his texts.",
            "Now half the people around us say I should have let it go to keep peace, and the other half say this was exactly when I needed to hold the boundary.",
            "Was I too strict, or did he turn a favor into a right?",
        ]
    )
    post = {
        "id": "synthetic-test",
        "title": "AITA for refusing to move my car after my neighbor used my driveway?",
        "content": content,
        "source_provider": "synthetic",
    }

    metadata = _build_local_fallback_metadata(post, "insufficient_quota")

    assert metadata["generation_fallback"] == "local_template"
    assert metadata["id"] == "synthetic-test"
    assert 750 <= metadata["script_char_count"] <= 1150
    assert "without asking" in metadata["first_2_seconds"].lower()


def test_local_fallback_metadata_covers_synthetic_seed_batch():
    generated = []
    for scenario in _SYNTHETIC_SCENARIOS[:17]:
        post = {
            "id": f"synthetic-test-{scenario['slug']}",
            "title": scenario["title"],
            "content": _synthetic_story_content(scenario),
            "source_provider": "synthetic",
        }

        generated.append(_build_local_fallback_metadata(post, "insufficient_quota"))

    assert len(generated) == 17
    assert all(item["generation_fallback"] == "local_template" for item in generated)
    assert all(750 <= item["script_char_count"] <= 1150 for item in generated)
