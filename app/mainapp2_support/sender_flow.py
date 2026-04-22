import re
from datetime import datetime, timedelta, timezone

from app.conversation_flow.calendar_tools import (
    available_calendar_slots,
    calendar_slots_text_for_days,
    cancel_calendar_invite,
    create_calendar_invite,
    update_calendar_invite,
)
from app.conversation_flow.config import sender_timezone
from app.conversation_flow.google_tools import (
    contains_scheduling_intent,
    create_threaded_draft,
    decode_payload_body,
    extract_email,
    header_value,
    mark_as_read,
    profile_email,
    safe_reply_subject,
    sender_gmail_service,
)
from app.conversation_flow.llm import extract_scheduling_response, generate_sender_reply_plan
from app.mainapp2_support.messaging import (
    customer_loop_address,
    ensure_sender_signature,
    log_message,
    missing_sender_booking_scopes,
    sender_loop_address,
)
from app.mainapp2_support.notifications import create_notification_once
from app.mainapp2_support.sms_reminders import queue_booking_sms_reminders


_new_leads_collection = None
_message_log_collection = None


def configure(new_leads_collection, message_log_collection) -> None:
    global _new_leads_collection, _message_log_collection
    _new_leads_collection = new_leads_collection
    _message_log_collection = message_log_collection


def _require_collections() -> None:
    if _new_leads_collection is None or _message_log_collection is None:
        raise ValueError("Sender flow collections are not configured.")


def _sender_mailbox_context() -> dict[str, object]:
    sender_addr = sender_loop_address()
    if not sender_addr:
        raise ValueError("Sender mailbox is not configured for the sender reply flow.")

    sender_service = sender_gmail_service()
    actual_sender = profile_email(sender_service)
    if actual_sender != sender_addr:
        raise ValueError(f"Sender token is authorized for {actual_sender}, expected {sender_addr}.")

    return {
        "sender_email": sender_addr,
        "sender_service": sender_service,
    }


def lead_participants(lead: dict, recipients: list[str]) -> list[str]:
    participants = list(recipients)
    customer_addr = customer_loop_address()
    if customer_addr and customer_addr not in participants:
        participants.append(customer_addr)
    return participants


def list_sender_conversations(participants: list[str]) -> list[dict[str, object]]:
    context = _sender_mailbox_context()
    sender_service = context["sender_service"]
    sender_addr = str(context["sender_email"])

    conversations: dict[str, dict[str, object]] = {}
    for participant in participants:
        participant_email = (participant or "").strip().lower()
        if not participant_email or participant_email == sender_addr:
            continue
        query = f"from:{participant_email} newer_than:30d"
        listing = sender_service.users().messages().list(userId="me", q=query, maxResults=12).execute()
        for item in listing.get("messages", []):
            full = sender_service.users().messages().get(userId="me", id=item["id"], format="full").execute()
            payload = full.get("payload", {})
            headers = payload.get("headers", [])
            from_email = extract_email(header_value(headers, "From"))
            if from_email != participant_email:
                continue
            internal_ts = int(full.get("internalDate", "0") or 0)
            record = {
                "message_id": full.get("id", ""),
                "thread_id": full.get("threadId", ""),
                "from_email": from_email,
                "to_email": extract_email(header_value(headers, "To")),
                "cc_email": extract_email(header_value(headers, "Cc")),
                "subject": header_value(headers, "Subject") or "(no subject)",
                "body": decode_payload_body(payload).strip(),
                "snippet": full.get("snippet", ""),
                "internal_ts": internal_ts,
                "received_at": datetime.fromtimestamp(internal_ts / 1000, tz=timezone.utc) if internal_ts else None,
            }
            thread_key = f"{from_email}|{record['thread_id'] or record['message_id']}"
            existing = conversations.get(thread_key)
            if not existing or int(existing.get("internal_ts", 0) or 0) < internal_ts:
                conversations[thread_key] = record

    return sorted(conversations.values(), key=lambda item: int(item.get("internal_ts", 0) or 0), reverse=True)


def latest_sender_log(company_name: str, participant_email: str) -> dict | None:
    _require_collections()
    return _message_log_collection.find_one(
        {
            "company_name": company_name,
            "to": participant_email,
            "from_email": sender_loop_address(),
        },
        sort=[("created_at", -1)],
    )


