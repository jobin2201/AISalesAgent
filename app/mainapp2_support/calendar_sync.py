import os
from datetime import datetime, timezone

from app.conversation_flow.calendar_tools import sender_calendar_service


def _calendar_id() -> str:
    return (os.getenv("GOOGLE_CALENDAR_ID") or "primary").strip() or "primary"


def _attendee_status(event: dict, email: str) -> str:
    target = (email or "").strip().lower()
    if not target:
        return ""
    for attendee in event.get("attendees", []) or []:
        attendee_email = str(attendee.get("email") or "").strip().lower()
        if attendee_email == target:
            return str(attendee.get("responseStatus") or "").strip().lower()
    return ""


def _iso_value(value: str) -> str:
    if not value:
        return ""
    return value.replace("Z", "+00:00")


def _extract_event_window(event: dict) -> tuple[str, str]:
    start_obj = event.get("start") or {}
    end_obj = event.get("end") or {}
    start = _iso_value(str(start_obj.get("dateTime") or ""))
    end = _iso_value(str(end_obj.get("dateTime") or ""))
    if not start:
        start = _iso_value(str(start_obj.get("date") or ""))
    if not end:
        end = _iso_value(str(end_obj.get("date") or ""))
    return start, end


def sync_lead_meeting_from_calendar(new_leads_collection, lead: dict) -> dict:
    event_id = str(lead.get("meeting_event_id") or "").strip()
    if not event_id:
        return lead

    try:
        service = sender_calendar_service()
        event = service.events().get(calendarId=_calendar_id(), eventId=event_id).execute()
    except Exception:
        return lead

    status = str(event.get("status") or "").strip().lower()
    start, end = _extract_event_window(event)
    html_link = str(event.get("htmlLink") or "").strip()
    contact_email = str(lead.get("meeting_contact_email") or lead.get("email") or "").strip().lower()
    attendee_response = _attendee_status(event, contact_email)

    next_state = str(lead.get("meeting_state") or "")
    if status == "cancelled":
        next_state = "cancelled"
    elif attendee_response == "declined":
        next_state = "declined"
    elif attendee_response in {"accepted", "tentative"}:
        next_state = "booked"
    elif status == "confirmed" and next_state in {"", "options_offered", "reschedule_requested"}:
        next_state = "booked"

    updates: dict[str, object] = {}
    if next_state and next_state != str(lead.get("meeting_state") or ""):
        updates["meeting_state"] = next_state
    if start and start != str(lead.get("meeting_start") or ""):
        updates["meeting_start"] = start
    if end and end != str(lead.get("meeting_end") or ""):
        updates["meeting_end"] = end
    if html_link and html_link != str(lead.get("meeting_event_link") or ""):
        updates["meeting_event_link"] = html_link

    if status == "cancelled":
        if str(lead.get("meeting_event_link") or ""):
            updates["meeting_event_link"] = ""
        if str(lead.get("meeting_start") or ""):
            updates["meeting_start"] = ""
        if str(lead.get("meeting_end") or ""):
            updates["meeting_end"] = ""

    if not updates:
        return lead

    updates["updated_at"] = datetime.now(timezone.utc)
    new_leads_collection.update_one({"_id": lead["_id"]}, {"$set": updates})
    lead.update(updates)
    return lead
