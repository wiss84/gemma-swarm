"""
Gemma Swarm — Coding Agent Graph
==================================
Wires the CodingAgent into a LangGraph StateGraph.

The coding agent graph is intentionally simple — it's a single-agent loop,
not a multi-agent supervisor graph. All orchestration happens inside
CodingAgent.run() via its tool loop. LangGraph provides session persistence
and the clean entry/exit boundary for Slack integration.

Flow:
    coding_agent_node → output_node → END

Nodes:
    coding_agent_node   — runs CodingAgent.run(), returns when task is complete
    output_node         — formats the final response for Slack

Checkpointing:
    Uses the same SqliteSaver checkpointer as the main graph (same DB file).
    Thread ID = session_id from CodingAgentState. Each coding session is
    independently resumable.

Public interface:
    get_coding_graph()                    — returns the compiled graph (singleton)
    run_coding_session(prompt, ...)       — invoke entry point used by Slack handler
"""

import logging
import threading
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, END

from coding_agent.state import CodingAgentState, default_coding_state
from coding_agent.agent import CodingAgent
from agents_utils.memory import get_checkpointer
from agents_utils.config import LABEL

logger = logging.getLogger(__name__)


# ── Node: coding_agent ────────────────────────────────────────────────────────

def coding_agent_node(state: CodingAgentState) -> dict:
    """
    Run the CodingAgent tool loop for one invocation.
    Passes workspace_path and slack context from state into the agent.
    Returns updated messages and task_complete flag.
    """
    workspace_path = state.get("workspace_path", "")
    agent = CodingAgent(workspace_path=workspace_path)

    result_text, _ = agent.run(
        messages=state.get("messages", []),
        state={
            "workspace_path":  workspace_path,
            "slack_thread_ts": state.get("slack_thread_ts", ""),
            "slack_channel":   state.get("slack_channel", ""),
        },
    )

    updated_messages = list(state.get("messages", [])) + [
        AIMessage(content=f"{LABEL['coding_agent']}\n{result_text}")
    ]

    return {
        "messages":     updated_messages,
        "task_complete": True,
        "task_summary":  result_text,
    }


# ── Node: output_node ─────────────────────────────────────────────────────────

def output_node(state: CodingAgentState) -> dict:
    """
    Format the agent's final response for posting to Slack.
    Extracts the task summary and wraps it cleanly.
    """
    summary = state.get("task_summary", "")

    if not summary:
        # Fallback: extract last AI message content
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                summary = msg.content
                break

    # Strip the label prefix if present (clean output for Slack)
    label = LABEL.get("coding_agent", "")
    if label and summary.startswith(label):
        summary = summary[len(label):].strip()

    formatted = [summary] if summary else ["Coding task completed. No output was generated."]

    return {"formatted_output": formatted}


# ── Routing ───────────────────────────────────────────────────────────────────

def _route_after_coding_agent(state: CodingAgentState) -> str:
    """Always route to output after the agent finishes."""
    return "output"


# ── Graph Builder ─────────────────────────────────────────────────────────────

def _build_coding_graph() -> StateGraph:
    graph = StateGraph(CodingAgentState)

    graph.add_node("coding_agent", coding_agent_node)
    graph.add_node("output",       output_node)

    graph.set_entry_point("coding_agent")

    graph.add_conditional_edges(
        "coding_agent",
        _route_after_coding_agent,
        {"output": "output"},
    )

    graph.add_edge("output", END)

    return graph


# ── Compiled Graph (singleton) ────────────────────────────────────────────────

_coding_graph = None
_checkpointer = None


def get_coding_graph():
    """
    Return the compiled CodingAgent graph. Singleton — built once per process.
    Uses the same SqliteSaver checkpointer as the main graph.
    """
    global _coding_graph, _checkpointer
    if _coding_graph is None:
        _checkpointer  = get_checkpointer()
        _coding_graph  = _build_coding_graph().compile(checkpointer=_checkpointer)
        logger.info("[coding_graph] Compiled successfully.")

        # Sanity check — print ASCII graph in debug mode
        try:
            logger.debug(
                "[coding_graph] Structure:\n"
                + _coding_graph.get_graph().draw_ascii()
            )
        except Exception:
            pass

    return _coding_graph


# ── Public Entry Point ────────────────────────────────────────────────────────

def run_coding_session(
    prompt:          str,
    session_id:      str,
    workspace_path:  str = "",
    project_name:    str = "",
    slack_thread_ts: str = "",
    slack_channel:   str = "",
    cancel_event:    threading.Event = None,
) -> list[str] | None:
    """
    Invoke the coding agent graph for one user prompt.

    Args:
        prompt:          The user's coding request.
        session_id:      Unique session ID — used as the LangGraph thread_id.
                         Reusing the same session_id resumes a previous session.
        workspace_path:  Absolute path to the project directory.
        project_name:    Human-readable project name.
        slack_thread_ts: Slack thread timestamp for posting status updates.
        slack_channel:   Slack channel ID.
        cancel_event:    Optional threading.Event — set it to interrupt mid-run.

    Returns:
        List of formatted output strings to post to Slack, or None if cancelled.
    """
    graph  = get_coding_graph()
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 100,  # low limit for now, just for testing
    }

    # Try to resume an existing session
    try:
        existing          = graph.get_state(config)
        existing_messages = existing.values.get("messages", []) if existing.values else []
    except Exception:
        existing_messages = []

    if existing_messages:
        # Resume: append new message to existing history
        input_state = {
            "messages": existing_messages + [
                HumanMessage(content=f"{LABEL['human']}\n{prompt}")
            ],
            "workspace_path":  workspace_path or existing.values.get("workspace_path", ""),
            "slack_thread_ts": slack_thread_ts,
            "slack_channel":   slack_channel,
            "task_complete":   False,
            "formatted_output": [],
        }
    else:
        # New session
        input_state = default_coding_state(
            workspace_path=workspace_path,
            project_name=project_name,
            session_id=session_id,
            slack_thread_ts=slack_thread_ts,
            slack_channel=slack_channel,
        )
        input_state["messages"] = [
            HumanMessage(content=f"{LABEL['human']}\n{prompt}")
        ]

    formatted_output = []

    try:
        for chunk in graph.stream(input_state, config, stream_mode="updates"):
            if cancel_event and cancel_event.is_set():
                logger.info(f"[coding_graph] Cancelled mid-run for session {session_id}")
                return None

            for node_name, node_output in chunk.items():
                if node_name == "output":
                    formatted_output = node_output.get("formatted_output", [])
                logger.debug(f"[coding_graph] Node completed: {node_name}")

    except Exception as e:
        logger.error(f"[coding_graph] Stream error for session {session_id}: {e}", exc_info=True)
        return [f"Coding session error: {e}"]

    return formatted_output if formatted_output else None
