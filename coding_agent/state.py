"""
Gemma Swarm — Coding Agent State
=================================
The state TypedDict that flows through the CodingAgent's LangGraph graph.

This is intentionally separate from the main AgentState — the coding agent
is an independent system with its own graph, not a node in the main graph.

Fields:
    messages          — conversation history (same LangChain message list pattern)
    workspace_path    — the project directory this session is scoped to
    project_name      — human-readable project name (derived from workspace path)
    current_branch    — the git branch created for this session
    active_subagents  — list of subagent types currently running (for status reporting)
    task_summary      — running summary of what the agent has done so far
    session_id        — unique ID for this coding session (used for checkpointing)
    slack_thread_ts   — Slack thread timestamp (for posting status updates)
    slack_channel     — Slack channel ID (for posting status updates)
    files_created     — list of file paths created during this session
    files_modified    — list of file paths modified during this session
    task_complete     — True when the agent has finished and posted its final summary
    formatted_output  — final response text(s) to post back to Slack
"""

from typing import TypedDict
from langchain_core.messages import BaseMessage


class CodingAgentState(TypedDict):

    # ── Core Conversation ──────────────────────────────────────────────────────
    messages: list[BaseMessage]

    # ── Workspace ──────────────────────────────────────────────────────────────
    workspace_path: str       # absolute path to the project directory
    project_name:   str       # human-readable name, e.g. "my-flask-app"

    # ── Git Context ────────────────────────────────────────────────────────────
    current_branch: str       # branch created for this session, e.g. "feat/add-auth"

    # ── Subagent Tracking ──────────────────────────────────────────────────────
    active_subagents: list    # e.g. ["research"] while a subagent is running

    # ── Session Progress ───────────────────────────────────────────────────────
    task_summary:  str        # running summary written by the agent after each major step
    session_id:    str        # unique ID — used as the LangGraph thread_id for checkpointing

    # ── File Tracking ──────────────────────────────────────────────────────────
    files_created:  list[str]  # paths of files created this session
    files_modified: list[str]  # paths of files modified this session

    # ── Flow Control ──────────────────────────────────────────────────────────
    task_complete: bool        # True → graph routes to output_node → END

    # ── Output ─────────────────────────────────────────────────────────────────
    formatted_output: list[str]  # final message(s) to post to Slack

    # ── Slack Context ──────────────────────────────────────────────────────────
    slack_thread_ts: str
    slack_channel:   str

    # ── Model Override ──────────────────────────────────────────────────────────
    model_override: str        # optional model name to replace the default coding_agent model

    # ── Agent Notes ─────────────────────────────────────────────────────────────
    agent_notes_enabled: bool  # whether the agent can read/write learning notes


def default_coding_state(
    workspace_path:  str = "",
    project_name:    str = "",
    session_id:      str = "",
    slack_thread_ts: str = "",
    slack_channel:   str = "",
    model_override:  str = "",
    agent_notes_enabled: bool = True,
) -> CodingAgentState:
    """
    Return a fresh CodingAgentState with sensible defaults.
    Call this when starting a new coding session.
    """
    return CodingAgentState(
        messages=[],
        workspace_path=workspace_path,
        project_name=project_name,
        current_branch="",
        active_subagents=[],
        task_summary="",
        session_id=session_id,
        files_created=[],
        files_modified=[],
        task_complete=False,
        formatted_output=[],
        slack_thread_ts=slack_thread_ts,
        slack_channel=slack_channel,
        model_override=model_override,
        agent_notes_enabled=agent_notes_enabled,
    )
