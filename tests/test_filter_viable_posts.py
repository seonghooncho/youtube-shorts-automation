import json

from generator.text.filter_viable_posts import (
    SourceScorecard,
    _ask_source_scorecard,
    _gate_fit_passes,
    _ask_yes_no,
    _is_llm_quota_error,
    _local_fallback_enabled,
    _local_precheck,
    _local_source_scorecard,
    _text_verbosity,
    compact_source_for_filter,
    filter_viable_posts,
    local_source_feasibility,
    local_source_priority,
    source_acceptance_score,
)


class _Response:
    output_text = "YES"


class _Responses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _Response()


class _Client:
    def __init__(self):
        self.responses = _Responses()


class _ScorecardResponse:
    output_text = """
    {
      "decision": "YES",
      "relatability": 5,
      "conflict_clarity": 5,
      "stakes": 4,
      "debate_potential": 5,
      "safe_adaptability": 5,
      "visualizability": 4,
      "gate_fit_score": 5,
      "hook_in_one_sentence": 5,
      "receipt_strength": 4,
      "visual_matchability": 5,
      "length_fit_score": 5,
      "metadata_repairability": 5,
      "retention_risk": "low",
      "archetype": "roommate_money",
      "reason": "Clear roommate conflict with a debatable bill."
    }
    """


class _ScorecardResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _ScorecardResponse()


class _ScorecardClient:
    def __init__(self):
        self.responses = _ScorecardResponses()


class _QuotaResponses:
    def create(self, **kwargs):
        raise RuntimeError("Error code: 429 - insufficient_quota")


class _QuotaClient:
    def __init__(self):
        self.responses = _QuotaResponses()


def test_ask_yes_no_omits_temperature_for_responses_api():
    client = _Client()

    assert _ask_yes_no(client, "Is this viable?", "gpt-5-mini") == "YES"
    assert "temperature" not in client.responses.kwargs
    assert client.responses.kwargs["max_output_tokens"] == 128
    assert client.responses.kwargs["reasoning"] == {"effort": "minimal"}


def test_ask_yes_no_omits_reasoning_for_non_gpt5_models():
    client = _Client()

    assert _ask_yes_no(client, "Is this viable?", "gpt-4.1-mini") == "YES"
    assert "reasoning" not in client.responses.kwargs


def test_ask_yes_no_omits_legacy_reasoning_for_gpt54_models():
    client = _Client()

    assert _ask_yes_no(client, "Is this viable?", "gpt-5.4-nano") == "YES"
    assert "reasoning" not in client.responses.kwargs


def test_ask_yes_no_uses_low_reasoning_for_gpt55():
    client = _Client()

    assert _ask_yes_no(client, "Is this viable?", "gpt-5.5") == "YES"
    assert client.responses.kwargs["reasoning"] == {"effort": "low"}


def test_ask_source_scorecard_returns_structured_fields():
    client = _ScorecardClient()

    scorecard = _ask_source_scorecard(client, "Evaluate this source", "gpt-5.4-nano")

    assert scorecard is not None
    assert scorecard.decision == "YES"
    assert scorecard.archetype == "roommate_money"
    assert scorecard.retention_risk == "low"
    assert _gate_fit_passes(scorecard) is True
    assert source_acceptance_score(scorecard) >= 4.0
    assert client.responses.kwargs["text"]["format"]["type"] == "json_object"


def test_source_acceptance_score_uses_gate_aware_fields():
    strong = _local_source_scorecard(
        {
            "title": "My roommate put the whole bill on my card",
            "content": " ".join(["The receipt and group chat showed the bill was put on my card without asking."] * 20),
        }
    )
    weak = strong.model_copy(
        update={
            "gate_fit_score": 2,
            "hook_in_one_sentence": 2,
            "receipt_strength": 2,
            "visual_matchability": 2,
            "length_fit_score": 2,
            "metadata_repairability": 2,
        }
    )

    assert source_acceptance_score(strong) > source_acceptance_score(weak)
    assert source_acceptance_score(weak) < 4.0


