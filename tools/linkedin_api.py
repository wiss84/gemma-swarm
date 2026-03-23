"""
Gemma Swarm — LinkedIn API Helpers
=====================================
All LinkedIn API interactions as plain Python functions.
Called directly from linkedin_send_node — no LLM involvement.

OAuth Flow:
- First run: no tokens → bot posts auth link to Slack → user clicks → tokens saved
- Day 60: access token expires → auto-refresh using refresh token (silent)
- Day 365: refresh token expires → bot posts auth link to Slack again

linkedin_state.json structure:
{
    "access_token":               "...",
    "token_issued_date":          "2026-03-13",
    "linkedin_counter": {
        "date":             "2026-03-13",
        "daily_post_count": 0
    }
}
"""

import os
import json
import time
import logging
import threading
import subprocess
import urllib.parse
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

API_VERSION          = "202510"
# Place linkedin_state.json at project root alongside .env and gemma_swarm.db
LINKEDIN_STATE_FILE  = Path(__file__).parent.parent / "linkedin_state.json"
DAILY_POST_LIMIT     = 100
DAILY_POST_WARNING   = 90
TOKEN_WARNING_DAYS   = 5
TOKEN_EXPIRY_DAYS    = 60
OAUTH_REDIRECT_URI   = "http://localhost:8765/linkedin/callback"
OAUTH_SCOPES         = "openid profile email w_member_social"
OAUTH_PORT           = 8765

_state_lock          = threading.Lock()
_urn_cache: str      = ""
_oauth_code_event    = threading.Event()
_oauth_code_received = ""


# ── State File ─────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if not LINKEDIN_STATE_FILE.exists():
        return {}
    try:
        return json.loads(LINKEDIN_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[linkedin] Could not load state: {e}")
        return {}


def _save_state(state: dict):
    try:
        LINKEDIN_STATE_FILE.write_text(
            json.dumps(state, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"[linkedin] Could not save state: {e}")


# ── Custom Exceptions ──────────────────────────────────────────────────────────

class LinkedInAuthError(Exception):
    pass


class LinkedInMediaError(Exception):
    pass


# ── OAuth Flow ─────────────────────────────────────────────────────────────────

def _get_client_credentials() -> tuple[str, str]:
    """Returns (client_id, client_secret) from env. Raises if missing."""
    client_id     = os.getenv("LINKEDIN_CLIENT_ID", "").strip()
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise LinkedInAuthError(
            "LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET must be set in .env"
        )
    return client_id, client_secret


def build_auth_url() -> str:
    """Build the LinkedIn OAuth authorization URL."""
    client_id, _ = _get_client_credentials()
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id":     client_id,
        "redirect_uri":  OAUTH_REDIRECT_URI,
        "scope":         OAUTH_SCOPES,
        "state":         "gemma_swarm_linkedin",
    })
    return f"https://www.linkedin.com/oauth/v2/authorization?{params}"


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler to catch LinkedIn's OAuth callback."""

    def do_GET(self):
        global _oauth_code_received
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code   = params.get("code", [""])[0]
        error  = params.get("error", [""])[0]

        if code:
            _oauth_code_received = code
            _oauth_code_event.set()
            body = b"<h2>Authorization successful! You can close this tab.</h2>"
            self.send_response(200)
        else:
            body = f"<h2>Authorization failed: {error}</h2>".encode()
            self.send_response(400)

        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Silence request logs


def _run_callback_server(timeout: int = 300):
    """
    Starts a local HTTP server on OAUTH_PORT to catch the LinkedIn callback.
    Blocks until code is received or timeout is reached.
    Returns the authorization code or empty string.
    """
    global _oauth_code_received, _oauth_code_event
    _oauth_code_received = ""
    _oauth_code_event    = threading.Event()

    server = HTTPServer(("localhost", OAUTH_PORT), _OAuthCallbackHandler)
    server.timeout = 5  # Check event every 5 seconds

    logger.info(f"[linkedin] OAuth callback server listening on port {OAUTH_PORT}")

    start = time.time()
    while not _oauth_code_event.is_set():
        if time.time() - start > timeout:
            logger.warning("[linkedin] OAuth callback timed out.")
            break
        server.handle_request()

    server.server_close()
    return _oauth_code_received


