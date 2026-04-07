# Gemma Swarm ⚡

**Stop context-switching. Do your entire workflow in Slack.**

Gemma Swarm is a fully free, open-source multi-agent AI assistant that lives in your Slack workspace. Research topics, compose emails, schedule meetings, manage files, and publish to LinkedIn — all through natural conversation with the bot.

Built on **Google's Gemma models** (free tier) and powered by completely free integrations — no paid subscriptions, no credit card required.

> **Active Development** — This project is actively being improved with new features and agents continuously added. Contributions and feedback are welcome.

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

All with human-in-the-loop approvals before sensitive actions (email sends, LinkedIn posts).

---

## Why Gemma Swarm?

✅ **Completely Free** — No API fees, no paid tiers, no credit card  
📍 **Runs Locally** — Your data stays in your workspace (uses SQLite for memory)  
🔐 **Privacy-First** — Open source, your workspace, your control  
🎯 **No More Context-Switching** — Everything happens in Slack  
⚡ **Straightforward Setup** — Get all API keys and run (30 mins first time)  
🧠 **Persistent Memory** — Conversations survive restarts and scale gracefully  
📚 **Multi-Model Support** — Specialized agents pick the right model for each task  

---

## Quick Start

### 1. Download & Install
```bash
# Clone the repo
git clone https://github.com/yourusername/gemma-swarm.git
cd gemma-swarm

# Windows: Double-click setup.bat (auto-installs everything)
# OR manual: pip install -r requirements.txt
pip install -r requirements.txt
```