def draft_sender_mailbox_reply(
    product_description: str,
    company_name: str,
    participant_email: str,
    subject: str,
    body: str,
    thread_id: str,
) -> dict[str, str]:
    context = _sender_mailbox_context()
    sender_service = context["sender_service"]
    sender_addr = str(context["sender_email"])
    body_with_signature = ensure_sender_signature(body, product_description)

    created = create_threaded_draft(
        sender_service,
        participant_email,
        subject,
        body_with_signature,
        thread_id,
        sender_addr,
    )
    log_message(
        {
            "product_description": product_description,
            "company_name": company_name,
            "website": "",
            "to": participant_email,
            "cc": "",
            "from_email": sender_addr,
            "subject": safe_reply_subject(subject),
            "body": body_with_signature,
            "status": "draft",
            "delivery_mode": "sender_gmail_thread_draft",
            "external_draft_id": created.get("draft_id", ""),
            "thread_id": thread_id,
            "lead_email": participant_email,
            "error": "",
            "actor": "sender",
            "created_at": datetime.now(timezone.utc),
        }
    )
    return {
        "status": "draft",
        "delivery_mode": "sender_gmail_thread_draft",
        "external_draft_id": created.get("draft_id", ""),
    }


def _calendar_min_slot_minutes() -> int:
    import os

    try:
        value = int((os.getenv("CALENDAR_MIN_SLOT_MINUTES") or "30").strip())
    except Exception:
        value = 30
    return max(15, min(180, value))


