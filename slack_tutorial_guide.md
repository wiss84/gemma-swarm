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
10. [Gmail Integration](#10-gmail-integration)
11. [Google Calendar](#11-google-calendar)
12. [Google Docs](#12-google-docs)
13. [Google Sheets](#13-google-sheets)
14. [Running Multiple Tasks at Once](#14-running-multiple-tasks-at-once)

---

## 1. Your First Message

To start a conversation, **mention the Agent** in any channel it has been added to:

> `@YourAgentName hi, I need some help`

The Agent will reply **inside a thread** — all conversation happens there, keeping your channel clean.

On your very first message, the Agent will ask you to select or create a workspace before it can help you.

---

## 2. Setting Up Your Workspace

Every project lives inside its own **workspace** — a dedicated folder that stores your research, email drafts, LinkedIn post drafts, and file attachments. When you first mention the Agent, you will see this:

```
👋 Welcome! Please select or create a workspace to get started:

[ 🆕 New Project ]

Or continue an existing project:
┌─────────────────────────────┐
│ Select a project        ▼  │  ← dropdown menu
└─────────────────────────────┘

Or update your preferences:
[ ⚙️ Preferences ]
```

### Selecting from the Dropdown

The dropdown shows your projects sorted by **most recently used**. You can:
- Click to open and see all projects
- Type to search/filter projects
- Select any project to continue

### Creating a New Project

Click **🆕 New Project** — a modal will appear asking you to name your project. Use letters, numbers, and hyphens (e.g. `job-search`, `my-startup`). Click **Create** and the Agent will set everything up and process your first message.

### First-Time Preferences

If this is your first time using the Agent, after creating a project a **Welcome modal** will appear asking:
- **What would you like me to call you?** — your name, so the Agent can address you personally
- **Any other preferences?** — optional instructions like *"be more concise"* or *"always use formal language"*

Click **Save** and the Agent will remember these preferences across all future conversations.

---

## 3. Setting Your Preferences

You can update your preferences at any time. Simply mention the Agent and click the **⚙️ Preferences** button that appears in the workspace selection message. The modal will open with your current preferences pre-filled so you can edit them.

Your preferences are applied globally — they influence how the Agent communicates with you across every project.

---

## 4. Resuming an Existing Project

### From the same Slack thread

If you want to continue a conversation with visible history, just **mention the Agent directly in the original thread**. The Agent will load your full conversation history and pick up right where you left off.

### From a new Slack thread

If you mention the Agent in the channel and then select an **existing project** from the dropdown menu, the Agent will:
- Start a clean, fresh-looking Slack thread (no visible history)
- But internally load your **full conversation history** from that project

This is useful when you want a cleaner view but don't want to lose any context.

---

## 5. Sending a Message While the Agent is Working

If you send a new message while the Agent is still processing a previous task, it will **pause and ask you what to do**:

```
⚡ New message received while I'm still working.
New message: "can you also check the pricing?"

What should I do?

[ 🔀 Combine ]  [ 🆕 Fresh Start ]  [ 📋 Queue ]

No response in 5 minutes → will queue automatically
```

### 🔀 Combine
Merges your new message with the current task into a single combined request. The current task is cancelled and the Agent starts fresh handling Agenth things together.

> **Example:** You asked to *"research the best laptops"* and then sent *"also check prices"* — Combine turns this into *"research the best laptops and also check prices"* in one go.

### 🆕 Fresh Start
Cancels the current task entirely and starts fresh with your new message. Previous conversation history is preserved, but the interrupted task is discarded.

> **Use this when:** You changed your mind and want to do something completely different.

### 📋 Queue
Lets the current task finish first, then automatically processes your new message afterwards. Nothing is lost or cancelled.

> **Use this when:** Agenth tasks are important and you want them handled one after the other.

If you don't click anything within 5 minutes, the Agent will **queue automatically**.

---

## 6. Attaching Files

You can attach files directly in the Slack thread. After uploading a file, the Agent will ask you how to use it:

```
📎 Resume_Sept25.pdf

How would you like to attach this file?

[ 📧 Email Attachment ]  [ 💼 LinkedIn Attachment ]  [ 📄 Context Attachment ]
```

### 📧 Email Attachment
Saves the file to your project's email attachments folder. When the email is sent, this file will be included as an attachment.

> **Important:** You must mention the filename including its extension in your message — the Agent will not attach files automatically.
> Example: `send an email to john@example.com with my resume attached: Resume_Sept25.pdf`

Supported file types: `pdf`, `docx`, `xlsx`, `csv`, `txt`, `png`, `jpg`, `jpeg`, `zip`, `rar`

### 💼 LinkedIn Attachment
Saves the file to your project's LinkedIn media folder. When you ask the Agent to create a LinkedIn post, it will include this file as the post's media attachment.

> **Important:** You must mention the filename including its extension in your message — the Agent will not attach files automatically.
> Example: `write a LinkedIn post about my new guide and attach: slack_tutorial_guide.pdf`

Supported file types: `pdf`, `doc`, `docx`, `ppt`, `pptx`, `jpg`, `jpeg`, `png`, `gif`, `mp4`, `mov`

### 📄 Context Attachment
Saves the file and passes its content directly to the Agent as context. Use this when you want the Agent to **read and understand** the file — for example, uploading a document and asking *"summarise this"* or *"write an email based on this report"*.

> **Tip:** You can include a message along with your file upload. The Agent will use that message as your instruction for what to do with the file.

---

## 7. Sending Emails

Simply ask the Agent to send an email in plain language:

> `send an email to john@example.com and remind him about our meeting tomorrow at 3pm`

The Agent will compose the email and show you a **preview** before sending:

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
Opens a feedback modal where you can type exactly what needs to change — for example: *"make it shorter"* or *"sign it with my name Wissam"*. The Agent will rewrite the email incorporating your feedback and show you a new preview.

You can reject and provide feedback as many times as needed until you are happy with the result.

> **Tip:** You can specify a language — *"write it in French"* — and the entire email including the greeting and closing will be written in that language.

---

## 8. Creating LinkedIn Posts

Ask the Agent to create a post in plain language:

> `write a LinkedIn post about our new product launch`

The Agent will compose the post and show you a **preview**:

```
💼 LinkedIn Post Draft — Please Review

[Post content preview here...]

[ ✅ Publish Post ]  [ ✏️ Reject & Give Feedback ]
```

### ✅ Publish Post
Approves the draft and publishes it to LinkedIn immediately.

### ✏️ Reject & Give Feedback
Opens a feedback modal where you can describe what to change — for example: *"add more hashtags"* or *"make the tone more professional"*. The Agent rewrites the post and shows a new preview.

### Attaching Media to a LinkedIn Post
Upload your image or video using the **💼 LinkedIn Attachment** button (see Section 6) before asking for the post. When you ask the Agent to create the post, mention the filename and it will be included as the post's media.

---

## 9. Research vs Deep Research

The Agent has two research modes depending on what you need.

### Regular Research 🔍
Used for quick lookups — news, facts, prices, recent events. The Agent searches the web and returns a structured summary with sources.

**How to trigger:** Just ask naturally.
> `what are the latest AI news this week?`
> `what's the current price of gold?`

Typically completes in **10–30 seconds**.

### Deep Research 🔬
Used for thorough research — reading full pages, documentation, technical articles, or fetching content from a specific URL. The Agent searches the web AND reads entire pages including all chunks of content.

**How to trigger:** Use the words *"deep research"*, *"deep search"*, or paste a URL directly.
> `deep research the LangGraph documentation on state management`
> `https://docs.example.com/api-reference — summarise this page`

Deep research can take **2–5 minutes** depending on the length of the pages being read. You will see a status message in the thread while it works.

> **When to use which:** If you just need a quick answer or a news summary, use regular research. If you need to understand documentation, read a specific article in full, or research a technical topic in depth, use deep research.

---

## 10. Gmail Integration

The Agent can read and manage your Gmail inbox — list emails, read specific messages, check for emails from a specific sender, and even watch for new emails.

### Listing Emails 📬
Ask to see your unread emails:
> `show me my unread emails`
> `Show me the latest N emails`

The Agent will display a list with sender, subject, date, and message ID.

### Reading an Email 📖
After listing emails, ask to read a specific one naturally:
> `read the email wissam, google, etc...`

### Checking for a Specific Sender 🔍
Ask the Agent to check if you have an email from a specific sender:
> `do I have any emails from john@example.com?`

### Watching for New Emails 👀
You can ask the Agent to watch for a specific sender and notify you when a new email arrives:
> `watch for emails from hr@company.com`
> `notify me when i get an email from hr@company.com`
> `let me know when i get an email from hr@company.com`

The Agent will check every 5 minutes and notify you in the thread when a matching email arrives.

### Stopping a Watch ⏹
> `stop watching for emails from john@example.com`

### Listing Active Watches 📋
> `show my active email watches`
> `which emails are being watched?`

---

## 11. Google Calendar

The Agent can manage your Google Calendar — list events, see your next event, create new events, and delete events.

### Listing Events 📅
Ask to see your calendar events:
> `what are my events this week?`
> `show me today's calendar`
> `list events for next month`

The Agent will display each event with title, date/time, location, description, and event ID.

### Next Event ⏭
> `what's my next event?`
> `what's coming up next on my calendar?`

### Creating an Event ✏️
Ask to create a calendar event:
> `create a meeting with the team tomorrow at 2pm`
> `schedule a call with john@example.com next Friday at 10am for 1 hour`

The Agent will create the event and show you a preview for confirmation:

```
📅 Calendar event ready for confirmation:
*Team Meeting*
📅 2024-03-15 14:00 → 15:00
🔗 <link|Open in Calendar>

[ ✅ Confirm ]  [ ✏️ Cancel ]
```

### Deleting an Event 🗑
> `delete the event with ID: abc123...`

> **Tip:** First ask to list events so you can find the correct event ID.

---

## 12. Google Docs

The Agent can create, read, and update Google Docs.

### Creating a Document 📄
Ask to create a new Google Doc:
> `create a document called "Meeting Notes"`
> `make a new doc for my project proposal`
> `create a Google Doc with the content: Hello World`

The Agent will create the document and show you a preview for confirmation:

```
📄 Google Doc ready for confirmation:
*Meeting Notes*
🔗 <link|Open in Google Docs>

[ ✅ Confirm ]  [ ✏️ Cancel ]
```

### Reading a Document 📖
Provide a document ID or URL to read:
> `read the document with ID: 1a2b3c...`
> `open this doc: https://docs.google.com/document/d/1a2b3c...`

The Agent will display the document content (truncated if too long) with a link to open it.

### Updating a Document ✏️
> `add "meeting notes" to the document 1a2b3c...`
> `append this text to my doc: Here are the action items...`

The Agent will update the document and show you a confirmation preview.

---

## 13. Google Sheets (Under Testing)

The Agent can create, read, and update Google Sheets.

### Creating a Spreadsheet 📊 
Ask to create a new Google Sheet:
> `create a spreadsheet called "Budget 2024"`
> `make a new sheet with this data: Name, Email, Phone`

You can specify rows of data:
> `create a sheet with columns: Item, Quantity, Price and rows: Apple, 5, 1.50 | Banana, 3, 0.75`

The Agent will create the spreadsheet and show you a preview for confirmation:

```
📊 Google Sheet ready for confirmation:
*Budget 2024*
🔗 <link|Open in Google Sheets>

[ ✅ Confirm ]  [ ✏️ Cancel ]
```

### Reading a Spreadsheet 📖
Provide a spreadsheet ID or URL to read:
> `read the spreadsheet with ID: 1a2b3c...`
> `show me the data in: https://docs.google.com/spreadsheets/d/1a2b3c...`

The Agent will display the sheet content in a table format (showing first 50 rows).

### Updating a Spreadsheet ✏️
> `add a row to sheet 1a2b3c with: New Item, 10, 25.00`
> `update cells A1:B2 with: Header1, Header2 | Value1, Value2`

---

## 14. Running Multiple Tasks at Once

You can ask the Agent to handle several things in one message. The Agent will automatically detect this as a complex request, create a plan, and execute each step in order.

> `research the top 5 CRM tools, then write me an email to my team summarising the findings`

The Agent will:
1. Detect this as a multi-step task
2. Create a plan:
   - Step 1: Research top 5 CRM tools → Researcher
   - Step 2: Write summary email → Email Composer
3. Execute each step in order
4. Show you the email preview for approval before sending

---

*Gemma Swarm is an open source project. Contributions and feedback are welcome.*
