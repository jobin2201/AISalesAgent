import importlib.util
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from pymongo import DESCENDING


BASE_DIR = Path(__file__).resolve().parents[1]


def _bootstrap_app_package() -> None:
    pkg_dir = BASE_DIR / "app"

    if "app" in sys.modules and getattr(sys.modules["app"], "__file__", "") == str(BASE_DIR / "mainapp2.py"):
        del sys.modules["app"]

    app_init = pkg_dir / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "app",
        app_init,
        submodule_search_locations=[str(pkg_dir)],
    )
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules["app"] = module
        spec.loader.exec_module(module)


_bootstrap_app_package()
load_dotenv(BASE_DIR / ".env")

from app.conversation_flow.flow import run_two_way_draft_cycle
from app.conversation_flow.calendar_tools import calendar_slots_text_for_days
from app.conversation_flow.google_tools import contains_scheduling_intent
from app.mainapp2_support import (
    EMAILS_COLLECTION_NAME,
    LEADS_COLLECTION_NAME,
    MESSAGE_LOG_COLLECTION_NAME,
    NOTIFICATIONS_COLLECTION_NAME,
    apply_sender_plan,
    collect_recipients,
    configure_messaging,
    configure_notifications,
    configure_sender_flow,
    create_notification,
    customer_flow_choices,
    customer_loop_address,
    customer_message_context,
    customer_reply_template,
    database,
    delete_notification,
    draft_customer_mailbox_reply,
    ensure_sender_signature,
    get_all_notifications,
    get_unread_count,
    get_unread_notifications,
    init_db2,
    list_sender_conversations,
    mark_all_as_read,
    mark_as_read,
    sender_loop_address,
    send_new_lead_message,
    sender_reply_plan,
)
from app.mainapp2_support.calendar_sync import sync_lead_meeting_from_calendar


app = Flask(__name__, template_folder="templates", static_folder="static")


init_db2()
db = database()
new_leads_collection = db[LEADS_COLLECTION_NAME]
new_emails_collection = db[EMAILS_COLLECTION_NAME]
message_log_collection = db[MESSAGE_LOG_COLLECTION_NAME]
notifications_collection = db[NOTIFICATIONS_COLLECTION_NAME]

configure_messaging(message_log_collection)
configure_sender_flow(new_leads_collection, message_log_collection)
configure_notifications(notifications_collection)


APP_TITLE = os.getenv("STREAMLIT_APP_TITLE", "AI Sales Agent Dashboard") + " Web"
FROM_EMAIL = (os.getenv("GMAIL_DRAFT_FROM_EMAIL") or os.getenv("SENDGRID_FROM_EMAIL") or "").strip()
CC_EMAIL = (os.getenv("AUTO_REPLY_CUSTOMER_EMAIL") or "").strip()


