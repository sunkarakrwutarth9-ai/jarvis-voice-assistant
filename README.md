# J.A.R.V.I.S. — a voice-controlled AI assistant for Windows

Talk to your PC like Tony Stark. Say **"OK Jarvis"** and it opens apps, plays music, browses, writes code,
builds web pages and games, researches the web, reads your files, controls volume and windows — in **English,
Telugu or Hindi** — with an animated Dynamic Island and a cinematic 3D command center you can even control
with **hand gestures**.

> 📘 **Full illustrated guide:** open [`docs/guide.html`](docs/guide.html) in your browser after downloading.

---

## ⚡ Quick start (3 steps)

1. **Download** — click the green **Code** button above → **Download ZIP**, then right-click the ZIP → **Extract All**.
2. **Run** — open the extracted folder and double-click **`run.bat`**.
   The first time it sets everything up automatically (installs Python if needed, a private environment,
   and the AI models — about 5–15 minutes, depending on your internet).
3. **Add your free key** — Jarvis asks for a **Google Gemini API key** on first start. Get one free at
   <https://aistudio.google.com/apikey> and paste it in. Done — say **"OK Jarvis"**.

From then on, just double-click `run.bat` (or `Start Jarvis.vbs` for no console window).

### Requirements
- Windows 10 or 11, a microphone and speakers, an internet connection
- Python 3.10–3.12 (installed automatically if missing)
- Optional: an NVIDIA GPU makes the voice-clone feature fast (everything else works without one)

---

## 🎙️ What you can say

| Say | What happens |
|---|---|
| "OK Jarvis, open YouTube" · "play Believer" | Opens sites / plays videos (in Chrome) |
| "Open Chrome" | Pops up your Chrome accounts — pick one by voice or click |
| "Set volume to 30" · "brightness 70" · "lock the PC" | Controls your system |
| "Write a Python program that…" · "build a snake game" · "make a website for…" | Written **live** on the command-center canvas |
| "Make the button blue" · "make it shorter" | Edits what's on the canvas |
| "Research electric cars in India" | Searches the web, reads sources, writes a cited report |
| "Summarise my resume PDF" | Reads PDFs, Word, text and code files |
| "What's on my screen?" · "click the Subscribe button" | Sees and operates your screen |
| "Open WhatsApp and search for Mom" | Autopilot: clicks and types step by step |
| "Good morning Jarvis" | Weather, news, battery and your reminders |
| "Remember my exam is on Monday" | Long-term memory |
| "Look at this — what am I holding?" | One webcam photo, only when you ask |
| "Full screen" · "run it" · "save it" | Controls the canvas (nothing is saved without asking) |
| "Switch to cinematic / iOS / Iron Man theme" · "dark mode" | Changes the look |
| "Deactivate" | Ends conversation mode (Jarvis stops listening until "OK Jarvis") |

Speak **Telugu or Hindi** any time — Jarvis answers in the language you use.

---

## 🖥️ The command center — <http://localhost:7777>

Say **"open command center"**. A full-screen dashboard with a living 3D orb, system vitals, the conversation
log and the **canvas** where everything Jarvis creates appears (Preview / Code / Run / Save / Full screen).

**Gestures** (click ✋ on the orb or say "turn on gestures"; camera stays on your PC):
✋ palm = talk · ✊ fist = stop · 👍 = yes · ✌️ = full screen · ☝️ point + 🤏 pinch = click · 👋 swipe = next/previous.

---

## 🧑 Your own voice and face (optional)

Record a 10–20 s selfie video of yourself talking, then run:

```bat
.venv\Scripts\python.exe build_avatar.py "C:\path\to\video.mp4"
```

Click the round avatar next to the island (or say "talk in my voice") — Jarvis now speaks in **your voice**
with **your face** lip-synced. Say "learn my voice" to improve it with a clean 25-second recording.
Your voice and face stay on your PC and are never uploaded.

---

## 🔧 Troubleshooting

| Problem | Fix |
|---|---|
| Jarvis doesn't hear "OK Jarvis" | Raise the mic level: Settings → System → Sound → Input → Volume (80–90%). Or click the island / press Space in the command center. |
| "An Application Control policy has blocked this file" in `jarvis.log` | Windows **Smart App Control** blocked a library. Jarvis works around it (the fast local voice falls back to an online one). |
| Replies are slow / "rate limited" | Free Gemini keys have daily limits per model; Jarvis switches models automatically. An extra provider key can be added in `.env` (see below). |
| Something else | Run `run.bat console` to see the live log, or open `jarvis.log`. |

### Settings (`.env`, created on first run)
```ini
GEMINI_API_KEY=...            # main brain (free)
XPL_API_KEY=...               # optional extra provider (Experiential Labs) raced alongside Gemini
JARVIS_LANGUAGES=en-IN,te-IN,hi-IN
JARVIS_ME_MODE=0
```

---

## 🗂️ Project layout
| File | Role |
|---|---|
| `jarvis.py` | App: wake word → speech → AI → actions → voice; island, tray, dashboard server |
| `brain.py` | AI (Gemini + backups raced for speed, tool calling, memory) |
| `tools.py` | Everything Jarvis can do (apps, browser, canvas creations, research, files, autopilot…) |
| `listener.py` / `systemaudio.py` | Microphone, wake word, and ignoring the PC's own audio |
| `speech.py` / `voiceclone.py` | Voices (Kokoro / Microsoft neural) and your cloned voice |
| `island.py` | The Dynamic Island |
| `server.py` + `dashboard*.html` | Command center at localhost:7777 |

## Credits
Built with Google Gemini, OpenVoice V2 (MIT), Kokoro TTS (Apache-2.0), openWakeWord (Apache-2.0),
MediaPipe (Apache-2.0), three.js (MIT), PySide6, Playwright and many other open-source projects.
