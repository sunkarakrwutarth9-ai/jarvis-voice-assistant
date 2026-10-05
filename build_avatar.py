"""One-time setup: turn a selfie video of the user into Jarvis's voice + talking face.

    python build_avatar.py "C:\\path\\to\\video.mp4"

Creates:
    assets/voice_ref.wav        voice sample used to clone the user's voice
    assets/avatar/*.jpg         face-centred frames (square), sharp ones only
    assets/avatar/index.json    per-frame mouth openness ("jawOpen") for lip-sync
"""

import json
import subprocess
import sys
from pathlib import Path

import cv2
import imageio_ffmpeg
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
AVATAR = ASSETS / "avatar"
MODEL = ASSETS / "models" / "face_landmarker.task"
SIZE = 360          # saved frame size (px)
FPS = 15


def extract_voice(video: Path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-ac", "1",
                    "-ar", "22050", "-af", "highpass=f=70,loudnorm", str(ASSETS / "voice_ref.wav")], check=True)


def read_frames(video: Path):
    """Decode frames (ffmpeg applies the phone's rotation) as RGB arrays."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    reader = imageio_ffmpeg.read_frames(str(video), output_params=["-vf", f"fps={FPS},scale=720:-2"])
    meta = next(reader)
    w, h = meta["size"]
    for raw in reader:
        yield np.frombuffer(raw, np.uint8).reshape(h, w, 3)


def build(video: Path):
    ASSETS.mkdir(exist_ok=True)
    AVATAR.mkdir(parents=True, exist_ok=True)
    for old in AVATAR.glob("*.jpg"):
        old.unlink()
    extract_voice(video)

    landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL)),
        output_face_blendshapes=True, num_faces=1))

    frames = []
    centre = None
    for i, rgb in enumerate(read_frames(video)):
        res = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
        if not res.face_landmarks:
            continue
        h, w = rgb.shape[:2]
        pts = np.array([(p.x * w, p.y * h) for p in res.face_landmarks[0]])
        jaw = next(b.score for b in res.face_blendshapes[0] if b.category_name == "jawOpen")
        x0, y0 = pts.min(0)
        x1, y1 = pts.max(0)
        c = np.array([(x0 + x1) / 2, (y0 + y1) / 2 + (y1 - y0) * 0.08])
        centre = c if centre is None else centre * 0.5 + c * 0.5      # damp camera shake
        side = max(x1 - x0, y1 - y0) * 1.75
        sx, sy = int(centre[0] - side / 2), int(centre[1] - side / 2)
        pad = int(side)
        padded = cv2.copyMakeBorder(rgb, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        crop = padded[sy + pad: sy + pad + int(side), sx + pad: sx + pad + int(side)]
        crop = cv2.resize(crop, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
        sharp = cv2.Laplacian(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var()
        frames.append({"i": i, "jaw": float(jaw), "sharp": float(sharp), "img": crop})

    if not frames:
        sys.exit("No face found in the video.")
    cutoff = np.percentile([f["sharp"] for f in frames], 35)     # drop the blurriest third
    kept = [f for f in frames if f["sharp"] >= cutoff]
    index = []
    for n, f in enumerate(kept):
        name = f"f{n:04d}.jpg"
        cv2.imwrite(str(AVATAR / name), cv2.cvtColor(f["img"], cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])
        index.append({"file": name, "jaw": round(f["jaw"], 4), "t": f["i"]})
    rest = min(kept, key=lambda f: f["jaw"] * 3 - f["sharp"] / cutoff * 0.05)
    json.dump({"frames": index, "rest": kept.index(rest), "size": SIZE}, open(AVATAR / "index.json", "w"))
    jaws = [f["jaw"] for f in kept]
    print(f"Saved {len(kept)} of {len(frames)} face frames; jawOpen {min(jaws):.2f}-{max(jaws):.2f}")
    print(f"Voice sample: {ASSETS / 'voice_ref.wav'}")


# Inner-lip landmark indices (MediaPipe face mesh), corner to corner.
UPPER_INNER = [78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308]
LOWER_INNER = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308]
PHOTO_LEVELS = 12


def build_photo():
    """A steady talking photo: one sharp, mouth-closed frame whose jaw is warped open at 12 levels."""
    idx = json.loads((AVATAR / "index.json").read_text())
    landmarker = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL)), output_face_blendshapes=True, num_faces=1))

    def analyse(img):
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        res = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
        if not res.face_landmarks:
            return None
        h, w = img.shape[:2]
        pts = np.array([(p.x * w, p.y * h) for p in res.face_landmarks[0]], np.float32)
        bs = {b.category_name: b.score for b in res.face_blendshapes[0]}
        return pts, bs

    # Pick the most "passport-like" frame: sharp, level, facing the camera, lips relaxed and closed.
    best, best_score = None, -1e9
    for f in idx["frames"]:
        img = cv2.imread(str(AVATAR / f["file"]))
        a = analyse(img)
        if a is None:
            continue
        pts, bs = a
        eye_l, eye_r = pts[33], pts[263]
        roll = abs(np.degrees(np.arctan2(eye_r[1] - eye_l[1], eye_r[0] - eye_l[0])))
        mid = (pts[234][0] + pts[454][0]) / 2
        yaw = abs(pts[1][0] - mid) / max(1, abs(pts[454][0] - pts[234][0]))
        sharp = cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
        score = (np.log1p(sharp) * 2 - roll * 0.15 - yaw * 25 - bs.get("jawOpen", 0) * 30
                 - bs.get("mouthPucker", 0) * 12 - bs.get("mouthFunnel", 0) * 12
                 - (bs.get("eyeBlinkLeft", 0) + bs.get("eyeBlinkRight", 0)) * 6)
        if score > best_score:
            best, best_score = img, score
    if best is None:
        sys.exit("No usable face frame found.")

    # Straighten the head so the eyes are level (the jaw then opens straight down).
    pts, _ = analyse(best)
    angle = np.degrees(np.arctan2(pts[263][1] - pts[33][1], pts[263][0] - pts[33][0]))
    centre = tuple(map(float, pts[[33, 263, 152]].mean(0)))
    rot = cv2.getRotationMatrix2D(centre, angle, 1.0)
    best = cv2.warpAffine(best, rot, (best.shape[1], best.shape[0]), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)
    pts, _ = analyse(best)
    h, w = best.shape[:2]

    cx = pts[13][0]
    mouth_y = (pts[13][1] + pts[14][1]) / 2
    mouth_w = abs(pts[308][0] - pts[78][0])
    chin_y = pts[152][1]
    face_h = chin_y - pts[10][1]
    max_open = 0.3 * mouth_w

    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    gx = np.exp(-((xs - cx) / (1.15 * mouth_w)) ** 2)
    ramp_top = np.clip((ys - (mouth_y - 1)) / 3.0, 0, 1)                          # lips and below move
    fade_neck = np.clip(1 - (ys - chin_y) / (0.28 * face_h), 0, 1)                # stretch fades into the neck
    field = gx * ramp_top * fade_neck

    out_dir = ASSETS / "avatar_photo"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()
    frames = []
    for k in range(PHOTO_LEVELS):
        o = k / (PHOTO_LEVELS - 1)
        dy = field * max_open * o
        warped = cv2.remap(best, xs, ys - dy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        if o > 0:
            upper = pts[UPPER_INNER]
            lower = pts[LOWER_INNER][::-1].copy()
            lower[:, 1] += max_open * o * np.exp(-((lower[:, 0] - cx) / (1.15 * mouth_w)) ** 2)
            poly = np.concatenate([upper, lower]).astype(np.int32)
            mask = np.zeros((h, w), np.float32)
            cv2.fillPoly(mask, [poly], 1.0, lineType=cv2.LINE_AA)
            mask = cv2.GaussianBlur(mask, (5, 5), 1.2)[..., None]
            # Mouth interior: dark red-brown, a little lighter towards the top (soft palate/teeth shadow).
            interior = np.zeros_like(warped, dtype=np.float32)
            depth = np.clip((ys - mouth_y) / max(1.0, max_open * o), 0, 1)[..., None]
            interior[:] = np.array([60, 50, 105], np.float32)                      # BGR
            interior = interior * (1 - 0.45 * depth)
            warped = (warped * (1 - mask) + interior * mask).astype(np.uint8)
        name = f"p{k:02d}.jpg"
        cv2.imwrite(str(out_dir / name), warped, [cv2.IMWRITE_JPEG_QUALITY, 92])
        frames.append({"file": name, "jaw": round(0.5 * o, 4), "t": 0})
    json.dump({"frames": frames, "rest": 0, "size": SIZE, "photo": True}, open(out_dir / "index.json", "w"))
    print(f"Talking photo: {PHOTO_LEVELS} mouth levels in {out_dir}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(Path(sys.argv[1]))
    build_photo()