def exchange_code_for_tokens(code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    client_id, client_secret = _get_client_credentials()
    response = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
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



def complete_oauth_flow(slack_post_fn=None) -> bool:
    """
    Full OAuth flow triggered when no tokens exist or refresh token expired.

    slack_post_fn: callable(message: str) — posts a message to the current
                   Slack thread. If None, logs the URL instead.

    Returns True if tokens were obtained successfully.
    """
    auth_url = build_auth_url()
    message  = (
        f"🔐 *LinkedIn Authorization Required*\n"
        f"Please click the link below to authorize Gemma Swarm to post on LinkedIn.\n"
        f"<{auth_url}|Click here to authorize LinkedIn>"
    )

    if slack_post_fn:
        slack_post_fn(message)
    else:
        logger.info(f"[linkedin] Auth URL: {auth_url}")

    # Start callback server in current thread (blocks until callback or timeout)
    logger.info("[linkedin] Waiting for OAuth callback (5 min timeout)...")
    code = _run_callback_server(timeout=300)

    if not code:
        if slack_post_fn:
            slack_post_fn("❌ LinkedIn authorization timed out. Please try again.")
        return False

    try:
        tokens = exchange_code_for_tokens(code)
    except Exception as e:
        logger.error(f"[linkedin] Token exchange failed: {e}")
        if slack_post_fn:
            slack_post_fn(f"❌ LinkedIn authorization failed: {e}")
        return False

    with _state_lock:
        state = _load_state()
        state["access_token"]      = tokens.get("access_token", "")
        state["token_issued_date"] = str(date.today())
        if "linkedin_counter" not in state:
            state["linkedin_counter"] = {
                "date":             str(date.today()),
                "daily_post_count": 0,
            }
        _save_state(state)

    logger.info("[linkedin] OAuth complete. Tokens saved to linkedin_state.json.")
    if slack_post_fn:
        slack_post_fn("✅ LinkedIn authorization successful! You can now post.")
    return True


# ── Token Retrieval ────────────────────────────────────────────────────────────

def _get_access_token(slack_post_fn=None) -> str:
    """
    Returns a valid access token.
    - No tokens → triggers full OAuth flow
    - Access token expired → triggers full OAuth flow (re-auth every 2 months)
    """
    with _state_lock:
        state = _load_state()

    access_token = state.get("access_token", "") or os.getenv("LINKEDIN_ACCESS_TOKEN", "")

    # No token at all — first time setup
    if not access_token:
        logger.info("[linkedin] No tokens found — starting OAuth flow.")
        success = complete_oauth_flow(slack_post_fn)
        if not success:
            raise LinkedInAuthError("LinkedIn authorization failed or timed out.")
        with _state_lock:
            state        = _load_state()
            access_token = state.get("access_token", "")
        return access_token

    # Check access token expiry
    issued_str = state.get("token_issued_date", "")
    if issued_str:
        issued       = datetime.strptime(issued_str, "%Y-%m-%d").date()
        days_elapsed = (date.today() - issued).days
        if days_elapsed >= TOKEN_EXPIRY_DAYS:
            logger.info("[linkedin] Access token expired — requesting re-authorization.")
            success = complete_oauth_flow(slack_post_fn)
            if not success:
                raise LinkedInAuthError("LinkedIn re-authorization failed or timed out.")
            with _state_lock:
                state        = _load_state()
                access_token = state.get("access_token", "")

    return access_token



# ── Rate Limit Tracking ────────────────────────────────────────────────────────

def _get_daily_count() -> int:
    with _state_lock:
        state   = _load_state()
        counter = state.get("linkedin_counter", {})
        today   = str(date.today())
        if counter.get("date") != today:
            counter = {"date": today, "daily_post_count": 0}
            state["linkedin_counter"] = counter
            _save_state(state)
        return counter.get("daily_post_count", 0)


def _increment_daily_count():
    with _state_lock:
        state   = _load_state()
        counter = state.get("linkedin_counter", {})
        today   = str(date.today())
        if counter.get("date") != today:
            counter = {"date": today, "daily_post_count": 0}
        counter["daily_post_count"] = counter.get("daily_post_count", 0) + 1
        state["linkedin_counter"]   = counter
        _save_state(state)


def check_rate_limit() -> tuple[bool, Optional[str]]:
    """
    Returns (can_post, warning_message).
    can_post=False means limit reached.
    """
    count = _get_daily_count()
    if count >= DAILY_POST_LIMIT:
        return False, (
            f"❌ LinkedIn daily post limit reached ({DAILY_POST_LIMIT} posts). "
            f"Try again tomorrow."
        )
    if count >= DAILY_POST_WARNING:
        remaining = DAILY_POST_LIMIT - count
        return True, (
            f"⚠️ LinkedIn rate limit warning: {count}/{DAILY_POST_LIMIT} posts used today. "
            f"{remaining} remaining."
        )
    return True, None


# ── Media Conversion ───────────────────────────────────────────────────────────

SUPPORTED_IMAGE_EXTS  = {"jpg", "jpeg", "png", "gif"}
SUPPORTED_VIDEO_EXTS  = {"mp4", "mov", "avi"}
SUPPORTED_DOCUMENT_EXTS = {"pdf", "doc", "docx", "ppt", "pptx"}
CONVERTIBLE_IMAGE_EXTS = {"webp", "bmp", "tiff", "tif", "heic", "heif", "avif"}
CONVERTIBLE_VIDEO_EXTS = {"wmv", "mkv", "flv", "webm", "m4v", "3gp"}


def _convert_image(src_path: Path) -> Path:
    try:
        from PIL import Image
        dst_path = src_path.with_suffix(".png")
        Image.open(src_path).convert("RGBA").save(dst_path, "PNG")
        logger.info(f"[linkedin] Converted: {src_path.name} → {dst_path.name}")
        return dst_path
    except ImportError:
        raise LinkedInMediaError("Pillow not installed. Run: pip install Pillow")
    except Exception as e:
        raise LinkedInMediaError(f"Image conversion failed: {e}")


def _convert_video(src_path: Path) -> Path:
    dst_path = src_path.with_suffix(".mp4")
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(src_path), "-c:v", "libx264",
             "-c:a", "aac", str(dst_path), "-y"],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise LinkedInMediaError(f"ffmpeg error: {result.stderr[:200]}")
        logger.info(f"[linkedin] Converted: {src_path.name} → {dst_path.name}")
        return dst_path
    except FileNotFoundError:
        raise LinkedInMediaError("ffmpeg not found. Please install ffmpeg.")
    except subprocess.TimeoutExpired:
        raise LinkedInMediaError("Video conversion timed out.")


