"""
Gemma Swarm — Output Formatter Node
======================================
Deterministic node. No LLM call.
Runs last before response is sent to Slack.

Responsibilities:
- Extract final response from state messages
- Convert markdown to Slack mrkdwn format
- Keep code blocks intact
- Detect the first markdown table and convert it to a Slack Block Kit table block
- Split long responses into Slack-safe chunks
- Clean up internal labels before sending to user
- Guard against empty responses that would cause Slack API errors

Table support:
- _convert_markdown_table() lifts the first markdown table out of the text and
  replaces it with SLACK_TABLE_SENTINEL.  The sentinel travels through all
  further processing completely untouched.
- Callers (output_node in graph.py, output_formatter_node here) receive a
  (chunks, table_block) pair.  They include the table_block dict in
  formatted_output so the Slack posting loop can send it via blocks=[].
- formatted_output type: list[str | dict]
    str  → post as text=chunk, mrkdwn=True
    dict → post as blocks=[chunk] (paired with preceding text if present)
"""

import re
import logging
from agents_utils.state import AgentState
from agents_utils.config import LABEL

logger = logging.getLogger(__name__)

SLACK_SAFE_CHARS = 2800

# ── Sentinel ──────────────────────────────────────────────────────────────────
# Inserted where the markdown table was.  Travels through _markdown_to_slack
# and _split_message completely untouched, then lifted out by the caller.
SLACK_TABLE_SENTINEL = "<<SLACK_TABLE>>"

# Regex: full markdown table = header row + separator row + 1+ data rows.
# Uses \r?\n so it matches both Unix (\n) and Windows (\r\n) line endings.
# Trailing spaces/tabs after the closing pipe are consumed by [ \t]*.
_MD_TABLE_RE = re.compile(
    r"(?m)"
    r"(^\|.+\|[ \t]*\r?\n)"                        # header row
    r"(^\|[-:| \t]+\|[ \t]*\r?\n)"                 # separator row
    r"((?:^\|.+\|[ \t]*\r?\n)*^\|.+\|[ \t]*$)",   # data rows: n-1 with \r?\n, last may end at EOL
)


# ── Label stripping ───────────────────────────────────────────────────────────

def _strip_labels(text: str) -> str:
    """Remove all internal agent labels from text before sending to user."""
    for label in LABEL.values():
        text = text.replace(label, "").strip()
    return text


# ── LaTeX arrow conversion ────────────────────────────────────────────────────

def _convert_latex_arrows(text: str) -> str:
    """Convert LaTeX arrow notation to Unicode arrow characters."""
    text = re.sub(r"\$\\rightarrow\$",     "→", text)
    text = re.sub(r"\$\\leftarrow\$",      "←", text)
    text = re.sub(r"\$\\leftrightarrow\$", "↔", text)
    text = re.sub(r"\$\\uparrow\$",        "↑", text)
    text = re.sub(r"\$\\downarrow\$",      "↓", text)
    text = re.sub(r"\$\\Rightarrow\$",     "⇒", text)
    text = re.sub(r"\$\\Leftarrow\$",      "⇐", text)
    text = re.sub(r"\$\\Leftrightarrow\$", "⇔", text)
    text = re.sub(r"\$\\Uparrow\$",        "⇑", text)
    text = re.sub(r"\$\\Downarrow\$",      "⇓", text)
    text = re.sub(r"\$\\mapsto\$",         "↦", text)
    text = re.sub(r"\$\\longmapsto\$",     "⟼", text)
    text = re.sub(r"\$\\implies\$",        "⟹", text)
    text = re.sub(r"\$\\gets\$",           "←", text)
    text = re.sub(r"\$\\to\$",             "→", text)
    return text


# ── Table detection & Block Kit conversion ────────────────────────────────────

