"""
Gemma Swarm — Main Graph
==========================
Wires all agents and nodes into a single LangGraph pipeline.

Flow:
    input_router → guard_rails → supervisor
        → researcher → supervisor
        → deep_researcher → supervisor
        → email_composer → human_gate → email_send → supervisor
        → memory → supervisor
        → validator → output_formatter → END
"""

import logging
import threading
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END

from agents_utils.state import AgentState, default_state
from agents_utils.config import LABEL, LANGGRAPH_RECURSION_LIMIT
from agents_utils.memory import get_checkpointer

# Agents
from agents.task_classifier_agent  import task_classifier_node, get_task_classifier_agent
from agents.planner_agent           import planner_agent_node, get_planner_agent
from agents.supervisor_agent        import supervisor_agent_node, get_supervisor_agent
from agents.researcher_agent       import researcher_agent_node, get_researcher_agent
from agents.deep_researcher_agent  import deep_researcher_agent_node, get_deep_researcher_agent
from agents.email_composer_agent   import email_composer_node, email_send_node, get_email_composer_agent
from agents.linkedin_composer_agent import linkedin_composer_node, linkedin_send_node, get_linkedin_composer_agent
from agents.memory_agent           import memory_agent_node, get_memory_agent
from agents.docs_agent        import docs_agent_node, get_docs_agent
from agents.calendar_agent    import calendar_agent_node, get_calendar_agent
from agents.sheets_agent      import sheets_agent_node, get_sheets_agent
from agents.gmail_agent       import gmail_agent_node, get_gmail_agent


# Nodes
from nodes.input_router    import input_router_node
from nodes.guard_rails     import guard_rails_node
from nodes.validator       import response_validator_node
from nodes.human_gate      import (
    human_gate_node,
    interrupt_node,
    email_confirm_node,
    linkedin_confirm_node,
    google_confirm_node,
    general_confirm_node,
)
from nodes.output_formatter import output_formatter_node

# Tools
_workspace_path: str = ""

def set_workspace(path: str):
    global _workspace_path
    _workspace_path = path
    logger.info(f"[workspace] Set to: {path}")

logger = logging.getLogger(__name__)

# Rate limiters passed to context monitor — populated on first get_graph() call
_rate_limiters: dict = {}


# ── Node Wrappers ──────────────────────────────────────────────────────────────

def _input_router(state: AgentState) -> dict:
    return input_router_node(state)

def _guard_rails(state: AgentState) -> dict:
    return guard_rails_node(state)

def _task_classifier(state: AgentState) -> dict:
    return task_classifier_node(state)

def _planner(state: AgentState) -> dict:
    return planner_agent_node(state)

def _inject_session(state: AgentState) -> None:
    """
    Inject session_id and project_name onto all supervisor-flow agent singletons
    so that base_agent._call_llm can record token activity for every agent.
    Called at the top of every agent node wrapper.
    """
    session_id   = state.get("slack_thread_ts", "")
    project_name = f"assistant\\{state.get('project_name', '')}"
    for agent_fn in [
        get_supervisor_agent, get_researcher_agent, get_deep_researcher_agent,
        get_email_composer_agent, get_linkedin_composer_agent,
        get_memory_agent, get_docs_agent, get_calendar_agent,
        get_sheets_agent, get_gmail_agent, get_task_classifier_agent,
        get_planner_agent,
    ]:
        try:
            agent = agent_fn()
            agent._current_session_id    = session_id
            agent._current_project_name  = project_name
        except Exception:
            pass


def _supervisor(state: AgentState) -> dict:
    _inject_session(state)
    return supervisor_agent_node(state)

def _researcher(state: AgentState) -> dict:
    _inject_session(state)
    return researcher_agent_node(state)

def _deep_researcher(state: AgentState) -> dict:
    _inject_session(state)
    return deep_researcher_agent_node(state)

def _email_composer(state: AgentState) -> dict:
    _inject_session(state)
    return email_composer_node(state)

def _email_send(state: AgentState) -> dict:
    return email_send_node(state)

def _linkedin_composer(state: AgentState) -> dict:
    _inject_session(state)
    return linkedin_composer_node(state)

def _linkedin_send(state: AgentState) -> dict:
    return linkedin_send_node(state)

def _memory(state: AgentState) -> dict:
    _inject_session(state)
    return memory_agent_node(state)

def _validator(state: AgentState) -> dict:
    return response_validator_node(state)

def _output_formatter(state: AgentState) -> dict:
    return output_formatter_node(state)

def _docs_agent(state: AgentState) -> dict:
    _inject_session(state)
    return docs_agent_node(state)

def _calendar_agent(state: AgentState) -> dict:
    _inject_session(state)
    return calendar_agent_node(state)

def _sheets_agent(state: AgentState) -> dict:
    _inject_session(state)
    return sheets_agent_node(state)

