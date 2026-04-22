import os
import time
import base64
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict

from bson import ObjectId
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.data.database import messages_collection


GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _send_via_sendgrid(to_email: str, subject: str, body: str) -> None:
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

    client = SendGridAPIClient(api_key)
    last_error: Exception | None = None

    # Retry transient network errors (e.g., DNS lookup failures) before failing.
    for attempt in range(3):
        try:
            response = client.send(message)
            if response.status_code >= 400:
                raise ValueError(f"SendGrid send failed with status {response.status_code}")
            return
        except Exception as exc:  # pragma: no cover - external provider behavior
            last_error = exc
            if attempt < 2:
                time.sleep(1.5)

    if last_error:
        raise last_error


def _delivery_mode() -> str:
    mode = (os.getenv("EMAIL_SEND_MODE") or "draft").strip().lower()
    return mode if mode in {"draft", "send", "gmail_draft"} else "draft"


def _app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _gmail_client_secret_path() -> Path:
    return Path(os.getenv("GMAIL_OAUTH_CLIENT_SECRET_PATH") or (_app_root() / "gmail_client_secret.json"))


def _gmail_token_path() -> Path:
    return Path(os.getenv("GMAIL_OAUTH_TOKEN_PATH") or (_app_root() / "gmail_token.json"))


def _gmail_sender_email() -> str:
    return (os.getenv("GMAIL_DRAFT_FROM_EMAIL") or os.getenv("SENDGRID_FROM_EMAIL") or "").strip()


def _get_gmail_credentials(interactive: bool) -> Credentials:
    token_path = _gmail_token_path()
    client_secret_path = _gmail_client_secret_path()
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        token_path.write_text(creds.to_json(), encoding="utf-8")

    if creds and creds.valid:
        return creds

    if not interactive:
        raise ValueError(
            "Gmail OAuth token not found. Use the dashboard auth button first and provide a Google client secret JSON file."
        )
    if not client_secret_path.exists():
        raise ValueError(
            f"Missing Gmail OAuth client secret file at {client_secret_path}. Create a Google OAuth desktop client and place the JSON there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), GMAIL_SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def ensure_gmail_oauth() -> Dict[str, str]:
    """Authenticate Gmail draft access and persist the token for future draft creation."""
    creds = _get_gmail_credentials(interactive=True)
    return {
        "status": "ok",
        "mode": "gmail_draft",
        "token_path": str(_gmail_token_path()),
        "authenticated_email": _gmail_sender_email() or "authenticated Gmail account",
        "token_valid": str(bool(creds.valid)).lower(),
    }


def _create_gmail_draft(to_email: str, subject: str, body: str) -> Dict[str, str]:
    creds = _get_gmail_credentials(interactive=False)
    service = build("gmail", "v1", credentials=creds)

    message = MIMEText(body)
    message["to"] = to_email
    message["subject"] = subject
    sender = _gmail_sender_email()
    if sender:
        message["from"] = sender

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    created = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return {
        "draft_id": created.get("id", ""),
        "gmail_message_id": created.get("message", {}).get("id", ""),
    }


def send_email(to_email: str, subject: str, body: str, lead_email: str, email_type: str = "outbound") -> Dict[str, str]:
    """Create a draft or send immediately depending on EMAIL_SEND_MODE."""
    sendgrid_enabled = _is_truthy(os.getenv("SENDGRID_ENABLED", "true"))
    requested_mode = _delivery_mode()
    status = "draft" if requested_mode == "draft" else "sent"
    delivery_mode = "review_queue" if requested_mode == "draft" else "simulated"
    error_message = ""
    external_draft_id = ""

    if requested_mode == "gmail_draft":
        status = "draft"
        delivery_mode = "gmail_draft"
        try:
            gmail_result = _create_gmail_draft(to_email=to_email, subject=subject, body=body)
            external_draft_id = gmail_result.get("draft_id", "")
        except Exception as exc:
            status = "failed"
            error_message = str(exc)
    elif requested_mode == "send" and sendgrid_enabled:
        try:
            _send_via_sendgrid(to_email=to_email, subject=subject, body=body)
            delivery_mode = "sendgrid"
        except Exception as exc:  # pragma: no cover - external provider behavior
            status = "failed"
            delivery_mode = "sendgrid"
            error_message = str(exc)

    doc = {
        "to": to_email,
        "lead_email": lead_email,
        "direction": "outbound",
        "type": email_type,
        "subject": subject,
        "body": body,
        "channel": "email",
        "status": status,
        "delivery_mode": delivery_mode,
        "review_required": requested_mode == "draft",
        "external_draft_id": external_draft_id,
        "error": error_message,
        "created_at": datetime.now(timezone.utc),
    }
    result = messages_collection.insert_one(doc)
    return {
        "message_id": str(result.inserted_id),
        "status": status,
        "delivery_mode": delivery_mode,
        "error": error_message,
        "external_draft_id": external_draft_id,
    }


def send_saved_draft(message_id: str) -> Dict[str, str]:
    """Send a previously queued draft message via SendGrid."""
    doc = messages_collection.find_one({"_id": ObjectId(message_id)})
    if not doc:
        raise ValueError("Draft not found")
    if doc.get("status") == "sent":
        raise ValueError("Draft already sent")

    sendgrid_enabled = _is_truthy(os.getenv("SENDGRID_ENABLED", "true"))
    if not sendgrid_enabled:
        raise ValueError("SENDGRID_ENABLED is false")

    status = "sent"
    delivery_mode = "sendgrid"
    error_message = ""

    try:
        _send_via_sendgrid(
            to_email=doc.get("to", ""),
            subject=doc.get("subject", ""),
            body=doc.get("body", ""),
        )
    except Exception as exc:  # pragma: no cover - external provider behavior
        status = "failed"
        error_message = str(exc)

    messages_collection.update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "status": status,
                "delivery_mode": delivery_mode,
                "review_required": False,
                "error": error_message,
                "sent_at": datetime.now(timezone.utc),
            }
        },
    )

    return {
        "message_id": str(doc["_id"]),
        "status": status,
        "delivery_mode": delivery_mode,
        "error": error_message,
    }
