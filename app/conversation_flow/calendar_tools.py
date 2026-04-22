from datetime import datetime, timedelta, timezone
from typing import Dict, List
from uuid import uuid4

from googleapiclient.discovery import build

from app.conversation_flow.config import SENDER_LOOP_SCOPES, get_credentials, sender_timezone, sender_token_path


def sender_calendar_service():
    creds = get_credentials(
        sender_token_path(),
        interactive=False,
        required_scopes=SENDER_LOOP_SCOPES,
        auth_label="Sender",
    )
    return build("calendar", "v3", credentials=creds)


def _calendar_id() -> str:
    import os

    return (os.getenv("GOOGLE_CALENDAR_ID") or "primary").strip() or "primary"


def _workday_start_hour() -> int:
    import os

    try:
        value = int((os.getenv("CALENDAR_WORKDAY_START_HOUR") or "9").strip())
    except Exception:
        value = 9
    return max(0, min(23, value))


def _workday_end_hour() -> int:
    import os

    try:
        value = int((os.getenv("CALENDAR_WORKDAY_END_HOUR") or "18").strip())
    except Exception:
        value = 18
    return max(1, min(24, value))


def _lookahead_days() -> int:
    import os

    try:
        value = int((os.getenv("CALENDAR_LOOKAHEAD_DAYS") or "7").strip())
    except Exception:
        value = 7
    return max(1, min(31, value))


def _minimum_slot_minutes() -> int:
    import os

    try:
        value = int((os.getenv("CALENDAR_MIN_SLOT_MINUTES") or "30").strip())
    except Exception:
        value = 30
    return max(15, min(180, value))


def format_time_range(start_dt: datetime, end_dt: datetime) -> str:
    return f"{start_dt.strftime('%I:%M %p')}-{end_dt.strftime('%I:%M %p')}"


def available_calendar_slots(lookahead_days: int | None = None) -> Dict[str, object]:
    tz = sender_timezone()
    now_local = datetime.now(tz)
    start_window = now_local + timedelta(hours=1)
    end_window = start_window + timedelta(days=lookahead_days or _lookahead_days())

    calendar_service = sender_calendar_service()
    calendar_id = _calendar_id()
    busy_resp = calendar_service.freebusy().query(
        body={
            "timeMin": start_window.astimezone(timezone.utc).isoformat(),
            "timeMax": end_window.astimezone(timezone.utc).isoformat(),
            "items": [{"id": calendar_id}],
            "timeZone": str(tz),
        }
    ).execute()

    raw_busy = busy_resp.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    busy_ranges: List[tuple[datetime, datetime]] = []
    for interval in raw_busy:
        busy_start = datetime.fromisoformat(interval["start"].replace("Z", "+00:00")).astimezone(tz)
        busy_end = datetime.fromisoformat(interval["end"].replace("Z", "+00:00")).astimezone(tz)
        if busy_end > start_window and busy_start < end_window:
            busy_ranges.append((max(busy_start, start_window), min(busy_end, end_window)))

    busy_ranges.sort(key=lambda item: item[0])
    merged_busy: List[tuple[datetime, datetime]] = []
    for busy_start, busy_end in busy_ranges:
        if not merged_busy or busy_start > merged_busy[-1][1]:
            merged_busy.append((busy_start, busy_end))
        else:
            merged_busy[-1] = (merged_busy[-1][0], max(merged_busy[-1][1], busy_end))

    start_hour = _workday_start_hour()
    end_hour = _workday_end_hour()
    min_slot = timedelta(minutes=_minimum_slot_minutes())
    slots: List[Dict[str, str]] = []

    cursor_day = start_window.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end_window.replace(hour=0, minute=0, second=0, microsecond=0)

    while cursor_day <= end_day:
        day_start = cursor_day.replace(hour=start_hour, minute=0)
        if end_hour == 24:
            day_end = cursor_day.replace(hour=23, minute=59)
        else:
            day_end = cursor_day.replace(hour=end_hour, minute=0)
        if cursor_day.date() == start_window.date() and start_window > day_start:
            day_start = start_window
        if day_end <= day_start:
            cursor_day += timedelta(days=1)
            continue

        day_busy = []
        for busy_start, busy_end in merged_busy:
            if busy_end <= day_start or busy_start >= day_end:
                continue
            day_busy.append((max(busy_start, day_start), min(busy_end, day_end)))

        free_windows: List[tuple[datetime, datetime]] = []
        pointer = day_start
        for busy_start, busy_end in day_busy:
            if busy_start > pointer:
                free_windows.append((pointer, busy_start))
            pointer = max(pointer, busy_end)
        if pointer < day_end:
            free_windows.append((pointer, day_end))

        for free_start, free_end in free_windows:
            if (free_end - free_start) < min_slot:
                continue
            slots.append(
                {
                    "start": free_start.isoformat(),
                    "end": free_end.isoformat(),
                    "date_label": free_start.strftime("%d %b"),
                    "weekday": free_start.strftime("%A"),
                    "weekday_short": free_start.strftime("%a"),
                    "time_label": format_time_range(free_start, free_end),
                    "display": f"{free_start.strftime('%A, %d %b')} {format_time_range(free_start, free_end)}",
                }
            )
        cursor_day += timedelta(days=1)

    return {
        "slots": slots,
        "timezone": str(tz),
        "calendar_id": calendar_id,
    }


