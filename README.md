# Gemma Swarm ⚡

A fully free, open-source multi-agent AI assistant that lives in your Slack workspace. Built on Google's Gemma models and the free tier of the Gemini API — no paid subscriptions, no credit card required.

Gemma Swarm can research the web, compose and send emails, create and publish LinkedIn posts, manage files, and handle complex multi-step tasks — all through natural conversation in Slack.

> **Active Development** — This project is a solid starting point but is actively being improved. New features, agents, and integrations are continuously being added. Contributions and feedback are welcome.

---

## Completely Free

Every component in this project was deliberately chosen to avoid paid APIs:

| Component | Free Tier |
|-----------|-----------|
| **Gemma models** | Google Gemini API free tier — 15,000 tokens/min, 14,400 requests/day |
| **Web search** | Jina AI — free API key, no signup required |
| **Email sending** | Gmail SMTP — free with a Gmail App Password |
| **LinkedIn posting** | LinkedIn API — free with a developer app |
| **Google Workspace** | Gmail, Calendar, Docs, Sheets APIs — free with OAuth |
| **Slack integration** | Slack Bolt — free for personal and small team workspaces |
| **Conversation memory** | SQLite via LangGraph checkpointing — local file, no cloud |

### Autonomous Mode

A background pipeline that runs scheduled tasks independently from the main chat interface. Configure it via the **Autonomous Settings** button in the workspace menu:

| Job | What it does |
|-----|--------------|
| **Email Watch** | Monitors inbox for emails from specific senders, posts alerts to Slack |
| **Inbox Check** | Scans inbox for new emails and posts summaries |
| **Calendar Notify** | Checks calendar for upcoming events, sends reminders |
| **Research + LinkedIn** | Researches configured topics and auto-drafts LinkedIn posts |
| **Daily Summary** | Posts daily activity summary to the autonomous channel |

Runs on a 60-second tick cycle. All activity is logged to a Google Sheet for tracking.

---

## Features

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
| LinkedIn Composer | gemma-3n-e4b-it | Writes LinkedIn post drafts with media support |
| Gmail Agent | gemma-3-4b-it | Reads and searches Gmail messages |
| Calendar Agent | gemma-3-4b-it | Creates and manages calendar events |
| Docs Agent | gemma-3-4b-it | Creates and edits Google Docs |
| Sheets Agent | gemma-3-4b-it | Creates and manages Google Sheets |
| Task Classifier | gemma-3-27b-it | Determines if a request is simple or multi-step |
| Memory | gemma-3-4b-it | Rolling context compression (only runs at threshold) |
| Validator | gemma-3n-e2b-it | Validates supervisor response before delivery |

### All Messages Are HumanMessage

Gemma models are **instruction-tuned** models. Unlike  Gemini, OpenAI or Anthropic models that support distinct `system`, `assistant`, and `user` roles, Gemma only reliably understands the `human` turn in a conversation. Every message in the pipeline — system prompts, agent results, tool outputs, and user messages — is wrapped as a `HumanMessage` with a label prefix (e.g. `[SUPERVISOR]`, `[RESEARCHER RESULT]`, `[HUMAN]`) so the model can distinguish the source without relying on role types it was not trained to handle.

---

## Rate Limits (Free Tier)

Gemma Swarm uses Google's free tier for Gemma models:

---

## Setup

### Prerequisites

Before you begin, you will need API keys and credentials for the services you want to use:

| Service | Required | Setup Guide |
|---------|----------|-------------|
| Google Gemma API | ✅ Yes | [Get API Key](https://aistudio.google.com/u/0/api-keys) |
| Jina AI (web search) | ✅ Yes | [Get API Key](https://jina.ai/) — no signup required |
| Slack | ✅ Yes | [Slack Workflow Setup](SLACK_WORKFLOW_SETUP.md) |
| Gmail (email sending) | ✅ Yes | [Email Workflow Setup](Email_WORKFLOW_SETUP.md) |
| LinkedIn (posting) | ✅ Yes | [LinkedIn Workflow Setup](LINKEDIN_WORKFLOW_SETUP.md) |
| Google Workspace (Gmail, Calendar, Docs, Sheets) | ✅ Yes | [Google Workflow Setup](Google_WORKFLOW_SETUP.md) |

---

### Installation

#### Option 1 — Windows (Recommended)

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

# Gmail — email sending
HUMAN_EMAIL=your_email@gmail.com
EMAIL_PASS=your_gmail_app_password

# LinkedIn — posting
LINKEDIN_CLIENT_ID=your_linkedin_client_id
LINKEDIN_CLIENT_SECRET=your_linkedin_client_secret

# Google Workspace — OAuth credentials (place Google_creds.json in project root)
# See Google_WORKFLOW_SETUP.md for setup instructions

# LangSmith — tracing (optional, for debugging)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_PROJECT=gemma-swarm
```

---

## How to Use

Once the app is running, mention the bot in any Slack channel it belongs to. A complete user guide covering every feature is available here:

📖 **[Slack Tutorial Guide](slack_tutorial_guide.md)**

The guide covers:
- Setting up your first workspace
- Setting user preferences
- Sending emails and LinkedIn posts with the approval flow
- Attaching files
- Research vs Deep Research
- Running multiple tasks at once
- The interrupt flow (Combine / Fresh Start / Queue)

---


---

## Editing System Prompts

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

## Contributing

This project is in active development. Contributions, bug reports, and feature requests are welcome. If you build something on top of Gemma Swarm or find it useful, a ⭐ on GitHub would be appreciated.

---

## License

MIT License — free to use, modify, and distribute.
