import base64
import os
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.conversation_flow.config import customer_email as loop_customer_email
from app.conversation_flow.config import get_credentials
from app.conversation_flow.config import sender_email as loop_sender_email
from app.conversation_flow.config import sender_token_path, token_scopes
from app.conversation_flow.google_tools import profile_email
from app.spam.orchestrator import post_send_record, pre_send_check


GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
SENDER_BOOKING_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

_message_log_collection = None


def configure(message_log_collection) -> None:
    global _message_log_collection
    _message_log_collection = message_log_collection


def delivery_mode() -> str:
    value = (os.getenv("EMAIL_SEND_MODE") or "draft").strip().lower()
    return value if value in {"draft", "send", "gmail_draft"} else "draft"


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _gmail_client_secret_path() -> Path:
    return Path(os.getenv("GMAIL_OAUTH_CLIENT_SECRET_PATH") or (Path(__file__).resolve().parents[2] / "gmail_client_secret.json"))


def _gmail_token_path() -> Path:
    return Path(os.getenv("GMAIL_OAUTH_TOKEN_PATH") or (Path(__file__).resolve().parents[2] / "gmail_token.json"))


def _get_gmail_credentials(interactive: bool) -> Credentials:
    token_path = _gmail_token_path()
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        token_path.write_text(creds.to_json(), encoding="utf-8")

    if creds and creds.valid:
        return creds

    if not interactive:
        raise ValueError("Gmail OAuth token not found. Use the auth button first.")
    raise ValueError(f"Missing or invalid Gmail token at {token_path}. Use the auth button first.")


def _create_gmail_draft(to_email: str, subject: str, body: str, cc_email: str = "") -> dict[str, str]:
    creds = _get_gmail_credentials(interactive=False)
    service = build("gmail", "v1", credentials=creds)

    message = MIMEText(body)
    message["to"] = to_email
    if cc_email:
        message["cc"] = cc_email
    message["subject"] = subject
    from_email = (os.getenv("GMAIL_DRAFT_FROM_EMAIL") or os.getenv("SENDGRID_FROM_EMAIL") or "").strip()
    if from_email:
        message["from"] = from_email

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    created = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return {
        "draft_id": created.get("id", ""),
        "gmail_message_id": created.get("message", {}).get("id", ""),
    }


def _send_via_sendgrid(to_email: str, subject: str, body: str, cc_email: str = "") -> None:
    api_key = os.getenv("SENDGRID_API_KEY", "").strip()
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "").strip()

    if not api_key:
        raise ValueError("SENDGRID_API_KEY is missing")
    if not from_email:
        raise ValueError("SENDGRID_FROM_EMAIL is missing")

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        plain_text_content=body,
        html_content=body.replace("\n", "<br>"),
    )
    if cc_email:
        message.cc = cc_email

    client = SendGridAPIClient(api_key)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = client.send(message)
            if response.status_code >= 400:
                raise ValueError(f"SendGrid send failed with status {response.status_code}")
            return
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5)
    if last_error:
        raise last_error


def log_message(payload: dict) -> None:
    if _message_log_collection is None:
        raise ValueError("Message log collection is not configured.")
    _message_log_collection.insert_one(payload)


def _sender_company_name(product_description: str) -> str:
    value = (product_description or "").strip().lower()
    if "digicom" in value:
        return "DigiCom Private Ltd"
    if "digiexpense" in value or "digidelight" in value or "digi delight" in value:
        return "Digi Delight Solution Private Ltd"
    return "Digi Delight Solution Private Ltd"


def ensure_sender_signature(body: str, product_description: str) -> str:
    """Ensure sender drafts always end with the required Balaji company signature."""
    text = (body or "").rstrip()
    company = _sender_company_name(product_description)

    # Avoid duplicate signatures when a draft is edited and sent again.
    lower = text.lower()
    if lower.endswith(f"balaji,\n{company}".lower()) or lower.endswith(f"balaji, {company}".lower()):
        return text

    return f"{text}\n\nBalaji,\n{company}" if text else f"Balaji,\n{company}"


