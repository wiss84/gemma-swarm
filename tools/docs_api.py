"""
Gemma Swarm — Docs API
=======================
Google Docs API functions for creating, reading, and updating documents.
Uses OAuth helpers from google_api.py.

docs_create           — creates a doc with plain text
docs_create_formatted — creates a doc with proper heading/bold/bullet/code formatting
docs_read             — reads a doc and returns plain text
docs_update           — replaces all content with plain text
docs_update_formatted — replaces all content with proper formatting applied
"""

import logging
import requests
import re

from tools.google_api import _get_access_token, _auth_headers

logger = logging.getLogger(__name__)


# ── Plain text create / read / update ─────────────────────────────────────────

def docs_create(title: str, content: str, slack_post_fn=None) -> dict:
    """Create a new Google Doc with plain text content."""
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
    """Read the content of a Google Doc."""
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
    """Replace all content in a Google Doc with new plain text."""
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


# ── Formatted create ───────────────────────────────────────────────────────────

def docs_create_formatted(title: str, content: str, slack_post_fn=None) -> dict:
    """
    Create a new Google Doc with proper formatting applied.

    Supported markdown-style markers in content:
    - Lines starting with ##   → Heading 2 (bold, large)
    - Lines starting with ###  → Heading 3 (bold, medium)
    - Lines starting with **text** → Bold text (whole line)
    - Lines starting with - or • → Bulleted list item
      - If the bullet text starts with **Title:** the title portion is bolded
        and the rest of the line follows in normal weight.
        Example: "- **Physical World Integration:** Some explanation here."
    - Lines starting with 1. 2. etc → Numbered list item
    - DRAFT 1: / DRAFT 2: etc → Heading 2
    - All other lines → Normal paragraph

    The function:
    1. Creates an empty doc
    2. Inserts all text in one batchUpdate call
    3. Applies formatting in a second batchUpdate call
    """
    token = _get_access_token(slack_post_fn)

    # Step 1: Create empty doc
    response = requests.post(
        "https://docs.googleapis.com/v1/documents",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"title": title},
        timeout=15,
    )
    response.raise_for_status()
    doc    = response.json()
    doc_id = doc["documentId"]

    if not content:
        link = f"https://docs.google.com/document/d/{doc_id}/edit"
        return {"id": doc_id, "title": title, "link": link}

    # Step 2: Parse content into structured lines
    lines = _parse_lines(content)

    # Step 3: Build the full plain text to insert (strip all markers)
    plain_text = "\n".join(line["text"] for line in lines) + "\n"

    requests.post(
        f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"requests": [{"insertText": {"location": {"index": 1}, "text": plain_text}}]},
        timeout=15,
    ).raise_for_status()

    # Step 4: Apply formatting with a second batchUpdate
    format_requests = _build_format_requests(lines)
    if format_requests:
        requests.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={"requests": format_requests},
            timeout=15,
        ).raise_for_status()

    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    logger.info(f"[google/docs] Created formatted doc: {title}")
    return {"id": doc_id, "title": title, "link": link}


def _parse_lines(content: str) -> list[dict]:
    """
    Parse content into a list of structured line dicts.

    Each dict has:
      text       — plain text to insert (stars stripped)
      type       — one of: heading2, heading3, bullet, bullet_bold_title,
                   numbered, bold_line, code_block, normal
      bold_end   — (bullet_bold_title only) character offset within text where
                   the bold portion ends (i.e. length of "Title: ")
    """
    lines         = []
    in_code_block = False

    for raw_line in content.split("\n"):
        stripped = raw_line.rstrip()

        # Fenced code block toggle (``` or ```python etc)
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue  # don't insert the fence line itself

        if in_code_block:
            lines.append({"text": raw_line.rstrip("\r"), "type": "code_block"})
            continue

        # ## Heading 2
        if stripped.startswith("## "):
            lines.append({"text": stripped[3:].strip(), "type": "heading2"})

        # ### Heading 3
        elif stripped.startswith("### "):
            lines.append({"text": stripped[4:].strip(), "type": "heading3"})

        # DRAFT N: pattern
        elif re.match(r"^DRAFT\s+\d+\s*:", stripped, re.IGNORECASE):
            lines.append({"text": stripped, "type": "heading2"})

        # Bullet list item
        elif stripped.startswith("- ") or stripped.startswith("• "):
            text = stripped[2:].strip()

            # Check for bold title prefix: **Title:** rest of text
            # Matches patterns like **Title:** or **Title -** or just **Title**
            bold_prefix_match = re.match(r"^\*\*(.+?)\*\*[:\-]?\s*", text)
            if bold_prefix_match:
                bold_part  = bold_prefix_match.group(1).rstrip(":- ")  # "Physical World Integration"
                after_bold = text[bold_prefix_match.end():]             # "Some explanation here."

                # Reconstruct plain text as "Title: rest" (colon separator if there's a body)
                if after_bold:
                    plain = f"{bold_part}: {after_bold}"
                else:
                    plain = bold_part

                lines.append({
                    "text":     plain,
                    "type":     "bullet_bold_title",
                    "bold_end": len(bold_part) + 2,  # +2 for ": "
                })
            else:
                # Normal bullet — strip any remaining inline ** markers
                clean = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
                lines.append({"text": clean, "type": "bullet"})

        # Numbered list item (1. 2. etc)
        elif re.match(r"^\d+\.\s", stripped):
            text = re.sub(r"^\d+\.\s+", "", stripped)
            lines.append({"text": text, "type": "numbered"})

        # Whole line is bold (**text**)
        elif stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            lines.append({"text": stripped[2:-2], "type": "bold_line"})

        # Empty line
        elif stripped == "":
            lines.append({"text": "", "type": "normal"})

        # Normal paragraph — strip inline ** markers
        else:
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
            lines.append({"text": clean, "type": "normal"})

    return lines


