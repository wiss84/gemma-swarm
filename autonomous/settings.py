"""
Gemma Swarm — Autonomous Settings
====================================
Loads, saves, and provides defaults for autonomous_settings.json.
Single source of truth for all autonomous pipeline configuration.
"""

import json
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

SETTINGS_FILE = Path(__file__).parent.parent / "autonomous_settings.json"

# ── Default structure ──────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "active":                  False,
    "autonomous_channel":      "",
    "autonomous_channel_id":   "",
    "activity_log_sheet_id":   "",
    "last_summary_date":       None,

    "inbox_check": {
        "enabled":              False,
        "poll_interval_minutes": 30,
        "last_seen_ids":        []
    },

    "email_watch": {
        "senders":              [],
        "poll_interval_minutes": 15,
        "last_seen_ids":        {}
    },

    "research": {
        "topics":               [],
        "interval_days":        3,
        "last_run":             None
    },

    "calendar_notify": {
        "enabled":              True,
        "notify_offsets":       [30],
        "last_checked_date":    None,
        "pending_notifications": []
    },
}


# ── Load / Save ────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    """
    Load autonomous_settings.json from project root.
    Returns DEFAULT_SETTINGS merged with whatever is saved — missing keys
    get their default values so old config files stay compatible.
    """
    if not SETTINGS_FILE.exists():
        return _deep_copy(DEFAULT_SETTINGS)

    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return _merge(DEFAULT_SETTINGS, saved)
    except Exception as e:
        logger.error(f"[autonomous/settings] Could not load settings: {e}")
        return _deep_copy(DEFAULT_SETTINGS)


def save_settings(data: dict):
    """Write settings dict to autonomous_settings.json."""
    try:
        SETTINGS_FILE.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("[autonomous/settings] Settings saved.")
    except Exception as e:
        logger.error(f"[autonomous/settings] Could not save settings: {e}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _deep_copy(d: dict) -> dict:
    """Simple deep copy via JSON round-trip."""
    return json.loads(json.dumps(d))


def _merge(defaults: dict, saved: dict) -> dict:
    """
    Recursively merge saved values into defaults.
    Keys present in defaults but missing in saved get their default value.
    Keys present in saved but not in defaults are kept as-is.
    """
    result = _deep_copy(defaults)
    for key, value in saved.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def get_channel_id(channel_name: str, slack_client) -> str | None:
    """
    Resolve a Slack channel name to its channel ID.
    Strips leading # if present. Returns None if not found.
    """
    name = channel_name.lstrip("#").strip().lower()
    try:
        cursor = None
        while True:
            kwargs = {"limit": 200, "types": "public_channel,private_channel"}
            if cursor:
                kwargs["cursor"] = cursor

            response = slack_client.conversations_list(**kwargs)
            channels = response.get("channels", [])

            for ch in channels:
                if ch.get("name", "").lower() == name:
                    return ch["id"]

            meta   = response.get("response_metadata", {})
            cursor = meta.get("next_cursor", "")
            if not cursor:
                break

    except Exception as e:
        logger.error(f"[autonomous/settings] Could not resolve channel '{name}': {e}")

    return None


def is_research_due(settings: dict) -> bool:
    """Check if research job is due based on interval_days and last_run."""
    last_run = settings["research"].get("last_run")
    if not last_run:
        return True
    try:
        last   = datetime.fromisoformat(last_run).date()
        today  = datetime.utcnow().date()
        delta  = (today - last).days
        return delta >= settings["research"]["interval_days"]
    except Exception:
        return True


def is_summary_due(settings: dict) -> bool:
    """Check if daily summary is due (not already posted today)."""
    last = settings.get("last_summary_date")
    if not last:
        return True
    try:
        return datetime.fromisoformat(last).date() < datetime.utcnow().date()
    except Exception:
        return True


def is_calendar_check_due(settings: dict) -> bool:
    """Check if calendar reminder setup is due (not already run today)."""
    last = settings["calendar_notify"].get("last_checked_date")
    if not last:
        return True
    try:
        return datetime.fromisoformat(last).date() < datetime.utcnow().date()
    except Exception:
        return True
