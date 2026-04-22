import base64
import re
from email.mime.text import MIMEText
from datetime import datetime, timezone
from typing import Dict, List

from googleapiclient.discovery import build

from app.conversation_flow.calendar_tools import calendar_slots_text
from app.conversation_flow.config import (
    CUSTOMER_LOOP_SCOPES,
    SENDER_LOOP_SCOPES,
    customer_token_path,
    get_credentials,
    sender_token_path,
)


def gmail_service_for_token(token_path, required_scopes, auth_label: str):
    creds = get_credentials(token_path, interactive=False, required_scopes=required_scopes, auth_label=auth_label)
    return build("gmail", "v1", credentials=creds)


def sender_gmail_service():
    return gmail_service_for_token(sender_token_path(), SENDER_LOOP_SCOPES, "Sender")


def customer_gmail_service():
    return gmail_service_for_token(customer_token_path(), CUSTOMER_LOOP_SCOPES, "Customer")


def profile_email(service) -> str:
    profile = service.users().getProfile(userId="me").execute()
    return (profile.get("emailAddress") or "").strip().lower()


def decode_payload_body(payload: Dict) -> str:
    body_data = (payload.get("body") or {}).get("data")
    if body_data:
        return base64.urlsafe_b64decode(body_data.encode("utf-8")).decode("utf-8", errors="replace")

    for part in payload.get("parts", []) or []:
        mime_type = part.get("mimeType", "")
        if mime_type in {"text/plain", "text/html"}:
            part_data = (part.get("body") or {}).get("data")
            if part_data:
                return base64.urlsafe_b64decode(part_data.encode("utf-8")).decode("utf-8", errors="replace")
    return ""


def header_value(headers: List[Dict], key: str) -> str:
    key_lower = key.lower()
    for header in headers:
        if (header.get("name") or "").lower() == key_lower:
            return header.get("value", "")
    return ""


def extract_email(address_field: str) -> str:
    value = (address_field or "").strip().lower()
    if "<" in value and ">" in value:
        return value.split("<")[-1].split(">")[0].strip().lower()
    return value


def safe_reply_subject(subject: str) -> str:
    cleaned = (subject or "").strip()
    if not cleaned:
        return "Re: Quick follow-up"
    if cleaned.lower().startswith("re:"):
        return cleaned
    return f"Re: {cleaned}"


def list_messages_from(service, from_email: str, limit: int = 5, unread_only: bool = True) -> List[Dict]:
    unread = "is:unread " if unread_only else ""
    query = f"{unread}from:{from_email} newer_than:14d"
    listing = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
    out: List[Dict] = []

    for item in listing.get("messages", []):
        full = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
        payload = full.get("payload", {})
        headers = payload.get("headers", [])
        internal_ts = int(full.get("internalDate", "0") or 0)
        out.append(
            {
                "id": full.get("id", ""),
                "thread_id": full.get("threadId", ""),
                "from_email": extract_email(header_value(headers, "From")),
                "to_email": extract_email(header_value(headers, "To")),
                "subject": header_value(headers, "Subject") or "(no subject)",
                "body": decode_payload_body(payload).strip(),
                "internal_ts": internal_ts,
                "received_at": datetime.fromtimestamp(internal_ts / 1000, tz=timezone.utc).isoformat() if internal_ts else "",
            }
        )
    return out


def latest_message_from(service, from_email: str) -> Dict | None:
    items = list_messages_from(service, from_email=from_email, limit=1, unread_only=False)
    return items[0] if items else None


def mark_as_read(service, message_id: str) -> None:
    if not message_id:
        return
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def create_threaded_draft(service, to_email: str, subject: str, body: str, thread_id: str, from_email: str) -> Dict[str, str]:
    message = MIMEText(body)
    message["to"] = to_email
    message["subject"] = safe_reply_subject(subject)
    if from_email:
        message["from"] = from_email

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    message_body = {"raw": raw}
    if thread_id:
        message_body["threadId"] = thread_id

    try:
        created = service.users().drafts().create(userId="me", body={"message": message_body}).execute()
    except Exception:
        created = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()

    return {
        "draft_id": created.get("id", ""),
        "gmail_message_id": created.get("message", {}).get("id", ""),
    }


def contains_scheduling_intent(text: str) -> bool:
    lower = (text or "").lower()
    phrases = [
        "book a call",
        "book call",
        "book meeting",
        "schedule",
        "meeting",
        "calendar",
        "availability",
        "available slot",
        "available time",
        "time slot",
        "slot",
        "call",
    ]
    if any(phrase in lower for phrase in phrases):
        return True
    return bool(re.search(r"\\btime\\b", lower)) and any(token in lower for token in ["free", "available", "what", "which"])


