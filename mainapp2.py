import importlib.util
import html
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from pymongo import DESCENDING


def _bootstrap_app_package() -> None:
    pkg_dir = Path(__file__).parent / "app"

    if "app" in sys.modules and getattr(sys.modules["app"], "__file__", "") == __file__:
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
load_dotenv()

from app.conversation_flow.flow import authorize_customer_loop_account
from app.conversation_flow.google_tools import customer_gmail_service, list_messages_from, profile_email, sender_gmail_service
from app.mainapp2_support.calendar_sync import sync_lead_meeting_from_calendar
from app.mainapp2_tabs import (
    render_customer_reply_tab,
    render_lead_overview_tab,
    render_replica_sender_tab,
    render_sender_outreach_tab,
    render_sender_replies_tab,
)
from app.mainapp2_support import (
    EMAILS_COLLECTION_NAME,
    LEADS_COLLECTION_NAME,
    MESSAGE_LOG_COLLECTION_NAME,
    NOTIFICATIONS_COLLECTION_NAME,
    collect_recipients,
    configure_messaging,
    configure_sms_reminders,
    configure_notifications,
    configure_sender_flow,
    customer_loop_address,
    database,
    delivery_mode,
    ensure_sender_booking_access,
    format_list,
    get_sms_preferences,
    init_db2,
    missing_sender_booking_scopes,
    process_due_sms_reminders,
    send_test_sms_now,
    sender_loop_address,
    set_sms_preferences,
    twilio_status,
)
from app.services.email_service import ensure_gmail_oauth


APP_TITLE = os.getenv("STREAMLIT_APP_TITLE", "AI Sales Agent Dashboard") + " 2"
FROM_EMAIL = (os.getenv("GMAIL_DRAFT_FROM_EMAIL") or os.getenv("SENDGRID_FROM_EMAIL") or "").strip()
CC_EMAIL = (os.getenv("AUTO_REPLY_CUSTOMER_EMAIL") or "").strip()


def _global_unread_mail_counts() -> dict[str, int]:
    sender_addr = sender_loop_address()
    customer_addr = customer_loop_address()

    counts = {
        "customer_unread": 0,
        "sender_unread": 0,
    }

    if sender_addr and customer_addr:
        customer_unread_messages: list[dict] = []
        sender_unread_messages: list[dict] = []
        try:
            customer_service = customer_gmail_service()
            sender_candidates = {sender_addr}
            try:
                sender_profile = profile_email(sender_gmail_service())
                if sender_profile:
                    sender_candidates.add(sender_profile)
            except Exception:
                pass

            customer_seen_ids: set[str] = set()
            for candidate in sender_candidates:
                for item in list_messages_from(customer_service, from_email=candidate, limit=25, unread_only=True):
                    item_id = str(item.get("id") or "")
                    if item_id and item_id not in customer_seen_ids:
                        customer_seen_ids.add(item_id)
                        customer_unread_messages.append(item)
            counts["customer_unread"] = len(customer_unread_messages)
        except Exception:
            counts["customer_unread"] = 0

        try:
            sender_service = sender_gmail_service()
            customer_candidates = {customer_addr}
            try:
                customer_profile = profile_email(customer_gmail_service())
                if customer_profile:
                    customer_candidates.add(customer_profile)
            except Exception:
                pass

            sender_seen_ids: set[str] = set()
            for candidate in customer_candidates:
                for item in list_messages_from(sender_service, from_email=candidate, limit=25, unread_only=True):
                    item_id = str(item.get("id") or "")
                    if item_id and item_id not in sender_seen_ids:
                        sender_seen_ids.add(item_id)
                        sender_unread_messages.append(item)
            counts["sender_unread"] = len(sender_unread_messages)
        except Exception:
            counts["sender_unread"] = 0

        # Keep badges turn-based: only the side with the newest unread message is shown.
        if counts["customer_unread"] > 0 and counts["sender_unread"] > 0:
            newest_customer = max(int(item.get("internal_ts", 0) or 0) for item in customer_unread_messages)
            newest_sender = max(int(item.get("internal_ts", 0) or 0) for item in sender_unread_messages)
            if newest_customer >= newest_sender:
                counts["sender_unread"] = 0
            else:
                counts["customer_unread"] = 0

    return counts


