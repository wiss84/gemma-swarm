"""
Gemma Swarm — Output Formatter Node
======================================
Deterministic node. No LLM call.
Runs last before response is sent to Slack.

Responsibilities:
- Extract final response from state messages
- Convert markdown to Slack mrkdwn format
- Keep code blocks intact
- Split long responses into Slack-safe chunks
- Clean up internal labels before sending to user
"""

import re
import logging
from agents_utils.state import AgentState
from agents_utils.config import LABEL

logger = logging.getLogger(__name__)

SLACK_SAFE_CHARS = 2800


def _strip_labels(text: str) -> str:
    """Remove all internal agent labels from text before sending to user."""
    for label in LABEL.values():
        text = text.replace(label, "").strip()
    return text


def _markdown_to_slack(text: str) -> str:
    """
    Convert markdown formatting to Slack mrkdwn.
    Skips content inside code blocks — those stay untouched.
    """
    # Split into code blocks and regular text
    # We process regular text only, leaving code blocks intact
    code_block_pattern = r"(```[\s\S]*?```|`[^`]+`)"
    parts = re.split(code_block_pattern, text)

    result = []
    for i, part in enumerate(parts):
        # Even indices are regular text, odd indices are code blocks
        if i % 2 == 1:
            result.append(part)  # Code block — leave untouched
            continue

        # --- Headings: # Heading → *Heading*
        part = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", part, flags=re.MULTILINE)

        # --- Bold: **text** or __text__ → placeholder first to avoid italic collision
        part = re.sub(r"\*\*(.+?)\*\*", r"SLACKBOLD\1SLACKBOLD", part)
        part = re.sub(r"__(.+?)__",     r"SLACKBOLD\1SLACKBOLD", part)

        # --- Italic: *text* → _text_ (safe now, no ** left)
        part = re.sub(r"\*(.+?)\*", r"_\1_", part)

        # --- Restore bold placeholders → *text*
        part = part.replace("SLACKBOLD", "*")

        # --- Strikethrough: ~~text~~ → ~text~
        part = re.sub(r"~~(.+?)~~", r"~\1~", part)

        # --- Unordered lists: - item or * item → • item
        part = re.sub(r"^[\-\*]\s+(.+)$", r"• \1", part, flags=re.MULTILINE)

        # --- Horizontal rules: --- or *** → blank line
        part = re.sub(r"^[-\*]{3,}$", "", part, flags=re.MULTILINE)

        # --- Links handled by _normalize_links() after the loop

        # --- Blockquotes: > text → ▎ text
        part = re.sub(r"^>\s+(.+)$", r"▎ \1", part, flags=re.MULTILINE)

        result.append(part)

    return _normalize_links("".join(result))


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
    Detect all URL patterns and convert to Slack <url|label> format.
    Handles [label](url), [url](url), (url), bare urls. Skips <url|x>.
    """
    url_re = r"https?://[^\s\)\]\>\"'',]+"

    # 1. [label](url)
    def replace_md_link(m):
        label = m.group(1)
        url   = m.group(2).rstrip(".,;:")
        if re.match(r"https?://", label.strip()):
            return f"<{url}|{_url_to_label(url)}>"
        return f"<{url}|{label}>"
    text = re.sub(r"\[([^\]]+)\]\((" + url_re + r")\)", replace_md_link, text)

    # 2. (url) standalone
    def replace_paren_url(m):
        url = m.group(1).rstrip(".,;:")
        return f"<{url}|{_url_to_label(url)}>"
    text = re.sub(r"\((" + url_re + r")\)", replace_paren_url, text)

    # 3. Bare URLs not already inside < >
    def replace_bare_url(m):
        url = m.group(0).rstrip(".,;:")
        return f"<{url}|{_url_to_label(url)}>"
    text = re.sub(r"(?<![<\(\[])" + url_re + r"(?![>\)\]])", replace_bare_url, text)

    return text



def _split_message(text: str, max_chars: int = SLACK_SAFE_CHARS) -> list[str]:
    """
    Split a long message into chunks, keeping code blocks intact.
    Tries to split at paragraph breaks first, then hard splits.
    """
    if len(text) <= max_chars:
        return [text]

    chunks  = []
    # Split on code blocks — keep them intact
    parts   = re.split(r"(```[\s\S]*?```)", text)
    current = ""

    for part in parts:
        # Large code block alone — hard split if needed
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
            # Try to split current at a paragraph break
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


def _get_final_response(state: AgentState) -> str:
    """Extract the final response to send to the user."""
    messages = state.get("messages", [])

    from langchain_core.messages import HumanMessage

    for msg in reversed(messages):
        if not isinstance(msg, HumanMessage):
            continue

        content = msg.content if isinstance(msg.content, str) else str(msg.content)

        if content.startswith(LABEL["supervisor"]):
            return content.replace(LABEL["supervisor"], "").strip()

        if content.startswith(LABEL["system"]):
            return content.replace(LABEL["system"], "").strip()

    error = state.get("error_message", "")
    if error:
        return f"An error occurred: {error}"

    return "I wasn't able to generate a response. Please try again."


def output_formatter_node(state: AgentState) -> dict:
    """
    Formats the final response and prepares it for Slack posting.
    """
    raw_response = _get_final_response(state)

    # Clean internal labels
    clean = _strip_labels(raw_response)

    # Convert markdown to Slack mrkdwn
    slack_formatted = _markdown_to_slack(clean)

    # Split into chunks
    chunks = _split_message(slack_formatted)

    logger.info(
        f"[output_formatter] Response ready: "
        f"{len(clean)} chars → {len(chunks)} chunk(s)"
    )

    return {
        "formatted_output": chunks,
        "task_complete":    True,
        "next_node":        "end",
    }
