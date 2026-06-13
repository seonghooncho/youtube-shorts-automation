import json

from generator.text.generate_script import DraftScript
from generator.text.filter_viable_posts import SourceScorecard, filter_viable_posts
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

    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT", "8")
    monkeypatch.setenv("SOURCE_LOCAL_PRERANK_ENABLED", "1")
    monkeypatch.setenv("SCRIPT_MAX_LLM_DRAFTS_PER_SOURCE", "2")
    monkeypatch.setenv("SCRIPT_ENABLE_JSON_FALLBACK", "0")
    monkeypatch.setenv("SCRIPT_CRITIC_ENABLED", "1")
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
    assert calls["source_scorecard"] <= 8
    assert calls["script"] <= len(viable) * 2
    assert summary["json_fallback_attempts"] == 0
    assert calls["critic"] == 0
    assert summary["critic_skipped"] == summary["final_accepted"]
    assert summary["source_scorecard_calls"] == 8
    assert summary["source_scorecard_skipped_by_prerank"] == 6
    assert "llm_call_estimate_total" in summary
    assert "estimated_output_token_budget_total" in summary
