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
    """
    Create a new Google Doc with content.
    
    Auto-detects markdown formatting markers (headings, lists, bold, code blocks)
    and uses formatted insertion if detected. Otherwise uses plain text insertion.
    """
    # Auto-detect markdown in content
    has_markdown = any([
        re.search(r'^#+\s', content, re.MULTILINE),  # Headings
        re.search(r'^[-•]\s', content, re.MULTILINE),  # Bullets
        re.search(r'^\d+\.\s', content, re.MULTILINE),  # Numbered lists
        re.search(r'\*\*.*?\*\*', content),  # Bold
        re.search(r'\*[^*]+\*', content),  # Italic
        re.search(r'```', content),  # Code blocks
    ])
    
    # If markdown detected, use formatted creation
    if has_markdown:
        return docs_create_formatted(title, content, slack_post_fn)
    
    # Otherwise use plain text
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

    Process:
    1. Creates an empty doc
    2. Inserts all content
    3. Applies formatting (headings, bold, bullets, etc.)
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

    # Step 3: Build and execute insertion requests
    insertion_requests = _build_insertion_requests(lines)
    
    if insertion_requests:
        requests.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={"requests": insertion_requests},
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
    logger.info(f"[google/docs] Created formatted doc: {title}")
    return {"id": doc_id, "title": title, "link": link}


def _strip_markdown_links(text: str) -> str:
    """
    Convert markdown links [label](url) to plain label text.
    Also strips bare angle-bracket links <url> and reference-style links.
    """
    # [label](url) → label
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # <url> → url
    text = re.sub(r'<(https?://[^>]+)>', r'\1', text)
    return text


def _parse_inline_segments(text: str) -> list[dict]:
    """
    Split a line of text into segments, each with bold/italic flags.
    Handles: **bold**, *italic*, ***bold+italic*** combinations.
    Returns list of {text, bold, italic} dicts.
    """
    segments = []
    # Order matters: *** before ** before *
    pattern  = re.compile(r'(\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|\*([^*].+?[^*]|[^*])\*)')
    last_end = 0
    for m in pattern.finditer(text):
        if m.start() > last_end:
            segments.append({"text": text[last_end:m.start()], "bold": False, "italic": False})
        if m.group(2):  # ***text***
            segments.append({"text": m.group(2), "bold": True,  "italic": True})
        elif m.group(3):  # **text**
            segments.append({"text": m.group(3), "bold": True,  "italic": False})
        elif m.group(4):  # *text* (single star, not double)
            segments.append({"text": m.group(4), "bold": False, "italic": True})
        last_end = m.end()
    if last_end < len(text):
        segments.append({"text": text[last_end:], "bold": False, "italic": False})
    return segments if segments else [{"text": text, "bold": False, "italic": False}]


