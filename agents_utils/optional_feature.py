"""
Gemma Swarm — Optional Feature Gating
========================================
Single source of truth for checking whether an optional integration is
configured.  Used by:
  - toolset_registry.py  (load_toolset meta-tool)
  - slack_app.py         (handler registration)
  - autonomous/scheduler.py (job dispatch guards)

Core features (research, web search) are always enabled.
Optional features require specific env vars or credential files.
"""

import os
from pathlib import Path

# Resolve project root relative to this file (agents_utils/ → project root)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def is_feature_enabled(feature_name: str) -> bool:
    """
    Return True if the named integration is fully configured.

    feature_name values:
        "google"    — Google Workspace (Gmail, Calendar, Docs, Sheets)
                      Requires Google_creds.json in project root.
        "linkedin"  — LinkedIn posting
                      Requires LINKEDIN_CLIENT_ID + LINKEDIN_CLIENT_SECRET env vars.
        "email"     — Direct SMTP email sending
                      Requires HUMAN_EMAIL + EMAIL_PASS env vars.
        anything else → True (treated as always-on / core feature)
    """
    if feature_name == "google":
        return (_PROJECT_ROOT / "Google_creds.json").exists()

    if feature_name == "linkedin":
        return bool(
            os.getenv("LINKEDIN_CLIENT_ID") and os.getenv("LINKEDIN_CLIENT_SECRET")
        )

    if feature_name == "email":
        return bool(os.getenv("HUMAN_EMAIL") and os.getenv("EMAIL_PASS"))

    # Unknown feature name → treat as core / always enabled
    return True


def get_missing_features() -> list[str]:
    """Return list of optional features that are NOT configured."""
    missing = []
    for feature in ("google", "linkedin", "email"):
        if not is_feature_enabled(feature):
            missing.append(feature)
    return missing