def _json_safe(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return value


def _find_lead_or_404(lead_id: str) -> dict:
    try:
        oid = ObjectId(lead_id)
    except Exception as exc:
        raise ValueError("Invalid lead id") from exc

    lead = new_leads_collection.find_one({"_id": oid})
    if not lead:
        raise LookupError("Lead not found")
    return lead


@app.get("/")
def index() -> str:
    return render_template("index.html", app_title=APP_TITLE)


@app.get("/api/health")
def health() -> Any:
    return jsonify({"status": "ok"})


@app.get("/api/bootstrap")
def bootstrap_state() -> Any:
    product_options = sorted(filter(None, new_leads_collection.distinct("product_description")))
    return jsonify(
        {
            "app_title": APP_TITLE,
            "from_email": FROM_EMAIL,
            "cc_email": CC_EMAIL,
            "sender_loop": sender_loop_address(),
            "customer_loop": customer_loop_address(),
            "products": product_options,
            "unread_notifications": int(get_unread_count()),
        }
    )


@app.get("/api/leads")
def list_leads() -> Any:
    selected_product = (request.args.get("product") or "").strip()
    company_search = (request.args.get("q") or "").strip()
    min_score = int(request.args.get("min_score", 0))
    priorities_raw = (request.args.get("priorities") or "HIGH,MEDIUM,LOW").strip()
    priorities = [item.strip().upper() for item in priorities_raw.split(",") if item.strip()]

    lead_query: dict[str, Any] = {"total_score": {"$gte": min_score}}
    if selected_product:
        # Enforce exact product match (case-insensitive) so DigiCom/DigiExpense filter is strict.
        lead_query["product_description"] = {"$regex": f"^{re.escape(selected_product)}$", "$options": "i"}
    if priorities:
        lead_query["priority"] = {"$in": priorities}
    if company_search:
        lead_query["company_name"] = {"$regex": company_search, "$options": "i"}

    docs = list(new_leads_collection.find(lead_query).sort("imported_at", DESCENDING))
    rows = [
        {
            "id": str(doc.get("_id")),
            "company_name": doc.get("company_name", ""),
            "product_description": doc.get("product_description", ""),
            "industry": doc.get("industry", ""),
            "priority": doc.get("priority", ""),
            "total_score": doc.get("total_score", 0),
            "website": doc.get("website", ""),
            "source_file": doc.get("source_file", ""),
        }
        for doc in docs
    ]

    return jsonify(
        {
            "count": len(rows),
            "rows": rows,
            "metrics": {
                "leads": len(rows),
                "products": len(sorted(filter(None, new_leads_collection.distinct("product_description")))),
                "email_contacts": int(new_emails_collection.count_documents({})),
            },
        }
    )


@app.get("/api/leads/<lead_id>")
def get_lead_detail(lead_id: str) -> Any:
    try:
        lead = _find_lead_or_404(lead_id)
    except ValueError:
        return jsonify({"error": "Invalid lead id"}), 400
    except LookupError:
        return jsonify({"error": "Lead not found"}), 404

    lead = sync_lead_meeting_from_calendar(new_leads_collection, lead)

    related_email_docs = list(
        new_emails_collection.find(
            {
                "product_description": lead.get("product_description", ""),
                "company": lead.get("company_name", ""),
            }
        ).sort("imported_at", DESCENDING)
    )
    recipients = collect_recipients(lead, related_email_docs)

    sender_addr = sender_loop_address()
    customer_addr = customer_loop_address()
    recipients_norm = [str(item or "").strip().lower() for item in (recipients or [])]
    participants = [
        email
        for email in dict.fromkeys(recipients_norm)
        if email and email not in {sender_addr, customer_addr}
    ]

    conversations = []
    try:
        conversations = list_sender_conversations(participants)
    except Exception as exc:
        conversations = [{"error": str(exc)}]

    company_name = str(lead.get("company_name") or "").strip().lower()
    if company_name:
        filtered_conversations = []
        for item in conversations:
            if item.get("error"):
                filtered_conversations.append(item)
                continue
            subject = str(item.get("subject") or "").lower()
            body = str(item.get("body") or "").lower()
            from_email = str(item.get("from_email") or "").strip().lower()
            if company_name in subject or company_name in body or from_email in set(participants):
                filtered_conversations.append(item)
        conversations = filtered_conversations

    company_name = str(lead.get("company_name") or "").strip()
    sender_history_query: dict[str, Any] = {"company_name": company_name}
    if sender_addr:
        sender_history_query["from_email"] = sender_addr

    sender_contact_docs = list(
        message_log_collection.find(sender_history_query).sort("created_at", DESCENDING).limit(200)
    )
    sender_contacts = sorted({str(doc.get("to") or "").strip().lower() for doc in sender_contact_docs if str(doc.get("to") or "").strip()})

    display_name = str(
        lead.get("contact_name")
        or lead.get("company_name")
        or "Lead"
    ).strip()
    profile_photo_url = f"https://ui-avatars.com/api/?name={display_name.replace(' ', '+')}&background=0D6EFD&color=ffffff&size=128&bold=true"

    return jsonify(
        {
            "lead": _json_safe(lead),
            "related_email_count": len(related_email_docs),
            "related_emails": _json_safe(related_email_docs[:20]),
            "recipients": recipients,
            "participants": participants,
            "sender_conversations": _json_safe(conversations[:40]),
            "sender_contacted_emails": sender_contacts,
            "sender_contact_history": _json_safe(sender_contact_docs[:50]),
            "profile_photo_url": profile_photo_url,
            "linkedin_urls": _json_safe(lead.get("linkedin_urls") or []),
        }
    )


@app.get("/api/message-logs")
def message_logs() -> Any:
    limit = min(max(int(request.args.get("limit", 30)), 1), 200)
    docs = list(message_log_collection.find().sort("created_at", DESCENDING).limit(limit))
    return jsonify({"count": len(docs), "rows": _json_safe(docs)})


@app.get("/api/notifications")
def notifications() -> Any:
    mode = (request.args.get("mode") or "unread").strip().lower()
    limit = min(max(int(request.args.get("limit", 100)), 1), 500)

    if mode == "all":
        docs = get_all_notifications(limit=limit)
    else:
        docs = get_unread_notifications(limit=limit)

    return jsonify(
        {
            "mode": mode,
            "unread_count": int(get_unread_count()),
            "rows": _json_safe(docs),
        }
    )


@app.post("/api/notifications/read/<notification_id>")
def notification_read(notification_id: str) -> Any:
    ok = mark_as_read(notification_id)
    return jsonify({"ok": bool(ok), "unread_count": int(get_unread_count())})


@app.post("/api/notifications/read-all")
def notifications_read_all() -> Any:
    count = int(mark_all_as_read())
    return jsonify({"ok": True, "marked": count, "unread_count": int(get_unread_count())})


@app.delete("/api/notifications/<notification_id>")
def notification_delete(notification_id: str) -> Any:
    ok = delete_notification(notification_id)
    return jsonify({"ok": bool(ok), "unread_count": int(get_unread_count())})


@app.post("/api/notifications/test")
def notification_test() -> Any:
    notification_id = create_notification(
        notification_type="inactivity",
        lead_name="Website Test",
        lead_email=CC_EMAIL or "test@example.com",
        message="Test notification from website module.",
        additional_data={"source": "website", "event_type": "test_notification"},
    )
    return jsonify({"ok": True, "id": notification_id, "unread_count": int(get_unread_count())})


@app.post("/api/cycles/two-way")
def run_two_way_cycle() -> Any:
    payload = request.get_json(silent=True) or {}
    max_messages_per_side = int(payload.get("max_messages_per_side", 5))
    result = run_two_way_draft_cycle(max_messages_per_side=max_messages_per_side)
    return jsonify(_json_safe(result))


@app.post("/api/sender/plan")
def sender_plan() -> Any:
    payload = request.get_json(silent=True) or {}
    lead_id = str(payload.get("lead_id") or "").strip()
    participant_email = str(payload.get("participant_email") or "").strip().lower()
    customer_text = str(payload.get("customer_text") or "").strip()

    if not lead_id or not participant_email or not customer_text:
        return jsonify({"error": "lead_id, participant_email and customer_text are required."}), 400

    try:
        lead = _find_lead_or_404(lead_id)
    except ValueError:
        return jsonify({"error": "Invalid lead id"}), 400
    except LookupError:
        return jsonify({"error": "Lead not found"}), 404

    plan = sender_reply_plan(lead, participant_email, customer_text)
    return jsonify({"ok": True, "plan": _json_safe(plan)})


@app.post("/api/sender/apply-plan")
def sender_apply_plan() -> Any:
    payload = request.get_json(silent=True) or {}
    lead_id = str(payload.get("lead_id") or "").strip()
    participant_email = str(payload.get("participant_email") or "").strip().lower()
    thread_id = str(payload.get("thread_id") or "").strip()
    plan = payload.get("plan") or {}

    if not lead_id or not participant_email or not thread_id or not isinstance(plan, dict):
        return jsonify({"error": "lead_id, participant_email, thread_id and plan are required."}), 400

    try:
        lead = _find_lead_or_404(lead_id)
    except ValueError:
        return jsonify({"error": "Invalid lead id"}), 400
    except LookupError:
        return jsonify({"error": "Lead not found"}), 404

    try:
        subject, body = apply_sender_plan(lead, participant_email, thread_id, plan)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "subject": subject, "body": body})


