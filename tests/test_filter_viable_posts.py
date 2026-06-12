from generator.text.filter_viable_posts import _ask_yes_no


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