def send_new_lead_message(product_description: str, company_name: str, website: str, to_email: str, subject: str, body: str) -> dict[str, str]:
    requested_mode = delivery_mode()
    status = "draft" if requested_mode == "draft" else "sent"
    delivery_mode_value = "review_queue" if requested_mode == "draft" else "simulated"
    error_message = ""
    external_draft_id = ""
    cc_email = (os.getenv("AUTO_REPLY_CUSTOMER_EMAIL") or "").strip()
    from_email = (os.getenv("GMAIL_DRAFT_FROM_EMAIL") or os.getenv("SENDGRID_FROM_EMAIL") or "").strip()
    body_with_signature = ensure_sender_signature(body, product_description)
    guardrail = pre_send_check(
        to_email=to_email,
        subject=subject,
        body=body_with_signature,
        sender_email=from_email,
    )
    body_final = str(guardrail.get("body") or body_with_signature)

    if not bool(guardrail.get("allowed", True)):
        status = "failed"
        delivery_mode_value = "blocked_by_guardrail"
        error_message = str(guardrail.get("reason") or "Blocked by send-layer compliance guardrail.")

    spam_risk = guardrail.get("spam_risk") or {"score": 0, "risk": "low", "flags": []}

    if status != "failed" and requested_mode == "gmail_draft":
        status = "draft"
        delivery_mode_value = "gmail_draft"
        try:
            result = _create_gmail_draft(to_email=to_email, subject=subject, body=body_final, cc_email=cc_email)
            external_draft_id = result.get("draft_id", "")
        except Exception as exc:
            status = "failed"
            error_message = str(exc)
    elif status != "failed" and requested_mode == "send" and _is_truthy(os.getenv("SENDGRID_ENABLED", "true")):
        delivery_mode_value = "sendgrid"
        try:
            _send_via_sendgrid(to_email=to_email, subject=subject, body=body_final, cc_email=cc_email)
        except Exception as exc:
            status = "failed"
            error_message = str(exc)

    log_message(
        {
            "product_description": product_description,
            "company_name": company_name,
            "website": website,
            "to": to_email,
            "cc": cc_email,
            "from_email": from_email,
            "subject": subject,
            "body": body_final,
            "status": status,
            "delivery_mode": delivery_mode_value,
            "external_draft_id": external_draft_id,
            "spam_risk": spam_risk,
            "dns_auth": guardrail.get("dns_auth") or {},
            "warmup": guardrail.get("warmup") or {},
            "error": error_message,
            "created_at": datetime.now(timezone.utc),
        }
    )
    post_send_record(sender_email=from_email, status=status)
    return {
        "status": status,
        "delivery_mode": delivery_mode_value,
        "error": error_message,
        "external_draft_id": external_draft_id,
    }


def sender_loop_address() -> str:
    return (loop_sender_email() or os.getenv("GMAIL_DRAFT_FROM_EMAIL") or os.getenv("SENDGRID_FROM_EMAIL") or "").strip().lower()


def customer_loop_address() -> str:
    return (loop_customer_email() or os.getenv("AUTO_REPLY_CUSTOMER_EMAIL") or "").strip().lower()


def missing_sender_booking_scopes() -> list[str]:
    granted = token_scopes(sender_token_path())
    return [scope for scope in SENDER_BOOKING_SCOPES if scope not in granted]


def ensure_sender_booking_access(interactive: bool) -> dict[str, str]:
    token_path = sender_token_path()
    creds = get_credentials(
        token_path,
        interactive=interactive,
        required_scopes=SENDER_BOOKING_SCOPES,
        auth_label="Sender booking",
    )
    service = build("gmail", "v1", credentials=creds)
    actual_sender = profile_email(service)
    expected_sender = sender_loop_address()
    if expected_sender and actual_sender != expected_sender:
        raise ValueError(
            f"Authorized sender account is {actual_sender}, expected {expected_sender}. Please re-authorize and choose the correct sender Google account."
        )
    return {
        "authorized_email": actual_sender,
        "token_path": str(token_path),
        "scopes": ", ".join(sorted(token_scopes(token_path))),
    }