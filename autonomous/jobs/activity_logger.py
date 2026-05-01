"""
Gemma Swarm — Autonomous Activity Logger
==========================================
Appends one row per action to the global activity log Google Sheet.
Called by all other autonomous jobs whenever they do something.
Creates the sheet on first call if it doesn't exist yet.
Zero LLM calls.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SHEET_TITLE   = "Gemma Swarm — Autonomous Activity Log"
SHEET_HEADERS = [["Timestamp", "Job", "Description", "Status"]]


def log(job: str, description: str, status: str):
    """
    Append one row to the activity log Google Sheet.
    Creates the sheet if it doesn't exist yet and saves the ID to settings.

    job:         e.g. "email_watcher", "researcher", "calendar_reminder"
    description: e.g. "New email from boss@company.com — Q3 Report"
    status:      e.g. "✅", "❌", "⏭️ skipped"
    """
    from autonomous.settings import load_settings, save_settings

    settings = load_settings()
    sheet_id = settings.get("activity_log_sheet_id", "")

    try:
        from tools.sheets_api import sheets_create, sheets_update

        # Create sheet on first call
        if not sheet_id:
            sheet    = sheets_create(title=SHEET_TITLE, rows=SHEET_HEADERS)
            sheet_id = sheet["id"]
            settings["activity_log_sheet_id"] = sheet_id
            save_settings(settings)
            logger.info(f"[activity_logger] Created activity log sheet: {sheet_id}")

        # Append new row
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        row       = [[timestamp, job, description, status]]

        # Find next empty row by appending — use a large row number offset
        # Google Sheets API append action handles this cleanly
        _append_row(sheet_id, row)
        logger.info(f"[activity_logger] Logged: [{job}] {description} {status}")

    except Exception as e:
        logger.error(f"[activity_logger] Failed to log activity: {e}")


def _append_row(sheet_id: str, row: list[list]):
    """Append a row to the sheet using the values append API endpoint."""
    from tools.google_api import _get_access_token, _auth_headers
    import requests

    try:
        token    = _get_access_token()
        response = requests.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/Sheet1:append",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            params={
                "valueInputOption":     "USER_ENTERED",
                "insertDataOption":     "INSERT_ROWS",
            },
            json={"values": row},
            timeout=15,
        )
        response.raise_for_status()
    except Exception as e:
        logger.error(f"[activity_logger] Append row failed: {e}")


def read_recent_logs(hours: int = 24) -> list[dict]:
    """
    Read activity log rows from the last N hours.
    Returns list of dicts with keys: timestamp, job, description, status.
    Used by daily_summary job.
    """
    from autonomous.settings import load_settings
    from tools.sheets_api import sheets_read

    settings = load_settings()
    sheet_id = settings.get("activity_log_sheet_id", "")

    if not sheet_id:
        return []

    try:
        sheet  = sheets_read(sheet_id, range_="Sheet1")
        values = sheet.get("values", [])

        if len(values) <= 1:  # Only headers or empty
            return []

        cutoff = datetime.now().timestamp() - (hours * 3600)
        rows   = []

        for row in values[1:]:  # Skip header row
            if len(row) < 4:
                continue
            try:
                ts = datetime.strptime(row[0], "%Y-%m-%d %H:%M").timestamp()
                if ts >= cutoff:
                    rows.append({
                        "timestamp":   row[0],
                        "job":         row[1],
                        "description": row[2],
                        "status":      row[3],
                    })
            except ValueError:
                continue

        return rows

    except Exception as e:
        logger.error(f"[activity_logger] Could not read logs: {e}")
        return []
