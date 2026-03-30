"""
Gemma Swarm — Calendar API
==========================
Google Calendar API functions for listing, creating, and deleting events.
Uses OAuth helpers from google_api.py.
"""

import logging
import requests
from datetime import datetime

# Import shared auth helpers from google_api
from tools.google_api import _get_access_token, _auth_headers

logger = logging.getLogger(__name__)


def calendar_list_events(
    max_results: int = 10,
    start_date: str = None,
    end_date: str = None,
    slack_post_fn=None,
) -> list[dict]:
    """
    List calendar events in a date range.
    start_date: ISO 8601 e.g. "2026-03-25T00:00:00Z". Defaults to right now.
    end_date:   ISO 8601 e.g. "2026-03-31T23:59:59Z". No upper limit if omitted.
    """
    token    = _get_access_token(slack_post_fn)
    time_min = start_date if start_date else datetime.utcnow().isoformat() + "Z"

    params = {
        "maxResults":   max_results,
        "timeMin":      time_min,
        "singleEvents": True,
        "orderBy":      "startTime",
    }
    if end_date:
        params["timeMax"] = end_date

    response = requests.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers=_auth_headers(token),
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    events = response.json().get("items", [])

    results = []
    for e in events:
        start = e.get("start", {})
        end   = e.get("end", {})
        results.append({
            "id":          e.get("id", ""),
            "title":       e.get("summary", "(no title)"),
            "description": e.get("description", ""),
            "location":    e.get("location", ""),
            "start":       start.get("dateTime") or start.get("date", ""),
            "end":         end.get("dateTime") or end.get("date", ""),
            "link":        e.get("htmlLink", ""),
        })

    logger.info(f"[google/calendar] Listed {len(results)} events.")
    return results


def calendar_get_next_event(slack_post_fn=None) -> dict | None:
    """
    Get the next upcoming calendar event.
    """
    events = calendar_list_events(max_results=1, slack_post_fn=slack_post_fn)
    return events[0] if events else None


def calendar_create_event(
    title: str,
    start_datetime: str,
    end_datetime: str,
    description: str = "",
    location: str = "",
    timezone: str = "UTC",
    slack_post_fn=None,
) -> dict:
    """
    Create a new calendar event.
    title: Event title
    start_datetime: ISO 8601 format e.g. "2026-03-25T14:00:00"
    end_datetime: ISO 8601 format e.g. "2026-03-25T15:00:00"
    description: Event description (optional)
    location: Event location (optional)
    timezone: Timezone (default: UTC)
    """
    token   = _get_access_token(slack_post_fn)
    payload = {
        "summary":     title,
        "description": description,
        "location":    location,
        "start": {"dateTime": start_datetime, "timeZone": timezone},
        "end":   {"dateTime": end_datetime,   "timeZone": timezone},
    }
    response = requests.post(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    event = response.json()
    logger.info(f"[google/calendar] Event created: {title}")
    return {
        "id":    event.get("id", ""),
        "title": event.get("summary", ""),
        "start": event.get("start", {}).get("dateTime", ""),
        "end":   event.get("end", {}).get("dateTime", ""),
        "link":  event.get("htmlLink", ""),
    }


def calendar_delete_event(event_id: str, slack_post_fn=None) -> bool:
    """
    Delete a calendar event by ID.
    Returns True if deleted successfully.
    """
    token    = _get_access_token(slack_post_fn)
    response = requests.delete(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers=_auth_headers(token),
        timeout=15,
    )
    if response.status_code == 204:
        logger.info(f"[google/calendar] Event deleted: {event_id}")
        return True
    response.raise_for_status()
    return False
