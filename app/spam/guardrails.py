from __future__ import annotations

import os
import re
from typing import Dict


def _csv_env(name: str) -> set[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def recipient_is_suppressed(to_email: str) -> bool:
    email = (to_email or "").strip().lower()
    if not email:
        return True

    suppressed_emails = _csv_env("SUPPRESSION_EMAILS")
    suppressed_domains = _csv_env("SUPPRESSION_DOMAINS")

    if email in suppressed_emails:
        return True

    if "@" in email:
        domain = email.split("@", 1)[1]
        if domain in suppressed_domains:
            return True

    return False


def _unsubscribe_footer() -> str:
    unsubscribe_email = (os.getenv("UNSUBSCRIBE_EMAIL") or "unsubscribe@stayconnect.io").strip()
    unsubscribe_url = (os.getenv("UNSUBSCRIBE_URL") or "").strip()

    if unsubscribe_url:
        return (
            "\n\n---\n"
            f"To unsubscribe from future outreach, reply STOP or visit: {unsubscribe_url}\n"
            f"You may also email: {unsubscribe_email}"
        )

    return (
        "\n\n---\n"
        "To unsubscribe from future outreach, reply STOP to this email.\n"
        f"For removal requests, contact: {unsubscribe_email}"
    )


def ensure_unsubscribe_capability(body: str) -> str:
    text = (body or "").rstrip()
    lower = text.lower()

    unsubscribe_markers = ["unsubscribe", "reply stop", "opt out", "remove me"]
    if any(marker in lower for marker in unsubscribe_markers):
        return text

    return text + _unsubscribe_footer() if text else _unsubscribe_footer().lstrip()


def spam_risk_score(subject: str, body: str) -> Dict[str, object]:
    s = (subject or "").strip()
    b = (body or "").strip()
    joined = f"{s}\n{b}".lower()

    score = 0
    flags: list[str] = []

    if joined.count("!") >= 4:
        score += 20
        flags.append("excessive_exclamation")

    spam_terms = ["guaranteed", "act now", "limited time", "free money", "risk free", "winner"]
    hit_terms = [term for term in spam_terms if term in joined]
    if hit_terms:
        score += min(40, 10 * len(hit_terms))
        flags.append("spam_phrases")

    words = re.findall(r"[A-Za-z]{3,}", f"{s} {b}")
    if words:
        caps_ratio = sum(1 for w in words if w.isupper()) / len(words)
        if caps_ratio >= 0.25:
            score += 20
            flags.append("high_caps_ratio")

    if len(b) < 40:
        score += 10
        flags.append("very_short_body")

    risk = "low"
    if score >= 50:
        risk = "high"
    elif score >= 25:
        risk = "medium"

    return {"score": score, "risk": risk, "flags": flags}


def apply_send_guardrails(to_email: str, subject: str, body: str) -> Dict[str, object]:
    if recipient_is_suppressed(to_email):
        return {
            "allowed": False,
            "reason": "Recipient is suppressed by send-layer compliance controls.",
            "body": body,
            "spam_risk": {"score": 0, "risk": "low", "flags": []},
        }

    final_body = ensure_unsubscribe_capability(body)
    risk = spam_risk_score(subject, final_body)

    return {
        "allowed": True,
        "reason": "",
        "body": final_body,
        "spam_risk": risk,
    }
