import re
import html
from datetime import datetime, timedelta

import streamlit as st

from app.conversation_flow.calendar_tools import calendar_slots_text_for_days
from app.conversation_flow.google_tools import contains_scheduling_intent, safe_reply_subject
from app.mainapp2_support import (
    customer_flow_choices,
    customer_loop_address,
    customer_message_context,
    customer_reply_template,
    draft_customer_mailbox_reply,
    sender_loop_address,
)


def render_customer_reply_tab(selected_lead_id: str, selected_lead: dict) -> None:
    st.subheader("Customer Reply Workspace")
    st.caption("Gmail-style view: review sender email on the left, choose reply path, and draft reply on the right.")
    st.markdown(
        """
        <style>
        .customer-reply-shell {
            background: linear-gradient(180deg, #eaf3ff 0%, #ffffff 100%);
            border: 1px solid #bfd6f3;
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 12px;
        }
        .customer-reply-kicker {
            font-size: 0.78rem;
            letter-spacing: 0.08em;
            font-weight: 800;
            text-transform: uppercase;
            color: #245892;
            margin-bottom: 2px;
        }
        .customer-reply-title {
            font-size: 1.05rem;
            font-weight: 800;
            color: #10213a;
            margin-bottom: 4px;
        }
        .customer-reply-subtitle {
            font-size: 0.88rem;
            color: #365273;
            font-weight: 600;
            margin-bottom: 0;
        }
        .customer-reply-step {
            background: #f2f7ff;
            border: 1px solid #c8daf2;
            border-radius: 10px;
            padding: 8px 10px;
            margin: 8px 0 10px 0;
            font-size: 0.9rem;
            color: #113662;
            font-weight: 800;
        }
        .customer-reply-meta-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 10px;
            margin: 10px 0 14px 0;
        }
        .customer-reply-meta-card {
            background: #f6faff;
            border: 1px solid #c2d8f5;
            border-radius: 12px;
            padding: 10px 12px;
            min-height: 82px;
            box-shadow: 0 6px 16px rgba(16, 33, 58, 0.05);
        }
        .customer-reply-meta-card:nth-child(2) {
            background: #edf7ff;
            border-color: #b6d6f5;
        }
        .customer-reply-meta-card:nth-child(3) {
            background: #f2f7ff;
            border-color: #c8daf2;
        }
        .customer-reply-meta-label {
            font-size: 0.72rem;
            letter-spacing: 0.07em;
            text-transform: uppercase;
            color: #3e5f84;
            font-weight: 800;
            margin-bottom: 6px;
        }
        .customer-reply-meta-value {
            font-size: 0.94rem;
            color: #10213a;
            font-weight: 750;
            line-height: 1.35;
            word-break: break-word;
        }
        @media (prefers-color-scheme: dark) {
            .customer-reply-shell {
                background: linear-gradient(180deg, rgba(30, 58, 95, 0.92) 0%, rgba(18, 28, 45, 0.95) 100%);
                border-color: rgba(117, 167, 233, 0.55);
            }
            .customer-reply-kicker {
                color: #9bc8ff;
            }
            .customer-reply-title,
            .customer-reply-meta-value {
                color: #f4f8ff;
            }
            .customer-reply-subtitle {
                color: #d0e2ff;
            }
            .customer-reply-step {
                background: rgba(23, 38, 58, 0.9);
                border-color: rgba(117, 167, 233, 0.42);
                color: #a6cdff;
            }
            .customer-reply-meta-card,
            .customer-reply-meta-card:nth-child(2),
            .customer-reply-meta-card:nth-child(3) {
                background: rgba(24, 39, 60, 0.9);
                border-color: rgba(117, 167, 233, 0.45);
            }
            .customer-reply-meta-label {
                color: #9cc8ff;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="customer-reply-shell">
            <div class="customer-reply-kicker">Customer Mailbox Reply</div>
            <div class="customer-reply-title">Understand fast, compose confidently</div>
            <div class="customer-reply-subtitle">Use the guided panel to keep response quality high while preserving your current process.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    customer_context: dict[str, object] | None = None
    try:
        customer_context = customer_message_context()
    except Exception as exc:
        st.info(f"Customer reply context is not ready yet: {exc}")

    sender_mailbox = html.escape(sender_loop_address() or "Not configured")
    customer_mailbox = html.escape(customer_loop_address() or "Not configured")
    lead_name = html.escape(selected_lead.get("company_name", "Unknown") or "Unknown")
    st.markdown(
        f"""
        <div class="customer-reply-meta-grid">
            <div class="customer-reply-meta-card">
                <div class="customer-reply-meta-label">Sender mailbox</div>
                <div class="customer-reply-meta-value">{sender_mailbox}</div>
            </div>
            <div class="customer-reply-meta-card">
                <div class="customer-reply-meta-label">Customer mailbox</div>
                <div class="customer-reply-meta-value">{customer_mailbox}</div>
            </div>
            <div class="customer-reply-meta-card">
                <div class="customer-reply-meta-label">Lead</div>
                <div class="customer-reply-meta-value">{lead_name}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if selected_lead.get("meeting_event_link") or selected_lead.get("meeting_start"):
        with st.expander("Current booked meeting visible to customer", expanded=False):
            st.write(f"Meeting state: {selected_lead.get('meeting_state', '—') or '—'}")
            st.write(f"Meeting contact: {selected_lead.get('meeting_contact_email', '—') or '—'}")
            st.write(f"Start: {selected_lead.get('meeting_start', '—') or '—'}")
            st.write(f"End: {selected_lead.get('meeting_end', '—') or '—'}")
            st.write(f"Invite link: {selected_lead.get('meeting_event_link', '—') or '—'}")

    if customer_context and customer_context.get("latest_message"):
        latest_sender_message = customer_context["latest_message"] or {}
        latest_subject = str(latest_sender_message.get("subject") or "Quick follow-up")
        latest_body = str(latest_sender_message.get("body") or "")
        latest_thread_id = str(latest_sender_message.get("thread_id") or "")
        message_signature = f"{latest_subject}|{latest_thread_id}|{hash(latest_body)}"

        flow_options, suggested_flow = customer_flow_choices(latest_subject, latest_body)
        flow_map = {item["id"]: item for item in flow_options}

        customer_subject_key = f"mainapp2_customer_subject_{selected_lead_id}"
        customer_body_key = f"mainapp2_customer_body_{selected_lead_id}"
        customer_signature_key = f"mainapp2_customer_signature_{selected_lead_id}"
        customer_slot_key = f"mainapp2_customer_slot_{selected_lead_id}"
        customer_duration_key = f"mainapp2_customer_duration_{selected_lead_id}"

        left_col, right_col = st.columns([0.45, 0.55], gap="large")

        with left_col:
            st.markdown("### Sender Email Snapshot")
            st.markdown('<div class="customer-reply-step">Step 1: Review the latest sender message context</div>', unsafe_allow_html=True)
            with st.container(border=True):
                st.write(f"**From:** {customer_context.get('sender_email') or 'Unknown'}")
                st.write(f"**To:** {customer_context.get('customer_email') or 'Unknown'}")
                st.write(f"**Subject:** {latest_subject}")
                st.caption("Latest sender message in customer mailbox")
                st.text_area(
                    "Message",
                    value=latest_body,
                    height=340,
                    disabled=True,
                    key=f"mainapp2_customer_preview_{selected_lead_id}_{hash(message_signature)}",
                )

        with right_col:
            st.markdown("### Reply Composer")
            st.markdown('<div class="customer-reply-step">Step 2: Pick reply path and refine draft details</div>', unsafe_allow_html=True)
            reply_mode = st.radio(
                "Reply style",
                options=["Guided flow", "Manual reply"],
                horizontal=True,
                key=f"mainapp2_customer_mode_{selected_lead_id}",
            )

            selected_flow = suggested_flow
            if reply_mode == "Guided flow":
                selected_flow = st.selectbox(
                    "Choose reply path",
                    options=[item["id"] for item in flow_options],
                    index=next((index for index, item in enumerate(flow_options) if item["id"] == suggested_flow), 0),
                    format_func=lambda value: flow_map[value]["label"],
                    key=f"mainapp2_customer_flow_{selected_lead_id}",
                )
                st.caption(flow_map[selected_flow]["description"])
            else:
                selected_flow = "custom"

            st.divider()
            st.markdown("##### Draft Inputs")

            if reply_mode == "Guided flow":
                suggested_subject, suggested_body = customer_reply_template(selected_flow, latest_subject, latest_body)
            else:
                suggested_subject = safe_reply_subject(latest_subject)
                suggested_body = "Hi,\n\n\n\nBest,\nCustomer"

            slot_table_text = ""
            parsed_slots: list[str] = []
            if selected_flow == "accept" and contains_scheduling_intent(f"{latest_subject}\n{latest_body}"):
                try:
                    slot_table_text = calendar_slots_text_for_days()
                    for raw_line in slot_table_text.splitlines():
                        line = raw_line.strip()
                        if not line or "|" not in line:
                            continue
                        if line.lower().startswith("date") or line.startswith("---"):
                            continue
                        parts = [part.strip() for part in line.split("|")]
                        if len(parts) < 3:
                            continue
                        date_label, day_label, windows = parts[0], parts[1], parts[2]
                        if not windows or windows.lower() == "busy":
                            continue
                        for window in [item.strip() for item in windows.split(",") if item.strip()]:
                            parsed_slots.append(f"{day_label}, {date_label} {window} IST")
                except Exception:
                    parsed_slots = []

            selected_slot = ""
            if parsed_slots:
                selected_slot = st.selectbox(
                    "Pick preferred slot",
                    options=parsed_slots,
                    index=0,
                    key=customer_slot_key,
                )

            selected_duration = 30
            selected_start_label = ""
            if selected_flow == "accept" and selected_slot:
                selected_duration = st.slider(
                    "Preferred duration (minutes)",
                    min_value=15,
                    max_value=180,
                    value=30,
                    step=15,
                    key=customer_duration_key,
                )

                slot_match = re.match(
                    r"^(?P<label>.+?)\s+(?P<start>\d{1,2}:\d{2}\s*[AP]M)-(?P<end>\d{1,2}:\d{2}\s*[AP]M)\s+IST$",
                    selected_slot,
                    flags=re.IGNORECASE,
                )
                if slot_match:
                    slot_label = slot_match.group("label")
                    slot_start = datetime.strptime(slot_match.group("start").upper(), "%I:%M %p")
                    slot_end = datetime.strptime(slot_match.group("end").upper(), "%I:%M %p")
                    if slot_end <= slot_start:
                        slot_end += timedelta(days=1)

                    candidate_starts: list[datetime] = []
                    cursor = slot_start
                    while cursor + timedelta(minutes=selected_duration) <= slot_end:
                        candidate_starts.append(cursor)
                        cursor += timedelta(minutes=15)

                    if candidate_starts:
                        default_start = candidate_starts[0]
                        start_options = [dt.strftime("%I:%M %p") for dt in candidate_starts]
                        selected_start_label = st.selectbox(
                            "Pick start time inside selected slot",
                            options=start_options,
                            index=0,
                            key=f"{customer_slot_key}_start",
                        )
                        selected_start_dt = datetime.strptime(selected_start_label, "%I:%M %p")
                        selected_end_dt = selected_start_dt + timedelta(minutes=selected_duration)
                        precise_slot = f"{slot_label} {selected_start_dt.strftime('%I:%M %p')}-{selected_end_dt.strftime('%I:%M %p')} IST"
                        selected_slot = precise_slot
                    else:
                        st.info("Selected duration does not fit inside this slot window. Reduce duration or choose another slot.")

            if selected_flow == "accept" and selected_slot:
                suggested_body = re.sub(
                    r"Preferred slot:\s*.*",
                    f"Preferred slot: {selected_slot}",
                    suggested_body,
                )
                subject_slot_label = selected_slot.replace(" IST", "")
                customer_addr = str(customer_context.get("customer_email") or "customer")
                suggested_subject = f"Re: Meeting slot request @ {subject_slot_label} (IST) ({customer_addr})"
                if re.search(r"Preferred duration:\s*.*", suggested_body):
                    suggested_body = re.sub(
                        r"Preferred duration:\s*.*",
                        f"Preferred duration: {selected_duration} minutes",
                        suggested_body,
                    )
                else:
                    suggested_body = suggested_body.replace(
                        "\n\nIf that slot is still available, please send the calendar invite.",
                        f"\nPreferred duration: {selected_duration} minutes\n\nIf that slot is still available, please send the calendar invite.",
                    )

            active_signature = f"{reply_mode}|{selected_flow}|{message_signature}|{selected_slot}|{selected_duration}|{selected_start_label}"
            if st.session_state.get(customer_signature_key) != active_signature:
                st.session_state[customer_subject_key] = suggested_subject
                st.session_state[customer_body_key] = suggested_body
                st.session_state[customer_signature_key] = active_signature

            reset_col, send_col = st.columns([0.3, 0.7])
            with reset_col:
                if st.button("Reset draft", key=f"mainapp2_customer_reset_{selected_lead_id}"):
                    st.session_state[customer_subject_key] = suggested_subject
                    st.session_state[customer_body_key] = suggested_body
                    st.session_state[customer_signature_key] = active_signature

            st.markdown('<div class="customer-reply-step">Step 3: Finalize subject/body and draft to Gmail</div>', unsafe_allow_html=True)
            subject_value = st.text_input("Reply subject", key=customer_subject_key)
            body_value = st.text_area("Reply body", key=customer_body_key, height=280)

            if slot_table_text:
                with st.expander("Live sender calendar availability", expanded=False):
                    st.code(slot_table_text, language="text")

            if st.button("Draft in Customer Gmail", type="primary", key=f"mainapp2_customer_submit_{selected_lead_id}"):
                if not subject_value.strip():
                    st.error("Reply subject is required.")
                elif not body_value.strip():
                    st.error("Reply body is required.")
                else:
                    try:
                        result = draft_customer_mailbox_reply(
                            product_description=selected_lead.get("product_description", ""),
                            company_name=selected_lead.get("company_name", ""),
                            subject=subject_value,
                            body=body_value,
                            thread_id=latest_thread_id,
                            sender_addr=str(customer_context.get("sender_email") or ""),
                            customer_addr=str(customer_context.get("customer_email") or ""),
                        )
                        st.success(
                            f"Customer Gmail draft created for {customer_context.get('sender_email')}"
                            + (f" | Draft ID: {result['external_draft_id']}" if result.get("external_draft_id") else "")
                        )
                    except Exception as exc:
                        st.error(str(exc))
    else:
        st.info("No recent sender email was found in the customer inbox. Once the sender mails the customer, the reply composer will show the latest sender message here.")
