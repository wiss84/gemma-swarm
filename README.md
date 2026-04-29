# Gemma Swarm ⚡

**Stop context-switching. Do your entire workflow in Slack.**

Gemma Swarm is a fully free, open-source multi-agent AI assistant that lives in your Slack workspace. Research topics, compose emails, schedule meetings, manage files, write code, and publish to LinkedIn — all through natural conversation with the bot.

Built on **Google's Gemma 4 models** (free tier) and powered by completely free integrations — no paid subscriptions, no credit card required.

> **Active Development** — This project is actively being improved with new features and agents continuously added. Contributions and feedback are welcome.

---

## Video Demos

- 📧 **Email Workflow** — Compose, get feedback, send with interrupts: https://youtu.be/LfiQYaT1l9Q
- 🤖 **Autonomous Mode** — Background research & LinkedIn posting: https://youtu.be/u5iaSv6Hi2U
- 💻 **Coding Agent** — Autonomous coding from Slack: https://youtu.be/ZCeozC2UQQc

---

## What You Can Do (In Slack)

💬 **Ask a question** → The bot researches it instantly and posts findings  
📧 **"Draft an email to Sarah about..."** → Get a polished draft, approve, and send (all in one place)  
📅 **"Schedule a meeting for..."** → Create and manage calendar events without leaving Slack  
📄 **"Create a doc with..."** → Generate Google Docs on the fly  
📊 **"Make a spreadsheet of..."** → Build and manage sheets in seconds  
💼 **"Post to LinkedIn about..."** → Compose and publish with media attachments  
📭 **Monitor emails** → Get alerts for important senders + daily inbox summaries  
🤖 **Run tasks in the background** → Schedule research, emails, and LinkedIn posts autonomously  
🛡️ **Interrupt & adjust** → Send a new message while the bot is working and choose to combine, restart, or queue  
💻 **Write code** → Build applications, fix bugs, refactor, test — full IDE in Slack with autonomous coding agent

All with human-in-the-loop approvals before sensitive actions (email sends, LinkedIn posts, destructive file operations).

---

## Not Home? 
No worries, We got you. Download slack app on your phone and stay in Control from anywhere.

---

## Why Gemma Swarm?

✅ **Completely Free** — No API fees, no paid tiers, no credit card  
📍 **Runs Locally** — Your data stays in your workspace (uses SQLite for memory)  
🔐 **Privacy-First** — Open source, your workspace, your control  
🎯 **No More Context-Switching** — Everything happens in Slack  
⚡ **Straightforward Setup** — Get all API keys and run (30 mins first time)  
🧠 **Persistent Memory** — Conversations survive restarts and scale gracefully  
📚 **Multi-Model Support** — Specialized agents pick the right model for each task  
💻 **Full Coding Workspace** — Autonomous coding with git, file editing, validation, and agent learning  
🖥️ **Context Monitor** — Desktop widget shows accumulated context window usage (conversation history + system prompt + tool schemas) as percentage of model limit, updating after each turn

---

## Quick Start

### 1. Download
```bash
# Clone the repo
git clone https://github.com/yourusername/gemma-swarm.git
```

### 2. Get Free API Keys (30-40 minutes 1 time setup)