def _strong_scorecard(archetype: str = "roommate_money") -> SourceScorecard:
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
        archetype=archetype,
        reason="Strong local conflict with receipts.",
    )


def _raw_post(idx: int, *, strong: bool = True) -> dict:
    if strong:
        sentence = (
            f"My roommate charged dinner {idx} to my card without asking, and the receipt, "
            "group chat messages, screenshot, and timestamp showed exactly what happened."
        )
    else:
        sentence = (
            f"I had a long disagreement {idx} with someone at home, and we talked about it "
            "for a while before deciding nobody was sure what to do."
        )
    content = " ".join([sentence] * 12) + " Would you have refused to pay?"
    return {
        "id": f"post-{idx}",
        "title": f"Post {idx}",
        "content": content,
        "content_char_count": len(content),
        "content_word_count": len(content.split()),
        "source_provider": "reddit",
    }


def test_filter_prerank_caps_llm_source_scorecard_calls(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    raw_path.write_text(json.dumps([_raw_post(i, strong=i < 10) for i in range(14)]), encoding="utf-8")
    calls = {"scorecard": 0}

    def fake_scorecard(*_args, **_kwargs):
        calls["scorecard"] += 1
        return _strong_scorecard()

    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT", "8")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "20")
    monkeypatch.setenv("SOURCE_LOCAL_PRERANK_ENABLED", "1")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_ACCEPT_ENABLED", "0")
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", fake_scorecard)

    filter_viable_posts()

    summary = json.loads((tmp_path / "source_filter_summary.json").read_text(encoding="utf-8"))
    assert calls["scorecard"] == 8
    assert summary["source_scorecard_calls"] == 8
    assert summary["source_scorecard_skipped_by_prerank"] == 2
    assert summary["local_feasibility_rejected"] == 4
    assert summary["local_priority_cutoff_score"] > 0
    assert len(summary["local_priority_top_scores"]) == 10
    assert summary["local_priority_min_evaluated"] == summary["local_priority_cutoff_score"]
    assert summary["prerank_skipped_examples"]
    assert summary["source_rejection_reason_counts"]["below_local_prerank_cutoff"] == 2
    assert summary["accepted_source_archetypes"]["roommate_money"] == 8
    assert len(summary["accepted_examples"]) == 5
    assert summary["accepted_examples"][0]["id"]
    assert summary["accepted_examples"][0]["source_priority_score"] > 0
    assert summary["accepted_examples"][0]["archetype"] == "roommate_money"
    assert "Strong local conflict" in summary["accepted_examples"][0]["reason"]


def test_filter_stops_scorecard_calls_after_quota(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    raw_path.write_text(json.dumps([_raw_post(i, strong=True) for i in range(6)]), encoding="utf-8")
    calls = {"scorecard": 0}

    def quota_scorecard(*_args, **_kwargs):
        calls["scorecard"] += 1
        raise RuntimeError("llm_quota_unavailable: insufficient_quota")

    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT", "6")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "20")
    monkeypatch.setenv("SOURCE_LOCAL_PRERANK_ENABLED", "1")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_ACCEPT_ENABLED", "0")
    monkeypatch.delenv("FILTER_LOCAL_FALLBACK_ENABLED", raising=False)
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", quota_scorecard)

    filter_viable_posts()

    summary = json.loads((tmp_path / "source_filter_summary.json").read_text(encoding="utf-8"))
    viable = json.loads(viable_path.read_text(encoding="utf-8"))
    assert calls["scorecard"] == 1
    assert viable == []
    assert summary["source_scorecard_calls"] == 1
    assert summary["source_scorecard_skipped_after_quota"] == 5
    assert "llm_quota_unavailable" in summary["llm_unavailable_reason"]
    assert summary["llm_circuit_open"] is True


def test_filter_target_two_builds_wider_source_pool(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    raw_path.write_text(json.dumps([_raw_post(i, strong=True) for i in range(10)]), encoding="utf-8")
    calls = {"scorecard": 0}

    def fake_scorecard(*_args, **_kwargs):
        calls["scorecard"] += 1
        return _strong_scorecard()

    monkeypatch.delenv("SOURCE_LLM_EVAL_LIMIT", raising=False)
    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT_DEFAULT", "4")
    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT_MAX", "6")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "2")
    monkeypatch.setenv("SOURCE_LOCAL_PRERANK_ENABLED", "1")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_ACCEPT_ENABLED", "0")
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", fake_scorecard)

    filter_viable_posts()

    summary = json.loads((tmp_path / "source_filter_summary.json").read_text(encoding="utf-8"))
    viable = json.loads(viable_path.read_text(encoding="utf-8"))
    assert calls["scorecard"] == 4
    assert len(viable) == 4
    assert summary["source_llm_eval_limit"] == 4
    assert summary["source_accepted_pool_target"] == 10
    assert summary["source_filter_stopped_after_target"] is False


def test_high_confidence_sources_skip_llm_scorecard_by_default(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    raw_path.write_text(json.dumps([_raw_post(i, strong=True) for i in range(4)]), encoding="utf-8")
    calls = {"scorecard": 0}

    def fake_scorecard(*_args, **_kwargs):
        calls["scorecard"] += 1
        raise AssertionError("high-confidence local sources should not call scorecard first")

    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT", "4")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "2")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_ACCEPT_LIMIT", "2")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_MIN_PRIORITY", "3.0")
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", fake_scorecard)

    filter_viable_posts()

    viable = json.loads(viable_path.read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "source_filter_summary.json").read_text(encoding="utf-8"))
    assert calls["scorecard"] == 0
    assert len(viable) == 2
    assert all(post["source_scorecard_path"] == "local_high_confidence" for post in viable)
    assert summary["local_high_confidence_accepted"] == 2
    assert summary["source_scorecard_skipped_by_local_accept"] == 2
    assert summary["source_scorecard_calls"] == 0


def test_local_feasibility_rejects_sources_before_llm(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    weak = _raw_post(1, strong=False)
    weak["content"] = " ".join(["We had a long vague disagreement at home without any specific object."] * 14)
    weak["content_char_count"] = len(weak["content"])
    weak["content_word_count"] = len(weak["content"].split())
    raw_path.write_text(json.dumps([weak, _raw_post(2, strong=True)]), encoding="utf-8")
    calls = {"scorecard": 0}

    def fake_scorecard(*_args, **_kwargs):
        calls["scorecard"] += 1
        return _strong_scorecard()

    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "20")
    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT", "8")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_ACCEPT_ENABLED", "0")
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", fake_scorecard)

    filter_viable_posts()

    summary = json.loads((tmp_path / "source_filter_summary.json").read_text(encoding="utf-8"))
    assert calls["scorecard"] == 1
    assert summary["local_feasibility_rejected"] == 1
    assert summary["local_feasibility_rejected_examples"][0]["id"] == weak["id"]


def test_local_feasibility_accepts_concrete_receipt_source():
    feasibility = local_source_feasibility(_raw_post(1, strong=True))

    assert feasibility["likely_script_gate_fit"] is True
    assert feasibility["can_make_title"] is True
    assert feasibility["can_make_opening_visual_query"] is True
    assert feasibility["has_receipt_or_concrete_detail"] is True


def test_generation_preflight_only_makes_zero_llm_calls(monkeypatch, tmp_path, capsys):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    raw_path.write_text(json.dumps([_raw_post(i, strong=True) for i in range(3)]), encoding="utf-8")
    calls = {"scorecard": 0}

    def fake_scorecard(*_args, **_kwargs):
        calls["scorecard"] += 1
        return _strong_scorecard()

    monkeypatch.setenv("GENERATION_PREFLIGHT_ONLY", "1")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "2")
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", fake_scorecard)

    filter_viable_posts()

    summary = json.loads((tmp_path / "source_preflight_summary.json").read_text(encoding="utf-8"))
    assert calls["scorecard"] == 0
    assert json.loads(viable_path.read_text(encoding="utf-8")) == []
    assert summary["preflight_only"] is True
    assert summary["expected_source_scorecard_calls"] == 3
    assert "PREFLIGHT SUMMARY" in capsys.readouterr().out


