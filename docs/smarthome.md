# Connect Ultron to Google Home (one time, about 10 minutes)

Ultron controls your AC, lights, fans and plugs through **your own Google account**. It sends the
same commands you'd say to a Nest speaker ("turn on the AC", "set the AC to 24 degrees") to the
Google Assistant API, and Google Home does the rest. Your Google login stays with Google; Ultron only
keeps a revocable token in `google_token.json` on your PC (never uploaded or committed).

## 1. Create a Google Cloud project
1. Open <https://console.cloud.google.com/> and sign in with **the same Google account as your Google Home app**.
2. Top bar → project picker → **New project** → name it `Ultron` → **Create**, then select it.

## 2. Turn on the Google Assistant API
1. Go to <https://console.cloud.google.com/apis/library/embeddedassistant.googleapis.com>.
2. Click **Enable**.

## 3. Consent screen
1. **APIs & Services → OAuth consent screen** (or "Google Auth Platform → Branding").
2. User type **External** → app name `Ultron`, your email as the support and developer contact → Save.
3. **Audience / Test users → Add users** → add your own Gmail address → Save.
   (The app stays in *Testing* mode. That's fine: only you use it.)

## 4. Create the desktop client
1. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type **Desktop app**, name `Ultron` → **Create**.
3. Click **Download JSON**, rename the file to **`google_client.json`**, and put it in the Ultron folder
   (next to `jarvis.py`).

## 5. Sign in once
Say **"OK Ultron, connect Google Home"**, or press **Connect Google Home** in the command center
(Daily panel → 🏠 HOME). A browser tab opens. Choose your account, click **Continue** on the
"Google hasn't verified this app" screen (it's your own app), and allow access.

Done. Try:
- "Turn on the bedroom AC"
- "Set the AC to 24 degrees"
- "Turn off all the lights"
- "Is the fan on?"

Devices you use appear as ON/OFF buttons in the 🏠 HOME tab.

**Tokens in a Testing-mode app expire after 7 days.** When that happens, Ultron will say Google
Home isn't connected; just say "connect Google Home" again. To remove access completely, delete
`google_token.json` and remove Ultron at <https://myaccount.google.com/permissions>.
