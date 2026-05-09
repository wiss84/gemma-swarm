"""
agents_utils/docs_parser.py
============================
Parses markdown content into structured segments and builds all Google Docs
API formatting requests. Consumed by tools/docs_api.py.

Public API
----------
  parse_content(content)          → list of segment dicts
  build_block_text(lines)         → (full_text, line_spans)
  build_format_requests(...)      → list of Docs API request dicts
  inline_style_requests(...)      → list of Docs API request dicts
  para_style(start, end, style)   → Docs API request dict
  text_style(start, end, ...)     → Docs API request dict
  extract_table_cell_indices(...) → list[int]

Segment shapes
--------------
  {"kind": "lines",  "lines": [line_dict, ...]}
  {"kind": "table",  "headers": [str, ...], "rows": [[str, ...], ...]}

line_dict keys
--------------
  text       — plain text (inline markers stripped)
  type       — heading1 | heading2 | heading3 | bullet | bullet_bold_title |
               numbered | bold_line | code_block | normal
  indent     — nesting level 0-based (bullet/numbered only)
  bold_end   — char offset where bold title ends (bullet_bold_title only)
  segments   — list of {text, bold, italic, strikethrough} dicts
               present on normal / bullet / numbered lines that contain
               inline markup
"""

import re


# ── LaTeX → Unicode substitution map ─────────────────────────────────────────
# Applied before inline tokenisation so $...$ sequences never reach the parser.

_LATEX_MAP: dict[str, str] = {
    # Arrows
    r"\rightarrow":  "→",
    r"\leftarrow":   "←",
    r"\Rightarrow":  "⇒",
    r"\Leftarrow":   "⇐",
    r"\leftrightarrow": "↔",
    r"\Leftrightarrow": "⇔",
    r"\uparrow":     "↑",
    r"\downarrow":   "↓",
    r"\to":          "→",
    r"\gets":        "←",
    # Comparisons
    r"\leq":         "≤",
    r"\geq":         "≥",
    r"\neq":         "≠",
    r"\approx":      "≈",
    r"\equiv":       "≡",
    r"\sim":         "~",
    r"\ll":          "≪",
    r"\gg":          "≫",
    # Logic / sets
    r"\in":          "∈",
    r"\notin":       "∉",
    r"\subset":      "⊂",
    r"\supset":      "⊃",
    r"\cup":         "∪",
    r"\cap":         "∩",
    r"\emptyset":    "∅",
    r"\forall":      "∀",
    r"\exists":      "∃",
    r"\neg":         "¬",
    r"\land":        "∧",
    r"\lor":         "∨",
    # Math symbols
    r"\infty":       "∞",
    r"\pm":          "±",
    r"\times":       "×",
    r"\div":         "÷",
    r"\cdot":        "·",
    r"\sqrt":        "√",
    r"\sum":         "∑",
    r"\prod":        "∏",
    r"\int":         "∫",
    r"\partial":     "∂",
    r"\nabla":       "∇",
    r"\alpha":       "α",
    r"\beta":        "β",
    r"\gamma":       "γ",
    r"\delta":       "δ",
    r"\epsilon":     "ε",
    r"\theta":       "θ",
    r"\lambda":      "λ",
    r"\mu":          "μ",
    r"\pi":          "π",
    r"\sigma":       "σ",
    r"\tau":         "τ",
    r"\phi":         "φ",
    r"\omega":       "ω",
    r"\Delta":       "Δ",
    r"\Sigma":       "Σ",
    r"\Omega":       "Ω",
}


def _substitute_latex(text: str) -> str:
    """
    Replace $...$ LaTeX math expressions with Unicode equivalents.

    Works by finding every $...$ span and substituting known commands inside
    it. Unknown commands are left as-is (without the $ delimiters) so the
    output is never worse than the input.
    """
    def _replace_span(m: re.Match) -> str:
        inner = m.group(1).strip()
        # Try exact match first (e.g. "$\rightarrow$")
        result = _LATEX_MAP.get(inner)
        if result:
            return result
        # Try replacing each \command token inside the span
        def _replace_cmd(cm: re.Match) -> str:
            return _LATEX_MAP.get(cm.group(0), cm.group(0))
        return re.sub(r'\\[a-zA-Z]+', _replace_cmd, inner)

    return re.sub(r'\$([^$]+)\$', _replace_span, text)


# ── Left-to-right inline tokeniser ───────────────────────────────────────────

