import json

import pytest

from generator.text.generate_script import DraftScript, GenerateScriptError, NativeViewerCritic
from generator.text.generate_scripts_from_filtered import (
    _finalize_candidate,
    generate_scripts_from_filtered,
    should_run_critic,
    validate_and_parse_metadata,
)


def _post():
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
        "content_char_count": len(content),
        "content_word_count": len(content.split()),
        "source_provider": "pullpush",
        "source_url": "https://reddit.test/r/AITA/comments/cat-bite",
    }


def _draft(lines=None):
    lines = lines or [
        "My roommate's cat bit mine again, twice in two weeks.",
        "The first bite looked small, but the second one left a fresh puncture.",
        "I asked if her cat was vaccinated, and she admitted three shots were expired.",
        "Instead of apologizing, she only sent me the new appointment date.",
        "After the vet suggested bloodwork, I texted that she needed to cover the bill.",
        "She brushed it off and then told me she was not renewing our lease.",
        "Now I have the text about the expired shots and the bloodwork bill in front of me.",
        "Should my roommate have paid for the bloodwork?",
    ]
    return DraftScript(
        title="Roommate Refused My Cat's Bloodwork Bill",
        voice="neutral",
        source_summary="A roommate's under-vaccinated cat bites the narrator's cat twice, leading to a bloodwork bill.",
        story_beats=[
            "The roommate's cat bites the narrator's cat twice.",
            "The second bite leaves a fresh puncture.",
            "The roommate admits three shots are expired.",
            "The narrator asks her to cover bloodwork.",
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
        voiceover_lines=lines,
    )


def _passing_critic():
    return NativeViewerCritic(
        ai_smell_score=2,
        native_naturalness_score=9,
        retention_score=9,
        specificity_score=9,
        hook_score=9,
        payoff_score=8,
        comment_potential_score=8,
    )


def _failing_critic():
    return NativeViewerCritic(
        ai_smell_score=6,
        native_naturalness_score=6,
        retention_score=6,
        specificity_score=6,
        hook_score=6,
        payoff_score=6,
        comment_potential_score=6,
        problems=["Sounds generic."],
        rewrite_instructions=["Make the hook more concrete."],
    )


def _run_generation(monkeypatch, tmp_path, drafts, critic=None):
    viable = tmp_path / "viable_posts.json"
    final = tmp_path / "final_metadata.json"
    failed = tmp_path / "failed_posts.json"
    viable.write_text(json.dumps([_post()]), encoding="utf-8")
    calls = {"llm": 0, "critic": 0}

    def fake_call(*_args, **_kwargs):
        calls["llm"] += 1
        return drafts[min(calls["llm"] - 1, len(drafts) - 1)]

    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.VIABLE_POSTS_FILE", viable)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FINAL_METADATA_FILE", final)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FAILED_POSTS_FILE", failed)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered._load_previous_accepted_metadata", lambda: [])
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.call_gpt_generate_script", fake_call)
    if critic is not None:
        def fake_critic(*_args, **_kwargs):
            calls["critic"] += 1
            return critic(calls["critic"])

        monkeypatch.setattr("generator.text.generate_scripts_from_filtered.critique_script", fake_critic)

    generate_scripts_from_filtered()
    accepted = json.loads(final.read_text(encoding="utf-8")) if final.exists() else []
    rejected = json.loads(failed.read_text(encoding="utf-8")) if failed.exists() else []
    return calls, accepted, rejected


