import json

from generator.text.generate_script import DraftScript
from generator.text.filter_viable_posts import SourceScorecard, filter_viable_posts
from generator.text import generate_scripts_from_filtered as generation_module
from generator.text.generate_scripts_from_filtered import generate_scripts_from_filtered


def _scorecard() -> SourceScorecard:
    return SourceScorecard(
        decision="YES",
        relatability=5,
        conflict_clarity=5,
        stakes=5,
        debate_potential=5,
        safe_adaptability=5,
        visualizability=5,
        gate_fit_score=5,
        hook_in_one_sentence=5,
        receipt_strength=5,
        visual_matchability=5,
        length_fit_score=5,
        metadata_repairability=5,
        retention_risk="low",
        archetype="roommate_money",
        reason="Receipt-backed roommate money conflict.",
    )


def _raw_post(idx: int) -> dict:
    sentence = (
        f"My roommate charged dinner order {idx} to my card without asking, and the receipt, "
        "group chat messages, screenshot, and timestamp showed everyone had agreed to split it."
    )
    content = " ".join([sentence] * 12) + " Would you have refused to pay the card charge?"
    return {
        "id": f"source-{idx}",
        "title": f"My Roommate Charged Dinner {idx} To My Card",
        "content": content,
        "content_char_count": len(content),
        "content_word_count": len(content.split()),
        "source_provider": "reddit",
        "source_url": f"https://reddit.test/source-{idx}",
    }


def _draft(idx: int) -> DraftScript:
    lines = [
        f"My roommate charged dinner order {idx} to my card before I even saw the receipt.",
        "The group chat already said everyone was paying for their own food before we ordered anything.",
        "She added two desserts, three drinks, and a second entree I never ordered to the same receipt.",
        "When I asked for the money back, she said I embarrassed her at the restaurant in front of everyone.",
        "I sent the screenshot where she promised to split the bill before we sat down at the table.",
        "Then she told our friends I was making her look cheap over one dinner instead of admitting she used my card.",
        "I disputed only the extra items and stopped putting my card down for the group after that receipt.",
        "Would you have refused to cover the charge too?",
    ]
    return DraftScript(
        title=f"Roommate Charged Dinner {idx} To My Card",
        voice="neutral",
        source_summary="A roommate puts a dinner order on the narrator's card despite a group chat agreement to split the bill.",
        story_beats=[
            "The roommate charges dinner to the narrator's card.",
            "The group chat says everyone should pay their own share.",
            "The receipt includes items the narrator did not order.",
            "The narrator disputes only the extra items.",
        ],
        adaptation_strategy="Compressed the source into the card charge, group chat receipt, restaurant accusation, and final refusal.",
        retention_angle="A card charge, restaurant receipt, group chat screenshot, and roommate accusation create a fast money dispute.",
        turning_point="The receipt showed extra items after the group chat had already promised a split bill.",
        payoff_line="I stopped putting my card down for the group.",
        viewer_question="Would you have refused to cover the charge too?",
        marketability_score=5,
        retention_risk="The rewrite starts with the card charge and gets to the receipt quickly.",
        rewrite_notes="Kept the money conflict and receipt in the first half.",
        hook_type="money_pressure",
        style_variant="money_trap",
        voiceover_lines=lines,
    )