def _tokenize_inline(text: str) -> str:
    """
    Convert inline markdown to Slack mrkdwn in a single left-to-right scan.

    This replaces all sequential regex passes for inline formatting (bold,
    italic, inline code, strikethrough, LaTeX arrows).  Because we consume
    each token the moment we recognise it, no pass can corrupt the output
    of a previous pass — the root cause of the old placeholder approach.

    Recognised tokens (in priority order at each position):
        $\arrow$  — LaTeX arrow shorthand  → Unicode
        ```…```     — fenced code block       → verbatim
        `…`         — inline code             → `…` (verbatim, no further processing)
        **…** / __…__ — bold                 → *…*
        ~~…~~       — strikethrough           → ~…~
        *…* / _…_   — italic                  → _…_
        everything else — copied as-is
    """
    # Pre-convert LaTeX arrows so the scan below never sees $\…$ sequences.
    text = _convert_latex_arrows(text)

    out = []
    i   = 0
    n   = len(text)

    while i < n:
        # ── fenced code (``` … ```) ───────────────────────────────────────
        if text[i:i+3] == "```":
            end = text.find("```", i + 3)
            if end != -1:
                out.append(text[i : end + 3])   # verbatim, including fences
                i = end + 3
                continue
            # No closing fence — treat the rest as literal
            out.append(text[i:])
            break

        # ── inline code (` … `) ──────────────────────────────────────────
        if text[i] == "`":
            end = text.find("`", i + 1)
            if end != -1:
                out.append(text[i : end + 1])   # verbatim backtick span
                i = end + 1
                continue

        # ── bold: **…** ──────────────────────────────────────────────────
        if text[i:i+2] == "**":
            end = text.find("**", i + 2)
            if end != -1:
                inner = _tokenize_inline(text[i+2 : end])   # recurse for nested
                out.append(f"*{inner}*")
                i = end + 2
                continue

        # ── bold: __…__ ──────────────────────────────────────────────────
        if text[i:i+2] == "__":
            end = text.find("__", i + 2)
            if end != -1:
                inner = _tokenize_inline(text[i+2 : end])
                out.append(f"*{inner}*")
                i = end + 2
                continue

        # ── strikethrough: ~~…~~ ─────────────────────────────────────────
        if text[i:i+2] == "~~":
            end = text.find("~~", i + 2)
            if end != -1:
                inner = _tokenize_inline(text[i+2 : end])
                out.append(f"~{inner}~")
                i = end + 2
                continue

        # ── italic: *…*  (only when not part of **) ───────────────────────
        if text[i] == "*" and (i + 1 >= n or text[i+1] != "*"):
            end = i + 1
            while end < n:
                if text[end] == "*" and (end + 1 >= n or text[end+1] != "*"):
                    break
                end += 1
            if end < n:
                inner = _tokenize_inline(text[i+1 : end])
                out.append(f"_{inner}_")
                i = end + 1
                continue

        # ── italic: _…_  (only when not part of __) ───────────────────────
        if text[i] == "_" and (i + 1 >= n or text[i+1] != "_"):
            end = i + 1
            while end < n:
                if text[end] == "_" and (end + 1 >= n or text[end+1] != "_"):
                    break
                end += 1
            if end < n:
                inner = _tokenize_inline(text[i+1 : end])
                out.append(f"_{inner}_")
                i = end + 1
                continue

        # ── plain character ───────────────────────────────────────────────
        out.append(text[i])
        i += 1

    return "".join(out)


def _format_cell(text: str) -> str:
    """Format a single table cell using the tokenizer."""
    return _tokenize_inline(text.strip())


