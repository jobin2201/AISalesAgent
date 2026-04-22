import re
import html
from datetime import timedelta, timezone

import streamlit as st

from app.conversation_flow.calendar_tools import calendar_slots_text_for_days
from app.conversation_flow.google_tools import safe_reply_subject
from app.mainapp2_support import (
    apply_sender_plan,
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
    """Keep only the required company footer by removing legacy sender footer first."""
    text = (body or "").rstrip()
    # Remove legacy footer wherever it appears near the end of generated bodies.
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n*Best,\s*\nAI Sales Team\s*", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return ensure_sender_signature(text, product_description)


def _format_ist_label(dt_value) -> str:
    """Format datetime as IST for user-facing labels."""
    if not dt_value:
        return ""

    ist_tz = timezone(timedelta(hours=5, minutes=30))
    try:
        if dt_value.tzinfo is None:
            dt_value = dt_value.replace(tzinfo=timezone.utc)
        return dt_value.astimezone(ist_tz).strftime("%d %b %Y %I:%M %p IST")
    except Exception:
        return str(dt_value)


def render_sender_replies_tab(selected_lead_id: str, selected_lead: dict, recipients: list[str]) -> None:
    st.subheader("Sender Reply Workspace")
    st.caption("Review inbound customer messages on the left and draft the next sender response on the right.")
    st.markdown(
        """
        <style>
        .sender-reply-shell {
            background: linear-gradient(180deg, #eaf3ff 0%, #ffffff 100%);
            border: 1px solid #bfd6f3;
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 12px;
            box-shadow: none;
        }
        .sender-reply-kicker {
            font-size: 0.8rem;
            letter-spacing: 0.08em;
            font-weight: 800;
            text-transform: uppercase;
            color: #245892;
            margin-bottom: 2px;
        }
        .sender-reply-title {
            font-size: 1.14rem;
            font-weight: 800;
            color: #10213a;
            margin-bottom: 4px;
        }
        .sender-reply-subtitle {
            font-size: 0.9rem;
            color: #365273;
            font-weight: 600;
            margin-bottom: 0;
        }
        .sender-reply-meta-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 10px;
            margin: 10px 0 14px 0;
        }
        .sender-reply-meta-card {
            background: #f6faff;
            border: 1px solid #c2d8f5;
            border-radius: 12px;
            padding: 10px 12px;
            min-height: 82px;
            box-shadow: 0 6px 16px rgba(16, 33, 58, 0.05);
        }
        .sender-reply-meta-card:nth-child(1) {
            background: linear-gradient(180deg, #ffffff 0%, #eaf3ff 100%);
            border-color: #bfd6f3;
        }
        .sender-reply-meta-card:nth-child(2) {
            background: #edf7ff;
            border-color: #b6d6f5;
        }
        .sender-reply-meta-card:nth-child(3) {
            background: #f2f7ff;
            border-color: #c8daf2;
        }
        .sender-reply-meta-label {
            font-size: 0.72rem;
            letter-spacing: 0.07em;
            text-transform: uppercase;
            color: #3e5f84;
            font-weight: 800;
            margin-bottom: 6px;
        }
        .sender-reply-meta-value {
            font-size: 0.98rem;
            color: #10213a;
            font-weight: 750;
            line-height: 1.35;
            word-break: break-word;
        }
        .sender-reply-step {
            background: #f2f7ff;
            border: 1px solid #c8daf2;
            border-radius: 10px;
            padding: 9px 11px;
            margin: 8px 0 10px 0;
            font-size: 0.92rem;
            color: #113662;
            font-weight: 800;
        }
        .sender-thread-summary-shell {
            border: 1px solid #bfd6f3;
            border-radius: 12px;
            padding: 10px 12px 12px 12px;
            background: linear-gradient(180deg, #eaf3ff 0%, #ffffff 100%);
            margin-bottom: 10px;
        }
        .sender-thread-id-chip {
            display: inline-block;
            margin-top: 8px;
            background: #edf7ff;
            color: #113662;
            border: 1px solid #b6d6f5;
            border-radius: 999px;
            padding: 4px 10px;
            font-size: 0.8rem;
            font-weight: 800;
            word-break: break-word;
        }
        .sender-refresh-wrap {
            margin-bottom: 12px;
        }
        .sender-section-caption {
            color: #335372;
            font-weight: 700;
        }
        .sender-reply-section {
            border: 1px solid #c2d8f5;
            border-radius: 12px;
            background: #ffffff;
            padding: 12px;
            margin-bottom: 12px;
        }
        @media (prefers-color-scheme: dark) {
            .sender-reply-shell,
            .sender-thread-summary-shell {
                background: linear-gradient(180deg, rgba(30, 58, 95, 0.92) 0%, rgba(18, 28, 45, 0.95) 100%);
                border-color: rgba(117, 167, 233, 0.55);
            }
            .sender-reply-kicker {
                color: #9bc8ff;
            }
            .sender-reply-title,
            .sender-reply-meta-value {
                color: #f4f8ff;
            }
            .sender-reply-subtitle,
            .sender-section-caption {
                color: #d0e2ff;
            }
            .sender-reply-meta-card,
            .sender-reply-meta-card:nth-child(1),
            .sender-reply-meta-card:nth-child(2),
            .sender-reply-meta-card:nth-child(3),
            .sender-reply-section {
                background: rgba(24, 39, 60, 0.9);
                border-color: rgba(117, 167, 233, 0.45);
            }
            .sender-reply-step {
                background: rgba(23, 38, 58, 0.9);
                border-color: rgba(117, 167, 233, 0.42);
                color: #a6cdff;
            }
            .sender-reply-meta-label {
                color: #9cc8ff;
            }
            .sender-thread-id-chip {
                background: rgba(23, 38, 58, 0.9);
                border-color: rgba(117, 167, 233, 0.42);
                color: #edf3ff;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="sender-reply-shell">
            <div class="sender-reply-kicker">Sender Mailbox Reply</div>
            <div class="sender-reply-title">Track threads quickly, answer with confidence</div>
            <div class="sender-reply-subtitle">Use the same guided workspace style for reviewing inbound context and creating polished sender drafts.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    sender_mailbox = html.escape(sender_loop_address() or "Not configured")
    customer_mailbox = html.escape(customer_loop_address() or "Not configured")
    lead_name = html.escape(selected_lead.get("company_name", "Unknown") or "Unknown")
    st.markdown(
        f"""
        <div class="sender-reply-meta-grid">
            <div class="sender-reply-meta-card">
                <div class="sender-reply-meta-label">Sender mailbox</div>
                <div class="sender-reply-meta-value">{sender_mailbox}</div>
            </div>
            <div class="sender-reply-meta-card">
                <div class="sender-reply-meta-label">Customer mailbox</div>
                <div class="sender-reply-meta-value">{customer_mailbox}</div>
            </div>
            <div class="sender-reply-meta-card">
                <div class="sender-reply-meta-label">Lead</div>
                <div class="sender-reply-meta-value">{lead_name}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    participants = lead_participants(selected_lead, recipients)
    with st.container():
        st.markdown('<div class="sender-refresh-wrap">', unsafe_allow_html=True)
        st.button("Refresh sender inbox", key=f"mainapp2_sender_refresh_{selected_lead_id}")
        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="sender-reply-step">Step 1: Pick a customer thread</div>', unsafe_allow_html=True)
        st.markdown(f'<p class="sender-section-caption">Tracked participants: {format_list(participants) or "None found"}</p>', unsafe_allow_html=True)

    sender_context_error = ""
    try:
        sender_conversations = list_sender_conversations(participants)
    except Exception as exc:
        sender_conversations = []
        sender_context_error = str(exc)

    if sender_context_error:
        st.info(f"Sender reply context is not ready yet: {sender_context_error}")
        return

    if not sender_conversations:
        st.info("No customer replies were found yet for this selected lead. Once a recipient replies, that conversation will appear here as a separate thread.")
        return

    conversation_labels = {
        str(index): (
            f"{item.get('from_email', 'unknown')} | {item.get('subject', '(no subject)')}"
            + (f" | {_format_ist_label(item.get('received_at'))}" if item.get("received_at") else "")
        )
        for index, item in enumerate(sender_conversations)
    }
    selected_conversation_index = st.selectbox(
        "Choose customer conversation",
        options=list(conversation_labels.keys()),
        format_func=lambda value: conversation_labels.get(value, value),
        key=f"mainapp2_sender_conversation_{selected_lead_id}",
    )
    selected_conversation = sender_conversations[int(selected_conversation_index)]
    participant_email = str(selected_conversation.get("from_email") or "")
    thread_id = str(selected_conversation.get("thread_id") or "")
    inbound_subject = str(selected_conversation.get("subject") or "Quick follow-up")
    inbound_body = str(selected_conversation.get("body") or "")
    inbound_message_id = str(selected_conversation.get("message_id") or "")
    inbound_signature = f"{selected_conversation.get('message_id')}|{thread_id}|{hash(inbound_body)}"
    last_sender_log = latest_sender_log(selected_lead.get("company_name", ""), participant_email)

    participant_value = html.escape(participant_email or "—")
    meeting_state_value = html.escape(selected_lead.get("meeting_state", "—") or "—")
    meeting_contact_value = html.escape(selected_lead.get("meeting_contact_email", "—") or "—")
    thread_id_value = html.escape(thread_id or "—")
    st.markdown(
        f"""
        <div class="sender-thread-summary-shell">
            <div class="sender-reply-meta-label">Thread Summary</div>
            <div class="sender-reply-meta-grid" style="margin: 6px 0 2px 0;">
                <div class="sender-reply-meta-card">
                    <div class="sender-reply-meta-label">Participant</div>
                    <div class="sender-reply-meta-value">{participant_value}</div>
                </div>
                <div class="sender-reply-meta-card">
                    <div class="sender-reply-meta-label">Meeting State</div>
                    <div class="sender-reply-meta-value">{meeting_state_value}</div>
                </div>
                <div class="sender-reply-meta-card">
                    <div class="sender-reply-meta-label">Current Meeting Contact</div>
                    <div class="sender-reply-meta-value">{meeting_contact_value}</div>
                </div>
            </div>
            <div class="sender-thread-id-chip">Thread ID: {thread_id_value}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="sender-reply-step">Step 2: Review conversation context</div>', unsafe_allow_html=True)
    with st.container(border=True):
        context_latest_tab, context_previous_tab = st.tabs(["Latest customer message", "Last sent sender draft"])
        with context_latest_tab:
            st.write(f"**From:** {participant_email or 'Unknown'}")
            st.write(f"**Subject:** {inbound_subject}")
            st.caption("Latest customer message")
            st.text_area(
                "Inbound message",
                value=inbound_body or "(No body found)",
                height=260,
                disabled=True,
                key=f"mainapp2_sender_inbound_{selected_lead_id}",
            )
        with context_previous_tab:
            if last_sender_log:
                st.write(f"**Subject:** {last_sender_log.get('subject', '(no subject)')}")
                st.text_area(
                    "Previous sender draft",
                    value=last_sender_log.get("body", "") or "(No body found)",
                    height=260,
                    disabled=True,
                    key=f"mainapp2_sender_previous_{selected_lead_id}",
                )
            else:
                st.info("No previous sender draft is logged yet for this participant.")

    st.markdown('<div class="sender-reply-step">Step 3: Compose the next sender reply</div>', unsafe_allow_html=True)
    sender_reply_mode = st.radio(
        "Reply style",
        options=["NLP-guided reply", "Manual reply"],
        horizontal=True,
        key=f"mainapp2_sender_mode_{selected_lead_id}",
    )

    suggested_plan = sender_reply_plan(selected_lead, participant_email, inbound_body)
    sender_subject_key = f"mainapp2_sender_reply_subject_{selected_lead_id}"
    sender_body_key = f"mainapp2_sender_reply_body_{selected_lead_id}"
    sender_signature_key = f"mainapp2_sender_reply_signature_{selected_lead_id}"
    sender_plan_key = f"mainapp2_sender_reply_plan_{selected_lead_id}"

    if sender_reply_mode == "NLP-guided reply":
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
            st.session_state[sender_plan_key] = {
                "kind": "manual",
                "action": "manual",
                "subject": safe_reply_subject(inbound_subject),
                "body": _normalize_sender_body(
                    "Hi,\n\n\n\nBest,\nAI Sales Team",
                    selected_lead.get("product_description", ""),
                ),
            }

    with st.container(border=True):
        top_actions_col, top_info_col = st.columns([0.3, 0.7])
        with top_actions_col:
            if st.button("Reset sender suggestion", key=f"mainapp2_sender_reset_{selected_lead_id}"):
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
        with top_info_col:
            st.caption("Tune the draft below. Use reset anytime to reload the current guided/manual baseline.")

        sender_subject_value = st.text_input("Reply subject", key=sender_subject_key)
        sender_body_value = st.text_area("Reply body", key=sender_body_key, height=300)

    if selected_lead.get("meeting_event_link") or selected_lead.get("meeting_start"):
        with st.expander("Current booked meeting", expanded=False):
            st.write(f"Meeting state: {selected_lead.get('meeting_state', '—') or '—'}")
            st.write(f"Meeting contact: {selected_lead.get('meeting_contact_email', '—') or '—'}")
            st.write(f"Start: {selected_lead.get('meeting_start', '—') or '—'}")
            st.write(f"End: {selected_lead.get('meeting_end', '—') or '—'}")
            st.write(f"Invite link: {selected_lead.get('meeting_event_link', '—') or '—'}")

    if suggested_plan.get("kind") == "scheduling":
        with st.expander("Current sender calendar availability", expanded=False):
            try:
                lookahead = int(suggested_plan.get("lookahead_days") or selected_lead.get("meeting_offer_window_days") or 7)
                st.code(calendar_slots_text_for_days(lookahead_days=lookahead), language="text")
            except Exception as exc:
                st.info(f"Calendar availability could not be loaded: {exc}")

    st.markdown('<div class="sender-reply-step">Step 4: Create draft in sender mailbox</div>', unsafe_allow_html=True)
    if st.button("Draft in Sender Gmail", type="primary", key=f"mainapp2_sender_reply_submit_{selected_lead_id}"):
        if not sender_subject_value.strip():
            st.error("Reply subject is required.")
        elif not sender_body_value.strip():
            st.error("Reply body is required.")
        else:
            try:
                active_plan = dict(st.session_state.get(sender_plan_key) or suggested_plan)
                active_plan["subject"] = sender_subject_value
                active_plan["body"] = sender_body_value
                active_plan["source_message_id"] = inbound_message_id
                apply_sender_plan(selected_lead, participant_email, thread_id, active_plan)
                st.session_state["mainapp2_notice"] = f"Sender Gmail draft created for {participant_email}"
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