def _show_global_mail_notifications(unread_counts: dict[str, int]) -> None:
    customer_unread = int(unread_counts.get("customer_unread", 0) or 0)
    sender_unread = int(unread_counts.get("sender_unread", 0) or 0)

    prev_customer = int(st.session_state.get("mainapp2_prev_customer_unread", 0) or 0)
    prev_sender = int(st.session_state.get("mainapp2_prev_sender_unread", 0) or 0)

    if customer_unread > 0:
        st.warning(f"Customer Reply: You have {customer_unread} unread sender email(s) in the customer mailbox.")
        if customer_unread > prev_customer:
            st.toast(f"Customer mailbox: {customer_unread} unread email(s)", icon="📧")

    if sender_unread > 0:
        st.info(f"Sender Replies: You have {sender_unread} unread customer reply email(s) in sender mailbox.")
        if sender_unread > prev_sender:
            st.toast(f"Sender mailbox: {sender_unread} unread reply email(s)", icon="📩")

    if customer_unread == 0 and sender_unread == 0:
        st.success("No unread cross-mail notifications.")

    st.session_state["mainapp2_prev_customer_unread"] = customer_unread
    st.session_state["mainapp2_prev_sender_unread"] = sender_unread


def _enable_auto_refresh(enabled: bool, interval_ms: int = 5000) -> None:
    if not enabled:
        return
    try:
        st.autorefresh(interval=max(2000, int(interval_ms)), key="mainapp2_live_mail_refresh")
    except Exception:
        # Fallback for Streamlit builds that do not expose autorefresh.
        pass


init_db2()

db = database()
new_leads_collection = db[LEADS_COLLECTION_NAME]
new_emails_collection = db[EMAILS_COLLECTION_NAME]
message_log_collection = db[MESSAGE_LOG_COLLECTION_NAME]
notifications_collection = db[NOTIFICATIONS_COLLECTION_NAME]

configure_messaging(message_log_collection)
configure_sender_flow(new_leads_collection, message_log_collection)
configure_notifications(notifications_collection)
configure_sms_reminders(notifications_collection)

if "mainapp2_sms_phone" not in st.session_state:
    st.session_state["mainapp2_sms_phone"] = os.getenv("TWILIO_TO_NUMBER", "")
if "mainapp2_sms_on_booking" not in st.session_state:
    st.session_state["mainapp2_sms_on_booking"] = True
if "mainapp2_sms_followups_enabled" not in st.session_state:
    st.session_state["mainapp2_sms_followups_enabled"] = True
if "mainapp2_live_refresh_enabled" not in st.session_state:
    st.session_state["mainapp2_live_refresh_enabled"] = True

set_sms_preferences(
    phone=st.session_state.get("mainapp2_sms_phone", ""),
    sms_on_booking=bool(st.session_state.get("mainapp2_sms_on_booking", True)),
    sms_followups_enabled=bool(st.session_state.get("mainapp2_sms_followups_enabled", True)),
)
sms_runtime = process_due_sms_reminders(limit=20)

product_options = sorted(filter(None, new_leads_collection.distinct("product_description")))

