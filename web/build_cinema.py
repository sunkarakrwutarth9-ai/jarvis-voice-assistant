"""Builds dashboard_cinema.html from web/cinema_template.html, copying the shared creation-canvas
CSS and JavaScript out of dashboard_ios.html so both themes run the same canvas code.

    python web/build_cinema.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ios = (ROOT / "dashboard_ios.html").read_text(encoding="utf-8")
tpl = (ROOT / "web" / "cinema_template.html").read_text(encoding="utf-8")


def between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


css = between(ios, "/* ---------- creation canvas", ".offline{position:fixed;inset:0;display:none;")
js = between(ios, "// ================= creation canvas =================", "const post = (url")
out = tpl.replace("/*__CANVAS_CSS__*/", css).replace("/*__CANVAS_JS__*/", js)
(ROOT / "dashboard_cinema.html").write_text(out, encoding="utf-8")
print(f"built dashboard_cinema.html ({len(out) // 1024} KB; canvas css {len(css)} chars, js {len(js)} chars)")
