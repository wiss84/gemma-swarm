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
from agents_utils.config import LABEL
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

# Nodes
from nodes.input_router    import input_router_node
from nodes.guard_rails     import guard_rails_node
from nodes.validator       import response_validator_node
from nodes.human_gate      import human_gate_node
from nodes.output_formatter import output_formatter_node

# Tools
from tools.file_tools import set_workspace

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

def _supervisor(state: AgentState) -> dict:
    return supervisor_agent_node(state)

def _researcher(state: AgentState) -> dict:
    return researcher_agent_node(state)

def _deep_researcher(state: AgentState) -> dict:
    return deep_researcher_agent_node(state)

def _email_composer(state: AgentState) -> dict:
    return email_composer_node(state)

def _email_send(state: AgentState) -> dict:
    return email_send_node(state)

def _linkedin_composer(state: AgentState) -> dict:
    return linkedin_composer_node(state)

def _linkedin_send(state: AgentState) -> dict:
    return linkedin_send_node(state)

def _memory(state: AgentState) -> dict:
    return memory_agent_node(state)

def _validator(state: AgentState) -> dict:
    return response_validator_node(state)

def _output_formatter(state: AgentState) -> dict:
    return output_formatter_node(state)

# Human gate needs Slack client — injected at runtime
_slack_client    = None
_slack_channel_fn = None

def set_slack_client(client, channel_fn):
    global _slack_client, _slack_channel_fn
    _slack_client    = client
    _slack_channel_fn = channel_fn

def _human_gate(state: AgentState) -> dict:
    return human_gate_node(state, client=_slack_client)


# ── Routing Functions ──────────────────────────────────────────────────────────

def _should_route_to_human_gate(state: AgentState) -> bool:
    """Check if graph should pause at human_gate for interrupt handling."""
    return state.get("is_interrupted", False)


def _route_after_input_router(state: AgentState) -> str:
    """Route to human_gate if interrupted, otherwise normal routing."""
    if _should_route_to_human_gate(state):
        return "human_gate"
    if state.get("next_node") == "memory":
        return "memory"
    return "guard_rails"


def _route_after_guard_rails(state: AgentState) -> str:
    if _should_route_to_human_gate(state):
        return "human_gate"
    if state.get("next_node") == "output_formatter":
        return "output_formatter"
    return "task_classifier"

def _route_after_task_classifier(state: AgentState) -> str:
    if _should_route_to_human_gate(state):
        return "human_gate"
    if state.get("is_complex_task"):
        return "planner"
    return "supervisor"

def _route_after_email_composer(state: AgentState) -> str:
    return "human_gate"


def _route_after_validator(state: AgentState) -> str:
    if _should_route_to_human_gate(state):
        return "human_gate"
    next_node = state.get("next_node", "output_formatter")
    if next_node == "supervisor":   return "supervisor"
    if next_node == "human_gate":   return "human_gate"
    return "output_formatter"


def _route_after_supervisor(state: AgentState) -> str:
    if _should_route_to_human_gate(state):
        return "human_gate"
    next_node = state.get("next_node", "")
    if next_node == "researcher":        return "researcher"
    if next_node == "deep_researcher":   return "deep_researcher"
    if next_node == "email_composer":    return "email_composer"
    if next_node == "linkedin_composer": return "linkedin_composer"
    if next_node == "human_gate":        return "human_gate"
    return "validator"


def _route_after_human_gate(state: AgentState) -> str:
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
    graph.add_node("human_gate",      _human_gate)
    graph.add_node("validator",        _validator)
    graph.add_node("output_formatter", _output_formatter)

    # ── Entry Point ────────────────────────────────────────────────────────────
    graph.set_entry_point("input_router")

    # ── Edges ──────────────────────────────────────────────────────────────────

    # input_router → guard_rails
    # input_router → memory (if compression needed) or guard_rails
    graph.add_conditional_edges(
        "input_router",
        _route_after_input_router,
        {
            "memory":      "memory",
            "guard_rails": "guard_rails",
        }
    )

    # guard_rails → task_classifier or output_formatter (blocked)
    graph.add_conditional_edges(
        "guard_rails",
        _route_after_guard_rails,
        {
            "task_classifier":  "task_classifier",
            "output_formatter": "output_formatter",
        }
    )

    # task_classifier → planner (complex) or supervisor (simple)
    graph.add_conditional_edges(
        "task_classifier",
        _route_after_task_classifier,
        {
            "planner":    "planner",
            "supervisor": "supervisor",
        }
    )

    # planner → supervisor (with interrupt check)
    def _route_after_planner(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "human_gate"
        return "supervisor"
    
    graph.add_conditional_edges(
        "planner",
        _route_after_planner,
        {
            "human_gate": "human_gate",
            "supervisor": "supervisor",
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
            "human_gate":        "human_gate",
            "validator":         "validator",
        }
    )

    # researcher → supervisor (with interrupt check)
    def _route_after_researcher(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "human_gate"
        return "supervisor"
    
    graph.add_conditional_edges(
        "researcher",
        _route_after_researcher,
        {
            "human_gate": "human_gate",
            "supervisor": "supervisor",
        }
    )

    # deep_researcher → supervisor (with interrupt check)
    def _route_after_deep_researcher(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "human_gate"
        return "supervisor"
    
    graph.add_conditional_edges(
        "deep_researcher",
        _route_after_deep_researcher,
        {
            "human_gate": "human_gate",
            "supervisor": "supervisor",
        }
    )


    # email_composer → human_gate (always)
    graph.add_conditional_edges(
        "email_composer",
        _route_after_email_composer,
        {"human_gate": "human_gate"}
    )

    # human_gate → email_send / linkedin_send (approved) or supervisor (rejected)
    graph.add_conditional_edges(
        "human_gate",
        _route_after_human_gate,
        {
            "email_send":    "email_send",
            "linkedin_send": "linkedin_send",
            "supervisor":    "supervisor",
        }
    )

    # email_send → supervisor (with interrupt check)
    def _route_after_email_send(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "human_gate"
        return "supervisor"
    
    graph.add_conditional_edges(
        "email_send",
        _route_after_email_send,
        {
            "human_gate": "human_gate",
            "supervisor": "supervisor",
        }
    )

    # linkedin_composer → human_gate (always)
    graph.add_edge("linkedin_composer", "human_gate")

    # linkedin_send → supervisor (with interrupt check)
    def _route_after_linkedin_send(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "human_gate"
        return "supervisor"
    
    graph.add_conditional_edges(
        "linkedin_send",
        _route_after_linkedin_send,
        {
            "human_gate": "human_gate",
            "supervisor": "supervisor",
        }
    )

    # memory → supervisor or guard_rails (with interrupt check)
    def _route_after_memory(state: AgentState) -> str:
        if _should_route_to_human_gate(state):
            return "human_gate"
        return "guard_rails"
    
    graph.add_conditional_edges(
        "memory",
        _route_after_memory,
        {
            "human_gate": "human_gate",
            "guard_rails": "guard_rails",
        }
    )

    # validator → output_formatter (on pass) or supervisor/human_gate (on fail)
    graph.add_conditional_edges(
        "validator",
        _route_after_validator,
        {
            "supervisor":       "supervisor",
            "human_gate":       "human_gate",
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
        ]:
            _rate_limiters[agent.model_name] = agent.rate_limiter
            # logger.info(f"[graph] Rate limiter registered: {agent.model_name}")

        logger.info("[graph] Graph compiled successfully.")
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
    config           = {"configurable": {"thread_id": actual_thread_id}, "recursion_limit": 100}

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