@app.post("/api/sender/outreach")
def sender_outreach() -> Any:
    payload = request.get_json(silent=True) or {}
    lead_id = str(payload.get("lead_id") or "").strip()
    to_email = str(payload.get("to_email") or "").strip().lower()
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or "").strip()

    if not lead_id or not to_email or not subject or not body:
        return jsonify({"error": "lead_id, to_email, subject and body are required."}), 400

    try:
        lead = _find_lead_or_404(lead_id)
    except ValueError:
        return jsonify({"error": "Invalid lead id"}), 400
    except LookupError:
        return jsonify({"error": "Lead not found"}), 404

    try:
        normalized_body = ensure_sender_signature(body, str(lead.get("product_description") or ""))
        result = send_new_lead_message(
            product_description=str(lead.get("product_description") or ""),
            company_name=str(lead.get("company_name") or ""),
            website=str(lead.get("website") or ""),
            to_email=to_email,
            subject=subject,
            body=normalized_body,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "result": _json_safe(result)})


@app.post("/api/customer/template")
def customer_template() -> Any:
    payload = request.get_json(silent=True) or {}
    intent = str(payload.get("intent") or "need_clarity").strip()
    sender_subject = str(payload.get("sender_subject") or "Quick follow-up").strip()
    sender_body = str(payload.get("sender_body") or "").strip()

    choices, default_choice = customer_flow_choices(sender_subject, sender_body)
    subject, body = customer_reply_template(intent, sender_subject, sender_body)

    return jsonify(
        {
            "ok": True,
            "choices": choices,
            "default_choice": default_choice,
            "subject": subject,
            "body": body,
        }
    )