def parse_inline_segments(text: str) -> list[dict]:
    """
    Tokenise a line of text into segments with bold / italic / strikethrough
    flags using a single left-to-right scan — no backtracking, no ambiguity.

    Recognised markers (checked at each position in priority order):
      ***text***  → bold + italic
      **text**    → bold
      ~~text~~    → strikethrough
      _text_      → italic  (underscore)
      *text*      → italic  (single star)
      $...$       → LaTeX math  (converted to Unicode via _LATEX_MAP)

    The tokeniser never re-examines consumed characters, so marker boundaries
    like **GPT-5.5** or *single* are never misread regardless of their content.
    """
    # Pre-process LaTeX so $...$ never reaches the star/underscore logic
    text = _substitute_latex(text)

    segments: list[dict] = []
    buf   = []          # plain-text accumulator
    i     = 0
    n     = len(text)

    def flush_buf():
        if buf:
            segments.append(_plain("".join(buf)))
            buf.clear()

    while i < n:
        # ── ***bold+italic*** ──────────────────────────────────────────────
        if text[i:i+3] == "***":
            end = text.find("***", i + 3)
            if end != -1:
                flush_buf()
                segments.append({
                    "text": text[i+3:end],
                    "bold": True, "italic": True, "strikethrough": False,
                })
                i = end + 3
                continue

        # ── **bold** ──────────────────────────────────────────────────────
        if text[i:i+2] == "**":
            end = text.find("**", i + 2)
            if end != -1:
                flush_buf()
                segments.append({
                    "text": text[i+2:end],
                    "bold": True, "italic": False, "strikethrough": False,
                })
                i = end + 2
                continue

        # ── ~~strikethrough~~ ─────────────────────────────────────────────
        if text[i:i+2] == "~~":
            end = text.find("~~", i + 2)
            if end != -1:
                flush_buf()
                segments.append({
                    "text": text[i+2:end],
                    "bold": False, "italic": False, "strikethrough": True,
                })
                i = end + 2
                continue

        # ── _italic_ (underscore) ─────────────────────────────────────────
        if text[i] == "_":
            end = text.find("_", i + 1)
            if end != -1:
                flush_buf()
                segments.append({
                    "text": text[i+1:end],
                    "bold": False, "italic": True, "strikethrough": False,
                })
                i = end + 1
                continue

        # ── *italic* (single star — only if not part of **) ───────────────
        if text[i] == "*" and text[i:i+2] != "**":
            # Find closing single star that is also not part of **
            j = i + 1
            end = -1
            while j < n:
                if text[j] == "*" and text[j:j+2] != "**":
                    end = j
                    break
                j += 1
            if end != -1:
                flush_buf()
                segments.append({
                    "text": text[i+1:end],
                    "bold": False, "italic": True, "strikethrough": False,
                })
                i = end + 1
                continue

        # ── plain character ───────────────────────────────────────────────
        buf.append(text[i])
        i += 1

    flush_buf()
    return segments if segments else [_plain(text)]


def _plain(text: str) -> dict:
    return {"text": text, "bold": False, "italic": False, "strikethrough": False}


def strip_inline_markers(text: str) -> str:
    """Return plain text with all inline markers and LaTeX removed."""
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

_TABLE_ROW_RE = re.compile(r'^\|(.+)\|$')
_TABLE_SEP_RE = re.compile(r'^\|[\s|:-]+\|$')


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW_RE.match(line.strip()))


def _is_separator_row(line: str) -> bool:
    return bool(_TABLE_SEP_RE.match(line.strip()))


def _parse_table_row(line: str) -> list[str]:
    """Split a pipe-delimited row into stripped cell strings."""
    inner = line.strip().strip("|")
    return [strip_inline_markers(cell.strip()) for cell in inner.split("|")]


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
        if ch == " ":
            spaces += 1
        elif ch == "\t":
            spaces += 2
        else:
            break
    return spaces // 2


def _parse_line(raw_line: str) -> dict:
    """Parse a single non-table, non-code line into a line_dict."""
    stripped = raw_line.rstrip()
    indent   = _indent_level(raw_line)
    content  = stripped.lstrip()

    # Heading 4+ → Heading 3
    if re.match(r"^#{4,}\s", content):
        text = strip_markdown_links(re.sub(r"^#{4,}\s+", "", content).strip())
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

    # Bullet list item (- or •)
    if re.match(r"^[-•]\s", content):
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
        segs  = parse_inline_segments(text)
        plain = "".join(s["text"] for s in segs)
        base  = {"text": plain, "type": "bullet", "indent": indent}
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

    # Whole-line bold (**text**)
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


# ── Top-level content parser ──────────────────────────────────────────────────

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
    raw_lines: list[str]  = content.split("\n")
    segments:  list[dict] = []
    current_lines: list[dict] = []
    in_code_block = False
    i = 0

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

        current_lines.append(_parse_line(raw_line))
        i += 1

    flush_lines()
    return segments


# ── Block text builder (used by docs_api._apply_formatted_content) ────────────

def build_block_text(lines: list[dict]) -> tuple[str, list[dict]]:
    """
    Concatenate all lines into one string for a single Docs API insertText call.

    Returns:
      full_text  — complete string to insert (tabs + text + newlines)
      line_spans — list of offset dicts relative to the start of full_text:
                   {tab_count, line_start_offset, text_start_offset,
                    text_end_offset, line_end_offset}

    Add block_start_index to any offset to get absolute document indices.
    Note: offsets are computed BEFORE createParagraphBullets strips the tabs.
    """
    parts:      list[str]  = []
    line_spans: list[dict] = []
    offset = 0

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


