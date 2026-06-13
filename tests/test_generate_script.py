from generator.text.generate_script import _token_budgets
from generator.text.generate_scripts_from_filtered import _regenerate_reason_from_error


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
