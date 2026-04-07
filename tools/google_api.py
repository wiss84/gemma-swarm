"""
Gemma Swarm — Google API Helpers
==================================
All Google API interactions as plain Python functions.
Called directly from the 4 Google agent nodes — no LLM involvement.

OAuth Flow:
- First run: no tokens → bot posts auth link to Slack → user clicks → tokens saved
- Every hour: access token expires → silent refresh using refresh token
- Refresh token never expires unless manually revoked by user

google_state.json structure:
{
    "access_token":        "...",
    "refresh_token":       "...",
    "token_expiry":        "2026-03-25T16:00:00",
    "token_issued_date":   "2026-03-25",
    "user_timezone":       "Europe/Warsaw"   ← fetched from Google Calendar settings
}
"""

import json
import time
import logging
import threading
import urllib.parse
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

GOOGLE_STATE_FILE  = Path(__file__).parent.parent / "google_state.json"
GOOGLE_CREDS_FILE  = Path(__file__).parent.parent / "Google_creds.json"
OAUTH_REDIRECT_URI = "http://localhost:8766/google/callback"
OAUTH_PORT         = 8766
OAUTH_SCOPES       = " ".join([
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "openid",
    "email",
    "profile",
])

_state_lock          = threading.Lock()
_oauth_code_event    = threading.Event()
_oauth_code_received = ""


# ── Custom Exceptions ──────────────────────────────────────────────────────────

class GoogleAuthError(Exception):
    pass


class GoogleAPIError(Exception):
    pass


# ── Credentials File ───────────────────────────────────────────────────────────

def _load_creds() -> dict:
    if not GOOGLE_CREDS_FILE.exists():
        raise GoogleAuthError(
            f"Google_creds.json not found at {GOOGLE_CREDS_FILE}. "
            "Please download it from Google Cloud Console → APIs & Services → Credentials."
        )
    try:
        raw   = json.loads(GOOGLE_CREDS_FILE.read_text(encoding="utf-8"))
        creds = raw.get("web") or raw.get("installed") or raw
        return creds
    except Exception as e:
        raise GoogleAuthError(f"Could not parse Google_creds.json: {e}")


def _get_client_credentials() -> tuple[str, str]:
    creds         = _load_creds()
    client_id     = creds.get("client_id", "").strip()
    client_secret = creds.get("client_secret", "").strip()
    if not client_id or not client_secret:
        raise GoogleAuthError("client_id or client_secret missing in Google_creds.json.")
    return client_id, client_secret


