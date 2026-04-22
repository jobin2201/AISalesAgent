from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from app.data.database import db


events_collection = db["spam_events"]
suppression_collection = db["spam_suppression"]
metrics_collection = db["spam_metrics"]


def _as_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_email_domain(email: str) -> str:
    value = (email or "").strip().lower()
    if "@" not in value:
        return ""
    return value.split("@", 1)[1]


def process_sendgrid_events(events: List[Dict]) -> Dict[str, int]:
    inserted = 0
    suppressed = 0

    for event in events or []:
        email = (event.get("email") or "").strip().lower()
        event_type = (event.get("event") or "").strip().lower()
        timestamp = event.get("timestamp")
        domain = _extract_email_domain(email)

        payload = {
            "email": email,
            "domain": domain,
            "event_type": event_type,
            "reason": event.get("reason") or event.get("response") or "",
            "status": event.get("status") or "",
            "raw": event,
            "event_ts": timestamp,
            "created_at": _as_utc_now(),
        }
        events_collection.insert_one(payload)
        inserted += 1

        if event_type in {"bounce", "blocked", "spamreport", "dropped", "unsubscribe"} and email:
            suppression_collection.update_one(
                {"email": email},
                {
                    "$set": {
                        "email": email,
                        "domain": domain,
                        "last_event": event_type,
                        "updated_at": _as_utc_now(),
                    },
                    "$setOnInsert": {"created_at": _as_utc_now()},
                },
                upsert=True,
            )
            suppressed += 1

    _update_24h_metrics()
    return {"inserted": inserted, "suppressed": suppressed}


def _update_24h_metrics() -> None:
    now = _as_utc_now()
    cutoff = now - timedelta(hours=24)

    base = {"created_at": {"$gte": cutoff}}
    delivered = events_collection.count_documents({**base, "event_type": "delivered"})
    bounced = events_collection.count_documents({**base, "event_type": "bounce"})
    complained = events_collection.count_documents({**base, "event_type": "spamreport"})
    blocked = events_collection.count_documents({**base, "event_type": "blocked"})

    total = max(1, delivered + bounced + complained + blocked)

    metrics_collection.update_one(
        {"window": "24h"},
        {
            "$set": {
                "window": "24h",
                "delivered": delivered,
                "bounced": bounced,
                "complained": complained,
                "blocked": blocked,
                "bounce_rate_24h": round((bounced / total) * 100, 3),
                "complaint_rate_24h": round((complained / total) * 100, 3),
                "blocked_rate_24h": round((blocked / total) * 100, 3),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
