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

PROJECT_ROOT    = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT  = PROJECT_ROOT / "workspaces"
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

# MODELS = {
#     "supervisor":        "gemini-3.1-flash-lite-preview",
#     "planner":           "gemma-3-27b-it",
#     "researcher":        "gemma-3-12b-it",
#     "deep_researcher":   "gemini-3.1-flash-lite-preview",
#     "email_composer":    "gemma-3-4b-it",
#     "linkedin_composer": "gemma-3n-e4b-it",
#     "task_classifier":   "gemma-3-27b-it",
#     "memory":            "gemini-3.1-flash-lite-preview",
#     "validator":         "gemma-3n-e2b-it",
#     # Google agents — focused, small models
#     "gmail_agent":       "gemma-3-4b-it",
#     "calendar_agent":    "gemma-3-4b-it",
#     "docs_agent":        "gemma-3-4b-it",
#     "sheets_agent":      "gemma-3-4b-it",
# }

# Fallback model assignments (all Gemma)
MODELS = {
    "supervisor":        "gemma-3-27b-it",
    "planner":           "gemma-3-27b-it",
    "researcher":        "gemma-3-12b-it",
    "deep_researcher":   "gemma-3-12b-it",
    "email_composer":    "gemma-3-4b-it",
    "linkedin_composer": "gemma-3n-e4b-it",
    "task_classifier":   "gemma-3-27b-it",
    "memory":            "gemma-3-4b-it",
    "validator":         "gemma-3n-e2b-it",
    "gmail_agent":       "gemma-3-4b-it",
    "calendar_agent":    "gemma-3-4b-it",
    "docs_agent":        "gemma-3-4b-it",
    "sheets_agent":      "gemma-3-4b-it",
}

# ── Model Context Windows ──────────────────────────────────────────────────────

MODEL_CONTEXT_WINDOWS = {
    "gemini-3.1-flash-lite-preview": 250000,
    "gemma-3-27b-it":  128000,
    "gemma-3-12b-it":  128000,
    "gemma-3-4b-it":   128000,
    "gemma-3n-e2b-it":  32000,
    "gemma-3-1b-it":    32000,
    "gemma-3n-e4b-it": 128000,
}

# ── Retry Limits Per Agent ─────────────────────────────────────────────────────

MAX_RETRIES = {
    "supervisor":        5,
    "planner":           3,
    "researcher":        2,
    "deep_researcher":   2,
    "email_composer":    5,
    "linkedin_composer": 5,
    "task_classifier":   3,
    "memory":            5,
    "gmail_agent":       3,
    "calendar_agent":    3,
    "docs_agent":        3,
    "sheets_agent":      3,
}

MAX_RETRIES_SERVICE_UNAVAILABLE = {
    "gemma":  5,
    "gemini": 1,
}

MAX_RETRY_FAILS = 5

# ── Tool Call Limit ────────────────────────────────────────────────────────────

MAX_TOOL_ITERATIONS = 15

# ── Context Window Thresholds ──────────────────────────────────────────────────

CONTEXT_SUMMARIZE_THRESHOLD = 0.10

# ── File Processing Limits ─────────────────────────────────────────────────────

MAX_CONTEXT_CHARS = 40000

# ── LangGraph Settings ─────────────────────────────────────────────────────────

LANGGRAPH_RECURSION_LIMIT = 100

# ── Timeouts ──────────────────────────────────────────────────────────────────

WORKSPACE_SELECTION_TIMEOUT = 120
HUMAN_CONFIRMATION_TIMEOUT  = 300
INTERRUPT_BUTTON_TIMEOUT    = 300

# ── Message Labels ─────────────────────────────────────────────────────────────

LABEL = {
    "human":                 "[HUMAN]",
    "supervisor":            "[SUPERVISOR]",
    "planner":               "[PLANNER]",
    "researcher":            "[RESEARCHER RESULT]",
    "deep_researcher":       "[DEEP RESEARCHER RESULT]",
    "email_composer":        "[EMAIL COMPOSER RESULT]",
    "linkedin_composer":     "[LINKEDIN COMPOSER RESULT]",
    "gmail_agent":           "[GMAIL AGENT RESULT]",
    "calendar_agent":        "[CALENDAR AGENT RESULT]",
    "docs_agent":            "[DOCS AGENT RESULT]",
    "sheets_agent":          "[SHEETS AGENT RESULT]",
    "memory":                "[MEMORY]",
    "system":                "[SYSTEM]",
    "confirmation":          "[AWAITING YOUR CONFIRMATION]",
    "tool_result":           "[TOOL RESULT]",
}

# ── Guard Rails — Blocked Patterns ────────────────────────────────────────────

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