| Service | Required | Setup Guide |
|---------|----------|-------------|
| Google Gemma API | ✅ Yes | [Get API Key](https://aistudio.google.com/u/0/api-keys) |
| Jina AI (web search) | ✅ Yes | [Get API Key](https://jina.ai/) — no signup required |
| Slack | ✅ Yes | [Slack Workflow Setup](SLACK_WORKFLOW_SETUP.md) |
| Gmail (email sending) | ✅ Yes | [Email Workflow Setup](Email_WORKFLOW_SETUP.md) |
| LinkedIn (posting) | ✅ Yes | [LinkedIn Workflow Setup](LINKEDIN_WORKFLOW_SETUP.md) |
| Google Workspace | ✅ Yes | [Google Workflow Setup](Google_WORKFLOW_SETUP.md) |

### 3. Environment Variables

Create a `.env` file in the project root:

```bash
# Google Gemma API (required)
GOOGLE_API_KEY=your_google_api_key

# Jina AI — web search (required)
JINA_API_KEY=your_jina_api_key

# Slack (required)
Bot_User_OAuth_Token=xoxb-your-bot-token
agent_socket_token=xapp-your-socket-token

# Gmail — email sending (required)
HUMAN_EMAIL=your_email@gmail.com
EMAIL_PASSWORD=your_gmail_app_password

# LinkedIn — posting (required)
LINKEDIN_CLIENT_ID=your_linkedin_client_id
LINKEDIN_CLIENT_SECRET=your_linkedin_client_secret

# Google Workspace — OAuth credentials (required)
# Place Google_creds.json in project root (see Google_WORKFLOW_SETUP.md)

# LangSmith — tracing (optional, for debugging)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_PROJECT=gemma-swarm
```

### 4. Install

#### Option 1 — Automated Setup (Easiest)

**Windows:** Double-click **`setup.bat`**  
**macOS / Linux:** Run `./setup.sh` in terminal (may need `chmod +x setup.sh` first)

The setup script will automatically:
1. Detect or install Miniconda
2. Create two Conda environments:
   - `gemma_swarm` — main app (Python 3.11)
   - `gemma_test` — **required** environment where all coding agent tools execute (pytest, ruff, flake8, mypy, magika)
3. Install all dependencies from `requirements.txt` into both environments
4. Install Node.js LTS (for JavaScript/TypeScript validation)
5. Install TypeScript and ESLint globally
6. Set up the ts-morph bridge for semantic JS/TS analysis
7. Create a launcher script (`gemma-swarm.bat` or `gemma-swarm.sh`)
8. Create a desktop shortcut (Windows/macOS)

After setup completes, use the launcher to start the app.

#### Option 2 — Manual (All Platforms)

```bash
# Create the main app environment (must be named gemma_swarm)
python -m venv gemma_swarm
source gemma_swarm/bin/activate  # Windows: gemma_swarm\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create separate environment for coding agent tool execution
# All shell commands, validation, and Python tool calls run in gemma_test
python -m venv gemma_test
source gemma_test/bin/activate  # Windows: gemma_test\Scripts\activate

# Install coding agent dependencies (pytest, ruff, flake8, mypy, magika)
pip install pytest ruff flake8 mypy magika

# Deactivate gemma_test and return to main environment
deactivate

# Install Node.js LTS (for JavaScript/TypeScript validation)
#   macOS: brew install node
#   Linux (Debian/Ubuntu): curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs
#   Windows: Download from nodejs.org or use winget: winget install OpenJS.NodeJS.LTS

# Install TypeScript and ESLint globally
npm install -g typescript eslint

# Install ts-morph bridge for semantic JS/TS analysis
cd tools/ts_analysis_bridge && npm install --prefer-offline && cd ../..

# Run the app (from gemma_swarm environment)
source gemma_swarm/bin/activate  # Windows: gemma_swarm\Scripts\activate
python slack_app.py
```

**Important:** 
- The `gemma_swarm` environment is the **main app** — must be activated to run `slack_app.py`
- The `gemma_test` environment is **required** — all coding agent Python tools (validation, testing, package queries, installs) execute there. Both environments must exist for the coding agent to function.

**→ Done. Start chatting.**

---

## Completely Free

Every component was deliberately chosen to avoid costs:

| Component | Free Tier |
|-----------|-----------|
| **Gemma 4 models** | Google Gemini API free tier |
| **Web research** | Free web fetch fallback → Jina AI — free with API key |
| **Email** | Gmail SMTP — free with App Password |
| **LinkedIn** | LinkedIn API — free with developer app |
| **Google Workspace** | Gmail, Calendar, Docs, Sheets APIs — free with OAuth |
| **Slack** | Slack Bolt — free for personal workspaces |
| **Memory** | SQLite — local file, no cloud costs |
| **Coding** | Local workspace + git — no cloud IDE fees |

No token counter watching your budget. Just use it.

---

## Autonomous Mode (Optional)

Run scheduling tasks in the background without Slack interaction:

🚀 **Email Watch** — Monitor specific senders, get Slack alerts for new emails  
📬 **Inbox Check** — Daily digest of unread emails  
📅 **Calendar Reminders** — Get notified of upcoming events  
🔍 **Auto-Research** — Periodically research topics and auto-save findings  
📊 **Activity Logging** — All autonomous actions logged to a Google Sheet

Configure via the **Autonomous Settings** button in the Slack workspace menu.

---

## Full Feature List

### Core Capabilities

- 🤖 **Multi-Agent Orchestration** — Supervisor, Planner, Researcher, Deep Researcher, Email Composer, LinkedIn Composer, Memory Agent, Task Classifier, **Coding Agent**
- 💻 **Autonomous Coding** — Write, edit, validate, test, and commit code with full workspace management
- 📧 **Gmail Integration** — read, search, and manage your Gmail inbox
- 📅 **Google Calendar** — create, read, and manage calendar events
- 📄 **Google Docs** — create and edit documents
- 📊 **Google Sheets** — create and manage spreadsheets
- 💬 **Slack-Native** — full human-in-the-loop confirmations, interrupt handling, file uploads, and real-time status updates
- 📧 **Email Automation** — compose, review, and send emails via Gmail SMTP with attachment support
- 💼 **LinkedIn Posting** — create and publish posts with image, video, and document (PDF/PPTX) attachments
- 🔍 **Web Research** — quick search and deep research modes with full page reading
- 🧠 **Persistent Memory** — conversation history survives restarts via SQLite checkpointing
- 📋 **Multi-Task Planning** — automatically detects complex requests and breaks them into ordered subtasks
- ⚡ **Interrupt Handling** — send a new message while the bot is working and choose to combine, fresh start, or queue
- 🗜️ **Context Compression** — automatic rolling summarization when context approaches the model's limit
- 🛡️ **Guard Rails** — safety checks blocking dangerous operations before they reach any agent
- 📁 **Workspace Management** — each project gets its own folder for research, drafts, and attachments
- ⚙️ **User Preferences** — personalize the bot's name, tone, and communication style
- 🖥️ **Context Monitor UI** — desktop widget shows accumulated context window usage (tokens consumed vs model limit), model, and project per session
- 📝 **Agent Learning Notes** — coding agent records insights across sessions to improve over time
- 🔀 **Git Integration** — automatic repo init, commits, and history tracking for coding projects
- 🎯 **Configurable Coding Settings** — human gate bypass, max iterations, model override per project

---

## Architecture

### Agent Flow

```
               New Message
                    │
                    ▼
               Input Router ──→ Memory Agent (if context > 70% threshold)
                    │                    │
                    └────────────────────┘
                    ▼
               Guard Rails
                    │
                    ▼
               Task Classifier
                    │
                    ├── Simple Task ──→ Supervisor
                    │
                    └── Complex Task ──→ Planner ──→ Supervisor
                                                       │
                                   ┌──────────────────────┼──────────────────────┐
                                   ▼                      ▼                      ▼
                              Researcher          Deep Researcher          Email Composer
                                   │                      │                LinkedIn Composer
                                   │                      │                   Gmail Agent
                                   │                      │                Calendar Agent
                                   │                      │                   Docs Agent
                                   │                      │                  Sheets Agent
                                   └──────────────────────┘
                                                       │                       │
                                                       │                   Human Gate
                                                       │            (approve / reject with feedback)
                                                       ┌─────────        Email / LinkedIn Send
                                                       │
                                                  Validator
                                                       │
                                                  Output Formatter
                                                       │
                                                       END
```

**Separate Coding Agent Graph** (accessed via "💻 Coding Agent" button):

```
               Coding Prompt
                    │
                    ▼
               Coding Agent (gemma-4-31b-it) ──→ spawn_subagent (gemma-4-26b-a4b-it) [optional]
                    │                                 │
                    ├─→ read/write/edit files   ──→   │
                    ├─→ execute shell commands  ──→   │
                    ├─→ git operations          ──→   │
                    ├─→ validate & test         ──→   │
                    ├─→ research (web/package)  ──→   │
                    └─→ update TODO, write notes      │
                    │                                 ▼
                    ┌───────────────────── Subagent returns summary
                    │
               Output Node
                    │
               Reset Node (Experimental)
                    │                          
                    │
                    ▼
          Final response to Slack
```

**Key design:** A dedicated `reset_node` executes after every completed task, wiping the **in-graph conversation message history** while preserving workspace identity (project name, Slack thread, git state, session metadata). The agent's **persistent project memory lives in the workspace** — TODO notes, created files, git commits, and agent learning notes are never cleared. This means after a reset you can immediately ask "improve what you just built" and the agent will read those disk artifacts to reconstruct full context, giving you a fresh 256k window for each new task without losing project continuity.

### Agents

#### Main Graph Agents

| Agent | Model | Purpose |
|-------|-------|---------|
| Supervisor | `gemma-4-31b-it` | Orchestrates tasks, routes to agents, synthesises results |
| Planner | `gemma-4-31b-it` | Breaks complex requests into ordered subtasks |
| Researcher | `gemma-4-31b-it` | Quick web search — news, facts, prices |
| Deep Researcher | `gemma-4-26b-a4b-it` | Full page reading — documentation, technical articles, URLs |
| Email Composer | `gemma-4-26b-a4b-it` | Writes email drafts with layout and language support |
| LinkedIn Composer | `gemma-4-26b-a4b-it` | Writes LinkedIn post drafts with media support |
| Gmail Agent | `gemma-4-26b-a4b-it` | Reads and searches Gmail messages |
| Calendar Agent | `gemma-4-26b-a4b-it` | Creates and manages calendar events |
| Docs Agent | `gemma-4-26b-a4b-it` | Creates and edits Google Docs |
| Sheets Agent | `gemma-4-26b-a4b-it` | Creates and manages Google Sheets |
| Task Classifier | `gemma-4-31b-it` | Determines if a request is simple or multi-step |
| Memory | `gemma-4-31b-it` | Rolling context compression (only runs at threshold) |
| Validator | `gemma-4-26b-a4b-it` | Validates supervisor response before delivery |

#### Coding Agent

| Agent | Model | Purpose |
|-------|-------|---------|
| Coding Agent (main) | `gemma-4-31b-it` | Orchestrates coding tasks, tool execution, subagent delegation |
| Coding Subagent | `gemma-4-26b-a4b-it` | Handles delegated subtasks (research, write, validate) |

**Per-task context reset:** After a coding task completes, the graph's `reset_node` wipes the in-graph conversation history, freeing the full 256k context window for the next independent request. The agent's **project memory persists on disk** — TODO notes, created files, git history, and agent learning notes are all preserved, so you can immediately ask "improve what you just built" and the agent will read those artifacts to reconstruct context. Triggered by `update_project_todo(operation="complete_task")` — this automatic cleanup is unique to the coding agent and prevents context bloat across multi-session workflows.

**Model selection rationale:**
- `gemma-4-31b-it` — best reasoning, 256k context, 15 RPM / 1500 RPD — used for orchestration and complex reasoning (supervisor, planner, researcher, memory, task classifier)
- `gemma-4-26b-a4b-it` — fast MoE architecture, 256k context — used for structured/constrained tasks with JSON output (deep researcher, composers, all tool agents, coding subagent)

> **Note:** Gemma 3 / 3n models were discontinued April 30, 2026. All agents have been migrated to the Gemma 4 family.

---

## How to Use

Once the app is running, mention the bot in any Slack channel it belongs to.

### Getting Started

1. **Choose your agent**: First you'll see two buttons — 🤖 **Assistant** for general tasks (research, email, calendar, docs) or 💻 **Coding Agent** for writing code
2. **Select or create a workspace**: Pick an existing project or create a new one (coding agent can start a new project from scratch, or optionally: import from a local path or GitHub URL via the `coding settings` button)
3. **Set preferences** (first time only): Tell the bot your name and communication style
4. **Start chatting**: Type your request and the agent will work through it step by step

**Examples:**
- Assistant: "Research quantum computing trends" or "Draft an email to the team about the project delay"
- Coding: "Build a Flask API with user authentication" or "Fix the bug in login.py and write tests"

### Advanced Features

- **Interrupt flow**: Send a new message while the bot is working, choose to combine, restart, or queue
- **File uploads**: Attach files (images, PDFs, documents) for the bot to process
- **User preferences**: Click "Preferences" in the workspace menu to customize bot personality and tone
- **Autonomous tasks**: Click "Autonomous" to configure background jobs (email watch, research, calendar)
- **Coding Settings**: Configure human gate bypass, agent notes, max iterations, and model override per project
- **Context Monitor**: A desktop widget automatically appears showing accumulated context window usage (tokens consumed vs model limit), model name, and project — drag it anywhere, minimize to title bar only
- **Agent Notes**: The coding agent records insights and lessons learned after each session, building cross-session knowledge that loads automatically on the next task
- **Stop button**: Cancel long-running coding sessions instantly
- **Reset Node (per-task context clearing)**: After each completed coding task, the graph automatically wipes the conversation message history, freeing the full 256k context window for the next independent request. The agent's **project memory persists in the workspace** — TODO notes, created files, git history, and agent learning notes are all preserved. You can immediately ask "improve what you just built" and the agent will read those artifacts to reconstruct context and continue. Triggered by `update_project_todo(operation="complete_task")` — a unique signal that only the coding agent uses.

**Full User Guide**: 📖 [Slack Tutorial Guide](slack_tutorial_guide.md)

---

## Customization

### Editing System Prompts

Each agent's system prompt lives in `system_prompts/` as a standalone `.py` file. You can modify any prompt without touching the agent's code. The agent loads the prompt at runtime on every call.

```
system_prompts/
├── supervisor_prompt.py        # Routing rules, agent descriptions
├── planner_prompt.py           # Task decomposition logic
├── researcher_prompt.py        # Search and citation instructions
├── deep_researcher_prompt.py   # Full page reading instructions
├── email_composer_prompt.py    # Email format and language rules
├── linkedin_composer_prompt.py # LinkedIn post formatting
├── gmail_agent_prompt.py       # Gmail read/search operations
├── calendar_agent_prompt.py    # Calendar event management
├── docs_agent_prompt.py        # Google Docs creation/editing
├── sheets_agent_prompt.py      # Google Sheets management
├── task_classifier_prompt.py   # Simple vs complex task detection
├── memory_prompt.py            # Summarization instructions
└── validator_prompt.py         # Response validation rules

coding_agent/prompts/
├── main_agent_prompt.py        # Main coding agent instructions, tool descriptions, workspace layout
└── subagent_prompt.py          # Coding subagent instructions for delegated tasks
```

---

## Workspace Structure

Gemma Swarm uses two separate workspace roots to keep assistant projects and coding projects organised:

```
workspaces/
├── assistant/            # All non-coding assistant projects
│   └── <project_name>/
│       ├── research/         # Saved research results
│       ├── drafts/           # Email/linkedin drafts
│       └── attachments/      # Uploaded files
│       
└── coding/                   # All coding agent projects
    └── <project_name>/
        ├── .git/             # Independent git repo (auto-initialised)
        ├── project_TODO.md   # Live task log managed by the agent
        └── <source_code>/    # Your actual code (entire project copied/imported here)
```

**Coding workspace features:**
- Each coding project is its own git repository with automatic commits
- Project TODO is updated in real-time as tasks are completed
- Import existing code: provide a local path or GitHub URL when creating a project
- Agent notes (Optional) are stored separately and loaded at session start for cross-session learning

---

## Support & Contributing

This project is actively developed. **Contributions, bug reports, and feature requests are welcome.**

If you find Gemma Swarm useful, please:
- Give it a ⭐ on GitHub
- Share your use cases or improvements
- Report bugs with examples

---

## License

MIT License — free to use, modify, and distribute. See [LICENSE](LICENSE) for details.