def _gmail_agent(state: AgentState) -> dict:
    _inject_session(state)
    return gmail_agent_node(state)


# Human gate needs Slack client — injected at runtime
_slack_client    = None
_slack_channel_fn = None

def set_slack_client(client, channel_fn):
    global _slack_client, _slack_channel_fn
    _slack_client    = client
    _slack_channel_fn = channel_fn

def _human_gate(state: AgentState) -> dict:
    return human_gate_node(state, client=_slack_client)

def _interrupt_gate(state: AgentState) -> dict:
    return interrupt_node(state, client=_slack_client)

def _email_confirm(state: AgentState) -> dict:
    return email_confirm_node(state, client=_slack_client)

def _linkedin_confirm(state: AgentState) -> dict:
    return linkedin_confirm_node(state, client=_slack_client)

def _google_confirm(state: AgentState) -> dict:
    return google_confirm_node(state, client=_slack_client)

def _general_confirm(state: AgentState) -> dict:
    return general_confirm_node(state, client=_slack_client)


# ── Routing Functions ──────────────────────────────────────────────────────────

def _should_route_to_human_gate(state: AgentState) -> bool:
    """Check if graph should pause at human_gate for interrupt handling."""
    return state.get("is_interrupted", False)


def _route_after_input_router(state: AgentState) -> str:
    """Route to interrupt_node if interrupted, otherwise normal routing."""
    if _should_route_to_human_gate(state):
        return "interrupt_node"
    if state.get("next_node") == "memory":
        return "memory"
    return "guard_rails"


def _route_after_guard_rails(state: AgentState) -> str:
    if _should_route_to_human_gate(state):
        return "interrupt_node"
    if state.get("next_node") == "output_formatter":
        return "output_formatter"
    return "task_classifier"

def _route_after_task_classifier(state: AgentState) -> str:
    if _should_route_to_human_gate(state):
        return "interrupt_node"
    if state.get("is_complex_task"):
        return "planner"
    return "supervisor"

def _route_after_email_composer(state: AgentState) -> str:
    return "email_confirm"


def _route_after_validator(state: AgentState) -> str:
    if _should_route_to_human_gate(state):
        return "interrupt_node"
    next_node = state.get("next_node", "output_formatter")
    if next_node == "supervisor":   return "supervisor"
    if next_node == "human_gate":   return "general_confirm"
    return "output_formatter"


def _route_after_supervisor(state: AgentState) -> str:
    if _should_route_to_human_gate(state):
        return "interrupt_node"
    next_node = state.get("next_node", "")
    if next_node == "researcher":
        return "researcher"
    if next_node == "deep_researcher":
        return "deep_researcher"
    if next_node == "email_composer":
        return "email_composer"
    if next_node == "linkedin_composer":
        return "linkedin_composer"
    if next_node == "docs_agent":
        return "docs_agent"
    if next_node == "calendar_agent":
        return "calendar_agent"
    if next_node == "sheets_agent":
        return "sheets_agent"
    if next_node == "gmail_agent":
        return "gmail_agent"
    
    # If supervisor wants a confirmation for a Google agent
    if next_node in {"calendar_agent", "docs_agent", "sheets_agent", "gmail_agent"} and state.get("google_requires_confirmation", False):
        return "google_confirm"

    return "validator"


def _route_after_interrupt_node(state: AgentState) -> str:
    """Route after interrupt decision."""
    decision = state.get("human_decision", "").lower().strip()
    if decision == "rejected":
        return "supervisor"
    return "end"


def _route_after_confirm_node(state: AgentState) -> str:
    """Route after a confirmation decision (email, linkedin, google, general)."""
    decision     = state.get("human_decision", "").lower().strip()
    active_agent = state.get("active_agent", "").lower().strip()

    if decision == "approved":
        if active_agent == "email_composer":
            return "email_send"
        if active_agent == "linkedin_composer":
            return "linkedin_send"
        return "supervisor"

    # Rejected (with or without feedback) → always back to supervisor
    return "supervisor"


