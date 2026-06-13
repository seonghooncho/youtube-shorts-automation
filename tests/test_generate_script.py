import json

import pytest

from generator.text import generate_script as generate_script_module
from generator.text.generate_script import NativeViewerCritic, ReturnScript, _token_budgets
from generator.text.generate_scripts_from_filtered import (
    EXAMPLE_JSON,
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
    assert "7 to 10 complete voiceover lines" in reason
    assert "hard max 1050" in reason


def test_llm_quota_error_detection():
    assert _is_llm_quota_error("Error code: 429 - insufficient_quota")
    assert _is_llm_quota_error("You exceeded your current quota")


def test_prompt_example_uses_consistent_voiceover_line_standard():
    example = json.loads(EXAMPLE_JSON)

    assert 7 <= len(example["voiceover_lines"]) <= 10
    assert example["script"] == example["voiceover_lines"]
    assert example["caption_chunks"][-1].endswith("?")
    assert all(len(chunk) <= 42 for chunk in example["caption_chunks"])


def test_critic_failure_causes_rewrite_failure(monkeypatch):
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "1")
    draft = ReturnScript(
        title="Neighbor Used My Driveway",
        description="A driveway conflict.",
        tags=["storytime"],
        voice="neutral",
        visual_keywords=["driveway", "door camera", "phone chat", "parking sign"],
        hook_type="neighbor_dispute",
        first_2_seconds="My neighbor parked in my driveway for six hours",
        source_summary="A neighbor uses a driveway and gets caught by a door camera.",
        story_beats=["driveway", "camera", "chat", "clip"],
        adaptation_strategy="Compressed the driveway story into a camera receipt and public correction.",
        retention_angle="The story has driveway misuse, a camera receipt, and a public correction.",
        turning_point="The door camera showed the car sitting there.",
        payoff_line="I posted the driveway clip.",
        viewer_question="Would you have posted the clip?",
        marketability_score=5,
        retention_risk="The script gets to the driveway camera clip quickly.",
        cut_plan=["driveway", "door camera", "phone chat", "parking sign"],
        bg_strategy="hybrid",
        style_variant="neighbor_dispute",
        script=[
            "My neighbor parked in my driveway for six hours.",
            "The door camera showed his car sitting there.",
            "He complained in the group chat after I asked him to move.",
            "Would you have posted the clip?",
        ],
    )
    failing_critic = NativeViewerCritic(
        ai_smell_score=6,
        native_naturalness_score=6,
        retention_score=6,
        specificity_score=7,
        hook_score=7,
        payoff_score=6,
        comment_potential_score=7,
        problems=["Sounds generic."],
        rewrite_instructions=["Add concrete details."],
    )
    monkeypatch.setattr(generate_script_module, "critique_script", lambda prompt, result: failing_critic)
    monkeypatch.setattr(generate_script_module, "_get_client", lambda: object())
    monkeypatch.setattr(generate_script_module, "_try_structured", lambda client, prompt, max_output_tokens: draft)

    with pytest.raises(ValueError, match="native_viewer_critic_failed"):
        generate_script_module._run_critic_rewrite_flow("source prompt", draft, 1000)


def test_local_fallback_metadata_passes_quality_validation(monkeypatch):
    monkeypatch.setenv("SCRIPT_LOCAL_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("SCRIPT_ALLOW_SYNTHETIC_SOURCES", "1")
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
    assert 650 <= metadata["script_char_count"] <= 1050
    assert "driveway" in metadata["first_2_seconds"].lower()


def test_local_fallback_metadata_covers_synthetic_seed_batch(monkeypatch):
    monkeypatch.setenv("SCRIPT_LOCAL_FALLBACK_ENABLED", "1")
    monkeypatch.setenv("SCRIPT_ALLOW_SYNTHETIC_SOURCES", "1")
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
    assert all(650 <= item["script_char_count"] <= 1050 for item in generated)
    assert all("Without asking me first" not in " ".join(item["script"]) for item in generated)
    assert all("one clear boundary in this situation" not in " ".join(item["script"]) for item in generated)
    assert all("At first, My" not in " ".join(item["script"]) for item in generated)
    assert all(not _ends_with_dangling_word(item["first_2_seconds"]) for item in generated)
    assert all(not _ends_with_dangling_word(line) for item in generated for line in item["script"])
    assert all(not _ends_with_dangling_word(_title_without_hashtags(item["title"])) for item in generated)
    assert all(len(item["title"]) <= 100 for item in generated)


def test_local_fallback_disabled_by_default_in_production(monkeypatch):
    monkeypatch.delenv("SCRIPT_LOCAL_FALLBACK_ENABLED", raising=False)
    monkeypatch.delenv("SCRIPT_ALLOW_SYNTHETIC_SOURCES", raising=False)

    post = {
        "id": "synthetic-test",
        "title": "AITA for refusing to move my car after my neighbor used my driveway?",
        "content": "My neighbor parked in my driveway for six hours. My door camera showed the car there.",
        "source_provider": "synthetic",
    }

    try:
        _build_local_fallback_metadata(post, "insufficient_quota")
    except RuntimeError as exc:
        assert "local-template" in str(exc)
    else:
        raise AssertionError("expected local fallback to be disabled by default")


def _ends_with_dangling_word(line: str) -> bool:
    return line.rstrip(" .,!?:;").split(" ")[-1].lower() in {
        "a",
        "and",
        "because",
        "but",
        "for",
        "from",
        "in",
        "like",
        "of",
        "that",
        "the",
        "then",
        "to",
        "with",
        "without",
    }


def _title_without_hashtags(title: str) -> str:
    return title.split(" #shorts", 1)[0]
