# Gemma Swarm ⚡

**Stop context-switching. Do your entire workflow in Slack.**

Gemma Swarm is a fully free, open-source multi-agent AI assistant that lives in your Slack workspace. Research topics, compose emails, schedule meetings, manage files, write code, and publish to LinkedIn — all through natural conversation with the bot.

Built on **Google's Gemma 4 models** (free tier) and powered by completely free integrations — no paid subscriptions, no credit card required.

> **Active Development** — This project is actively being improved with new features and agents continuously added. Contributions and feedback are welcome.

**Please** ⭐ Star this repo to support the development of the project and motivate me to build new features!

---

## Video Demos

- **UI Enhancment** — New UI visuals and card blocks: https://youtu.be/1xhXDvsD9IY
- 📧 **Email Workflow** — Compose, get feedback, send with interrupts: https://youtu.be/LfiQYaT1l9Q
- 🤖 **Autonomous Mode** — Background research & LinkedIn posting: https://youtu.be/u5iaSv6Hi2U
- 💻 **Coding Agent** — Autonomous coding from Slack: https://youtu.be/ZCeozC2UQQc
- 💻 **Coding Agent** — Autonomous Workflow: https://youtu.be/_FqojFjg63U

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

All with human-in-the-loop approvals before sensitive actions (email sends, LinkedIn posts, Google write operations).

---

## Not Home?
No worries, We got you. Download the Slack app on your phone and stay in control from anywhere.

---

## Why Gemma Swarm?

✅ **Completely Free** — No API fees, no paid tiers, no credit card  
📍 **Runs Locally** — Your data stays in your workspace (uses SQLite for memory)  
🔐 **Privacy-First** — Open source, your workspace, your control  
🎯 **No More Context-Switching** — Everything happens in Slack  
⚡ **Straightforward Setup** — Core features work with just Slack + a Google API key. Add integrations when you need them  
🧠 **Persistent Memory** — Conversations survive restarts and scale gracefully  
🔧 **Dynamic Tool Loading** — Supervisor loads only the tools it needs per turn — zero wasted context  
💻 **Full Coding Workspace** — Autonomous coding with git, file editing, validation, and agent learning  
🖥️ **Context Monitor** — Desktop widget shows accumulated context window usage as percentage of model limit, updating after each turn

---

## Quick Start

### 1. Download
```bash
git clone https://github.com/wiss84/gemma-swarm.git
```

### 2. Get API Keys

**Required for all features:**