### 2. Get API Keys (Free)
- [Google Gemma API](https://aistudio.google.com/u/0/api-keys) — free tier
- [Jina AI](https://jina.ai/) — no signup needed
- Create `.env` file with keys (see Environment Variables below)

### 3. Connect to Slack
- Follow [SLACK_WORKFLOW_SETUP.md](SLACK_WORKFLOW_SETUP.md) — 5 minutes
- Run `python slack_app.py`
- Mention the bot in any channel

**→ Done. Start chatting.**

---

## Completely Free

Every component was deliberately chosen to avoid costs:

| Component | Free Tier |
|-----------|-----------|
| **Gemma models** | Google Gemini API free tier — 15k tokens/min |
| **Web research** | Jina AI — free with API key |
| **Email** | Gmail SMTP — free with App Password |
| **LinkedIn** | LinkedIn API — free with developer app |
| **Google Workspace** | Gmail, Calendar, Docs, Sheets APIs — free with OAuth |
| **Slack** | Slack Bolt — free for personal workspaces |
| **Memory** | SQLite — local file, no cloud costs |

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

## Video Demos

- 📧 **Email Workflow** — Compose, get feedback, send with interrupts: https://youtu.be/LfiQYaT1l9Q
- 🤖 **Autonomous Mode** — Background research & LinkedIn posting: https://youtu.be/u5iaSv6Hi2U

---

## Full Feature List

### Core Capabilities

- 🤖 **Multi-Agent Orchestration** — Supervisor, Planner, Researcher, Deep Researcher, Email Composer, LinkedIn Composer, Memory Agent, Task Classifier
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
- ⚡ **Interrupt Handling** — send a new message while the agent is working and choose to combine, fresh start, or queue
- 🗜️ **Context Compression** — automatic rolling summarization when context approaches the model's limit
- 🛡️ **Guard Rails** — safety checks blocking dangerous operations before they reach any agent
- 📁 **Workspace Management** — each project gets its own folder for research, drafts, and attachments
- ⚙️ **User Preferences** — personalize the bot's name, tone, and communication style

---

## Architecture

### Agent Flow

```
New Message
     │
     ▼
Input Router ──→ Memory Agent (if context > 10% threshold)
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

### Agents

| Agent | Model | Purpose |
|-------|-------|---------|
| Supervisor | gemma-3-27b-it | Orchestrates tasks, routes to agents, synthesises results |
| Planner | gemma-3-27b-it | Breaks complex requests into ordered subtasks |
| Researcher | gemma-3-12b-it | Quick web search — news, facts, prices |
| Deep Researcher | gemma-3-12b-it | Full page reading — documentation, technical articles, URLs |
| Email Composer | gemma-3-4b-it | Writes email drafts with layout and language support |
| LinkedIn Composer | gemma-3-4b-it | Writes LinkedIn post drafts with media support |
| Gmail Agent | gemma-3-4b-it | Reads and searches Gmail messages |
| Calendar Agent | gemma-3-4b-it | Creates and manages calendar events |
| Docs Agent | gemma-3-4b-it | Creates and edits Google Docs |
| Sheets Agent | gemma-3-4b-it | Creates and manages Google Sheets |
| Task Classifier | gemma-3-27b-it | Determines if a request is simple or multi-step |
| Memory | gemma-3-4b-it | Rolling context compression (only runs at threshold) |
| Validator | gemma-3-4b-it | Validates supervisor response before delivery |

### Why Gemma Models?

Every message in the pipeline — system prompts, agent results, tool outputs, and user messages — is wrapped as a `HumanMessage` with a label prefix (e.g. `[SUPERVISOR]`, `[RESEARCHER RESULT]`, `[HUMAN]`). This works because **Gemma models are instruction-tuned** and reliably understand the `human` role. Unlike other models that support distinct `system`, `assistant`, and `user` roles, this label-based approach reduces confusion and works better with Gemma's training.

---

## Full Setup Guide

### Prerequisites

API keys for the services you want to use:

| Service | Required | Setup Guide |
|---------|----------|-------------|
| Google Gemma API | ✅ Yes | [Get API Key](https://aistudio.google.com/u/0/api-keys) |
| Jina AI (web search) | ✅ Yes | [Get API Key](https://jina.ai/) — no signup required |
| Slack | ✅ Yes | [Slack Workflow Setup](SLACK_WORKFLOW_SETUP.md) |
| Gmail (email sending) | ✅ Yes | [Email Workflow Setup](Email_WORKFLOW_SETUP.md) |
| LinkedIn (posting) | ✅ Yes | [LinkedIn Workflow Setup](LINKEDIN_WORKFLOW_SETUP.md) |
| Google Workspace | ✅ Yes | [Google Workflow Setup](Google_WORKFLOW_SETUP.md) |

---

### Installation

#### Option 1 — Windows (Easiest)

Double-click **`setup.bat`**. It will automatically:
1. Detect or install Miniconda
2. Create a `gemma_swarm` Conda environment with Python 3.11
3. Install all dependencies from `requirements.txt`
4. Create a `gemma-swarm.bat` launcher with your paths pre-configured
5. Place a desktop shortcut with a custom icon

After setup completes, click the desktop shortcut to start the app.

#### Option 2 — Manual (All Platforms)

```bash
# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python slack_app.py
```

---

### Environment Variables

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

---

## How to Use

Once the app is running, mention the bot in any Slack channel it belongs to.

### Getting Started

1. **Start simple**: Say "Hello" or ask a question
2. **Research**: Say "Research [topic]" for quick facts or "Deep research [topic]" for full articles
3. **Compose email**: Say "Draft an email to [person] about [topic]" — bot sends a draft for approval
4. **Schedule meeting**: Say "Schedule a meeting on [date] at [time]" with optional attendees
5. **Create doc**: Say "Create a Google Doc with..." and the bot will make it
6. **Post to LinkedIn**: Say "Post to LinkedIn about..." with optional media attachment

### Advanced Features

- **Interrupt flow**: Send a new message while the bot is working, choose to combine, restart, or queue
- **File uploads**: Attach files (images, PDFs, documents) for the bot to process
- **User preferences**: Click "User Preferences" in the workspace menu to customize bot personality and tone
- **Autonomous tasks**: Click "Autonomous Settings" to configure background jobs (email watch, research, calendar)

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
