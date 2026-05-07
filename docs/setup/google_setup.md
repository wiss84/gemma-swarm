## Google Workflow Setup Guide

To use Google Workspace services (Gmail, Calendar, Docs, Sheets, Drive) via your agent, you need to set up a **Google Cloud Project** and authenticate using OAuth. Here's how to get started:

---

### Prerequisites

Before you begin, you need:

1. A **Google Account** (personal)
2. Access to **[Google Cloud Console](https://cloud.google.com/cloud-console)**

---

### Step 1: Create a Google Project

1. Go to [Google Cloud Console](https://cloud.google.com/cloud-console)
2. Click **Console** at the top right of your screen
3. Select or create a new project

---

### Step 2: Enable Google Workspace APIs

1. Go to **APIs & Services** → Click **Library** from the left sidebar
2. Scroll to **Google Workspace**, click **View all**
3. Enable the following APIs by clicking on each one and selecting **Enable**:
   - Gmail API
   - Google Docs API
   - Google Sheets API
   - Google Drive API
   - Google Calendar API

---

### Step 3: Configure OAuth Consent Screen

1. Click on **OAuth consent screen** from the left sidebar
2. Under **Data Access**, click **Add or remove scopes**
3. In **Manually add scopes**, paste these links:
   ```
   https://www.googleapis.com/auth/gmail.readonly
   https://www.googleapis.com/auth/calendar
   https://www.googleapis.com/auth/documents
   https://www.googleapis.com/auth/spreadsheets
   https://www.googleapis.com/auth/drive.file
   ```
4. Click **Add to table** then click **Update**

---

### Step 4: Create OAuth Credentials

1. Go to **APIs & Services** → click **Credentials** from the left sidebar
2. Click **+ Create Credentials** from the top → Click **OAuth client ID** from the dropdown menu
3. If prompted, configure OAuth consent screen:
   - **User type**: External (for personal use)
   - Add your email
   - Choose: **Web Application**
4. Under **Authorized redirect URIs**, click **+ Add URI** and paste:
   ```
   http://localhost:8766/google/callback
   ```
5. Click **Create** and download the JSON file containing your `client_id` and `client_secret`
6. Rename the JSON file to `Google_creds.json` and place it in the project root directory

---

### Step 5: Set Publishing Status

1. Click on **Publishing status** from the left sidebar
2. Change from **Testing** to **Production**

This ensures your refresh token will work indefinitely, rather than expiring after 1 week.

---

### OAuth Authentication

The first time you trigger one of the Google agents (Gmail, Calendar, Docs, Sheets), the agent will automatically trigger the OAuth flow:

1. The bot will post an **authorization link** to your Slack thread
2. Click the link and sign in to your Google account
3. Grant the required permissions (if prompted)
4. You may see a "not found" page — click **Advanced** at the bottom and click your project name to proceed
5. After successful authorization, a token will be saved to `google_state.json`

---

### Token Expiry & Refresh

- **Refresh Token**: Valid indefinitely (when using Production mode)
- **Access Token**: Short-lived, automatically refreshed by the agent

If you keep the status as "Testing", the refresh token will expire after 1 week. For persistent access, ensure you set the publishing status to **Production**.

---

### Rate Limits

- **Gmail API**: 100 requests per 100 seconds
- **Google Drive API**: 1,000 requests per 100 seconds
- **Google Calendar API**: 100 requests per 100 seconds
- **Google Sheets API**: 100 requests per 100 seconds
- **Google Docs API**: 60 requests per 60 seconds

The agent handles rate limiting automatically with retry logic.

---
