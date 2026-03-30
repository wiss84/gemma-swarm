"""
Gemma Swarm — Sheets API
========================
Google Sheets API functions for creating, reading, and updating spreadsheets.
Uses OAuth helpers from google_api.py.
"""

import logging
import requests

# Import shared auth helpers from google_api
from tools.google_api import _get_access_token, _auth_headers

logger = logging.getLogger(__name__)


def sheets_create(title: str, rows: list[list], slack_post_fn=None) -> dict:
    """
    Create a new Google Sheet.
    title: Spreadsheet title
    rows: Initial data as list of rows (optional)
    """
    token = _get_access_token(slack_post_fn)

    response = requests.post(
        "https://sheets.googleapis.com/v4/spreadsheets",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"properties": {"title": title}},
        timeout=15,
    )
    response.raise_for_status()
    sheet    = response.json()
    sheet_id = sheet["spreadsheetId"]

    if rows:
        requests.put(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/Sheet1!A1",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            params={"valueInputOption": "USER_ENTERED"},
            json={"values": rows},
            timeout=15,
        ).raise_for_status()

    link = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    logger.info(f"[google/sheets] Created: {title}")
    return {"id": sheet_id, "title": title, "link": link}


def sheets_read(sheet_id: str, range_: str = "Sheet1", slack_post_fn=None) -> dict:
    """
    Read data from a Google Sheet.
    sheet_id: The spreadsheet ID (from the URL)
    range_: The cell range to read (default: "Sheet1")
    """
    token    = _get_access_token(slack_post_fn)
    response = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{range_}",
        headers=_auth_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()

    meta = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}",
        headers=_auth_headers(token),
        params={"fields": "properties.title"},
        timeout=15,
    ).json()

    return {
        "id":     sheet_id,
        "title":  meta.get("properties", {}).get("title", ""),
        "values": data.get("values", []),
        "link":   f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit",
    }


def sheets_update(
    sheet_id: str,
    range_: str,
    values: list[list],
    slack_post_fn=None,
) -> dict:
    """
    Update data in a Google Sheet.
    sheet_id: The spreadsheet ID
    range_: The cell range to update (e.g., "Sheet1!A1:B2")
    values: 2D array of values to write
    """
    token    = _get_access_token(slack_post_fn)
    response = requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{range_}",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        params={"valueInputOption": "USER_ENTERED"},
        json={"values": values},
        timeout=15,
    )
    response.raise_for_status()
    link = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    logger.info(f"[google/sheets] Updated: {sheet_id} range {range_}")
    return {"id": sheet_id, "range": range_, "link": link}
