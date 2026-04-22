import json
from typing import Dict
from urllib import error, request

from app.conversation_flow.config import groq_api_key, groq_base_url, groq_model


def _post_chat(messages: list[dict], temperature: float = 0.2, max_tokens: int = 500) -> str:
    api_key = groq_api_key()
    if not api_key:
        raise ValueError("GROQ_API_KEY is missing")

    payload = {
        "model": groq_model(),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=f"{groq_base_url()}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Groq request failed: {detail}") from exc
    except Exception as exc:
        raise ValueError(f"Groq request failed: {exc}") from exc

    parsed = json.loads(raw)
    return parsed["choices"][0]["message"]["content"]


def _parse_json_content(content: str) -> Dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])
        raise


def generate_sender_reply_plan(lead: Dict, customer_text: str, calendar_text: str) -> Dict:
    lead_name = lead.get("name") or "there"
    company = lead.get("company") or "the team"
    lead_score = lead.get("lead_score", 0)
    industry = lead.get("industry") or "business"

    system = (
        "You are an AI sales assistant writing concise, relevant email drafts. "
        "Use the customer's latest email, lead context, and available calendar text. "
        "Return JSON only with keys: subject, body, intent, should_close. "
        "If the customer asks about implementation, onboarding effort, timeline, meeting, calendar, schedule, or availability, "
        "answer briefly and include this week's availability from the provided calendar text. "
        "Ask whether next week or next month would be better if this week does not work. "
        "Do not invent unavailable times outside the provided calendar text. "
        "Do not mention attachments unless explicitly requested."
    )
    user = {
        "lead_name": lead_name,
        "company": company,
        "lead_score": lead_score,
        "industry": industry,
        "customer_email_text": customer_text,
        "calendar_text": calendar_text,
    }
    content = _post_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user)},
        ],
        temperature=0.1,
        max_tokens=700,
    )
    parsed = _parse_json_content(content)
    return {
        "subject": str(parsed.get("subject") or "Re: Quick follow-up"),
        "body": str(parsed.get("body") or ""),
        "intent": str(parsed.get("intent") or "unknown"),
        "should_close": bool(parsed.get("should_close", False)),
    }


def generate_customer_reply_plan(sender_text: str) -> Dict:
    system = (
        "You are simulating a prospect named Daniel replying to a sales email. "
        "Return JSON only with keys: subject, body, intent, should_close. "
        "Reply naturally and briefly. If the sender offers calendar times, choose one. "
        "If the sender asks a clarifying question, ask for implementation or ROI details. "
        "If the sender closes the conversation, respond with thanks and set should_close=true."
    )
    content = _post_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": sender_text},
        ],
        temperature=0.3,
        max_tokens=300,
    )
    parsed = _parse_json_content(content)
    return {
        "subject": str(parsed.get("subject") or "Re: Quick follow-up"),
        "body": str(parsed.get("body") or "Hi,\n\nCould you share more details?\n\nBest,\nDaniel"),
        "intent": str(parsed.get("intent") or "clarify"),
        "should_close": bool(parsed.get("should_close", False)),
    }


def extract_scheduling_response(customer_text: str, offered_slots: list[dict], current_event: Dict | None = None) -> Dict:
    system = (
        "You classify customer email replies in a scheduling workflow. "
        "Return JSON only with keys: intent, selected_slot_index, requested_time_text, confidence, notes, asks_next_week, asks_next_month. "
        "intent must be one of: accept, reschedule, decline, not_interested, ask_availability, unclear. "
        "Use not_interested for unsubscribe/no/not interested/stop outreach. "
        "Use decline when they decline a proposed time but are still open to scheduling later. "
        "Use reschedule when they suggest another day/time or ask for next week/next month/later. "
        "Use ask_availability when they want time options but did not choose one. "
        "selected_slot_index must be -1 if no offered slot clearly matches. "
        "requested_time_text should be the exact requested time phrase if present, otherwise empty."
    )
    user = {
        "customer_text": customer_text,
        "offered_slots": offered_slots,
        "current_event": current_event or {},
    }
    content = _post_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user)},
        ],
        temperature=0,
        max_tokens=350,
    )
    parsed = _parse_json_content(content)
    try:
        selected_slot_index = int(parsed.get("selected_slot_index", -1))
    except Exception:
        selected_slot_index = -1
    return {
        "intent": str(parsed.get("intent") or "unclear"),
        "selected_slot_index": selected_slot_index,
        "requested_time_text": str(parsed.get("requested_time_text") or "").strip(),
        "confidence": str(parsed.get("confidence") or "low"),
        "notes": str(parsed.get("notes") or "").strip(),
        "asks_next_week": bool(parsed.get("asks_next_week", False)),
        "asks_next_month": bool(parsed.get("asks_next_month", False)),
    }