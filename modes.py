"""Business modes: one switch turns Ultron into a Hospital, Supermarket, Restaurant, Pharmacy, School, Hotel,
Office or Factory assistant - its own persona, records, actions, console (/business) and theme.

Everything is stored locally in business/<mode>.db (SQLite, never uploaded, never committed). Each mode defines
its collections (tables) and fields; the same engine gives every mode:
  records       add / find / list / update / delete / demo data          (any collection)
  make_bill     priced items -> GST -> stock goes down -> receipt          (catalog modes)
  urgent_alert  staff / doctor alert with escalation until acknowledged   (any mode, built for hospitals)
  prescription  voice-drafted Rx -> read-back -> doctor confirms -> sent   (hospital, pharmacy)
  report        today / week / month: sales, top items, counts
  alerts        low stock, expiring medicines, rooms to clean, machines down...
  import        CSV / Excel -> records
"""
import datetime
import difflib
import html
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("jarvis.modes")
DIR = Path(__file__).resolve().parent / "business"
_lock = threading.RLock()
hooks = {"publish": lambda e: None, "say": lambda t: None, "theme": lambda tid: None, "telegram": None,
         "state": lambda k, d=None: d, "save": lambda k, v: None, "show": None, "confirmed": lambda: False}

F = lambda *names: list(names)                       # field list helper
MODES = {
    "personal": {"name": "Personal", "icon": "🧠", "theme": "ironman-13", "who": "the user's personal assistant",
                 "collections": {}},
    "hospital": {
        "name": "Hospital", "icon": "🏥", "theme": "industry-1", "who": "the hospital's operations and clinical-support assistant",
        "collections": {
            "patients": F("name", "age", "gender", "phone", "ward", "bed", "condition", "doctor", "status"),
            "doctors": F("name", "specialty", "phone", "reg_no", "on_call", "telegram_chat"),
            "beds": F("ward", "bed", "status", "patient"),
            "prescriptions": F("patient", "doctor", "reg_no", "medicines", "advice", "status", "pharmacy"),
            "alerts": F("message", "priority", "to", "status", "raised", "acked_by", "acked_at"),
        },
        "rules": "Urgent cases: call urgent_alert at once (it alerts the on-call doctor and escalates by itself). "
                 "Prescriptions: prescription(action='draft') -> read it back -> only after the DOCTOR clearly "
                 "confirms call action='confirm', then 'send'. You never diagnose or decide treatment - the doctor does.",
    },
    "supermarket": {
        "name": "Supermarket", "icon": "🛒", "theme": "industry-2", "who": "the supermarket's billing, stock and customer assistant",
        "catalog": "products", "sales": "sales",
        "collections": {
            "products": F("name", "category", "price", "stock", "min_stock", "aisle", "barcode", "gst"),
            "sales": F("bill_no", "items", "subtotal", "gst", "total", "customer", "payment", "time"),
            "customers": F("name", "phone", "points", "visits"),
            "suppliers": F("name", "phone", "supplies", "lead_days"),
        },
        "rules": "Billing: make_bill with the item names and quantities. Prices and stock come ONLY from the records "
                 "(records find) - never guess a price. 'Where is X' = the product's aisle.",
    },
    "restaurant": {
        "name": "Restaurant", "icon": "🍽", "theme": "industry-3", "who": "the restaurant's orders, kitchen and billing assistant",
        "catalog": "menu", "sales": "sales",
        "collections": {
            "menu": F("name", "category", "price", "veg", "available", "gst"),
            "tables": F("number", "seats", "status"),
            "orders": F("table", "items", "status", "time"),
            "reservations": F("name", "phone", "time", "people", "status"),
            "sales": F("bill_no", "items", "subtotal", "gst", "total", "customer", "payment", "time"),
        },
        "rules": "A new order: records add to 'orders' (table, items, status 'new') - the kitchen screen shows it. "
                 "The bill: make_bill. Menu prices only from records.",
    },
    "pharmacy": {
        "name": "Pharmacy", "icon": "💊", "theme": "industry-4", "who": "the medical store's stock, billing and prescription assistant",
        "catalog": "medicines", "sales": "sales",
        "collections": {
            "medicines": F("name", "salt", "price", "stock", "min_stock", "expiry", "batch", "rack", "schedule", "gst"),
            "prescriptions": F("patient", "doctor", "reg_no", "medicines", "advice", "status", "pharmacy"),
            "sales": F("bill_no", "items", "subtotal", "gst", "total", "customer", "payment", "time"),
            "suppliers": F("name", "phone", "supplies", "lead_days"),
        },
        "rules": "Schedule H / H1 medicines need a valid prescription before billing - ask for it. Never suggest "
                 "doses or substitutes as medical advice; refer to the pharmacist/doctor.",
    },
    "school": {
        "name": "School", "icon": "🏫", "theme": "industry-5", "who": "the school's attendance, marks and timetable assistant",
        "collections": {
            "students": F("name", "class", "roll", "parent_name", "parent_phone"),
            "attendance": F("date", "class", "student", "status"),
            "marks": F("student", "class", "subject", "exam", "marks", "max"),
            "timetable": F("class", "day", "period", "subject", "teacher"),
            "homework": F("class", "subject", "task", "due"),
        },
        "rules": "Attendance: one 'attendance' record per absent/present student. Report cards: business_report.",
    },
    "hotel": {
        "name": "Hotel", "icon": "🏨", "theme": "industry-6", "who": "the hotel's front desk, rooms and housekeeping assistant",
        "catalog": "services", "sales": "sales",
        "collections": {
            "rooms": F("number", "type", "price", "status"),
            "bookings": F("guest", "phone", "room", "check_in", "check_out", "status"),
            "services": F("name", "price", "gst"),
            "housekeeping": F("room", "task", "status"),
            "sales": F("bill_no", "items", "subtotal", "gst", "total", "customer", "payment", "time"),
        },
        "rules": "Check-in: update the room status to 'occupied' and add a booking. Check-out: room -> 'cleaning', "
                 "add a housekeeping task, make_bill for services.",
    },
    "office": {
        "name": "Office", "icon": "🏢", "theme": "industry-7", "who": "the company's office manager assistant",
        "collections": {
            "employees": F("name", "role", "team", "phone", "email"),
            "tasks": F("title", "owner", "due", "priority", "status"),
            "visitors": F("name", "purpose", "host", "time_in", "time_out"),
            "leaves": F("employee", "from", "to", "reason", "status"),
            "meetings": F("title", "time", "people", "room"),
        },
        "rules": "Calendar meetings can also go to Google Calendar (calendar_add) when connected.",
    },
    "factory": {
        "name": "Factory", "icon": "🏭", "theme": "industry-8", "who": "the factory's production, inventory and maintenance assistant",
        "collections": {
            "machines": F("name", "line", "status", "last_service"),
            "production": F("date", "line", "product", "qty", "defects"),
            "inventory": F("material", "stock", "min_stock", "unit", "location"),
            "maintenance": F("machine", "issue", "priority", "status"),
        },
        "rules": "A machine breakdown: update the machine to 'down', add a maintenance record, and urgent_alert the "
                 "maintenance team.",
    },
}
ALIASES = {
    "pharmacy": "pharmacy medical chemist medicine drug", "hospital": "hospital clinic doctor nursing icu",
    "supermarket": "supermarket kirana store shop mart grocery retail", "restaurant": "restaurant cafe food dhaba canteen kitchen",
    "school": "school college coaching tuition class academy", "hotel": "hotel lodge resort guest house rooms",
    "office": "office company business startup corporate", "factory": "factory warehouse manufacturing plant industry",
    "personal": "personal normal home default me jarvis ultron",
}
CONTROLLED = re.compile(r"\b(morphine|fentanyl|pethidine|tramadol|buprenorphine|methadone|codeine|oxycodone|"
                        r"alprazolam|diazepam|lorazepam|clonazepam|nitrazepam|zolpidem|methylphenidate|ketamine|"
                        r"phenobarbit\w*)\b", re.I)


