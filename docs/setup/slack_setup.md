## Slack Workflow Setup Guide

To connect your project to Slack, you need to create a Slack App and get the required tokens. Here's the complete flow:

---

## 1. Create a Free Workspace


1. Go to [https://slack.com/get-started](https://slack.com/get-started)
2. Enter your email address and click "Continue"
3. Click "Create a workspace"
4. Choose a workspace name (e.g., your project name)
5. Add your name and your photo
6. Invite team members if needed (optional for testing) or click 'Skip this step'
7.  Click 'Start with the limited Free Version'
8. Right click on new-channel --> View channel details --> Edit channel name (pick a name)

---

## 2. Set Up Slack App

### Step 1: Create a New App
1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click "Create New App"
3. Select "From scratch"
4. Enter your app name (e.g., "Gemma Swarm Bot")
5. Select your workspace from the dropdown
6. Click "Create App"

---

### Step 2: Basic App Information
1. In the left sidebar, click "Basic Information"
2. Scroll down to App-Level Tokens --> Click Generate Token and Scopes
3. In the Token Name field write: agent_socket_token
4. Click Add Scope --> Choose connections:write --> Click Generate --> Copy and save it somewhere then click Done
5. Scroll down to Display Information: Write your App name and Upload an app icon (optional but recommended)

---

## 3. Configure Bot Permissions
1. In the left sidebar, click "Socket Mode"
2. Toggle\Enable Socket Mode
3. In the left sidebar, click "OAuth & Permissions"
4. Scroll down to Scopes --> 'Bot Token Scopes' --> Click the Arrow pointed to right
5. Click 'Add an OAuth Scope' Add the following 
- bot token scopes:
   - `app_mentions:read` 
   - `channels:history` 
   - `channels:read` 
   - `chat:write` 
   - `files:read`
   - `groups:read`    
- User Token Scopes:
  - `files:read`
  - `files:write`
6. In the left sidebar, click "Event Subscriptions"
7. Turn on 'Enable Events'
8. Under "Subscribe to bot events" click "Add Bot User Event"
9. Add 'file_shared'
10. In the left sidebar, click "Install App"
11. Click install to (the name you picked for your worksape)
12. Choose your Workspace name from the drop down menu and click Allow
13. Copy the Bot User OAuth Token
14. In the left sidebar, click "App Home"
15. In the 'Your App’s Presence in Slack' You will find 'Display Name (Bot Name):'
16. The Display name is the bot name you will mention in slack channel (e.g., @name)
17. Toggle 'Always Show My Bot as Online' to turn it on
18. In the left sidebar, click "Install App" again
19. Click Reinstall to (the name you picked for your worksape)

---

## 4. Set Up Environment Variables
In your `.env` file in your project root. Add the following variables:

```bash
# Slack Configuration
Bot_User_OAuth_Token=xoxb-your-oauth-token-here
agent_socket_token=xapp-your-agent-socket-token-here
```

---

## 5. Add the app to slack channels from step 1 (e.g. for ai-updates, chating)
1. Click on any channel to open it.
2. Click on the channel name on the top → 'Integrations'.
3. Go to Apps section and click 'Add an App' → 'Add' your app name.

### Important: 
If you want to use the automated flow for the Autonomous updates. You need to create a channel for updates and add the app to it.

---
