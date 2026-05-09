"""
Gemma Swarm — Configuration
=============================
Central config for all agents, models, retries, paths, and limits.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# ── Project Root ───────────────────────────────────────────────────────────────

PROJECT_ROOT             = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT           = PROJECT_ROOT / "workspaces"
ASSISTANT_WORKSPACE_ROOT = WORKSPACE_ROOT / "assistant"
CODING_WORKSPACE_ROOT    = WORKSPACE_ROOT / "coding"

# Ensure workspace roots exist
WORKSPACE_ROOT.mkdir(exist_ok=True)
ASSISTANT_WORKSPACE_ROOT.mkdir(exist_ok=True)
CODING_WORKSPACE_ROOT.mkdir(exist_ok=True)
DB_PATH               = PROJECT_ROOT / "checkpoints.db"
LINKEDIN_STATE_PATH   = PROJECT_ROOT / "linkedin_state.json"
GOOGLE_STATE_PATH     = PROJECT_ROOT / "google_state.json"
GOOGLE_CREDS_PATH     = PROJECT_ROOT / "Google_creds.json"
RATE_LIMIT_FILE       = PROJECT_ROOT / "rate_limit_state.json"
USER_PREFERENCES_FILE = PROJECT_ROOT / "user_preferences.json"

WORKSPACE_ROOT.mkdir(exist_ok=True)

# ── API Keys ──────────────────────────────────────────────────────────────────

GOOGLE_API_KEY  = os.getenv("GOOGLE_API_KEY")
JINA_API_KEY    = os.getenv("JINA_API_KEY")
SLACK_BOT_TOKEN = os.getenv("Bot_User_OAuth_Token")
SLACK_APP_TOKEN = os.getenv("agent_socket_token")

# ── Email ──────────────────────────────────────────────────────────────────────

HUMAN_EMAIL    = os.getenv("HUMAN_EMAIL")
EMAIL_PASSWORD = os.getenv("EMAIL_PASS")

EMAIL_LAYOUTS_DIR = PROJECT_ROOT / "email_layouts"

# ── Model Assignments ──────────────────────────────────────────────────────────
#
# Gemma 3 / 3n models are discontinued April 30, 2026.
# All agents migrated to Gemma 4 family.
#
# Model selection rationale:
#   gemma-4-31b-it      — best reasoning, 256k context, 15 RPM / 1500 RPD
#                          used for orchestration and complex reasoning tasks
#   gemma-4-26b-a4b-it  — fast MoE architecture, 256k context, same RPD bucket
#                          used for structured/constrained tasks (JSON in/out)

MODELS = {
    # ── Main graph agents ──────────────────────────────────────────────────────
    "supervisor":  "gemma-4-26b-a4b-it",    # orchestration + direct tool calling
    "memory":      "gemma-4-31b-it",    # context compression
    "validator":   "gemma-4-26b-a4b-it", # pass/fail JSON validation
    # ── Coding Agent ──────────────────────────────────────────────────────────
    "coding_agent":    "gemma-4-31b-it",
    "coding_subagent": "gemma-4-26b-a4b-it",
}

# ── Model Context Windows ──────────────────────────────────────────────────────

MODEL_CONTEXT_WINDOWS = {
    "gemma-4-31b-it":       256000,
    "gemma-4-26b-a4b-it":   256000,
}

# ── Retry Limits Per Agent ─────────────────────────────────────────────────────

MAX_RETRIES = {
    "supervisor":  5,
    "memory":      5,
    "validator":   3,
    # ── Coding Agent ──────────────────────────────────────────────────────────
    "coding_agent":    5,
    "coding_subagent": 3,
}

MAX_RETRIES_SERVICE_UNAVAILABLE = {
    "gemma":  5,
    "gemini": 1,
}

MAX_RETRY_FAILS = 5

# ── Tool Call Limit ────────────────────────────────────────────────────────────

# Global default for all agents.
MAX_TOOL_ITERATIONS = 15

# Per-agent overrides for coding agent roles.
# The main coding agent needs more steps for complex tasks (research → write → validate → fix).
# The subagent gets a generous limit since it handles any task type.
CODING_MAX_TOOL_ITERATIONS = {
    "coding_agent":    100,
    "coding_subagent": 100,
}

# ── Context Window Thresholds ──────────────────────────────────────────────────

CONTEXT_SUMMARIZE_THRESHOLD = 0.70

# ── File Processing Limits ─────────────────────────────────────────────────────

MAX_CONTEXT_CHARS = 40000

# ── LangGraph Settings ─────────────────────────────────────────────────────────

LANGGRAPH_RECURSION_LIMIT = 1000

# ── Timeouts ──────────────────────────────────────────────────────────────────

WORKSPACE_SELECTION_TIMEOUT = 120
HUMAN_CONFIRMATION_TIMEOUT  = 300
INTERRUPT_BUTTON_TIMEOUT    = 300

# ── Message Labels ─────────────────────────────────────────────────────────────

LABEL = {
    "human":        "[HUMAN]",
    "supervisor":   "[SUPERVISOR]",
    "memory":       "[MEMORY]",
    "system":       "[SYSTEM]",
    "confirmation": "[AWAITING YOUR CONFIRMATION]",
    "tool_result":  "[TOOL RESULT]",
    # ── Coding Agent ──────────────────────────────────────────────────────────
    "coding_agent":    "[CODING AGENT]",
    "coding_subagent": "[CODING SUBAGENT]",
}

# ── Guard Rails — Blocked Patterns ────────────────────────────────────────────

# AGENT_GUARDS removed — supervisor now signals routing via next_node directly,
# not via routing flags. Kept for reference in git history.

BLOCKED_PATTERNS = [

    "rm -rf",
    "format c:",
    "drop database",
    "delete all",
    "sudo rm",
    ":(){:|:&};:",
]

# ── Sensitive Operations (require human confirmation) ──────────────────────────

SENSITIVE_OPERATIONS = [
    "delete_file",
    "execute_shell",
    "write_outside_workspace",
]
