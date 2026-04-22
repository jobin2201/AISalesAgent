from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Dict

from app.data.database import db


_warmup_collection = db["spam_warmup"]


def _enabled() -> bool:
    return (os.getenv("SPAM_WARMUP_ENABLED") or "false").strip().lower() in {"1", "true", "yes", "on"}


def _daily_cap_for_day(day_number: int) -> int:
    # Conservative warm-up defaults.
    if day_number <= 7:
        return 40
    if day_number <= 14:
        return 100
    if day_number <= 21:
        return 200
    return 400


def _domain_from_email(sender_email: str) -> str:
    value = (sender_email or "").strip().lower()
    if "@" not in value:
        return ""
    return value.split("@", 1)[1]


def check_warmup_allowance(sender_email: str) -> Dict[str, object]:
    if not _enabled():
        return {"allowed": True, "reason": "warmup_disabled", "limit": 0, "used": 0}

    domain = _domain_from_email(sender_email)
    if not domain:
        return {"allowed": False, "reason": "invalid_sender_domain", "limit": 0, "used": 0}

    now = datetime.now(timezone.utc)
    doc = _warmup_collection.find_one({"domain": domain})
    if not doc:
        _warmup_collection.insert_one(
            {
                "domain": domain,
                "first_seen_at": now,
                "sent_today": 0,
                "window_start": now.replace(hour=0, minute=0, second=0, microsecond=0),
                "updated_at": now,
            }
        )
        doc = _warmup_collection.find_one({"domain": domain}) or {}

    first_seen = doc.get("first_seen_at") or now
    day_number = max(1, (now.date() - first_seen.date()).days + 1)

    window_start = doc.get("window_start")
    if not window_start or window_start.date() != now.date():
        _warmup_collection.update_one(
            {"domain": domain},
            {
                "$set": {
                    "window_start": now.replace(hour=0, minute=0, second=0, microsecond=0),
                    "sent_today": 0,
                    "updated_at": now,
                }
            },
        )
        sent_today = 0
    else:
        sent_today = int(doc.get("sent_today") or 0)

    cap = _daily_cap_for_day(day_number)
    if sent_today >= cap:
        return {
            "allowed": False,
            "reason": "daily_warmup_cap_reached",
            "limit": cap,
            "used": sent_today,
        }

    return {
        "allowed": True,
        "reason": "",
        "limit": cap,
        "used": sent_today,
    }


def record_warmup_send(sender_email: str, status: str) -> None:
    if not _enabled():
        return

    domain = _domain_from_email(sender_email)
    if not domain:
        return

    now = datetime.now(timezone.utc)
    _warmup_collection.update_one(
        {"domain": domain},
        {
            "$inc": {"sent_today": 1},
            "$set": {
                "last_status": status,
                "updated_at": now,
                "last_sent_at": now,
            },
            "$setOnInsert": {
                "first_seen_at": now,
                "window_start": now.replace(hour=0, minute=0, second=0, microsecond=0),
            },
        },
        upsert=True,
    )
