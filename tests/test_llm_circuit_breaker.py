import pytest

from shared.llm.circuit_breaker import (
    LlmCircuitOpen,
    assert_llm_circuit_closed,
    is_llm_quota_or_auth_error,
    is_llm_rate_limit_error,
    llm_circuit_is_open,
    llm_circuit_summary,
    open_llm_circuit,
)


def test_quota_auth_and_rate_limit_detection():
    assert is_llm_quota_or_auth_error("Error code: 429 - insufficient_quota")
    assert is_llm_quota_or_auth_error("invalid_api_key")
    assert is_llm_quota_or_auth_error("permission denied")
    assert is_llm_rate_limit_error("rate_limit_exceeded")
    assert is_llm_rate_limit_error("429")


def test_open_circuit_blocks_later_llm_calls():
    open_llm_circuit("insufficient_quota", "source_filter")

    assert llm_circuit_is_open() is True
    with pytest.raises(LlmCircuitOpen, match="llm_circuit_open"):
        assert_llm_circuit_closed("script_generation")

    summary = llm_circuit_summary()
    assert summary["llm_circuit_open"] is True
    assert summary["llm_circuit_stage"] == "source_filter"
    assert summary["llm_circuit_reason"] == "insufficient_quota"
