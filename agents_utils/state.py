"""
Gemma Swarm — Agent State
===========================
The shared state that flows through the entire LangGraph pipeline.
Every agent and every deterministic node reads from and writes to this state.

Redesign notes (supervisor redesign):
  - Removed all per-agent history fields (researcher_history, email_history, etc.)
  - Removed all routing flag fields (requires_research, requires_email, etc.)
  - Removed planning fields (is_complex_task, task_plan, current_subtask, etc.)
  - Added loaded_tools: list — dynamic toolset loaded by load_toolset meta-tool
  - Supervisor now routes via next_node only; no routing flags needed.
"""

from typing import TypedDict
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):

    # ── Core Conversation ──────────────────────────────────────────────────────
    messages: list[BaseMessage]

    # ── Task Tracking ──────────────────────────────────────────────────────────
    original_task: str
    active_agent:  str
    task_complete: bool

    # ── Retry / Error Handling ─────────────────────────────────────────────────
    retry_counts:  dict
    error_message: str

    # ── Human In The Loop ──────────────────────────────────────────────────────
    awaiting_human:       bool
    human_decision:       str
    pending_confirmation: str

    # ── Workspace ──────────────────────────────────────────────────────────────
    workspace_path: str
    project_name:   str
    files_created:  list[str]
    files_modified: list[str]

    # ── Memory ─────────────────────────────────────────────────────────────────
    context_summary: str   # supervisor-level context compression by memory agent

    # ── Dynamic Toolset Loading ────────────────────────────────────────────────
    # Set by the load_toolset meta-tool inside the supervisor agentic loop.
    # Contains the name of the currently loaded toolset (e.g. "gmail").
    # Cleared at the start of each new turn.
    loaded_toolset: str    # name of the toolset currently loaded, "" if none

    # ── Routing ────────────────────────────────────────────────────────────────
    next_node: str         # Supervisor signals its desired next node here.
                           # Only used by confirm nodes and validator.
                           # All routing is via next_node — no routing flags.

    # ── Interrupt Handling ────────────────────────────────────────────────────
    is_interrupted: bool   # True when user sends message while agent is running

    # ── Email ──────────────────────────────────────────────────────────────────
    email_draft: dict
    # Structure:
    # {
    #   "to":          ["email@domain.com"],
    #   "subject":     "...",
    #   "message":     "...",
    #   "language":    "english",
    #   "layout":      "official",
    #   "rendered_body": "...",
    #   "attachments": [],
    #   "feedback":    "",
    # }

    # ── LinkedIn ───────────────────────────────────────────────────────────────
    linkedin_draft: dict
    # Structure:
    # {
    #   "post_text":      "...",
    #   "media_filename": "...",
    #   "media_path":     "...",
    #   "language":       "english",
    #   "feedback":       "",
    # }

    # ── Google ─────────────────────────────────────────────────────────────────
    google_requires_confirmation: bool
    # True after write actions (calendar_create, calendar_delete,
    # docs_create, docs_update, sheets_create, sheets_update).
    # Note: _slack_client is injected at runtime by graph.py and
    # is NOT persisted in state (not serialisable).

    # ── Output ─────────────────────────────────────────────────────────────────
    formatted_output: list[str]

    # ── Slack Context ──────────────────────────────────────────────────────────
    slack_thread_ts: str
    slack_channel:   str


def default_state(
    original_task:   str = "",
    workspace_path:  str = "",
    project_name:    str = "",
    slack_thread_ts: str = "",
    slack_channel:   str = "",
) -> AgentState:
    return AgentState(
        messages=[],
        original_task=original_task,
        active_agent="",
        task_complete=False,
        retry_counts={},
        error_message="",
        awaiting_human=False,
        human_decision="",
        pending_confirmation="",
        workspace_path=workspace_path,
        project_name=project_name,
        files_created=[],
        files_modified=[],
        context_summary="",
        loaded_toolset="",
        next_node="",
        is_interrupted=False,
        email_draft={},
        linkedin_draft={},
        google_requires_confirmation=False,
        formatted_output=[],
        slack_thread_ts=slack_thread_ts,
        slack_channel=slack_channel,
    )
