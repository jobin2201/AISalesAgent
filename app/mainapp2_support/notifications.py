"""
Notifications: track alerts for meetings, replies, escalations, and inactivity.
Stored in separate MongoDB collection without affecting existing workflow.
"""
from datetime import datetime, timezone
from typing import List, Dict, Optional

from bson import ObjectId


_notifications_collection = None


def configure(notifications_collection) -> None:
    """Configure the notifications collection reference."""
    global _notifications_collection
    _notifications_collection = notifications_collection


def _require_collection() -> None:
    if _notifications_collection is None:
        raise ValueError("Notifications collection is not configured.")


def create_notification(
    notification_type: str,
    lead_name: str,
    lead_email: str,
    message: str,
    additional_data: Optional[Dict] = None,
) -> str:
    """
    Create a new notification.
    
    Args:
        notification_type: "meeting_booked", "meeting_cancelled", "reply_received", "escalation", "inactivity"
        lead_name: company name or contact name
        lead_email: lead email address
        message: human-readable notification message
        additional_data: optional dict with extra context
    
    Returns:
        notification_id (MongoDB ObjectId as string)
    """
    _require_collection()
    
    doc = {
        "type": notification_type,
        "lead_name": lead_name,
        "lead_email": lead_email,
        "message": message,
        "status": "unread",
        "created_at": datetime.now(timezone.utc),
        "read_at": None,
    }
    
    if additional_data:
        doc.update(additional_data)
    
    result = _notifications_collection.insert_one(doc)
    return str(result.inserted_id)


def create_notification_once(
    notification_type: str,
    lead_name: str,
    lead_email: str,
    message: str,
    dedup_key: str,
    additional_data: Optional[Dict] = None,
) -> Optional[str]:
    """Create a notification only if a matching dedup_key does not already exist."""
    _require_collection()

    key = (dedup_key or "").strip()
    if not key:
        return create_notification(notification_type, lead_name, lead_email, message, additional_data)

    existing = _notifications_collection.find_one({"dedup_key": key})
    if existing:
        return None

    payload = dict(additional_data or {})
    payload["dedup_key"] = key
    return create_notification(notification_type, lead_name, lead_email, message, payload)


def get_unread_notifications(limit: int = 50) -> List[Dict]:
    """Get all unread notifications, sorted by latest first."""
    _require_collection()
    
    docs = _notifications_collection.find({"status": "unread"}).sort("created_at", -1).limit(limit)
    for doc in docs:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        if "created_at" in doc and isinstance(doc["created_at"], datetime):
            doc["created_at"] = doc["created_at"].isoformat()
        if "read_at" in doc and doc["read_at"] and isinstance(doc["read_at"], datetime):
            doc["read_at"] = doc["read_at"].isoformat()
    
    return list(docs)


def get_all_notifications(limit: int = 100) -> List[Dict]:
    """Get all notifications (read + unread), sorted by latest first."""
    _require_collection()
    
    docs = _notifications_collection.find({}).sort("created_at", -1).limit(limit)
    for doc in docs:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        if "created_at" in doc and isinstance(doc["created_at"], datetime):
            doc["created_at"] = doc["created_at"].isoformat()
        if "read_at" in doc and doc["read_at"] and isinstance(doc["read_at"], datetime):
            doc["read_at"] = doc["read_at"].isoformat()
    
    return list(docs)


def mark_as_read(notification_id: str) -> bool:
    """Mark a notification as read."""
    _require_collection()
    
    try:
        obj_id = ObjectId(notification_id)
    except Exception:
        return False
    
    result = _notifications_collection.update_one(
        {"_id": obj_id},
        {
            "$set": {
                "status": "read",
                "read_at": datetime.now(timezone.utc),
            }
        },
    )
    
    return result.modified_count > 0


def mark_all_as_read() -> int:
    """Mark all unread notifications as read."""
    _require_collection()
    
    result = _notifications_collection.update_many(
        {"status": "unread"},
        {
            "$set": {
                "status": "read",
                "read_at": datetime.now(timezone.utc),
            }
        },
    )
    
    return result.modified_count


def get_unread_count() -> int:
    """Get count of unread notifications."""
    _require_collection()
    return _notifications_collection.count_documents({"status": "unread"})


def delete_notification(notification_id: str) -> bool:
    """Delete a notification."""
    _require_collection()
    
    try:
        obj_id = ObjectId(notification_id)
    except Exception:
        return False
    
    result = _notifications_collection.delete_one({"_id": obj_id})
    return result.deleted_count > 0


def get_by_type(notification_type: str, limit: int = 50) -> List[Dict]:
    """Get notifications filtered by type."""
    _require_collection()
    
    docs = _notifications_collection.find({"type": notification_type}).sort("created_at", -1).limit(limit)
    for doc in docs:
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        if "created_at" in doc and isinstance(doc["created_at"], datetime):
            doc["created_at"] = doc["created_at"].isoformat()
        if "read_at" in doc and doc["read_at"] and isinstance(doc["read_at"], datetime):
            doc["read_at"] = doc["read_at"].isoformat()
    
    return list(docs)