# ------------------------------------------------------------------ state
def current():
    m = hooks["state"]("biz_mode", "personal")
    return m if m in MODES else "personal"


def spec(mode=None):
    return MODES[mode or current()]


def match_mode(name):
    q = (name or "").lower().strip()
    if q in MODES:
        return q
    for m in MODES:                                  # the mode's own name wins ("hotel" -> hotel, not restaurant)
        if re.search(rf"\b{m}\b", q):
            return m
    for m, words in ALIASES.items():                 # pharmacy is checked before hospital ("medical store")
        if any(re.search(rf"\b{w}", q) for w in words.split()):
            return m
    best = difflib.get_close_matches(q, list(MODES), n=1, cutoff=0.5)
    return best[0] if best else None


def mode_prompt():
    m = current()
    if m == "personal":
        return ""
    s = spec(m)
    cols = "; ".join(f"{c}({', '.join(f)})" for c, f in s["collections"].items())
    return (f"BUSINESS MODE: {s['name'].upper()} {s['icon']}. You are now {s['who']} (still Ultron, still polite, "
            f"concise and fast). Work with the records in this mode - collections: {cols}. Use the tools records, "
            f"make_bill, urgent_alert, prescription, business_report, business_alerts, import_records. Never invent "
            f"names, prices, stock or patient details - look them up with records(action='find'). {s.get('rules', '')} "
            f"The console is at http://localhost:7777/business (open_business).")


# ------------------------------------------------------------------ storage
def _db(mode=None):
    DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DIR / f"{mode or current()}.db", timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS rec (id INTEGER PRIMARY KEY AUTOINCREMENT, col TEXT, data TEXT, "
                "created REAL, updated REAL)")
    con.execute("CREATE INDEX IF NOT EXISTS rec_col ON rec(col)")
    return con


def _rows(col, mode=None, limit=500):
    with _lock, _db(mode) as con:
        rs = con.execute("SELECT * FROM rec WHERE col=? ORDER BY id DESC LIMIT ?", (col, limit)).fetchall()
    return [dict(json.loads(r["data"]), id=r["id"], _t=r["updated"]) for r in rs]


def _clean(col, data):
    fields = spec()["collections"].get(col, [])
    out = {}
    for k, v in (data or {}).items():
        k2 = str(k).strip().lower().replace(" ", "_")
        if k2 in ("id", "_t"):
            continue
        k2 = difflib.get_close_matches(k2, fields, n=1, cutoff=0.75)[0] if fields and k2 not in fields and \
            difflib.get_close_matches(k2, fields, n=1, cutoff=0.75) else k2
        out[k2] = v
    for num in ("price", "stock", "min_stock", "gst", "qty", "defects", "marks", "max", "points", "seats", "age",
                "total", "subtotal", "lead_days"):
        if num in out and isinstance(out[num], str):
            try:
                out[num] = float(re.sub(r"[^\d.\-]", "", out[num]) or 0)
            except ValueError:
                pass
    return out


def _add(col, data, mode=None):
    now = time.time()
    with _lock, _db(mode) as con:
        cur = con.execute("INSERT INTO rec(col,data,created,updated) VALUES(?,?,?,?)",
                          (col, json.dumps(data, ensure_ascii=False), now, now))
        return cur.lastrowid


def _update(rid, data):
    with _lock, _db() as con:
        r = con.execute("SELECT data FROM rec WHERE id=?", (rid,)).fetchone()
        if not r:
            return None
        d = dict(json.loads(r["data"]), **data)
        con.execute("UPDATE rec SET data=?, updated=? WHERE id=?", (json.dumps(d, ensure_ascii=False), time.time(), rid))
        return d


def _delete(rid):
    with _lock, _db() as con:
        return con.execute("DELETE FROM rec WHERE id=?", (rid,)).rowcount