def _strip_inline_markers(text: str) -> str:
    """Strip all inline markdown markers (**bold**, *italic*) leaving plain text."""
    segs = _parse_inline_segments(text)
    return "".join(s["text"] for s in segs)


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
    raw_lines    = content.split("\n")
    lines        = []
    i            = 0
    in_code_block = False

    while i < len(raw_lines):
        raw_line = raw_lines[i]
        stripped = raw_line.rstrip()

        # Fenced code block toggle (``` or ```python etc)
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            i += 1
            continue

        if in_code_block:
            lines.append({"text": raw_line.rstrip("\r"), "type": "code_block"})
            i += 1
            continue

        # Check for markdown table block — table parsing removed
        # (Previously supported markdown tables; now treated as normal text)

        # #### Heading 4 / ##### Heading 5 — collapse to Heading 3
        if re.match(r'^#{4,}\s', stripped):
            text = _strip_markdown_links(re.sub(r'^#{4,}\s+', '', stripped).strip())
            lines.append({"text": text, "type": "heading3"})

        # ### Heading 3 (must check before ## and #)
        elif stripped.startswith("### "):
            lines.append({"text": _strip_markdown_links(stripped[4:].strip()), "type": "heading3"})

        # ## Heading 2
        elif stripped.startswith("## "):
            lines.append({"text": _strip_markdown_links(stripped[3:].strip()), "type": "heading2"})

        # # Heading 1
        elif stripped.startswith("# "):
            lines.append({"text": _strip_markdown_links(stripped[2:].strip()), "type": "heading1"})

        # DRAFT N: pattern
        elif re.match(r"^DRAFT\s+\d+\s*:", stripped, re.IGNORECASE):
            lines.append({"text": _strip_markdown_links(stripped), "type": "heading2"})

        # Bullet list item
        elif stripped.startswith("- ") or stripped.startswith("• "):
            text = _strip_markdown_links(stripped[2:].strip())

            # Check for bold title prefix: **Title:** rest of text
            bold_prefix_match = re.match(r"^\*\*(.+?)\*\*[:\-]?\s*", text)
            if bold_prefix_match:
                bold_part  = bold_prefix_match.group(1).rstrip(":- ")
                after_bold = text[bold_prefix_match.end():]
                plain = f"{bold_part}: {after_bold}" if after_bold else bold_part
                lines.append({
                    "text":     plain,
                    "type":     "bullet_bold_title",
                    "bold_end": len(bold_part) + 2,
                })
            else:
                clean = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
                lines.append({"text": clean, "type": "bullet"})

        # Numbered list item (1. 2. etc)
        elif re.match(r"^\d+\.\s", stripped):
            text = _strip_markdown_links(re.sub(r"^\d+\.\s+", "", stripped))
            segments = _parse_inline_segments(text)
            plain    = "".join(s["text"] for s in segments)
            if any(s["bold"] or s["italic"] for s in segments):
                lines.append({"text": plain, "type": "numbered", "segments": segments})
            else:
                lines.append({"text": plain, "type": "numbered"})

        # Whole line is bold (**text**)
        elif stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4:
            lines.append({"text": _strip_markdown_links(stripped[2:-2]), "type": "bold_line"})

        # Empty line
        elif stripped == "":
            lines.append({"text": "", "type": "normal"})

        # Normal paragraph
        else:
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
            clean = _strip_markdown_links(clean)
            lines.append({"text": clean, "type": "normal"})

        i += 1

    return lines


def _build_insertion_requests(lines: list[dict]) -> list[dict]:
    """
    Build Google Docs API requests to insert all content.
    """
    requests_list = []
    index = 1

    for line in lines:
        text = line["text"]
        length = len(text)
        if length > 0:
            requests_list.append({
                "insertText": {
                    "location": {"index": index},
                    "text": text
                }
            })
            index += length

        # Newline after each line
        requests_list.append({
            "insertText": {
                "location": {"index": index},
                "text": "\n"
            }
        })
        index += 1

    return requests_list


def _build_format_requests(lines: list[dict]) -> list[dict]:
    """
    Build Google Docs API batchUpdate requests to apply formatting.
    We track character positions as we go through the lines.
    Index starts at 1 (Google Docs indexes from 1, not 0).
    """
    requests_list = []
    index = 1  # current character position in the doc

    for line in lines:
        text      = line["text"]
        line_type = line["type"]

        length = len(text)
        if length == 0:
            index += 1  # newline character
            continue

        end_index = index + length

        if line_type == "heading1":
            requests_list.append({
                "updateParagraphStyle": {
                    "range":          {"startIndex": index, "endIndex": end_index},
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "fields":         "namedStyleType",
                }
            })

        elif line_type == "heading2":
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
            # Apply inline bold/italic if segments were parsed
            segments = line.get("segments")
            if segments:
                seg_cursor = index
                for seg in segments:
                    seg_len = len(seg["text"])
                    if seg_len > 0 and (seg["bold"] or seg["italic"]):
                        style = {}
                        fields = []
                        if seg["bold"]:
                            style["bold"] = True
                            fields.append("bold")
                        if seg["italic"]:
                            style["italic"] = True
                            fields.append("italic")
                        requests_list.append({
                            "updateTextStyle": {
                                "range":     {"startIndex": seg_cursor, "endIndex": seg_cursor + seg_len},
                                "textStyle": style,
                                "fields":    ",".join(fields),
                            }
                        })
                    seg_cursor += seg_len

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
    Same formatting rules as docs_create_formatted.
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

    # Step 3: Parse and build insertion requests
    lines = _parse_lines(new_content)
    insertion_requests = _build_insertion_requests(lines)
    
    if insertion_requests:
        requests.post(
            f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={"requests": insertion_requests},
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