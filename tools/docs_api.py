"""
Gemma Swarm — Docs API
=======================
Google Docs API functions for creating, reading, and updating documents.
Uses OAuth helpers from google_api.py.
"""

import logging
import requests

# Import shared auth helpers from google_api
from tools.google_api import _get_access_token, _auth_headers

logger = logging.getLogger(__name__)


def docs_create(title: str, content: str, slack_post_fn=None) -> dict:
    """
    Create a new Google Doc.
    title: Document title
    content: Initial content (optional)
    """
    token = _get_access_token(slack_post_fn)

    response = requests.post(
        "https://docs.googleapis.com/v1/documents",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"title": title},
        timeout=15,
    )
    response.raise_for_status()
    doc    = response.json()
    doc_id = doc["documentId"]

    if content:
        requests.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]},
            timeout=15,
        ).raise_for_status()

    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    logger.info(f"[google/docs] Created: {title}")
    return {"id": doc_id, "title": title, "link": link}


def docs_read(doc_id: str, slack_post_fn=None) -> dict:
    """
    Read the content of a Google Doc.
    doc_id: The document ID (from the URL)
    """
    token    = _get_access_token(slack_post_fn)
    response = requests.get(
        f"https://docs.googleapis.com/v1/documents/{doc_id}",
        headers=_auth_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    doc     = response.json()
    content = _extract_docs_text(doc)
    return {
        "id":      doc_id,
        "title":   doc.get("title", ""),
        "content": content,
        "link":    f"https://docs.google.com/document/d/{doc_id}/edit",
    }


def docs_update(doc_id: str, new_content: str, slack_post_fn=None) -> dict:
    """
    Update a Google Doc by replacing its content.
    doc_id: The document ID
    new_content: New content to replace existing content
    """
    token = _get_access_token(slack_post_fn)

    response = requests.get(
        f"https://docs.googleapis.com/v1/documents/{doc_id}",
        headers=_auth_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    doc       = response.json()
    end_index = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)

    batch_requests = []
    if end_index > 2:
        batch_requests.append({
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": end_index - 1}
            }
        })
    batch_requests.append({
        "insertText": {"location": {"index": 1}, "text": new_content}
    })

    requests.post(
        f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"requests": batch_requests},
        timeout=15,
    ).raise_for_status()

    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    logger.info(f"[google/docs] Updated: {doc_id}")
    return {"id": doc_id, "title": doc.get("title", ""), "link": link}


def _extract_docs_text(doc: dict) -> str:
    """
    Extract plain text from a Google Doc document structure.
    """
    text = []
    for block in doc.get("body", {}).get("content", []):
        para = block.get("paragraph")
        if not para:
            continue
        for element in para.get("elements", []):
            run = element.get("textRun")
            if run:
                text.append(run.get("content", ""))
    return "".join(text)