def test_filter_compacts_long_source_and_preserves_receipts_and_question():
    long_middle = " ".join([f"Slow context sentence {i} about unrelated history." for i in range(120)])
    receipt = "The receipt screenshot showed the dinner bill was charged to my card at 8:42."
    final_question = "Would you have refused to cover the card charge too?"
    content = (
        "My roommate charged dinner to my card without asking. "
        + long_middle
        + " "
        + receipt
        + " "
        + "More unrelated context. " * 80
        + final_question
    )
    post = {"title": "Dinner Card Charge", "content": content}

    compacted = compact_source_for_filter(post, 1200)

    assert len(compacted) <= 1200
    assert "receipt screenshot" in compacted
    assert final_question in compacted


def test_filter_summary_records_compaction_counts(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    long_post = _raw_post(1) | {"content": " ".join([_raw_post(1)["content"]] * 8)}
    long_post["content_char_count"] = len(long_post["content"])
    long_post["content_word_count"] = len(long_post["content"].split())
    raw_path.write_text(json.dumps([long_post]), encoding="utf-8")

    monkeypatch.setenv("FILTER_SOURCE_MAX_CHARS", "900")
    monkeypatch.setenv("FILTER_SOURCE_LONG_POST_MAX_CHARS", "800")
    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT", "8")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "20")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_ACCEPT_ENABLED", "0")
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", lambda *_args, **_kwargs: _strong_scorecard())

    filter_viable_posts()

    summary = json.loads((tmp_path / "source_filter_summary.json").read_text(encoding="utf-8"))
    assert summary["filter_prompt_compacted_count"] == 1
    assert summary["filter_prompt_total_chars_after"] < summary["filter_prompt_total_chars_before"]