def prepare_media(file_path: str) -> tuple[Path, str]:
    """
    Validates, converts if needed, and returns (resolved_path, media_type).
    media_type is "IMAGE", "VIDEO", or "DOCUMENT".
    """
    path = Path(file_path)
    if not path.exists():
        raise LinkedInMediaError(f"Media file not found: {file_path}")

    ext = path.suffix.lstrip(".").lower()

    if ext in SUPPORTED_DOCUMENT_EXTS:
        return path, "DOCUMENT"
    if ext in SUPPORTED_IMAGE_EXTS:
        return path, "IMAGE"
    if ext in SUPPORTED_VIDEO_EXTS:
        return path, "VIDEO"
    if ext in CONVERTIBLE_IMAGE_EXTS:
        return _convert_image(path), "IMAGE"
    if ext in CONVERTIBLE_VIDEO_EXTS:
        return _convert_video(path), "VIDEO"

    raise LinkedInMediaError(
        f"Unsupported format: .{ext}. "
        f"Supported documents: {', '.join(sorted(SUPPORTED_DOCUMENT_EXTS))}. "
        f"Supported images: {', '.join(sorted(SUPPORTED_IMAGE_EXTS | CONVERTIBLE_IMAGE_EXTS))}. "
        f"Supported videos: {', '.join(sorted(SUPPORTED_VIDEO_EXTS | CONVERTIBLE_VIDEO_EXTS))}."
    )


# ── LinkedIn API Calls ─────────────────────────────────────────────────────────

def _get_headers(token: str, content_type: bool = True) -> dict:
    headers = {
        "Authorization":             f"Bearer {token}",
        "LinkedIn-Version":          API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def get_person_urn(slack_post_fn=None) -> str:
    """Fetch and cache the authenticated user's person URN."""
    global _urn_cache
    if _urn_cache:
        return _urn_cache

    token    = _get_access_token(slack_post_fn)
    response = requests.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={
            "Authorization":    f"Bearer {token}",
            "LinkedIn-Version": API_VERSION,
        },
        timeout=10,
    )
    response.raise_for_status()
    user_id    = response.json()["sub"]
    _urn_cache = f"urn:li:person:{user_id}"
    logger.info(f"[linkedin] Person URN cached: {_urn_cache}")
    return _urn_cache