def _parse_markdown_table(table_text: str) -> dict | None:
    """
    Parse a markdown table string into a Slack Block Kit table block dict.
    Returns None if parsing fails — caller leaves the text unchanged.

    Block Kit table block shape (Slack API, August 2025):
    {
        "type": "table",
        "body": {
            "rows": [
                {"cells": [{"type": "raw_text", "text": "col1"}, ...]},
                ...
            ]
        },
        "has_dividers": true,
        "header_rows": 1
    }
    """
    try:
        lines = [l.rstrip() for l in table_text.strip().splitlines()]
        # Drop the separator row (contains only |, -, :, spaces/tabs)
        data_lines = [
            l for l in lines
            if l.strip("| \t") and not re.match(r"^[\|\-: \t]+$", l)
        ]
        if len(data_lines) < 2:   # need header + at least one data row
            return None

        rows = []
        for line in data_lines:
            cells_raw = line.strip().strip("|")
            cells = [c.strip() for c in cells_raw.split("|")]
            rows.append(
                [{"type": "raw_text", "text": _format_cell(cell)} for cell in cells]
            )

        # Pad/trim all rows to match the header column count (Slack rejects uneven tables)
        num_cols = len(rows[0])
        empty_cell = {"type": "raw_text", "text": ""}
        rows = [r + [empty_cell] * (num_cols - len(r)) if len(r) < num_cols else r[:num_cols] for r in rows]

        return {
            "type": "table",
            "rows": rows,
        }
    except Exception:
        return None


def _extract_table(text: str) -> tuple[str, dict | None]:
    """
    Find the first markdown table in text.
    Replace it with SLACK_TABLE_SENTINEL and return (modified_text, table_block).
    If no table is found, returns (text, None) unchanged.
    Only the first table is handled (one-table-per-response policy).
    """
    match = _MD_TABLE_RE.search(text)
    if not match:
        return text, None

    table_block = _parse_markdown_table(match.group(0))
    if table_block is None:
        # Parsing failed — leave text untouched, no block
        return text, None

    modified = text[: match.start()] + SLACK_TABLE_SENTINEL + text[match.end() :]
    return modified, table_block


# ── mrkdwn conversion ─────────────────────────────────────────────────────────

def _process_mrkdwn(part: str) -> str:
    """
    Convert one plain-text segment (no code fences, no sentinel) to Slack mrkdwn.

    Line-level patterns (headings, bullets, blockquotes, HR) are handled with
    regex first because they are unambiguous line-boundary transforms that
    cannot interfere with each other.  All inline formatting (bold, italic,
    code, strikethrough, LaTeX arrows) is then handled by _tokenize_inline,
    which scans left-to-right once so no pass can corrupt another.
    """
    lines = part.split("\n")
    processed = []

    for line in lines:
        # Horizontal rules — remove entirely
        if re.match(r"^[-\*]{3,}$", line.strip()):
            processed.append("")
            continue

        # Headings: # Heading → *Heading*  (strip the #s, bold the rest)
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            # Tokenize the heading text so inline formatting inside headings works
            inner = _tokenize_inline(heading_match.group(2))
            processed.append(f"*{inner}*")
            continue

        # Unordered list items: - item / * item → • item
        list_match = re.match(r"^(\s*)[\-\*]\s+(.+)$", line)
        if list_match:
            indent = list_match.group(1)
            inner  = _tokenize_inline(list_match.group(2))
            processed.append(f"{indent}\u2022 {inner}")
            continue

        # Blockquotes: > text → ▎ text
        bq_match = re.match(r"^>\s+(.+)$", line)
        if bq_match:
            inner = _tokenize_inline(bq_match.group(1))
            processed.append(f"\u258e {inner}")
            continue

        # Numbered list items: keep number, tokenize content
        num_match = re.match(r"^(\s*)(\d+\.\s+)(.+)$", line)
        if num_match:
            indent  = num_match.group(1)
            prefix  = num_match.group(2)
            inner   = _tokenize_inline(num_match.group(3))
            processed.append(f"{indent}{prefix}{inner}")
            continue

        # Plain line — tokenize inline formatting
        processed.append(_tokenize_inline(line))

    return "\n".join(processed)


