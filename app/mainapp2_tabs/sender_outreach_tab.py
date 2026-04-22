import streamlit as st
import re
from collections import Counter

from app.mainapp2_support import ensure_sender_signature, send_new_lead_message


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "has", "have",
    "if", "in", "into", "is", "it", "its", "of", "on", "or", "our", "that", "the", "their",
    "there", "this", "to", "we", "with", "you", "your", "i", "me", "my", "us", "they", "them",
    "will", "can", "could", "should", "would", "may", "might", "about", "regards", "thanks",
    "hello", "hi", "dear", "team", "best", "looking", "forward", "please",
}


def _normalize_subject_text(subject: str) -> str:
    cleaned = re.sub(r"[\r\n\t]+", " ", subject or "")
    cleaned = cleaned.replace("?", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,:;")
    return cleaned


def _extract_topic_keywords(body: str) -> list[str]:
    text = re.sub(r"https?://\S+|\S+@\S+", " ", body or "")
    tokens = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text.lower())
    filtered = [tok for tok in tokens if tok not in _STOP_WORDS]
    if not filtered:
        return []
    freq = Counter(filtered)
    ranked = [word for word, _ in freq.most_common(6)]
    return ranked


def _build_subject_from_body(company_name: str, product_description: str, body: str, fallback_subject: str) -> str:
    normalized_fallback = _normalize_subject_text(fallback_subject)
    keywords = _extract_topic_keywords(body)

    topic = ""
    if keywords:
        topic = " ".join(word.title() for word in keywords[:3])
    elif product_description.strip():
        topic = " ".join(product_description.strip().split()[:5])

    body_l = (body or "").lower()
    if any(kw in body_l for kw in ("demo", "walkthrough", "showcase")):
        intent = f"Product Walkthrough and Value Alignment{(': ' + topic) if topic else ''}"
    elif any(kw in body_l for kw in ("follow up", "follow-up", "following up")):
        intent = f"Follow-Up on Business Priorities{(': ' + topic) if topic else ''}"
    elif any(kw in body_l for kw in ("automate", "streamline", "optimize", "improve", "efficiency")):
        intent = f"Improving Operational Outcomes{(': ' + topic) if topic else ''}"
    elif topic:
        intent = f"Strategic Collaboration Opportunity: {topic}"
    else:
        intent = normalized_fallback or "Business Collaboration Opportunity"

    company = (company_name or "").strip()
    composed = f"{company}: {intent}" if company else intent
    composed = _normalize_subject_text(composed)
    if len(composed) > 110:
        composed = composed[:110].rstrip(" ,:;-.")
    return composed or "Business Collaboration Opportunity"


def render_sender_outreach_tab(selected_lead_id: str, selected_lead: dict, related_email_docs: list[dict], recipients: list[str]) -> None:
    st.subheader("Email Workspace")
    related_labels = {
        str(doc["_id"]): f"{doc.get('contact_name') or 'No contact'} | {doc.get('contact_email') or 'No email'} | {doc.get('email_subject') or 'No subject'}"
        for doc in related_email_docs
    }
    selected_email_id = st.selectbox(
        "Choose stored email template",
        options=list(related_labels.keys()),
        format_func=lambda value: related_labels.get(value, value),
        key=f"mainapp2_sender_template_{selected_lead_id}",
    ) if related_labels else None

    selected_email_doc = next((doc for doc in related_email_docs if str(doc["_id"]) == selected_email_id), None)
    default_to = recipients[0] if recipients else ""
    raw_subject = (selected_email_doc or {}).get("email_subject") or selected_lead.get("draft_email_subject", "")
    default_body = ensure_sender_signature(
        (selected_email_doc or {}).get("email_body") or selected_lead.get("draft_email_body", ""),
        selected_lead.get("product_description", ""),
    )
    default_subject = _build_subject_from_body(
        company_name=selected_lead.get("company_name", ""),
        product_description=selected_lead.get("product_description", ""),
        body=default_body,
        fallback_subject=raw_subject,
    )

    e1, e2 = st.columns([1, 1])
    with e1:
        chosen_recipient = st.selectbox(
            "Known recipient",
            options=recipients or [""],
            index=0,
            key=f"mainapp2_sender_recipient_{selected_lead_id}",
        )
    with e2:
        manual_recipient = st.text_input(
            "Or type recipient email",
            value=default_to if not recipients else "",
            key=f"mainapp2_sender_manual_{selected_lead_id}",
        )

    final_recipient = manual_recipient.strip() or chosen_recipient.strip()
    subject_value = st.text_input("Subject", value=default_subject, key=f"mainapp2_sender_subject_{selected_lead_id}")
    body_value = st.text_area("Body", value=default_body, height=280, key=f"mainapp2_sender_body_{selected_lead_id}")

    if st.button("Send / Create Draft", type="primary", key=f"mainapp2_sender_submit_{selected_lead_id}"):
        if not final_recipient:
            st.error("Recipient email is required.")
        elif not subject_value.strip():
            st.error("Subject is required.")
        elif not body_value.strip():
            st.error("Body is required.")
        else:
            try:
                send_subject = _normalize_subject_text(subject_value)
                if not send_subject:
                    send_subject = _build_subject_from_body(
                        company_name=selected_lead.get("company_name", ""),
                        product_description=selected_lead.get("product_description", ""),
                        body=body_value,
                        fallback_subject=raw_subject,
                    )

                result = send_new_lead_message(
                    product_description=selected_lead.get("product_description", ""),
                    company_name=selected_lead.get("company_name", ""),
                    website=selected_lead.get("website", ""),
                    to_email=final_recipient,
                    subject=send_subject,
                    body=body_value,
                )
                if result["delivery_mode"] == "gmail_draft" and result["status"] == "draft":
                    st.success(f"Gmail draft created for {final_recipient}")
                elif result["delivery_mode"] == "sendgrid" and result["status"] == "sent":
                    st.success(f"Email sent to {final_recipient}")
                elif result["status"] == "draft":
                    st.success(f"Message saved in review mode for {final_recipient}")
                else:
                    st.error(result["error"] or "Message send failed")
            except Exception as exc:
                st.error(str(exc))