def _build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # ── Nodes ──────────────────────────────────────────────────────────────────
    graph.add_node("input_router",      _input_router)
    graph.add_node("task_classifier",   _task_classifier)
    graph.add_node("planner",           _planner)
    graph.add_node("guard_rails",     _guard_rails)
    graph.add_node("supervisor",      _supervisor)
    graph.add_node("researcher",      _researcher)
    graph.add_node("deep_researcher", _deep_researcher)
    graph.add_node("email_composer",  _email_composer)
    graph.add_node("email_send",         _email_send)
    graph.add_node("linkedin_composer",  _linkedin_composer)
    graph.add_node("linkedin_send",      _linkedin_send)
    graph.add_node("memory",          _memory)
    graph.add_node("interrupt_node",   _interrupt_gate)
    graph.add_node("email_confirm",    _email_confirm)
    graph.add_node("linkedin_confirm", _linkedin_confirm)
    graph.add_node("google_confirm",   _google_confirm)
    graph.add_node("general_confirm",  _general_confirm)
    graph.add_node("validator",        _validator)
    graph.add_node("output_formatter", _output_formatter)
    graph.add_node("docs_agent",       _docs_agent)
    graph.add_node("calendar_agent",  _calendar_agent)
    graph.add_node("sheets_agent",    _sheets_agent)
    graph.add_node("gmail_agent",     _gmail_agent)


    # ── Entry Point ────────────────────────────────────────────────────────────
    graph.set_entry_point("input_router")

    # ── Edges ──────────────────────────────────────────────────────────────────

    # input_router → guard_rails
    # input_router → memory (if compression needed) or guard_rails
    graph.add_conditional_edges(
        "input_router",
        _route_after_input_router,
        {
            "memory":        "memory",
            "guard_rails":   "guard_rails",
            "interrupt_node": "interrupt_node",
        }
    )

    # guard_rails → task_classifier or output_formatter (blocked)
    graph.add_conditional_edges(
        "guard_rails",
        _route_after_guard_rails,
        {
            "task_classifier":  "task_classifier",
            "output_formatter": "output_formatter",
            "interrupt_node":   "interrupt_node",
        }
    )

    # task_classifier → planner (complex) or supervisor (simple)
    graph.add_conditional_edges(
        "task_classifier",
        _route_after_task_classifier,
        {
            "planner":       "planner",
            "supervisor":    "supervisor",
            "interrupt_node": "interrupt_node",
        }
    )

    # planner → supervisor (with interrupt check)
    def _route_after_planner(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "supervisor"
    
    graph.add_conditional_edges(
        "planner",
        _route_after_planner,
        {
            "interrupt_node": "interrupt_node",
            "supervisor":     "supervisor",
        }
    )

    # supervisor → agents based on next_node
    graph.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        {
            "researcher":        "researcher",
            "deep_researcher":   "deep_researcher",
            "email_composer":    "email_composer",
            "linkedin_composer": "linkedin_composer",
            "interrupt_node":    "interrupt_node",
            "validator":         "validator",
            "docs_agent":       "docs_agent",
            "calendar_agent":  "calendar_agent",
            "sheets_agent":    "sheets_agent",
            "gmail_agent":     "gmail_agent",
        }
    )

    # researcher → supervisor (with interrupt check)
    def _route_after_researcher(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "supervisor"
    
    graph.add_conditional_edges(
        "researcher",
        _route_after_researcher,
        {
            "interrupt_node": "interrupt_node",
            "supervisor":     "supervisor",
        }
    )

    def _route_after_docs_agent(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "supervisor"
    
    graph.add_conditional_edges("docs_agent", _route_after_docs_agent, {
        "interrupt_node": "interrupt_node",
        "supervisor":     "supervisor",
    })

    def _route_after_calendar_agent(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "supervisor"
    
    graph.add_conditional_edges("calendar_agent", _route_after_calendar_agent, {
        "interrupt_node": "interrupt_node",
        "supervisor":     "supervisor",
    })

    def _route_after_sheets_agent(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "supervisor"
    
    graph.add_conditional_edges("sheets_agent", _route_after_sheets_agent, {
        "interrupt_node": "interrupt_node",
        "supervisor":     "supervisor",
    })

    def _route_after_gmail_agent(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "supervisor"
    
    graph.add_conditional_edges("gmail_agent", _route_after_gmail_agent, {
        "interrupt_node": "interrupt_node",
        "supervisor":     "supervisor",
    })


    # deep_researcher → supervisor (with interrupt check)
    def _route_after_deep_researcher(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "supervisor"
    
    graph.add_conditional_edges(
        "deep_researcher",
        _route_after_deep_researcher,
        {
            "interrupt_node": "interrupt_node",
            "supervisor":     "supervisor",
        }
    )


    # email_composer → email_confirm (always)
    graph.add_conditional_edges(
        "email_composer",
        _route_after_email_composer,
        {"email_confirm": "email_confirm"}
    )

    # human_gate → email_send / linkedin_send (approved) or supervisor (rejected)
    graph.add_conditional_edges(
        "interrupt_node",
        _route_after_interrupt_node,
        {
            "supervisor":    "supervisor",
            "end":           END,
        }
    )

    graph.add_conditional_edges(
        "email_confirm",
        _route_after_confirm_node,
        {
            "email_send":    "email_send",
            "supervisor":    "supervisor",
        }
    )

    graph.add_conditional_edges(
        "linkedin_confirm",
        _route_after_confirm_node,
        {
            "linkedin_send": "linkedin_send",
            "supervisor":    "supervisor",
        }
    )

    graph.add_conditional_edges(
        "google_confirm",
        _route_after_confirm_node,
        {
            "supervisor":    "supervisor",
        }
    )

    graph.add_conditional_edges(
        "general_confirm",
        _route_after_confirm_node,
        {
            "output_formatter": "output_formatter",
            "supervisor":       "supervisor",
        }
    )

    # email_send → supervisor (with interrupt check)
    def _route_after_email_send(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "supervisor"
    
    graph.add_conditional_edges(
        "email_send",
        _route_after_email_send,
        {
            "interrupt_node": "interrupt_node",
            "supervisor":     "supervisor",
        }
    )

    # linkedin_composer → linkedin_confirm (always)
    graph.add_edge("linkedin_composer", "linkedin_confirm")

    # linkedin_send → supervisor (with interrupt check)
    def _route_after_linkedin_send(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "supervisor"
    
    graph.add_conditional_edges(
        "linkedin_send",
        _route_after_linkedin_send,
        {
            "interrupt_node": "interrupt_node",
            "supervisor":     "supervisor",
        }
    )

    # memory → supervisor or guard_rails (with interrupt check)
    def _route_after_memory(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "interrupt_node"
        return "guard_rails"
    
    graph.add_conditional_edges(
        "memory",
        _route_after_memory,
        {
            "interrupt_node": "interrupt_node",
            "guard_rails":    "guard_rails",
        }
    )

    # validator → output_formatter (on pass) or supervisor/human_gate (on fail)
    graph.add_conditional_edges(
        "validator",
        _route_after_validator,
        {
            "supervisor":       "supervisor",
            "general_confirm":  "general_confirm",
            "interrupt_node":   "interrupt_node",
            "output_formatter": "output_formatter",
        }
    )

    # output_formatter → END
    graph.add_edge("output_formatter", END)

    return graph


# ── Compiled Graph (singleton) ─────────────────────────────────────────────────

_graph        = None
_checkpointer = None


def get_graph():
    global _graph, _checkpointer
    if _graph is None:
        _checkpointer = get_checkpointer()
        compiled      = _build_graph().compile(checkpointer=_checkpointer)
        _graph        = compiled

        # Register rate limiters for context monitor
        for agent in [
            get_task_classifier_agent(),
            get_planner_agent(),
            get_supervisor_agent(),
            get_researcher_agent(),
            get_deep_researcher_agent(),
            get_email_composer_agent(),
            get_linkedin_composer_agent(),
            get_memory_agent(),
            get_docs_agent(),
            get_calendar_agent(),
            get_sheets_agent(),
            get_gmail_agent(),
        ]:
            _rate_limiters[agent.model_name] = agent.rate_limiter
            # logger.info(f"[graph] Rate limiter registered: {agent.model_name}")

        # logger.info("[graph] Graph compiled successfully.")
    return _graph


# ── Public Interface ───────────────────────────────────────────────────────────

def ask(
    message: str,
    thread_id: str,
    workspace_path:  str = "",
    project_name:    str = "",
    slack_thread_ts: str = "",
    slack_channel:   str = "",
    cancel_event: threading.Event = None,
    thread_id_override: str = None,
) -> list[str] | None:
    graph            = get_graph()
    actual_thread_id = thread_id_override or thread_id
    config           = {"configurable": {"thread_id": actual_thread_id}, "recursion_limit": LANGGRAPH_RECURSION_LIMIT}

    if workspace_path:
        set_workspace(workspace_path)

    try:
        existing          = graph.get_state(config)
        existing_messages = existing.values.get("messages", []) if existing.values else []
    except Exception:
        existing_messages = []

    if existing_messages:
        input_state = {
            "messages": existing_messages + [
                HumanMessage(content=f"{LABEL['human']}\n{message}")
            ],
            "slack_thread_ts": slack_thread_ts,
            "slack_channel":   slack_channel,
            "task_complete":   False,
            "formatted_output": [],
        }
    else:
        input_state = default_state(
            original_task=message,
            workspace_path=workspace_path,
            project_name=project_name,
            slack_thread_ts=slack_thread_ts,
            slack_channel=slack_channel,
        )
        input_state["messages"] = [
            HumanMessage(content=f"{LABEL['human']}\n{message}")
        ]

    formatted_output = []

    try:
        for chunk in graph.stream(input_state, config, stream_mode="updates"):

            if cancel_event and cancel_event.is_set():
                logger.info(f"[graph] Cancelled mid-run for thread {actual_thread_id}")
                return None

            for node_name, node_output in chunk.items():
                if node_name == "output_formatter":
                    formatted_output = node_output.get("formatted_output", [])

                logger.debug(f"[graph] Node completed: {node_name}")

    except Exception as e:
        logger.error(f"[graph] Stream error: {e}")
        return [f"An error occurred: {e}"]

    return formatted_output if formatted_output else None