def test_end_to_end_cost_budget_controls(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    final_path = tmp_path / "final_metadata.json"
    failed_path = tmp_path / "failed_posts.json"
    raw_path.write_text(json.dumps([_raw_post(i) for i in range(14)]), encoding="utf-8")
    calls = {"source_scorecard": 0, "script": 0, "critic": 0}

    def fake_scorecard(*_args, **_kwargs):
        calls["source_scorecard"] += 1
        return _scorecard()

    def fake_script(_title, _content, post=None, regenerate_reason=None):
        calls["script"] += 1
        draft = _draft(calls["script"])
        object.__setattr__(
            draft,
            "_generation_telemetry",
            {
                "structured_attempts": 1,
                "json_fallback_attempts": 0,
                "structured_failures": 0,
                "json_fallback_failures": 0,
                "estimated_output_token_budget_total": 1600,
            },
        )
        return draft

    monkeypatch.delenv("SOURCE_LLM_EVAL_LIMIT", raising=False)
    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT_DEFAULT", "4")
    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT_MAX", "6")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "2")
    monkeypatch.setenv("SOURCE_LOCAL_PRERANK_ENABLED", "1")
    monkeypatch.setenv("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", "1")
    monkeypatch.setenv("SCRIPT_STOP_AFTER_ACCEPTED_TARGET", "1")
    monkeypatch.setenv("SCRIPT_ENABLE_JSON_FALLBACK", "0")
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "0")
    monkeypatch.setenv("SCRIPT_CRITIC_STAGE", "after_local_gate")
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", fake_scorecard)

    filter_viable_posts()

    viable = json.loads(viable_path.read_text(encoding="utf-8"))
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FINAL_METADATA_FILE", final_path)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.FAILED_POSTS_FILE", failed_path)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered._load_previous_accepted_metadata", lambda: [])
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.call_gpt_generate_script", fake_script)
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.validate_script_quality", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.batch_diversity_issues", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("generator.text.generate_scripts_from_filtered.critique_script", lambda *_args, **_kwargs: calls.__setitem__("critic", calls["critic"] + 1))

    generate_scripts_from_filtered()

    summary = json.loads((tmp_path / "generation_summary.json").read_text(encoding="utf-8"))
    assert calls["source_scorecard"] == 0
    assert calls["script"] <= 2
    assert summary["json_fallback_attempts"] == 0
    assert calls["critic"] == 0
    assert summary["critic_calls_attempted"] == 0
    assert summary["source_scorecard_calls"] == 0
    assert summary["source_scorecard_skipped_by_local_accept"] == 2
    assert summary["source_scorecard_skipped_by_prerank"] == 10
    assert summary["final_accepted"] == 2
    assert summary["final_rejected"] == 0
    assert summary["target_accepted_scripts"] == 2
    assert summary["stopped_after_target"] is True
    assert summary["llm_calls_by_stage"]["source_scorecard"] == 0
    assert summary["llm_calls_by_stage"]["critic"] == 0
    assert summary["minimum_once_token_budget"] == summary["actual_token_budget"]
    assert summary["token_overhead"] == 0
    assert summary["token_overhead_rate"] == 0
    assert summary["token_overhead_status"] == "ok"
    assert summary["actual_token_budget_by_stage"]["source_scorecard"] == 0
    assert summary["actual_token_budget_by_stage"]["initial_script_draft"] == 2 * 1600
    for key in (
        "llm_call_estimate_total",
        "estimated_output_token_budget_total",
        "minimum_once_token_budget",
        "actual_token_budget",
        "token_overhead_rate",
        "token_overhead_by_stage",
        "source_scorecard_calls",
        "source_scorecard_skipped_by_prerank",
        "critic_skipped",
        "final_accepted",
        "final_rejected",
    ):
        assert key in summary
    assert summary["operator_recommendation"].startswith("OK")


def _token_item(**overrides) -> dict:
    item = {
        "id": "token-item",
        "candidate_bucket": "accepted",
        "generation_attempt_count": 1,
        "llm_draft_count": 1,
        "generation_telemetry": {
            "structured_attempts": 1,
            "json_fallback_attempts": 0,
            "structured_failures": 0,
            "json_fallback_failures": 0,
            "estimated_output_token_budget_total": 1600,
        },
    }
    item.update(overrides)
    return item


def test_token_overhead_counts_near_miss_rewrite(monkeypatch):
    monkeypatch.setattr(generation_module, "_load_source_filter_summary", lambda: {})
    initial = _token_item(id="source-1", candidate_bucket="near_miss")
    rewrite = _token_item(id="source-1", near_miss_rewrite=True, llm_draft_count=2, generation_attempt_count=2)

    summary = generation_module._generation_summary([{"id": "source-1"}], [rewrite], [], candidate_pool=[initial, rewrite])

    assert summary["minimum_once_token_budget"] == 1600
    assert summary["actual_token_budget"] == 3200
    assert summary["token_overhead"] == 1600
    assert summary["token_overhead_rate"] == 1
    assert summary["token_overhead_status"] == "above_target"
    assert summary["token_overhead_by_stage"]["near_miss_rewrite"] == 1600
    assert summary["operator_recommendation"].startswith("CHECK_TOKEN_OVERHEAD")


