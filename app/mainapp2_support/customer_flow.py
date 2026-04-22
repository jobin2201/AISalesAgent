from datetime import datetime, timezone

from app.conversation_flow.google_tools import (
    contains_scheduling_intent,
    create_threaded_draft,
    customer_gmail_service,
    latest_message_from,
    profile_email,
    safe_reply_subject,
)
from app.mainapp2_support.messaging import customer_loop_address, log_message, sender_loop_address


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
    return "[Enter the exact date and time from the availability shared]"


def customer_flow_choices(sender_subject: str, sender_body: str) -> tuple[list[dict[str, str]], str]:
    combined = f"{sender_subject}\n{sender_body}".lower()
    if contains_scheduling_intent(combined):
        return (
            [
                {"id": "accept", "label": "Accept a proposed slot", "description": "Confirm one of the shared meeting options."},
                {"id": "postpone", "label": "Postpone or reschedule", "description": "Ask for another set of meeting times."},
                {"id": "decline", "label": "Decline the meeting", "description": "Close out the scheduling thread politely."},
                {"id": "need_clarity", "label": "Ask for more clarity", "description": "Request more details before accepting the call."},
                {"id": "custom", "label": "Write a custom reply", "description": "Draft your own reply manually."},
            ],
            "accept",
        )
    if any(token in combined for token in ["price", "pricing", "cost", "budget", "roi", "commercial"]):
        return (
            [
                {"id": "pricing_question", "label": "Ask pricing questions", "description": "Request pricing, ROI, or commercial details."},
                {"id": "need_clarity", "label": "Ask for more clarity", "description": "Get more implementation detail before replying."},
                {"id": "not_interested", "label": "Not interested", "description": "Close the conversation politely."},
                {"id": "custom", "label": "Write a custom reply", "description": "Draft your own reply manually."},
            ],
            "pricing_question",
        )
    if any(token in combined for token in ["timeline", "implementation", "integrat", "rollout", "effort", "case study", "how does"]):
        return (
            [
                {"id": "need_clarity", "label": "Ask for more clarity", "description": "Ask for operational or implementation details."},
                {"id": "pricing_question", "label": "Ask pricing questions", "description": "Request commercial details before proceeding."},
                {"id": "thank_you_close", "label": "Acknowledge and close", "description": "Reply positively without committing to a call."},
                {"id": "custom", "label": "Write a custom reply", "description": "Draft your own reply manually."},
            ],
            "need_clarity",
        )
    return (
        [
            {"id": "need_clarity", "label": "Ask for more clarity", "description": "Request a more specific follow-up from the sender."},
            {"id": "thank_you_close", "label": "Acknowledge and close", "description": "Reply courteously without opening a new thread."},
            {"id": "not_interested", "label": "Not interested", "description": "Politely decline the outreach."},
            {"id": "custom", "label": "Write a custom reply", "description": "Draft your own reply manually."},
        ],
        "need_clarity",
    )


def customer_reply_template(intent: str, sender_subject: str, sender_body: str) -> tuple[str, str]:
    subject = safe_reply_subject(sender_subject or "Quick follow-up")
    if intent == "accept":
        preferred_slot = _extract_first_offered_slot_from_email(sender_body)
        return (
            subject,
            (
                "Hi,\n\n"
                "I would like to accept one of the meeting slots you shared.\n\n"
                f"Preferred slot: {preferred_slot}\n\n"
                "If that slot is still available, please send the calendar invite.\n\n"
                "Best,\nCustomer"
            ),
        )
    if intent == "postpone":
        return (
            subject,
            (
                "Hi,\n\n"
                "The currently shared slots do not work for me. Could we postpone this and look at a few options for next week?\n\n"
                "Best,\nCustomer"
            ),
        )
    if intent == "decline":
        return (
            subject,
            (
                "Hi,\n\n"
                "Thank you for sharing the availability. I will not proceed with the meeting right now.\n\n"
                "Best,\nCustomer"
            ),
        )
    if intent == "pricing_question":
        return (
            subject,
            (
                "Hi,\n\n"
                "Can you share pricing and expected ROI for our team before we proceed further?\n\n"
                "Best,\nCustomer"
            ),
        )
    if intent == "thank_you_close":
        return (
            subject,
            (
                "Hi,\n\n"
                "Thank you for the note. This is helpful, and I will review it internally before coming back to you.\n\n"
                "Best,\nCustomer"
            ),
        )
    if intent == "not_interested":
        return (
            subject,
            (
                "Hi,\n\n"
                "Thank you for reaching out. We will not move forward with this right now.\n\n"
                "Best,\nCustomer"
            ),
        )
    return (
        subject,
        (
            "Hi,\n\n"
            "This sounds useful. Can you share a bit more detail on the implementation timeline and effort from our side?\n\n"
            "Best,\nCustomer"
        ),
    )


def customer_message_context() -> dict[str, object]:
    sender_addr = sender_loop_address()
    customer_addr = customer_loop_address()
    if not sender_addr:
        raise ValueError("Sender mailbox is not configured for the customer reply flow.")
    if not customer_addr:
        raise ValueError("Customer mailbox is not configured for the customer reply flow.")

    customer_service = customer_gmail_service()
    actual_customer = profile_email(customer_service)
    if actual_customer != customer_addr:
        raise ValueError(f"Customer token is authorized for {actual_customer}, expected {customer_addr}.")

    latest_sender_msg = latest_message_from(customer_service, from_email=sender_addr)
    return {
        "sender_email": sender_addr,
        "customer_email": customer_addr,
        "latest_message": latest_sender_msg,
    }


def draft_customer_mailbox_reply(
    product_description: str,
    company_name: str,
    subject: str,
    body: str,
    thread_id: str,
    sender_addr: str,
    customer_addr: str,
) -> dict[str, str]:
    customer_service = customer_gmail_service()
    actual_customer = profile_email(customer_service)
    if actual_customer != customer_addr:
        raise ValueError(f"Customer token is authorized for {actual_customer}, expected {customer_addr}.")

    created = create_threaded_draft(
        customer_service,
        sender_addr,
        subject,
        body,
        thread_id,
        customer_addr,
    )
    log_message(
        {
            "product_description": product_description,
            "company_name": company_name,
            "website": "",
            "to": sender_addr,
            "cc": "",
            "from_email": customer_addr,
            "subject": safe_reply_subject(subject),
            "body": body,
            "status": "draft",
            "delivery_mode": "customer_gmail_draft",
            "external_draft_id": created.get("draft_id", ""),
            "error": "",
            "actor": "customer",
            "created_at": datetime.now(timezone.utc),
        }
    )
    return {
        "status": "draft",
        "delivery_mode": "customer_gmail_draft",
        "external_draft_id": created.get("draft_id", ""),
    }