def _build_format_requests(lines: list[dict]) -> list[dict]:
    """
    Build Google Docs API batchUpdate requests to apply formatting.
    We track character positions as we go through the lines.
    Index starts at 1 (Google Docs indexes from 1, not 0).
    """
    requests_list = []
    index         = 1  # current character position in the doc

    for line in lines:
        text      = line["text"]
        line_type = line["type"]
        length    = len(text)

        if length == 0:
            index += 1  # newline character
            continue

        end_index = index + length

        if line_type == "heading2":
            requests_list.append({
                "updateParagraphStyle": {
                    "range":          {"startIndex": index, "endIndex": end_index},
                    "paragraphStyle": {"namedStyleType": "HEADING_2"},
                    "fields":         "namedStyleType",
                }
            })

        elif line_type == "heading3":
            requests_list.append({
                "updateParagraphStyle": {
                    "range":          {"startIndex": index, "endIndex": end_index},
                    "paragraphStyle": {"namedStyleType": "HEADING_3"},
                    "fields":         "namedStyleType",
                }
            })

        elif line_type == "bold_line":
            requests_list.append({
                "updateTextStyle": {
                    "range":     {"startIndex": index, "endIndex": end_index},
                    "textStyle": {"bold": True},
                    "fields":    "bold",
                }
            })

        elif line_type == "bullet":
            requests_list.append({
                "createParagraphBullets": {
                    "range":        {"startIndex": index, "endIndex": end_index},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            })

        elif line_type == "bullet_bold_title":
            # Apply bullet formatting to the whole line
            requests_list.append({
                "createParagraphBullets": {
                    "range":        {"startIndex": index, "endIndex": end_index},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            })
            # Apply bold only to the title portion (up to bold_end)
            bold_end = line.get("bold_end", length)
            requests_list.append({
                "updateTextStyle": {
                    "range":     {"startIndex": index, "endIndex": index + bold_end},
                    "textStyle": {"bold": True},
                    "fields":    "bold",
                }
            })

        elif line_type == "numbered":
            requests_list.append({
                "createParagraphBullets": {
                    "range":        {"startIndex": index, "endIndex": end_index},
                    "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN",
                }
            })

        elif line_type == "code_block":
            requests_list.append({
                "updateTextStyle": {
                    "range": {"startIndex": index, "endIndex": end_index},
                    "textStyle": {
                        "weightedFontFamily": {"fontFamily": "Courier New"},
                        "fontSize":           {"magnitude": 10, "unit": "PT"},
                        "backgroundColor":   {
                            "color": {
                                "rgbColor": {"red": 0.937, "green": 0.937, "blue": 0.937}
                            }
                        },
                    },
                    "fields": "weightedFontFamily,fontSize,backgroundColor",
                }
            })

        # +1 for the newline character after each line
        index = end_index + 1

    return requests_list


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


# ── Formatted update ───────────────────────────────────────────────────────────

def docs_update_formatted(doc_id: str, new_content: str, slack_post_fn=None) -> dict:
    """
    Replace all content in a Google Doc with new content, applying full formatting.
    Same formatting rules as docs_create_formatted:
    ## → Heading 2, ### → Heading 3, - **Title:** → bold bullet title, etc.
    """
    token = _get_access_token(slack_post_fn)

    # Step 1: Get current doc to find end index for deletion
    response = requests.get(
        f"https://docs.googleapis.com/v1/documents/{doc_id}",
        headers=_auth_headers(token),
        timeout=15,
    )
    response.raise_for_status()
    doc       = response.json()
    end_index = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)

    # Step 2: Clear existing content
    clear_requests = []
    if end_index > 2:
        clear_requests.append({
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": end_index - 1}
            }
        })

    if clear_requests:
        requests.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={"requests": clear_requests},
            timeout=15,
        ).raise_for_status()

    if not new_content:
        link = f"https://docs.google.com/document/d/{doc_id}/edit"
        return {"id": doc_id, "title": doc.get("title", ""), "link": link}

    # Step 3: Insert plain text
    lines      = _parse_lines(new_content)
    plain_text = "\n".join(line["text"] for line in lines) + "\n"

    requests.post(
        f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"requests": [{"insertText": {"location": {"index": 1}, "text": plain_text}}]},
        timeout=15,
    ).raise_for_status()

    # Step 4: Apply formatting
    format_requests = _build_format_requests(lines)
    if format_requests:
        requests.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={"requests": format_requests},
            timeout=15,
        ).raise_for_status()

    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    logger.info(f"[google/docs] Updated formatted doc: {doc_id}")
    return {"id": doc_id, "title": doc.get("title", ""), "link": link}