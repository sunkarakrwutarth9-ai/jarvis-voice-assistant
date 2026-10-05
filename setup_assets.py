"""Downloads the model files that are too big for GitHub. Run by setup.bat; safe to run again."""
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = [
    ("https://huggingface.co/myshell-ai/OpenVoiceV2/resolve/main/converter/checkpoint.pth",
     HERE / "openvoice" / "checkpoints" / "checkpoint.pth", "OpenVoice voice converter (131 MB)"),
    ("https://huggingface.co/myshell-ai/OpenVoiceV2/resolve/main/converter/config.json",
     HERE / "openvoice" / "checkpoints" / "config.json", "OpenVoice config"),
    ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
     HERE / "assets" / "models" / "face_landmarker.task", "face landmark model (avatar builder)"),
]


def bar(done, total):
    if total > 0:
        pct = done * 100 // total
        sys.stdout.write(f"\r      [{'#' * (pct // 4):<25}] {pct:3d}%")
        sys.stdout.flush()


for url, dest, label in FILES:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ok  {label}")
        continue
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  get {label}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total, done = int(r.headers.get("Content-Length", 0)), 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            bar(done, total)
    tmp.replace(dest)
    print()

# openWakeWord's own small models (wake word "hey jarvis")
try:
    import openwakeword.utils
    openwakeword.utils.download_models(["hey_jarvis"])
    print("  ok  wake-word models")
except Exception as e:
    print(f"  !!  wake-word models will download on first run ({e})")
print("Models ready.")
