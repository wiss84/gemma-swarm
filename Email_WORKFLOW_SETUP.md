## Email Workflow Setup Guide

To send emails via Gmail SMTP, you need an **App Password** (not your regular Gmail password). Here's how to get one:

---

### Prerequisites

- ✅ You need **2-Factor Authentication (2FA)** enabled on your Google Account

---

### Steps to Create App Password

1. Go to **[myaccount.google.com](https://myaccount.google.com)** and sign in

2. Navigate to **Security & sign-in** (left sidebar)

3. Under **"How you sign in to Google,"** find **2-Step Verification** and make sure it's **ON**

4. In the **search bar** at the top of the page, search for **"App Passwords"** and click on it when it appears

5. Enter your **App name** (any name you prefer) then click **Create**

6. Copy the **16-character password** that appears (format: `xxxx xxxx xxxx xxxx`)

7. Add to your `.env` file:

```bash
HUMAN_EMAIL=your_email@gmail.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx
```

---

