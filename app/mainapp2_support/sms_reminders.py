import base64
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from app.conversation_flow.config import sender_timezone
from app.mainapp2_support.notifications import create_notification_once


_notifications_collection = None
_sms_phone = ""
_sms_on_booking = True
_sms_followups_enabled = True


def configure(notifications_collection) -> None:
    global _notifications_collection
    _notifications_collection = notifications_collection


def _require_collection() -> None:
    if _notifications_collection is None:
        raise ValueError("SMS reminders collection is not configured.")


def set_preferences(phone: str, sms_on_booking: bool, sms_followups_enabled: bool) -> None:
    global _sms_phone, _sms_on_booking, _sms_followups_enabled

    normalized = normalize_phone(phone)
    if not normalized:
        normalized = normalize_phone(os.getenv("TWILIO_TO_NUMBER") or "")
    if normalized:
        _sms_phone = normalized
    _sms_on_booking = bool(sms_on_booking)
    _sms_followups_enabled = bool(sms_followups_enabled)


def get_preferences() -> dict[str, object]:
    return {
        "phone": _sms_phone,
        "sms_on_booking": _sms_on_booking,
        "sms_followups_enabled": _sms_followups_enabled,
    }


def normalize_phone(phone: str) -> str:
    raw = str(phone or "").strip()
    if not raw:
        return ""

    digits = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if digits.startswith("00"):
        digits = "+" + digits[2:]

    if digits.startswith("+"):
        return digits

    only_digits = "".join(ch for ch in digits if ch.isdigit())
    if len(only_digits) == 10:
        return "+91" + only_digits
    if only_digits:
        return "+" + only_digits
    return ""


def _twilio_credentials() -> tuple[str, str, str, str]:
    sid = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()
    from_phone = normalize_phone(os.getenv("TWILIO_FROM_NUMBER") or "")
    messaging_sid = (os.getenv("TWILIO_MESSAGING_SID") or "").strip()
    return sid, token, from_phone, messaging_sid


def _twilio_ready() -> bool:
    sid, token, from_phone, messaging_sid = _twilio_credentials()
    return bool(sid and token and (from_phone or messaging_sid))


def twilio_status() -> dict[str, object]:
    sid, token, from_phone, messaging_sid = _twilio_credentials()
    missing: list[str] = []
    if not sid:
        missing.append("TWILIO_ACCOUNT_SID")
    if not token:
        missing.append("TWILIO_AUTH_TOKEN")
    if not from_phone and not messaging_sid:
        missing.append("TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SID")
    return {
        "ready": not missing,
        "missing": missing,
        "from_phone": from_phone,
        "messaging_sid": messaging_sid,
    }


def _format_local(dt_utc: datetime) -> datetime:
    tz_name = str(sender_timezone())
    try:
        from zoneinfo import ZoneInfo

        return dt_utc.astimezone(ZoneInfo(tz_name))
    except Exception:
        return dt_utc.astimezone(timezone(timedelta(hours=5, minutes=30)))


def _meeting_text(lead_name: str, meeting_start_utc: datetime) -> str:
    local_dt = _format_local(meeting_start_utc)
    return f"{lead_name} on {local_dt.strftime('%A, %d %b %Y at %I:%M %p')}"


def _booking_sms_message(lead_name: str, meeting_start_utc: datetime, meeting_link: str) -> str:
    summary = _meeting_text(lead_name, meeting_start_utc)
    link_part = f" Invite: {meeting_link}" if meeting_link else ""
    return f"Meeting booked: {summary}.{link_part}".strip()


def _two_day_sms_message(lead_name: str, meeting_start_utc: datetime) -> str:
    summary = _meeting_text(lead_name, meeting_start_utc)
    return f"Reminder: You have two days for your call with Balaji. Scheduled meeting: {summary}. Best regards."


def _morning_sms_message(lead_name: str, meeting_start_utc: datetime) -> str:
    summary = _meeting_text(lead_name, meeting_start_utc)
    return f"Today reminder: Your call with Balaji is today. Meeting details: {summary}. Please be prepared."


def _thirty_min_sms_message(lead_name: str, meeting_start_utc: datetime) -> str:
    summary = _meeting_text(lead_name, meeting_start_utc)
    return f"Reminder: Your call with Balaji starts in 30 minutes. Meeting details: {summary}."


