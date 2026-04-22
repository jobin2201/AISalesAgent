import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict

from googleapiclient.discovery import build

from app.data.database import leads_collection, messages_collection
from app.services.ai_service import generate_email
from app.services.reply_analyzer import decide_agent_next_action
from app.conversation_flow.config import (
    CUSTOMER_LOOP_SCOPES,
    SENDER_LOOP_SCOPES,
    customer_email,
    customer_token_path,
    get_credentials,
    is_exit_message,
    sender_email,
    sender_timezone,
    sender_token_path,
    token_scopes,
)
from app.conversation_flow.calendar_tools import (
    available_calendar_slots,
    cancel_calendar_invite,
    calendar_slots_text_for_days,
    create_calendar_invite,
    update_calendar_invite,
)
from app.conversation_flow.google_tools import (
    calendar_slots_text,
    contains_scheduling_intent,
    create_threaded_draft,
    customer_gmail_service,
    latest_message_from,
    list_messages_from,
    mark_as_read,
    profile_email,
    safe_reply_subject,
    sender_gmail_service,
)
from app.conversation_flow.llm import (
    extract_scheduling_response,
    generate_customer_reply_plan,
    generate_sender_reply_plan,
)
from app.mainapp2_support.notifications import create_notification_once


def _lead_display_name(lead_email: str) -> str:
    lead = leads_collection.find_one({"email": lead_email}) or {}
    return (
        str(
            lead.get("company_name")
            or lead.get("company")
            or lead.get("contact_name")
            or lead.get("name")
            or lead_email
        )
        .strip()
        or lead_email
    )


def _safe_notification_once(
    *,
    notification_type: str,
    lead_email: str,
    message: str,
    dedup_key: str,
    additional_data: Dict[str, object],
) -> None:
    try:
        create_notification_once(
            notification_type=notification_type,
            lead_name=_lead_display_name(lead_email),
            lead_email=lead_email,
            message=message,
            dedup_key=dedup_key,
            additional_data=additional_data,
        )
    except Exception:
        # Notifications are side-channel only and must never block core workflow.
        pass


def authorize_sender_loop_account() -> Dict[str, str]:
    token_path = sender_token_path()
    creds = get_credentials(token_path, interactive=True, required_scopes=SENDER_LOOP_SCOPES, auth_label="Sender")
    service = build("gmail", "v1", credentials=creds)
    actual_sender = profile_email(service)
    expected_sender = sender_email()
    if expected_sender and actual_sender != expected_sender:
        try:
            token_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise ValueError(
            f"Authorized sender account is {actual_sender}, expected {expected_sender}. Please re-authorize and choose the correct sender Google account."
        )
    return {
        "status": "ok",
        "account_type": "sender",
        "token_path": str(token_path),
        "authorized_email": actual_sender,
        "scopes": ", ".join(sorted(token_scopes(token_path))),
    }


def authorize_customer_loop_account() -> Dict[str, str]:
    token_path = customer_token_path()
    creds = get_credentials(token_path, interactive=True, required_scopes=CUSTOMER_LOOP_SCOPES, auth_label="Customer")
    service = build("gmail", "v1", credentials=creds)
    actual_customer = profile_email(service)
    expected_customer = customer_email()
    if expected_customer and actual_customer != expected_customer:
        try:
            token_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise ValueError(
            f"Authorized customer account is {actual_customer}, expected {expected_customer}. Please re-authorize and choose the customer Google account."
        )
    return {
        "status": "ok",
        "account_type": "customer",
        "token_path": str(token_path),
        "authorized_email": actual_customer,
        "scopes": ", ".join(sorted(token_scopes(token_path))),
    }


def _upsert_inbound_log(
    lead_email: str,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    thread_id: str,
    external_message_id: str = "",
) -> bool:
    if external_message_id:
        existing_by_external = messages_collection.find_one(
            {"direction": "inbound", "external_message_id": external_message_id}
        )
        if existing_by_external:
            return False

    existing_by_content = messages_collection.find_one(
        {
            "direction": "inbound",
            "lead_email": lead_email,
            "thread_id": thread_id,
            "subject": subject,
            "body": body,
        }
    )
    if existing_by_content:
        return False

    messages_collection.insert_one(
        {
            "lead_email": lead_email,
            "from_email": from_email,
            "to": to_email,
            "direction": "inbound",
            "type": "gmail_auto_inbound",
            "subject": subject,
            "body": body,
            "channel": "email",
            "status": "received",
            "delivery_mode": "gmail_inbox",
            "thread_id": thread_id,
            "external_message_id": external_message_id,
            "created_at": datetime.now(timezone.utc),
        }
    )
    return True