def _find(col, query, limit=20):
    words = [w for w in re.findall(r"[\w.]+", (query or "").lower()) if w]
    rows = _rows(col)
    if not words:
        return rows[:limit]
    scored = []
    for r in rows:
        hay = " ".join(str(v) for k, v in r.items() if k != "_t").lower()
        hit = sum(w in hay for w in words)
        if hit:
            scored.append((hit, r))
    if not scored:                                   # fuzzy fallback on the main name field
        names = {str(r.get("name") or r.get("patient") or r.get("title") or r.get("material") or ""): r for r in rows}
        for n in difflib.get_close_matches(query, list(names), n=5, cutoff=0.55):
            scored.append((1, names[n]))
    scored.sort(key=lambda x: -x[0])
    return [r for _s, r in scored[:limit]]


def _changed(col=None):
    hooks["publish"]({"type": "biz_update", "mode": current(), "col": col})


def _short(r):
    return ", ".join(f"{k}: {v}" for k, v in r.items() if k not in ("_t",) and v not in ("", None))[:300]


# ------------------------------------------------------------------ tools
def business_mode(name: str) -> str:
    """Switch Ultron into a business mode (or back to personal)."""
    m = match_mode(name)
    if not m:
        return "FAILED: modes: " + ", ".join(f"{s['icon']} {s['name']}" for s in MODES.values())
    before = current()
    hooks["save"]("biz_mode", m)
    s = spec(m)
    if before == "personal" and m != "personal":    # remember the user's own look, to bring it back later
        hooks["save"]("theme_before_mode", hooks["state"]("ui_theme", "") or "")
    theme = (hooks["state"]("theme_before_mode", "") or s["theme"]) if m == "personal" else s["theme"]
    try:
        hooks["theme"](theme)
    except Exception:
        log.exception("mode theme failed")
    hooks["publish"]({"type": "biz_mode", "mode": m, "name": s["name"], "icon": s["icon"]})
    if m == "personal":
        return "OK: back to personal mode."
    try:                                             # the console opens by itself (an open one just switches)
        if hooks.get("open") and not hooks.get("is_open", lambda: False)():   # (a business- window)
            hooks["open"]()
    except Exception:
        log.exception("opening the business console failed")
    counts = {c: len(_rows(c, m, 100000)) for c in s["collections"]}
    empty = sum(counts.values()) == 0
    return (f"OK: {s['icon']} {s['name']} mode on - I'm now {s['who']}. Theme changed to match. Records: "
            + ", ".join(f"{c} {n}" for c, n in counts.items())
            + (". It's empty - offer to load demo data (records action 'demo') or import their Excel/CSV." if empty else ".")
            + " The console is at localhost:7777/business.")


def records(action: str = "list", collection: str = "", data: dict = None, query: str = "", id: int = 0) -> str:
    m = current()
    if m == "personal":
        return "FAILED: switch to a business mode first (business_mode)."
    s = spec(m)
    if action == "demo":
        return load_demo()
    col = collection.lower().strip() if collection else ""
    if col and col not in s["collections"]:
        col = (difflib.get_close_matches(col, list(s["collections"]), n=1, cutoff=0.5) or [""])[0]
    if not col:
        return "FAILED: collection must be one of: " + ", ".join(s["collections"])
    if action == "add":
        d = _clean(col, data or {})
        if not d:
            return "FAILED: nothing to add."
        rid = _add(col, d)
        _changed(col)
        return f"OK: added to {col} (#{rid}): {_short(d)}"
    if action in ("find", "list"):
        rows = _find(col, query) if action == "find" else _rows(col)[:25]
        if not rows:
            return f"OK: nothing in {col}" + (f" matching '{query}'." if query else ".")
        return f"OK: {len(rows)} in {col}: " + " | ".join(f"#{r['id']} {_short({k: v for k, v in r.items() if k != 'id'})}" for r in rows[:15])
    if action in ("update", "delete"):
        target = [r for r in _rows(col) if r["id"] == int(id)] if id else _find(col, query, 3)
        if not target:
            return f"FAILED: no {col} record matches."
        if len(target) > 1 and not id:
            return f"FAILED: several match - say which: " + " | ".join(f"#{r['id']} {_short(r)}" for r in target)
        r = target[0]
        if action == "delete":
            _delete(r["id"])
            _changed(col)
            return f"OK: deleted {col} #{r['id']}."
        d = _update(r["id"], _clean(col, data or {}))
        _changed(col)
        return f"OK: updated {col} #{r['id']}: {_short(d)}"
    if action == "count":
        return f"OK: {len(_rows(col, limit=100000))} records in {col}."
    return "FAILED: action must be add, find, list, update, delete, count or demo."


def _catalog_item(name):
    s = spec()
    cat = s.get("catalog")
    rows = _rows(cat, limit=100000)
    names = {str(r.get("name", "")).lower(): r for r in rows}
    n = name.lower().strip()
    if n in names:
        return names[n]
    hits = [r for k, r in names.items() if n in k or k in n]
    if len(hits) == 1:
        return hits[0]
    best = difflib.get_close_matches(n, list(names), n=1, cutoff=0.6)
    return names[best[0]] if best else (hits[0] if hits else None)


