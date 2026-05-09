"""
Gemma Swarm — Docs API
=======================
Google Docs API functions for creating, reading, and updating documents.
Uses OAuth helpers from google_api.py.

docs_create           — creates a doc with plain text
docs_create_formatted — creates a doc with proper heading/bold/bullet/code/table formatting
docs_read             — reads a doc and returns plain text
docs_update           — replaces all content with plain text
docs_update_formatted — replaces all content with proper formatting applied

Formatting is delegated to agents_utils/docs_parser.py which parses markdown
into a list of segments (lines blocks and table blocks).
"""

import logging
import re
import requests as req

from tools.google_api import _get_access_token, _auth_headers
from agents_utils.docs_parser import parse_content

logger = logging.getLogger(__name__)


# ── Plain text create / read / update ─────────────────────────────────────────

def docs_create(title: str, content: str, slack_post_fn=None) -> dict:
    """
    Create a new Google Doc with content.

    Auto-detects markdown formatting markers and uses formatted insertion if
    detected. Otherwise uses plain text insertion.
    """
    has_markdown = any([
        re.search(r'^#+\s',       content, re.MULTILINE),
        re.search(r'^[-•]\s',     content, re.MULTILINE),
        re.search(r'^\d+\.\s',    content, re.MULTILINE),
        re.search(r'\*\*.*?\*\*', content),
        re.search(r'\*[^*]+\*',   content),
        re.search(r'```',         content),
        re.search(r'^\|.+\|',     content, re.MULTILINE),
    ])

    if has_markdown:
        return docs_create_formatted(title, content, slack_post_fn)

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
        req.post(
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
    """Replace all content in a Google Doc with new plain text."""
    token = _get_access_token(slack_post_fn)

    response = req.get(
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

    req.post(
        f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"requests": batch_requests},
        timeout=15,
    ).raise_for_status()

    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    logger.info(f"[google/docs] Updated: {doc_id}")
    return {"id": doc_id, "title": doc.get("title", ""), "link": link}


# ── Formatted create / update ──────────────────────────────────────────────────

def docs_create_formatted(title: str, content: str, slack_post_fn=None) -> dict:
    """
    Create a new Google Doc with full markdown formatting applied.

    Supported:
    - Headings (#, ##, ###, ####)
    - Bullets (- / •) with arbitrary nesting via indentation (2 spaces = 1 level)
    - Numbered lists (1. 2. ...) with nesting
    - Inline bold (**text**), italic (*text* / _text_), strikethrough (~~text~~)
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
    logger.info(f"[google/docs] Created formatted doc: {title}")
    return {"id": doc_id, "title": title, "link": link}


def docs_update_formatted(doc_id: str, new_content: str, slack_post_fn=None) -> dict:
    """
    Replace all content in a Google Doc with new content, applying full formatting.
    """
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
    logger.info(f"[google/docs] Updated formatted doc: {doc_id}")
    return {"id": doc_id, "title": doc.get("title", ""), "link": link}


# ── Core formatting engine ─────────────────────────────────────────────────────

def _apply_formatted_content(doc_id: str, token: str, content: str) -> None:
    """
    Parse content into segments and apply them to the doc in order.

    For each "lines" segment:
      1. Fetch doc end index (= where the block will start).
      2. Build the full insertion string for the block and insert it in ONE
         request so absolute indices are known precisely.
      3. Apply all formatting requests using those indices.

    For "table" segments: insert → fetch cell indices → fill cells.
    """
    segments = parse_content(content)

    for segment in segments:
        if segment["kind"] == "lines":
            lines = segment["lines"]
            if not lines:
                continue

            # Step 1: where does this block start?
            block_start = _get_doc_end_index(doc_id, token)

            # Step 2: build full text and insert in one request
            full_text, line_spans = _build_block_text(lines)
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

            # Step 3: format using exact absolute positions
            format_requests = _build_format_requests(lines, line_spans, block_start)
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


# ── Block text builder ─────────────────────────────────────────────────────────

def _build_block_text(lines: list[dict]) -> tuple[str, list[dict]]:
    """
    Concatenate all lines into one string for a single insertText call.

    Returns:
      full_text  — the complete string to insert (tabs + text + newlines)
      line_spans — list of {tab_count, text_start_offset, text_end_offset}
                   giving each line's positions as offsets from the block start,
                   BEFORE createParagraphBullets strips the tabs.

    Offsets are relative to the start of full_text (i.e. add block_start_index
    to get absolute document indices).
    """
    parts      = []
    line_spans = []
    offset     = 0

    for line in lines:
        indent       = line.get("indent", 0)
        is_list_item = line["type"] in ("bullet", "bullet_bold_title", "numbered")
        tab_count    = indent if is_list_item else 0
        prefix       = "\t" * tab_count
        text         = line["text"]
        full_line    = prefix + text + "\n"

        line_spans.append({
            "tab_count":         tab_count,
            "line_start_offset": offset,
            "text_start_offset": offset + tab_count,
            "text_end_offset":   offset + tab_count + len(text),
            "line_end_offset":   offset + tab_count + len(text),  # excludes \n
        })

        parts.append(full_line)
        offset += len(full_line)

    return "".join(parts), line_spans


# ── Format request builder ─────────────────────────────────────────────────────

def _build_format_requests(lines: list[dict], line_spans: list[dict],
                           block_start: int) -> list[dict]:
    """
    Build all formatting requests for a lines block.

    Uses line_spans (offsets from _build_block_text) + block_start to compute
    absolute document indices.

    Key insight for nested lists: createParagraphBullets strips leading tabs
    and shifts all subsequent indices. To avoid this, we group all contiguous
    list items of the same type into ONE createParagraphBullets request
    covering the whole group. Within a group the tabs haven't been stripped
    yet, so our offsets are still valid. After the group request fires,
    tabs are gone — but we've already computed all offsets up front from the
    original full_text layout, so nothing breaks.

    We emit: heading/bold/code/inline styles first, then all bullet groups,
    then all numbered groups — so createParagraphBullets fires after all
    index-sensitive style requests.
    """
    style_requests  = []   # heading, bold_line, code_block, inline styles
    bullet_groups   = []   # [{start, end}] one per contiguous bullet run
    numbered_groups = []   # [{start, end}] one per contiguous numbered run

    # Track contiguous list runs
    cur_bullet_start   = None
    cur_bullet_end     = None
    cur_numbered_start = None
    cur_numbered_end   = None

    def flush_bullet():
        if cur_bullet_start is not None:
            bullet_groups.append({"start": cur_bullet_start, "end": cur_bullet_end})

    def flush_numbered():
        if cur_numbered_start is not None:
            numbered_groups.append({"start": cur_numbered_start, "end": cur_numbered_end})

    for i, (line, span) in enumerate(zip(lines, line_spans)):
        line_type  = line["type"]
        abs_line_s = block_start + span["line_start_offset"]
        abs_line_e = block_start + span["line_end_offset"]
        abs_text_s = block_start + span["text_start_offset"]
        abs_text_e = block_start + span["text_end_offset"]
        text_len   = abs_text_e - abs_text_s

        is_bullet   = line_type in ("bullet", "bullet_bold_title")
        is_numbered = line_type == "numbered"

        # ── Flush list runs when type changes ─────────────────────────────
        if not is_bullet and cur_bullet_start is not None:
            flush_bullet()
            cur_bullet_start = cur_bullet_end = None

        if not is_numbered and cur_numbered_start is not None:
            flush_numbered()
            cur_numbered_start = cur_numbered_end = None

        # ── Per-line style requests ────────────────────────────────────────
        if line_type == "heading1":
            style_requests.append(_para_style(abs_line_s, abs_line_e, "HEADING_1"))

        elif line_type == "heading2":
            style_requests.append(_para_style(abs_line_s, abs_line_e, "HEADING_2"))

        elif line_type == "heading3":
            style_requests.append(_para_style(abs_line_s, abs_line_e, "HEADING_3"))

        elif line_type == "bold_line":
            style_requests.append(_text_style(abs_line_s, abs_line_e, bold=True))

        elif line_type == "code_block":
            style_requests.append({
                "updateTextStyle": {
                    "range":     {"startIndex": abs_line_s, "endIndex": abs_line_e},
                    "textStyle": {
                        "weightedFontFamily": {"fontFamily": "Courier New"},
                        "fontSize":           {"magnitude": 10, "unit": "PT"},
                        "backgroundColor": {
                            "color": {
                                "rgbColor": {"red": 0.937, "green": 0.937, "blue": 0.937}
                            }
                        },
                    },
                    "fields": "weightedFontFamily,fontSize,backgroundColor",
                }
            })

        elif is_bullet:
            # Extend or start bullet run
            if cur_bullet_start is None:
                cur_bullet_start = abs_line_s
            cur_bullet_end = abs_line_e

            # bold title
            if line_type == "bullet_bold_title":
                bold_end = line.get("bold_end", text_len)
                style_requests.append(_text_style(abs_text_s, abs_text_s + bold_end, bold=True))

            # inline markup
            segs = line.get("segments")
            if segs:
                style_requests.extend(_inline_style_requests(abs_text_s, segs))

        elif is_numbered:
            if cur_numbered_start is None:
                cur_numbered_start = abs_line_s
            cur_numbered_end = abs_line_e

            segs = line.get("segments")
            if segs:
                style_requests.extend(_inline_style_requests(abs_text_s, segs))

        elif line_type == "normal":
            segs = line.get("segments")
            if segs:
                style_requests.extend(_inline_style_requests(abs_text_s, segs))

    # Flush any open list runs at end of block
    flush_bullet()
    flush_numbered()

    # Build bullet/numbered group requests
    list_requests = []
    for g in bullet_groups:
        list_requests.append({
            "createParagraphBullets": {
                "range":        {"startIndex": g["start"], "endIndex": g["end"]},
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }
        })
    for g in numbered_groups:
        list_requests.append({
            "createParagraphBullets": {
                "range":        {"startIndex": g["start"], "endIndex": g["end"]},
                "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN",
            }
        })

    # Style requests first (indices still valid), list requests last
    return style_requests + list_requests


# ── Helpers ────────────────────────────────────────────────────────────────────

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


# ── Table insertion ────────────────────────────────────────────────────────────

def _insert_table(doc_id: str, token: str, table_seg: dict) -> None:
    """
    Insert a markdown table segment as a native Google Docs table.

    Process:
      1. Fetch current end index so we know where to insert.
      2. insertTable (creates empty rows x cols table).
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
    cell_indices = _extract_table_cell_indices(response.json(), insert_at)

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
        bold_requests.append(_text_style(cell_start, cell_start + len(header_text), bold=True))

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


def _extract_table_cell_indices(doc: dict, insert_at: int) -> list[int]:
    """
    Walk the document body to find the table inserted near insert_at and
    return the insertion index for each cell in row-major order.
    """
    body_content = doc.get("body", {}).get("content", [])
    target_table = None

    for element in body_content:
        if "table" not in element:
            continue
        if abs(element.get("startIndex", 0) - insert_at) <= 2:
            target_table = element["table"]
            break

    if target_table is None:
        for element in reversed(body_content):
            if "table" in element:
                target_table = element["table"]
                break

    if target_table is None:
        return []

    cell_indices = []
    for table_row in target_table.get("tableRows", []):
        for cell in table_row.get("tableCells", []):
            cell_content = cell.get("content", [])
            if cell_content:
                para_start = cell_content[0].get("startIndex")
                if para_start is not None:
                    cell_indices.append(para_start)
    return cell_indices


# ── Style helper functions ─────────────────────────────────────────────────────

def _para_style(start: int, end: int, named_style: str) -> dict:
    return {
        "updateParagraphStyle": {
            "range":          {"startIndex": start, "endIndex": end},
            "paragraphStyle": {"namedStyleType": named_style},
            "fields":         "namedStyleType",
        }
    }


def _text_style(start: int, end: int, bold: bool = False, italic: bool = False,
                strikethrough: bool = False) -> dict:
    style  = {}
    fields = []
    if bold:
        style["bold"] = True
        fields.append("bold")
    if italic:
        style["italic"] = True
        fields.append("italic")
    if strikethrough:
        style["strikethrough"] = True
        fields.append("strikethrough")
    return {
        "updateTextStyle": {
            "range":     {"startIndex": start, "endIndex": end},
            "textStyle": style,
            "fields":    ",".join(fields),
        }
    }


def _inline_style_requests(base_index: int, segments: list[dict]) -> list[dict]:
    """Build updateTextStyle requests for inline segments starting at base_index."""
    requests_list = []
    cursor = base_index
    for seg in segments:
        seg_len = len(seg["text"])
        if seg_len > 0 and (seg.get("bold") or seg.get("italic") or seg.get("strikethrough")):
            requests_list.append(
                _text_style(
                    cursor, cursor + seg_len,
                    bold=seg.get("bold", False),
                    italic=seg.get("italic", False),
                    strikethrough=seg.get("strikethrough", False),
                )
            )
        cursor += seg_len
    return requests_list


# ── Plain text extractor ───────────────────────────────────────────────────────

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
