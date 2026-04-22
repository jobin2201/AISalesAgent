import html
import re
from datetime import timedelta, timezone

import streamlit as st

from app.conversation_flow.calendar_tools import calendar_slots_text_for_days
from app.conversation_flow.google_tools import safe_reply_subject
from app.mainapp2_support import (
    LEADS_COLLECTION_NAME,
    MESSAGE_LOG_COLLECTION_NAME,
    apply_sender_plan,
    database,
    ensure_sender_signature,
    format_list,
    latest_sender_log,
    lead_participants,
    list_sender_conversations,
    customer_loop_address,
    sender_loop_address,
    sender_reply_plan,
)


def _normalize_sender_body(body: str, product_description: str) -> str:
    text = (body or "").rstrip()
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n*Best,\s*\nAI Sales Team\s*", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return ensure_sender_signature(text, product_description)


def _format_ist_label(dt_value) -> str:
    if not dt_value:
        return ""

    ist_tz = timezone(timedelta(hours=5, minutes=30))
    try:
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return dt_value.astimezone(ist_tz).strftime("%d %b %Y %I:%M %p IST")
    except Exception:
        return str(dt_value)


def _extract_client_name(subject: str, body: str, fallback_company: str) -> str:
    subj = (subject or "").strip()
    body_text = (body or "").strip()

    # Prefer explicit meeting/company marker like: "Intro call - Emburse"
    match = re.search(r"intro\s+call\s*-\s*([^@\n\r\(]+)", subj, flags=re.IGNORECASE)
    if match:
        candidate = (match.group(1) or "").strip(" -")
        if candidate:
            return candidate

    # Fallback to selected lead company if available.
    company = (fallback_company or "").strip()
    if company:
        return company

    # Last resort: try first meaningful title chunk from body.
    for line in body_text.splitlines():
        line = line.strip()
        if len(line) >= 3 and "@" not in line:
            return line[:60]
    return "Unknown Client"


@st.cache_data(ttl=45, show_spinner=False)
def _load_sender_conversations_cached(participants_key: tuple[str, ...], refresh_nonce: int) -> list[dict]:
    _ = refresh_nonce
    return list_sender_conversations(list(participants_key))


@st.cache_data(ttl=45, show_spinner=False)
def _load_previous_sender_messages_cached(
    participant_email: str,
    sender_email: str,
    client_name: str,
    selected_company_name: str,
    refresh_nonce: int,
) -> list[dict]:
    _ = refresh_nonce
    if not participant_email or not sender_email:
        return []

    db = database()
    col = db[MESSAGE_LOG_COLLECTION_NAME]
    cursor = (
        col.find(
            {
                "to": participant_email,
                "from_email": sender_email,
            }
        )
        .sort("created_at", -1)
        .limit(60)
    )

    client_key = (client_name or "").strip().lower()
    selected_company_key = (selected_company_name or "").strip().lower()

    matched: list[dict] = []
    fallback: list[dict] = []

    for doc in cursor:
        item = {
            "subject": str(doc.get("subject") or "(no subject)"),
            "body": str(doc.get("body") or ""),
            "created_at": doc.get("created_at"),
            "status": str(doc.get("status") or ""),
            "delivery_mode": str(doc.get("delivery_mode") or ""),
            "product_description": str(doc.get("product_description") or ""),
            "company_name": str(doc.get("company_name") or ""),
        }

        subject_l = item["subject"].lower()
        body_l = item["body"].lower()
        company_l = item["company_name"].strip().lower()

        is_client_match = False
        if client_key:
            if client_key in subject_l or client_key in body_l or company_l == client_key:
                is_client_match = True
        elif selected_company_key and company_l == selected_company_key:
            is_client_match = True

        if is_client_match:
            matched.append(item)
        else:
            fallback.append(item)

    # Keep newest first by created_at (None-safe fallback to string compare).
    def _sort_key(x: dict):
        return x.get("created_at") or ""

    matched = sorted(matched, key=_sort_key, reverse=True)
    fallback = sorted(fallback, key=_sort_key, reverse=True)

    if matched:
        return matched[:15]
    return fallback[:15]


