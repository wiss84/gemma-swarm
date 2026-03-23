# Gemma Swarm — Slack Tutorial Guide

Welcome to Gemma Swarm! This guide walks you through everything you need to know to get the most out of your AI assistant on Slack — from your very first message to advanced multi-task workflows.

---

## Table of Contents

1. [Your First Message](#1-your-first-message)
2. [Setting Up Your Workspace](#2-setting-up-your-workspace)
3. [Setting Your Preferences](#3-setting-your-preferences)
4. [Resuming an Existing Project](#4-resuming-an-existing-project)
5. [Sending a Message While the Agent is Working](#5-sending-a-message-while-the-agent-is-working)
6. [Attaching Files](#6-attaching-files)
7. [Sending Emails](#7-sending-emails)
8. [Creating LinkedIn Posts](#8-creating-linkedin-posts)
9. [Research vs Deep Research](#9-research-vs-deep-research)
10. [Running Multiple Tasks at Once](#10-running-multiple-tasks-at-once)

---

## 1. Your First Message

To start a conversation, **mention the bot** in any channel it has been added to:

> `@YourBotName hi, I need some help`

The bot will reply **inside a thread** — all conversation happens there, keeping your channel clean.

On your very first message, the bot will ask you to select or create a workspace before it can help you.

---

## 2. Setting Up Your Workspace

Every project lives inside its own **workspace** — a dedicated folder that stores your research, email drafts, LinkedIn post drafts, and file attachments. When you first mention the bot, you will see this:

```
👋 Welcome! Please select or create a workspace to get started:

[ 🆕 New Project ]

Or continue an existing project:
[ 📁 my-project ]  [ 📁 another-project ]

Or update your preferences:
[ ⚙️ Preferences ]
```

### Creating a New Project

Click **🆕 New Project** — a modal will appear asking you to name your project. Use letters, numbers, and hyphens (e.g. `job-search`, `my-startup`). Click **Create** and the bot will set everything up and process your first message.

### First-Time Preferences

If this is your first time using the bot, after creating a project a **Welcome modal** will appear asking:
- **What would you like me to call you?** — your name, so the bot can address you personally
- **Any other preferences?** — optional instructions like *"be more concise"* or *"always use formal language"*

Click **Save** and the bot will remember these preferences across all future conversations.

---

## 3. Setting Your Preferences

You can update your preferences at any time. Simply mention the bot and click the **⚙️ Preferences** button that appears in the workspace selection message. The modal will open with your current preferences pre-filled so you can edit them.

Your preferences are applied globally — they influence how the bot communicates with you across every project.

---

## 4. Resuming an Existing Project

### From the same Slack thread

If you want to continue a conversation with visible history, just **mention the bot directly in the original thread**. The bot will load your full conversation history and pick up right where you left off.

### From a new Slack thread

If you mention the bot in the channel and then select an **existing project** from the workspace buttons, the bot will:
- Start a clean, fresh-looking Slack thread (no visible history)
- But internally load your **full conversation history** from that project

This is useful when you want a cleaner view but don't want to lose any context.

---

## 5. Sending a Message While the Agent is Working

If you send a new message while the bot is still processing a previous task, it will **pause and ask you what to do**:

```
⚡ New message received while I'm still working.
New message: "can you also check the pricing?"

What should I do?

[ 🔀 Combine ]  [ 🆕 Fresh Start ]  [ 📋 Queue ]

No response in 5 minutes → will queue automatically
```

### 🔀 Combine
Merges your new message with the current task into a single combined request. The current task is cancelled and the bot starts fresh handling both things together.

> **Example:** You asked to *"research the best laptops"* and then sent *"also check prices"* — Combine turns this into *"research the best laptops and also check prices"* in one go.

### 🆕 Fresh Start
Cancels the current task entirely and starts fresh with your new message. Previous conversation history is preserved, but the interrupted task is discarded.

> **Use this when:** You changed your mind and want to do something completely different.

### 📋 Queue
Lets the current task finish first, then automatically processes your new message afterwards. Nothing is lost or cancelled.

> **Use this when:** Both tasks are important and you want them handled one after the other.

If you don't click anything within 5 minutes, the bot will **queue automatically**.

---

## 6. Attaching Files

You can attach files directly in the Slack thread. After uploading a file, the bot will ask you how to use it:

```
📎 Resume_Sept25.pdf

How would you like to attach this file?

[ 📧 Email Attachment ]  [ 💼 LinkedIn Attachment ]  [ 📄 Context Attachment ]
```

### 📧 Email Attachment
Saves the file to your project's email attachments folder. When the email is sent, this file will be included as an attachment.

> **Important:** You must mention the filename including its extension in your message — the bot will not attach files automatically.
> Example: `send an email to john@example.com with my resume attached: Resume_Sept25.pdf`

Supported file types: `pdf`, `docx`, `xlsx`, `csv`, `txt`, `png`, `jpg`, `jpeg`, `zip`, `rar`

### 💼 LinkedIn Attachment
Saves the file to your project's LinkedIn media folder. When you ask the bot to create a LinkedIn post, it will include this file as the post's media attachment.

> **Important:** You must mention the filename including its extension in your message — the bot will not attach files automatically.
> Example: `write a LinkedIn post about my new guide and attach: slack_tutorial_guide.pdf`

Supported file types: `pdf`, `doc`, `docx`, `ppt`, `pptx`, `jpg`, `jpeg`, `png`, `gif`, `mp4`, `mov`

### 📄 Context Attachment
Saves the file and passes its content directly to the bot as context. Use this when you want the bot to **read and understand** the file — for example, uploading a document and asking *"summarise this"* or *"write an email based on this report"*.

> **Tip:** You can include a message along with your file upload. The bot will use that message as your instruction for what to do with the file.

---

## 7. Sending Emails

Simply ask the bot to send an email in plain language:

> `send an email to john@example.com and remind him about our meeting tomorrow at 3pm`

The bot will compose the email and show you a **preview** before sending:

```
📧 Email Draft — Please Review

To:       john@example.com
Subject:  Reminder: Meeting Tomorrow at 3 PM
Language: English
Layout:   Official

Message:
Hi John,
Just a friendly reminder about our meeting tomorrow at 3 PM...

[ ✅ Send Email ]  [ ✏️ Reject & Give Feedback ]
```

### ✅ Send Email
Approves the draft and sends it immediately.

### ✏️ Reject & Give Feedback
Opens a feedback modal where you can type exactly what needs to change — for example: *"make it shorter"* or *"sign it with my name Wissam"*. The bot will rewrite the email incorporating your feedback and show you a new preview.

You can reject and provide feedback as many times as needed until you are happy with the result.

> **Tip:** You can specify a language — *"write it in French"* — and the entire email including the greeting and closing will be written in that language.

---

## 8. Creating LinkedIn Posts

Ask the bot to create a post in plain language:

> `write a LinkedIn post about our new product launch`

The bot will compose the post and show you a **preview**:

```
💼 LinkedIn Post Draft — Please Review

[Post content preview here...]

[ ✅ Publish Post ]  [ ✏️ Reject & Give Feedback ]
```

### ✅ Publish Post
Approves the draft and publishes it to LinkedIn immediately.

### ✏️ Reject & Give Feedback
Opens a feedback modal where you can describe what to change — for example: *"add more hashtags"* or *"make the tone more professional"*. The bot rewrites the post and shows a new preview.

### Attaching Media to a LinkedIn Post
Upload your image or video using the **💼 LinkedIn Attachment** button (see Section 6) before asking for the post. When you ask the bot to create the post, mention the filename and it will be included as the post's media.

---

## 9. Research vs Deep Research

The bot has two research modes depending on what you need.

### Regular Research 🔍
Used for quick lookups — news, facts, prices, recent events. The bot searches the web and returns a structured summary with sources.

**How to trigger:** Just ask naturally.
> `what are the latest AI news this week?`
> `what's the current price of gold?`

Typically completes in **10–30 seconds**.

### Deep Research 🔬
Used for thorough research — reading full pages, documentation, technical articles, or fetching content from a specific URL. The bot searches the web AND reads entire pages including all chunks of content.

**How to trigger:** Use the words *"deep research"*, *"deep search"*, or paste a URL directly.
> `deep research the LangGraph documentation on state management`
> `https://docs.example.com/api-reference — summarise this page`

Deep research can take **2–5 minutes** depending on the length of the pages being read. You will see a status message in the thread while it works.

> **When to use which:** If you just need a quick answer or a news summary, use regular research. If you need to understand documentation, read a specific article in full, or research a technical topic in depth, use deep research.

---

## 10. Running Multiple Tasks at Once

You can ask the bot to handle several things in one message. The bot will automatically detect this as a complex request, create a plan, and execute each step in order.

> `research the top 5 CRM tools, then write me an email to my team summarising the findings`

The bot will:
1. Detect this as a multi-step task
2. Create a plan:
   - Step 1: Research top 5 CRM tools → Researcher
   - Step 2: Write summary email → Email Composer
3. Execute each step in order
4. Show you the email preview for approval before sending

### Tips for Multi-Task Requests

- You can combine research with emails, LinkedIn posts, or both
- The bot will always ask for your approval before sending emails or publishing posts, even within a planned multi-task flow
- If one step fails, the bot will report the error and stop — it will not skip ahead to the next step silently

---

## Quick Reference

| What you want | What to say |
|---|---|
| Quick web search | Ask naturally — *"what is..."*, *"find me..."* |
| Deep research / read a page | *"deep research..."* or paste a URL |
| Send an email | *"send an email to..."* |
| Create a LinkedIn post | *"write a LinkedIn post about..."* |
| Attach a file to an email | Upload file → click 📧 Email Attachment |
| Attach media to LinkedIn | Upload file → click 💼 LinkedIn Attachment |
| Give the bot a document to read | Upload file → click 📄 Context Attachment |
| Run multiple tasks | Describe everything in one message |
| Update your name or preferences | Mention bot → click ⚙️ Preferences |

---

*Gemma Swarm is an open source project. Contributions and feedback are welcome.*