def test_582_char_cat_draft_is_repaired_and_accepted(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    draft = _draft(
        [
            "My roommate's cat bit mine again, twice in two weeks.",
            "First it looked like a scab, then I found a fresh puncture while I was holding my cat.",
            "I asked if her cat was vaccinated, and she admitted three shots were expired in the text thread.",
            "She only sent me the new appointment date instead of offering to help.",
            "After the vet suggested bloodwork, I asked her to cover the bill.",
            "She brushed it off and said she was not renewing our lease.",
            "Now I have the expired-shot text and the vet bill with the estimate attached in front of me.",
            "Should my roommate have paid for the bloodwork?",
        ]
    )
    assert 540 <= len(" ".join(draft.voiceover_lines)) < 650

    metadata = validate_and_parse_metadata(draft, 0, _post())

    assert metadata["script_char_count"] >= 650
    assert metadata["length_repair_status"] == "added_source_grounded_line"


def test_repair_only_failure_does_not_call_llm_again(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "0")
    calls = {"finalize": 0}

    def flaky_finalize(metadata, metadata_list, previous_history, post, *, append=True):
        calls["finalize"] += 1
        if calls["finalize"] == 1:
            raise ValueError("content_gate_failed:script_accept:title_quality:missing_actor_or_object")
        if append:
            metadata_list.append(metadata)
        return metadata

    monkeypatch.setattr("generator.text.generate_scripts_from_filtered._finalize_candidate", flaky_finalize)

    llm_calls, accepted, rejected = _run_generation(monkeypatch, tmp_path, [_draft()])

    assert llm_calls["llm"] == 1
    assert calls["finalize"] == 2
    assert len(accepted) == 1
    assert rejected == []


def test_weak_hook_triggers_at_most_one_rewrite(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "0")
    monkeypatch.setenv("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", "2")
    monkeypatch.setenv("SCRIPT_ALLOW_LLM_REWRITE_ON_NARRATIVE_FAILURE", "1")

    def always_weak(*_args, **_kwargs):
        raise ValueError("post 0 오류: ❌ 품질검증 실패: weak_market_hook: first sentence is weak")

    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.validate_and_parse_metadata", always_weak)

    calls, accepted, rejected = _run_generation(monkeypatch, tmp_path, [_draft(), _draft()])

    assert calls["llm"] == 2
    assert accepted == []
    assert rejected[0]["failure_action"] == "skip_source"


def test_narrative_failure_does_not_rewrite_without_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "0")
    monkeypatch.setenv("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", "2")
    monkeypatch.delenv("SCRIPT_ALLOW_LLM_REWRITE_ON_NARRATIVE_FAILURE", raising=False)

    def always_weak(*_args, **_kwargs):
        raise ValueError("post 0 오류: ❌ 품질검증 실패: weak_market_hook: first sentence is weak")

    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.validate_and_parse_metadata", always_weak)

    calls, accepted, rejected = _run_generation(monkeypatch, tmp_path, [_draft(), _draft()])

    assert calls["llm"] == 1
    assert accepted == []
    assert rejected[0]["failure_action"] == "skip_source"
    assert rejected[0]["llm_draft_count"] == 1


def test_fatal_source_skips_before_llm(monkeypatch, tmp_path):
    thin_post = _post() | {"content": "Too thin.", "content_char_count": 9, "content_word_count": 2}
    viable = tmp_path / "viable_posts.json"
    final = tmp_path / "final_metadata.json"
    failed = tmp_path / "failed_posts.json"
    viable.write_text(json.dumps([thin_post]), encoding="utf-8")
    calls = {"llm": 0}

    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.VIABLE_POSTS_FILE", viable)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FINAL_METADATA_FILE", final)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FAILED_POSTS_FILE", failed)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.call_gpt_generate_script", lambda *_a, **_k: calls.__setitem__("llm", calls["llm"] + 1))

    generate_scripts_from_filtered()

    assert calls["llm"] == 0
    assert json.loads(final.read_text(encoding="utf-8")) == []


def test_mechanical_failure_does_not_call_critic(monkeypatch):
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "1")
    metadata = validate_and_parse_metadata(_draft(), 0, _post())
    metadata["public_title"] = ""
    calls = {"critic": 0}
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.critique_script", lambda *_a, **_k: calls.__setitem__("critic", calls["critic"] + 1))

    with pytest.raises(ValueError, match="content_gate_failed|missing_public_title"):
        _finalize_candidate(metadata, [], [], _post(), append=False)

    assert calls["critic"] == 0


def test_after_local_gate_critic_runs_once(monkeypatch):
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "1")
    metadata = validate_and_parse_metadata(_draft(), 0, _post())
    calls = {"critic": 0}

    def fake_critic(*_args, **_kwargs):
        calls["critic"] += 1
        return _passing_critic()

    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.critique_script", fake_critic)

    accepted = _finalize_candidate(metadata, [], [], _post(), append=False)

    assert calls["critic"] == 1
    assert accepted["critic_passed"] is True


