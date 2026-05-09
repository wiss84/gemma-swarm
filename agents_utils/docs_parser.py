"""
agents_utils/docs_parser.py
============================
Parses markdown content into a structured segment list consumed by docs_api.py.

Each segment is one of:
  {"kind": "lines",  "lines": [line_dict, ...]}
  {"kind": "table",  "headers": [str, ...], "rows": [[str, ...], ...]}

line_dict keys:
  text       — plain text (inline markers stripped)
  type       — heading1 | heading2 | heading3 | bullet | bullet_bold_title |
               numbered | bold_line | code_block | normal
  indent     — (bullet/numbered) nesting level 0-based (0 = top level)
  bold_end   — (bullet_bold_title) char offset where bold portion ends
  segments   — (normal / numbered with inline markup) list of
               {text, bold, italic, strikethrough} dicts
"""

import re


# ── Inline segment parser ─────────────────────────────────────────────────────

def parse_inline_segments(text: str) -> list[dict]:
    """
    Split a line of text into segments with bold / italic / strikethrough flags.

    Recognised markers (precedence order):
      ***text***  → bold + italic
      **text**    → bold
      _text_      → italic  (underscore style)
      *text*      → italic  (single-star style, not adjacent to another *)
      ~~text~~    → strikethrough
    """
    segments = []
    # Pattern groups (in priority order):
    #  1. ***bold+italic***
    #  2. **bold**
    #  3. ~~strikethrough~~
    #  4. _italic_ (underscore)
    #  5. *italic* (single star, not ** boundary)
    pattern = re.compile(
        r'(\*\*\*(.+?)\*\*\*'        # group 1+2  bold+italic
        r'|\*\*(.+?)\*\*'            # group 3    bold
        r'|~~(.+?)~~'                # group 4    strikethrough
        r'|_([^_]+)_'                # group 5    italic underscore
        r'|\*([^*][^*]*?[^*]|[^*])\*'  # group 6 italic single-star
        r')',
        re.DOTALL,
    )
    last_end = 0
    for m in pattern.finditer(text):
        if m.start() > last_end:
            segments.append(_plain(text[last_end:m.start()]))
        if m.group(2):   # ***bold+italic***
            segments.append({"text": m.group(2),  "bold": True,  "italic": True,  "strikethrough": False})
        elif m.group(3): # **bold**
            segments.append({"text": m.group(3),  "bold": True,  "italic": False, "strikethrough": False})
        elif m.group(4): # ~~strikethrough~~
            segments.append({"text": m.group(4),  "bold": False, "italic": False, "strikethrough": True})
        elif m.group(5): # _italic_
            segments.append({"text": m.group(5),  "bold": False, "italic": True,  "strikethrough": False})
        elif m.group(6): # *italic*
            segments.append({"text": m.group(6),  "bold": False, "italic": True,  "strikethrough": False})
        last_end = m.end()

    if last_end < len(text):
        segments.append(_plain(text[last_end:]))

    return segments if segments else [_plain(text)]


def _plain(text: str) -> dict:
    return {"text": text, "bold": False, "italic": False, "strikethrough": False}


def strip_inline_markers(text: str) -> str:
    """Return plain text with all inline markers removed."""
    return "".join(s["text"] for s in parse_inline_segments(text))


def has_inline_markup(segments: list[dict]) -> bool:
    return any(s["bold"] or s["italic"] or s["strikethrough"] for s in segments)


# ── Markdown link stripper ────────────────────────────────────────────────────

def strip_markdown_links(text: str) -> str:
    """Convert [label](url) → label and <url> → url."""
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'<(https?://[^>]+)>', r'\1', text)
    return text


# ── Table detection & parsing ─────────────────────────────────────────────────

_TABLE_ROW_RE  = re.compile(r'^\|(.+)\|$')
_TABLE_SEP_RE  = re.compile(r'^\|[\s|:-]+\|$')


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW_RE.match(line.strip()))


def _is_separator_row(line: str) -> bool:
    return bool(_TABLE_SEP_RE.match(line.strip()))


def _parse_table_row(line: str) -> list[str]:
    """Split a pipe-delimited row into stripped cell strings."""
    inner = line.strip().strip('|')
    return [strip_inline_markers(cell.strip()) for cell in inner.split('|')]


def _try_parse_table(raw_lines: list[str], start: int) -> tuple[dict | None, int]:
    """
    Try to parse a markdown table starting at raw_lines[start].
    Returns (table_segment, next_i) on success, or (None, start) on failure.

    A valid table is:
      header row
      separator row  (---|---| etc.)
      one or more data rows
    """
    if start + 2 > len(raw_lines):
        return None, start

    header_line = raw_lines[start].rstrip()
    sep_line    = raw_lines[start + 1].rstrip() if start + 1 < len(raw_lines) else ""

    if not _is_table_row(header_line) or not _is_separator_row(sep_line):
        return None, start

    headers = _parse_table_row(header_line)
    rows    = []
    i       = start + 2
    while i < len(raw_lines) and _is_table_row(raw_lines[i].rstrip()):
        rows.append(_parse_table_row(raw_lines[i].rstrip()))
        i += 1

    if not rows:
        return None, start

    return {"kind": "table", "headers": headers, "rows": rows}, i


