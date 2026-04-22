from __future__ import annotations

import os
from typing import Dict

from app.spam.dns_auth import check_dns_authentication
from app.spam.guardrails import apply_send_guardrails
from app.spam.warmup import check_warmup_allowance, record_warmup_send


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def pre_send_check(to_email: str, subject: str, body: str, sender_email: str) -> Dict[str, object]:
    guard = apply_send_guardrails(to_email=to_email, subject=subject, body=body)
    if not bool(guard.get("allowed", True)):
        return {
            "allowed": False,
            "reason": str(guard.get("reason") or "blocked_by_guardrail"),
            "body": guard.get("body") or body,
            "spam_risk": guard.get("spam_risk") or {"score": 0, "risk": "low", "flags": []},
            "dns_auth": {"ready": False, "notes": ["blocked_before_dns_check"]},
            "warmup": {"allowed": True, "reason": "not_checked"},
        }

    dns_auth = {"ready": True, "notes": []}
    if _is_truthy(os.getenv("SPAM_ENABLE_DNS_CHECK", "false")):
        dns_auth = check_dns_authentication(sender_email)
        if _is_truthy(os.getenv("SPAM_BLOCK_IF_DNS_FAIL", "false")) and not bool(dns_auth.get("ready", False)):
            return {
                "allowed": False,
                "reason": "sender_domain_auth_not_ready",
                "body": guard.get("body") or body,
                "spam_risk": guard.get("spam_risk") or {"score": 0, "risk": "low", "flags": []},
                "dns_auth": dns_auth,
                "warmup": {"allowed": True, "reason": "not_checked"},
            }

    warmup = check_warmup_allowance(sender_email)
    if not bool(warmup.get("allowed", True)):
        return {
            "allowed": False,
            "reason": str(warmup.get("reason") or "warmup_blocked"),
            "body": guard.get("body") or body,
            "spam_risk": guard.get("spam_risk") or {"score": 0, "risk": "low", "flags": []},
            "dns_auth": dns_auth,
            "warmup": warmup,
        }

    return {
        "allowed": True,
        "reason": "",
        "body": guard.get("body") or body,
        "spam_risk": guard.get("spam_risk") or {"score": 0, "risk": "low", "flags": []},
        "dns_auth": dns_auth,
        "warmup": warmup,
    }


def post_send_record(sender_email: str, status: str) -> None:
    record_warmup_send(sender_email=sender_email, status=status)