def test_strong_candidate_skips_after_local_gate_critic(monkeypatch):
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "1")
    monkeypatch.delenv("SCRIPT_CRITIC_SAMPLE_RATE", raising=False)
    post = _post() | {"source_priority_score": 4.8}
    metadata = validate_and_parse_metadata(_draft(), 0, post)
    metadata.pop("length_repair_status", None)
    metadata["script_char_count"] = 760
    metadata["quality_warnings"] = []
    metadata["predicted_ai_smell_score"] = 2
    metadata["repair_actions"] = [
        {"code": "caption_chunks_rebuilt"},
        {"code": "first_frame_text_rebuilt"},
        {"code": "opening_visual_query_rebuilt"},
        {"code": "public_title_rebuilt"},
    ]
    calls = {"critic": 0}
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.critique_script", lambda *_a, **_k: calls.__setitem__("critic", calls["critic"] + 1))

    accepted = _finalize_candidate(metadata, [], [], post, append=False)

    assert calls["critic"] == 0
    assert accepted["critic_policy"] == "skipped_strong_candidate"


def test_strong_candidate_sample_rate_one_runs_critic(monkeypatch):
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "1")
    monkeypatch.setenv("SCRIPT_CRITIC_SAMPLE_RATE", "1")
    post = _post() | {"source_priority_score": 4.8}
    metadata = validate_and_parse_metadata(_draft(), 0, post)
    metadata.pop("length_repair_status", None)
    metadata["script_char_count"] = 760
    metadata["quality_warnings"] = []
    metadata["predicted_ai_smell_score"] = 2
    metadata["repair_actions"] = [
        {"code": "caption_chunks_rebuilt"},
        {"code": "first_frame_text_rebuilt"},
        {"code": "opening_visual_query_rebuilt"},
        {"code": "public_title_rebuilt"},
    ]
    calls = {"critic": 0}

    def fake_critic(*_args, **_kwargs):
        calls["critic"] += 1
        return _passing_critic()

    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.critique_script", fake_critic)

    accepted = _finalize_candidate(metadata, [], [], post, append=False)

    assert calls["critic"] == 1
    assert accepted["critic_policy"] == "sampled_strong_candidate"
    assert accepted["critic_policy_reason"] == "sample_rate"


def test_critic_sample_seed_is_stable(monkeypatch):
    monkeypatch.setenv("SCRIPT_CRITIC_SAMPLE_RATE", "0.5")
    monkeypatch.setenv("SCRIPT_CRITIC_SAMPLE_SEED", "stable-test")
    post = _post() | {"source_priority_score": 4.8}
    metadata = {
        "quality_warnings": [],
        "script_char_count": 820,
        "marketability_score": 5,
        "predicted_ai_smell_score": 1,
        "repair_actions": [],
        "script_fingerprint": "fingerprint-1",
        "public_title": "Her Cat Bit Mine Twice",
    }

    first = should_run_critic(metadata, post)
    second = should_run_critic(metadata, post)

    assert first == second


def test_critic_policy_runs_for_borderline_reasons(monkeypatch):
    post = _post() | {"source_priority_score": 4.8}
    metadata = validate_and_parse_metadata(_draft(), 0, post)
    metadata["quality_warnings"] = []
    metadata["predicted_ai_smell_score"] = 2

    run, reason = should_run_critic(metadata, post)

    assert run is True
    assert reason in {"length_repair_status_present", "high_risk_repair_action", "script_under_700_chars"}

    metadata.pop("length_repair_status", None)
    metadata["repair_actions"] = []
    metadata["script_char_count"] = 760
    metadata["marketability_score"] = 4

    run, reason = should_run_critic(metadata, post)

    assert run is True
    assert reason == "marketability_below_5"


def test_critic_always_forces_policy(monkeypatch):
    monkeypatch.setenv("SCRIPT_CRITIC_ALWAYS", "1")
    post = _post() | {"source_priority_score": 5.0}
    metadata = {
        "quality_warnings": [],
        "script_char_count": 820,
        "marketability_score": 5,
        "predicted_ai_smell_score": 1,
        "repair_actions": [],
    }

    assert should_run_critic(metadata, post) == (True, "SCRIPT_CRITIC_ALWAYS=1")


