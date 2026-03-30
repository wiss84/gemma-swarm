"""
Gemma Swarm — Agent State
===========================
The shared state that flows through the entire LangGraph pipeline.
Every agent and every deterministic node reads from and writes to this state.
"""

from typing import TypedDict
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):

    # ── Core Conversation ──────────────────────────────────────────────────────
    messages: list[BaseMessage]

    # ── Task Tracking ──────────────────────────────────────────────────────────
    original_task:   str
    current_subtask: str
    subtask_results: dict
    active_agent:    str
    task_complete:   bool

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
    context_summary:                     str    # supervisor conversation summary
    researcher_history:                  list   # researcher's own conversation history
    deep_researcher_history:             list   # deep researcher's own conversation history
    email_history:                       list   # email composer's own conversation history
    linkedin_history:                    list   # linkedin composer's own conversation history
    gmail_history:                       list   # gmail agent's own conversation history
    calendar_history:                    list   # calendar agent's own conversation history
    docs_history:                        list   # docs agent's own conversation history
    sheets_history:                      list   # sheets agent's own conversation history
    researcher_context_summary:          str
    deep_researcher_context_summary:     str
    email_context_summary:               str
    linkedin_context_summary:            str
    gmail_context_summary:               str
    calendar_context_summary:            str
    docs_context_summary:                str
    sheets_context_summary:              str

    # ── Planning ───────────────────────────────────────────────────────────────
    is_complex_task:   bool        # Set by task_classifier
    task_plan:         list[dict]  # Set by planner — list of subtasks
    # Each subtask: {"id": 1, "description": "...", "agent": "researcher", "status": "pending|done|failed"}

    # ── Routing ────────────────────────────────────────────────────────────────
    next_node:              str
    requires_research:      bool  # Route to researcher (search_web only)
    requires_deep_research: bool  # Route to deep_researcher (search + fetch)
    requires_email:         bool
    requires_linkedin:     bool  # Route to email_composer
    require_gmail:         bool
    requires_calendar:     bool
    requires_docs:         bool
    requires_sheets:       bool
    requires_confirmation:  bool  # Human must approve before continuing

    # ── Interrupt Handling ───────────────────────────────────────────────────────
    is_interrupted:     bool  # True when user sends message while agent is running

    # ── Email ──────────────────────────────────────────────────────────────────
    email_draft: dict
    # Structure:
    # {
    #   "to":          ["email@domain.com"],
    #   "subject":     "...",
    #   "message":     "...",
    #   "language":    "english",
    #   "layout":      "official",
    #   "attachments": [],   # file paths relative to workspace/email_attachments/
    #   "feedback":    "",   # populated on reject → recompose cycle
    # }

    # ── LinkedIn ────────────────────────────────────────────────────────────────
    linkedin_draft: dict
    # Structure:
    # {
    #   "post_text":      "...",
    #   "media_filename": "...",  # filename in linkedin_media/post_attachments/
    #   "media_path":     "...",  # resolved full path
    #   "language":       "english",
    #   "feedback":       "",     # populated on reject → recompose cycle
    # }

    # ── Google ─────────────────────────────────────────────────────────────────
    google_requires_confirmation: bool
    # True after write actions (calendar_create, calendar_delete,
    # docs_create, docs_update, sheets_create, sheets_update).
    # False after read actions — routes straight back to supervisor.
    # Note: _slack_client is injected at runtime by graph.py node wrappers
    # and is NOT persisted in state (not serialisable).

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
        current_subtask="",
        subtask_results={},
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
        researcher_history=[],
        deep_researcher_history=[],
        email_history=[],
        linkedin_history=[],
        gmail_history=[],
        calendar_history=[],
        docs_history=[],
        sheets_history=[],
        researcher_context_summary="",
        deep_researcher_context_summary="",
        email_context_summary="",
        linkedin_context_summary="",
        next_node="",
        is_complex_task=False,
        task_plan=[],
        requires_research=False,
        requires_deep_research=False,
        requires_email=False,
        requires_linkedin=False,
        requires_gmail=False,
        requires_calendar=False,
        requires_docs=False,
        requires_sheets=False,
        requires_confirmation=False,
        is_interrupted=False,
        email_draft={},
        linkedin_draft={},
        google_requires_confirmation=False,
        formatted_output=[],
        slack_thread_ts=slack_thread_ts,
        slack_channel=slack_channel,
    )
