import json
import os
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


def _env_scopes(var_name: str) -> list[str]:
    raw = (os.getenv(var_name) or "").strip()
    if not raw:
        return []
    return [scope.strip() for scope in raw.split(",") if scope.strip()]


def sender_loop_scopes() -> list[str]:
    default_scopes = [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    ]
    configured = _env_scopes("SENDER_LOOP_SCOPES")
    if not configured:
        return default_scopes
    # Keep deterministic order while removing duplicates.
    ordered_unique: list[str] = []
    for scope in configured:
        if scope not in ordered_unique:
            ordered_unique.append(scope)
    return ordered_unique


SENDER_LOOP_SCOPES = sender_loop_scopes()

CUSTOMER_LOOP_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]

EXIT_PHRASES = [
    "thank you for helping",
    "thanks for helping",
    "thanks, this helps",
    "stop",
    "not interested",
    "unsubscribe",
    "pause outreach",
]


def app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_path(value: str | None, default_name: str) -> Path:
    if value and value.strip():
        return Path(value.strip())
    return app_root() / default_name


def gmail_client_secret_path() -> Path:
    return resolve_path(os.getenv("GMAIL_OAUTH_CLIENT_SECRET_PATH"), "gmail_client_secret.json")


def sender_token_path() -> Path:
    configured = os.getenv("GMAIL_LOOP_SENDER_TOKEN_PATH")
    if configured and configured.strip():
        return Path(configured.strip())
    return resolve_path(None, "gmail_sender_loop_token.json")


def customer_token_path() -> Path:
    configured = os.getenv("GMAIL_LOOP_CUSTOMER_TOKEN_PATH")
    return resolve_path(configured, "gmail_customer_token.json")


def sender_email() -> str:
    return (os.getenv("GMAIL_DRAFT_FROM_EMAIL") or os.getenv("SENDGRID_FROM_EMAIL") or "").strip().lower()


def customer_email() -> str:
    return (os.getenv("AUTO_REPLY_CUSTOMER_EMAIL") or "").strip().lower()


def sender_timezone() -> ZoneInfo:
    tz_name = (os.getenv("SENDER_TIMEZONE") or "Asia/Kolkata").strip()
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def groq_api_key() -> str:
    return (os.getenv("GROQ_API_KEY") or "").strip()


def groq_base_url() -> str:
    return (os.getenv("GROQ_BASE_URL") or "https://api.groq.com/openai/v1").rstrip("/")


def groq_model() -> str:
    return (os.getenv("GROQ_MODEL") or "llama-3.1-8b-instant").strip()


def is_exit_message(text: str) -> bool:
    lower = (text or "").lower()
    return any(phrase in lower for phrase in EXIT_PHRASES)


def token_scopes(token_path: Path) -> set[str]:
    if not token_path.exists():
        return set()
    try:
        token_json = json.loads(token_path.read_text(encoding="utf-8"))
        return set(token_json.get("scopes", []))
    except Exception:
        return set()


def get_credentials(token_path: Path, interactive: bool, required_scopes: List[str], auth_label: str) -> Credentials:
    client_secret = gmail_client_secret_path()
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), required_scopes)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except RefreshError:
            # Token was revoked or expired on Google's side (invalid_grant).
            # Delete the stale file and fall through to the interactive OAuth flow.
            try:
                token_path.unlink(missing_ok=True)
            except Exception:
                pass
            creds = None

    granted = token_scopes(token_path)
    required = set(required_scopes)
    if creds and creds.valid and required.issubset(granted):
        return creds

    if creds and creds.valid and not required.issubset(granted) and not interactive:
        raise ValueError(
            f"{auth_label} token is missing scopes. Re-authorize {auth_label.lower()} access from dashboard."
        )

    if not interactive:
        raise ValueError(
            f"Token not found or invalid at {token_path}. Authorize {auth_label.lower()} access first from dashboard."
        )

    if not client_secret.exists():
        raise ValueError(f"Missing Gmail OAuth client secret file at {client_secret}.")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), required_scopes)
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds
