import pytest

from shared.llm.circuit_breaker import reset_llm_circuit


@pytest.fixture(autouse=True)
def _reset_llm_circuit_between_tests():
    reset_llm_circuit()
    yield
    reset_llm_circuit()