def test_critic_failure_can_select_near_miss_backup_without_rewrite(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "1")
    monkeypatch.setenv("SCRIPT_CRITIC_STAGE", "after_local_gate")
    monkeypatch.setenv("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", "2")
    monkeypatch.setenv("SCRIPT_ALLOW_LLM_REWRITE_ON_NARRATIVE_FAILURE", "1")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "1")
    monkeypatch.setenv("CANDIDATE_BACKUP_ACCEPT_SCORE", "70")

    def critic(call_count):
        return _failing_critic() if call_count == 1 else _passing_critic()

    calls, accepted, rejected = _run_generation(monkeypatch, tmp_path, [_draft(), _draft()], critic=critic)
    summary = json.loads((tmp_path / "generation_summary.json").read_text(encoding="utf-8"))
    near_misses = json.loads((tmp_path / "near_miss_candidates.json").read_text(encoding="utf-8"))

    assert calls["llm"] == 1
    assert calls["critic"] == 1
    assert [item["id"] for item in accepted] == ["cat-bite"]
    assert accepted[0]["selected_as_backup_candidate"] is True
    assert rejected == []
    assert summary["near_miss_count"] == 1
    assert summary["token_overhead_rate"] == 0
    assert near_misses[0]["candidate_score"] > 0


def test_failed_generation_telemetry_is_counted_without_result(monkeypatch, tmp_path):
    viable = tmp_path / "viable_posts.json"
    final = tmp_path / "final_metadata.json"
    failed = tmp_path / "failed_posts.json"
    viable.write_text(json.dumps([_post()]), encoding="utf-8")
    telemetry = {
        "structured_attempts": 1,
        "json_fallback_attempts": 0,
        "structured_failures": 1,
        "json_fallback_failures": 0,
        "estimated_output_token_budget_total": 1600,
        "structured_retry_skipped_reason": "schema_validation_failure",
    }

    monkeypatch.setenv("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", "1")
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.VIABLE_POSTS_FILE", viable)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FINAL_METADATA_FILE", final)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FAILED_POSTS_FILE", failed)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered._load_previous_accepted_metadata", lambda: [])
    monkeypatch.setattr(
        "generator.text.generate_scripts_from_filtered.call_gpt_generate_script",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(GenerateScriptError("schema failed", telemetry)),
    )

    generate_scripts_from_filtered()

    rejected = json.loads(failed.read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "generation_summary.json").read_text(encoding="utf-8"))
    assert rejected[0]["generation_telemetry"]["structured_attempts"] == 1
    assert rejected[0]["llm_structured_attempts"] == 1
    assert summary["structured_attempts"] == 1
    assert summary["structured_failures"] == 1