def _store_new_lead_updates(lead_id, updates: dict[str, object]) -> None:
    _require_collections()
    if not updates:
        return
    _new_leads_collection.update_one(
        {"_id": lead_id},
        {
            "$set": {
                **updates,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


def _clear_pending_meeting_options(lead_id) -> None:
    _store_new_lead_updates(
        lead_id,
        {
            "meeting_offered_slots": [],
            "meeting_last_offer_text": "",
            "meeting_offer_window_days": None,
        },
    )


def _save_meeting_offer(lead_id, participant_email: str, slots: list[dict], offer_text: str, lookahead_days: int) -> None:
    _store_new_lead_updates(
        lead_id,
        {
            "meeting_state": "options_offered",
            "meeting_contact_email": participant_email,
            "meeting_offered_slots": slots,
            "meeting_last_offer_text": offer_text,
            "meeting_offer_window_days": lookahead_days,
            "meeting_offer_generated_at": datetime.now(timezone.utc),
        },
    )


def _safe_notification_once(
    *,
    notification_type: str,
    lead_name: str,
    lead_email: str,
    message: str,
    dedup_key: str,
    additional_data: dict[str, object] | None = None,
) -> None:
    try:
        create_notification_once(
            notification_type=notification_type,
            lead_name=lead_name,
            lead_email=lead_email,
            message=message,
            dedup_key=dedup_key,
            additional_data=additional_data or {},
        )
    except Exception:
        # Notifications are side-channel only and must never block core sender actions.
        pass


def _parse_requested_time(requested_time_text: str, base_dt: datetime) -> datetime | None:
    text = (requested_time_text or "").strip().lower()
    if not text:
        return None

    match_12h = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", text)
    if match_12h:
        hour = int(match_12h.group(1)) % 12
        minute = int(match_12h.group(2) or "0")
        if match_12h.group(3) == "pm":
            hour += 12
        return base_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)

    match_24h = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    if match_24h:
        hour = int(match_24h.group(1))
        minute = int(match_24h.group(2))
        return base_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if "noon" in text:
        return base_dt.replace(hour=12, minute=0, second=0, microsecond=0)
    if "morning" in text:
        return base_dt.replace(hour=9, minute=0, second=0, microsecond=0)
    if "afternoon" in text:
        return base_dt.replace(hour=14, minute=0, second=0, microsecond=0)
    if "evening" in text:
        return base_dt.replace(hour=17, minute=0, second=0, microsecond=0)

    return None


def _parse_requested_window(requested_time_text: str, base_dt: datetime) -> tuple[datetime, datetime] | None:
    text = (requested_time_text or "").strip().lower()
    if not text:
        return None

    window_match = re.search(
        r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\s*(?:-|to|–)\s*(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b",
        text,
    )
    if not window_match:
        return None

    start_hour = int(window_match.group(1)) % 12
    start_minute = int(window_match.group(2) or "0")
    if window_match.group(3) == "pm":
        start_hour += 12

    end_hour = int(window_match.group(4)) % 12
    end_minute = int(window_match.group(5) or "0")
    if window_match.group(6) == "pm":
        end_hour += 12

    start_dt = base_dt.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end_dt = base_dt.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def _requested_duration_minutes(text: str, default_minutes: int) -> int:
    raw = (text or "").lower()
    duration_match = re.search(r"\b(\d{1,3})\s*(?:minute|minutes|min|mins)\b", raw)
    if not duration_match:
        return default_minutes
    try:
        value = int(duration_match.group(1))
    except Exception:
        return default_minutes
    return max(15, min(480, value))


def _resolve_slot_selection(selection: dict[str, object], offered_slots: list[dict]) -> tuple[datetime, datetime] | None:
    slot_index = int(selection.get("selected_slot_index", -1) or -1)
    if slot_index < 0 or slot_index >= len(offered_slots):
        return None

    slot = offered_slots[slot_index]
    slot_start = datetime.fromisoformat(slot["start"])
    slot_end = datetime.fromisoformat(slot["end"])
    requested_text = str(selection.get("requested_time_text") or "")
    requested_window = _parse_requested_window(requested_text, slot_start)
    if requested_window is not None:
        window_start, window_end = requested_window
        if window_start < slot_start:
            window_start = slot_start
        if window_end > slot_end:
            window_end = slot_end
        if window_end > window_start:
            return window_start, window_end

    requested_start = _parse_requested_time(requested_text, slot_start)
    if requested_start is None:
        requested_start = slot_start

    requested_minutes = _requested_duration_minutes(requested_text, _calendar_min_slot_minutes())
    min_duration = timedelta(minutes=requested_minutes)
    if requested_start < slot_start:
        requested_start = slot_start
    if requested_start + min_duration > slot_end:
        requested_start = slot_start

    requested_end = requested_start + min_duration
    return requested_start, requested_end


def _match_slot_from_text(requested_text: str, candidate_slots: list[dict]) -> tuple[datetime, datetime] | None:
    text = (requested_text or "").strip().lower()
    if not text or not candidate_slots:
        return None

    date_match = re.search(r"\b(\d{1,2})\s+([a-z]{3,9})\b", text)
    window_match = re.search(
        r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\s*(?:-|to|–)\s*(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b",
        text,
    )
    month_aliases = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }

    for slot in candidate_slots:
        slot_start = datetime.fromisoformat(slot["start"])
        slot_end = datetime.fromisoformat(slot["end"])
        if date_match:
            wanted_day = int(date_match.group(1))
            wanted_month = month_aliases.get(date_match.group(2).lower())
            if wanted_month and (slot_start.day != wanted_day or slot_start.month != wanted_month):
                continue

        candidate_start = slot_start
        candidate_end = None
        if window_match:
            start_hour = int(window_match.group(1)) % 12
            start_minute = int(window_match.group(2) or "0")
            if window_match.group(3) == "pm":
                start_hour += 12
            candidate_start = slot_start.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
            end_hour = int(window_match.group(4)) % 12
            end_minute = int(window_match.group(5) or "0")
            if window_match.group(6) == "pm":
                end_hour += 12
            candidate_end = slot_start.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
            if candidate_end <= candidate_start:
                candidate_end += timedelta(days=1)

        requested_minutes = _requested_duration_minutes(text, _calendar_min_slot_minutes())
        min_duration = timedelta(minutes=requested_minutes)
        if candidate_start < slot_start:
            candidate_start = slot_start

        if candidate_end is not None:
            if candidate_end > slot_end:
                candidate_end = slot_end
            if candidate_end > candidate_start:
                return candidate_start, candidate_end

        if candidate_start + min_duration > slot_end:
            continue
        return candidate_start, candidate_start + min_duration

    return None


def _resolve_slot_from_live_availability(customer_text: str, lookahead_days: int = 14) -> tuple[datetime, datetime] | None:
    try:
        slot_payload = available_calendar_slots(lookahead_days=lookahead_days)
    except Exception:
        return None
    return _match_slot_from_text(customer_text, list(slot_payload.get("slots") or []))


def _resolve_customer_requested_slot(
    customer_text: str,
    scheduling: dict[str, object],
    offered_slots: list[dict],
    lookahead_days: int = 14,
) -> tuple[datetime, datetime] | None:
    resolved = _resolve_slot_selection(scheduling, offered_slots)
    if resolved is not None:
        return resolved

    requested_text = str(scheduling.get("requested_time_text") or customer_text or "")
    resolved = _match_slot_from_text(requested_text, offered_slots)
    if resolved is not None:
        return resolved

    return _resolve_slot_from_live_availability(requested_text, lookahead_days=lookahead_days)


def _has_explicit_slot_request(text: str) -> bool:
    lower = (text or "").lower()
    if "preferred slot" in lower or "preferred time" in lower:
        return True
    has_date = bool(re.search(r"\b\d{1,2}\s+[a-z]{3,9}\b", lower))
    has_time = bool(re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", lower)) or bool(
        re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", lower)
    )
    return has_date and has_time


def _fallback_scheduling_response(customer_text: str) -> dict[str, object]:
    lower = (customer_text or "").lower()
    if any(token in lower for token in ["unsubscribe", "not interested", "stop", "no thanks", "remove me"]):
        return {"intent": "not_interested", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": False, "asks_next_month": False}
    if any(token in lower for token in ["next month", "next quarter"]):
        return {"intent": "reschedule", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": False, "asks_next_month": True}
    if any(token in lower for token in ["next week", "later", "another time", "reschedule", "instead", "busy then", "not now"]):
        return {"intent": "reschedule", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": True, "asks_next_month": False}
    if _has_explicit_slot_request(customer_text):
        return {"intent": "accept", "selected_slot_index": -1, "requested_time_text": customer_text, "asks_next_week": False, "asks_next_month": False}
    if any(token in lower for token in ["works for me", "works", "sounds good", "let's do", "book it", "confirmed", "send the invite"]):
        return {"intent": "accept", "selected_slot_index": -1, "requested_time_text": customer_text, "asks_next_week": False, "asks_next_month": False}
    if contains_scheduling_intent(customer_text):
        return {"intent": "ask_availability", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": False, "asks_next_month": False}
    return {"intent": "unclear", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": False, "asks_next_month": False}


def _detect_scheduling_response(customer_text: str, offered_slots: list[dict], current_event: dict[str, str]) -> dict[str, object]:
    try:
        detected = extract_scheduling_response(customer_text, offered_slots, current_event)
    except Exception:
        return _fallback_scheduling_response(customer_text)
    if _has_explicit_slot_request(customer_text) and str(detected.get("intent") or "") in {"ask_availability", "unclear", "decline"}:
        detected["intent"] = "accept"
        detected["requested_time_text"] = str(detected.get("requested_time_text") or customer_text)
        detected["selected_slot_index"] = int(detected.get("selected_slot_index", -1) or -1)
    return detected


def _is_end_message(text: str) -> bool:
    lower = (text or "").strip().lower()
    return lower == "end" or bool(re.search(r"(^|\W)end($|\W)", lower))


def _meeting_confirmation_body(start_dt: datetime, invite: dict[str, str], rescheduled: bool) -> str:
    tz_name = str(sender_timezone())
    verb = "rescheduled" if rescheduled else "booked"
    link = invite.get("meeting_link") or invite.get("html_link") or ""
    link_line = f"Invite: {link}\n\n" if link else ""
    return (
        f"Hi,\n\nYour appointment is {verb}.\n\n"
        f"Time: {start_dt.strftime('%A, %d %b %Y at %I:%M %p')} {tz_name}\n"
        f"{link_line}"
        "The Google Calendar invite has been sent. Looking forward to meeting you.\n\n"
        "Best,\nAI Sales Team"
    )


def _sender_scheduling_plan(lead: dict, participant_email: str, customer_text: str) -> dict[str, object] | None:
    if not participant_email:
        return None

    if _is_end_message(customer_text):
        return {
            "kind": "generic",
            "action": "close_thread",
            "subject": "Re: Conversation closed",
            "body": "Hi,\n\nUnderstood. I will close the conversation here. If you want to revisit this later, feel free to reply anytime.\n\nBest,\nAI Sales Team",
        }

    current_event = {
        "event_id": lead.get("meeting_event_id", ""),
        "start": lead.get("meeting_start", ""),
        "end": lead.get("meeting_end", ""),
        "html_link": lead.get("meeting_event_link", ""),
    }
    offered_slots = lead.get("meeting_offered_slots") or []
    stored_contact = (lead.get("meeting_contact_email") or "").strip().lower()
    if stored_contact and stored_contact != participant_email:
        current_event = {"event_id": "", "start": "", "end": "", "html_link": ""}
        offered_slots = []

    lower = (customer_text or "").lower()
    scheduling_context_active = bool(offered_slots or current_event.get("event_id"))
    scheduling_keywords = contains_scheduling_intent(customer_text) or any(
        token in lower
        for token in [
            "works for me",
            "send the invite",
            "next week",
            "next month",
            "reschedule",
            "another time",
            "later",
            "calendar",
            "availability",
            "friday",
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
        ]
    )
    if not scheduling_context_active and not scheduling_keywords:
        return None

    scheduling = _detect_scheduling_response(customer_text, offered_slots, current_event)
    intent = str(scheduling.get("intent") or "unclear")

    if intent == "not_interested":
        return {
            "kind": "scheduling",
            "action": "cancel",
            "subject": "Re: Thanks for the update",
            "body": "Hi,\n\nUnderstood. I have cancelled the pending meeting flow and will pause outreach.\n\nBest,\nAI Sales Team",
            "current_event": current_event,
        }

    if intent == "reschedule" and (scheduling.get("asks_next_week") or scheduling.get("asks_next_month")):
        lookahead_days = 14 if scheduling.get("asks_next_week") else 30
        slot_payload = available_calendar_slots(lookahead_days=lookahead_days)
        offer_text = calendar_slots_text_for_days(lookahead_days=lookahead_days)
        return {
            "kind": "scheduling",
            "action": "offer_slots",
            "subject": "Re: Meeting options",
            "body": (
                "Hi,\n\nNo problem. Here are some updated options.\n\n"
                "I checked my Google Calendar and here is my availability:\n"
                f"{offer_text}\n\n"
                "Reply with the date/time that works best. If none of these work, tell me whether you prefer next week or next month and I will send updated options.\n\n"
                "Best,\nAI Sales Team"
            ),
            "slots": slot_payload["slots"],
            "offer_text": offer_text,
            "lookahead_days": lookahead_days,
            "cancel_existing": bool(current_event.get("event_id")),
            "meeting_contact_email": participant_email,
        }

    if intent in {"accept", "reschedule"}:
        resolved = _resolve_customer_requested_slot(
            customer_text=customer_text,
            scheduling=scheduling,
            offered_slots=offered_slots,
            lookahead_days=int(lead.get("meeting_offer_window_days") or 14),
        )
        if resolved is None and current_event.get("event_id") and intent == "accept":
            start_dt = datetime.fromisoformat(str(current_event.get("start") or ""))
            return {
                "kind": "scheduling",
                "action": "confirm_existing",
                "subject": "Re: Meeting confirmed",
                "body": _meeting_confirmation_body(start_dt, current_event, rescheduled=False),
                "current_event": current_event,
            }
        if resolved is None and offered_slots:
            return {
                "kind": "scheduling",
                "action": "clarify_slot",
                "subject": "Re: Quick scheduling clarification",
                "body": (
                    "Hi,\n\nI want to make sure I book the right slot. I could not confidently map your preferred time to a currently free window. Please reply with the exact preferred day and start time, and I will book it if it is still open.\n\n"
                    "Best,\nAI Sales Team"
                ),
            }
        if resolved is None:
            return {
                "kind": "scheduling",
                "action": "request_precise_slot",
                "subject": "Re: Quick scheduling clarification",
                "body": (
                    "Hi,\n\nI checked the current calendar, but I could not map your reply to a specific free slot yet. Please send the exact preferred day and start time, and I will book it if it is available.\n\n"
                    "Best,\nAI Sales Team"
                ),
            }

        start_dt, end_dt = resolved
        return {
            "kind": "scheduling",
            "action": "book_slot",
            "subject": "Re: Meeting confirmed",
            "body": _meeting_confirmation_body(start_dt, current_event, rescheduled=bool(current_event.get("event_id"))),
            "start_dt": start_dt,
            "end_dt": end_dt,
            "current_event": current_event,
            "meeting_contact_email": participant_email,
        }

    if intent in {"decline", "ask_availability"}:
        lookahead_days = 14 if scheduling.get("asks_next_week") else 30 if scheduling.get("asks_next_month") else 7
        intro = "No problem. Here are some updated options." if intent == "decline" else "Happy to share availability."
        slot_payload = available_calendar_slots(lookahead_days=lookahead_days)
        offer_text = calendar_slots_text_for_days(lookahead_days=lookahead_days)
        return {
            "kind": "scheduling",
            "action": "offer_slots",
            "subject": "Re: Meeting options",
            "body": (
                f"Hi,\n\n{intro}\n\n"
                "I checked my Google Calendar and here is my availability:\n"
                f"{offer_text}\n\n"
                "Reply with the date/time that works best. If none of these work, tell me whether you prefer next week or next month and I will send updated options.\n\n"
                "Best,\nAI Sales Team"
            ),
            "slots": slot_payload["slots"],
            "offer_text": offer_text,
            "lookahead_days": lookahead_days,
            "cancel_existing": False,
            "meeting_contact_email": participant_email,
        }

    return None


def _generic_sender_reply_plan(lead: dict, participant_email: str, customer_text: str) -> dict[str, object]:
    def _is_clarity_request(text: str) -> bool:
        lower = (text or "").lower()
        tokens = [
            "more detail",
            "more details",
            "need clarity",
            "share detail",
            "implementation timeline",
            "timeline",
            "effort from our side",
            "implementation effort",
            "how does this work",
            "can you explain",
            "clarify",
        ]
        return any(token in lower for token in tokens)

    def _clarity_reply_template(product_description: str) -> tuple[str, str]:
        product_lower = (product_description or "").strip().lower()
        if "digiexpense" in product_lower:
            focus_line = (
                "DigiExpense helps teams automate expense capture, policy checks, approvals, and reimbursement workflows "
                "with clear finance visibility."
            )
        elif "digicom" in product_lower:
            focus_line = (
                "DigiCom helps teams unify customer communication, run consistent outreach journeys, and improve response speed "
                "with better engagement tracking."
            )
        else:
            focus_line = (
                f"{(product_description or 'Our solution')} helps reduce manual effort, improve process clarity, and accelerate outcomes."
            )

        body = (
            "Hi,\n\n"
            "Thank you for the thoughtful note. Here is a quick clarification first.\n\n"
            f"{focus_line}\n\n"
            "For implementation, we can walk you through expected timeline, ownership from your team, and the exact rollout steps in a short Google Meet session. "
            "In that call, we will also show how Digi Delight Solution Private Ltd supports customers through onboarding and adoption.\n\n"
            "Please share a suitable time and I will send a Google Meet invite.\n\n"
            "Best,\nAI Sales Team"
        )
        return "Re: Implementation clarity and next steps", body

    lead_payload = {
        "name": lead.get("contact_name") or lead.get("company_name") or "there",
        "company": lead.get("company_name") or "the team",
        "lead_score": lead.get("total_score", 0),
        "industry": lead.get("industry") or "business",
    }
    calendar_text = calendar_slots_text_for_days()
    latest_outbound = latest_sender_log(lead.get("company_name", ""), participant_email)
    conversation_context = customer_text
    if latest_outbound and latest_outbound.get("body"):
        conversation_context = (
            "Previous sender draft:\n"
            f"{latest_outbound.get('body', '')}\n\n"
            "Latest customer reply:\n"
            f"{customer_text}"
        )

    if _is_clarity_request(customer_text):
        clarity_subject, clarity_body = _clarity_reply_template(lead.get("product_description", ""))
        return {
            "kind": "generic",
            "action": "nlp_reply",
            "subject": clarity_subject,
            "body": clarity_body,
            "intent": "need_clarity",
        }

    try:
        plan = generate_sender_reply_plan(lead_payload, conversation_context, calendar_text)
        if str(plan.get("intent") or "").strip().lower() in {"need_clarity", "clarification", "implementation_query"}:
            clarity_subject, clarity_body = _clarity_reply_template(lead.get("product_description", ""))
            return {
                "kind": "generic",
                "action": "nlp_reply",
                "subject": clarity_subject,
                "body": clarity_body,
                "intent": "need_clarity",
            }
        return {
            "kind": "generic",
            "action": "nlp_reply",
            "subject": plan.get("subject") or "Re: Quick follow-up",
            "body": plan.get("body") or "Hi,\n\nThanks for the note.\n\nBest,\nAI Sales Team",
            "intent": plan.get("intent") or "unknown",
        }
    except Exception:
        if _is_clarity_request(customer_text):
            clarity_subject, clarity_body = _clarity_reply_template(lead.get("product_description", ""))
            return {
                "kind": "generic",
                "action": "nlp_reply",
                "subject": clarity_subject,
                "body": clarity_body,
                "intent": "need_clarity",
            }
        return {
            "kind": "generic",
            "action": "nlp_reply",
            "subject": "Re: Quick follow-up",
            "body": "Hi,\n\nThanks for the update. Could you share a bit more detail so I can tailor the next step correctly?\n\nBest,\nAI Sales Team",
            "intent": "fallback",
        }


def sender_reply_plan(lead: dict, participant_email: str, customer_text: str) -> dict[str, object]:
    scheduling_plan = _sender_scheduling_plan(lead, participant_email, customer_text)
    if scheduling_plan is not None:
        return scheduling_plan
    return _generic_sender_reply_plan(lead, participant_email, customer_text)


def apply_sender_plan(lead: dict, participant_email: str, thread_id: str, plan: dict[str, object]) -> tuple[str, str]:
    lead_id = lead["_id"]
    action = str(plan.get("action") or "")
    subject = str(plan.get("subject") or "Re: Quick follow-up")
    body = str(plan.get("body") or "")
    source_message_id = str(plan.get("source_message_id") or "")
    lead_name = str(lead.get("company_name") or lead.get("contact_name") or participant_email)

    if plan.get("kind") == "scheduling":
        if action in {"cancel", "offer_slots", "book_slot"}:
            if missing_sender_booking_scopes():
                raise ValueError(
                    "Sender calendar access is missing required scopes. Click 'Authorize Sender Inbox + Calendar' in the sidebar, complete the Google consent flow again, and retry the draft."
                )
        current_event = plan.get("current_event") or {}
        lead_contact = {
            "email": participant_email,
            "name": lead.get("contact_name") or lead.get("company_name") or participant_email,
            "company": lead.get("company_name") or "Prospect",
        }

        if action == "cancel":
            if current_event.get("event_id"):
                cancel_calendar_invite(str(current_event.get("event_id") or ""))
            _store_new_lead_updates(
                lead_id,
                {
                    "status": "disqualified",
                    "meeting_state": "cancelled",
                    "meeting_event_id": "",
                    "meeting_event_link": "",
                    "meeting_start": "",
                    "meeting_end": "",
                    "meeting_contact_email": participant_email,
                },
            )
            _clear_pending_meeting_options(lead_id)
            _safe_notification_once(
                notification_type="meeting_cancelled",
                lead_name=lead_name,
                lead_email=participant_email,
                message="Meeting cancelled and lead moved to disqualified.",
                dedup_key=f"{participant_email}|{thread_id}|meeting_cancelled|{source_message_id}",
                additional_data={
                    "event_type": "meeting_cancelled",
                    "thread_id": thread_id,
                    "source_message_id": source_message_id,
                },
            )
            _safe_notification_once(
                notification_type="escalation",
                lead_name=lead_name,
                lead_email=participant_email,
                message="Lead marked disqualified after meeting cancellation/decline.",
                dedup_key=f"{participant_email}|{thread_id}|disqualified|{source_message_id}",
                additional_data={
                    "event_type": "disqualified",
                    "thread_id": thread_id,
                    "source_message_id": source_message_id,
                },
            )
        elif action == "offer_slots":
            if plan.get("cancel_existing") and current_event.get("event_id"):
                cancel_calendar_invite(str(current_event.get("event_id") or ""))
                _store_new_lead_updates(
                    lead_id,
                    {
                        "meeting_event_id": "",
                        "meeting_event_link": "",
                        "meeting_start": "",
                        "meeting_end": "",
                        "meeting_state": "reschedule_requested",
                    },
                )
            _save_meeting_offer(
                lead_id,
                participant_email,
                list(plan.get("slots") or []),
                str(plan.get("offer_text") or ""),
                int(plan.get("lookahead_days") or 7),
            )
            if plan.get("cancel_existing"):
                _safe_notification_once(
                    notification_type="meeting_rescheduled",
                    lead_name=lead_name,
                    lead_email=participant_email,
                    message="Meeting moved to reschedule flow; new slots offered.",
                    dedup_key=f"{participant_email}|{thread_id}|meeting_rescheduled|{source_message_id}",
                    additional_data={
                        "event_type": "meeting_rescheduled",
                        "thread_id": thread_id,
                        "source_message_id": source_message_id,
                    },
                )
        elif action == "book_slot":
            start_dt = plan.get("start_dt")
            end_dt = plan.get("end_dt")
            if not isinstance(start_dt, datetime) or not isinstance(end_dt, datetime):
                raise ValueError("Unable to resolve the requested meeting slot.")
            try:
                if current_event.get("event_id"):
                    invite = update_calendar_invite(str(current_event.get("event_id") or ""), lead_contact, start_dt, end_dt, thread_id)
                    rescheduled = True
                else:
                    invite = create_calendar_invite(lead_contact, start_dt, end_dt, thread_id)
                    rescheduled = False
            except Exception as exc:
                if "insufficient" in str(exc).lower() or "scope" in str(exc).lower() or "permission" in str(exc).lower():
                    raise ValueError(
                        "Google Calendar blocked the booking because the sender token is not authorized with calendar write permission. Click 'Authorize Sender Inbox + Calendar' in the sidebar, approve access again, and then retry."
                    ) from exc
                raise
            _store_new_lead_updates(
                lead_id,
                {
                    "status": "meeting_booked",
                    "meeting_state": "booked",
                    "meeting_event_id": invite.get("event_id", ""),
                    "meeting_event_link": invite.get("meeting_link") or invite.get("html_link") or "",
                    "meeting_start": invite.get("start", start_dt.isoformat()),
                    "meeting_end": invite.get("end", end_dt.isoformat()),
                    "meeting_contact_email": participant_email,
                },
            )
            _clear_pending_meeting_options(lead_id)
            body = _meeting_confirmation_body(start_dt, invite, rescheduled=rescheduled)
            _safe_notification_once(
                notification_type="meeting_rescheduled" if rescheduled else "meeting_booked",
                lead_name=lead_name,
                lead_email=participant_email,
                message="Meeting rescheduled successfully." if rescheduled else "Meeting booked successfully.",
                dedup_key=(
                    f"{participant_email}|{thread_id}|meeting_rescheduled|{source_message_id}"
                    if rescheduled
                    else f"{participant_email}|{thread_id}|meeting_booked|{source_message_id}"
                ),
                additional_data={
                    "event_type": "meeting_rescheduled" if rescheduled else "meeting_booked",
                    "thread_id": thread_id,
                    "source_message_id": source_message_id,
                    "meeting_start": invite.get("start", start_dt.isoformat()),
                    "meeting_end": invite.get("end", end_dt.isoformat()),
                },
            )
            try:
                queue_booking_sms_reminders(
                    lead_name=lead_name,
                    lead_email=participant_email,
                    meeting_start_iso=invite.get("start", start_dt.isoformat()),
                    meeting_link=invite.get("meeting_link") or invite.get("html_link") or "",
                    dedup_context=f"{participant_email}|{thread_id}|{invite.get('event_id', '')}|{source_message_id}",
                )
            except Exception:
                # SMS reminder is a side-channel and must not block sender draft creation.
                pass
        elif action in {"clarify_slot", "request_precise_slot"}:
            _safe_notification_once(
                notification_type="escalation",
                lead_name=lead_name,
                lead_email=participant_email,
                message="Scheduling ambiguity detected. Manual attention may be needed.",
                dedup_key=f"{participant_email}|{thread_id}|escalation|{source_message_id}",
                additional_data={
                    "event_type": "escalation",
                    "thread_id": thread_id,
                    "source_message_id": source_message_id,
                },
            )

    draft_sender_mailbox_reply(
        product_description=lead.get("product_description", ""),
        company_name=lead.get("company_name", ""),
        participant_email=participant_email,
        subject=subject,
        body=body,
        thread_id=thread_id,
    )

    if source_message_id:
        try:
            sender_service = _sender_mailbox_context()["sender_service"]
            mark_as_read(sender_service, source_message_id)
        except Exception:
            # Read-state sync should never block reply draft generation.
            pass

    return subject, body