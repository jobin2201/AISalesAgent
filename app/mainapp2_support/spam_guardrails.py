"""Backward-compatible wrapper for send-layer spam guardrails."""

from app.spam.guardrails import apply_send_guardrails, ensure_unsubscribe_capability, recipient_is_suppressed, spam_risk_score

__all__ = [
    "apply_send_guardrails",
    "ensure_unsubscribe_capability",
    "recipient_is_suppressed",
    "spam_risk_score",
]