def _markdown_to_slack(text: str) -> tuple[str, dict | None]:
    """
    Convert markdown to Slack mrkdwn.  Returns (formatted_text, table_block | None).

    Processing order:
    1. Extract the first markdown table → sentinel + table_block dict
    2. Split on code fences (those pass through unchanged)
    3. For each non-code segment, protect the sentinel then apply _process_mrkdwn
    4. Normalise links across the whole result
    """
    # Step 1 — pull out the table before anything else touches it
    text, table_block = _extract_table(text)

    # Step 2 — process text around the sentinel only.
    # Do NOT pre-split on backticks here: _tokenize_inline (called by
    # _process_mrkdwn) handles inline code and fenced blocks internally in
    # a single pass.  Pre-splitting on backticks breaks bold-around-code
    # spans like **`robots-ai`** by tearing them apart before the tokenizer
    # ever sees the full span.
    if SLACK_TABLE_SENTINEL in text:
        before, after = text.split(SLACK_TABLE_SENTINEL, 1)
        result = _process_mrkdwn(before) + SLACK_TABLE_SENTINEL + _process_mrkdwn(after)
    else:
        result = _process_mrkdwn(text)

    formatted = _normalize_links(result)
    return formatted, table_block


# ── Link normalisation ────────────────────────────────────────────────────────

def _url_to_label(url: str) -> str:
    """Extract a human-readable label from a URL."""
    clean = re.sub(r"https?://", "", url)
    clean = re.sub(r"^www\.", "", clean)
    parts = [p for p in clean.split("/") if p]
    if not parts:
        return url
    domain = parts[0].split(".")[0]
    path   = parts[1] if len(parts) > 1 else ""
    path   = re.sub(r"[-_]", " ", path).strip()
    return f"{domain} {path}" if path else domain


def _normalize_links(text: str) -> str:
    """
    Convert all URL patterns to Slack <url|label> format.
    Handles [label](url), [url](url), (url), bare URLs.  Skips <url|x>.
    """
    url_re = r"https?://[^\s\)\]\>\"'',]+"

    def replace_md_link(m):
        label = m.group(1)
        url   = m.group(2).rstrip(".,;:")
        if re.match(r"https?://", label.strip()):
            return f"<{url}|{_url_to_label(url)}>"
        return f"<{url}|{label}>"
    text = re.sub(r"\[([^\]]+)\]\((" + url_re + r")\)", replace_md_link, text)

    def replace_paren_url(m):
        url = m.group(1).rstrip(".,;:")
        return f"<{url}|{_url_to_label(url)}>"
    text = re.sub(r"\((" + url_re + r")\)", replace_paren_url, text)

    def replace_bare_url(m):
        url = m.group(0).rstrip(".,;:")
        return f"<{url}|{_url_to_label(url)}>"
    text = re.sub(r"(?<![<\(\[])" + url_re + r"(?![>\)\]])", replace_bare_url, text)

    return text


# ── Message splitting ─────────────────────────────────────────────────────────

def _split_message(text: str, max_chars: int = SLACK_SAFE_CHARS) -> list[str]:
    """
    Split a long message into Slack-safe chunks.
    Code fences and the SLACK_TABLE_SENTINEL travel through intact.
    Tries to split at paragraph breaks first, then hard-splits.
    
    IMPORTANT: Code blocks (```...```) are NEVER split - they stay as atomic units.
    Also handles orphan fences (``` at end without closing).
    """
    if len(text) <= max_chars:
        return [text]

    chunks  = []
    # Use re.split with capturing group - this includes delimiters in the result
    # Pattern: (code fence with content) OR (sentinel)
    # Also handle orphan fences: ``` at end of string without closing
    code_block_pattern = r"(```[\s\S]*?```|```[\s\S]*?$|" + re.escape(SLACK_TABLE_SENTINEL) + r")"
    parts   = re.split(code_block_pattern, text, flags=re.MULTILINE)
    current = ""

    for i, part in enumerate(parts):
        # Check if this is a code block or sentinel (odd indices from re.split with capturing group)
        is_code_or_sentinel = part.lstrip().startswith("```") or part == SLACK_TABLE_SENTINEL
        
        if is_code_or_sentinel:
            # Code blocks and sentinel are ALWAYS kept intact - never split
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.append(part)
            continue
        
        # Regular text - can split if needed
        if len(part) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            while len(part) > max_chars:
                chunks.append(part[:max_chars])
                part = part[max_chars:]
            current = part
            continue

        if len(current) + len(part) > max_chars:
            split_pos = current.rfind("\n\n")
            if split_pos > max_chars // 2:
                chunks.append(current[:split_pos].strip())
                current = current[split_pos:].strip() + part
            else:
                chunks.append(current.strip())
                current = part
        else:
            current += part

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c.strip()] or [text[:max_chars]]


