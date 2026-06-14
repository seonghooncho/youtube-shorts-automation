from generator.text.failure_policy import FailureAction, classify_failure
from generator.text.generate_scripts_from_filtered import _generation_summary, _operator_recommendation


def test_classifies_mechanical_failures_as_repair_only():
    assert classify_failure("content_gate_failed:title_quality:missing_conflict_action") == FailureAction.REPAIR_ONLY
    assert classify_failure("caption_chunks_not_in_tts_text:chunk_1") == FailureAction.REPAIR_ONLY
    assert classify_failure("post 0 오류: ❌ script가 너무 짧음 (현재 618자)") == FailureAction.REPAIR_ONLY


def test_classifies_weak_narration_as_single_rewrite():
    assert classify_failure("weak_market_hook: first sentence is weak") == FailureAction.LLM_REWRITE_ONCE
    assert classify_failure("post 0 오류: ❌ script가 너무 짧음 (현재 420자)") == FailureAction.LLM_REWRITE_ONCE


def test_classifies_fatal_or_repeated_failures_as_skip():
    assert classify_failure("unsafe_visual_keywords: teen romance") == FailureAction.SKIP_SOURCE
    assert classify_failure("weak_market_hook", repeated=True) == FailureAction.SKIP_SOURCE
    assert classify_failure("source_too_thin: 40 words") == FailureAction.SKIP_SOURCE
    assert classify_failure("source_truncated: missing ending") == FailureAction.SKIP_SOURCE
    assert classify_failure("content_gate_failed:title_quality", repeated=True) == FailureAction.REPAIR_ONLY


def test_generation_summary_counts_only_explicit_preflight_stage():
    posts = [{"id": "a"}, {"id": "b"}]
    failed = [
        {
            "id": "a",
            "stage": "script_generation",
            "error": "unsupported_high_stakes_fact: script invents facts not present in source",
            "generation_attempt_count": 1,
        },
        {
            "id": "b",
            "stage": "source_preflight",
            "error": "source is too thin",
        },
    ]

    summary = _generation_summary(posts, [], failed)

    assert summary["sources_skipped_preflight"] == 1
    assert summary["final_rejected"] == 2
    assert "operator_recommendation" in summary


def test_operator_recommendation_distinguishes_source_and_gate_failures():
    assert _operator_recommendation(
        {
            "final_accepted": 0,
            "sources_considered": 0,
            "source_scorecard_calls": 8,
        }
    ).startswith("CHECK_SOURCE_FILTER")
    assert _operator_recommendation(
        {
            "final_accepted": 0,
            "sources_considered": 3,
            "source_scorecard_calls": 8,
        }
    ).startswith("CHECK_GATE")


def test_operator_recommendation_flags_cost_and_schema_issues():
    assert _operator_recommendation(
        {
            "final_accepted": 2,
            "json_fallback_attempts": 1,
            "structured_attempts": 4,
            "structured_failures": 0,
        }
    ).startswith("CHECK_COST")
    assert _operator_recommendation(
        {
            "final_accepted": 2,
            "json_fallback_attempts": 0,
            "structured_attempts": 4,
            "structured_failures": 3,
        }
    ).startswith("CHECK_PROMPT_SCHEMA")


def test_operator_recommendation_flags_critic_naturalness_and_ok():
    assert _operator_recommendation(
        {
            "final_accepted": 2,
            "json_fallback_attempts": 0,
            "structured_attempts": 4,
            "structured_failures": 0,
            "critic_failed": 3,
            "critic_passed": 1,
            "critic_skipped": 1,
        }
    ).startswith("CHECK_SCRIPT_NATURALNESS")
    assert _operator_recommendation(
        {
            "final_accepted": 3,
            "json_fallback_attempts": 0,
            "structured_attempts": 4,
            "structured_failures": 0,
            "critic_failed": 0,
            "critic_passed": 1,
            "critic_skipped": 2,
        }
    ).startswith("OK")