def test_local_priority_sorts_strong_sources_first(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    posts = [_raw_post(1, strong=False), _raw_post(2, strong=True), _raw_post(3, strong=True)]
    raw_path.write_text(json.dumps(posts), encoding="utf-8")
    evaluated_titles = []

    def fake_scorecard(_client, prompt, _model):
        marker = next(line for line in prompt.splitlines() if line.strip().startswith("Title:"))
        evaluated_titles.append(marker)
        return _strong_scorecard()

    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT", "2")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "20")
    monkeypatch.setenv("SOURCE_LOCAL_PRERANK_ENABLED", "1")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_ACCEPT_ENABLED", "0")
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", fake_scorecard)

    filter_viable_posts()

    assert "Title: Post 2" in evaluated_titles[0]
    assert "Title: Post 3" in evaluated_titles[1]
    assert local_source_priority(posts[1]) > local_source_priority(posts[0])


def test_thin_post_rejected_before_source_scorecard(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    thin = {"id": "thin", "title": "Thin", "content": "Too short.", "source_provider": "reddit"}
    raw_path.write_text(json.dumps([thin, _raw_post(1)]), encoding="utf-8")
    calls = {"scorecard": 0}

    def fake_scorecard(*_args, **_kwargs):
        calls["scorecard"] += 1
        return _strong_scorecard()

    monkeypatch.setenv("SOURCE_LLM_EVAL_LIMIT", "8")
    monkeypatch.setenv("TARGET_ACCEPTED_SCRIPTS", "20")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_ACCEPT_ENABLED", "0")
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: object())
    monkeypatch.setattr("generator.text.filter_viable_posts._ask_source_scorecard", fake_scorecard)

    filter_viable_posts()

    assert calls["scorecard"] == 1


def test_filter_text_verbosity_uses_medium_for_gpt_41(monkeypatch):
    monkeypatch.delenv("FILTER_TEXT_VERBOSITY", raising=False)

    assert _text_verbosity("gpt-4.1-mini") == "medium"
    assert _text_verbosity("gpt-5.4-nano") == "low"


def test_filter_text_verbosity_allows_env_override(monkeypatch):
    monkeypatch.setenv("FILTER_TEXT_VERBOSITY", "high")

    assert _text_verbosity("gpt-4.1-mini") == "high"


def test_ask_source_scorecard_raises_on_quota_error():
    client = _QuotaClient()

    try:
        _ask_source_scorecard(client, "Evaluate this source", "gpt-5.4-nano")
    except RuntimeError as exc:
        assert "llm_quota_unavailable" in str(exc)
    else:
        raise AssertionError("expected quota error")


def test_local_source_scorecard_accepts_synthetic_boundary_source():
    post = {
        "source_provider": "synthetic",
        "title": "AITA for refusing to cover a family bill?",
        "content": (
            "I had one clear boundary in this situation: I would not put the whole bill on my card. "
            "Then my aunt told the server to charge my card without asking. "
            "The group chat proof showed everyone agreed to pay their own share."
        ),
    }

    scorecard = _local_source_scorecard(post, "insufficient_quota")

    assert _is_llm_quota_error("You exceeded your current quota")
    assert scorecard.decision == "YES"
    assert scorecard.archetype == "money_pressure"
    assert scorecard.retention_risk == "medium"


def test_filter_local_fallback_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FILTER_LOCAL_FALLBACK_ENABLED", raising=False)

    assert _local_fallback_enabled() is False


def test_local_source_scorecard_does_not_bonus_synthetic_provider():
    content = (
        "I had one clear boundary: my roommate could not put shared bills on my card. "
        "Then she charged a dinner receipt without permission and blamed me in the group chat. "
        "The messages showed I had only agreed to cover my own order."
    )
    synthetic = _local_source_scorecard({"source_provider": "synthetic", "title": "Card bill", "content": content})
    reddit = _local_source_scorecard({"source_provider": "reddit", "title": "Card bill", "content": content})

    assert synthetic.relatability == reddit.relatability
    assert synthetic.debate_potential == reddit.debate_potential


def test_llm_quota_failure_in_production_does_not_locally_approve(monkeypatch, tmp_path):
    raw_path = tmp_path / "raw_posts.json"
    viable_path = tmp_path / "viable_posts.json"
    content = " ".join(
        [
            "My roommate put our shared dinner bill on my card without asking.",
            "I had already said in the group chat that everyone needed to pay their own share.",
            "The receipt showed twelve separate dinners, two desserts, and drinks I never ordered.",
            "When I disputed the charge, she told our friends I embarrassed her at the restaurant.",
            "I sent screenshots of the message where she promised to split the bill.",
            "Now she says I made her look cheap, but she used my card before I agreed.",
        ]
        * 4
    )
    raw_path.write_text(
        json.dumps(
            [
                {
                    "id": "quota-1",
                    "title": "My Roommate Put Twelve Dinners On My Card",
                    "content": content,
                    "content_char_count": len(content),
                    "content_word_count": len(content.split()),
                    "source_provider": "reddit",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SOURCE_LOCAL_HIGH_CONFIDENCE_ACCEPT_ENABLED", "0")
    monkeypatch.delenv("FILTER_LOCAL_FALLBACK_ENABLED", raising=False)
    monkeypatch.setattr("generator.text.filter_viable_posts.RAW_POSTS_FILE", raw_path)
    monkeypatch.setattr("generator.text.filter_viable_posts.VIABLE_POSTS_FILE", viable_path)
    monkeypatch.setattr("generator.text.filter_viable_posts._get_client", lambda: _QuotaClient())

    filter_viable_posts()

    assert json.loads(viable_path.read_text(encoding="utf-8")) == []


def test_local_precheck_rejects_minor_romance_source():
    reason = _local_precheck(
        {
            "title": "AITA for asking my girlfriend to be public?",
            "content": (
                "I am 18 and my girlfriend is 17. We were dating for months, but she kept saying "
                "she was single in public. I asked her to treat me like her boyfriend around friends, "
                "and she said I was being dramatic. The relationship argument went in circles until "
                "friends started taking sides."
            ),
        }
    )

    assert "minors" in reason
