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
from agents_utils.context_tracker import snapshot_context_usage
from agents_utils.memory import get_checkpointer
from agents_utils.config import LANGGRAPH_RECURSION_LIMIT
logger = logging.getLogger(__name__)

# ── Cancel-event registry ─────────────────────────────────────────────────────
# Maps session_id → threading.Event so coding_agent_node can check for cancellation
# without putting a non-serialisable object in LangGraph state.
_cancel_events: dict = {}
_cancel_events_lock = threading.Lock()

# Status-callback registry ──────────────────────────────────────────────────────
# Maps session_id → callable so coding_agent_node can inject Slack status updates.
_status_callbacks: dict = {}
_status_callbacks_lock = threading.Lock()


def register_cancel_event(session_id: str, event: threading.Event):
    with _cancel_events_lock:
        _cancel_events[session_id] = event


def unregister_cancel_event(session_id: str):
    with _cancel_events_lock:
        _cancel_events.pop(session_id, None)


def get_cancel_event(session_id: str) -> "threading.Event | None":
    with _cancel_events_lock:
        return _cancel_events.get(session_id)


def register_status_callback(session_id: str, callback: callable):
    with _status_callbacks_lock:
        _status_callbacks[session_id] = callback


def unregister_status_callback(session_id: str):
    with _status_callbacks_lock:
        _status_callbacks.pop(session_id, None)


def get_status_callback(session_id: str) -> "callable | None":
    with _status_callbacks_lock:
        return _status_callbacks.get(session_id)


# ── Node: coding_agent ────────────────────────────────────────────────────────

def coding_agent_node(state: CodingAgentState) -> dict:
    """
    Run the CodingAgent tool loop for one invocation.
    No message labels — the coding agent is a single-agent loop with no
    supervisor routing, so HUMAN/CODING AGENT labels are pure token waste.
    """
    workspace_path      = state.get("workspace_path", "")
    session_id          = state.get("session_id", "")
    cancel_event        = get_cancel_event(session_id) if session_id else None
    status_callback     = get_status_callback(session_id) if session_id else None
    model_override      = state.get("model_override", "")
    agent_notes_enabled = state.get("agent_notes_enabled", True)

    agent = CodingAgent(
        workspace_path=workspace_path,
        model_override=model_override,
        agent_notes_enabled=agent_notes_enabled,
        status_callback=status_callback,
    )
    # Inject session_id so base_agent._call_llm can record token activity
    agent._current_session_id = session_id
    agent._current_project_name = state.get("project_name", "")

    result_text, parsed = agent.run(
        messages=state.get("messages", []),
        state={
            "workspace_path":  workspace_path,
            "slack_thread_ts": state.get("slack_thread_ts", ""),
            "slack_channel":   state.get("slack_channel", ""),
        },
        cancel_event=cancel_event,
    )

    if result_text == "[cancelled]":
        return {
            "messages":      list(state.get("messages", [])),
            "task_complete": False,
            "task_summary":  "",
        }

    updated_messages = list(state.get("messages", [])) + [AIMessage(content=result_text)]

    # task_complete is signalled via the parsed dict returned by base_agent
    # when TASK_COMPLETE appeared in a tool result during this run.
    # We do NOT wipe messages here — output_node still needs them.
    # The reset happens in reset_node, after output_node has captured the response.
    task_complete = bool(parsed and parsed.get("task_complete"))
    if task_complete:
        logger.info("[coding_agent_node] TASK_COMPLETE detected")

    return {
        "messages":      updated_messages,
        "task_complete": task_complete,
        "task_summary":  result_text,
    }


# ── Node: output_node ─────────────────────────────────────────────────────────

def output_node(state: CodingAgentState) -> dict:
    """
    Format the agent's final response for posting to Slack.
    - Strips any JSON wrapper ({"response": "..."}) the model may still emit
    - Converts markdown to Slack mrkdwn format (headings, bold, italic, etc.)
    - Splits long messages into Slack-safe chunks
    - Filters out thinking blocks from Gemma 4's structured responses
    """
    from agents_utils.json_parser import _extract_json
    from nodes.output_formatter import _markdown_to_slack, _strip_labels, _build_formatted_output

    summary = state.get("task_summary", "")

    if not summary:
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                raw = msg.content
                # If content is a list of blocks (Gemma 4), extract text only, skip thinking
                if isinstance(raw, list):
                    text_parts = []
                    for block in raw:
                        if isinstance(block, dict):
                            block_type = block.get("type", "")
                            if block_type == "text":
                                text_parts.append(block.get("text", ""))
                            elif block_type == "thinking":
                                continue
                            elif "text" in block:
                                text_parts.append(block["text"])
                    summary = "".join(text_parts)
                else:
                    summary = raw if isinstance(raw, str) else str(raw)
                break

    # If the model returned {"response": "..."} despite native tool calling,
    # unwrap it so the user sees clean text, not raw JSON.
    if summary:
        parsed = _extract_json(summary)
        if parsed and "response" in parsed:
            summary = parsed["response"]

    if not summary or not summary.strip():
        return {"formatted_output": ["Coding task completed. No output was generated."]}

    # Clean internal labels
    clean = _strip_labels(summary)

    # Convert markdown → mrkdwn; extract table block if present
    slack_text, table_block = _markdown_to_slack(clean)
    formatted_output        = _build_formatted_output(slack_text, table_block)

    logger.info(
        f"[output_node] Response formatted: "
        f"{len(clean)} chars → {len(formatted_output)} item(s)"
        + (" (includes table block)" if table_block else "")
    )

    return {"formatted_output": formatted_output}