def test_token_overhead_counts_json_fallback(monkeypatch):
    monkeypatch.setattr(generation_module, "_load_source_filter_summary", lambda: {})
    item = _token_item(
        generation_telemetry={
            "structured_attempts": 1,
            "json_fallback_attempts": 1,
            "structured_failures": 1,
            "json_fallback_failures": 0,
            "estimated_output_token_budget_total": 3200,
        }
    )

    summary = generation_module._generation_summary([{"id": "source-1"}], [item], [], candidate_pool=[item])

    assert summary["minimum_once_token_budget"] == 1600
    assert summary["actual_token_budget"] == 3200
    assert summary["token_overhead_by_stage"]["json_fallback"] == 1600
    assert summary["token_overhead_status"] == "above_target"


def test_token_overhead_counts_same_source_retry_when_telemetry_only_has_last_call(monkeypatch):
    monkeypatch.setattr(generation_module, "_load_source_filter_summary", lambda: {})
    item = _token_item(generation_attempt_count=3, llm_draft_count=3)

    summary = generation_module._generation_summary([{"id": "source-1"}], [item], [], candidate_pool=[item])

    assert summary["minimum_once_token_budget"] == 1600
    assert summary["actual_token_budget"] == 4800
    assert summary["token_overhead_by_stage"]["same_source_retry"] == 3200
    assert summary["token_overhead_rate"] == 2
    assert summary["token_overhead_status"] == "above_target"


def test_token_overhead_counts_tts_regenerate_marker(monkeypatch):
    monkeypatch.setattr(generation_module, "_load_source_filter_summary", lambda: {})
    initial = _token_item(id="source-1")
    tts_regenerate = _token_item(id="source-1", tts_regenerate=True)

    summary = generation_module._generation_summary([{"id": "source-1"}], [initial], [], candidate_pool=[initial, tts_regenerate])

    assert summary["minimum_once_token_budget"] == 1600
    assert summary["actual_token_budget"] == 3200
    assert summary["token_overhead_by_stage"]["tts_regenerate"] == 1600
    assert summary["token_overhead_status"] == "above_target"


def test_select_final_candidates_uses_near_miss_backup_without_retry(monkeypatch):
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "2")
    monkeypatch.setenv("CANDIDATE_BACKUP_ACCEPT_SCORE", "70")
    accepted = _token_item(id="accepted", candidate_bucket="accepted", candidate_score=82, hard_blockers=[])
    backup = _token_item(id="backup", candidate_bucket="near_miss", candidate_score=74, hard_blockers=[])
    weak = _token_item(id="weak", candidate_bucket="near_miss", candidate_score=69, hard_blockers=[])

    selected = generation_module._select_final_candidates([weak, backup, accepted])

    assert [item["id"] for item in selected] == ["accepted", "backup"]
    assert selected[1]["selected_as_backup_candidate"] is True
    assert selected[1]["candidate_selection_reason"] == "near_miss_backup_above_floor"


def test_near_miss_rewrite_skips_when_backup_candidates_can_fill_target(monkeypatch):
    calls = {"llm": 0}
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "2")
    monkeypatch.setenv("CANDIDATE_BACKUP_ACCEPT_SCORE", "70")
    monkeypatch.setattr(generation_module, "llm_circuit_is_open", lambda: False)
    monkeypatch.setattr(
        generation_module,
        "call_gpt_generate_script",
        lambda *_args, **_kwargs: calls.__setitem__("llm", calls["llm"] + 1),
    )
    candidate_pool = [
        _token_item(id="accepted", candidate_bucket="accepted", candidate_score=82, hard_blockers=[]),
        _token_item(id="backup", candidate_bucket="near_miss", candidate_score=74, hard_blockers=[]),
    ]

    generation_module._rewrite_near_miss_candidates(candidate_pool, [{"id": "backup"}], [])

    assert calls["llm"] == 0