st.set_page_config(page_title=APP_TITLE, page_icon="📬", layout="wide")
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.2rem;
        padding-bottom: 1.4rem;
    }
    .mainapp2-section {
        padding: 1rem 1.1rem;
        border: 1px solid rgba(49, 51, 63, 0.14);
        border-radius: 16px;
        background: linear-gradient(180deg, rgba(248, 250, 252, 0.96), rgba(255, 255, 255, 0.98));
        box-shadow: none !important;
        margin-bottom: 0.9rem;
    }
    .mainapp2-kicker {
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #5b6472;
        margin-bottom: 0.3rem;
    }
    .mainapp2-note {
        font-size: 0.94rem;
        color: #334155;
        margin-bottom: 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title(APP_TITLE)
st.caption("Dedicated lead workspace for lead_gen3 exports stored in MongoDB new_leads and new_emails")
if st.session_state.get("mainapp2_notice"):
    st.success(str(st.session_state.pop("mainapp2_notice")))

_enable_auto_refresh(bool(st.session_state.get("mainapp2_live_refresh_enabled", True)), interval_ms=5000)

global_unread_counts = _global_unread_mail_counts()
_show_global_mail_notifications(global_unread_counts)

with st.sidebar:
    st.subheader("Sender")
    st.write(f"From: {FROM_EMAIL or 'Not configured'}")
    st.write(f"CC: {CC_EMAIL or 'Not configured'}")
    st.write(f"Mode: {delivery_mode()}")
    if delivery_mode() == "gmail_draft":
        if st.button("Authorize Gmail Draft Access", key="mainapp2_auth_gmail"):
            try:
                result = ensure_gmail_oauth()
                st.success(f"Gmail OAuth ready. Token saved at {result['token_path']}")
            except Exception as exc:
                st.error(str(exc))

    st.divider()
    st.subheader("Reply Accounts")
    st.write(f"Customer mailbox: {customer_loop_address() or 'Not configured'}")
    if st.button("Authorize Sender Inbox + Calendar", key="mainapp2_auth_sender_loop"):
        try:
            result = ensure_sender_booking_access(interactive=True)
            st.success(f"Sender loop ready for {result['authorized_email']}")
        except Exception as exc:
            st.error(str(exc))
    if st.button("Authorize Customer Reply Mailbox", key="mainapp2_auth_customer_loop"):
        try:
            result = authorize_customer_loop_account()
            st.success(f"Customer mailbox ready for {result['authorized_email']}")
        except Exception as exc:
            st.error(str(exc))

    st.divider()
    st.subheader("Live Updates")
    st.checkbox(
        "Auto-refresh unread mail badges",
        key="mainapp2_live_refresh_enabled",
        help="Refreshes every few seconds so sender/customer unread indicators update without manual page refresh.",
    )

    st.divider()
    st.subheader("SMS Reminders")
    st.checkbox(
        "Send SMS when meeting is booked",
        key="mainapp2_sms_on_booking",
        help="Sends an SMS immediately after a meeting is booked.",
    )
    st.checkbox(
        "Enable reminder schedule (2 days before + meeting morning)",
        key="mainapp2_sms_followups_enabled",
        help="Adds reminder SMS before the meeting date.",
    )
    st.text_input(
        "Reminder phone number",
        key="mainapp2_sms_phone",
        help="Use E.164 format if possible, e.g. +91XXXXXXXXXX.",
    )
    set_sms_preferences(
        phone=st.session_state.get("mainapp2_sms_phone", ""),
        sms_on_booking=bool(st.session_state.get("mainapp2_sms_on_booking", True)),
        sms_followups_enabled=bool(st.session_state.get("mainapp2_sms_followups_enabled", True)),
    )
    sms_prefs = get_sms_preferences()
    sms_provider = twilio_status()
    if sms_prefs.get("sms_on_booking"):
        st.success(f"SMS reminders enabled for {sms_prefs.get('phone')}")
    else:
        st.info("SMS reminders are disabled.")

    if sms_provider.get("ready"):
        if sms_provider.get("messaging_sid"):
            st.caption(f"Twilio ready. Messaging service: {sms_provider.get('messaging_sid')}")
        else:
            st.caption(f"Twilio ready. From number: {sms_provider.get('from_phone')}")
    else:
        st.warning(f"Twilio is not configured. Missing: {', '.join(list(sms_provider.get('missing') or []))}")

    if st.button("Send test SMS now", key="mainapp2_sms_test_now"):
        test_message = "Test SMS from AI Sales Agent. If you received this, SMS delivery is working."
        result = send_test_sms_now(
            to_phone=str(sms_prefs.get("phone") or ""),
            message=test_message,
            lead_name="SMS Test",
            lead_email=CC_EMAIL or "",
        )
        if bool(result.get("ok")):
            st.success("Test SMS sent successfully.")
        else:
            st.error(f"Test SMS failed: {result.get('error')}")

    if sms_runtime.get("sent", 0) > 0:
        st.caption(f"Sent {sms_runtime.get('sent', 0)} scheduled SMS reminder(s) in this run.")
    if sms_runtime.get("failed", 0) > 0:
        st.caption(f"{sms_runtime.get('failed', 0)} SMS reminder(s) failed. Check Twilio credentials.")
    if sms_runtime.get("pending", 0) > 0:
        st.caption(f"{sms_runtime.get('pending', 0)} SMS reminder(s) are queued and waiting for send time.")

    st.divider()
    selected_product = st.selectbox(
        "Product Description",
        options=product_options or [""],
        index=0 if product_options else None,
        placeholder="Choose a product",
    )
    priority_filter = st.multiselect(
        "Priority",
        options=["HIGH", "MEDIUM", "LOW"],
        default=["HIGH", "MEDIUM", "LOW"],
    )
    min_score = st.slider("Minimum Score", 0, 100, 0)
    company_search = st.text_input("Search company")

lead_query: dict = {"total_score": {"$gte": min_score}}
if selected_product:
    lead_query["product_description"] = selected_product
if priority_filter:
    lead_query["priority"] = {"$in": priority_filter}
if company_search.strip():
    lead_query["company_name"] = {"$regex": company_search.strip(), "$options": "i"}

lead_docs = list(new_leads_collection.find(lead_query).sort("imported_at", DESCENDING))

lead_rows = [
    {
        "company_name": doc.get("company_name", ""),
        "product_description": doc.get("product_description", ""),
        "industry": doc.get("industry", ""),
        "priority": doc.get("priority", ""),
        "total_score": doc.get("total_score", 0),
        "website": doc.get("website", ""),
        "source_file": doc.get("source_file", ""),
    }
    for doc in lead_docs
]

metric1, metric2, metric3 = st.columns(3)
metric1.metric("Leads", len(lead_docs))
metric2.metric("Products", len(product_options))
metric3.metric(
    "Email Contacts",
    new_emails_collection.count_documents({"product_description": selected_product}) if selected_product else new_emails_collection.count_documents({}),
)

st.subheader("Lead List")
if lead_rows:
    st.dataframe(pd.DataFrame(lead_rows), use_container_width=True, height=280)
else:
    st.info("No leads found for the current filters.")

lead_options = {str(doc["_id"]): f"{doc.get('company_name', 'Unknown')} | {doc.get('priority', '')} | {doc.get('total_score', 0)}" for doc in lead_docs}
selected_lead_id = st.selectbox(
    "Choose lead",
    options=list(lead_options.keys()),
    format_func=lambda value: lead_options.get(value, value),
) if lead_options else None

if selected_lead_id:
    selected_lead = next(doc for doc in lead_docs if str(doc["_id"]) == selected_lead_id)
    selected_lead = sync_lead_meeting_from_calendar(new_leads_collection, selected_lead)
    related_email_docs = list(
        new_emails_collection.find(
            {
                "product_description": selected_lead.get("product_description", ""),
                "company": selected_lead.get("company_name", ""),
            }
        ).sort("imported_at", DESCENDING)
    )
    recipients = collect_recipients(selected_lead, related_email_docs)

    lead_company = html.escape(str(selected_lead.get("company_name", "Unknown") or "Unknown"))
    lead_product_raw = str(selected_lead.get("product_description", "No product") or "No product")
    lead_product = html.escape(lead_product_raw)
    lead_priority = html.escape(str(selected_lead.get("priority", "N/A") or "N/A"))
    lead_score = html.escape(str(selected_lead.get("total_score", 0)))
    lead_industry = html.escape(str(selected_lead.get("industry", "Unknown industry") or "Unknown industry"))
    lead_description = html.escape(str(selected_lead.get("description", "") or "No additional company description available."))
    lead_recommended_action = html.escape(str(selected_lead.get("recommended_action", "No specific action recommendation available yet.") or "No specific action recommendation available yet."))

    lead_signals = selected_lead.get("signals_found") or []
    if isinstance(lead_signals, list):
        lead_need_hint = ", ".join(str(item).strip() for item in lead_signals if str(item).strip())
    else:
        lead_need_hint = str(lead_signals).strip()
    lead_need_hint = html.escape(lead_need_hint or "No explicit pain signals captured yet.")

    product_key = lead_product_raw.strip().lower()
    if "digiexpense" in product_key:
        product_fit = (
            "DigiExpense can help by digitizing expense capture, automating approvals, enforcing policy compliance, "
            "and giving finance and sales leaders real-time spend visibility with faster reimbursement cycles."
        )
    elif "digicom" in product_key:
        product_fit = (
            "DigiCom can help by unifying customer messaging across channels, improving campaign consistency, "
            "and enabling faster, compliant customer engagement journeys."
        )
    else:
        product_fit = (
            f"{lead_product_raw} can help by reducing manual work, improving process consistency, and giving teams better operational visibility."
        )
    product_fit = html.escape(product_fit)

    st.markdown("---")
    st.markdown(
        f"""
        <div class="mainapp2-section">
            <div class="mainapp2-kicker">Selected Lead</div>
            <p class="mainapp2-note"><strong>{lead_company}</strong> is in the <strong>{lead_industry}</strong> space. Based on the available context ({lead_description}) and observed intent signals ({lead_need_hint}), <strong>{lead_product}</strong> is a strong fit for their operational needs. {product_fit} This lead is currently marked <strong>{lead_priority}</strong> with score <strong>{lead_score}</strong>, so the recommended next move is <strong>{lead_recommended_action}</strong>.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    summary_1, summary_2, summary_3, summary_4 = st.columns(4)
    summary_1.metric("Priority", selected_lead.get("priority", "—") or "—")
    summary_2.metric("Score", selected_lead.get("total_score", 0))
    summary_3.metric("Known Recipients", len(recipients))
    summary_4.metric("Stored Templates", len(related_email_docs))

    if missing_sender_booking_scopes():
        st.warning("Sender calendar write access is not fully authorized yet. Use 'Authorize Sender Inbox + Calendar' in the sidebar before booking or rescheduling meetings.")

    sender_unread = int(global_unread_counts.get("sender_unread", 0) or 0)
    customer_unread = int(global_unread_counts.get("customer_unread", 0) or 0)
    sender_tab_label = f"Sender Replies ({sender_unread})" if sender_unread > 0 else "Sender Replies"
    customer_tab_label = f"Customer Reply ({customer_unread})" if customer_unread > 0 else "Customer Reply"

    overview_tab, sender_tab, sender_replies_tab, customer_tab, replica_sender_tab = st.tabs(
        ["Lead Overview", "Sender Outreach", sender_tab_label, customer_tab_label, "REPLICA of Sender Replies"]
    )

    with overview_tab:
        render_lead_overview_tab(selected_lead, format_list)

    with sender_tab:
        render_sender_outreach_tab(
            selected_lead_id=selected_lead_id,
            selected_lead=selected_lead,
            related_email_docs=related_email_docs,
            recipients=recipients,
        )

    with sender_replies_tab:
        render_sender_replies_tab(
            selected_lead_id=selected_lead_id,
            selected_lead=selected_lead,
            recipients=recipients,
        )

    with customer_tab:
        render_customer_reply_tab(selected_lead_id=selected_lead_id, selected_lead=selected_lead)

    with replica_sender_tab:
        render_replica_sender_tab(
            selected_lead_id=selected_lead_id,
            selected_lead=selected_lead,
            recipients=recipients,
        )

st.markdown("---")

st.subheader("New App Message Log")
log_docs = list(message_log_collection.find().sort("created_at", DESCENDING).limit(30))
if log_docs:
    log_rows = [
        {
            "time": doc.get("created_at"),
            "product": doc.get("product_description", ""),
            "company": doc.get("company_name", ""),
            "to": doc.get("to", ""),
            "subject": doc.get("subject", ""),
            "status": doc.get("status", ""),
            "delivery_mode": doc.get("delivery_mode", ""),
            "error": doc.get("error", ""),
        }
        for doc in log_docs
    ]
    st.dataframe(pd.DataFrame(log_rows), use_container_width=True, height=260)
else:
    st.info("No messages sent from mainapp2 yet.")
