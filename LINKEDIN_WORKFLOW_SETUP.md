## LinkedIn Workflow Setup Guide

To post to LinkedIn via the your agent, you need to set up a **LinkedIn Developer App** and authenticate using OAuth. Here's how to get started:

---

### Prerequisites

Before you begin, you need:

1. A **LinkedIn account** (personal)
2. A **LinkedIn Company Page** (must be created first - see below)
3. Then go to the **LinkedIn Developer Portal**

---

### Step 1: Create a LinkedIn Company Page

If you don't have a Company Page and want to post as yourself:

1. Go to **[linkedin.com/company/setup/new](https://www.linkedin.com/company/setup/new/)**
2. Enter your company details:
   - **Company Name**: Your name or brand
   - **Company Size**: Select **0-1 employees** (Small Business)
   - **Company Type**: Select **Self-Employed** or **Privately Held**
3. Complete the verification steps
4. Once created, you can proceed to create your Developer App

---

### Step 2: Create LinkedIn Developer App

1. Go to **[developer.linkedin.com](https://developer.linkedin.com)** and sign in with your LinkedIn account

2. Navigate to **My Apps** (top right) and click **Create App**

3. Fill in the app details:
   - **App Name**: Gemma Swarm (or any name you prefer)
   - **LinkedIn Company Page**: Select your Company Page (created in Step 1)
   - **App Logo**: Upload a logo
   - **Legal agreement**:  check the box

4. Click **Create App** to finish setup

---

### Step 3: Request Product Access

1. Go to the **Products** tab in your app page

2. Click **Request access** on the following products:
   - **Share on LinkedIn** — Allows posting on your behalf
   - **Sign In with LinkedIn using OpenID Connect** — Enables OpenID authentication

   These two products grant the required permissions:
   - `openid` — Basic profile
   - `profile` — Full profile access
   - `email` — Email address
   - `w_member_social` — Post on your behalf (required for publishing)

---

### Step 4: Configure OAuth Redirect URLs

1. Go to the **Auth** tab in your app settings

2. Under **OAuth 2.0 settings**, add these redirect URLs:
   - `https://www.linkedin.com/developers/tools/oauth/redirect`
   - `http://localhost:8765/linkedin/callback`

---

### Step 5: Add Environment Variables

1. Copy your:
   - **Client ID**
   - **Client Secret**

2. Add to your `.env` file:

```bash
LINKEDIN_CLIENT_ID=your_client_id_here
LINKEDIN_CLIENT_SECRET=your_client_secret_here
```

---

### OAuth Authentication

The first time you try to post, the agent will automatically trigger the OAuth flow:

1. The bot will post an **authorization link** to your Slack thread
2. Click the link and sign in to LinkedIn
3. Grant the required permission (if prompted)
4. After successful authorization, an access token will be saved to `linkedin_state.json`

---

### Token Expiry & Refresh

- **Access Token**: Valid for **60 days**

When the access token expires, the agent will automatically request re-authorization via Slack. Simply click the new auth link to refresh the token.

---

### Rate Limits

- **Daily Post Limit**: 100 posts per day
- **Warning Threshold**: 90 posts — you'll receive a warning when approaching the limit

The agent tracks your daily usage in `linkedin_state.json`.

---