def _is_inbound_already_processed(external_message_id: str) -> bool:
    if not external_message_id:
        return False
    return bool(messages_collection.find_one({"direction": "inbound", "external_message_id": external_message_id}))


def _insert_outbound_draft_log(
    lead_email: str,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    thread_id: str,
    draft_id: str,
    email_type: str,
) -> None:
    messages_collection.insert_one(
        {
            "lead_email": lead_email,
            "from_email": from_email,
            "to": to_email,
            "direction": "outbound",
            "type": email_type,
            "subject": subject,
            "body": body,
            "channel": "email",
            "status": "draft",
            "delivery_mode": "gmail_draft_auto",
            "review_required": True,
            "thread_id": thread_id,
            "external_draft_id": draft_id,
            "created_at": datetime.now(timezone.utc),
        }
    )


def _calendar_min_slot_minutes() -> int:
    try:
        value = int((os.getenv("CALENDAR_MIN_SLOT_MINUTES") or "30").strip())
    except Exception:
        value = 30
    return max(15, min(180, value))


def _store_meeting_updates(lead_email: str, updates: Dict[str, object]) -> None:
    if not lead_email or not updates:
        return
    leads_collection.update_one(
        {"email": lead_email},
        {
            "$set": {
                **updates,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


def _clear_pending_meeting_options(lead_email: str) -> None:
    _store_meeting_updates(
        lead_email,
        {
            "meeting_offered_slots": [],
            "meeting_last_offer_text": "",
            "meeting_offer_window_days": None,
        },
    )


def _save_meeting_offer(lead_email: str, slots: list[dict], offer_text: str, lookahead_days: int) -> None:
    _store_meeting_updates(
        lead_email,
        {
            "meeting_state": "options_offered",
            "meeting_offered_slots": slots,
            "meeting_last_offer_text": offer_text,
            "meeting_offer_window_days": lookahead_days,
            "meeting_offer_generated_at": datetime.now(timezone.utc),
        },
    )


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


def _resolve_slot_selection(selection: Dict[str, object], offered_slots: list[dict]) -> tuple[datetime, datetime] | None:
    slot_index = int(selection.get("selected_slot_index", -1) or -1)
    if slot_index < 0 or slot_index >= len(offered_slots):
        return None

    slot = offered_slots[slot_index]
    slot_start = datetime.fromisoformat(slot["start"])
    slot_end = datetime.fromisoformat(slot["end"])
    requested_start = _parse_requested_time(str(selection.get("requested_time_text") or ""), slot_start)
    if requested_start is None:
        requested_start = slot_start

    min_duration = timedelta(minutes=_calendar_min_slot_minutes())
    if requested_start < slot_start:
        requested_start = slot_start
    if requested_start + min_duration > slot_end:
        requested_start = slot_start

    requested_end = requested_start + min_duration
    return requested_start, requested_end


def _fallback_scheduling_response(customer_text: str) -> Dict[str, object]:
    lower = (customer_text or "").lower()
    if any(token in lower for token in ["unsubscribe", "not interested", "stop", "no thanks", "remove me"]):
        return {"intent": "not_interested", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": False, "asks_next_month": False}
    if any(token in lower for token in ["next month", "next quarter"]):
        return {"intent": "reschedule", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": False, "asks_next_month": True}
    if any(token in lower for token in ["next week", "later", "another time", "reschedule", "instead", "busy then", "not now"]):
        return {"intent": "reschedule", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": True, "asks_next_month": False}
    if any(token in lower for token in ["works for me", "works", "sounds good", "let's do", "book it", "confirmed", "send the invite"]):
        return {"intent": "accept", "selected_slot_index": -1, "requested_time_text": customer_text, "asks_next_week": False, "asks_next_month": False}
    if contains_scheduling_intent(customer_text):
        return {"intent": "ask_availability", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": False, "asks_next_month": False}
    return {"intent": "unclear", "selected_slot_index": -1, "requested_time_text": "", "asks_next_week": False, "asks_next_month": False}


def _detect_scheduling_response(customer_text: str, offered_slots: list[dict], current_event: Dict | None) -> Dict[str, object]:
    try:
        return extract_scheduling_response(customer_text, offered_slots, current_event)
    except Exception:
        return _fallback_scheduling_response(customer_text)


def _meeting_confirmation_body(start_dt: datetime, invite: Dict[str, str], rescheduled: bool) -> str:
    tz_name = str(sender_timezone())
    verb = "rescheduled" if rescheduled else "booked"
    link = invite.get("meeting_link") or invite.get("html_link") or ""
    link_line = f"Invite: {link}\n\n" if link else ""
    return (
        f"Hi,\n\nYour meeting is {verb}.\n\n"
        f"Time: {start_dt.strftime('%A, %d %b %Y at %I:%M %p')} {tz_name}\n"
        f"{link_line}"
        "A Google Calendar invite has been sent to you.\n\n"
        "Best,\nAI Sales Team"
    )


def _meeting_offer_payload(customer_addr: str, intro_text: str, lookahead_days: int) -> Dict[str, str]:
    slot_payload = available_calendar_slots(lookahead_days=lookahead_days)
    offer_text = calendar_slots_text_for_days(lookahead_days=lookahead_days)
    _save_meeting_offer(customer_addr, slot_payload["slots"], offer_text, lookahead_days)
    return {
        "subject": "Re: Meeting options",
        "body": (
            f"Hi,\n\n{intro_text}\n\n"
            "I checked my Google Calendar and here is my availability:\n"
            f"{offer_text}\n\n"
            "Reply with the date/time that works best. If none of these work, tell me whether you prefer next week or next month and I will send updated options.\n\n"
            "Best,\nAI Sales Team"
        ),
    }


def _handle_scheduling_workflow(lead: Dict, customer_text: str) -> Dict[str, str] | None:
    lead_email = (lead.get("email") or "").strip().lower()
    if not lead_email:
        return None

    current_event = {
        "event_id": lead.get("meeting_event_id", ""),
        "start": lead.get("meeting_start", ""),
        "end": lead.get("meeting_end", ""),
        "html_link": lead.get("meeting_event_link", ""),
    }
    offered_slots = lead.get("meeting_offered_slots") or []
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
        if current_event.get("event_id"):
            cancel_calendar_invite(str(current_event["event_id"]))
        _store_meeting_updates(
            lead_email,
            {
                "status": "disqualified",
                "meeting_state": "cancelled",
                "meeting_event_id": "",
                "meeting_event_link": "",
                "meeting_start": "",
                "meeting_end": "",
            },
        )
        _clear_pending_meeting_options(lead_email)
        return {
            "subject": "Re: Thanks for the update",
            "body": "Hi,\n\nUnderstood. I have cancelled the pending meeting flow and will pause outreach.\n\nBest,\nAI Sales Team",
        }

    if intent == "reschedule" and (scheduling.get("asks_next_week") or scheduling.get("asks_next_month")):
        lookahead = 14 if scheduling.get("asks_next_week") else 30
        if current_event.get("event_id"):
            cancel_calendar_invite(str(current_event["event_id"]))
            _store_meeting_updates(
                lead_email,
                {
                    "meeting_event_id": "",
                    "meeting_event_link": "",
                    "meeting_start": "",
                    "meeting_end": "",
                    "meeting_state": "reschedule_requested",
                    "status": "qualified",
                },
            )
        return _meeting_offer_payload(
            lead_email,
            "No problem. Here are some updated options.",
            lookahead_days=lookahead,
        )

    if intent in {"accept", "reschedule"}:
        resolved = _resolve_slot_selection(scheduling, offered_slots)
        if resolved is None and current_event.get("event_id") and intent == "accept":
            start_dt = datetime.fromisoformat(str(current_event.get("start") or ""))
            return {
                "subject": "Re: Meeting confirmed",
                "body": _meeting_confirmation_body(start_dt, current_event, rescheduled=False),
            }
        if resolved is None and offered_slots:
            return {
                "subject": "Re: Quick scheduling clarification",
                "body": (
                    "Hi,\n\nI want to make sure I book the right slot. Please reply with one of the available day/time options exactly as shown, or say next week / next month if you want a new set of slots.\n\n"
                    f"{lead.get('meeting_last_offer_text') or calendar_slots_text()}\n\n"
                    "Best,\nAI Sales Team"
                ),
            }
        if resolved is None:
            return _meeting_offer_payload(
                lead_email,
                "Happy to share availability.",
                lookahead_days=7,
            )

        start_dt, end_dt = resolved
        if current_event.get("event_id"):
            invite = update_calendar_invite(str(current_event["event_id"]), lead, start_dt, end_dt)
            rescheduled = True
        else:
            invite = create_calendar_invite(lead, start_dt, end_dt)
            rescheduled = False
        _store_meeting_updates(
            lead_email,
            {
                "status": "meeting_booked",
                "meeting_state": "booked",
                "meeting_event_id": invite.get("event_id", ""),
                "meeting_event_link": invite.get("meeting_link") or invite.get("html_link") or "",
                "meeting_start": invite.get("start", start_dt.isoformat()),
                "meeting_end": invite.get("end", end_dt.isoformat()),
            },
        )
        _clear_pending_meeting_options(lead_email)
        return {
            "subject": "Re: Meeting confirmed",
            "body": _meeting_confirmation_body(start_dt, invite, rescheduled=rescheduled),
        }

    if intent in {"decline", "ask_availability"}:
        lookahead = 14 if scheduling.get("asks_next_week") else 30 if scheduling.get("asks_next_month") else 7
        intro = "No problem. Here are some updated options." if intent == "decline" else "Happy to share availability."
        return _meeting_offer_payload(lead_email, intro, lookahead_days=lookahead)

    return None


def _sender_reply_payload(customer_addr: str, customer_text: str) -> Dict[str, str] | None:
    if is_exit_message(customer_text):
        return {
            "subject": "Re: Thank you",
            "body": "Hi,\n\nThank you for your message. I am closing this thread now.\n\nBest,\nAI Sales Team",
        }

    lead = leads_collection.find_one({"email": customer_addr}) or {"email": customer_addr}
    try:
        scheduling_payload = _handle_scheduling_workflow(lead, customer_text)
        if scheduling_payload:
            return scheduling_payload
    except Exception:
        pass

    calendar_text = calendar_slots_text()

    try:
        plan = generate_sender_reply_plan(lead=lead, customer_text=customer_text, calendar_text=calendar_text)
        if plan.get("should_close"):
            return {
                "subject": plan.get("subject") or "Re: Thank you",
                "body": plan.get("body") or "Hi,\n\nThank you for your message.\n\nBest,\nAI Sales Team",
            }
        if plan.get("body"):
            return {"subject": plan.get("subject") or "Re: Quick follow-up", "body": plan["body"]}
    except Exception:
        pass

    lead_score = int((lead or {}).get("lead_score", 50))
    lower = customer_text.lower()
    implementation_clarity_intent = any(
        phrase in lower
        for phrase in [
            "sounds useful",
            "implementation timeline",
            "implementation",
            "effort from our side",
            "effort on our side",
            "effort required",
        ]
    )
    action, _detail = decide_agent_next_action(customer_text, lead_score)

    if action == "end_sequence":
        return {
            "subject": "Re: Thanks for your time",
            "body": "Hi,\n\nThank you for your response. I will pause outreach for now.\n\nBest,\nAI Sales Team",
        }

    if contains_scheduling_intent(customer_text) or action == "propose_meeting" or implementation_clarity_intent:
        return _meeting_offer_payload(
            customer_addr,
            "Great question. Implementation is typically light on your side (kickoff + integration + enablement), and we can tailor exact effort to your workflow.",
            lookahead_days=7,
        )

    if action == "send_objection_handler":
        if any(token in lower for token in ["price", "cost", "budget"]):
            email_type = "price_objection"
        elif any(token in lower for token in ["later", "busy", "next month", "next quarter"]):
            email_type = "timing_objection"
        else:
            email_type = "case_study"
        if lead:
            return generate_email(lead, lead_score, email_type=email_type)

    if action == "send_proof" and lead:
        return generate_email(lead, lead_score, email_type="case_study")

    if lead:
        return generate_email(lead, lead_score, email_type="first_touch")

    return {
        "subject": "Re: Quick follow-up",
        "body": "Hi,\n\nThanks for the update. Could you share your priority so I can tailor next steps better?\n\nBest,\nAI Sales Team",
    }


def _extract_first_offered_slot_from_email(sender_body: str) -> str:
    lines = (sender_body or "").splitlines()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or "|" not in line:
            continue
        if line.lower().startswith("date") or line.startswith("---"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            continue
        date_label, day_label, window_label = parts[0], parts[1], parts[2]
        if not date_label or not day_label or not window_label or window_label.lower() == "busy":
            continue
        first_window = window_label.split(",")[0].strip()
        display_day = {
            "Mon": "Monday",
            "Tue": "Tuesday",
            "Wed": "Wednesday",
            "Thu": "Thursday",
            "Fri": "Friday",
            "Sat": "Saturday",
            "Sun": "Sunday",
        }.get(day_label, day_label)
        if "-" in first_window:
            start_time, end_time = [part.strip() for part in first_window.split("-", 1)]
            return f"{display_day}, {date_label} from {start_time} to {end_time} IST"
        return f"{display_day}, {date_label} at {first_window} IST"
    return "[Enter the exact date and time from the availability shared, for example Monday, 06 Apr from 10:17 AM to 06:00 PM IST]"


def _customer_intent_body(intent: str, custom_text: str, sender_body: str = "") -> Dict[str, str]:
    if intent == "meeting_yes":
        return {
            "subject": "Re: Meeting options",
            "body": "Hi,\n\nWednesday 10 AM IST works for me. Please send the invite.\n\nBest,\nDaniel",
        }
    if intent == "accept":
        preferred_slot = _extract_first_offered_slot_from_email(sender_body)
        return {
            "subject": "Re: Meeting options",
            "body": (
                "Hi,\n\n"
                "I would like to accept one of the available meeting slots.\n\n"
                f"Preferred slot: {preferred_slot}\n\n"
                "If that slot is still available, please send the calendar invite.\n\n"
                "Best,\nDaniel"
            ),
        }
    if intent == "reschedule":
        return {
            "subject": "Re: Meeting options",
            "body": (
                "Hi,\n\n"
                "The currently shared slots do not work for me. Could you please share a few slots for next week that work for you?\n\n"
                "Best,\nDaniel"
            ),
        }
    if intent == "decline":
        return {
            "subject": "Re: Meeting options",
            "body": (
                "Hi,\n\n"
                "Thank you for sharing the availability. I will not proceed with the meeting right now.\n\n"
                "Best,\nDaniel"
            ),
        }
    if intent == "need_clarity":
        return {
            "subject": "Re: Need more clarity",
            "body": "Hi,\n\nThis sounds useful. Can you explain implementation timeline and effort from our side?\n\nBest,\nDaniel",
        }
    if intent == "pricing_question":
        return {
            "subject": "Re: Pricing question",
            "body": "Hi,\n\nCan you share pricing and expected ROI for a 5-person SDR team?\n\nBest,\nDaniel",
        }
    if intent == "not_interested":
        return {
            "subject": "Re: Not interested",
            "body": "Hi,\n\nThank you for reaching out. Not interested right now.\n\nBest,\nDaniel",
        }
    if intent == "thank_you_close":
        return {
            "subject": "Re: Thank you",
            "body": "Hi,\n\nThank you for helping. This was useful.\n\nBest,\nDaniel",
        }
    shortcut = (custom_text or "").strip().lower()
    if intent == "custom" and shortcut in {"accept", "reschedule", "decline"}:
        return _customer_intent_body(intent=shortcut, custom_text="", sender_body=sender_body)

    return {
        "subject": "Re: Quick follow-up",
        "body": custom_text.strip() or "Hi,\n\nCould you share more details?\n\nBest,\nDaniel",
    }


def create_customer_intent_draft(intent: str, custom_text: str = "") -> Dict[str, str]:
    sender_addr = sender_email()
    customer_addr = customer_email()
    if not sender_addr or not customer_addr:
        raise ValueError("Sender/Customer emails are not configured in .env")

    sender_service = sender_gmail_service()
    customer_service = customer_gmail_service()
    actual_sender = profile_email(sender_service)
    actual_customer = profile_email(customer_service)
    if actual_sender != sender_addr:
        raise ValueError(f"Sender token mapped to {actual_sender}, expected {sender_addr}")
    if actual_customer != customer_addr:
        raise ValueError(f"Customer token mapped to {actual_customer}, expected {customer_addr}")

    latest_sender_msg = latest_message_from(customer_service, from_email=sender_addr)
    thread_id = latest_sender_msg.get("thread_id", "") if latest_sender_msg else ""
    subject_seed = latest_sender_msg.get("subject", "Quick follow-up") if latest_sender_msg else "Quick follow-up"

    latest_sender_body = latest_sender_msg.get("body", "") if latest_sender_msg else ""
    payload = _customer_intent_body(intent=intent, custom_text=custom_text, sender_body=latest_sender_body)
    subject = payload.get("subject") or safe_reply_subject(subject_seed)
    body = payload.get("body") or ""
    created = create_threaded_draft(customer_service, sender_addr, subject, body, thread_id, customer_addr)
    _insert_outbound_draft_log(sender_addr, customer_addr, sender_addr, subject, body, thread_id, created.get("draft_id", ""), "customer_manual_intent_draft")
    return {
        "status": "ok",
        "draft_id": created.get("draft_id", ""),
        "to": sender_addr,
        "subject": subject,
        "customer_email": customer_addr,
        "thread_id": thread_id,
    }


def _customer_auto_reply_payload(sender_text: str) -> Dict[str, str]:
    try:
        plan = generate_customer_reply_plan(sender_text)
        if plan.get("body"):
            return {
                "subject": plan.get("subject") or "Re: Quick follow-up",
                "body": plan["body"],
            }
    except Exception:
        pass
    return _customer_intent_body(intent="need_clarity", custom_text="")


def run_two_way_draft_cycle(max_messages_per_side: int = 5) -> Dict[str, int | str | bool]:
    sender_addr = sender_email()
    customer_addr = customer_email()
    if not sender_addr:
        raise ValueError("Set GMAIL_DRAFT_FROM_EMAIL or SENDGRID_FROM_EMAIL for sender mailbox.")
    if not customer_addr:
        raise ValueError("Set AUTO_REPLY_CUSTOMER_EMAIL in .env to enable customer-side auto-replies.")

    sender_service = sender_gmail_service()
    customer_service = customer_gmail_service()
    actual_sender = profile_email(sender_service)
    actual_customer = profile_email(customer_service)
    if actual_sender != sender_addr:
        raise ValueError(f"Sender token is authorized for {actual_sender}, expected {sender_addr}. Re-authorize sender inbox access.")
    if actual_customer != customer_addr:
        raise ValueError(f"Customer token is authorized for {actual_customer}, expected {customer_addr}. Re-authorize customer inbox access.")

    sender_candidates = list_messages_from(sender_service, from_email=customer_addr, limit=max_messages_per_side * 3, unread_only=False)
    customer_candidates = list_messages_from(customer_service, from_email=sender_addr, limit=max_messages_per_side * 3, unread_only=False)
    sender_inbound = [msg for msg in sender_candidates if not _is_inbound_already_processed(msg.get("id", ""))][:max_messages_per_side]
    customer_inbound = [msg for msg in customer_candidates if not _is_inbound_already_processed(msg.get("id", ""))][:max_messages_per_side]

    sender_drafts_created = 0
    customer_drafts_created = 0
    sender_exit_detected = False
    customer_exit_detected = False

    for message in sender_inbound:
        incoming_text = message.get("body", "")
        thread_id = message.get("thread_id", "")
        subject = message.get("subject", "(no subject)")
        message_id = str(message.get("id", "") or "")
        inserted = _upsert_inbound_log(customer_addr, customer_addr, sender_addr, subject, incoming_text, thread_id, message_id)
        if inserted:
            _safe_notification_once(
                notification_type="reply_received",
                lead_email=customer_addr,
                message=f"New customer reply received in sender inbox: {subject}",
                dedup_key=f"{customer_addr}|{thread_id}|inbound_customer_reply_sender_mailbox|{message_id}",
                additional_data={
                    "thread_id": thread_id,
                    "event_type": "inbound_customer_reply_sender_mailbox",
                    "source_message_id": message_id,
                    "source": "sender_replies",
                },
            )
        if is_exit_message(incoming_text):
            sender_exit_detected = True
            mark_as_read(sender_service, message.get("id", ""))
            continue
        reply_payload = _sender_reply_payload(customer_addr, incoming_text)
        if reply_payload:
            draft_subject = reply_payload.get("subject") or safe_reply_subject(subject)
            draft_body = reply_payload.get("body") or ""
            created = create_threaded_draft(sender_service, customer_addr, draft_subject, draft_body, thread_id, sender_addr)
            _insert_outbound_draft_log(customer_addr, sender_addr, customer_addr, draft_subject, draft_body, thread_id, created.get("draft_id", ""), "sender_auto_reply_draft")
            sender_drafts_created += 1
        mark_as_read(sender_service, message.get("id", ""))

    for message in customer_inbound:
        incoming_text = message.get("body", "")
        thread_id = message.get("thread_id", "")
        subject = message.get("subject", "(no subject)")
        message_id = str(message.get("id", "") or "")
        inserted = _upsert_inbound_log(sender_addr, sender_addr, customer_addr, subject, incoming_text, thread_id, message_id)
        if inserted:
            _safe_notification_once(
                notification_type="reply_received",
                lead_email=sender_addr,
                message=f"New sender reply received in customer inbox: {subject}",
                dedup_key=f"{sender_addr}|{thread_id}|inbound_sender_reply_customer_mailbox|{message_id}",
                additional_data={
                    "thread_id": thread_id,
                    "event_type": "inbound_sender_reply_customer_mailbox",
                    "source_message_id": message_id,
                    "source": "customer_replies",
                },
            )
        if is_exit_message(incoming_text):
            customer_exit_detected = True
            mark_as_read(customer_service, message.get("id", ""))
            continue
        payload = _customer_auto_reply_payload(incoming_text)
        draft_subject = payload.get("subject") or safe_reply_subject(subject)
        draft_body = payload.get("body") or ""
        created = create_threaded_draft(customer_service, sender_addr, draft_subject, draft_body, thread_id, customer_addr)
        _insert_outbound_draft_log(sender_addr, customer_addr, sender_addr, draft_subject, draft_body, thread_id, created.get("draft_id", ""), "customer_auto_reply_draft")
        customer_drafts_created += 1
        mark_as_read(customer_service, message.get("id", ""))

    return {
        "status": "ok",
        "sender_inbound_processed": len(sender_inbound),
        "customer_inbound_processed": len(customer_inbound),
        "sender_drafts_created": sender_drafts_created,
        "customer_drafts_created": customer_drafts_created,
        "sender_email": sender_addr,
        "customer_email": customer_addr,
        "actual_sender_email": actual_sender,
        "actual_customer_email": actual_customer,
        "sender_exit_detected": sender_exit_detected,
        "customer_exit_detected": customer_exit_detected,
    }


def run_two_way_multi_cycle(max_rounds: int = 4, max_messages_per_side: int = 5) -> Dict[str, int | str | bool]:
    rounds_run = 0
    total_sender_inbound = 0
    total_customer_inbound = 0
    total_sender_drafts = 0
    total_customer_drafts = 0
    sender_exit = False
    customer_exit = False
    last_sender_email = ""
    last_customer_email = ""

    for _ in range(max_rounds):
        cycle = run_two_way_draft_cycle(max_messages_per_side=max_messages_per_side)
        rounds_run += 1
        total_sender_inbound += int(cycle.get("sender_inbound_processed", 0))
        total_customer_inbound += int(cycle.get("customer_inbound_processed", 0))
        total_sender_drafts += int(cycle.get("sender_drafts_created", 0))
        total_customer_drafts += int(cycle.get("customer_drafts_created", 0))
        last_sender_email = str(cycle.get("actual_sender_email", ""))
        last_customer_email = str(cycle.get("actual_customer_email", ""))
        sender_exit = sender_exit or bool(cycle.get("sender_exit_detected", False))
        customer_exit = customer_exit or bool(cycle.get("customer_exit_detected", False))
        no_new_work = (
            int(cycle.get("sender_inbound_processed", 0)) == 0
            and int(cycle.get("customer_inbound_processed", 0)) == 0
            and int(cycle.get("sender_drafts_created", 0)) == 0
            and int(cycle.get("customer_drafts_created", 0)) == 0
        )
        if no_new_work or sender_exit or customer_exit:
            break

    return {
        "status": "ok",
        "rounds_run": rounds_run,
        "sender_inbound_processed": total_sender_inbound,
        "customer_inbound_processed": total_customer_inbound,
        "sender_drafts_created": total_sender_drafts,
        "customer_drafts_created": total_customer_drafts,
        "actual_sender_email": last_sender_email,
        "actual_customer_email": last_customer_email,
        "sender_exit_detected": sender_exit,
        "customer_exit_detected": customer_exit,
    }
