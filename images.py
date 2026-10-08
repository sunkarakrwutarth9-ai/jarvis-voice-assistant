"""Real photos for slides and creations - from Wikimedia Commons (free licences, credited), so lessons,
presentations and web pages show real pictures instead of placeholders. No API key needed.
"""

import json
import logging
import re
import urllib.parse
import urllib.request

log = logging.getLogger("jarvis.images")
UA = {"User-Agent": "UltronAssistant/1.0 (personal desktop assistant; contact: local user)"}


def find(query: str, n: int = 1, width: int = 1600):
    """[{url, title, credit, page}] for real photos matching the query (best first)."""
    q = re.sub(r"\s+", " ", (query or "").strip())[:120]
    if not q:
        return []
    params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": "6",
              "gsrsearch": f"{q} filetype:bitmap", "gsrlimit": str(max(n * 3, 6)), "prop": "imageinfo",
              "iiprop": "url|extmetadata|size", "iiurlwidth": str(width)}
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8) as r:
            pages = json.loads(r.read()).get("query", {}).get("pages", {})
    except Exception as e:
        log.info("image search failed for %r: %s", q, e)
        return []
    out = []
    for pg in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        info = (pg.get("imageinfo") or [{}])[0]
        if not info.get("thumburl") or info.get("width", 0) < 500:
            continue
        meta = info.get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()[:60]
        lic = meta.get("LicenseShortName", {}).get("value", "")
        out.append({"url": info["thumburl"], "title": pg.get("title", "").replace("File:", "")[:80],
                    "credit": " · ".join(x for x in (artist, lic, "Wikimedia Commons") if x),
                    "page": info.get("descriptionurl", "")})
        if len(out) >= n:
            break
    return out


def for_prompt(topic: str, n: int = 6) -> str:
    """A block for a generation prompt listing real images it may use."""
    imgs = find(topic, n)
    if not imgs:
        return ""
    lines = "\n".join(f"- {i['url']}  (\"{i['title']}\", credit: {i['credit']})" for i in imgs)
    return ("\n\nREAL PHOTOS you can use (hot-linkable, free licences) - use several of them with <img src=...> or CSS "
            "background-image to make it look real and professional; add a small credit line ('Photos: Wikimedia "
            f"Commons') in the footer. Never invent image URLs; use only these:\n{lines}\n")