def _build_formatted_output(slack_text: str, table_block: dict | None) -> list:
    """
    Turn (slack_text, table_block) into the final formatted_output list.

    Rules:
    - Split text on SLACK_TABLE_SENTINEL.
    - Text segments → str items (posted as mrkdwn).
    - table_block dict → inserted where the sentinel was.
    - If text before the table and the table itself should be in one message,
      they are kept as separate items — the posting loop pairs the last text
      chunk with the table block in a single chat_postMessage call.
    - Empty string segments are dropped.

    Returns list[str | dict]
    """
    chunks = _split_message(slack_text)

    if table_block is None:
        return chunks

    # Re-join chunks to locate the sentinel, then rebuild
    result: list = []
    for chunk in chunks:
        if SLACK_TABLE_SENTINEL in chunk:
            before, after = chunk.split(SLACK_TABLE_SENTINEL, 1)
            if before.strip():
                result.append(before.strip())
            result.append(table_block)
            if after.strip():
                result.append(after.strip())
        else:
            result.append(chunk)

    return result


# ── State helpers ─────────────────────────────────────────────────────────────

def _get_final_response(state: AgentState) -> str:
    """
    Extract the final response to send to the user.

    Looks for the most recent supervisor message with a non-empty response.
    Falls back to system messages, then error_message, then a safe default.
    """
    from langchain_core.messages import HumanMessage

    messages = state.get("messages", [])

    for msg in reversed(messages):
        if not isinstance(msg, HumanMessage):
            continue

        content = msg.content if isinstance(msg.content, str) else str(msg.content)

        if content.startswith(LABEL["supervisor"]):
            text = content.replace(LABEL["supervisor"], "").strip()
            if text:
                return text
            logger.warning(
                "[output_formatter] Supervisor produced an empty response. "
                "This usually means MODE A was used for a task that needed MODE B "
                "(e.g. creative writing placed in current_subtask instead of response)."
            )
            break

        if content.startswith(LABEL["system"]):
            text = content.replace(LABEL["system"], "").strip()
            if text:
                return text

    error = state.get("error_message", "")
    if error:
        return f"An error occurred: {error}"

    return "I wasn't able to generate a response. Please try again."


# ── Node ──────────────────────────────────────────────────────────────────────

def output_formatter_node(state: AgentState) -> dict:
    """
    Formats the final response and prepares it for Slack posting.
    formatted_output is list[str | dict]:
      str  → post as text=..., mrkdwn=True
      dict → post as blocks=[...] (Slack Block Kit table block)
    """
    raw_response = _get_final_response(state)
    clean        = _strip_labels(raw_response)

    slack_text, table_block = _markdown_to_slack(clean)
    
    formatted_output        = _build_formatted_output(slack_text, table_block)

    logger.info(
        f"[output_formatter] Response ready: "
        f"{len(clean)} chars → {len(formatted_output)} item(s)"
        + (" (includes table block)" if table_block else "")
    )

    return {
        "formatted_output": formatted_output,
        "task_complete":    True,
        "next_node":        "end",
    }
