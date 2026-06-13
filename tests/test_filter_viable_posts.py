from generator.text.filter_viable_posts import _ask_source_scorecard, _ask_yes_no, _local_precheck


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
    assert client.responses.kwargs["text"]["format"]["type"] == "json_object"


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
