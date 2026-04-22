from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict

from app.data.database import db


events_collection = db["spam_events"]
metrics_collection = db["spam_metrics"]


def build_deliverability_report(hours: int = 24) -> Dict[str, object]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, hours))

    base = {"created_at": {"$gte": cutoff}}
    delivered = events_collection.count_documents({**base, "event_type": "delivered"})
    bounced = events_collection.count_documents({**base, "event_type": "bounce"})
    complained = events_collection.count_documents({**base, "event_type": "spamreport"})
    blocked = events_collection.count_documents({**base, "event_type": "blocked"})

    total = max(1, delivered + bounced + complained + blocked)
    return {
        "window_hours": hours,
        "generated_at": now.isoformat(),
        "delivered": delivered,
        "bounced": bounced,
        "complained": complained,
        "blocked": blocked,
        "bounce_rate": round((bounced / total) * 100, 3),
        "complaint_rate": round((complained / total) * 100, 3),
        "blocked_rate": round((blocked / total) * 100, 3),
        "inbox_placement_note": "Estimate only. Final inbox/spam placement is controlled by recipient mailbox providers.",
    }