@app.post("/api/customer/scheduling-intent")
def customer_scheduling_intent() -> Any:
    payload = request.get_json(silent=True) or {}
    sender_subject = str(payload.get("sender_subject") or "").strip()
    sender_body = str(payload.get("sender_body") or "").strip()
    text = f"{sender_subject}\n{sender_body}".strip()
    has_scheduling_intent = bool(contains_scheduling_intent(text))
    return jsonify({"ok": True, "has_scheduling_intent": has_scheduling_intent})


@app.get("/api/calendar/slots")
def calendar_slots() -> Any:
    lookahead_days = min(max(int(request.args.get("lookahead_days", 7)), 1), 30)
    try:
        slot_text = calendar_slots_text_for_days(lookahead_days=lookahead_days)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "lookahead_days": lookahead_days, "slot_text": slot_text})


@app.post("/api/customer/draft")
def customer_draft() -> Any:
    payload = request.get_json(silent=True) or {}
    product_description = str(payload.get("product_description") or "").strip()
    company_name = str(payload.get("company_name") or "").strip()
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or "").strip()
    thread_id = str(payload.get("thread_id") or "").strip()
    sender_addr = str(payload.get("sender_addr") or sender_loop_address()).strip().lower()
    customer_addr = str(payload.get("customer_addr") or customer_loop_address()).strip().lower()

    if not subject or not body or not thread_id:
        return jsonify({"error": "subject, body and thread_id are required."}), 400

    try:
        result = draft_customer_mailbox_reply(
            product_description=product_description,
            company_name=company_name,
            subject=subject,
            body=body,
            thread_id=thread_id,
            sender_addr=sender_addr,
            customer_addr=customer_addr,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "result": result})


@app.get("/api/customer/context")
def customer_context() -> Any:
    try:
        context = customer_message_context()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "context": _json_safe(context)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5055, debug=True)