def _register_upload(owner_urn: str, media_type: str, token: str) -> tuple[str, str]:
    response = requests.post(
        "https://api.linkedin.com/rest/assets?action=registerUpload",
        json={
            "registerUploadRequest": {
                "recipes": [f"urn:li:digitalmediaRecipe:feedshare-{media_type.lower()}"],
                "owner":   owner_urn,
                "serviceRelationships": [{
                    "relationshipType": "OWNER",
                    "identifier":       "urn:li:userGeneratedContent",
                }],
            }
        },
        headers=_get_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    data       = response.json()["value"]
    upload_url = data["uploadMechanism"][
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
    ]["uploadUrl"]
    asset_urn  = data["asset"]
    return upload_url, asset_urn


def _upload_binary(upload_url: str, file_path: Path, token: str):
    with open(file_path, "rb") as f:
        response = requests.put(
            upload_url,
            data=f,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )
    response.raise_for_status()




def _upload_document(owner_urn: str, file_path: Path, token: str) -> str:
    """
    Upload a document (PDF, DOC, DOCX, PPT, PPTX) to LinkedIn.
    Uses the Documents API — separate from image/video assets API.
    Returns the document URN.
    """
    # Step 1: Initialize upload
    response = requests.post(
        "https://api.linkedin.com/rest/documents?action=initializeUpload",
        json={
            "initializeUploadRequest": {
                "owner": owner_urn,
            }
        },
        headers=_get_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    data        = response.json()["value"]
    upload_url  = data["uploadUrl"]
    document_urn = data["document"]

    # Step 2: Upload file binary
    with open(file_path, "rb") as f:
        upload_response = requests.put(
            upload_url,
            data=f,
            headers={
                "Authorization":    f"Bearer {token}",
                "Content-Type":     "application/octet-stream",
            },
            timeout=120,
        )
    upload_response.raise_for_status()
    logger.info(f"[linkedin] Document uploaded: {file_path.name} → {document_urn}")
    return document_urn

def _create_post(author_urn: str, text: str, asset_urn: Optional[str], token: str, is_document: bool = False) -> str:
    payload = {
        "author":         author_urn,
        "commentary":     text,
        "visibility":     "PUBLIC",
        "distribution":   {"feedDistribution": "MAIN_FEED"},
        "lifecycleState": "PUBLISHED",
    }
    if asset_urn:
        if is_document:
            payload["content"] = {"document": {"id": asset_urn}}
        else:
            payload["content"] = {"media": {"id": asset_urn}}

    response = requests.post(
        "https://api.linkedin.com/rest/posts",
        json=payload,
        headers=_get_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    return response.headers.get("x-restli-id", "post-created")


# ── Main Public Function ───────────────────────────────────────────────────────

def publish_linkedin_post(
    text: str,
    media_path: Optional[str] = None,
    slack_post_fn=None,
) -> tuple[bool, str]:
    """
    Full LinkedIn post flow. Called from linkedin_send_node.
    slack_post_fn: callable(message) — used for auth flow messages if needed.
    Returns (success, message).
    """
    # Rate limit check first
    can_post, rate_msg = check_rate_limit()
    if not can_post:
        return False, rate_msg

    try:
        token      = _get_access_token(slack_post_fn)
        person_urn = get_person_urn(slack_post_fn)
        asset_urn  = None

        is_document = False
        if media_path:
            try:
                resolved_path, media_type = prepare_media(media_path)
                if media_type == "DOCUMENT":
                    asset_urn   = _upload_document(person_urn, resolved_path, token)
                    is_document = True
                else:
                    upload_url, asset_urn = _register_upload(person_urn, media_type, token)
                    _upload_binary(upload_url, resolved_path, token)
                logger.info(f"[linkedin] Media uploaded: {resolved_path.name} ({media_type})")
            except LinkedInMediaError as e:
                return False, f"❌ Media error: {e}"
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                return False, f"❌ Media upload failed ({status}): {e.response.text[:200]}"

        post_id = _create_post(person_urn, text, asset_urn, token, is_document=is_document)
        _increment_daily_count()

        if rate_msg:
            return True, f"✅ Post published successfully.\n{rate_msg}"
        return True, "✅ Post published successfully."

    except LinkedInAuthError as e:
        return False, f"🔐 LinkedIn auth error: {e}"
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        body   = e.response.text[:300] if e.response is not None else ""
        logger.error(f"[linkedin] HTTP {status}: {body}")
        return False, f"❌ LinkedIn API error ({status}): {body}"
    except requests.Timeout:
        return False, "❌ LinkedIn API request timed out. Please try again."
    except Exception as e:
        logger.error(f"[linkedin] Unexpected error: {e}")
        return False, f"❌ Unexpected error: {e}"
