"""Search my files by meaning: "what did my notes say about thermodynamics?" - answers from the user's own files.

Reads text, Markdown, PDF and Word files in Documents, Desktop, Downloads (and OneDrive Documents) plus the
memory vault, splits them into passages and stores a meaning-vector for each (Gemini embeddings) in docindex/ on
this PC. A question is matched by meaning, not by file name, and answered from the best passages with the file
names cited. Indexing runs quietly in the background and only re-reads files that changed.
Files that look private (passwords, bank, ID, keys, tokens) are never read.
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.docsearch")
HERE = Path(__file__).resolve().parent
IDX = HERE / "docindex"
HOME = Path.home()
EXT = {".txt", ".md", ".pdf", ".docx", ".csv", ".html", ".htm", ".rtf"}
SKIP_DIRS = {"node_modules", ".git", "appdata", "venv", ".venv", "__pycache__", "site-packages", "$recycle.bin",
             ".obsidian", "docindex", "chats", "my games", "zoom"}
PRIVATE = re.compile(r"passw|bank|statement|aadha|aadhar|\bpan\b|passport|credential|secret|token|\.env|wallet|"
                     r"seed ?phrase|recovery|license key|salary|payslip|tax|itr|kyc|otp|pin\b", re.I)
MAX_FILE_MB, CHUNK, OVERLAP, MAX_CHUNKS_FILE, MAX_TOTAL = 30, 1200, 200, 60, 25000
MODEL, DIM = "gemini-embedding-001", 768
_lock = threading.Lock()
_state = {"busy": False, "done": 0, "total": 0, "last": 0.0}
_cache = {"vec": None, "meta": None, "mtime": 0}


def roots():
    out = [HOME / "Documents", HOME / "Desktop", HOME / "Downloads", HOME / "OneDrive" / "Documents",
           HOME / "OneDrive" / "Desktop", HERE / "vault", HERE / "study"]
    for extra in os.environ.get("JARVIS_DOC_FOLDERS", "").split(";"):
        if extra.strip():
            out.append(Path(extra.strip()))
    return [r for r in out if r.exists()]


def _client():
    from openai import OpenAI
    return OpenAI(base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                  api_key=os.environ.get("GEMINI_API_KEY", ""), max_retries=2, timeout=60)


def _embed(texts):
    out = []
    c = _client()
    for i in range(0, len(texts), 100):
        for attempt in range(4):
            try:
                r = c.embeddings.create(model=MODEL, input=[t[:6000] for t in texts[i:i + 100]], dimensions=DIM)
                out.extend(d.embedding for d in r.data)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                log.info("embedding retry (%s)", str(e)[:80])
                time.sleep(4 * (attempt + 1))
        time.sleep(0.6)                                   # stay under the free-tier rate limit
    v = np.asarray(out, dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    return v


def _read(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        r = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in r.pages[:80])
    if ext == ".docx":
        import docx
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    text = path.read_text(encoding="utf-8", errors="ignore")
    if ext in (".html", ".htm"):
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
    if ext == ".rtf":
        text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", " ", text)
    return text


def _chunks(text):
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
    out, i = [], 0
    while i < len(text) and len(out) < MAX_CHUNKS_FILE:
        out.append(text[i:i + CHUNK])
        i += CHUNK - OVERLAP
    return [c for c in out if len(c.strip()) > 40]


def _files():
    seen = 0
    for root in roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for f in filenames:
                p = Path(dirpath) / f
                if p.suffix.lower() not in EXT or PRIVATE.search(f):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > MAX_FILE_MB * 1e6 or st.st_size < 30:
                    continue
                seen += 1
                if seen > 6000:
                    return
                yield p, st.st_mtime


def _load():
    try:
        meta = json.loads((IDX / "meta.json").read_text(encoding="utf-8"))
        vec = np.load(IDX / "vectors.npy")
        return meta, vec
    except (OSError, ValueError):
        return {"files": {}, "chunks": []}, np.zeros((0, DIM), dtype=np.float32)


def build(publish=None):
    """(Re)index: only new / changed files are read and embedded."""
    if not os.environ.get("GEMINI_API_KEY"):
        return "FAILED: file search needs the Gemini key."
    with _lock:
        if _state["busy"]:
            return "OK: already indexing."
        _state["busy"] = True
    try:
        IDX.mkdir(exist_ok=True)
        meta, vec = _load()
        old_files, old_chunks = meta["files"], meta["chunks"]
        current = {str(p): m for p, m in _files()}
        keep_idx, new_meta_chunks, todo = [], [], []
        for i, ch in enumerate(old_chunks):
            f = ch["f"]
            if f in current and abs(old_files.get(f, 0) - current[f]) < 1:
                keep_idx.append(i)
                new_meta_chunks.append(ch)
        # A file is (re)read when it is new or its modified-time changed; files read before with no text are skipped.
        changed = [f for f in current if abs(old_files.get(f, -1) - current[f]) >= 1]
        done_files = {f: current[f] for f in current if f not in changed}
        _state.update(done=0, total=len(changed))
        texts, chunk_meta = [], []
        for n, f in enumerate(changed):
            done_files[f] = current[f]
            try:
                parts = _chunks(_read(Path(f)))
            except Exception as e:
                log.info("skip %s: %s", f, str(e)[:80])
                parts = []
            for j, c in enumerate(parts):
                texts.append(f"{Path(f).name}\n{c}")
                chunk_meta.append({"f": f, "i": j, "t": c})
            _state["done"] = n + 1
            if len(new_meta_chunks) + len(chunk_meta) > MAX_TOTAL:
                break
        new_vec = _embed(texts) if texts else np.zeros((0, DIM), dtype=np.float32)
        vec = np.vstack([vec[keep_idx] if len(vec) else np.zeros((0, DIM), dtype=np.float32), new_vec]).astype(np.float16)
        meta = {"files": done_files, "chunks": new_meta_chunks + chunk_meta,
                "built": time.time(), "roots": [str(r) for r in roots()]}
        np.save(IDX / "vectors.npy", vec)
        (IDX / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _cache["mtime"] = 0
        _state["last"] = time.time()
        msg = (f"OK: indexed {len(current)} files ({len(meta['chunks'])} passages); "
               f"{len(changed)} new or changed read just now.")
        log.info(msg)
        if publish:
            publish({"type": "docindex", "files": len(current), "passages": len(meta["chunks"])})
        return msg
    finally:
        _state["busy"] = False


def _index():
    p = IDX / "meta.json"
    if not p.exists():
        return None, None
    m = p.stat().st_mtime
    if _cache["mtime"] != m:
        meta, vec = _load()
        _cache.update(meta=meta, vec=vec.astype(np.float32), mtime=m)
    return _cache["meta"], _cache["vec"]


def search(question, k=6):
    meta, vec = _index()
    if meta is None or not len(meta["chunks"]):
        return []
    q = _embed([question])[0]
    sims = vec @ q
    # a little keyword boost: exact words from the question that appear in the passage / file name
    words = [w for w in re.findall(r"[\w]{4,}", question.lower())][:8]
    order = np.argsort(-sims)[:200]
    scored = []
    for i in order:
        ch = meta["chunks"][int(i)]
        hay = (Path(ch["f"]).name + " " + ch["t"]).lower()
        scored.append((float(sims[i]) + 0.03 * sum(w in hay for w in words), int(i)))
    scored.sort(reverse=True)
    out, per_file = [], {}
    for s, i in scored:
        ch = meta["chunks"][i]
        if per_file.get(ch["f"], 0) >= 2:
            continue
        per_file[ch["f"]] = per_file.get(ch["f"], 0) + 1
        out.append({"file": ch["f"], "text": ch["t"], "score": round(s, 3)})
        if len(out) >= k:
            break
    return out


def search_my_files(question: str) -> str:
    """Tool: answer a question from the user's own files."""
    import tools
    meta, _v = _index()
    if meta is None:
        threading.Thread(target=build, args=(tools.publish,), daemon=True).start()
        return ("OK: I haven't read your files yet - I've started now (Documents, Desktop, Downloads, notes). "
                "It takes a few minutes the first time; ask me again shortly.")
    hits = search(question)
    if not hits or hits[0]["score"] < 0.45:
        return "OK: I couldn't find anything about that in your files."
    hits = [h for h in hits if h["score"] >= hits[0]["score"] - 0.06]      # only the clearly relevant ones
    ctxt = "\n\n".join(f"[{n + 1}] {Path(h['file']).name}\n{h['text']}" for n, h in enumerate(hits))
    answer = tools.generate_text(
        "Answer the user's question from these passages of their own files. Cite like [1]. You may connect "
        "obvious ideas (e.g. entropy = disorder), but don't add facts that aren't there; if the passages don't "
        f"answer it, say so. Be concise (max 120 words).\n\nQuestion: {question}\n\n{ctxt}", 500) or ""
    md = (f"# 🔎 {question}\n\n{answer}\n\n## Sources\n" +
          "\n".join(f"{n + 1}. `{h['file']}`" for n, h in enumerate(hits)))
    try:
        tools.show_content("document", f"Files: {question[:40]}", "md", md)
    except Exception:
        pass
    files = ", ".join(dict.fromkeys(Path(h["file"]).name for h in hits[:3]))
    return f"OK: {answer}\n(Sources: {files}. Shown on the Ultron Screen - say 'open file' to open one.)"


