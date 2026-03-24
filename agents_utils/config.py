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
DB_PATH             = PROJECT_ROOT / "checkpoints.db"
LINKEDIN_STATE_PATH = PROJECT_ROOT / "linkedin_state.json"
RATE_LIMIT_FILE = PROJECT_ROOT / "rate_limit_state.json"
USER_PREFERENCES_FILE = PROJECT_ROOT / "user_preferences.json"

# Ensure directories exist
WORKSPACE_ROOT.mkdir(exist_ok=True)

# ── API Keys ──────────────────────────────────────────────────────────────────

GOOGLE_API_KEY  = os.getenv("GOOGLE_API_KEY")
JINA_API_KEY    = os.getenv("JINA_API_KEY")
SLACK_BOT_TOKEN = os.getenv("Bot_User_OAuth_Token")
SLACK_APP_TOKEN = os.getenv("agent_socket_token")

# ── Email ──────────────────────────────────────────────────────────────────────

HUMAN_EMAIL    = os.getenv("HUMAN_EMAIL")   # Sender email address (Gmail)
EMAIL_PASSWORD = os.getenv("EMAIL_PASS")    # Gmail App Password

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
#     "memory":            "gemini-3.1-flash-lite-preview", # runs only on compression trigger threshold
#     "validator":         "gemma-3n-e2b-it"
# }

MODELS = {
    "supervisor":        "gemma-3-27b-it",
    "planner":           "gemma-3-27b-it",
    "researcher":        "gemma-3-12b-it",
    "deep_researcher":   "gemma-3-12b-it",
    "email_composer":    "gemma-3-4b-it",
    "linkedin_composer": "gemma-3n-e4b-it",
    "task_classifier":   "gemma-3-27b-it",
    "memory":            "gemma-3-4b-it", # runs only on compression trigger threshold
    "validator":         "gemma-3n-e2b-it"
}

# ── Model Context Windows ──────────────────────────────────────────────────────

MODEL_CONTEXT_WINDOWS = {
    "gemini-3.1-flash-lite-preview": 250000,
    "gemma-3-27b-it": 128000,
    "gemma-3-12b-it": 128000,
    "gemma-3-4b-it":  128000,
    "gemma-3n-e2b-it": 32000,
    "gemma-3-1b-it":  32000,
    "gemma-3n-e4b-it": 128000,
}

# ── Retry Limits Per Agent ─────────────────────────────────────────────────────

MAX_RETRIES = {
    "supervisor":      5,
    "planner":         3,
    "researcher":      2,
    "deep_researcher": 2,
    "email_composer":  5,
    "linkedin_composer": 5,
    "task_classifier": 3,
    "memory":          5,
}

# ── Tool Call Limit ────────────────────────────────────────────────────────────

MAX_TOOL_ITERATIONS = 15

# ── Context Window Thresholds ──────────────────────────────────────────────────

CONTEXT_SUMMARIZE_THRESHOLD  = 0.10   # 10% → trigger summarization (~12,800 tokens)


# ── LangGraph Settings ─────────────────────────────────────────────────────────

LANGGRAPH_RECURSION_LIMIT = 100

# ── Timeouts ──────────────────────────────────────────────────────────────────

WORKSPACE_SELECTION_TIMEOUT = 120   # seconds to wait for workspace selection then reprompt
HUMAN_CONFIRMATION_TIMEOUT  = 300   # seconds to wait for human confirmation (5 min)
INTERRUPT_BUTTON_TIMEOUT    = 300   # seconds to wait for interrupt button click (5 min)

# ── Notes File ─────────────────────────────────────────────────────────────────

# ── Message Labels ─────────────────────────────────────────────────────────────

LABEL = {
    "human":                 "[HUMAN]",
    "supervisor":            "[SUPERVISOR]",
    "planner":               "[PLANNER]",
    "researcher":            "[RESEARCHER RESULT]",
    "deep_researcher":       "[DEEP RESEARCHER RESULT]",
    "email_composer":        "[EMAIL COMPOSER RESULT]",
    "linkedin_composer":     "[LINKEDIN COMPOSER RESULT]",
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
    ":(){:|:&};:",   # fork bomb
]

# ── Sensitive Operations (require human confirmation) ──────────────────────────

SENSITIVE_OPERATIONS = [
    "delete_file",
    "execute_shell",
    "write_outside_workspace",
]