# ── Line parser ───────────────────────────────────────────────────────────────

def _indent_level(raw_line: str) -> int:
    """Convert leading whitespace to a nesting level (2 spaces or 1 tab = 1 level)."""
    spaces = 0
    for ch in raw_line:
        if ch == ' ':
            spaces += 1
        elif ch == '\t':
            spaces += 2
        else:
            break
    return spaces // 2


def _parse_line(raw_line: str) -> dict:
    """Parse a single non-table, non-code line into a line_dict."""
    stripped = raw_line.rstrip()
    indent   = _indent_level(raw_line)
    content  = stripped.lstrip()  # remove leading whitespace for pattern matching

    # #### Heading 4+ → Heading 3
    if re.match(r'^#{4,}\s', content):
        text = strip_markdown_links(re.sub(r'^#{4,}\s+', '', content).strip())
        return {"text": text, "type": "heading3", "indent": 0}

    if content.startswith("### "):
        return {"text": strip_markdown_links(content[4:].strip()), "type": "heading3", "indent": 0}

    if content.startswith("## "):
        return {"text": strip_markdown_links(content[3:].strip()), "type": "heading2", "indent": 0}

    if content.startswith("# "):
        return {"text": strip_markdown_links(content[2:].strip()), "type": "heading1", "indent": 0}

    # DRAFT N:
    if re.match(r"^DRAFT\s+\d+\s*:", content, re.IGNORECASE):
        return {"text": strip_markdown_links(content), "type": "heading2", "indent": 0}

    # Bullet list item (- or •), any indentation level
    if re.match(r'^[-•]\s', content):
        text = strip_markdown_links(content[2:].strip())
        bold_prefix = re.match(r"^\*\*(.+?)\*\*[:\-]?\s*", text)
        if bold_prefix:
            bold_part  = bold_prefix.group(1).rstrip(":- ")
            after_bold = text[bold_prefix.end():]
            plain      = f"{bold_part}: {after_bold}" if after_bold else bold_part
            return {
                "text":     plain,
                "type":     "bullet_bold_title",
                "indent":   indent,
                "bold_end": len(bold_part) + 2,
            }
        # Parse inline markup inside bullet text
        segs = parse_inline_segments(text)
        plain = "".join(s["text"] for s in segs)
        base = {"text": plain, "type": "bullet", "indent": indent}
        if has_inline_markup(segs):
            base["segments"] = segs
        return base

    # Numbered list item
    if re.match(r"^\d+\.\s", content):
        text     = strip_markdown_links(re.sub(r"^\d+\.\s+", "", content))
        segments = parse_inline_segments(text)
        plain    = "".join(s["text"] for s in segments)
        base     = {"text": plain, "type": "numbered", "indent": indent}
        if has_inline_markup(segments):
            base["segments"] = segments
        return base

    # Whole line bold (**text**)
    if content.startswith("**") and content.endswith("**") and len(content) > 4:
        return {"text": strip_markdown_links(content[2:-2]), "type": "bold_line", "indent": 0}

    # Empty line
    if content == "":
        return {"text": "", "type": "normal", "indent": 0}

    # Normal paragraph — preserve inline markup
    text     = strip_markdown_links(content)
    segments = parse_inline_segments(text)
    plain    = "".join(s["text"] for s in segments)
    base     = {"text": plain, "type": "normal", "indent": 0}
    if has_inline_markup(segments):
        base["segments"] = segments
    return base


# ── Top-level parser ──────────────────────────────────────────────────────────

def parse_content(content: str) -> list[dict]:
    """
    Parse markdown content into a list of segments.

    Returns:
      [
        {"kind": "lines", "lines": [line_dict, ...]},
        {"kind": "table", "headers": [...], "rows": [[...], ...]},
        ...
      ]

    Consecutive non-table lines are grouped into a single "lines" segment.
    Each markdown table becomes its own "table" segment.
    """
    raw_lines     = content.split("\n")
    segments: list[dict] = []
    current_lines: list[dict] = []
    in_code_block = False
    i             = 0

    def flush_lines():
        if current_lines:
            segments.append({"kind": "lines", "lines": list(current_lines)})
            current_lines.clear()

    while i < len(raw_lines):
        raw_line = raw_lines[i]
        stripped = raw_line.rstrip()

        # Code block fence toggle
        if stripped.lstrip().startswith("```"):
            in_code_block = not in_code_block
            i += 1
            continue

        if in_code_block:
            current_lines.append({"text": raw_line.rstrip("\r"), "type": "code_block", "indent": 0})
            i += 1
            continue

        # Try table
        if _is_table_row(stripped):
            table_seg, next_i = _try_parse_table(raw_lines, i)
            if table_seg is not None:
                flush_lines()
                segments.append(table_seg)
                i = next_i
                continue

        # Regular line
        current_lines.append(_parse_line(raw_line))
        i += 1

    flush_lines()
    return segments