def index_my_files(folder: str = "") -> str:
    """Tool: (re)read the user's files now; optionally add a folder to the search."""
    import tools
    if folder:
        p = Path(os.path.expandvars(os.path.expanduser(folder)))
        if not p.exists():
            return f"FAILED: folder not found: {folder}"
        cur = os.environ.get("JARVIS_DOC_FOLDERS", "")
        if str(p) not in cur.split(";"):
            os.environ["JARVIS_DOC_FOLDERS"] = (cur + ";" if cur else "") + str(p)
            try:
                from dotenv import set_key
                set_key(str(HERE / ".env"), "JARVIS_DOC_FOLDERS", os.environ["JARVIS_DOC_FOLDERS"])
            except Exception:
                pass
    if _state["busy"]:
        return f"OK: already reading your files ({_state['done']}/{_state['total']})."
    threading.Thread(target=build, args=(tools.publish,), daemon=True, name="docindex").start()
    return "OK: reading your files in the background: " + ", ".join(str(r) for r in roots())


def background():
    """Quietly keep the index fresh: shortly after start, then every 6 hours."""
    import tools

    def loop():
        time.sleep(150)
        while True:
            try:
                build(tools.publish)
            except Exception:
                log.exception("indexing failed")
            time.sleep(6 * 3600)
    threading.Thread(target=loop, daemon=True, name="docindex-bg").start()
