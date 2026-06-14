from __future__ import annotations

import time
from typing import Any


class LlmCircuitOpen(RuntimeError):
    """Raised when this process should stop making LLM calls."""


_STATE: dict[str, Any] = {
    "open": False,
    "reason": "",
    "stage": "",
    "opened_at": None,
}


def _message(exc_or_message: Any) -> str:
    if exc_or_message is None:
        return ""
    return str(exc_or_message)


def is_llm_quota_or_auth_error(exc_or_message: Any) -> bool:
    lowered = _message(exc_or_message).lower()
    quota_or_auth_terms = (
        "insufficient_quota",
        "exceeded your current quota",
        "quota",
        "invalid_api_key",
        "invalid api key",
        "openai_api_key",
        "api_key",
        "api key",
        "auth",
        "unauthorized",
        "permission",
        "forbidden",
        "401",
        "403",
    )
    return any(term in lowered for term in quota_or_auth_terms)


def is_llm_rate_limit_error(exc_or_message: Any) -> bool:
    lowered = _message(exc_or_message).lower()
    return "429" in lowered or "rate_limit" in lowered or "rate limit" in lowered


def open_llm_circuit(reason: str, stage: str) -> None:
    _STATE.update(
        {
            "open": True,
            "reason": str(reason or "")[:500],
            "stage": str(stage or "unknown")[:120],
            "opened_at": time.time(),
        }
    )


def llm_circuit_is_open() -> bool:
    return bool(_STATE.get("open"))


def assert_llm_circuit_closed(stage: str) -> None:
    if not llm_circuit_is_open():
        return
    summary = llm_circuit_summary()
    raise LlmCircuitOpen(
        f"llm_circuit_open: stage={summary.get('llm_circuit_stage')}; "
        f"reason={summary.get('llm_circuit_reason')}; requested_stage={stage}"
    )


def llm_circuit_summary() -> dict[str, Any]:
    return {
        "llm_circuit_open": bool(_STATE.get("open")),
        "llm_circuit_stage": _STATE.get("stage") or "",
        "llm_circuit_reason": _STATE.get("reason") or "",
        "llm_circuit_opened_at": _STATE.get("opened_at"),
    }


def reset_llm_circuit() -> None:
    _STATE.update({"open": False, "reason": "", "stage": "", "opened_at": None})