def _render_slots_text(slots: List[Dict[str, str]], tz_name: str, calendar_id: str) -> str:
    lines: List[str] = [
        "Date       | Day | Free windows",
        "-----------|-----|----------------------------------------------------",
    ]

    grouped: dict[tuple[str, str], list[str]] = {}
    for slot in slots:
        key = (slot["date_label"], slot["weekday_short"])
        grouped.setdefault(key, []).append(slot["time_label"])

    for (date_label, weekday_short), windows in grouped.items():
        lines.append(f"{date_label:<10} | {weekday_short:<3} | {', '.join(windows)}")

    if len(lines) > 2:
        lines.append("")
        lines.append(f"Timezone: {tz_name}")
        lines.append(f"Calendar: {calendar_id}")
        return "\n".join(lines)

    return (
        "Date       | Day | Free windows\n"
        "-----------|-----|-------------------------------\n"
        "No free windows found in the current lookahead period.\n"
        "Please share if you prefer next week or next month."
    )


def calendar_slots_text_for_days(lookahead_days: int | None = None) -> str:
    try:
        slot_payload = available_calendar_slots(lookahead_days=lookahead_days)
        return _render_slots_text(
            slot_payload["slots"],
            str(slot_payload["timezone"]),
            str(slot_payload["calendar_id"]),
        )
    except Exception as exc:
        return (
            "Date       | Day | Free windows\n"
            "-----------|-----|-------------------------------\n"
            f"(Unable to read calendar right now: {exc})\n"
            "Please re-authorize sender access with calendar scope and try again."
        )


def calendar_slots_text() -> str:
    try:
        return calendar_slots_text_for_days()
    except Exception as exc:
        return (
            "Date       | Day | Free windows\n"
            "-----------|-----|-------------------------------\n"
            f"(Unable to read calendar right now: {exc})\n"
            "Please re-authorize sender access with calendar scope and try again."
        )

    return (
        "Date       | Day | Free windows\n"
        "-----------|-----|-------------------------------\n"
        "(Unable to read calendar right now)\n"
        "Please share if you prefer next week or next month."
    )


def create_calendar_invite(
    lead: Dict,
    start_dt: datetime,
    end_dt: datetime,
    thread_id: str = "",
) -> Dict[str, str]:
    calendar_service = sender_calendar_service()
    calendar_id = _calendar_id()
    tz_name = str(sender_timezone())
    attendee_email = (lead.get("email") or "").strip().lower()
    attendee_name = (lead.get("name") or attendee_email or "Prospect").strip()
    company = (lead.get("company") or "Prospect").strip()

    body = {
        "summary": f"Intro call - {company}",
        "description": (
            "Auto-created from AI Sales Agent scheduling flow.\n"
            f"Lead email: {attendee_email or 'unknown'}\n"
            f"Lead name: {attendee_name}\n"
            f"Thread ID: {thread_id or 'n/a'}"
        ),
        "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name},
        "attendees": ([{"email": attendee_email, "displayName": attendee_name}] if attendee_email else []),
    }

    try:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
        created = calendar_service.events().insert(
            calendarId=calendar_id,
            body=body,
            sendUpdates="all",
            conferenceDataVersion=1,
        ).execute()
    except Exception:
        body.pop("conferenceData", None)
        created = calendar_service.events().insert(
            calendarId=calendar_id,
            body=body,
            sendUpdates="all",
        ).execute()

    return {
        "event_id": created.get("id", ""),
        "html_link": created.get("htmlLink", ""),
        "meeting_link": ((created.get("hangoutLink") or "") if isinstance(created, dict) else ""),
        "start": ((created.get("start") or {}).get("dateTime") or start_dt.isoformat()),
        "end": ((created.get("end") or {}).get("dateTime") or end_dt.isoformat()),
    }


def update_calendar_invite(
    event_id: str,
    lead: Dict,
    start_dt: datetime,
    end_dt: datetime,
    thread_id: str = "",
) -> Dict[str, str]:
    if not event_id:
        raise ValueError("Missing calendar event id for update")

    calendar_service = sender_calendar_service()
    calendar_id = _calendar_id()
    existing = calendar_service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    attendee_email = (lead.get("email") or "").strip().lower()
    attendee_name = (lead.get("name") or attendee_email or "Prospect").strip()
    tz_name = str(sender_timezone())

    existing["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz_name}
    existing["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz_name}
    if attendee_email:
        existing["attendees"] = [{"email": attendee_email, "displayName": attendee_name}]
    description = existing.get("description") or ""
    if thread_id and f"Thread ID: {thread_id}" not in description:
        existing["description"] = f"{description}\nThread ID: {thread_id}".strip()

    updated = calendar_service.events().update(
        calendarId=calendar_id,
        eventId=event_id,
        body=existing,
        sendUpdates="all",
        conferenceDataVersion=1,
    ).execute()
    return {
        "event_id": updated.get("id", ""),
        "html_link": updated.get("htmlLink", ""),
        "meeting_link": ((updated.get("hangoutLink") or "") if isinstance(updated, dict) else ""),
        "start": ((updated.get("start") or {}).get("dateTime") or start_dt.isoformat()),
        "end": ((updated.get("end") or {}).get("dateTime") or end_dt.isoformat()),
    }


def cancel_calendar_invite(event_id: str) -> None:
    if not event_id:
        return
    calendar_service = sender_calendar_service()
    calendar_id = _calendar_id()
    event = calendar_service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    event["status"] = "cancelled"
    calendar_service.events().update(
        calendarId=calendar_id,
        eventId=event_id,
        body=event,
        sendUpdates="all",
    ).execute()