# ── Format request builder ────────────────────────────────────────────────────

def build_format_requests(
    lines: list[dict],
    line_spans: list[dict],
    block_start: int,
) -> list[dict]:
    """
    Build all formatting requests for a lines block.

    Uses line_spans (offsets from build_block_text) + block_start to compute
    absolute document indices.

    Key insight for nested lists: createParagraphBullets strips leading tabs
    and shifts all subsequent indices. We therefore group all contiguous list
    items of the same type into ONE createParagraphBullets request. Within a
    group the tabs haven't been stripped yet, so offsets are still valid.

    Emission order: style requests first (index-sensitive), list group requests
    last — so createParagraphBullets fires after all text-style requests.
    """
    style_requests:  list[dict] = []
    bullet_groups:   list[dict] = []
    numbered_groups: list[dict] = []

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

    for line, span in zip(lines, line_spans):
        line_type  = line["type"]
        abs_line_s = block_start + span["line_start_offset"]
        abs_line_e = block_start + span["line_end_offset"]
        abs_text_s = block_start + span["text_start_offset"]
        abs_text_e = block_start + span["text_end_offset"]
        text_len   = abs_text_e - abs_text_s

        is_bullet   = line_type in ("bullet", "bullet_bold_title")
        is_numbered = line_type == "numbered"

        # Flush list runs when type changes
        if not is_bullet and cur_bullet_start is not None:
            flush_bullet()
            cur_bullet_start = cur_bullet_end = None

        if not is_numbered and cur_numbered_start is not None:
            flush_numbered()
            cur_numbered_start = cur_numbered_end = None

        if line_type == "heading1":
            style_requests.append(para_style(abs_line_s, abs_line_e, "HEADING_1"))

        elif line_type == "heading2":
            style_requests.append(para_style(abs_line_s, abs_line_e, "HEADING_2"))

        elif line_type == "heading3":
            style_requests.append(para_style(abs_line_s, abs_line_e, "HEADING_3"))

        elif line_type == "bold_line":
            style_requests.append(text_style(abs_line_s, abs_line_e, bold=True))

        elif line_type == "code_block":
            style_requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": abs_line_s, "endIndex": abs_line_e},
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
            if cur_bullet_start is None:
                cur_bullet_start = abs_line_s
            cur_bullet_end = abs_line_e

            if line_type == "bullet_bold_title":
                bold_end = line.get("bold_end", text_len)
                style_requests.append(text_style(abs_text_s, abs_text_s + bold_end, bold=True))

            segs = line.get("segments")
            if segs:
                style_requests.extend(inline_style_requests(abs_text_s, segs))

        elif is_numbered:
            if cur_numbered_start is None:
                cur_numbered_start = abs_line_s
            cur_numbered_end = abs_line_e

            segs = line.get("segments")
            if segs:
                style_requests.extend(inline_style_requests(abs_text_s, segs))

        elif line_type == "normal":
            segs = line.get("segments")
            if segs:
                style_requests.extend(inline_style_requests(abs_text_s, segs))

    flush_bullet()
    flush_numbered()

    list_requests: list[dict] = []
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

    return style_requests + list_requests


# ── Docs API style helpers ────────────────────────────────────────────────────

def para_style(start: int, end: int, named_style: str) -> dict:
    return {
        "updateParagraphStyle": {
            "range":          {"startIndex": start, "endIndex": end},
            "paragraphStyle": {"namedStyleType": named_style},
            "fields":         "namedStyleType",
        }
    }


def text_style(
    start: int,
    end: int,
    bold: bool = False,
    italic: bool = False,
    strikethrough: bool = False,
) -> dict:
    style:  dict = {}
    fields: list = []
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


def inline_style_requests(base_index: int, segments: list[dict]) -> list[dict]:
    """Build updateTextStyle requests for inline segments starting at base_index."""
    requests: list[dict] = []
    cursor = base_index
    for seg in segments:
        seg_len = len(seg["text"])
        if seg_len > 0 and (seg.get("bold") or seg.get("italic") or seg.get("strikethrough")):
            requests.append(
                text_style(
                    cursor, cursor + seg_len,
                    bold=seg.get("bold", False),
                    italic=seg.get("italic", False),
                    strikethrough=seg.get("strikethrough", False),
                )
            )
        cursor += seg_len
    return requests


# ── Table cell index extractor ────────────────────────────────────────────────

def extract_table_cell_indices(doc: dict, insert_at: int) -> list[int]:
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

    cell_indices: list[int] = []
    for table_row in target_table.get("tableRows", []):
        for cell in table_row.get("tableCells", []):
            cell_content = cell.get("content", [])
            if cell_content:
                para_start = cell_content[0].get("startIndex")
                if para_start is not None:
                    cell_indices.append(para_start)
    return cell_indices