def make_bill(items: list, customer: str = "", payment: str = "cash", discount: float = 0) -> str:
    s = spec()
    if not s.get("catalog"):
        return f"FAILED: {s['name']} mode has no billing - switch to Supermarket, Restaurant, Pharmacy or Hotel."
    lines, missing, short = [], [], []
    for it in items or []:
        if isinstance(it, str):
            m = re.match(r"\s*(\d+(?:\.\d+)?)\s*[x×]?\s*(.+)", it)
            it = {"name": m.group(2), "qty": float(m.group(1))} if m else {"name": it, "qty": 1}
        row = _catalog_item(str(it.get("name", "")))
        qty = float(it.get("qty") or 1)
        if not row:
            missing.append(str(it.get("name")))
            continue
        if current() == "pharmacy" and re.search(r"\bh1?\b|schedule ?h", str(row.get("schedule", "")), re.I) \
                and not it.get("prescription_seen"):
            short.append(f"{row['name']} is Schedule {row.get('schedule')} - needs a prescription (ask, then bill with prescription_seen)")
            continue
        if "stock" in row and isinstance(row.get("stock"), (int, float)) and row["stock"] < qty:
            short.append(f"{row['name']} only {row['stock']:g} in stock")
            continue
        price = float(row.get("price") or 0)
        gst = float(row.get("gst") or 0)
        lines.append({"id": row["id"], "name": row["name"], "qty": qty, "price": price, "gst": gst,
                      "amount": round(price * qty, 2), "tax": round(price * qty * gst / 100, 2)})
    if not lines:
        return "FAILED: nothing billed. " + (f"Not found: {', '.join(missing)}. " if missing else "") + "; ".join(short)
    sub = round(sum(l["amount"] for l in lines), 2)
    tax = round(sum(l["tax"] for l in lines), 2)
    total = round(sub + tax - float(discount or 0), 2)
    bill_no = f"B{datetime.datetime.now():%y%m%d}-{len(_rows(s['sales'], limit=100000)) + 1:04d}"
    for l in lines:                                      # stock goes down
        row = next((r for r in _rows(s["catalog"], limit=100000) if r["id"] == l["id"]), None)
        if row and isinstance(row.get("stock"), (int, float)):
            _update(l["id"], {"stock": row["stock"] - l["qty"]})
    sale = {"bill_no": bill_no, "items": [{k: l[k] for k in ("name", "qty", "price", "gst", "amount")} for l in lines],
            "subtotal": sub, "gst": tax, "discount": float(discount or 0), "total": total, "customer": customer,
            "payment": payment, "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
    _add(s["sales"], sale)
    if customer and "customers" in s["collections"]:
        c = _find("customers", customer, 1)
        if c:
            _update(c[0]["id"], {"points": float(c[0].get("points") or 0) + total // 100, "visits": float(c[0].get("visits") or 0) + 1})
    _show(f"Bill {bill_no}", receipt_html(sale))
    _changed(s["sales"])
    note = (f" Not found: {', '.join(missing)}." if missing else "") + (f" Skipped: {'; '.join(short)}." if short else "")
    low = [a for a in check_alerts() if a["kind"] == "stock"][:3]
    return (f"OK: bill {bill_no} - {len(lines)} items, subtotal ₹{sub:,.2f}, GST ₹{tax:,.2f}, TOTAL ₹{total:,.2f} "
            f"({payment}). Receipt is on the screen.{note}" + (" Low stock now: " + "; ".join(a['text'] for a in low) if low else ""))


def receipt_html(sale):
    s = spec()
    rows = "".join(f"<tr><td>{html.escape(str(i['name']))}</td><td>{i['qty']:g}</td><td>₹{i['price']:,.2f}</td>"
                   f"<td>{i['gst']:g}%</td><td>₹{i['amount']:,.2f}</td></tr>" for i in sale["items"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{font:14px 'Segoe UI',system-ui;background:#f4f6f8;margin:0;padding:24px;color:#111}}
.r{{max-width:420px;margin:auto;background:#fff;border-radius:14px;padding:22px;box-shadow:0 10px 40px rgba(0,0,0,.12)}}
h1{{font-size:18px;margin:0;text-align:center}} .s{{text-align:center;color:#666;font-size:12px;margin:4px 0 14px}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:6px 4px;text-align:right;border-bottom:1px dashed #ddd}}
th:first-child,td:first-child{{text-align:left}} .t td{{border:0;font-weight:700;font-size:16px}}
@media print{{body{{background:#fff}} .r{{box-shadow:none}}}}</style></head><body><div class="r">
<h1>{s['icon']} {html.escape(hooks['state']('biz_name', s['name']) or s['name'])}</h1>
<div class="s">Bill {sale['bill_no']} · {sale['time']} · {html.escape(sale.get('payment', ''))}{(' · ' + html.escape(sale['customer'])) if sale.get('customer') else ''}</div>
<table><tr><th>Item</th><th>Qty</th><th>Rate</th><th>GST</th><th>Amount</th></tr>{rows}
<tr><td colspan="4">Subtotal</td><td>₹{sale['subtotal']:,.2f}</td></tr><tr><td colspan="4">GST</td><td>₹{sale['gst']:,.2f}</td></tr>
{f"<tr><td colspan='4'>Discount</td><td>-₹{sale['discount']:,.2f}</td></tr>" if sale.get('discount') else ''}
<tr class="t"><td colspan="4">TOTAL</td><td>₹{sale['total']:,.2f}</td></tr></table>
<p class="s">Thank you! · Powered by ULTRON</p></div></body></html>"""


def _show(title, page):
    if hooks["show"]:
        try:
            hooks["show"]("webpage", title, "html", page)
        except Exception:
            log.exception("show failed")


# ------------------------------------------------------------------ urgent alerts (with escalation)
_escalations = {}


def urgent_alert(message: str, priority: str = "high", to: str = "") -> str:
    m = current()
    people = []
    if "doctors" in spec()["collections"]:
        docs = _rows("doctors", limit=1000)
        named = _find("doctors", to, 3) if to else []
        oncall = [d for d in docs if str(d.get("on_call", "")).lower() in ("yes", "y", "true", "1", "on")]
        people = named or oncall or docs
    who = [str(p.get("name")) for p in people][:4] or [to or "the on-duty team"]
    raised = datetime.datetime.now().strftime("%H:%M:%S")
    rec = {"message": message, "priority": priority, "to": who[0], "status": "open", "raised": raised, "chain": who}
    col = "alerts" if "alerts" in spec()["collections"] else None
    rid = _add(col or "alerts", rec)
    _announce(rid, rec, 0)
    _escalations[rid] = 0
    threading.Thread(target=_escalate, args=(rid, m), daemon=True, name=f"alert-{rid}").start()
    return (f"OK: URGENT alert #{rid} raised for {who[0]} ({priority}): '{message}'. It is flashing on every Ultron "
            f"screen{', sent to Telegram' if hooks['telegram'] else ''}, and escalates to "
            f"{' → '.join(who[1:]) or 'a repeat alert'} every 2 minutes until someone presses Acknowledge.")


def _announce(rid, rec, level):
    target = rec["chain"][min(level, len(rec["chain"]) - 1)]
    hooks["publish"]({"type": "biz_alert", "id": rid, "message": rec["message"], "priority": rec["priority"],
                      "to": target, "level": level, "raised": rec["raised"]})
    hooks["say"](f"{'Escalating. ' if level else ''}Urgent alert for {target}: {rec['message']}")
    if hooks["telegram"]:
        try:
            hooks["telegram"](f"🚨 URGENT{' (escalation ' + str(level) + ')' if level else ''} → {target}\n{rec['message']}\n"
                              f"Priority: {rec['priority']} · raised {rec['raised']}\nReply 'ack {rid}' or press Acknowledge in Ultron.")
        except Exception:
            log.exception("telegram alert failed")


def _escalate(rid, mode):
    for level in range(1, 6):
        time.sleep(120)
        rows = [r for r in _rows("alerts", mode, 1000) if r["id"] == rid]
        if not rows or rows[0].get("status") != "open":
            return
        _announce(rid, rows[0], level)


def ack_alert(rid, by="staff"):
    if not any(r["id"] == int(rid) for r in _rows("alerts", limit=1000)):
        return None
    d = _update(int(rid), {"status": "acknowledged", "acked_by": by, "acked_at": datetime.datetime.now().strftime("%H:%M:%S")})
    _escalations.pop(int(rid), None)
    hooks["publish"]({"type": "biz_alert_ack", "id": int(rid)})
    _changed("alerts")
    return d


# ------------------------------------------------------------------ prescriptions (doctor always confirms)
def prescription(action: str = "draft", patient: str = "", medicines: str = "", doctor: str = "", advice: str = "",
                 rx_id: int = 0, pharmacy: str = "") -> str:
    s = spec()
    if "prescriptions" not in s["collections"]:
        return "FAILED: prescriptions work in Hospital or Pharmacy mode."
    if action == "draft":
        if not (patient and medicines):
            return "FAILED: need the patient and the medicines (name, strength, dose, how many days)."
        if CONTROLLED.search(medicines):
            return ("FAILED: that includes a controlled (NDPS / Schedule X-type) medicine - it cannot be prescribed "
                    "through this digital channel. The doctor must write a physical prescription.")
        doc = (_find("doctors", doctor, 1) if doctor and "doctors" in s["collections"] else []) or [{}]
        rx = {"patient": patient, "doctor": doctor or doc[0].get("name", ""), "reg_no": doc[0].get("reg_no", ""),
              "medicines": medicines, "advice": advice, "status": "draft", "pharmacy": pharmacy,
              "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
        rid = _add("prescriptions", rx)
        _show(f"Rx #{rid} (draft)", rx_html(dict(rx, id=rid)))
        _changed("prescriptions")
        return (f"OK: prescription #{rid} DRAFTED (not final). Read it back to the doctor word for word: patient "
                f"{patient}; {medicines}; advice: {advice or 'none'}. Ask 'Shall I confirm and send it, doctor?' - only "
                f"if they clearly say yes/confirm, call prescription(action='confirm', rx_id={rid}).")
    rows = [r for r in _rows("prescriptions", limit=1000) if r["id"] == int(rx_id or 0)] or \
        [r for r in _rows("prescriptions", limit=50) if r.get("status") in ("draft", "confirmed")][:1]
    if not rows:
        return "FAILED: no such prescription."
    rx = rows[0]
    if action == "confirm":
        if not hooks["confirmed"]():
            return "FAILED: the doctor has not clearly confirmed. Read it back and ask them to say 'confirm'."
        _update(rx["id"], {"status": "confirmed", "confirmed_at": datetime.datetime.now().strftime("%H:%M")})
        _changed("prescriptions")
        return f"OK: prescription #{rx['id']} confirmed by the doctor. Ask which pharmacy to send it to (or send now)."
    if action == "send":
        if rx.get("status") != "confirmed":
            return "FAILED: it must be confirmed by the doctor first."
        _update(rx["id"], {"status": "sent", "pharmacy": pharmacy or rx.get("pharmacy") or "hospital pharmacy",
                           "sent_at": datetime.datetime.now().strftime("%H:%M")})
        if hooks["telegram"]:
            try:
                hooks["telegram"](f"💊 Prescription #{rx['id']} → {pharmacy or rx.get('pharmacy') or 'pharmacy'}\n"
                                  f"Patient: {rx['patient']}\n{rx['medicines']}\nAdvice: {rx.get('advice') or '-'}\n"
                                  f"Dr {rx.get('doctor', '')} {rx.get('reg_no', '')}")
            except Exception:
                pass
        _show(f"Rx #{rx['id']} (sent)", rx_html(dict(rx, status="sent")))
        _changed("prescriptions")
        return (f"OK: prescription #{rx['id']} sent to {pharmacy or rx.get('pharmacy') or 'the hospital pharmacy'} "
                f"(it is in their queue on the Ultron console{', and on Telegram' if hooks['telegram'] else ''}).")
    return "FAILED: action must be draft, confirm or send."


def rx_html(rx):
    meds = "".join(f"<li>{html.escape(m.strip())}</li>" for m in re.split(r"[;\n]|, (?=[A-Z])", rx["medicines"]) if m.strip())
    stamp = {"draft": "#e67e22", "confirmed": "#2980b9", "sent": "#27ae60"}.get(rx.get("status"), "#999")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{font:15px Georgia,serif;background:#eef3f6;margin:0;padding:24px}}
.p{{max-width:640px;margin:auto;background:#fff;border-radius:10px;padding:30px 34px;box-shadow:0 10px 40px rgba(0,0,0,.12);position:relative}}
h1{{font:700 20px 'Segoe UI';color:#0b6e6e;margin:0}} .m{{color:#555;font:13px 'Segoe UI'}} .rx{{font:700 34px Georgia;color:#0b6e6e;margin:18px 0 4px}}
li{{margin:6px 0}} .st{{position:absolute;right:26px;top:24px;border:3px solid {stamp};color:{stamp};font:800 14px 'Segoe UI';
padding:6px 12px;border-radius:8px;transform:rotate(6deg);letter-spacing:2px}} hr{{border:0;border-top:1px solid #dde}}</style></head>
<body><div class="p"><div class="st">{html.escape(str(rx.get('status', '')).upper())}</div>
<h1>Dr {html.escape(str(rx.get('doctor') or '________'))}</h1><div class="m">Reg. No: {html.escape(str(rx.get('reg_no') or '________'))} · {html.escape(str(rx.get('time', '')))}</div><hr>
<div class="m">Patient: <b>{html.escape(rx['patient'])}</b></div><div class="rx">℞</div><ol>{meds}</ol>
<div class="m">Advice: {html.escape(rx.get('advice') or '-')}</div><hr>
<div class="m">Prescription #{rx.get('id', '')} · drafted by voice with ULTRON · valid only after the doctor's confirmation</div></div></body></html>"""


# ------------------------------------------------------------------ alerts, reports
def check_alerts():
    s, out = spec(), []
    for col in ("products", "medicines", "inventory"):
        if col in s["collections"]:
            for r in _rows(col, limit=100000):
                st, mn = r.get("stock"), r.get("min_stock")
                if isinstance(st, (int, float)) and isinstance(mn, (int, float)) and st <= mn:
                    out.append({"kind": "stock", "text": f"{r.get('name') or r.get('material')}: {st:g} left (min {mn:g})"})
    if "medicines" in s["collections"]:
        soon = datetime.date.today() + datetime.timedelta(days=60)
        for r in _rows("medicines", limit=100000):
            try:
                exp = datetime.date.fromisoformat(str(r.get("expiry", ""))[:10])
            except ValueError:
                continue
            if exp <= soon:
                out.append({"kind": "expiry", "text": f"{r.get('name')} batch {r.get('batch', '')} expires {exp:%d %b %Y}"})
    if True:                                         # urgent alerts can be raised in any mode
        for r in _rows("alerts", limit=200):
            if r.get("status") == "open":
                out.append({"kind": "urgent", "text": f"URGENT #{r['id']}: {r.get('message')} → {r.get('to')}", "id": r["id"]})
    if "rooms" in s["collections"]:
        out += [{"kind": "rooms", "text": f"Room {r.get('number')} needs cleaning"} for r in _rows("rooms", limit=1000)
                if str(r.get("status", "")).lower() == "cleaning"]
    if "machines" in s["collections"]:
        out += [{"kind": "machine", "text": f"Machine {r.get('name')} (line {r.get('line')}) is DOWN"} for r in
                _rows("machines", limit=1000) if str(r.get("status", "")).lower() in ("down", "broken", "stopped")]
    if "beds" in s["collections"]:
        free = sum(1 for r in _rows("beds", limit=10000) if str(r.get("status", "")).lower() == "free")
        if _rows("beds", limit=1) and free <= 2:
            out.append({"kind": "beds", "text": f"Only {free} free bed(s) left"})
    if "homework" in s["collections"]:
        today = f"{datetime.date.today()}"
        out += [{"kind": "homework", "text": f"Homework due today: {r.get('class')} {r.get('subject')} - {r.get('task')}"}
                for r in _rows("homework", limit=500) if str(r.get("due", ""))[:10] == today]
    if "orders" in s["collections"]:
        out += [{"kind": "kitchen", "text": f"Table {r.get('table')}: {r.get('items')} ({r.get('status')})"}
                for r in _rows("orders", limit=200) if str(r.get("status", "")).lower() in ("new", "cooking")]
    return out


def business_alerts() -> str:
    a = check_alerts()
    if not a:
        return "OK: no alerts - everything is fine."
    return f"OK: {len(a)} alert(s): " + " | ".join(x["text"] for x in a[:12])


def overview():
    m = current()
    s = spec(m)
    kpis = []
    if s.get("sales"):
        today = f"{datetime.date.today()}"
        sales = [r for r in _rows(s["sales"], limit=100000) if str(r.get("time", "")).startswith(today)]
        kpis += [{"label": "Sales today", "value": f"₹{sum(float(r.get('total') or 0) for r in sales):,.0f}"},
                 {"label": "Bills today", "value": len(sales)}]
    for c in list(s["collections"])[:4]:
        kpis.append({"label": c.replace("_", " ").title(), "value": len(_rows(c, limit=100000))})
    if "beds" in s["collections"]:
        kpis.insert(0, {"label": "Free beds", "value": sum(1 for r in _rows("beds", limit=10000) if str(r.get("status", "")).lower() == "free")})
    al = check_alerts()
    kpis.insert(0, {"label": "Alerts", "value": len(al)})
    return {"mode": m, "name": s["name"], "icon": s["icon"], "theme": s["theme"], "who": s["who"],
            "catalog": s.get("catalog"), "collections": s["collections"], "kpis": kpis[:7], "alerts": al[:30],
            "modes": [{"id": k, "name": v["name"], "icon": v["icon"]} for k, v in MODES.items()],
            "biz_name": hooks["state"]("biz_name", "") or ""}


def business_report(period: str = "today") -> str:
    s = spec()
    if not s.get("sales"):
        counts = {c: len(_rows(c, limit=100000)) for c in s["collections"]}
        _show(f"{s['name']} report", _report_page(s, {}, [], counts, period))
        return "OK: " + ", ".join(f"{c}: {n}" for c, n in counts.items()) + ". Report on the screen."
    days = {"today": 0, "week": 6, "month": 29, "year": 364}.get(period, 0)
    since = f"{datetime.date.today() - datetime.timedelta(days=days)}"
    sales = [r for r in _rows(s["sales"], limit=100000) if str(r.get("time", ""))[:10] >= since]
    total = sum(float(r.get("total") or 0) for r in sales)
    items = {}
    for r in sales:
        for i in r.get("items") or []:
            items[i["name"]] = items.get(i["name"], 0) + float(i.get("amount") or 0)
    top = sorted(items.items(), key=lambda x: -x[1])[:8]
    counts = {c: len(_rows(c, limit=100000)) for c in s["collections"]}
    _show(f"{s['name']} report ({period})", _report_page(s, {"total": total, "bills": len(sales)}, top, counts, period))
    return (f"OK: {period}: {len(sales)} bills, sales ₹{total:,.0f}" + (f", average ₹{total / len(sales):,.0f}" if sales else "")
            + (". Top: " + ", ".join(f"{n} ₹{v:,.0f}" for n, v in top[:5]) if top else "") + ". Report on the screen.")


def _report_page(s, money, top, counts, period):
    mx = max([v for _n, v in top] or [1])
    bars = "".join(f"<div class='b'><span>{html.escape(n)}</span><i style='width:{v / mx * 100:.0f}%'></i><b>₹{v:,.0f}</b></div>" for n, v in top)
    cards = "".join(f"<div class='c'><small>{html.escape(c.title())}</small><b>{n}</b></div>" for c, n in counts.items())
    if money:
        cards = (f"<div class='c hi'><small>Sales ({period})</small><b>₹{money['total']:,.0f}</b></div>"
                 f"<div class='c'><small>Bills</small><b>{money['bills']}</b></div>") + cards
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{font:14px 'Segoe UI',system-ui;background:#0d1117;color:#e6edf3;margin:0;padding:26px}}
h1{{margin:0 0 18px;font-weight:600}} .g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}}
.c{{background:#161b22;border:1px solid #30363d;border-radius:14px;padding:14px}} .c small{{color:#8b949e}} .c b{{display:block;font-size:26px;margin-top:4px}}
.hi{{border-color:#3fb950}} .b{{display:grid;grid-template-columns:160px 1fr 90px;align-items:center;gap:10px;margin:8px 0}}
.b i{{height:14px;border-radius:7px;background:linear-gradient(90deg,#3fb950,#58a6ff);display:block}} .b b{{text-align:right}}
</style></head><body><h1>{s['icon']} {s['name']} report · {period}</h1><div class="g">{cards}</div>
{('<h3>Top items</h3>' + bars) if bars else ''}</body></html>"""


def import_records(collection: str, path: str) -> str:
    m = current()
    if m == "personal":
        return "FAILED: switch to a business mode first."
    p = Path(path.strip('"'))
    if not p.exists():
        for base in (Path.home() / "Downloads", Path.home() / "Documents", Path.home() / "Desktop"):
            hits = list(base.glob(f"*{p.name}*"))
            if hits:
                p = hits[0]
                break
    if not p.exists():
        return f"FAILED: file not found: {path}"
    rows = []
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        ws = openpyxl.load_workbook(p, read_only=True, data_only=True).active
        it = ws.iter_rows(values_only=True)
        head = [str(h or "").strip() for h in next(it)]
        rows = [dict(zip(head, r)) for r in it if any(v not in (None, "") for v in r)]
    else:
        import csv
        with p.open(encoding="utf-8-sig", errors="ignore") as f:
            rows = list(csv.DictReader(f))
    col = collection.lower().strip()
    if col not in spec()["collections"]:
        col = (difflib.get_close_matches(col, list(spec()["collections"]), n=1, cutoff=0.6) or [""])[0]
    if not col:
        return "FAILED: collection must be one of: " + ", ".join(spec()["collections"])
    n = 0
    for r in rows[:20000]:
        d = _clean(col, {k: v for k, v in r.items() if k})
        if d:
            _add(col, d)
            n += 1
    _changed(col)
    return f"OK: imported {n} rows from {p.name} into {col}."


def open_business() -> str:
    hooks["publish"]({"type": "open_url", "url": "/business"})
    if hooks.get("open"):
        hooks["open"]()
    return "OK: the business console is open."


# ------------------------------------------------------------------ console actions (from /business)
def handle(op, data):
    if op == "mode":
        return business_mode(data.get("mode", ""))
    if op == "add":
        return records("add", data.get("col", ""), data.get("data") or {})
    if op == "update":
        return records("update", data.get("col", ""), data.get("data") or {}, id=int(data.get("id") or 0))
    if op == "delete":
        return records("delete", data.get("col", ""), id=int(data.get("id") or 0))
    if op == "bill":
        return make_bill(data.get("items") or [], data.get("customer", ""), data.get("payment", "cash"))
    if op == "alert":
        return urgent_alert(data.get("message", ""), data.get("priority", "high"), data.get("to", ""))
    if op == "ack":
        ack_alert(data.get("id"), data.get("by", "staff"))
        return "OK"
    if op == "rx_confirm":                          # the doctor pressed Confirm themselves on the console
        rows = [r for r in _rows("prescriptions", limit=1000) if r["id"] == int(data.get("id") or 0)]
        if rows and rows[0].get("status") == "draft":
            _update(rows[0]["id"], {"status": "confirmed", "confirmed_at": datetime.datetime.now().strftime("%H:%M")})
            _changed("prescriptions")
        return "OK"
    if op == "rx_send":
        return prescription("send", rx_id=int(data.get("id") or 0), pharmacy=data.get("pharmacy", ""))
    if op == "demo":
        return load_demo()
    if op == "name":
        hooks["save"]("biz_name", str(data.get("name", ""))[:60])
        _changed()
        return "OK"
    if op == "report":
        return business_report(data.get("period", "today"))
    return "FAILED: unknown op"


# ------------------------------------------------------------------ demo data (so any mode can be shown instantly)
DEMO = {
    "hospital": {
        "doctors": [{"name": "Dr Ananya Rao", "specialty": "Cardiology", "phone": "", "reg_no": "TSMC/2014/1123", "on_call": "yes"},
                    {"name": "Dr Vikram Reddy", "specialty": "Emergency Medicine", "phone": "", "reg_no": "TSMC/2011/0876", "on_call": "yes"},
                    {"name": "Dr Sameer Khan", "specialty": "Orthopaedics", "phone": "", "reg_no": "TSMC/2016/2210", "on_call": "no"}],
        "patients": [{"name": "Ravi Kumar", "age": 58, "gender": "M", "ward": "ICU", "bed": "ICU-2", "condition": "Chest pain, observation", "doctor": "Dr Ananya Rao", "status": "admitted"},
                     {"name": "Lakshmi Devi", "age": 34, "gender": "F", "ward": "General", "bed": "G-5", "condition": "Dengue, recovering", "doctor": "Dr Vikram Reddy", "status": "admitted"}],
        "beds": [{"ward": "ICU", "bed": f"ICU-{i}", "status": "occupied" if i == 2 else "free"} for i in range(1, 5)] +
                [{"ward": "General", "bed": f"G-{i}", "status": "occupied" if i == 5 else "free"} for i in range(1, 9)],
    },
    "supermarket": {
        "products": [{"name": n, "category": c, "price": p, "stock": s, "min_stock": m, "aisle": a, "gst": g} for n, c, p, s, m, a, g in [
            ("Basmati Rice 1kg", "Grocery", 120, 40, 10, "A1", 5), ("Toor Dal 1kg", "Grocery", 150, 25, 8, "A1", 5),
            ("Sunflower Oil 1L", "Grocery", 145, 6, 8, "A2", 5), ("Amul Milk 500ml", "Dairy", 28, 60, 20, "C1", 0),
            ("Amul Butter 100g", "Dairy", 58, 18, 6, "C1", 12), ("Bread", "Bakery", 45, 15, 5, "B3", 0),
            ("Eggs (12)", "Dairy", 84, 20, 6, "C2", 0), ("Colgate 150g", "Personal care", 95, 30, 8, "D1", 18),
            ("Surf Excel 1kg", "Home care", 130, 12, 5, "D3", 18), ("Maggi 4-pack", "Snacks", 56, 50, 12, "B1", 12),
            ("Coca-Cola 750ml", "Beverages", 45, 4, 10, "E1", 28), ("Bananas (dozen)", "Fruits", 60, 22, 6, "F1", 0)]],
        "customers": [{"name": "Suresh", "phone": "", "points": 120, "visits": 14}],
        "suppliers": [{"name": "Sri Balaji Distributors", "phone": "", "supplies": "Grocery, oil", "lead_days": 2}],
    },
    "restaurant": {
        "menu": [{"name": n, "category": c, "price": p, "veg": v, "available": "yes", "gst": 5} for n, c, p, v in [
            ("Hyderabadi Chicken Biryani", "Mains", 280, "no"), ("Veg Biryani", "Mains", 210, "yes"), ("Paneer Butter Masala", "Mains", 240, "yes"),
            ("Butter Naan", "Breads", 45, "yes"), ("Masala Dosa", "Tiffins", 90, "yes"), ("Filter Coffee", "Drinks", 40, "yes"),
            ("Gulab Jamun", "Desserts", 70, "yes"), ("Chicken 65", "Starters", 220, "no")]],
        "tables": [{"number": i, "seats": 4 if i < 7 else 6, "status": "free"} for i in range(1, 11)],
    },
    "pharmacy": {
        "medicines": [{"name": n, "salt": s, "price": p, "stock": st, "min_stock": m, "expiry": e, "batch": b, "rack": r, "schedule": sc, "gst": 12} for n, s, p, st, m, e, b, r, sc in [
            ("Dolo 650", "Paracetamol 650mg", 32, 120, 30, "2027-08-31", "D650-22", "R1", "OTC"),
            ("Azithral 500", "Azithromycin 500mg", 120, 14, 15, "2026-11-30", "AZ-118", "R3", "H"),
            ("Pan 40", "Pantoprazole 40mg", 155, 45, 15, "2027-03-31", "PN-77", "R2", "H"),
            ("Cetzine", "Cetirizine 10mg", 18, 80, 20, "2026-12-15", "CZ-09", "R1", "OTC"),
            ("ORS Electral", "Oral rehydration salts", 22, 60, 20, "2028-01-31", "EL-31", "R4", "OTC")]],
    },
    "school": {
        "students": [{"name": n, "class": "10-A", "roll": i + 1, "parent_name": "", "parent_phone": ""} for i, n in enumerate(
            ["Aarav", "Diya", "Ishaan", "Kavya", "Rohan", "Sneha", "Tarun", "Varsha"])],
        "timetable": [{"class": "10-A", "day": "Monday", "period": p + 1, "subject": s, "teacher": t} for p, (s, t) in enumerate(
            [("Maths", "Mrs Sharma"), ("Physics", "Mr Rao"), ("English", "Ms Thomas"), ("Telugu", "Mr Prasad")])],
        "homework": [{"class": "10-A", "subject": "Maths", "task": "Exercise 4.2, Q1-10", "due": f"{datetime.date.today()}"}],
    },
    "hotel": {
        "rooms": [{"number": 100 + i, "type": "Deluxe" if i < 6 else "Suite", "price": 3500 if i < 6 else 6500,
                   "status": "occupied" if i in (2, 7) else "cleaning" if i == 4 else "free"} for i in range(1, 10)],
        "services": [{"name": n, "price": p, "gst": 18} for n, p in [("Room night Deluxe", 3500), ("Room night Suite", 6500),
                                                                      ("Breakfast", 350), ("Laundry", 200), ("Airport pickup", 1200)]],
    },
    "office": {
        "employees": [{"name": n, "role": r, "team": t} for n, r, t in [("Priya", "Engineer", "Product"), ("Karthik", "Designer", "Product"),
                                                                       ("Meena", "HR Manager", "People"), ("Arjun", "Sales Lead", "Sales")]],
        "tasks": [{"title": "Client demo deck", "owner": "Arjun", "due": f"{datetime.date.today() + datetime.timedelta(days=2)}", "priority": "high", "status": "open"}],
    },
    "factory": {
        "machines": [{"name": f"CNC-{i}", "line": "A" if i < 3 else "B", "status": "down" if i == 3 else "running", "last_service": "2026-09-12"} for i in range(1, 5)],
        "inventory": [{"material": "Steel rod 12mm", "stock": 340, "min_stock": 200, "unit": "pcs", "location": "Store 1"},
                      {"material": "Cutting oil", "stock": 18, "min_stock": 25, "unit": "L", "location": "Store 2"}],
    },
}


def load_demo() -> str:
    m = current()
    data = DEMO.get(m)
    if not data:
        return "FAILED: no demo data for this mode."
    n = 0
    for col, rows in data.items():
        if _rows(col, limit=1):
            continue                                  # never mix demo rows into real data
        for r in rows:
            _add(col, r)
            n += 1
    _changed()
    return f"OK: loaded {n} demo records into {spec()['name']} mode (only into empty collections)."