def _create_sms_queue_doc(*, send_at: datetime, to_phone: str, message: str, lead_name: str, lead_email: str, dedup_key: str, phase: str) -> None:
    _require_collection()
    if _notifications_collection.find_one({"dedup_key": dedup_key}):
        return
    _notifications_collection.insert_one(
        {
            "type": "sms_reminder",
            "lead_name": lead_name,
            "lead_email": lead_email,
            "message": f"SMS reminder queued ({phase}).",
            "sms_to": to_phone,
            "sms_body": message,
            "sms_phase": phase,
            "send_at": send_at,
            "status": "pending",
            "provider": "twilio",
            "created_at": datetime.now(timezone.utc),
            "read_at": None,
            "dedup_key": dedup_key,
        }
    )


def queue_booking_sms_reminders(
    *,
    lead_name: str,
    lead_email: str,
    meeting_start_iso: str,
    meeting_link: str,
    dedup_context: str,
) -> dict[str, int]:
    _require_collection()
    to_phone = normalize_phone(_sms_phone)

    if not to_phone or not _sms_on_booking:
        return {"queued": 0}

    try:
        start_dt = datetime.fromisoformat(str(meeting_start_iso))
    except Exception:
        return {"queued": 0}

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)
    queued = 0

    booking_msg = _booking_sms_message(lead_name, start_dt, meeting_link)
    booking_key = f"{dedup_context}|sms|booking"
    _create_sms_queue_doc(
        send_at=now_utc,
        to_phone=to_phone,
        message=booking_msg,
        lead_name=lead_name,
        lead_email=lead_email,
        dedup_key=booking_key,
        phase="booking",
    )
    queued += 1

    if _sms_followups_enabled:
        local_start = _format_local(start_dt)
        morning_local = local_start.replace(hour=8, minute=0, second=0, microsecond=0)
        two_day_local = (local_start - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
        thirty_min_local = local_start - timedelta(minutes=30)

        two_day_utc = two_day_local.astimezone(timezone.utc)
        morning_utc = morning_local.astimezone(timezone.utc)
        thirty_min_utc = thirty_min_local.astimezone(timezone.utc)

        if two_day_utc > now_utc:
            _create_sms_queue_doc(
                send_at=two_day_utc,
                to_phone=to_phone,
                message=_two_day_sms_message(lead_name, start_dt),
                lead_name=lead_name,
                lead_email=lead_email,
                dedup_key=f"{dedup_context}|sms|two_days",
                phase="two_days_before",
            )
            queued += 1

        if morning_utc > now_utc:
            _create_sms_queue_doc(
                send_at=morning_utc,
                to_phone=to_phone,
                message=_morning_sms_message(lead_name, start_dt),
                lead_name=lead_name,
                lead_email=lead_email,
                dedup_key=f"{dedup_context}|sms|morning",
                phase="morning_of_meeting",
            )
            queued += 1

        if thirty_min_utc > now_utc:
            _create_sms_queue_doc(
                send_at=thirty_min_utc,
                to_phone=to_phone,
                message=_thirty_min_sms_message(lead_name, start_dt),
                lead_name=lead_name,
                lead_email=lead_email,
                dedup_key=f"{dedup_context}|sms|thirty_mins",
                phase="thirty_minutes_before",
            )
            queued += 1

    create_notification_once(
        notification_type="sms_status",
        lead_name=lead_name,
        lead_email=lead_email,
        message=f"SMS reminders enabled and queued to {to_phone}.",
        dedup_key=f"{dedup_context}|sms|queue_notice",
        additional_data={
            "event_type": "sms_reminders_queued",
            "sms_to": to_phone,
            "queued_count": queued,
        },
    )
    return {"queued": queued}


def _send_via_twilio(to_phone: str, body: str) -> tuple[bool, str]:
    sid, token, from_phone, messaging_sid = _twilio_credentials()
    if not (sid and token and (from_phone or messaging_sid)):
        return False, "Twilio credentials are not configured."

    endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    form_payload = {
        "To": to_phone,
        "Body": body,
    }
    if messaging_sid:
        form_payload["MessagingServiceSid"] = messaging_sid
    else:
        form_payload["From"] = from_phone

    payload = urllib.parse.urlencode(form_payload).encode("utf-8")

    auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(endpoint, data=payload, method="POST")
    request.add_header("Authorization", f"Basic {auth}")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body_raw = response.read().decode("utf-8", errors="replace")
        return True, body_raw[:500]
    except Exception as exc:
        return False, str(exc)


def process_due_sms_reminders(limit: int = 20) -> dict[str, int]:
    _require_collection()
    if not _twilio_ready():
        pending_count = _notifications_collection.count_documents(
            {
                "type": "sms_reminder",
                "status": "pending",
            }
        )
        return {"sent": 0, "failed": 0, "pending": int(pending_count)}

    now_utc = datetime.now(timezone.utc)
    docs = list(
        _notifications_collection.find(
            {
                "type": "sms_reminder",
                "status": "pending",
                "send_at": {"$lte": now_utc},
            }
        )
        .sort("send_at", 1)
        .limit(max(1, int(limit)))
    )

    sent = 0
    failed = 0
    for doc in docs:
        to_phone = normalize_phone(str(doc.get("sms_to") or ""))
        body = str(doc.get("sms_body") or "").strip()
        if not to_phone or not body:
            _notifications_collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": "failed",
                        "error": "Missing phone or message body.",
                        "updated_at": now_utc,
                    }
                },
            )
            failed += 1
            continue

        ok, provider_response = _send_via_twilio(to_phone, body)
        if ok:
            _notifications_collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": "sent",
                        "sent_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                        "provider_response": provider_response,
                    }
                },
            )
            sent += 1
            create_notification_once(
                notification_type="sms_status",
                lead_name=str(doc.get("lead_name") or "Lead"),
                lead_email=str(doc.get("lead_email") or ""),
                message=f"SMS reminder sent to {to_phone}.",
                dedup_key=f"{str(doc.get('dedup_key') or '')}|sent_notice",
                additional_data={
                    "event_type": "sms_sent",
                    "sms_to": to_phone,
                    "sms_phase": str(doc.get("sms_phase") or ""),
                },
            )
        else:
            _notifications_collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": "failed",
                        "error": provider_response,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )
            failed += 1
            create_notification_once(
                notification_type="sms_status",
                lead_name=str(doc.get("lead_name") or "Lead"),
                lead_email=str(doc.get("lead_email") or ""),
                message=f"SMS reminder failed for {to_phone}: {provider_response}",
                dedup_key=f"{str(doc.get('dedup_key') or '')}|failed_notice",
                additional_data={
                    "event_type": "sms_failed",
                    "sms_to": to_phone,
                    "sms_phase": str(doc.get("sms_phase") or ""),
                    "error": provider_response,
                },
            )

    pending_count = _notifications_collection.count_documents(
        {
            "type": "sms_reminder",
            "status": "pending",
            "send_at": {"$gt": now_utc},
        }
    )
    return {"sent": sent, "failed": failed, "pending": int(pending_count)}


