# Connect Atomo to Gmail (one time, about 3 minutes)

Atomo can sort your inbox, summarise emails, flag phishing and **write reply drafts**. **It never sends
email.** Drafts wait in Gmail → Drafts for you to review and send yourself. Your login stays with Google;
Atomo keeps a revocable token in `gmail_token.json` on your PC (never uploaded or committed).

If you already did the Google Home setup ([`smarthome.md`](smarthome.md)), only steps 2 and 4 are needed.

1. Do steps 1, 3 and 4 of [`smarthome.md`](smarthome.md) (Google Cloud project, consent screen with yourself as
   test user, `google_client.json` in the Atomo folder).
2. Enable the Gmail API: <https://console.cloud.google.com/apis/library/gmail.googleapis.com> → **Enable**.
3. (Consent screen → Data access / Scopes: add `.../auth/gmail.modify` if Google asks for it.)
4. Say **"OK Atomo, connect Gmail"** and allow access in the browser tab that opens.

Try:
- "Sort my inbox" → unread mail grouped as Important / Reply needed / Updates / Receipts / Newsletters /
  Promotions, labelled in Gmail under **Atomo/**, phishing flagged
- "Any emails from the college?" · "What did Amazon send me?"
- "Draft a reply to Ravi's email saying I'll join on Monday" → saved as a draft, **not sent**

Safety: emails are treated as untrusted text. Instructions hidden inside an email are never followed.
Testing-mode Google apps need re-connecting every 7 days ("connect Gmail" again).
