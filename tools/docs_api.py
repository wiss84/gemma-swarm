"""
Gemma Swarm — Docs API
=======================
Google Docs API functions for creating, reading, and updating documents.
Uses OAuth helpers from google_api.py.

All formatting logic lives in agents_utils/docs_parser.py.
This module only handles HTTP calls and wiring.

Public functions
----------------
  docs_create(title, content, slack_post_fn)       — create a new doc
  docs_read(doc_id, slack_post_fn)                 — read a doc
  docs_update(doc_id, new_content, slack_post_fn)  — replace all content
"""

import logging
import requests as req

from tools.google_api import _get_access_token, _auth_headers
from agents_utils.docs_parser import (
    parse_content,
    build_block_text,
    build_format_requests,
    text_style,
    extract_table_cell_indices,
)

logger = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def docs_create(title: str, content: str, slack_post_fn=None) -> dict:
    """
    Create a new Google Doc with full markdown formatting applied.

    Supported:
    - Headings (#, ##, ###, ####)
    - Bullets (- / •) with arbitrary nesting via indentation (2 spaces = 1 level)
    - Numbered lists (1. 2. ...) with nesting
    - Inline bold (**text**), italic (*text* / _text_), strikethrough (~~text~~)
    - LaTeX math ($\\rightarrow$ etc.) converted to Unicode
    - Code blocks (``` fenced)
    - Markdown tables → native Google Docs tables
    """
    token = _get_access_token(slack_post_fn)

    response = req.post(
        "https://docs.googleapis.com/v1/documents",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"title": title},
        timeout=15,
    )
    response.raise_for_status()
    doc    = response.json()
    doc_id = doc["documentId"]

    if content:
        _apply_formatted_content(doc_id, token, content)

    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    logger.info(f"[google/docs] Created: {title}")
    return {"id": doc_id, "title": title, "link": link}


def docs_read(doc_id: str, slack_post_fn=None) -> dict:
    """Read the content of a Google Doc and return plain text."""
    token    = _get_access_token(slack_post_fn)
    response = req.get(
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
    """Replace all content in a Google Doc with new content, fully formatted."""
    token = _get_access_token(slack_post_fn)

    response = req.get(
        f"https://docs.googleapis.com/v1/documents/{doc_id}",
        headers=_auth_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    doc       = response.json()
    end_index = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)

    if end_index > 2:
        req.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={"requests": [{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end_index - 1}}}]},
            timeout=15,
        ).raise_for_status()

    if new_content:
        _apply_formatted_content(doc_id, token, new_content)

    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    logger.info(f"[google/docs] Updated: {doc_id}")
    return {"id": doc_id, "title": doc.get("title", ""), "link": link}


# ── Core formatting engine ────────────────────────────────────────────────────

def _apply_formatted_content(doc_id: str, token: str, content: str) -> None:
    """
    Parse content into segments and apply them to the doc in order.

    For each "lines" segment:
      1. Fetch doc end index (= where this block will start).
      2. Build the full insertion string and insert in ONE request so absolute
         indices are known precisely.
      3. Apply all formatting requests using those indices.

    For "table" segments: insert table → fetch cell indices → fill cells.
    """
    segments = parse_content(content)

    for segment in segments:
        if segment["kind"] == "lines":
            lines = segment["lines"]
            if not lines:
                continue

            block_start           = _get_doc_end_index(doc_id, token)
            full_text, line_spans = build_block_text(lines)

            if full_text:
                r = req.post(
                    f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
                    headers={**_auth_headers(token), "Content-Type": "application/json"},
                    json={"requests": [{
                        "insertText": {
                            "endOfSegmentLocation": {"segmentId": ""},
                            "text": full_text,
                        }
                    }]},
                    timeout=15,
                )
                if not r.ok:
                    logger.error(f"[google/docs] insertText error {r.status_code}: {r.text}")
                    r.raise_for_status()

            format_requests = build_format_requests(lines, line_spans, block_start)
            if format_requests:
                r = req.post(
                    f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
                    headers={**_auth_headers(token), "Content-Type": "application/json"},
                    json={"requests": format_requests},
                    timeout=15,
                )
                if not r.ok:
                    logger.error(f"[google/docs] formatRequests error {r.status_code}: {r.text}")
                    logger.error(f"[google/docs] failed format_requests={format_requests}")
                    r.raise_for_status()

        elif segment["kind"] == "table":
            _insert_table(doc_id, token, segment)


# ── Table insertion ───────────────────────────────────────────────────────────

def _insert_table(doc_id: str, token: str, table_seg: dict) -> None:
    """
    Insert a markdown table segment as a native Google Docs table.

    Process:
      1. Fetch current end index so we know where to insert.
      2. insertTable (creates empty rows × cols table).
      3. Fetch the updated doc to discover each cell's startIndex.
      4. Insert text into each cell via insertText requests (reverse order).
      5. Bold the header row.
    """
    headers  = table_seg["headers"]
    rows     = table_seg["rows"]
    num_cols = len(headers)
    all_rows = [headers] + rows
    num_rows = len(all_rows)

    if num_rows == 0 or num_cols == 0:
        return

    end_index = _get_doc_end_index(doc_id, token)
    insert_at = max(end_index - 1, 1)

    r = req.post(
        f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"requests": [{"insertTable": {"rows": num_rows, "columns": num_cols, "location": {"index": insert_at}}}]},
        timeout=15,
    )
    if not r.ok:
        logger.error(f"[google/docs] insertTable error {r.status_code}: {r.text}")
        r.raise_for_status()

    response = req.get(
        f"https://docs.googleapis.com/v1/documents/{doc_id}",
        headers=_auth_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    cell_indices = extract_table_cell_indices(response.json(), insert_at)

    if not cell_indices:
        logger.warning("[google/docs] Could not find table cell indices after insertion.")
        return

    insert_requests = []
    for row_i, row_data in enumerate(all_rows):
        for col_i, cell_text in enumerate(row_data):
            flat_index = row_i * num_cols + col_i
            if flat_index >= len(cell_indices) or not cell_text:
                continue
            insert_requests.append({
                "insertText": {
                    "location": {"index": cell_indices[flat_index]},
                    "text":     cell_text,
                }
            })

    if insert_requests:
        insert_requests.sort(key=lambda r: r["insertText"]["location"]["index"], reverse=True)
        r = req.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={"requests": insert_requests},
            timeout=15,
        )
        if not r.ok:
            logger.error(f"[google/docs] fillCells error {r.status_code}: {r.text}")
            r.raise_for_status()

    bold_requests = []
    for col_i, header_text in enumerate(headers):
        if col_i >= len(cell_indices) or not header_text:
            continue
        cell_start = cell_indices[col_i]
        bold_requests.append(text_style(cell_start, cell_start + len(header_text), bold=True))

    if bold_requests:
        r = req.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={"requests": bold_requests},
            timeout=15,
        )
        if not r.ok:
            logger.error(f"[google/docs] boldHeaders error {r.status_code}: {r.text}")
            r.raise_for_status()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_doc_end_index(doc_id: str, token: str) -> int:
    """Fetch the current end index of the document body."""
    response = req.get(
        f"https://docs.googleapis.com/v1/documents/{doc_id}",
        headers=_auth_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    doc = response.json()
    return doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)


def _extract_docs_text(doc: dict) -> str:
    """Extract plain text from a Google Doc document structure."""
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