def send_test_sms_now(*, to_phone: str, message: str, lead_name: str = "Test Lead", lead_email: str = "") -> dict[str, object]:
    """Send an immediate test SMS and log status notification."""
    _require_collection()
    normalized_phone = normalize_phone(to_phone)
    if not normalized_phone:
        return {"ok": False, "error": "Invalid destination phone number."}

    ok, provider_response = _send_via_twilio(normalized_phone, (message or "").strip() or "Test SMS from AI Sales Agent")
    if ok:
        create_notification_once(
            notification_type="sms_status",
            lead_name=lead_name,
            lead_email=lead_email,
            message=f"Test SMS sent to {normalized_phone}.",
            dedup_key=f"test_sms|{normalized_phone}|{int(datetime.now(timezone.utc).timestamp())}",
            additional_data={
                "event_type": "sms_test_sent",
                "sms_to": normalized_phone,
            },
        )
        return {"ok": True, "response": provider_response}

    create_notification_once(
        notification_type="sms_status",
        lead_name=lead_name,
        lead_email=lead_email,
        message=f"Test SMS failed for {normalized_phone}: {provider_response}",
        dedup_key=f"test_sms_failed|{normalized_phone}|{int(datetime.now(timezone.utc).timestamp())}",
        additional_data={
            "event_type": "sms_test_failed",
            "sms_to": normalized_phone,
            "error": provider_response,
        },
    )
    return {"ok": False, "error": provider_response}