| Service | Setup Guide |
|---------|-------------|
| Google Gemma API | [Get API Key](https://aistudio.google.com/u/0/api-keys) |
| Jina AI (web search) | [Get API Key](https://jina.ai/) — no signup required |
| Slack | [Slack Setup](docs/setup/slack_setup.md) |

**Optional integrations** — add when you need them, skip what you don't:

| Integration | Setup Guide |
|-------------|-------------|
| Gmail & Google Workspace | [Google Setup](docs/setup/google_setup.md) |
| Email sending (SMTP) | [Email Setup](docs/setup/email_setup.md) |
| LinkedIn posting | [LinkedIn Setup](docs/setup/linkedin_setup.md) |

> If an integration isn't configured, Gemma Swarm shows a setup guide button in Slack instead of crashing. You can add integrations at any time, restart the terminal and continue the same conversation.

### 3. Environment Variables

Create a `.env` file in the project root:

```bash
# Google Gemma API (required)
GOOGLE_API_KEY=your_google_api_key

# Jina AI — web search (optional)
JINA_API_KEY=your_jina_api_key

# Slack (required)
Bot_User_OAuth_Token=xoxb-your-bot-token
agent_socket_token=xapp-your-socket-token

# Gmail — email sending (optional)
HUMAN_EMAIL=your_email@gmail.com
EMAIL_PASS=your_gmail_app_password

# LinkedIn — posting (optional)
LINKEDIN_CLIENT_ID=your_linkedin_client_id
LINKEDIN_CLIENT_SECRET=your_linkedin_client_secret

# Google Workspace — OAuth credentials (optional)
# Place Google_creds.json in project root (see docs/setup/google_setup.md)

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
python -m venv gemma_test
source gemma_test/bin/activate  # Windows: gemma_test\Scripts\activate
pip install pytest ruff flake8 mypy magika
deactivate

# Install Node.js LTS (for JavaScript/TypeScript validation)
#   macOS: brew install node
#   Linux: curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs
#   Windows: winget install OpenJS.NodeJS.LTS

# Install TypeScript and ESLint globally
npm install -g typescript eslint

# Install ts-morph bridge for semantic JS/TS analysis
cd tools/ts_analysis_bridge && npm install --prefer-offline && cd ../..

# Activate main env and run
source gemma_swarm/bin/activate  # Windows: gemma_swarm\Scripts\activate
python slack_app.py
```

**Important:**
- The `gemma_swarm` environment is the **main app** — must be activated to run `slack_app.py`
- The `gemma_test` environment is **required** for the coding agent — both environments must exist

**→ Done. Start chatting.**

---

## Completely Free

| Component | Free Tier |
|-----------|-----------|
| **Gemma 4 models** | Google Gemini API free tier |
| **Web research** | Jina AI — free with API key |
| **Email** | Gmail SMTP — free with App Password |
| **LinkedIn** | LinkedIn API — free with developer app |
| **Google Workspace** | Gmail, Calendar, Docs, Sheets APIs — free with OAuth |
| **Slack** | Slack Bolt — free for personal workspaces |
| **Memory** | SQLite — local file, no cloud costs |
| **Coding** | Local workspace + git — no cloud IDE fees |

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

- 🔧 **Agentic Tool Calling** — Supervisor dynamically loads only the tools it needs per turn. No routing hops, no wasted context.
- 💻 **Autonomous Coding** — Write, edit, validate, test, and commit code with full workspace management
- 📧 **Gmail Integration** — Read, search, and manage your Gmail inbox
- 📅 **Google Calendar** — Create, read, and manage calendar events
- 📄 **Google Docs** — Create and edit documents
- 📊 **Google Sheets** — Create and manage spreadsheets
- 💬 **Slack-Native** — Full human-in-the-loop confirmations, interrupt handling, file uploads, and real-time status updates
- 📧 **Email Automation** — Compose, review, and send emails via Gmail SMTP with attachment support
- 💼 **LinkedIn Posting** — Create and publish posts with image, video, and document attachments
- 🔍 **Web Research** — Quick search and deep page reading modes
- 🧠 **Persistent Memory** — Conversation history survives restarts via SQLite checkpointing
- ⚡ **Interrupt Handling** — Send a new message while the bot is working: combine, fresh start, or queue
- 🗜️ **Context Compression** — Automatic rolling summarization when context approaches the model limit
- 🛡️ **Guard Rails** — Safety checks blocking dangerous operations before they reach the agent
- 📁 **Workspace Management** — Each project gets its own folder for research, drafts, and attachments
- ⚙️ **User Preferences** — Personalize the bot's name, tone, and communication style
- 🖥️ **Context Monitor UI** — Desktop widget shows accumulated context window usage, model, and project per session
- 📝 **Agent Learning Notes** — Coding agent records insights across sessions to improve over time
- 🔀 **Git Integration** — Automatic repo init, commits, and history tracking for coding projects
- 🎯 **Configurable Coding Settings** — Human gate bypass, max iterations, model override per project
- 🔌 **Optional Integrations** — Missing credentials show a clickable setup guide in Slack, never a crash

---

## Architecture

### Main Assistant Graph

```
               New Message
                    │
                    ▼
               Input Router ──► Memory Agent (if context > 70% threshold)
                    │                    │
                    └────────────────────┘
                    ▼
               Guard Rails
                    │
                    ▼
               Supervisor ◄─────────────────────────────────────┐
               (agentic loop)                                   │
                    │                                           │
                    │  🧠 Thinking...                           │
                    │  🔧 load_toolset("gmail")                 │ retry
                    │  🔧 gmail_list_messages(...)              │
                    │  🧠 Thinking...                           │
                    │  ── final response ──                      │
                    │                                            │
                    ▼                                            │
               Validator ────────────────────────────────────────┘
                    │
                    ▼
               Output Formatter
                    │
                   END
```

**Blocking tools** — Email, LinkedIn, and Google write actions (create/update/delete) block inside the supervisor's tool call and handle human confirmation directly:

```
Supervisor calls send_email(...)
      │
      ▼
   Tool posts draft to Slack + waits for Approve / Reject
      │
      ├── Approved → email sent → returns "✅ Sent" to supervisor
      └── Rejected with feedback → returns "rejected: <feedback>" to supervisor
                                        │
                                        └── Supervisor rewrites and calls tool again
```

No routing hops for confirmations. The supervisor sees only a tool result string.

**Separate Coding Agent Graph** (accessed via "💻 Coding Agent" button):

```
               Coding Prompt
                    │
                    ▼
               Coding Agent (gemma-4-31b-it) ──► spawn_subagent (gemma-4-26b-a4b-it) [optional]
                    │
                    ├─► read/write/edit files
                    ├─► execute shell commands
                    ├─► git operations
                    ├─► validate & test
                    ├─► research (web/package)
                    └─► update TODO, write notes
                    │
               Output Node
                    │
               Reset Node
                    │
                    ▼
          Final response to Slack
```

**Per-task context reset:** After each coding task, `reset_node` wipes the in-graph conversation history, freeing the full 256k context window for the next task. Project memory persists on disk (TODO notes, files, git history, agent notes), so you can immediately ask "improve what you just built."

---

### Agents & Models

#### Main Graph

| Component | Model | Role |
|-----------|-------|------|
| Supervisor | `gemma-4-31b-it` | Single agentic loop — loads tools, calls them, produces response |
| Memory | `gemma-4-31b-it` | Rolling context compression (only runs at threshold) |
| Validator | `gemma-4-26b-a4b-it` | Response quality check before delivery |

#### Coding Agent

| Component | Model | Role |
|-----------|-------|------|
| Coding Agent (main) | `gemma-4-31b-it` | Orchestrates coding tasks, tool execution, subagent delegation |
| Coding Subagent | `gemma-4-26b-a4b-it` | Handles delegated subtasks (research, write, validate) |

**Model selection rationale:**
- `gemma-4-31b-it` — best reasoning, 256k context, used for orchestration and complex synthesis
- `gemma-4-26b-a4b-it` — fast MoE architecture, 256k context, used for structured/constrained tasks

---

### Dynamic Toolset Loading

The supervisor starts each turn with a single meta-tool: `load_toolset`. When it needs a capability, it loads the toolset first, then calls the tools:

| Toolset | Tools | Requires |
|---------|-------|----------|
| `research` | `search_web`, `fetch_page`, `fetch_next_chunk` | Always available |
| `gmail` | `gmail_list_messages`, `gmail_read_message`, `gmail_check_for_sender` | `Google_creds.json` |
| `calendar` | `calendar_list`, `calendar_next`, `calendar_create`*, `calendar_delete`* | `Google_creds.json` |
| `docs` | `docs_read`, `docs_create`*, `docs_update`* | `Google_creds.json` |
| `sheets` | `sheets_read`, `sheets_create`*, `sheets_update`* | `Google_creds.json` |
| `email` | `send_email`* | `HUMAN_EMAIL` + `EMAIL_PASS` |
| `linkedin` | `publish_linkedin_post`* | `LINKEDIN_CLIENT_ID` + `LINKEDIN_CLIENT_SECRET` |

`*` = requires human approval before executing (handled inside the tool, transparent to the supervisor)

If credentials are missing for a toolset, the supervisor receives a `CONFIG_MISSING` signal and the user sees a setup guide with a clickable button to open the relevant file in their editor.

---

## How to Use

Once the app is running, mention the bot in any Slack channel it belongs to.

### Getting Started

1. **Choose your agent**: First you'll see two buttons — 🤖 **Assistant** for general tasks or 💻 **Coding Agent** for writing code
2. **Select or create a workspace**: Pick an existing project or create a new one
3. **Set preferences** (first time only): Tell the bot your name and communication style
4. **Start chatting**: Type your request and the agent will work through it step by step

**Examples:**
- "Research quantum computing trends"
- "Draft an email to the team about the project delay"
- "What's on my calendar tomorrow?"
- "Create a Google Doc with meeting notes from our last call"
- "Post to LinkedIn about my latest project"
- "Build a Flask API with user authentication" *(Coding Agent)*

### Advanced Features

- **Interrupt flow**: Send a new message while the bot is working — combine, restart, or queue
- **File uploads**: Attach files (images, PDFs, documents) for the bot to process
- **User preferences**: Click "Preferences" to customize bot personality and tone
- **Autonomous tasks**: Click "Autonomous" to configure background jobs
- **Coding Settings**: Configure human gate bypass, agent notes, max iterations, and model override per project
- **Context Monitor**: Desktop widget shows context window usage, model, and project — drag it anywhere
- **Agent Notes**: Coding agent records lessons learned after each session for cross-session continuity
- **Stop button**: Cancel long-running coding sessions instantly

**Full User Guide**: 📖 [Slack Tutorial Guide](docs/setup/slack_tutorial.md)

---

## Customization

### Editing System Prompts

Each agent's system prompt lives in `system_prompts/` as a standalone `.py` file. Modify any prompt without touching agent code — loaded at runtime on every call.

```
system_prompts/
├── supervisor_prompt.py        # Tool usage rules, available toolsets, tone
├── memory_prompt.py            # Summarization instructions
└── validator_prompt.py         # Response validation rules

coding_agent/prompts/
├── main_agent_prompt.py        # Main coding agent instructions
└── subagent_prompt.py          # Delegated subtask instructions
```

---

## Workspace Structure

```
workspaces/
├── assistant/                  # All non-coding assistant projects
│   └── <project_name>/
│       ├── research/           # Saved research results
│       ├── drafts/             # Email/LinkedIn drafts
│       └── attachments/        # Uploaded files
│
└── coding/                     # All coding agent projects
    └── <project_name>/
        ├── .git/               # Independent git repo (auto-initialised)
        ├── project_TODO.md     # Live task log managed by the agent
        └── <source_code>/      # Your actual code

docs/
└── setup/
    ├── slack_setup.md
    ├── google_setup.md
    ├── email_setup.md
    └── linkedin_setup.md
```

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