# ── Routing ───────────────────────────────────────────────────────────────────

def _route_after_coding_agent(state: CodingAgentState) -> str:
    """Route to output if agent produced a response; skip to END if cancelled."""
    if not state.get("task_summary", "") and not state.get("task_complete", False):
        return "end"  # cancelled
    return "output"


# ── Node: reset_node ──────────────────────────────────────────────────────────

def reset_node(state: CodingAgentState) -> dict:
    """
    Runs after output_node. Snapshots context usage, then wipes the message
    history when a task was completed (task_complete=True) so the next task
    starts with a clean context window.

    output_node has already captured the response in formatted_output
    before this runs, so wiping messages here is safe — the Slack
    response is completely unaffected.
    """
    task_complete = state.get("task_complete", False)

    # ── Context usage snapshot (always, not just on task_complete) ────────────
    # Import here to avoid circular imports at module load time.
    try:
        from coding_agent.prompts.main_agent_prompt import get_system_prompt
        system_prompt = get_system_prompt(
            workspace_path=state.get("workspace_path", ""),
            agent_notes_enabled=state.get("agent_notes_enabled", True),
        )
        snapshot_context_usage(
            session_id=state.get("session_id", ""),
            project_name=f"coding\\{state.get('project_name', '')}",
            messages=state.get("messages", []),
            system_prompt=system_prompt,
            model=state.get("model_override", ""),
            include_tool_schemas=True,
            workspace_path=state.get("workspace_path", ""),
            agent_notes_enabled=state.get("agent_notes_enabled", True),
            task_complete=task_complete,
        )
    except Exception as e:
        logger.warning(f"[reset_node] Context snapshot failed (non-fatal): {e}")

    if task_complete:
        logger.info("[reset_node] Task complete — wiping message history for next task")
        # Also reset token activity so the chart starts fresh for the next task
        try:
            from agents_utils.token_activity_tracker import reset_session
            reset_session(state.get("session_id", ""))
        except Exception:
            pass
        return {"messages": [], "task_complete": False}
    return {}


# ── Graph Builder ─────────────────────────────────────────────────────────────

def _build_coding_graph() -> StateGraph:
    graph = StateGraph(CodingAgentState)

    graph.add_node("coding_agent", coding_agent_node)
    graph.add_node("output",       output_node)
    graph.add_node("reset",        reset_node)

    graph.set_entry_point("coding_agent")

    graph.add_conditional_edges(
        "coding_agent",
        _route_after_coding_agent,
        {"output": "output", "end": END},
    )

    graph.add_edge("output", "reset")
    graph.add_edge("reset",  END)

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
    model_override:  str = "",
    agent_notes_enabled: bool = True,
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
        model_override:  Optional model name to override the default coding_agent model.
        agent_notes_enabled: Whether the agent can read/write learning notes.

    Returns:
        List of formatted output strings to post to Slack, or None if cancelled.
    """
    graph  = get_coding_graph()
    config = {
        "configurable": {"thread_id": session_id},
        "recursion_limit": LANGGRAPH_RECURSION_LIMIT,  # low limit for now, just for testing
    }

    # Try to resume an existing session
    try:
        existing          = graph.get_state(config)
        existing_messages = existing.values.get("messages", []) if existing.values else []
    except Exception:
        existing_messages = []

    if existing_messages:
        # Resume: append new message to existing history (no label prefix)
        input_state = {
            "messages":       existing_messages + [HumanMessage(content=prompt)],
            "workspace_path": workspace_path or existing.values.get("workspace_path", ""),
            "slack_thread_ts": slack_thread_ts,
            "slack_channel":   slack_channel,
            "task_complete":   False,
            "formatted_output": [],
            "model_override":  model_override,
            "agent_notes_enabled": agent_notes_enabled,
        }
    else:
        # New session — no label prefix on user message
        input_state = default_coding_state(
            workspace_path=workspace_path,
            project_name=project_name,
            session_id=session_id,
            slack_thread_ts=slack_thread_ts,
            slack_channel=slack_channel,
            model_override=model_override,
            agent_notes_enabled=agent_notes_enabled,
        )
        input_state["messages"] = [HumanMessage(content=prompt)]

    formatted_output = []

    # Register the cancel_event so coding_agent_node can look it up by session_id.
    # Also wire it into execute_shell so subprocesses are killed immediately on Stop
    # rather than waiting for the full timeout.
    if cancel_event and session_id:
        register_cancel_event(session_id, cancel_event)
    from tools.coding_tools import set_shell_cancel_event
    set_shell_cancel_event(cancel_event)

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
    finally:
        if session_id:
            unregister_cancel_event(session_id)
        set_shell_cancel_event(None)  # clear so it doesn't leak into the next session

    return formatted_output if formatted_output else None
