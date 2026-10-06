# ATOMO — a voice-controlled AI assistant for Windows

*(formerly J.A.R.V.I.S. — "Hey Jarvis" still wakes it too)*

Talk to your PC like Tony Stark. Say **"OK Atomo"** and it opens apps, plays music, browses, writes code,
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
3. **Add your free key** — Atomo asks for a **Google Gemini API key** on first start. Get one free at
   <https://aistudio.google.com/apikey> and paste it in. Done — say **"OK Atomo"**.

From then on, just double-click `run.bat` (or `Start Jarvis.vbs` for no console window).
To have Atomo start by itself every time you sign in to Windows, double-click **`autostart.bat`**
(`autostart.bat off` turns that off again).

### Requirements
- Windows 10 or 11, a microphone and speakers, an internet connection
- Python 3.10–3.12 (installed automatically if missing)
- Optional: an NVIDIA GPU makes the voice-clone feature fast (everything else works without one)

---

## 🎙️ What you can say

| Say | What happens |
|---|---|
| "OK Atomo, open YouTube" · "play Believer" | Opens sites / plays videos (in Chrome) |
| "Open Chrome" | Pops up your Chrome accounts — pick one by voice or click |
| "Set volume to 30" · "brightness 70" · "lock the PC" | Controls your system |
| "Write a Python program that…" · "build a snake game" · "make a website for…" | Written **live** on the command-center canvas |
| "Make a 3D globe of the Earth" · "build a solar system" · "show a 3D atom" | Interactive **3D** scenes you can rotate and zoom |
| "Make a piano" · "a drum machine" · "simulate gravity" · "an animated logo" | Music apps, simulations and animations |
| "Make the button blue" · "make it shorter" | Edits what's on the canvas |
| "Remind me to call Mom at 6 pm" · "wake me up at 6:30 on weekdays" | Reminders and alarms, spoken aloud when due (survive restarts) |
| "Add milk and eggs to my shopping list" · "what's on my to-do list?" | Shopping / to-do / any lists |
| "When I say good night, mute the volume and lock the PC" | Routines: your own phrase runs several steps |
| Play any music | A little robot dances to the beat in the screen corner (click-through; hides in full screen). "Make the robot dance" · "hide the robot" · "move the robot left" |
| "Turn on the AC" · "set the AC to 24" · "turn off all the lights" | Controls your **Google Home** devices (one-time setup: [`docs/smarthome.md`](docs/smarthome.md)); ON/OFF buttons in the 🏠 HOME tab |
| "Tell me a joke" · "quiz me on capitals" · "how many km in 10 miles?" | Jokes, quizzes, conversions, meanings, translations |
| "Research electric cars in India" | Searches the web, reads sources, writes a cited report |
| "Summarise my resume PDF" | Reads PDFs, Word, text and code files |
| "What's on my screen?" · "click the Subscribe button" | Sees and operates your screen |
| "Open WhatsApp and search for Mom" | Autopilot: clicks and types step by step |
| "Good morning Atomo" | Weather, news, battery and your reminders |
| "Remember my exam is on Monday" | Long-term memory |
| "Look at this — what am I holding?" | One webcam photo, only when you ask |
| "Full screen" · "run it" · "save it" | Controls the canvas (nothing is saved without asking) |
| "Switch to cinematic / iOS / Iron Man theme" · "dark mode" | Changes the look |
| "Deactivate" | Ends conversation mode (Atomo stops listening until "OK Atomo") |

Speak **Telugu or Hindi** any time — Atomo answers in the language you use.

---

## 🖥️ The command center — <http://localhost:7777>

Say **"open command center"**. A full-screen dashboard with a living 3D **atom** (a glowing nucleus with
electrons racing around it, reacting to your voice), system vitals, the conversation log and the **canvas**
with a **Daily** panel (reminders and alarms with one-click cancel, shopping/to-do lists you can tick off or type into,
routines with ▶ run buttons), live weather and next-alarm chips.

Everything Atomo creates opens in its own **Atomo Screen** window (on your second monitor if you have one),
so the command center is never covered: Preview / Code / Run / Save / Copy / VS Code / Full screen.

**Gestures — in every app** (click ✋ GESTURE or say "turn on gestures"; the camera stays on your PC):
✋ palm = talk · ✊ fist = stop Atomo / pause-play media ·
👍 = yes · ✌️ = full screen · 👋 swipe = next/previous (slides, photos, video seek).
Your mouse is never moved unless you ask: say "control my mouse with gestures" to point = move the cursor, 🤏 pinch = click.
While the command center is in front, your hand **sculpts the 3D atom** instead: move an open palm to turn it,
bring your hand closer to zoom, make a fist to collapse the electrons, open your hand to burst them out, ✌️ to make them race.

---

## 🧑 Your own voice and face (optional)

Record a 10–20 s selfie video of yourself talking, then run:

```bat
.venv\Scripts\python.exe build_avatar.py "C:\path\to\video.mp4"
```

Click the round avatar next to the island (or say "talk in my voice") — Atomo now speaks in **your voice**
with **your face** lip-synced. Say "learn my voice" to improve it with a clean 25-second recording.
Your voice and face stay on your PC and are never uploaded.

---

## 🔧 Troubleshooting

| Problem | Fix |
|---|---|
| Atomo doesn't hear "OK Atomo" | Raise the mic level: Settings → System → Sound → Input → Volume (80–90%). Or click the island / press Space in the command center. |
| "An Application Control policy has blocked this file" in `jarvis.log` | Windows **Smart App Control** blocked a library. Atomo works around it (the fast local voice falls back to an online one). |
| Replies are slow / "rate limited" | Free Gemini keys have daily limits per model; Atomo switches models automatically. An extra provider key can be added in `.env` (see below). |
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
| `tools.py` | Everything Atomo can do (apps, browser, canvas creations, research, files, autopilot…) |
| `listener.py` / `systemaudio.py` | Microphone, wake word, and ignoring the PC's own audio |
| `speech.py` / `voiceclone.py` | Voices (Kokoro / Microsoft neural) and your cloned voice |
| `everyday.py` | Reminders, alarms, lists and routines |
| `robot.py` | The dancing robot (beat detection from the speakers) |
| `gestures.py` | System-wide hand-gesture control (webcam + MediaPipe) |
| `island.py` | The Dynamic Island |
| `server.py` + `dashboard*.html` | Command center at localhost:7777 |

## Credits
Built with Google Gemini, OpenVoice V2 (MIT), Kokoro TTS (Apache-2.0), openWakeWord (Apache-2.0),
MediaPipe (Apache-2.0), three.js (MIT), PySide6, Playwright and many other open-source projects.
