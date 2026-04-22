"""
Conversation memory: tracks all outbound/inbound messages per lead.
This is part of Layer 2 (Memory).
"""
from datetime import datetime, timezone
from typing import Dict, List

from app.data.database import messages_collection


def log_outbound(lead_email: str, subject: str, body: str, email_type: str = "first_touch") -> None:
    """Log an outbound email we send to prospect."""
    messages_collection.insert_one(
        {
            "lead_email": lead_email,
            "direction": "outbound",
            "type": email_type,
            "subject": subject,
            "body": body,
            "created_at": datetime.now(timezone.utc),
        }
    )


def log_inbound(lead_email: str, from_email: str, subject: str, body: str) -> None:
    """Log a reply we received from prospect."""
    messages_collection.insert_one(
        {
            "lead_email": lead_email,
            "direction": "inbound",
            "from_email": from_email,
            "subject": subject,
            "body": body,
            "created_at": datetime.now(timezone.utc),
        }
    )


def get_conversation_history(lead_email: str) -> List[Dict]:
    """Get all messages (sent + received) for this lead, in order."""
    return list(messages_collection.find({"lead_email": lead_email}).sort("created_at", 1))


def get_last_reply(lead_email: str) -> Dict | None:
    """Get the most recent reply from prospect."""
    return messages_collection.find_one(
        {"lead_email": lead_email, "direction": "inbound"},
        sort=[("created_at", -1)],
    )