# ── State File ─────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if not GOOGLE_STATE_FILE.exists():
        return {}
    try:
        return json.loads(GOOGLE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[google] Could not load state: {e}")
        return {}


def _save_state(state: dict):
    try:
        GOOGLE_STATE_FILE.write_text(
            json.dumps(state, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"[google] Could not save state: {e}")


# ── OAuth Flow ─────────────────────────────────────────────────────────────────

def build_auth_url() -> str:
    client_id, _ = _get_client_credentials()
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id":     client_id,
        "redirect_uri":  OAUTH_REDIRECT_URI,
        "scope":         OAUTH_SCOPES,
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         "gemma_swarm_google",
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


class _OAuthCallbackHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        global _oauth_code_received
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code   = params.get("code", [""])[0]
        error  = params.get("error", [""])[0]

        if code:
            _oauth_code_received = code
            _oauth_code_event.set()
            body = b"<h2>Google authorization successful! You can close this tab.</h2>"
            self.send_response(200)
        else:
            body = f"<h2>Authorization failed: {error}</h2>".encode()
            self.send_response(400)

        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _run_callback_server(timeout: int = 300) -> str:
    global _oauth_code_received, _oauth_code_event
    _oauth_code_received = ""
    _oauth_code_event    = threading.Event()

    server         = HTTPServer(("localhost", OAUTH_PORT), _OAuthCallbackHandler)
    server.timeout = 5

    logger.info(f"[google] OAuth callback server listening on port {OAUTH_PORT}")

    start = time.time()
    while not _oauth_code_event.is_set():
        if time.time() - start > timeout:
            logger.warning("[google] OAuth callback timed out.")
            break
        server.handle_request()

    server.server_close()
    return _oauth_code_received


def _exchange_code_for_tokens(code: str) -> dict:
    client_id, client_secret = _get_client_credentials()
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  OAUTH_REDIRECT_URI,
            "client_id":     client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _refresh_access_token() -> str:
    state         = _load_state()
    refresh_token = state.get("refresh_token", "")
    if not refresh_token:
        raise GoogleAuthError("No refresh token found. Re-authorization required.")

    client_id, client_secret = _get_client_credentials()
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "client_id":     client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    response.raise_for_status()
    tokens = response.json()

    access_token = tokens.get("access_token", "")
    expires_in   = tokens.get("expires_in", 3600)
    expiry       = datetime.utcnow() + timedelta(seconds=expires_in - 60)

    with _state_lock:
        state["access_token"] = access_token
        state["token_expiry"] = expiry.isoformat()
        _save_state(state)

    logger.info("[google] Access token refreshed silently.")
    return access_token


def complete_oauth_flow(slack_post_fn=None) -> bool:
    auth_url = build_auth_url()
    message  = (
        "🔐 *Google Authorization Required*\n"
        "Please click the link below to authorize Gemma Swarm to access your Google account.\n"
        f"<{auth_url}|Click here to authorize Google>"
    )

    if slack_post_fn:
        slack_post_fn(message)
    else:
        logger.info(f"[google] Auth URL: {auth_url}")

    logger.info("[google] Waiting for OAuth callback (5 min timeout)...")
    code = _run_callback_server(timeout=300)

    if not code:
        if slack_post_fn:
            slack_post_fn("❌ Google authorization timed out. Please try again.")
        return False

    try:
        tokens = _exchange_code_for_tokens(code)
    except Exception as e:
        logger.error(f"[google] Token exchange failed: {e}")
        if slack_post_fn:
            slack_post_fn(f"❌ Google authorization failed: {e}")
        return False

    access_token  = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    expires_in    = tokens.get("expires_in", 3600)
    expiry        = datetime.utcnow() + timedelta(seconds=expires_in - 60)

    if not refresh_token:
        logger.error("[google] No refresh_token returned.")
        if slack_post_fn:
            slack_post_fn(
                "❌ Google did not return a refresh token.\n"
                "Please go to https://myaccount.google.com/permissions, "
                "revoke Gemma Swarm access, and try again."
            )
        return False

    with _state_lock:
        state = _load_state()
        state["access_token"]      = access_token
        state["refresh_token"]     = refresh_token
        state["token_expiry"]      = expiry.isoformat()
        state["token_issued_date"] = str(datetime.utcnow().date())
        _save_state(state)

    logger.info("[google] OAuth complete. Tokens saved.")

    # Immediately fetch and store user timezone after successful auth
    try:
        tz = _fetch_calendar_timezone()
        if tz:
            with _state_lock:
                state = _load_state()
                state["user_timezone"] = tz
                _save_state(state)
            logger.info(f"[google] User timezone detected: {tz}")
    except Exception as e:
        logger.warning(f"[google] Could not fetch timezone: {e}")

    if slack_post_fn:
        slack_post_fn(
            "✅ Google authorization successful! "
            "You can now use Gmail, Calendar, Docs, and Sheets."
        )
    return True


def _get_access_token(slack_post_fn=None) -> str:
    with _state_lock:
        state = _load_state()

    access_token  = state.get("access_token", "")
    refresh_token = state.get("refresh_token", "")
    expiry_str    = state.get("token_expiry", "")

    if not access_token or not refresh_token:
        success = complete_oauth_flow(slack_post_fn)
        if not success:
            raise GoogleAuthError("Google authorization failed or timed out.")
        return _load_state().get("access_token", "")

    if expiry_str:
        try:
            expiry = datetime.fromisoformat(expiry_str)
            if datetime.utcnow() >= expiry:
                logger.info("[google] Access token expired. Refreshing silently...")
                return _refresh_access_token()
        except ValueError:
            pass

    return access_token


# ── Shared Header Helper ───────────────────────────────────────────────────────

def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── User Timezone ──────────────────────────────────────────────────────────────

def _fetch_calendar_timezone() -> str | None:
    """
    Fetch the user's timezone from their Google Calendar settings.
    Returns an IANA timezone string e.g. "Europe/Warsaw", "America/New_York".
    This is the timezone the user has set in their Google Calendar account.
    """
    try:
        token    = _get_access_token()
        response = requests.get(
            "https://www.googleapis.com/calendar/v3/users/me/settings/timezone",
            headers=_auth_headers(token),
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("value", None)
    except Exception as e:
        logger.warning(f"[google] Could not fetch calendar timezone: {e}")
        return None


def get_user_timezone() -> str:
    """
    Get the user's timezone. Returns cached value from google_state.json if available.
    Falls back to fetching from Google Calendar API. Falls back to UTC if all else fails.

    This is the primary function to call whenever you need the user's timezone.
    """
    # Check cache first
    state = _load_state()
    cached = state.get("user_timezone", "")
    if cached:
        return cached

    # Not cached — fetch from Google Calendar
    tz = _fetch_calendar_timezone()
    if tz:
        with _state_lock:
            state = _load_state()
            state["user_timezone"] = tz
            _save_state(state)
        logger.info(f"[google] User timezone fetched and cached: {tz}")
        return tz

    logger.warning("[google] Could not determine user timezone — defaulting to UTC.")
    return "UTC"


# ── Plain Text Body Extractor ──────────────────────────────────────────────────

def _extract_plain_text_body(payload: dict) -> str:
    """
    Recursively extract ONLY plain text from a Gmail message payload.
    Skips HTML parts, attachments, images — returns clean readable text only.
    """
    import base64

    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        try:
            decoded = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
            return decoded.strip()
        except Exception:
            return ""

    for part in payload.get("parts", []):
        result = _extract_plain_text_body(part)
        if result:
            return result

    return ""
