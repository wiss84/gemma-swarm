# Gemma Swarm ⚡

A fully free, open-source multi-agent AI assistant that lives in your Slack workspace. Built on Google's Gemma models and the free tier of the Gemini API — no paid subscriptions, no credit card required.

Gemma Swarm can research the web, compose and send emails, create and publish LinkedIn posts, manage files, and handle complex multi-step tasks — all through natural conversation in Slack.

> **Active Development** — This project is a solid starting point but is actively being improved. New features, agents, and integrations are continuously being added. Contributions and feedback are welcome.

---

## Why Completely Free?

Every component in this project was deliberately chosen to avoid paid APIs:

| Component | Free Tier |
|-----------|-----------|
| **Gemma models** | Google Gemini API free tier — 15,000 tokens/min, 14,400 requests/day |
| **Web search** | Jina AI — free API key, no signup required |
| **Email sending** | Gmail SMTP — free with a Gmail App Password |
| **LinkedIn posting** | LinkedIn API — free with a developer app |
| **Slack integration** | Slack Bolt — free for personal and small team workspaces |
| **Conversation memory** | SQLite via LangGraph checkpointing — local file, no cloud |

---

## Features

- 🤖 **Multi-Agent Orchestration** — Supervisor, Planner, Researcher, Deep Researcher, Email Composer, LinkedIn Composer, Memory Agent, Task Classifier
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
                    ▼                      ▼                       ▼
               Researcher          Deep Researcher          Email Composer
                    │                      │                  LinkedIn Composer
                    └──────────────────────┘
                                           │
                                      Human Gate
                                  (approve / reject)
                                           │
                                    Email / LinkedIn Send
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
| Supervisor | gemini-2.0-flash-lite | Orchestrates tasks, routes to agents, synthesises results |
| Planner | gemma-3-27b-it | Breaks complex requests into ordered subtasks |
| Researcher | gemma-3-12b-it | Quick web search — news, facts, prices |
| Deep Researcher | gemini-2.0-flash-lite | Full page reading — documentation, technical articles, URLs |
| Email Composer | gemma-3-4b-it | Writes email drafts with layout and language support |
| LinkedIn Composer | gemma-3n-e4b-it | Writes LinkedIn post drafts with media support |
| Task Classifier | gemma-3-27b-it | Determines if a request is simple or multi-step |
| Memory | gemini-2.0-flash-lite | Rolling context compression (only runs at threshold) |
| Validator | gemma-3n-e2b-it | Validates supervisor response before delivery |

### Why All Messages Are HumanMessage

Gemma models are **instruction-tuned** models. Unlike OpenAI or Anthropic models that support distinct `system`, `assistant`, and `user` roles, Gemma only reliably understands the `human` turn in a conversation. Every message in the pipeline — system prompts, agent results, tool outputs, and user messages — is wrapped as a `HumanMessage` with a label prefix (e.g. `[SUPERVISOR]`, `[RESEARCHER RESULT]`, `[HUMAN]`) so the model can distinguish the source without relying on role types it was not trained to handle.

---

## Rate Limits (Free Tier)

Gemma Swarm uses two types of models with different free tier limits:

**Gemini models** (Supervisor, Deep Researcher, Memory):

| Limit | Value |
|-------|-------|
| Requests per minute | 15 |
| Tokens per minute | 250,000 |
| Daily requests | 500 |

**Gemma models** (all other agents):

| Limit | Value |
|-------|-------|
| Requests per minute | 30 |
| Tokens per minute | 15,000 |
| Daily requests | 14,400 |

**Automatic Gemini → Gemma fallback:** When the Gemini daily limit of 500 requests is exhausted, all Gemini-based agents (Supervisor, Deep Researcher, Memory) automatically switch to `gemma-3-27b-it` for the rest of the session. On the next app restart, if a new calendar day is detected, the daily counter resets to 0 and agents resume using their configured Gemini models.

The built-in rate limit handler tracks all requests proactively, waits automatically when limits approach, and posts countdown messages in Slack so you always know what is happening.

---

## Setup

### Prerequisites

Before you begin, you will need API keys and credentials for the services you want to use:

| Service | Required | Setup Guide |
|---------|----------|-------------|
| Google Gemini API | ✅ Yes | [Get API Key](https://aistudio.google.com/u/0/api-keys) |
| Jina AI (web search) | ✅ Yes | [Get API Key](https://jina.ai/) — no signup required |
| Slack | ✅ Yes | [Slack Workflow Setup](SLACK_WORKFLOW_SETUP.md) |
| Gmail (email sending) | ✅ Yes | [Email Workflow Setup](Email_WORKFLOW_SETUP.md) |
| LinkedIn (posting) | ✅ Yes | [LinkedIn Workflow Setup](LINKEDIN_WORKFLOW_SETUP.md) |

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
# Google Gemini API (required)
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
├── researcher_prompt.py        # Search and citation instructions
├── email_composer_prompt.py    # Email format and language rules
└── memory_prompt.py            # Summarization instructions
```

---

## Contributing

This project is in active development. Contributions, bug reports, and feature requests are welcome. If you build something on top of Gemma Swarm or find it useful, a ⭐ on GitHub would be appreciated.

---

## License

MIT License — free to use, modify, and distribute.