@st.cache_data(ttl=180, show_spinner=False)
def _load_company_product_map_cached() -> dict[str, str]:
    db = database()
    col = db[LEADS_COLLECTION_NAME]
    out: dict[str, str] = {}
    for doc in col.find({}, {"company_name": 1, "product_description": 1}):
        company = str(doc.get("company_name") or "").strip().lower()
        product = str(doc.get("product_description") or "").strip()
        if company and product and company not in out:
            out[company] = product
    return out


def _resolve_product_for_client(
    explicit_product: str,
    explicit_company: str,
    client_name: str,
    company_product_map: dict[str, str],
    fallback_product: str,
) -> str:
    if explicit_product.strip():
        return explicit_product.strip()
    company_key = (explicit_company or client_name or "").strip().lower()
    if company_key and company_key in company_product_map:
        return company_product_map[company_key]
    return fallback_product.strip() or "Unknown Product"


def render_replica_sender_tab(selected_lead_id: str, selected_lead: dict, recipients: list[str]) -> None:
    st.subheader("REPLICA SENDER")
    st.caption("Gmail-style sender inbox view with a clean two-pane layout: inbox on the left, opened message and reply composer on the right.")

    st.markdown(
        """
        <style>
        @keyframes replicaFadeIn {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .replica-hero {
            background: linear-gradient(120deg, #f6faff 0%, #ffffff 58%, #fbfdff 100%);
            border: 1px solid #dce6f4;
            border-radius: 14px;
            padding: 12px 14px;
            margin-bottom: 10px;
            animation: replicaFadeIn 260ms ease-out;
        }
        .replica-hero-title {
            font-size: 1rem;
            font-weight: 800;
            color: #1f3556;
            margin-bottom: 4px;
        }
        .replica-hero-sub {
            font-size: 0.86rem;
            color: #4c6488;
            font-weight: 600;
        }
        .replica-chip {
            display: inline-block;
            background: #f4f8ff;
            color: #345076;
            border: 1px solid #d6e3f7;
            border-radius: 999px;
            font-size: 0.74rem;
            padding: 3px 9px;
            margin-right: 6px;
            margin-top: 8px;
        }
        .replica-toolbar-card {
            border: 1px solid #dce6f4;
            border-radius: 12px;
            background: #fff;
            padding: 10px 12px;
            margin-bottom: 10px;
            animation: replicaFadeIn 280ms ease-out;
        }
        .replica-toolbar-label {
            color: #2d4a73;
            font-size: 0.88rem;
            font-weight: 700;
        }
        .replica-pane {
            border: 1px solid #dbe4f2;
            border-radius: 14px;
            background: linear-gradient(180deg, #fcfeff 0%, #f7fbff 100%);
            padding: 14px;
            animation: replicaFadeIn 300ms ease-out;
            transition: box-shadow 180ms ease, border-color 180ms ease;
        }
        .replica-pane:hover {
            box-shadow: 0 10px 24px rgba(48, 83, 142, 0.08);
            border-color: #ceddf3;
        }
        .replica-left-head,
        .replica-right-head {
            color: #223a61;
            font-size: 0.95rem;
            font-weight: 800;
            margin-bottom: 10px;
            background: #eff4fc;
            border: 1px solid #d1dff2;
            border-radius: 9px;
            padding: 10px 12px;
        }
        .replica-mail-card {
            border: 1px solid #e2e9f5;
            border-radius: 10px;
            padding: 11px 12px;
            background: #ffffff;
            transition: transform 180ms ease, box-shadow 180ms ease, opacity 180ms ease, filter 180ms ease, border-color 180ms ease;
            animation: replicaFadeIn 220ms ease-out;
        }
        .replica-mail-card.inactive {
            opacity: 1;
            filter: none;
        }
        .replica-mail-card.active {
            border-color: #7ea7e8;
            background: #edf3ff;
            box-shadow: 0 4px 14px rgba(36, 78, 142, 0.14);
        }
        .replica-mail-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 22px rgba(31, 42, 68, 0.1);
        }
        .replica-from {
            color: #203a61;
            font-weight: 800;
            font-size: 0.84rem;
            margin-bottom: 2px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .replica-subject {
            color: #33507f;
            font-weight: 700;
            font-size: 0.82rem;
            margin-bottom: 2px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .replica-snippet {
            color: #5a6f8f;
            font-size: 0.78rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .replica-message-head {
            border: 1px solid #dbe6f6;
            border-radius: 10px;
            padding: 12px 13px;
            background: #f4f8ff;
            margin-bottom: 12px;
            animation: replicaFadeIn 240ms ease-out;
        }
        .replica-message-subject {
            color: #203a61;
            font-size: 1rem;
            font-weight: 800;
            margin-bottom: 6px;
            word-break: break-word;
        }
        .replica-message-meta {
            color: #5a6f8f;
            font-size: 0.82rem;
            line-height: 1.45;
        }
        .replica-history-card {
            border: 1px solid #dfe8f6;
            border-radius: 10px;
            background: #fbfdff;
            padding: 8px 10px;
            margin-bottom: 8px;
            animation: replicaFadeIn 220ms ease-out;
        }
        .replica-history-head {
            color: #2b446c;
            font-size: 0.82rem;
            font-weight: 800;
            margin-bottom: 3px;
        }
        .replica-history-meta {
            color: #5d7392;
            font-size: 0.76rem;
            margin-bottom: 5px;
        }
        .replica-history-body {
            color: #536885;
            font-size: 0.79rem;
            line-height: 1.4;
            max-height: 62px;
            overflow: hidden;
        }
        .replica-section-spacer {
            margin-top: 6px;
            margin-bottom: 4px;
        }
        .replica-customer-preview {
            border: 1px dashed #cfdcf0;
            background: #f8fbff;
            color: #4b6691;
            border-radius: 9px;
            padding: 8px 10px;
            font-size: 0.79rem;
            margin-bottom: 8px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        body:has(.replica-pane-left:focus-within) .replica-mail-card.inactive,
        body:has(.replica-pane-right:focus-within) .replica-mail-card.inactive {
            opacity: 0.28;
            filter: saturate(0.55);
        }
        body:has(.replica-pane-left:focus-within) .replica-mail-card.active,
        body:has(.replica-pane-right:focus-within) .replica-mail-card.active {
            opacity: 1;
            filter: none;
        }
        .st-key-replica_sender_refresh_ button {
            min-height: 34px;
            border-radius: 8px;
        }
        @media (prefers-color-scheme: dark) {
            .replica-hero {
                background: linear-gradient(120deg, rgba(17, 34, 64, 0.95) 0%, rgba(17, 30, 56, 0.96) 58%, rgba(16, 33, 63, 0.95) 100%);
                border-color: rgba(126, 168, 234, 0.42);
            }
            .replica-hero-title,
            .replica-left-head,
            .replica-right-head,
            .replica-from,
            .replica-subject,
            .replica-message-subject,
            .replica-history-head {
                color: #eaf2ff;
            }
            .replica-hero-sub,
            .replica-toolbar-label,
            .replica-snippet,
            .replica-message-meta,
            .replica-history-meta,
            .replica-history-body {
                color: #bfd4f2;
            }
            .replica-chip,
            .replica-pill {
                background: rgba(35, 58, 100, 0.95);
                color: #d8e6ff;
                border-color: rgba(134, 173, 236, 0.45);
            }
            .replica-toolbar-card,
            .replica-pane,
            .replica-mail-card,
            .replica-message-head,
            .replica-history-card,
            .replica-left-head,
            .replica-right-head {
                background: rgba(20, 31, 54, 0.94);
                border-color: rgba(127, 165, 230, 0.34);
            }
            .replica-pane {
                background: linear-gradient(180deg, rgba(22, 34, 60, 0.95) 0%, rgba(19, 30, 53, 0.95) 100%);
            }
            .replica-left-head,
            .replica-right-head {
                color: #eef5ff;
                background: rgba(33, 48, 78, 0.97);
                border-color: rgba(137, 173, 233, 0.45);
            }
            .replica-mail-card.active {
                background: rgba(44, 68, 110, 0.96);
                border-color: rgba(139, 177, 238, 0.6);
                box-shadow: 0 4px 16px rgba(8, 16, 30, 0.45);
            }
            body:has(.replica-pane-left:focus-within) .replica-mail-card.inactive,
            body:has(.replica-pane-right:focus-within) .replica-mail-card.inactive {
                opacity: 0.26;
                filter: saturate(0.5);
            }
            .replica-customer-preview {
                border-color: rgba(132, 169, 232, 0.38);
                background: rgba(36, 53, 86, 0.7);
                color: #bfd4f2;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    participants = lead_participants(selected_lead, recipients)
    company_product_map = _load_company_product_map_cached()
    participants_key = tuple(sorted({str(item).strip().lower() for item in participants if str(item).strip()}))
    sender_context_error = ""
    refresh_nonce_key = f"replica_sender_refresh_nonce_{selected_lead_id}"
    if refresh_nonce_key not in st.session_state:
        st.session_state[refresh_nonce_key] = 0

    try:
        sender_conversations = _load_sender_conversations_cached(
            participants_key,
            int(st.session_state.get(refresh_nonce_key, 0)),
        )
    except Exception as exc:
        sender_conversations = []
        sender_context_error = str(exc)

    if sender_context_error:
        st.info(f"Sender reply context is not ready yet: {sender_context_error}")
        return

    if not sender_conversations:
        st.info("No customer replies were found yet for this selected lead.")
        return

    selected_key = f"replica_sender_selected_{selected_lead_id}"
    refresh_key = f"replica_sender_refresh_{selected_lead_id}"

    if selected_key not in st.session_state:
        st.session_state[selected_key] = 0

    st.markdown(
        f"""
        <style>
        @import url('https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css');
        .st-key-{refresh_key} button {{
            color: transparent !important;
            min-height: 34px;
            border-radius: 8px;
            position: relative;
        }}
        .st-key-{refresh_key} button::before {{
            content: "\\F116";
            font-family: 'bootstrap-icons' !important;
            font-size: 16px;
            color: #325d9d;
            position: absolute;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            line-height: 1;
        }}
        .st-key-{refresh_key} button:hover {{
            background-color: rgba(50, 93, 157, 0.12);
        }}
        .st-key-{refresh_key} button:focus {{
            outline: 2px solid rgba(50, 93, 157, 0.35);
        }}
        @media (prefers-color-scheme: dark) {{
            .st-key-{refresh_key} button::before {{ color: #9fc3ff; }}
            .st-key-{refresh_key} button:hover {{ background-color: rgba(126, 168, 234, 0.16); }}
            .st-key-{refresh_key} button:focus {{ outline: 2px solid rgba(126, 168, 234, 0.34); }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    toolbar_left, toolbar_right = st.columns([0.92, 0.08])
    with toolbar_left:
        st.markdown("", unsafe_allow_html=True)
    with toolbar_right:
        if st.button(" ", key=refresh_key, help="Refresh inbox", use_container_width=True):
            st.session_state[refresh_nonce_key] = int(st.session_state.get(refresh_nonce_key, 0)) + 1
            st.rerun()

    left_col, right_col = st.columns([0.36, 0.64], gap="medium")

    with left_col:
        st.markdown('<div class="replica-pane replica-pane-left">', unsafe_allow_html=True)
        st.markdown('<div class="replica-left-head">Inbox (Latest first)</div>', unsafe_allow_html=True)
        planned_selected = int(st.session_state.get(selected_key, 0))
        for idx, item in enumerate(sender_conversations):
            client_name = _extract_client_name(
                str(item.get("subject") or ""),
                str(item.get("body") or ""),
                str(selected_lead.get("company_name") or ""),
            )
            product_name = _resolve_product_for_client(
                explicit_product="",
                explicit_company="",
                client_name=client_name,
                company_product_map=company_product_map,
                fallback_product=str(selected_lead.get("product_description") or ""),
            )
            from_label = html.escape(f"{product_name} | {client_name}")
            email_label = html.escape(str(item.get("from_email") or "unknown"))
            subject_label = html.escape(str(item.get("subject") or "(no subject)"))
            snippet_label = html.escape(str(item.get("snippet") or item.get("body") or ""))
            when_label = html.escape(_format_ist_label(item.get("received_at")))

            row_left, row_right = st.columns([0.78, 0.22])
            with row_right:
                is_clicked = st.button("Open", key=f"replica_sender_open_{selected_lead_id}_{idx}", use_container_width=True)
                if is_clicked:
                    planned_selected = idx
                    st.session_state[selected_key] = idx

            active = idx == planned_selected
            cls = "replica-mail-card active" if active else "replica-mail-card inactive"
            with row_left:
                st.markdown(
                    f"""
                    <div class=\"{cls}\">
                        <div class=\"replica-from\">{from_label}</div>
                        <div class=\"replica-snippet\" style=\"font-weight:700;\">{email_label}</div>
                        <div class=\"replica-subject\">{subject_label}</div>
                        <div class=\"replica-snippet\">{snippet_label}</div>
                        <div class=\"replica-snippet\" style=\"margin-top:4px;\">{when_label}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        st.markdown("</div>", unsafe_allow_html=True)

    selected_index = int(st.session_state.get(selected_key, 0))
    if selected_index < 0 or selected_index >= len(sender_conversations):
        selected_index = 0

    selected_conversation = sender_conversations[selected_index]
    participant_email = str(selected_conversation.get("from_email") or "")
    thread_id = str(selected_conversation.get("thread_id") or "")
    inbound_subject = str(selected_conversation.get("subject") or "Quick follow-up")
    inbound_body = str(selected_conversation.get("body") or "")
    inbound_signature = f"{selected_conversation.get('message_id')}|{thread_id}|{hash(inbound_body)}"
    last_sender_log = latest_sender_log(selected_lead.get("company_name", ""), participant_email)
    sender_addr = sender_loop_address() or ""

    with right_col:
        st.markdown('<div class="replica-pane replica-pane-right">', unsafe_allow_html=True)
        st.markdown('<div class="replica-right-head">Latest Customer Email</div>', unsafe_allow_html=True)

        st.markdown(
            f"""
            <div class=\"replica-message-head\">
                <div class=\"replica-message-subject\">{html.escape(inbound_subject)}</div>
                <div class=\"replica-message-meta\">
                    From: {html.escape(participant_email or 'Unknown')}<br>
                    To: {html.escape(sender_loop_address() or 'Not configured')}<br>
                    Received: {html.escape(_format_ist_label(selected_conversation.get('received_at')) or 'Unknown')}<br>
                    Thread ID: {html.escape(thread_id or '—')}
                </div>
                <span class=\"replica-pill\">Lead: {html.escape(selected_lead.get('company_name', 'Unknown') or 'Unknown')}</span>
                <span class=\"replica-pill\">Meeting State: {html.escape(str(selected_lead.get('meeting_state', '—') or '—'))}</span>
                <span class=\"replica-pill\">Customer Mailbox: {html.escape(customer_loop_address() or 'Not configured')}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        preview_line = (inbound_body or "").strip().replace("\r\n", " ").replace("\n", " ")
        preview_line = (preview_line[:180] + "...") if len(preview_line) > 180 else (preview_line or "(No body found)")
        customer_dropdown_preview = (preview_line[:140] + "...") if len(preview_line) > 140 else preview_line
        with st.expander(f"Customer message | Latest response preview: {customer_dropdown_preview}", expanded=False):
            st.markdown(
                f"<div class=\"replica-customer-preview\">Latest response preview: {html.escape(preview_line)}</div>",
                unsafe_allow_html=True,
            )
            st.text_area(
                "Customer message",
                value=inbound_body or "(No body found)",
                height=250,
                disabled=True,
                key=f"replica_sender_inbound_{selected_lead_id}",
            )

        st.markdown('<div class="replica-section-spacer"></div>', unsafe_allow_html=True)

        with st.expander("Last sent sender draft", expanded=False):
            if last_sender_log:
                st.write(f"Subject: {last_sender_log.get('subject', '(no subject)')}")
                st.text_area(
                    "Previous sender draft",
                    value=last_sender_log.get("body", "") or "(No body found)",
                    height=180,
                    disabled=True,
                    key=f"replica_sender_previous_{selected_lead_id}",
                )
            else:
                st.info("No previous sender draft is logged yet for this participant.")

        st.markdown("### Previous Sender Messages")
        show_history_key = f"replica_sender_show_history_{selected_lead_id}"
        if show_history_key not in st.session_state:
            st.session_state[show_history_key] = True
        show_history = st.toggle("Show previous sender messages", key=show_history_key)

        previous_sender_messages: list[dict] = []
        if show_history:
            opened_client_name = _extract_client_name(
                inbound_subject,
                inbound_body,
                str(selected_lead.get("company_name") or ""),
            )

            previous_sender_messages = _load_previous_sender_messages_cached(
                participant_email,
                sender_addr,
                opened_client_name,
                str(selected_lead.get("company_name", "") or ""),
                int(st.session_state.get(refresh_nonce_key, 0)),
            )

        if show_history and previous_sender_messages:
            for msg in previous_sender_messages:
                sent_label = _format_ist_label(msg.get("created_at")) or "Unknown"
                status_label = html.escape(str(msg.get("status") or ""))
                mode_label = html.escape(str(msg.get("delivery_mode") or ""))
                subject_label = html.escape(str(msg.get("subject") or "(no subject)"))
                client_name = _extract_client_name(
                    str(msg.get("subject") or ""),
                    str(msg.get("body") or ""),
                    str(selected_lead.get("company_name") or ""),
                )
                resolved_product = _resolve_product_for_client(
                    explicit_product=str(msg.get("product_description") or ""),
                    explicit_company=str(msg.get("company_name") or ""),
                    client_name=client_name,
                    company_product_map=company_product_map,
                    fallback_product=str(selected_lead.get("product_description") or ""),
                )
                product_name = html.escape(resolved_product)
                client_label = html.escape(client_name)
                preview = str(msg.get("body") or "").strip().replace("\r\n", "\n")
                preview = preview[:220] + ("..." if len(preview) > 220 else "")
                st.markdown(
                    f"""
                    <div class="replica-history-card">
                        <div class="replica-history-head">{subject_label}</div>
                        <div class="replica-history-meta">Client: {product_name} | {client_label}</div>
                        <div class="replica-history-meta">Sent (latest first): {html.escape(sent_label)} | Status: {status_label or '—'} | Mode: {mode_label or '—'}</div>
                        <div class="replica-history-body">{html.escape(preview or '(No body found)')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        elif show_history:
            st.info("No previous sender messages found for this participant yet.")

        st.markdown("### Reply To This Email")
        sender_reply_mode = st.radio(
            "Reply style",
            options=["Manual reply", "NLP-guided reply"],
            horizontal=True,
            key=f"replica_sender_mode_{selected_lead_id}",
        )

        suggested_plan = {
            "kind": "manual",
            "action": "manual",
            "subject": safe_reply_subject(inbound_subject),
            "body": _normalize_sender_body(
                "Hi,\n\n\n\nBest,\nAI Sales Team",
                selected_lead.get("product_description", ""),
            ),
        }
        sender_subject_key = f"replica_sender_subject_{selected_lead_id}"
        sender_body_key = f"replica_sender_body_{selected_lead_id}"
        sender_signature_key = f"replica_sender_signature_{selected_lead_id}"
        sender_plan_key = f"replica_sender_plan_{selected_lead_id}"

        if sender_reply_mode == "NLP-guided reply":
            suggested_plan = sender_reply_plan(selected_lead, participant_email, inbound_body)
            draft_subject = str(suggested_plan.get("subject") or safe_reply_subject(inbound_subject))
            draft_body = _normalize_sender_body(
                str(suggested_plan.get("body") or ""),
                selected_lead.get("product_description", ""),
            )
            draft_signature = f"guided|{inbound_signature}|{suggested_plan.get('kind')}|{suggested_plan.get('action')}"
            if st.session_state.get(sender_signature_key) != draft_signature:
                st.session_state[sender_subject_key] = draft_subject
                st.session_state[sender_body_key] = draft_body
                st.session_state[sender_signature_key] = draft_signature
                st.session_state[sender_plan_key] = suggested_plan

            if suggested_plan.get("kind") == "scheduling":
                st.info(f"Scheduling action detected: {suggested_plan.get('action', 'unknown')}")
            else:
                st.info(f"NLP intent detected: {suggested_plan.get('intent', 'unknown')}")
        else:
            manual_signature = f"manual|{inbound_signature}"
            if st.session_state.get(sender_signature_key) != manual_signature:
                st.session_state[sender_subject_key] = safe_reply_subject(inbound_subject)
                st.session_state[sender_body_key] = _normalize_sender_body(
                    "Hi,\n\n\n\nBest,\nAI Sales Team",
                    selected_lead.get("product_description", ""),
                )
                st.session_state[sender_signature_key] = manual_signature
                st.session_state[sender_plan_key] = suggested_plan

        actions_col, _ = st.columns([0.22, 0.78])
        with actions_col:
            if st.button("Reset", key=f"replica_sender_reset_{selected_lead_id}"):
                if sender_reply_mode == "NLP-guided reply":
                    st.session_state[sender_subject_key] = str(suggested_plan.get("subject") or safe_reply_subject(inbound_subject))
                    st.session_state[sender_body_key] = _normalize_sender_body(
                        str(suggested_plan.get("body") or ""),
                        selected_lead.get("product_description", ""),
                    )
                    st.session_state[sender_plan_key] = suggested_plan
                else:
                    st.session_state[sender_subject_key] = safe_reply_subject(inbound_subject)
                    st.session_state[sender_body_key] = _normalize_sender_body(
                        "Hi,\n\n\n\nBest,\nAI Sales Team",
                        selected_lead.get("product_description", ""),
                    )

        sender_subject_value = st.text_input("Reply subject", key=sender_subject_key)
        sender_body_value = st.text_area("Reply body", key=sender_body_key, height=250)

        if suggested_plan.get("kind") == "scheduling":
            with st.expander("Current sender calendar availability", expanded=False):
                try:
                    lookahead = int(suggested_plan.get("lookahead_days") or selected_lead.get("meeting_offer_window_days") or 7)
                    st.code(calendar_slots_text_for_days(lookahead_days=lookahead), language="text")
                except Exception as exc:
                    st.info(f"Calendar availability could not be loaded: {exc}")

        if st.button("Draft in Sender Gmail", type="primary", key=f"replica_sender_submit_{selected_lead_id}"):
            if not sender_subject_value.strip():
                st.error("Reply subject is required.")
            elif not sender_body_value.strip():
                st.error("Reply body is required.")
            else:
                try:
                    active_plan = dict(st.session_state.get(sender_plan_key) or suggested_plan)
                    active_plan["subject"] = sender_subject_value
                    active_plan["body"] = sender_body_value
                    apply_sender_plan(selected_lead, participant_email, thread_id, active_plan)
                    st.session_state["mainapp2_notice"] = f"Sender Gmail draft created for {participant_email}"
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        st.markdown("</div>", unsafe_allow_html=True)
