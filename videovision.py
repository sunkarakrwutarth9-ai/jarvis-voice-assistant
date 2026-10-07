"""Video vision: Ultron watches a short clip instead of one still photo - so it understands what HAPPENS
(movement, gestures, actions, changes over time), not just what's in a single frame.

Sources: the webcam ("watch me", "what am I doing?"), the screen ("watch my screen for 10 seconds"),
or a video file on the PC ("watch this video and summarise it"). Frames are sampled in order and sent
together to the vision model. The camera is used only for the requested seconds; nothing is saved.
"""

import base64
import io
import logging
import time
from pathlib import Path

log = logging.getLogger("jarvis.video")
MAX_FRAMES = 10


def _jpeg(img_bgr_or_pil, max_side=768):
    from PIL import Image
    if not isinstance(img_bgr_or_pil, Image.Image):
        import cv2
        img_bgr_or_pil = Image.fromarray(cv2.cvtColor(img_bgr_or_pil, cv2.COLOR_BGR2RGB))
    im = img_bgr_or_pil.convert("RGB")
    s = min(1.0, max_side / max(im.size))
    if s < 1:
        im = im.resize((int(im.width * s), int(im.height * s)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=72)
    return base64.b64encode(buf.getvalue()).decode()


def _from_camera(seconds):
    import cv2
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError("the camera is not available (in use by gestures or another app?)")
    frames, t0, every = [], time.time(), max(0.25, seconds / MAX_FRAMES)
    nxt = t0
    try:
        for _ in range(5):                     # let the exposure settle
            cap.read()
        while time.time() - t0 < seconds:
            ok, f = cap.read()
            if ok and time.time() >= nxt:
                frames.append(_jpeg(f))
                nxt += every
    finally:
        cap.release()
    return frames


def _from_screen(seconds):
    from PIL import ImageGrab
    frames, t0, every = [], time.time(), max(0.4, seconds / MAX_FRAMES)
    while time.time() - t0 < seconds and len(frames) < MAX_FRAMES:
        frames.append(_jpeg(ImageGrab.grab(), 1024))
        time.sleep(every)
    return frames


def _from_file(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    if n <= 0:
        raise RuntimeError("couldn't read that video")
    frames = []
    for k in range(MAX_FRAMES):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (k + .5) / MAX_FRAMES))
        ok, f = cap.read()
        if ok:
            frames.append(_jpeg(f))
    cap.release()
    return frames, n / fps


def _ask(prompt, frames):
    import tools
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in frames]
    last = None
    for model in tools.best_models()[:3]:
        try:
            r = tools.llm_client.with_options(timeout=45).chat.completions.create(
                model=model, max_tokens=700, messages=[{"role": "user", "content": content}])
            return r.choices[0].message.content or ""
        except Exception as e:
            last = e
            log.warning("video vision via %s failed: %s", model, str(e)[:100])
    raise last or RuntimeError("no vision model answered")


def watch(source: str = "camera", seconds: float = 4, question: str = "") -> str:
    """Watch a short video (camera / screen / file path) and describe or answer about it."""
    import tools
    seconds = max(2.0, min(float(seconds or 4), 20.0))
    q = question.strip() or "Describe what is happening."
    src = (source or "camera").strip()
    try:
        if src.lower() in ("camera", "webcam", "me", "cam"):
            tools.progress(f"Watching through the camera ({int(seconds)} s)")
            frames, what = _from_camera(seconds), f"a {int(seconds)}-second webcam video of the user (and anyone with them)"
        elif src.lower() in ("screen", "display", "monitor"):
            tools.progress(f"Watching the screen ({int(seconds)} s)")
            frames, what = _from_screen(seconds), f"a {int(seconds)}-second recording of the user's screen"
        else:
            p = Path(src.strip('"'))
            if not p.exists():
                return f"FAILED: couldn't find the video '{src}'."
            tools.progress("Watching the video")
            frames, dur = _from_file(p)
            what = f"the video file '{p.name}' ({int(dur)} s long; frames spread evenly across it, no audio)"
    except Exception as e:
        return f"FAILED: {e}"
    if not frames:
        return "FAILED: no frames were captured."
    answer = _ask(f"These {len(frames)} images are frames IN ORDER from {what}. Treat them as one moving video: "
                  f"describe what happens over time - movements, gestures, actions, expressions, what changes - "
                  f"then answer: {q}\nBe concise and concrete (2-5 sentences); don't describe each frame separately.",
                  frames)
    return "OK: " + (answer.strip() or "I couldn't make out what happened.")