def test_two_failed_sources_keep_separate_exception_telemetry(monkeypatch, tmp_path):
    viable = tmp_path / "viable_posts.json"
    final = tmp_path / "final_metadata.json"
    failed = tmp_path / "failed_posts.json"
    first = _post() | {"id": "fail-one"}
    second = _post() | {"id": "fail-two"}
    viable.write_text(json.dumps([first, second]), encoding="utf-8")
    telemetries = iter(
        [
            {
                "structured_attempts": 1,
                "json_fallback_attempts": 0,
                "structured_failures": 1,
                "json_fallback_failures": 0,
                "estimated_output_token_budget_total": 1600,
                "structured_retry_skipped_reason": "schema_validation_failure",
            },
            {
                "structured_attempts": 2,
                "json_fallback_attempts": 0,
                "structured_failures": 2,
                "json_fallback_failures": 0,
                "estimated_output_token_budget_total": 3800,
                "structured_retry_skipped_reason": "max_structured_attempts_reached",
            },
        ]
    )

    def fail_with_next_telemetry(*_args, **_kwargs):
        telemetry = next(telemetries)
        raise GenerateScriptError("schema failed", telemetry)

    monkeypatch.setenv("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", "1")
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.VIABLE_POSTS_FILE", viable)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FINAL_METADATA_FILE", final)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FAILED_POSTS_FILE", failed)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered._load_previous_accepted_metadata", lambda: [])
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.call_gpt_generate_script", fail_with_next_telemetry)

    generate_scripts_from_filtered()

    rejected = json.loads(failed.read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "generation_summary.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in rejected] == ["fail-one", "fail-two"]
    assert rejected[0]["generation_telemetry"]["structured_attempts"] == 1
    assert rejected[1]["generation_telemetry"]["structured_attempts"] == 2
    assert rejected[0]["generation_telemetry"]["estimated_output_token_budget_total"] == 1600
    assert rejected[1]["generation_telemetry"]["estimated_output_token_budget_total"] == 3800
    assert summary["structured_attempts"] == 3
    assert summary["estimated_output_token_budget_total"] >= 5400


def test_script_quota_opens_circuit_and_skips_remaining_sources(monkeypatch, tmp_path):
    viable = tmp_path / "viable_posts.json"
    final = tmp_path / "final_metadata.json"
    failed = tmp_path / "failed_posts.json"
    viable.write_text(json.dumps([_post() | {"id": "quota-one"}, _post() | {"id": "quota-two"}]), encoding="utf-8")
    calls = {"llm": 0, "critic": 0}

    def quota_failure(*_args, **_kwargs):
        calls["llm"] += 1
        raise GenerateScriptError(
            "Error code: 429 - insufficient_quota",
            {
                "structured_attempts": 1,
                "json_fallback_attempts": 0,
                "structured_failures": 1,
                "json_fallback_failures": 0,
                "estimated_output_token_budget_total": 1600,
            },
        )

    monkeypatch.setenv("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", "2")
    monkeypatch.setenv("SCRIPT_ALLOW_LLM_REWRITE_ON_NARRATIVE_FAILURE", "1")
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.VIABLE_POSTS_FILE", viable)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FINAL_METADATA_FILE", final)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FAILED_POSTS_FILE", failed)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered._load_previous_accepted_metadata", lambda: [])
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.call_gpt_generate_script", quota_failure)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.critique_script", lambda *_a, **_k: calls.__setitem__("critic", calls["critic"] + 1))

    generate_scripts_from_filtered()

    rejected = json.loads(failed.read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "generation_summary.json").read_text(encoding="utf-8"))
    assert calls["llm"] == 1
    assert calls["critic"] == 0
    assert [item["id"] for item in rejected] == ["quota-one", "quota-two"]
    assert rejected[0]["failure_action"] == "circuit_breaker"
    assert rejected[1]["generation_attempt_count"] == 0
    assert summary["llm_circuit_open"] is True
    assert summary["failure_category_counts"]["quota_or_auth"] == 2
    assert summary["cost_waste_warning"] is True


def test_dry_run_summary_prints_key_metrics(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SCRIPT_DRY_RUN_SUMMARY_ONLY", "1")
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "0")

    _run_generation(monkeypatch, tmp_path, [_draft()])

    output = capsys.readouterr().out
    summary = json.loads((tmp_path / "generation_summary.json").read_text(encoding="utf-8"))
    final_metadata = json.loads((tmp_path / "final_metadata.json").read_text(encoding="utf-8"))
    assert "DRY RUN SUMMARY" in output
    assert "raw=1" in output
    assert "draft_calls=1" in output
    assert "accepted=1" in output
    assert "estimated_output_token_budget=" in output
    assert summary["dry_run"] is True
    assert final_metadata[0]["dry_run"] is True


def test_calibration_mode_writes_candidate_funnel_without_final_output(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIPT_CALIBRATION_MODE", "1")
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "0")

    _run_generation(monkeypatch, tmp_path, [_draft()])

    summary = json.loads((tmp_path / "generation_summary.json").read_text(encoding="utf-8"))
    final_metadata = json.loads((tmp_path / "final_metadata.json").read_text(encoding="utf-8"))
    candidate_scores = json.loads((tmp_path / "candidate_scores.json").read_text(encoding="utf-8"))
    gate_distribution = json.loads((tmp_path / "gate_distribution.json").read_text(encoding="utf-8"))
    funnel = json.loads((tmp_path / "source_to_acceptance_funnel.json").read_text(encoding="utf-8"))

    assert final_metadata == []
    assert summary["dry_run"] is True
    assert summary["calibration_mode"] is True
    assert summary["candidate_pool_count"] == 1
    assert candidate_scores[0]["candidate_score"] > 0
    assert "candidate_buckets" in gate_distribution
    assert funnel["raw"] == 1
    assert funnel["scored"] == 1
