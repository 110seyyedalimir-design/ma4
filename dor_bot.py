# -*- coding: utf-8 -*-
"""
ربات تلگرام دوربرگردون — @dorbargardoon_bot
Hosted on a Hugging Face Space (Gradio/ZeroGPU wrapper in app.py, or Docker).

Connection to Telegram — NO PROXY NEEDED:
  HF blocks api.telegram.org, so the bot talks to Telegram over MTProto with Telethon
  (direct to Telegram IPs) — exactly like the old Telethon bot.  See dor_tgclient.py.

Storage: DATA_DIR (/data, a Storage Bucket) and/or a private HF dataset repo (needs HF_TOKEN).

Environment (Space → Settings → Variables and secrets):
  BOT_TOKEN            (secret)  token from @BotFather
  ADMIN_ID             (secret)  your numeric Telegram ID (several admins: 111,222)
  LICENSE_PRIVATE_KEY  (secret)  private signing key (hex "d" from license_key.json)
  TG_API_ID            (secret)  api_id   from https://my.telegram.org
  TG_API_HASH          (secret)  api_hash from https://my.telegram.org
  HF_TOKEN             (secret, optional) HF *write* token → data also saved in a private dataset repo
  DATA_REPO            (variable, optional) default <owner>/dorbargardoon-data
  DATA_DIR             (variable, optional) default /data
  TELEGRAM_API_BASE    (variable, optional) use an external Bot API URL/proxy instead of Telethon
  MODE                 (variable, optional) polling | webhook (webhook only with TELEGRAM_API_BASE)
  WEBHOOK_SECRET       (secret, only for webhook mode)
"""
import csv
import html
import io
import json
import logging
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

import dor_jdate as J
import dor_license as L
from dor_hfsync import HFSync
import dor_ios as IOS

VERSION = "2.2"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("bot")

# ============================================================ config
def _env(k, d=""):
    return (os.environ.get(k) or d).strip()

BOT_TOKEN = _env("DOR_BOT_TOKEN")
# ادمین دوربرگردون — کاملاً جدا از ادمین ربات آچارفرانسه (ADMIN_IDS اصلاً خوانده نمی‌شود)
ADMIN_IDS = [int(x) for x in re.findall(r"-?\d+", _env("DOR_ADMIN_ID") or _env("ADMIN_ID"))]
TG_API_ID = re.sub(r"\D", "", _env("TG_API_ID") or _env("API_ID") or _env("TELEGRAM_API_ID"))
TG_API_HASH = re.sub(r"[^0-9a-fA-F]", "", _env("TG_API_HASH") or _env("API_HASH") or _env("TELEGRAM_API_HASH"))
_EXT_BASE = _env("TELEGRAM_API_BASE").rstrip("/")
# telethon = MTProto directly to Telegram (no proxy, like the old bot)   ← default on HF
# remote   = TELEGRAM_API_BASE (proxy) or api.telegram.org (e.g. on a normal VPS)
TRANSPORT = "remote" if _EXT_BASE else ("telethon" if (TG_API_ID and TG_API_HASH) else "remote")
API_BASE = _EXT_BASE or "https://api.telegram.org"
TT = None          # dor_tgclient.Bridge when TRANSPORT == "telethon"
WEBHOOK_SECRET = re.sub(r"[^A-Za-z0-9_\-]", "", _env("WEBHOOK_SECRET"))
SPACE_HOST = _env("SPACE_HOST")
SPACE_ID = _env("SPACE_ID")
PUBLIC_URL = _env("PUBLIC_URL", f"https://{SPACE_HOST}" if SPACE_HOST else "").rstrip("/")
_want_mode = _env("MODE").lower()
if TRANSPORT == "telethon":
    MODE = "telethon"                    # updates are pushed by Telethon (no webhook/polling needed)
elif _want_mode in ("webhook", "polling"):
    MODE = _want_mode
else:
    MODE = "webhook" if (_EXT_BASE and PUBLIC_URL and WEBHOOK_SECRET) else "polling"
PORT = int(_env("PORT", "7860"))
DATA_DIR = _env("DATA_DIR", "/data")
HF_TOKEN = _env("HF_TOKEN") or _env("HUGGING_FACE_HUB_TOKEN") or _env("HF_API_TOKEN")
DATA_REPO = _env("DATA_REPO") or (SPACE_ID.split("/")[0] + "/dorbargardoon-data" if "/" in SPACE_ID else "")
KEEPALIVE_MIN = float(_env("KEEPALIVE_MINUTES", "20"))
KEEPALIVE_PATH = _env("KEEPALIVE_PATH", "/health")
TRIAL_MINUTES = int(_env("TRIAL_MINUTES", "30"))
IOS_PUBLIC_URL = _env("IOS_PUBLIC_URL", PUBLIC_URL).rstrip("/")   # آدرس عمومی برای لینک‌های آیفون
IOS_MAX_IPS = int(_env("IOS_MAX_IPS", "3") or 3)
IOS_UPSTREAMS = [u.strip() for u in _env("IOS_UPSTREAMS").split(",") if u.strip().startswith("https://")]
TRIAL_WINDOW_H = float(_env("TRIAL_ACTIVATE_HOURS", "1"))
PAID_WINDOW_H = float(_env("PAID_ACTIVATE_HOURS", "72"))
BOT_NAME = "دوربرگردون"

STATUS = {"started": time.time(), "telegram": "در حال اتصال…", "webhook": "-", "errors": [], "storage": "-",
          "keepalive": "—"}


def note_error(msg):
    log.error(msg)
    STATUS["errors"] = (STATUS["errors"] + [f"{J.greg(time.time())} {msg}"])[-8:]


# ============================================================ storage
class DB:
    def __init__(self, want_dir):
        self.lock = threading.RLock()
        self.persistent = True
        self.dir = want_dir
        try:
            os.makedirs(want_dir, exist_ok=True)
            probe = os.path.join(want_dir, ".write_test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
        except Exception as e:
            log.error("DATA_DIR %s is not writable (%s) — falling back to ./data (NOT persistent!)", want_dir, e)
            self.persistent = False
            for alt in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"), "/tmp/dorbar-data"):
                try:
                    os.makedirs(alt, exist_ok=True)
                    probe = os.path.join(alt, ".write_test")
                    with open(probe, "w") as f:
                        f.write("ok")
                    os.remove(probe)
                    self.dir = alt
                    break
                except Exception:
                    continue
        if self.persistent and not self._is_mounted(want_dir):
            # A plain folder inside the container is wiped on every restart.
            self.persistent = False
        self.path = os.path.join(self.dir, "db.json")
        self.bak = os.path.join(self.dir, "db.json.bak")
        self.snap_dir = os.path.join(self.dir, "backups")
        os.makedirs(self.snap_dir, exist_ok=True)
        self.remote = None
        self.disk_persistent = self.persistent
        if HF_TOKEN and DATA_REPO:
            # Private dataset repo: restores db.json at startup and keeps a copy after every change.
            # Used as the main storage when no bucket is mounted, and as an extra copy otherwise.
            self.remote = HFSync(HF_TOKEN, DATA_REPO, self.path)
            if not self.disk_persistent:
                self.remote.pull()
            else:
                self.remote.ok = True          # the bucket is the source of truth; just mirror it
            if self.remote.ok:
                self.remote.start()
                self.persistent = True
        self.data = self._load()
        if self.remote and self.remote.ok and not os.path.exists(self.path):
            self.remote.mark_dirty()
        self.update_label()

    def update_label(self):
        parts = []
        if self.disk_persistent:
            parts.append(f"{self.dir} (دائمی ✅)")
        if self.remote:
            parts.append(self.remote.label())
        if not parts:
            parts.append(f"{self.dir} (موقت ⚠️)")
        STATUS["storage"] = " + ".join(parts)

    @staticmethod
    def _is_mounted(path):
        """True if path (or a parent other than /) is a separate mount (e.g. an HF Storage Bucket)."""
        path = os.path.abspath(path)
        try:
            mps = set()
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) > 1:
                        mps.add(parts[1].replace("\\040", " "))
            p = path
            while p and p != "/":
                if p in mps or os.path.ismount(p):
                    return True
                p = os.path.dirname(p)
        except Exception:
            return os.path.ismount(path)
        return False

    @staticmethod
    def empty():
        return {"version": 1, "users": {}, "trials": {}, "ledger": [], "requests": {}, "inbox": [],
                "msgmap": {}, "apk": None, "apps": {}, "counters": {"req": 0, "inbox": 0},
                "settings": {"price_text": "", "welcome_text": "", "trial_enabled": True},
                "meta": {"created": int(time.time()), "last_backup": 0, "last_snapshot": 0}, "peers": {},
                "ios": {"subs": {}, "trials": {}, "counter": 0, "log": []}}

    def _load(self):
        for p in [self.path, self.bak] + sorted(
                [os.path.join(self.snap_dir, f) for f in os.listdir(self.snap_dir) if f.endswith(".json")],
                reverse=True):
            try:
                if os.path.exists(p) and os.path.getsize(p) > 0:
                    with open(p, encoding="utf-8") as f:
                        d = json.load(f)
                    if isinstance(d, dict) and "users" in d:
                        log.info("database loaded from %s", p)
                        return self._migrate(d)
            except Exception as e:
                log.error("cannot read %s: %s", p, e)
        log.info("new database")
        return self.empty()

    def _migrate(self, d):
        e = self.empty()
        for k, v in e.items():
            if k not in d:
                d[k] = v
        for k, v in e["settings"].items():
            d["settings"].setdefault(k, v)
        for k, v in e["meta"].items():
            d["meta"].setdefault(k, v)
        return d

    def dump(self):
        with self.lock:
            d = dict(self.data)
            d["meta"] = {k: v for k, v in self.data.get("meta", {}).items() if k != "tg_session"}  # login stays private
            return _dumps(d, indent=0)

    def save(self):
        with self.lock:
            s = _dumps(self.data, separators=(",", ":"))
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(s)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass
                try:
                    if os.path.exists(self.path):
                        shutil.copyfile(self.path, self.bak)
                except Exception:
                    pass
                os.replace(tmp, self.path)
            except Exception as e:
                log.warning("atomic save failed (%s) — writing directly", e)
                try:
                    with open(self.path, "w", encoding="utf-8") as f:
                        f.write(s)
                except Exception as e2:
                    note_error(f"SAVE FAILED: {e2}")
            if self.remote:
                self.remote.mark_dirty()
            now = time.time()
            if now - self.data["meta"].get("last_snapshot", 0) > 6 * 3600:
                self.data["meta"]["last_snapshot"] = int(now)
                try:
                    name = time.strftime("db-%Y%m%d-%H%M.json", time.gmtime(now))
                    with open(os.path.join(self.snap_dir, name), "w", encoding="utf-8") as f:
                        f.write(s)
                    snaps = sorted(x for x in os.listdir(self.snap_dir) if x.endswith(".json"))
                    for old in snaps[:-20]:
                        os.remove(os.path.join(self.snap_dir, old))
                except Exception as e:
                    log.warning("snapshot failed: %s", e)


def _dumps(d, **kw):
    """json.dumps that tolerates the iPhone DNS threads updating counters at the same moment."""
    for i in range(8):
        try:
            return json.dumps(d, ensure_ascii=False, **kw)
        except RuntimeError:          # dictionary changed size during iteration
            time.sleep(0.02 * (i + 1))
    return json.dumps(json.loads(json.dumps(d, ensure_ascii=False, default=str)), ensure_ascii=False, **kw)


db: DB = None
IOS.configure(get_db=lambda: db, public_url=IOS_PUBLIC_URL,
              event=lambda ev: IOS_EVENTS.put(ev) if "IOS_EVENTS" in globals() else None)
signer: L.Signer = None
ME = {"username": "dorbargardoon_bot"}

# ============================================================ telegram API
class TGError(Exception):
    def __init__(self, desc, code=0):
        super().__init__(desc)
        self.code = code


S = requests.Session()


def api(method, data=None, files=None, timeout=40):
    if TT is not None:
        return TT.call(method, data, files, timeout=max(timeout, 60))
    url = f"{API_BASE}/bot{BOT_TOKEN}/{method}"
    last = None
    for attempt in range(4):
        try:
            if files:
                r = S.post(url, data=data, files=files, timeout=timeout)
            else:
                r = S.post(url, json=data or {}, timeout=timeout)
            try:
                j = r.json()
            except Exception:
                raise TGError(f"HTTP {r.status_code}: {r.text[:200]}", r.status_code)
        except TGError as e:
            last = e
            time.sleep(1 + attempt * 2)
            continue
        except Exception as e:
            last = TGError(f"{type(e).__name__}: {e}")
            time.sleep(1 + attempt * 2)
            continue
        if j.get("ok"):
            return j["result"]
        code = j.get("error_code", 0)
        if code == 429:
            time.sleep(int(j.get("parameters", {}).get("retry_after", 3)) + 1)
            continue
        raise TGError(j.get("description", "error"), code)
    raise last


def send(chat, text, kb=None, reply_to=None, parse="HTML", entities=None):
    d = {"chat_id": chat, "text": text[:4096], "disable_web_page_preview": True}
    if parse and not entities:
        d["parse_mode"] = parse
    if entities:
        d["entities"] = entities
    if kb is not None:
        d["reply_markup"] = kb
    if reply_to:
        d["reply_to_message_id"] = reply_to
        d["allow_sending_without_reply"] = True
    return api("sendMessage", d)


def edit(chat, mid, text, kb=None):
    d = {"chat_id": chat, "message_id": mid, "text": text[:4096], "parse_mode": "HTML",
         "disable_web_page_preview": True}
    if kb is not None:
        d["reply_markup"] = kb
    try:
        return api("editMessageText", d)
    except TGError as e:
        if "not modified" in str(e):
            return None
        return send(chat, text, kb)


def answer(cb_id, text="", alert=False):
    try:
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:190], "show_alert": alert})
    except Exception:
        pass


def copy(to_chat, from_chat, mid, caption=None, caption_entities=None, kb=None, reply_to=None):
    d = {"chat_id": to_chat, "from_chat_id": from_chat, "message_id": mid}
    if caption is not None:
        d["caption"] = caption[:1024]
        if caption_entities is not None:
            d["caption_entities"] = caption_entities
        else:
            d["parse_mode"] = "HTML"
    if kb is not None:
        d["reply_markup"] = kb
    if reply_to:
        d["reply_to_message_id"] = reply_to
        d["allow_sending_without_reply"] = True
    return api("copyMessage", d)


def send_file(chat, filename, content: bytes, caption="", kb=None):
    data = {"chat_id": str(chat), "caption": caption[:1024], "parse_mode": "HTML"}
    if kb is not None:
        data["reply_markup"] = json.dumps(kb)
    return api("sendDocument", data, files={"document": (filename, content)}, timeout=120)


def download(file_id) -> bytes:
    if TT is not None:
        return TT.download(file_id)
    f = api("getFile", {"file_id": file_id})
    fp = f.get("file_path") or ""
    if fp.startswith("/") and os.path.isfile(fp):         # local Bot API server (--local): file is on disk
        with open(fp, "rb") as fh:
            data = fh.read()
        try:
            os.remove(fp)
        except Exception:
            pass
        return data
    r = S.get(f"{API_BASE}/file/bot{BOT_TOKEN}/{fp}", timeout=120)
    r.raise_for_status()
    return r.content


def u16(s):
    return len(s.encode("utf-16-le")) // 2


# ============================================================ UI constants
BTN_TRIAL = "🎁 تست رایگان"
BTN_BUY = "💎 خرید اشتراک"
BTN_SUP = "💬 پیام به پشتیبانی"
BTN_APK = "🤖 برنامهٔ اندروید"
BTN_APK_OLD = "📥 دریافت آخرین نسخه"   # کیبورد قدیمی کاربران
BTN_IOS = "🍏 آیفون"
BTN_WIN = "💻 برنامهٔ ویندوز"
BTN_CHROME = "🌐 افزونهٔ کروم"
BTN_ADMIN = "🛠 پنل مدیریت"
BTN_CANCEL = "❌ انصراف"


def is_admin(uid):
    return uid in ADMIN_IDS


def main_kb(uid):
    rows = [[{"text": BTN_TRIAL}, {"text": BTN_BUY}], [{"text": BTN_APK}, {"text": BTN_WIN}],
            [{"text": BTN_IOS}, {"text": BTN_CHROME}], [{"text": BTN_SUP}]]
    if is_admin(uid):
        rows.append([{"text": BTN_ADMIN}])
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True,
            "input_field_placeholder": "یکی از گزینه‌ها را انتخاب کنید…"}


def welcome_inline():
    return {"inline_keyboard": [
        [{"text": BTN_TRIAL, "callback_data": "u:trial"}, {"text": BTN_BUY, "callback_data": "u:buy"}],
        [{"text": BTN_APK, "callback_data": "u:apk"}, {"text": BTN_WIN, "callback_data": "u:win"}],
        [{"text": BTN_IOS, "callback_data": "u:ios"}, {"text": BTN_CHROME, "callback_data": "u:chrome"}],
        [{"text": BTN_SUP, "callback_data": "u:sup"}]]}


def ikb(rows):
    return {"inline_keyboard": rows}


def btn(text, data):
    return {"text": text, "callback_data": data}


CANCEL_ROW = [btn(BTN_CANCEL, "u:cancel")]

PLANS = [
    ("m1", "۱ ماهه", L.T_DUR, 30 * 86400),
    ("m3", "۳ ماهه", L.T_DUR, 90 * 86400),
    ("m6", "۶ ماهه", L.T_DUR, 180 * 86400),
    ("y1", "۱ ساله", L.T_DUR, 365 * 86400),
    ("unl", "♾ نامحدود", L.T_UNL, 0),
    ("until", "📅 تا تاریخ مشخص", L.T_UNTIL, None),
    ("custom", "✏️ مدت دلخواه", L.T_DUR, None),
]
ADMIN_EXTRA_PLANS = [("d1", "۱ روزه", L.T_DUR, 86400), ("d7", "۷ روزه", L.T_DUR, 7 * 86400),
                     ("trial", f"🎁 تست {TRIAL_MINUTES} دقیقه", L.T_TRIAL, TRIAL_MINUTES * 60)]


def plan_by_key(k):
    for p in PLANS + ADMIN_EXTRA_PLANS:
        if p[0] == k:
            return p
    return None


def plan_rows(prefix, admin=False):
    items = (ADMIN_EXTRA_PLANS if admin else []) + PLANS
    rows, row = [], []
    for k, label, _, _ in items:
        row.append(btn(label, f"{prefix}{k}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def plan_text(p):
    """p = dict(type, value)"""
    t, v = p["type"], p.get("value", 0)
    if t == L.T_UNL:
        return "نامحدود (دائمی)"
    if t == L.T_UNTIL:
        return f"تا {J.jalali(v, False)}"
    if t == L.T_TRIAL:
        return f"تست {J.dur(v)}"
    return f"{J.dur(v)}"


# ============================================================ helpers: users
def esc(s):
    return html.escape(str(s or ""), quote=False)


def user_rec(u):
    uid = str(u["id"])
    now = int(time.time())
    with db.lock:
        rec = db.data["users"].get(uid)
        if not rec:
            rec = {"id": u["id"], "joined": now, "devices": [], "blocked": False, "state": None}
            db.data["users"][uid] = rec
        rec["first_name"] = u.get("first_name", "")
        rec["last_name"] = u.get("last_name", "")
        rec["username"] = u.get("username", "")
        rec["last_seen"] = now
        rec.pop("bot_blocked", None)
    return rec


def get_user(uid):
    return db.data["users"].get(str(uid))


def full_name(rec):
    n = (rec.get("first_name", "") + " " + rec.get("last_name", "")).strip()
    return n or "بدون نام"


def ulink(rec):
    """Clickable name (opens the private chat even without a username) + @username."""
    uid = rec["id"]
    s = f'<a href="tg://user?id={uid}">{esc(full_name(rec))}</a>'
    s += f" | @{esc(rec['username'])}" if rec.get("username") else " | <i>بدون یوزرنیم</i>"
    return s


def ucard(rec):
    return (f"👤 {ulink(rec)}\n"
            f"🆔 <code>{rec['id']}</code>")


def customer_label(rec):
    if not rec:
        return ""
    return (f"@{rec['username']}" if rec.get("username") else full_name(rec)) + f" (tg:{rec['id']})"


def set_state(rec, state):
    rec["state"] = state


def remember_device(rec, dev_id):
    devs = rec.setdefault("devices", [])
    if dev_id in devs:
        devs.remove(dev_id)
    devs.insert(0, dev_id)
    del devs[5:]


def find_users(q):
    q = (q or "").strip()
    out = []
    if not q:
        return out
    users = db.data["users"].values()
    if re.fullmatch(r"-?\d{3,}", q):
        r = get_user(q)
        return [r] if r else []
    if q.startswith("@"):
        ql = q[1:].lower()
        return [u for u in users if (u.get("username") or "").lower() == ql]
    try:
        _, dev = L.parse_dev(q)
        ids = {u["id"] for u in users if dev in u.get("devices", [])}
        ids |= {e.get("tg_id") for e in db.data["ledger"] if e.get("device") == dev and e.get("tg_id")}
        return [get_user(i) for i in ids if get_user(i)]
    except ValueError:
        pass
    ql = q.lower()
    return [u for u in users if ql in full_name(u).lower() or ql in (u.get("username") or "").lower()][:30]


# ============================================================ ledger
def ledger_serials():
    return {e["serial"] for e in db.data["ledger"]}


def device_expiry(dev_id):
    """Best known expiry for a device from the ledger (durations counted from issue time)."""
    best, unl = 0, False
    for e in db.data["ledger"]:
        if e.get("device") != dev_id:
            continue
        if e["type"] == L.T_UNL:
            unl = True
        elif e["type"] == L.T_UNTIL:
            best = max(best, e["value"])
        else:
            best = max(best, e["issuedAt"] + e["value"])
    return float("inf") if unl else best


def issue_code(dev_id, typ, value, window_h, rec=None, source="bot", note="", extend=True):
    dev_b, dev_id = L.parse_dev(dev_id)
    now = int(time.time())
    extended = False
    if extend and typ == L.T_DUR:
        cur = device_expiry(dev_id)
        if cur == float("inf"):
            raise ValueError("این دستگاه از قبل اشتراک نامحدود دارد.")
        if cur > now:
            typ, value, extended = L.T_UNTIL, int(cur + value), True
    with db.lock:
        serial = L.new_serial(ledger_serials())
        act_by = now + int(window_h * 3600)
        code = L.make_code(signer, typ, dev_b, serial, now, int(value), act_by)
        entry = {"serial": serial, "device": dev_id, "type": typ, "value": int(value), "issuedAt": now,
                 "activateBy": act_by, "code": code, "customer": customer_label(rec), "note": note,
                 "tg_id": rec["id"] if rec else None, "source": source}
        db.data["ledger"].append(entry)
        if rec:
            remember_device(rec, dev_id)
    entry["_extended"] = extended
    return entry


def entry_desc(e):
    t = e["type"]
    if t == L.T_UNL:
        return "نامحدود (دائمی)"
    if t == L.T_UNTIL:
        return f"تا {J.jalali(e['value'])}"
    if t == L.T_TRIAL:
        return f"تست رایگان {J.dur(e['value'])} (از لحظهٔ فعال‌سازی)"
    return f"{J.dur(e['value'])} از لحظهٔ فعال‌سازی"


def import_codes(text, source="import"):
    """Adds codes from a generator backup (JSON), a bot backup, CSV, or any text. Skips duplicates."""
    items = []
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and isinstance(obj.get("ledger"), list):
            obj = obj["ledger"]
        if isinstance(obj, list):
            for c in obj:
                if isinstance(c, dict) and c.get("code"):
                    items.append((c["code"], c))
                elif isinstance(c, str):
                    items.append((c, {}))
    except Exception:
        pass
    if not items:
        for line in text.splitlines():
            for m in L.CODE_RE.finditer(line):
                rest = (line[:m.start()] + " " + line[m.end():]).strip(" ,;\t\"'|")
                items.append((m.group(0), {"note": rest[:200]} if rest else {}))
    added = dup = bad = 0
    with db.lock:
        serials = ledger_serials()
        codes = {e["code"] for e in db.data["ledger"]}
        for code, meta in items:
            try:
                r = L.read_code(signer, code)
            except ValueError:
                bad += 1
                continue
            if not r["valid"]:
                bad += 1
                continue
            if r["serial"] in serials or r["code"] in codes:
                dup += 1
                continue
            e = {"serial": r["serial"], "device": r["device"], "type": r["type"], "value": r["value"],
                 "issuedAt": r["issuedAt"], "activateBy": r["activateBy"], "code": r["code"],
                 "customer": str(meta.get("customer", "") or ""), "note": str(meta.get("note", "") or ""),
                 "tg_id": meta.get("tg_id"), "source": meta.get("source") or source}
            db.data["ledger"].append(e)
            serials.add(r["serial"])
            codes.add(r["code"])
            added += 1
        db.data["ledger"].sort(key=lambda e: e["issuedAt"])
    return added, dup, bad


def ledger_json():
    """Same format as the HTML generator export — can be imported there too."""
    keys = ["serial", "device", "type", "value", "issuedAt", "activateBy", "code", "customer", "note"]
    out = []
    for e in db.data["ledger"]:
        o = {k: e.get(k, "") for k in keys}
        if e.get("tg_id"):
            o["tg_id"] = e["tg_id"]
        if e.get("source"):
            o["source"] = e["source"]
        out.append(o)
    return json.dumps(out, ensure_ascii=False, indent=1)


def ledger_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["issued_tehran", "issued_jalali", "customer", "telegram_id", "device", "type", "value",
                "expiry_or_duration", "activate_by", "serial", "source", "note", "code"])
    for e in db.data["ledger"]:
        exp = J.greg(e["value"]) if e["type"] == L.T_UNTIL else ("unlimited" if e["type"] == L.T_UNL else J.dur(e["value"]))
        w.writerow([J.greg(e["issuedAt"]), J.jalali(e["issuedAt"]), e.get("customer", ""), e.get("tg_id") or "",
                    e["device"], L.TNAME.get(e["type"], e["type"]), e["value"], exp, J.greg(e["activateBy"]),
                    e["serial"], e.get("source", ""), e.get("note", ""), e["code"]])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# ============================================================ admin notify
def notify_admins(text, kb=None, map_uid=None):
    for a in ADMIN_IDS:
        try:
            m = send(a, text, kb)
            if map_uid:
                map_msg(a, m["message_id"], map_uid)
        except Exception as e:
            log.warning("notify admin %s failed: %s", a, e)


def map_msg(admin_chat, mid, uid):
    mm = db.data["msgmap"]
    mm[f"{admin_chat}:{mid}"] = uid
    if len(mm) > 6000:
        for k in list(mm.keys())[:1000]:
            mm.pop(k, None)


def code_notice(title, rec, e, extra=""):
    return (f"{title}\n\n"
            f"👤 نام: {ulink(rec) if rec else '—'}\n"
            f"🆔 آیدی عددی: <code>{rec['id'] if rec else '—'}</code>\n"
            f"📅 تاریخ: {J.jalali(e['issuedAt'])}  ({J.greg(e['issuedAt'])})\n"
            f"📱 کد دستگاه: <code>{e['device']}</code>\n"
            f"📦 نوع: {entry_desc(e)}{' — تمدید' if e.get('_extended') else ''}\n"
            f"{extra}"
            f"🔑 کد اشتراک:\n<code>{e['code']}</code>")


def user_actions_kb(uid, extra=None):
    rows = [[btn("↩️ پاسخ / گفتگو", f"a:chat:{uid}"), btn("👤 پروفایل", f"a:user:{uid}")],
            [btn("✅ پاسخ داده شد", f"a:done:{uid}"), btn("🚫 بلاک", f"a:blk:{uid}")]]
    if extra:
        rows = extra + rows
    return ikb(rows)


# ============================================================ user flows
def device_prompt(rec, purpose):
    rows = [[btn(f"📱 {d}", f"u:dev:{purpose}:{d.replace('-', '')}")] for d in rec.get("devices", [])[:3]]
    rows.append([btn("🍏 آیفون / 🌐 کروم دارم", "u:ios_t" if purpose == "t" else "u:ios_b")])
    rows.append(CANCEL_ROW)
    txt = ("📱 لطفاً <b>کد دستگاه</b> خود را بفرستید.\n\n"
           "این کد در صفحهٔ «اشتراک» برنامهٔ دوربرگردون (اندروید یا ویندوز) نوشته شده؛ مثل:\n<code>7K2M-9QXA-TR4B-HW8C</code>\n"
           "روی «کپی» بزنید و همین‌جا بفرستید.\n\n"
           "💡 اگر داخل برنامه روی «تست رایگان» یا «خرید اشتراک» بزنید، کد دستگاه خودکار ارسال می‌شود.\n"
           "🍏 <b>آیفون یا افزونهٔ کروم؟</b> کد دستگاه لازم نیست؛ دکمهٔ «آیفون / کروم دارم» را بزنید.")
    if rec.get("devices"):
        txt += "\n\nیا یکی از دستگاه‌های قبلی خود را انتخاب کنید 👇"
    return txt, ikb(rows)


def send_welcome(chat, rec, extra=""):
    s = db.data["settings"]
    name = esc(rec.get("first_name") or "دوست عزیز")
    send(chat, f"👋 سلام {name}!", main_kb(rec["id"]))
    body = s.get("welcome_text") or (
        "🔁 <b>به دوربرگردون خوش آمدید!</b>\n\n"
        "🌐 <b>اینترنت آزاد، سریع و بی‌دردسر — فقط با یک لمس.</b>\n"
        "دوربرگردون با ۸ سرور اختصاصی و اتصال رمزنگاری‌شده، سایت‌ها و برنامه‌های مسدود را باز می‌کند؛ "
        "بدون افت سرعت و بدون هیچ تنظیم پیچیده‌ای.\n"
        "📱 برای <b>اندروید</b> و <b>ویندوز</b> (برنامه)، <b>آیفون</b> (بدون نصب برنامه، با یک پروفایل) و <b>کروم / اج</b> (افزونه).\n\n"
        f"🎁 <b>تست رایگان</b> — {J.fa(TRIAL_MINUTES)} دقیقه، فوری و خودکار\n"
        "💎 <b>خرید اشتراک</b> — ماهانه، تا تاریخ دلخواه یا نامحدود\n"
        "🤖 <b>برنامهٔ اندروید</b> — همیشه آخرین نسخه\n"
        "💻 <b>برنامهٔ ویندوز</b> — کل ویندوز، با یک کلیک\n"
        "🍏 <b>آیفون</b> — نصب در ۳۰ ثانیه، بدون برنامه\n"
        "🌐 <b>افزونهٔ کروم</b> — DNS امن برای مرورگر\n"
        "💬 <b>پشتیبانی</b> — گفتگوی مستقیم با ما\n\n"
        "🔐 هر اشتراک فقط روی دستگاه خود شما کار می‌کند.\n\n"
        "👇 از دکمه‌های زیر شروع کنید")
    send(chat, body + extra, welcome_inline())


def start_trial(chat, rec, dev=None):
    if not db.data["settings"].get("trial_enabled", True):
        send(chat, "⏸ در حال حاضر تست رایگان فعال نیست. برای خرید اشتراک یا سوال با پشتیبانی در تماس باشید.",
             ikb([[btn(BTN_BUY, "u:buy"), btn(BTN_SUP, "u:sup")]]))
        return
    if dev:
        return do_trial(chat, rec, dev)
    set_state(rec, {"k": "trial_dev"})
    t, kb = device_prompt(rec, "t")
    send(chat, "🎁 <b>تست رایگان " + J.fa(TRIAL_MINUTES) + " دقیقه‌ای</b>\n\n" + t, kb)


def do_trial(chat, rec, dev_text):
    try:
        dev_b, dev = L.parse_dev(dev_text)
    except ValueError as e:
        send(chat, f"⚠️ {esc(e)}\n\nدوباره کد دستگاه را بفرستید یا «انصراف» را بزنید.", ikb([CANCEL_ROW]))
        return
    with db.lock:
        prev = db.data["trials"].get(dev)
        prev_ledger = next((e for e in db.data["ledger"] if e["device"] == dev and e["type"] == L.T_TRIAL), None)
        if prev or prev_ledger:
            at = prev["at"] if prev else prev_ledger["issuedAt"]
            set_state(rec, None)
            remember_device(rec, dev)
            send(chat, f"⛔️ برای این دستگاه (<code>{dev}</code>) قبلاً در تاریخ {J.jalali(at)} تست رایگان صادر شده است.\n\n"
                       "هر دستگاه فقط <b>یک بار</b> می‌تواند تست رایگان بگیرد. برای ادامهٔ استفاده، اشتراک تهیه کنید 👇",
                 ikb([[btn(BTN_BUY, f"u:dev:b:{dev.replace('-', '')}")], [btn(BTN_SUP, "u:sup")]]))
            return
        e = issue_code(dev, L.T_TRIAL, TRIAL_MINUTES * 60, TRIAL_WINDOW_H, rec, source="bot-trial", extend=False)
        db.data["trials"][dev] = {"uid": rec["id"], "at": e["issuedAt"], "serial": e["serial"]}
        set_state(rec, None)
        db.save()
    send(chat, "✅ <b>کد تست رایگان شما آماده است!</b>\n\n"
               f"📱 دستگاه: <code>{dev}</code>\n"
               f"⏱ مدت: {J.fa(TRIAL_MINUTES)} دقیقه از لحظهٔ فعال‌سازی\n"
               f"⌛ مهلت فعال‌سازی: تا {J.jalali(e['activateBy'])}\n\n"
               "📲 برنامه را باز کنید ← کد زیر را در بخش «کد فعال‌سازی» بچسبانید ← «فعال‌سازی».\n"
               "👇 برای کپی، روی کد بزنید:")
    send(chat, f"<code>{e['code']}</code>", ikb([[btn(BTN_BUY, f"u:dev:b:{dev.replace('-', '')}")]]))
    notify_admins(code_notice("🎁 <b>کد تست رایگان صادر شد</b> (خودکار)", rec, e), user_actions_kb(rec["id"]),
                  map_uid=rec["id"])


def start_buy(chat, rec, dev=None):
    if dev:
        return buy_got_device(chat, rec, dev)
    set_state(rec, {"k": "buy_dev"})
    t, kb = device_prompt(rec, "b")
    send(chat, "💎 <b>خرید اشتراک</b>\n\n" + t, kb)


def buy_got_device(chat, rec, dev_text):
    try:
        _, dev = L.parse_dev(dev_text)
    except ValueError as e:
        send(chat, f"⚠️ {esc(e)}\n\nدوباره کد دستگاه را بفرستید یا «انصراف» را بزنید.", ikb([CANCEL_ROW]))
        return
    remember_device(rec, dev)
    set_state(rec, {"k": "buy_plan", "dev": dev})
    price = db.data["settings"].get("price_text") or ""
    cur = device_expiry(dev)
    info = ""
    if cur == float("inf"):
        info = "\nℹ️ این دستگاه از قبل اشتراک <b>نامحدود</b> دارد.\n"
    elif cur > time.time():
        info = f"\nℹ️ این دستگاه اشتراک فعال تا حدود {J.jalali(cur)} دارد؛ اشتراک مدت‌دار جدید به انتهای آن اضافه می‌شود.\n"
    send(chat, f"💎 <b>انتخاب اشتراک</b>\n\n📱 دستگاه: <code>{dev}</code>\n{info}"
               + (f"\n{price}\n" if price else "")
               + "\nنوع اشتراک مورد نظر را انتخاب کنید 👇",
         ikb(plan_rows("u:plan:") + [CANCEL_ROW]))


def parse_duration(text):
    s = (text or "").translate(J.TO_EN).strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(روز|day|days|d|ماه|month|months|m|سال|year|years|y|ساعت|hour|hours|h)?$", s)
    if not m:
        return None
    n = float(m.group(1))
    u = m.group(2) or "روز"
    mult = 86400
    if u in ("ماه", "month", "months", "m"):
        mult = 30 * 86400
    elif u in ("سال", "year", "years", "y"):
        mult = 365 * 86400
    elif u in ("ساعت", "hour", "hours", "h"):
        mult = 3600
    sec = int(n * mult)
    return sec if 3600 <= sec <= 20 * 365 * 86400 else None


def buy_create_request(chat, rec, dev, plan_key, label, typ, value):
    ios_tok = (rec.get("state") or {}).get("ios") if dev == "IOS" else None
    with db.lock:
        db.data["counters"]["req"] += 1
        rid = db.data["counters"]["req"]
        req = {"id": rid, "uid": rec["id"], "device": dev, "plan_key": plan_key, "label": label,
               "type": typ, "value": value, "status": "open", "created": int(time.time())}
        if dev == "IOS":
            req["platform"], req["ios"] = "ios", ios_tok or "new"
        db.data["requests"][str(rid)] = req
        set_state(rec, {"k": "support", "rid": rid})
        db.save()
    send(chat, "✅ <b>درخواست شما ثبت شد و برای پشتیبانی ارسال شد.</b>\n\n"
               f"🧾 شماره درخواست: #{J.fa(rid)}\n"
               + (f"📱 دستگاه: 🍏 آیفون{' — تمدید ' + ios_req_serial(req) if ios_req_serial(req) else ''}\n" if dev == "IOS" else f"📱 دستگاه: <code>{dev}</code>\n") +
               f"📦 اشتراک: {esc(label)}\n\n"
               "پشتیبان به‌زودی از طریق همین ربات (مثلاً برای اعلام مبلغ و شماره کارت) به شما پیام می‌دهد.\n"
               "💬 هر پیام، عکس رسید یا فایلی که اینجا بفرستید مستقیم به دست پشتیبان می‌رسد.", main_kb(rec["id"]))
    notify_admins(request_card(req), request_kb(req), map_uid=rec["id"])


def request_card(req):
    rec = get_user(req["uid"]) or {"id": req["uid"], "first_name": "?"}
    st = {"open": "🟡 باز", "done": "🟢 کد صادر شد", "rejected": "🔴 رد شد"}.get(req["status"], req["status"])
    s = (f"🛒 <b>درخواست خرید #{req['id']}</b>  —  {st}\n\n"
         f"{ucard(rec)}\n"
         + (f"📱 دستگاه: 🍏 <b>آیفون</b> — {('تمدید ' + ios_req_serial(req)) if ios_req_serial(req) else 'اشتراک جدید'}\n"
            if req.get("platform") == "ios" else f"📱 کد دستگاه: <code>{req['device']}</code>\n") +
         f"📦 اشتراک درخواستی: <b>{esc(req['label'])}</b>\n"
         f"📅 زمان: {J.jalali(req['created'])}")
    if req.get("platform") == "ios":
        sub = IOS.data()["subs"].get(req.get("ios") or "")
        if sub:
            s += f"\nℹ️ وضعیت فعلی: {esc(IOS.describe(IOS.resolve_token(sub['token'])))}"
        cur = 0
    else:
        cur = device_expiry(req["device"])
    if cur == float("inf"):
        s += "\nℹ️ این دستگاه اشتراک نامحدود دارد."
    elif cur > time.time():
        s += f"\nℹ️ اشتراک فعلی تا حدود {J.jalali(cur)} (کد مدت‌دار جدید، تمدید حساب می‌شود)"
    if req["status"] == "open":
        s += "\n\n💡 برای گفتگو، روی همین پیام <b>Reply</b> بزنید یا «پیام به کاربر» را انتخاب کنید."
    return s


def ios_req_serial(req):
    sub = IOS.data()["subs"].get(req.get("ios") or "")
    return IOS.resolve_token(sub["token"])["serial"] if sub else ""


def request_kb(req):
    rid, uid = req["id"], req["uid"]
    rows = []
    if req["status"] == "open":
        rows += [[btn("✉️ پیام به کاربر", f"a:chat:{uid}"), btn("✅ صدور همین کد", f"a:rq_ok:{rid}")],
                 [btn("🔑 صدور با پلن دیگر", f"a:rq_pl:{rid}"), btn("❌ رد درخواست", f"a:rq_no:{rid}")]]
    rows += [[btn("👤 پروفایل کاربر", f"a:user:{uid}"), btn("🚫 بلاک", f"a:blk:{uid}")]]
    return ikb(rows)


def start_support(chat, rec):
    set_state(rec, {"k": "support"})
    send(chat, "💬 <b>پیام به پشتیبانی</b>\n\nپیام خود را بنویسید (متن، عکس، فایل یا ویس).\n"
               "پیام شما مستقیم برای پشتیبان ارسال می‌شود و پاسخ را همین‌جا دریافت می‌کنید.", ikb([CANCEL_ROW]))


def send_apk(chat, rec):
    a = db.data.get("apk")
    if not a:
        send(chat, "⏳ فایل نسخهٔ جدید هنوز در ربات قرار داده نشده است. لطفاً کمی بعد دوباره امتحان کنید یا به پشتیبانی پیام دهید.")
        return
    cap = ("📥 <b>آخرین نسخهٔ دوربرگردون</b>\n"
           + (f"{esc(a.get('caption'))}\n" if a.get("caption") else "")
           + f"🗓 {J.jalali(a['at'], False)}\n\n"
           "نصب: فایل را باز کنید و «نصب» را بزنید. اگر نسخهٔ قبلی نصب است، روی همان به‌روزرسانی می‌شود و اشتراک شما حفظ می‌شود.")
    try:
        api("sendDocument", {"chat_id": chat, "document": a["file_id"], "caption": cap, "parse_mode": "HTML"})
    except TGError as e:
        note_error(f"send apk: {e}")
        send(chat, "⚠️ ارسال فایل ممکن نشد. لطفاً به پشتیبانی پیام دهید.")


WIN_GUIDE = ("<b>نصب (ویندوز ۱۰ و ۱۱، ۶۴ بیتی):</b>\n"
             "۱. فایل را دانلود و اجرا کنید — نصب لازم نیست.\n"
             "۲. اگر پیام آبی «Windows protected your PC» آمد: <b>More info</b> ← <b>Run anyway</b>\n"
             "۳. برای دسترسی Administrator «<b>Yes</b>» بزنید (برای ساختن اتصال امن لازم است).\n"
             "۴. در برنامه «تست رایگان» یا «خرید اشتراک» را بزنید؛ کد دستگاه خودکار به ربات می‌آید.\n\n"
             "💡 با بستن پنجره، اتصال در کنار ساعت ویندوز روشن می‌ماند (راست‌کلیک ← قطع اتصال).\n"
             "▶️ یوتیوب و سایت‌هایی که با نام سایت (SNI) فیلتر شده‌اند با موتور ضدفیلتر تازه باز می‌شوند؛ اگر سایتی باز نشد، «سرور بعدی» را بزنید.\n"
             "🌐 افزونهٔ کروم را هم نصب کنید تا از داخل مرورگر وصل/قطع کنید یا حالت «فقط مرورگر» را بزنید.")


def app_slot(slot):
    return (db.data.get("apps") or {}).get(slot)


def send_win(chat, rec):
    a = app_slot("win")
    kb = ikb([[btn(BTN_TRIAL, "u:trial"), btn(BTN_BUY, "u:buy")], [btn(BTN_SUP, "u:sup")]])
    if not a:
        send(chat, "💻 <b>دوربرگردون برای ویندوز</b>\n\n⏳ فایل برنامهٔ ویندوز هنوز در ربات قرار داده نشده است. "
                   "لطفاً کمی بعد دوباره امتحان کنید یا به پشتیبانی پیام دهید.", ikb([[btn(BTN_SUP, "u:sup")]]))
        return
    cap = ("💻 <b>دوربرگردون برای ویندوز</b>\n"
           + (f"{esc(a.get('caption'))}\n" if a.get("caption") else "")
           + f"🗓 {J.jalali(a['at'], False)}")
    try:
        api("sendDocument", {"chat_id": chat, "document": a["file_id"], "caption": cap, "parse_mode": "HTML"})
    except TGError as e:
        note_error(f"send win: {e}")
        send(chat, "⚠️ ارسال فایل ممکن نشد. لطفاً به پشتیبانی پیام دهید.")
        return
    send(chat, WIN_GUIDE + "\n\n🔐 اشتراک ویندوز مثل اندروید با «کد دستگاه» فعال می‌شود و فقط روی همان کامپیوتر کار می‌کند.", kb)


def chrome_menu(chat, rec):
    send(chat, "🌐 <b>دوربرگردون برای کروم و اج</b>\n\n"
               "<b>۱) همراه برنامهٔ ویندوز (پیشنهادی — یوتیوب هم باز می‌شود):</b>\n"
               "برنامهٔ «💻 ویندوز» را اجرا کنید؛ افزونه خودش آن را پیدا می‌کند و کروم را از موتور ضدفیلتر برنامه عبور می‌دهد. "
               "وصل/قطع، سرور بعدی و حالت «فقط مرورگر / کل ویندوز» از داخل خود افزونه. اشتراک همان اشتراک برنامهٔ ویندوز است.\n\n"
               + ("<b>۲) بدون برنامه — DNS شخصی:</b>\n"
                  "با همان «لینک شخصی» آیفون، DNS مرورگر رمزنگاری می‌شود (کروم و اج روی ویندوز، مک، لینوکس و کروم‌بوک). "
                  "⚠️ این حالت فقط فیلتر DNS را رد می‌کند؛ یوتیوب فقط در حالت ۱ باز می‌شود.\n\n" if ios_ready() else "")
               + "<b>نصب افزونه:</b>\n"
               "۱. فایل ZIP را دانلود و <b>Extract</b> کنید\n"
               "۲. آدرس <code>chrome://extensions</code> را باز کنید (در اج: <code>edge://extensions</code>)\n"
               "۳. <b>Developer mode</b> را روشن کنید ← <b>Load unpacked</b> ← پوشهٔ استخراج‌شده را انتخاب کنید\n"
               "۴. روی آیکون دوربرگردون (🧩) بزنید و آن را سنجاق (📌) کنید\n"
               "♻️ نسخهٔ قبلی را دارید؟ پوشه را با نسخهٔ جدید جایگزین کنید و در همان صفحه «Reload» را بزنید.",
         ikb([[btn(BTN_WIN, "u:win")]]))
    a = app_slot("chrome")
    if a:
        try:
            api("sendDocument", {"chat_id": chat, "document": a["file_id"], "parse_mode": "HTML",
                                 "caption": "🌐 <b>افزونهٔ دوربرگردون</b>" + (f"\n{esc(a.get('caption'))}" if a.get("caption") else "")
                                 + f"\n🗓 {J.jalali(a['at'], False)}"})
        except TGError as e:
            note_error(f"send chrome: {e}")
    else:
        send(chat, "⏳ فایل افزونه هنوز در ربات قرار داده نشده است؛ کمی بعد دوباره امتحان کنید.")
    if not ios_ready():
        return
    subs = [s for s in IOS.current_subs(rec["id"]) if IOS.state(s) != "revoked"]
    if not subs:
        send(chat, f"🔗 برای استفاده، یک <b>لینک شخصی</b> لازم است.\n🎁 تست رایگان {J.fa(TRIAL_MINUTES)} دقیقه‌ای یا خرید اشتراک 👇",
             ikb([[btn("🎁 تست رایگان", "u:ios_t"), btn("💎 خرید اشتراک", "u:ios_b")], [btn(BTN_SUP, "u:sup")]]))
        return
    lines = ["🔗 <b>لینک‌های شخصی شما</b> — یکی را در افزونه بچسبانید:\n"]
    for s in subs[-4:]:
        lines.append(f"{ios_sub_line(s)}\n<code>{IOS.links(s)['page']}</code>\n")
    lines.append("🔒 این لینک‌ها مخصوص شماست؛ برای کسی نفرستید.")
    rows = [[btn(f"🔄 تمدید {s['serial']}", f"u:ios_br:{s['serial']}")] for s in subs[-2:]]
    rows.append([btn("➕ اشتراک جدید", "u:ios_bn"), btn(BTN_SUP, "u:sup")])
    send(chat, "\n".join(lines), ikb(rows))


def forward_to_admins(msg, rec):
    """Copies any user message (text/photo/file/voice...) to the admins, with the sender's info."""
    uid = rec["id"]
    st = rec.get("state") or {}
    rid = st.get("rid")
    open_reqs = [r for r in db.data["requests"].values() if r["uid"] == uid and r["status"] == "open"]
    tag = f"  |  🛒 درخواست باز #{open_reqs[-1]['id']}" if open_reqs else ""
    header = f"💬 <b>پیام جدید از کاربر</b>{tag}\n{ucard(rec)}"
    kb = user_actions_kb(uid)
    with db.lock:
        db.data["counters"]["inbox"] += 1
        preview = msg.get("text") or msg.get("caption") or media_kind(msg)
        db.data["inbox"].append({"id": db.data["counters"]["inbox"], "uid": uid, "mid": msg["message_id"],
                                 "at": int(time.time()), "preview": preview[:300], "answered": False})
        if len(db.data["inbox"]) > 3000:
            db.data["inbox"] = [i for i in db.data["inbox"] if not i["answered"]][-3000:]
    for a in ADMIN_IDS:
        try:
            if "text" in msg:
                m = send(a, header + "\n\n" + esc(msg["text"]), kb)
            elif any(k in msg for k in ("photo", "video", "document", "audio", "animation", "voice")):
                cap = header + (("\n\n" + esc(msg["caption"])) if msg.get("caption") else "")
                m = copy(a, uid, msg["message_id"], caption=cap, kb=kb)
            else:
                h = send(a, header, kb)
                map_msg(a, h["message_id"], uid)
                m = copy(a, uid, msg["message_id"], reply_to=h["message_id"])
            map_msg(a, m["message_id"], uid)
        except Exception as e:
            note_error(f"forward to admin {a}: {e}")
    db.save()
    now = time.time()
    if now - rec.get("last_ack", 0) > 90:
        rec["last_ack"] = int(now)
        send(msg["chat"]["id"], "✅ پیام شما برای پشتیبانی ارسال شد. پاسخ را همین‌جا دریافت می‌کنید.")


def media_kind(msg):
    for k, n in (("photo", "🖼 عکس"), ("video", "🎬 ویدیو"), ("document", "📎 فایل"), ("voice", "🎤 ویس"),
                 ("audio", "🎵 صوت"), ("sticker", "استیکر"), ("animation", "GIF"), ("video_note", "ویدیو مسیج"),
                 ("contact", "📇 مخاطب"), ("location", "📍 موقعیت")):
        if k in msg:
            return n
    return "پیام"


def handle_start(chat, rec, payload):
    payload = (payload or "").strip()
    if payload in ("chrome", "windows", "win", "iphone", "ios"):
        send(chat, f"👋 سلام {esc(rec.get('first_name') or '')}!", main_kb(rec["id"]))
        return chrome_menu(chat, rec) if payload == "chrome" else (
            send_win(chat, rec) if payload in ("windows", "win") else ios_menu(chat, rec))
    m = re.match(r"^(w?trial|w?buy|w?id)_([0-9A-Za-z]{16})$", payload)
    if m:
        kind, raw = m.group(1), m.group(2)
        if kind.startswith("w"):               # from the Windows app
            kind = kind[1:]
            plats = rec.setdefault("platforms", [])
            if "win" not in plats:
                plats.append("win")
        try:
            _, dev = L.parse_dev(raw)
            remember_device(rec, dev)
        except ValueError:
            dev = None
        if dev and kind == "trial":
            send(chat, f"👋 سلام {esc(rec.get('first_name') or '')}! کد دستگاه شما دریافت شد: <code>{dev}</code>", main_kb(rec["id"]))
            return do_trial(chat, rec, dev)
        if dev and kind == "buy":
            send(chat, f"👋 سلام {esc(rec.get('first_name') or '')}! کد دستگاه شما دریافت شد: <code>{dev}</code>", main_kb(rec["id"]))
            return buy_got_device(chat, rec, dev)
        if dev:
            return send_welcome(chat, rec, f"\n\n📱 کد دستگاه شما ثبت شد: <code>{dev}</code>")
    send_welcome(chat, rec)


# ============================================================ admin: sending to users
def deliver(admin_chat, admin_msg, uid):
    """Sends an admin message (any type) to a user; returns (ok, error)."""
    prefix = "💬 پیام پشتیبانی:\n\n"
    head_ent = {"type": "bold", "offset": 0, "length": u16("💬 پیام پشتیبانی:")}
    kb = ikb([[btn("✍️ پاسخ به پشتیبانی", "u:sup")]])
    try:
        if "text" in admin_msg:
            ents = [head_ent] + [dict(e, offset=e["offset"] + u16(prefix)) for e in admin_msg.get("entities", [])]
            send(uid, prefix + admin_msg["text"], kb, parse=None, entities=ents)
        elif any(k in admin_msg for k in ("photo", "video", "document", "audio", "animation", "voice")):
            cap = admin_msg.get("caption", "")
            ents = [head_ent] + [dict(e, offset=e["offset"] + u16(prefix)) for e in admin_msg.get("caption_entities", [])]
            copy(uid, admin_chat, admin_msg["message_id"], caption=(prefix + cap).rstrip(), caption_entities=ents, kb=kb)
        else:
            copy(uid, admin_chat, admin_msg["message_id"], kb=kb)
    except TGError as e:
        if e.code == 403:
            rec = get_user(uid)
            if rec:
                rec["bot_blocked"] = True
            return False, "کاربر ربات را بلاک کرده یا حسابش حذف شده است."
        return False, str(e)
    mark_answered(uid)
    return True, ""


def mark_answered(uid):
    with db.lock:
        for i in db.data["inbox"]:
            if i["uid"] == uid and not i["answered"]:
                i["answered"] = True


def unanswered():
    by = {}
    for i in db.data["inbox"]:
        if not i["answered"]:
            by.setdefault(i["uid"], []).append(i)
    return by


# ============================================================ admin panel
def admin_menu_kb():
    ib = len(unanswered())
    rq = sum(1 for r in db.data["requests"].values() if r["status"] == "open")
    return ikb([
        [btn("📊 وضعیت و آمار", "a:stats"), btn("👥 کاربران", "a:users:0")],
        [btn(f"📥 پیام‌های بی‌پاسخ ({J.fa(ib)})", "a:inbox"), btn(f"🛒 درخواست‌های باز ({J.fa(rq)})", "a:reqs")],
        [btn("📒 دفتر اشتراک‌ها", "a:led"), btn("🔑 صدور کد دستی", "a:issue")],
        [btn(f"🍏 آیفون ({J.fa(IOS.counts()[0]['active'])} فعال)", "a:ios:0"), btn("🍏 صدور آیفون", "a:ios_new")],
        [btn("✉️ پیام به یک کاربر", "a:send"), btn("📣 پیام همگانی", "a:bc")],
        [btn("📦 فایل‌های برنامه", "a:files"), btn("⚙️ تنظیمات", "a:set")],
        [btn("💾 پشتیبان‌گیری", "a:backup"), btn("♻️ بازیابی", "a:restore")],
    ])


def admin_menu_text():
    return (f"🛠 <b>پنل مدیریت دوربرگردون</b>\n\n"
            f"👥 کاربران: {J.fa(len(db.data['users']))}   |   📒 کدها: {J.fa(len(db.data['ledger']))}\n"
            f"💾 ذخیره‌سازی: {esc(STATUS['storage'])}")


def show_admin(chat, mid=None):
    if mid:
        edit(chat, mid, admin_menu_text(), admin_menu_kb())
    else:
        send(chat, admin_menu_text(), admin_menu_kb())


BACK = [btn("🔙 بازگشت به پنل", "a:menu")]


def stats_text():
    d = db.data
    now = time.time()
    led = d["ledger"]
    cnt = {t: sum(1 for e in led if e["type"] == t) for t in (1, 2, 3, 4)}
    active_users = sum(1 for u in d["users"].values() if now - u.get("last_seen", 0) < 7 * 86400)
    if MODE == "webhook":
        try:
            wi = api("getWebhookInfo")
            wh = f"{'✅' if wi.get('url') else '—'} در صف: {J.fa(wi.get('pending_update_count', 0))}"
            if wi.get("last_error_message"):
                wh += f"\n   ⚠️ آخرین خطا: {esc(wi['last_error_message'])} ({J.jalali(wi.get('last_error_date', 0))})"
        except Exception as e:
            wh = f"نامشخص ({esc(e)})"
    else:
        wh = "لازم نیست (دریافت مستقیم پیام‌ها)"
    db.update_label()
    s = (f"📊 <b>وضعیت ربات</b>\n\n"
         f"🤖 نسخه ربات: {VERSION}   |   حالت: {MODE}\n"
         f"⏱ روشن از: {J.jalali(STATUS['started'])}\n"
         f"🌐 اتصال تلگرام: {esc(STATUS['telegram'])}\n"
         f"🛰 مسیر اتصال: {esc(transport_label())}\n"
         f"🔗 وبهوک: {wh}\n"
         f"⏰ بیدار نگه‌داشتن: {esc(STATUS['keepalive'])}\n"
         f"💾 ذخیره‌سازی: {esc(STATUS['storage'])}\n"
         f"🗂 آخرین پشتیبان روزانه: {J.jalali(d['meta'].get('last_backup')) if d['meta'].get('last_backup') else '—'}\n"
         f"🔐 کلید امضا: {'✅ با برنامه مطابقت دارد' if signer.matches_app else '❌ با برنامه مطابقت ندارد!'}\n\n"
         f"👥 کل کاربران: {J.fa(len(d['users']))}  (فعال ۷ روز اخیر: {J.fa(active_users)})\n"
         f"🚫 بلاک‌شده: {J.fa(sum(1 for u in d['users'].values() if u.get('blocked')))}\n"
         f"🎁 تست‌های رایگان صادرشده: {J.fa(len(d['trials']))}\n"
         f"📒 دفتر اشتراک‌ها: {J.fa(len(led))} کد  (مدت‌دار {J.fa(cnt[1])} | تا تاریخ {J.fa(cnt[2])} | نامحدود {J.fa(cnt[3])} | تست {J.fa(cnt[4])})\n"
         f"🍏 آیفون: فعال {J.fa(IOS.counts()[0]['active'])} | آماده {J.fa(IOS.counts()[0]['pending'])} | آنلاین {J.fa(IOS.counts()[1])} | DNS امروز {J.fa(IOS.STATE['today'])}"
         + ("" if ios_ready() else " ⚠️ آدرس عمومی تنظیم نشده") + "\n"
         f"🛒 درخواست‌های باز: {J.fa(sum(1 for r in d['requests'].values() if r['status'] == 'open'))}\n"
         f"📥 کاربران با پیام بی‌پاسخ: {J.fa(len(unanswered()))}")
    if not db.persistent:
        s += ("\n\n⚠️ <b>هشدار:</b> ذخیره‌سازی دائمی نیست و با ری‌استارت پاک می‌شود! "
              "یک Storage Bucket با مسیر <code>/data</code> متصل کنید یا Secret <code>HF_TOKEN</code> (توکن Write) بسازید.")
    if STATUS["errors"]:
        s += "\n\n🧯 خطاهای اخیر:\n" + "\n".join(esc(x) for x in STATUS["errors"][-4:])
    return s


def users_page(page):
    users = sorted(db.data["users"].values(), key=lambda u: u.get("joined", 0), reverse=True)
    per = 8
    total = len(users)
    pages = max(1, (total + per - 1) // per)
    page = max(0, min(page, pages - 1))
    chunk = users[page * per:(page + 1) * per]
    lines = [f"👥 <b>کاربران</b> — {J.fa(total)} نفر  (صفحه {J.fa(page + 1)} از {J.fa(pages)})\n"
             "روی نام بزنید تا چت خصوصی باز شود (حتی بدون یوزرنیم).\n"]
    rows = []
    for i, u in enumerate(chunk, start=page * per + 1):
        flag = " 🚫" if u.get("blocked") else (" ⛔️" if u.get("bot_blocked") else "")
        lines.append(f"{J.fa(i)}. {ulink(u)}{flag}\n    🆔 <code>{u['id']}</code> · عضویت {J.jalali(u.get('joined', 0), False)}"
                     + (f" · 📱 {J.fa(len(u.get('devices', [])))}" if u.get("devices") else ""))
        rows.append([btn(f"{i}. {full_name(u)[:24]}" + (f" @{u['username']}" if u.get("username") else ""), f"a:user:{u['id']}")])
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبلی", f"a:users:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("بعدی ▶️", f"a:users:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([btn("🔎 جستجوی کاربر", "a:find"), btn("📄 فایل کامل کاربران", "a:ufile")])
    rows.append(BACK)
    return "\n".join(lines), ikb(rows)


def users_file():
    rows = []
    for u in sorted(db.data["users"].values(), key=lambda u: u.get("joined", 0)):
        codes = [e for e in db.data["ledger"] if e.get("tg_id") == u["id"]]
        un = u.get("username") or ""
        rows.append(
            "<tr><td>{n}</td><td><a href='tg://user?id={id}'>{name}</a></td><td>{un}</td><td>{id}</td>"
            "<td>{devs}</td><td>{codes}</td><td>{j}</td><td>{ls}</td><td>{st}</td></tr>".format(
                n=len(rows) + 1, id=u["id"], name=html.escape(full_name(u)),
                un=(f"<a href='https://t.me/{html.escape(un)}'>@{html.escape(un)}</a>" if un else "—"),
                devs="<br>".join(u.get("devices", [])) or "—", codes=len(codes),
                j=J.jalali(u.get("joined", 0)), ls=J.jalali(u.get("last_seen", 0)),
                st=("بلاک" if u.get("blocked") else "ربات را بلاک کرده" if u.get("bot_blocked") else "فعال")))
    page = ("<!doctype html><html lang='fa' dir='rtl'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>کاربران دوربرگردون</title><style>body{font-family:Tahoma,sans-serif;background:#0b0f1e;color:#eef;padding:12px}"
            "table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #2a335a;padding:6px}"
            "th{background:#1a2140}a{color:#22d3ee}tr:nth-child(even){background:#11172b}</style>"
            f"<h2>کاربران ربات دوربرگردون — {len(rows)} نفر</h2><p>{J.both(time.time())}</p>"
            "<p>روی نام هر کاربر بزنید تا چت خصوصی او در تلگرام باز شود (حتی اگر یوزرنیم ندارد).</p>"
            "<table><tr><th>#</th><th>نام (لینک چت)</th><th>یوزرنیم</th><th>آیدی عددی</th><th>دستگاه‌ها</th><th>کدها</th>"
            "<th>عضویت</th><th>آخرین فعالیت</th><th>وضعیت</th></tr>" + "".join(rows) + "</table></html>")
    return page.encode("utf-8")


def user_profile(uid):
    u = get_user(uid)
    if not u:
        return "کاربر پیدا نشد.", ikb([BACK])
    codes = [e for e in db.data["ledger"] if e.get("tg_id") == u["id"] or e.get("device") in u.get("devices", [])]
    reqs = [r for r in db.data["requests"].values() if r["uid"] == u["id"]]
    ua = len(unanswered().get(u["id"], []))
    s = (f"👤 <b>پروفایل کاربر</b>\n\n{ulink(u)}\n🆔 <code>{u['id']}</code>\n"
         f"📅 عضویت: {J.jalali(u.get('joined', 0))}\n🕒 آخرین فعالیت: {J.jalali(u.get('last_seen', 0))}\n"
         f"وضعیت: {'🚫 بلاک‌شده توسط شما' if u.get('blocked') else '⛔️ ربات را بلاک کرده' if u.get('bot_blocked') else '✅ فعال'}\n"
         f"📥 پیام بی‌پاسخ: {J.fa(ua)}   |   🛒 درخواست‌ها: {J.fa(len(reqs))}\n")
    if u.get("devices"):
        s += "\n📱 دستگاه‌ها:\n" + "\n".join(
            f"• <code>{d}</code>" + (" 🎁" if d in db.data["trials"] else "") for d in u["devices"])
    if codes:
        s += "\n\n🔑 آخرین کدها:\n" + "\n".join(
            f"• {J.jalali(e['issuedAt'], False)} — {entry_desc(e)} — <code>{e['device']}</code>" for e in codes[-5:])
    isubs = IOS.current_subs(u["id"])
    if isubs:
        s += "\n\n🍏 آیفون:\n" + "\n".join(ios_sub_line(x) for x in isubs[-4:])
    rows = [[btn("✉️ گفتگو / پیام", f"a:chat:{uid}"), btn("🔑 صدور کد برای او", f"a:uissue:{uid}")],
            [btn(f"🍏 آیفون ({J.fa(len(isubs))})", f"a:ios_u:{uid}")],
            [btn("✅ آنبلاک" if u.get("blocked") else "🚫 بلاک", f"a:{'unblk' if u.get('blocked') else 'blk'}:{uid}"),
             btn(f"📥 پیام‌ها ({J.fa(ua)})", f"a:ibx:{uid}")],
            BACK]
    return s, ikb(rows)


def inbox_view():
    by = unanswered()
    if not by:
        return "📥 <b>صندوق پیام‌ها</b>\n\n✨ هیچ پیام بی‌پاسخی وجود ندارد.", ikb([BACK])
    items = sorted(by.items(), key=lambda kv: kv[1][-1]["at"], reverse=True)
    now = time.time()
    lines = [f"📥 <b>پیام‌های بی‌پاسخ</b> — {J.fa(len(items))} کاربر\nبرای دیدن و پاسخ، روی هر کاربر بزنید.\n"]
    rows = []
    for uid, msgs in items[:25]:
        u = get_user(uid) or {"id": uid, "first_name": str(uid)}
        last = msgs[-1]
        lines.append(f"• {ulink(u)} — {J.fa(len(msgs))} پیام — {J.ago(last['at'], now)}\n   «{esc(last['preview'][:70])}»")
        rows.append([btn(f"{full_name(u)[:22]} ({len(msgs)})", f"a:ibx:{uid}")])
    rows.append(BACK)
    return "\n".join(lines), ikb(rows)


def open_inbox_of(chat, uid):
    msgs = unanswered().get(uid, [])
    u = get_user(uid) or {"id": uid, "first_name": str(uid)}
    if not msgs:
        send(chat, f"از {ulink(u)} پیام بی‌پاسخی وجود ندارد.", user_actions_kb(uid))
        return
    send(chat, f"📨 <b>{J.fa(len(msgs))} پیام بی‌پاسخ از</b> {ulink(u)}\n🆔 <code>{uid}</code>\n"
               "برای پاسخ روی هر پیام Reply بزنید یا «پاسخ / گفتگو» را انتخاب کنید.")
    for i in msgs[-15:]:
        try:
            m = copy(chat, uid, i["mid"])
            map_msg(chat, m["message_id"], uid)
        except Exception:
            m = send(chat, f"🕒 {J.jalali(i['at'])}\n{esc(i['preview'])}")
            map_msg(chat, m["message_id"], uid)
    send(chat, "⬆️ پایان پیام‌ها", user_actions_kb(uid))


def reqs_view():
    open_r = sorted([r for r in db.data["requests"].values() if r["status"] == "open"], key=lambda r: r["id"])
    if not open_r:
        return "🛒 <b>درخواست‌های خرید</b>\n\n✨ درخواست باز وجود ندارد.", ikb([BACK])
    lines = [f"🛒 <b>درخواست‌های باز</b> — {J.fa(len(open_r))} مورد\n"]
    rows = []
    for r in open_r[-25:]:
        u = get_user(r["uid"]) or {"id": r["uid"], "first_name": "?"}
        lines.append(f"#{J.fa(r['id'])} — {ulink(u)} — {esc(r['label'])} — {J.jalali(r['created'])}")
        rows.append([btn(f"#{r['id']} · {full_name(u)[:18]} · {r['label']}", f"a:req:{r['id']}")])
    rows.append(BACK)
    return "\n".join(lines), ikb(rows)


def ledger_view():
    led = db.data["ledger"]
    lines = [f"📒 <b>دفتر اشتراک‌ها</b> — {J.fa(len(led))} کد\n"]
    for e in led[-10:][::-1]:
        lines.append(f"• {J.jalali(e['issuedAt'])} — {entry_desc(e)}\n   📱 <code>{e['device']}</code> — {esc(e.get('customer') or '—')}")
    lines.append("\nفایل پشتیبان سازندهٔ کد (JSON) را می‌توانید وارد کنید؛ فقط کدهای جدید اضافه می‌شوند و تکراری‌ها نادیده گرفته می‌شوند.")
    return "\n".join(lines), ikb([
        [btn("➕ افزودن دستی (کد)", "a:led_add"), btn("📤 ورود فایل پشتیبان", "a:led_imp")],
        [btn("🔎 جستجو", "a:led_find"), btn("🔑 صدور کد جدید", "a:issue")],
        [btn("📄 خروجی JSON", "a:led_json"), btn("📄 خروجی CSV (اکسل)", "a:led_csv")],
        BACK])


def settings_view():
    s = db.data["settings"]
    return ("⚙️ <b>تنظیمات</b>\n\n"
            f"🎁 تست رایگان خودکار: {'✅ فعال' if s.get('trial_enabled', True) else '⏸ غیرفعال'}\n\n"
            f"💰 متن قیمت‌ها (در مرحلهٔ خرید نمایش داده می‌شود):\n{esc(s.get('price_text')) or '<i>خالی</i>'}\n\n"
            f"👋 متن خوش‌آمد: {'سفارشی' if s.get('welcome_text') else 'پیش‌فرض'}"), ikb([
        [btn("⏸ غیرفعال کردن تست" if s.get("trial_enabled", True) else "▶️ فعال کردن تست", "a:trial_toggle")],
        [btn("💰 ویرایش متن قیمت‌ها", "a:set_price"), btn("👋 ویرایش خوش‌آمد", "a:set_welcome")],
        [btn("↩️ خوش‌آمد پیش‌فرض", "a:welcome_reset")],
        BACK])


def send_backup(chat, caption="💾 پشتیبان کامل دیتابیس ربات"):
    data = db.dump().encode("utf-8")
    name = time.strftime("dorbargardoon-bot-backup-%Y%m%d-%H%M.json", time.localtime())
    send_file(chat, name, data, caption + f"\n🗓 {J.jalali(time.time())}\n"
              "برای بازیابی: پنل ← ♻️ بازیابی ← همین فایل را بفرستید.")


# ============================================================ admin: message handling
def handle_admin_message(msg, rec):
    """Returns True if the message was consumed as an admin action."""
    chat = msg["chat"]["id"]
    text = msg.get("text", "")
    st = rec.get("state") or {}
    k = st.get("k", "")

    if text in ("/admin", BTN_ADMIN, "/panel"):
        set_state(rec, None)
        show_admin(chat)
        return True
    if text == "/backup":
        send_backup(chat)
        return True
    if text == "/stats":
        send(chat, stats_text(), ikb([BACK]))
        return True
    if text.startswith("/user ") or text.startswith("/find "):
        show_found(chat, text.split(" ", 1)[1])
        return True
    if text.startswith("/msg "):
        parts = text.split(" ", 2)
        us = find_users(parts[1]) if len(parts) > 1 else []
        if len(parts) < 3 or len(us) != 1:
            send(chat, "فرمت: <code>/msg آیدی‌عددی متن پیام</code>")
            return True
        ok, err = deliver(chat, {"text": parts[2], "message_id": msg["message_id"]}, us[0]["id"])
        send(chat, "✅ ارسال شد." if ok else f"❌ {esc(err)}")
        return True

    # reply (swipe) to a forwarded message -> goes to that user
    rt = msg.get("reply_to_message")
    if rt and not text.startswith("/"):
        uid = db.data["msgmap"].get(f"{chat}:{rt['message_id']}")
        if uid:
            ok, err = deliver(chat, msg, uid)
            u = get_user(uid) or {"id": uid, "first_name": str(uid)}
            send(chat, f"✅ برای {ulink(u)} ارسال شد." if ok else f"❌ ارسال نشد: {esc(err)}",
                 reply_to=msg["message_id"])
            db.save()
            return True

    if k == "a_chat":
        if text in (BTN_TRIAL, BTN_BUY, BTN_SUP, BTN_APK, BTN_APK_OLD, BTN_IOS, BTN_WIN, BTN_CHROME):
            return False
        uid = st["uid"]
        ok, err = deliver(chat, msg, uid)
        u = get_user(uid) or {"id": uid, "first_name": str(uid)}
        send(chat, (f"✅ برای {ulink(u)} ارسال شد." if ok else f"❌ ارسال نشد: {esc(err)}")
             + "\nپیام بعدی را هم می‌توانید بفرستید.",
             ikb([[btn("⏹ پایان گفتگو", "a:endchat"), btn("🔑 صدور کد", f"a:uissue:{uid}")]]),
             reply_to=msg["message_id"])
        db.save()
        return True
    if k == "a_find":
        set_state(rec, None)
        show_found(chat, text)
        return True
    if k == "a_ios_find":
        set_state(rec, None)
        found = IOS.find(text)
        if not found:
            us = find_users(text)
            for u in us[:3]:
                found += IOS.current_subs(u["id"])
        if not found:
            send(chat, "چیزی پیدا نشد.", ikb([[btn("🔎 جستجوی دوباره", "a:ios_find")], BACK]))
        elif len(found) == 1:
            t, kb = ios_sub_text(found[0])
            send(chat, t, kb)
        else:
            send(chat, "\n".join(ios_sub_line(x) for x in found[:20]),
                 ikb([[btn(f"{x['serial']} — {(x.get('customer') or '')[:20]}", f"a:ios_s:{x['serial']}")] for x in found[:20]] + [BACK]))
        return True
    if k == "a_ios_who":
        t = text.strip()
        if t.startswith("-"):
            st_ = {"k": "a_issue_plan", "dev": "IOS", "ios": "new", "uid": None, "customer": t[1:].strip()}
            who = esc(st_["customer"]) or "بدون نام"
        else:
            us = find_users(t)
            if len(us) != 1:
                send(chat, "کاربری پیدا نشد (یا چند نفر پیدا شد). آیدی عددی دقیق، @یوزرنیم، یا <code>-</code> بفرستید.", ikb([BACK]))
                return True
            st_ = {"k": "a_issue_plan", "dev": "IOS", "ios": "new", "uid": us[0]["id"]}
            who = ulink(us[0])
        set_state(rec, st_)
        send(chat, f"🍏 اشتراک آیفون برای {who}\nنوع اشتراک را انتخاب کنید:", ikb(plan_rows("a:is_plan:", admin=True) + [BACK]))
        return True
    if k == "a_send_who":
        us = find_users(text)
        if not us:
            send(chat, "کاربری پیدا نشد. آیدی عددی، @یوزرنیم یا کد دستگاه را دوباره بفرستید.", ikb([BACK]))
            return True
        if len(us) > 1:
            set_state(rec, None)
            send(chat, "چند کاربر پیدا شد؛ یکی را انتخاب کنید:",
                 ikb([[btn(f"{full_name(u)[:24]} {('@' + u['username']) if u.get('username') else u['id']}", f"a:chat:{u['id']}")] for u in us[:20]] + [BACK]))
            return True
        start_chat(chat, rec, us[0]["id"])
        return True
    if k == "a_bc":
        set_state(rec, {"k": "a_bc_confirm", "mid": msg["message_id"]})
        n = sum(1 for u in db.data["users"].values() if not u.get("blocked") and not u.get("bot_blocked"))
        send(chat, f"📣 این پیام برای <b>{J.fa(n)}</b> کاربر ارسال شود؟",
             ikb([[btn("✅ بله، ارسال کن", "a:bc_go"), btn(BTN_CANCEL, "a:menu")]]), reply_to=msg["message_id"])
        return True
    if k in ("a_issue_dev",):
        try:
            _, dev = L.parse_dev(text)
        except ValueError as e:
            send(chat, f"⚠️ {esc(e)}", ikb([BACK]))
            return True
        st["dev"] = dev
        st["k"] = "a_issue_plan"
        send(chat, f"🔑 صدور کد برای دستگاه <code>{dev}</code>\nنوع اشتراک را انتخاب کنید:",
             ikb(plan_rows("a:is_plan:", admin=True) + [BACK]))
        return True
    if k in ("a_issue_until", "a_issue_custom"):
        if k == "a_issue_until":
            ts = J.parse_date(text)
            if not ts or ts <= time.time():
                send(chat, "⚠️ تاریخ نامعتبر یا گذشته است. مثال: <code>1405/12/29</code> یا <code>2027-03-20</code>", ikb([BACK]))
                return True
            admin_issue(chat, rec, st, L.T_UNTIL, ts, f"تا {J.jalali(ts, False)}")
        else:
            sec = parse_duration(text)
            if not sec:
                send(chat, "⚠️ مدت نامعتبر است. مثال: <code>45 روز</code> ، <code>2 ماه</code> ، <code>12 ساعت</code>", ikb([BACK]))
                return True
            admin_issue(chat, rec, st, L.T_DUR, sec, J.dur(sec))
        return True
    if k == "a_led_add":
        added, dup, bad = import_codes(text, source="manual")
        set_state(rec, None)
        db.save()
        send(chat, f"📒 نتیجه:\n➕ اضافه شد: {J.fa(added)}\n♻️ تکراری (نادیده): {J.fa(dup)}\n❌ نامعتبر: {J.fa(bad)}",
             ikb([[btn("➕ افزودن بیشتر", "a:led_add")], [btn("📒 دفتر اشتراک‌ها", "a:led")], BACK]))
        return True
    if k == "a_led_find":
        set_state(rec, None)
        q = text.strip().lower()
        qd = re.sub(r"[\s\-]", "", q)
        res = [e for e in db.data["ledger"] if qd and qd in e["device"].replace("-", "").lower()
               or q in (e.get("customer") or "").lower() or q in (e.get("note") or "").lower()
               or q == str(e["serial"]) or q == str(e.get("tg_id") or "")]
        if not res:
            send(chat, "چیزی پیدا نشد.", ikb([[btn("📒 دفتر اشتراک‌ها", "a:led")], BACK]))
            return True
        out = [f"🔎 {J.fa(len(res))} نتیجه:"]
        for e in res[-15:]:
            out.append(f"\n• {J.jalali(e['issuedAt'])} — {entry_desc(e)}\n  📱 <code>{e['device']}</code> — {esc(e.get('customer') or '—')}"
                       f"\n  <code>{e['code']}</code>")
        send(chat, "\n".join(out)[:4000], ikb([[btn("📒 دفتر اشتراک‌ها", "a:led")], BACK]))
        return True
    if k in ("a_set_price", "a_set_welcome"):
        key = "price_text" if k == "a_set_price" else "welcome_text"
        db.data["settings"][key] = msg.get("text", "")[:3000] if text != "-" else ""
        set_state(rec, None)
        db.save()
        send(chat, "✅ ذخیره شد.", settings_view()[1])
        return True

    doc = msg.get("document")
    if doc:
        name = (doc.get("file_name") or "").lower()
        if k == "a_upl":
            set_app(chat, rec, msg, (rec.get("state") or {}).get("slot") or "win")
            return True
        slot = "win" if name.endswith(".exe") else ("chrome" if name.endswith((".zip", ".crx")) and any(
            w in name for w in ("chrome", "extension", "edge", "افزونه", "کروم")) else None)
        if slot and k not in ("a_led_imp", "a_restore"):
            set_state(rec, {"k": "a_upl_confirm", "slot": slot, "msg": {"document": doc, "caption": msg.get("caption", "")}})
            send(chat, f"📦 این فایل به‌عنوان «{APP_SLOTS[slot][0]}» برای کاربران ثبت شود؟",
                 ikb([[btn("✅ بله", f"a:upl_yes:{slot}"), btn("خیر", "a:menu")]]), reply_to=msg["message_id"])
            return True
        if k == "a_apk" or (name.endswith(".apk") and k not in ("a_led_imp", "a_restore")):
            if k == "a_apk":
                set_apk(chat, rec, msg)
            else:
                set_state(rec, {"k": "a_apk_confirm", "msg": {"document": doc, "caption": msg.get("caption", "")}})
                send(chat, "📦 این فایل به‌عنوان «آخرین نسخه» برای کاربران ثبت شود؟",
                     ikb([[btn("✅ بله", "a:apk_yes"), btn("خیر", "a:menu")]]), reply_to=msg["message_id"])
            return True
        if k == "a_led_imp" or k == "a_restore" or name.endswith((".json", ".csv", ".txt")):
            try:
                raw = download(doc["file_id"]).decode("utf-8-sig", errors="replace")
            except Exception as e:
                send(chat, f"❌ دریافت فایل ممکن نشد: {esc(e)}")
                return True
            if k == "a_restore":
                do_restore(chat, rec, raw)
                return True
            if k != "a_led_imp":
                set_state(rec, {"k": "a_file_choice", "file_id": doc["file_id"]})
                send(chat, "این فایل را چه کنم؟", ikb([[btn("📒 ورود به دفتر اشتراک‌ها", "a:fimp")],
                                                     [btn("♻️ بازیابی کامل دیتابیس", "a:frestore")], BACK]))
                return True
            added, dup, bad = import_codes(raw, source="import")
            set_state(rec, None)
            db.save()
            send(chat, f"📤 <b>ورود فایل پشتیبان</b>\n\n➕ کد جدید اضافه شد: {J.fa(added)}\n"
                       f"♻️ تکراری (نادیده گرفته شد): {J.fa(dup)}\n❌ نامعتبر: {J.fa(bad)}\n\n"
                       f"📒 مجموع دفتر: {J.fa(len(db.data['ledger']))} کد", ikb([[btn("📒 دفتر اشتراک‌ها", "a:led")], BACK]))
            return True
    return False


def set_apk(chat, rec, msg):
    doc = msg["document"]
    db.data["apk"] = {"file_id": doc["file_id"], "file_name": doc.get("file_name", ""), "size": doc.get("file_size", 0),
                      "caption": msg.get("caption", ""), "at": int(time.time())}
    set_state(rec, None)
    db.save()
    send(chat, f"✅ فایل <b>{esc(doc.get('file_name', ''))}</b> ({J.fa(round(doc.get('file_size', 0) / 1048576, 1))} مگابایت) "
               "به‌عنوان آخرین نسخه ثبت شد. کاربران با دکمهٔ «📥 دریافت آخرین نسخه» آن را دریافت می‌کنند.", ikb([BACK]))


APP_SLOTS = {"win": ("💻 برنامهٔ ویندوز", "EXE"), "chrome": ("🌐 افزونهٔ کروم", "ZIP")}


def set_app(chat, rec, msg, slot):
    doc = msg["document"]
    db.data.setdefault("apps", {})[slot] = {"file_id": doc["file_id"], "file_name": doc.get("file_name", ""),
                                            "size": doc.get("file_size", 0), "caption": msg.get("caption", ""),
                                            "at": int(time.time())}
    set_state(rec, None)
    db.save()
    send(chat, f"✅ فایل <b>{esc(doc.get('file_name', ''))}</b> ({J.fa(round(doc.get('file_size', 0) / 1048576, 1))} مگابایت) "
               f"به‌عنوان «{APP_SLOTS[slot][0]}» ثبت شد. کاربران با دکمهٔ «{BTN_WIN if slot == 'win' else BTN_CHROME}» آن را دریافت می‌کنند.",
         ikb([[btn("📦 فایل‌های برنامه", "a:files")], BACK]))


def files_view():
    out = ["📦 <b>فایل‌های برنامه</b>\n"]
    a = db.data.get("apk")
    out.append("🤖 اندروید: " + (f"{esc(a.get('file_name'))} — {J.jalali(a['at'])}" if a else "—"))
    for k, (label, _) in APP_SLOTS.items():
        x = app_slot(k)
        out.append(f"{label}: " + (f"{esc(x.get('file_name'))} — {J.jalali(x['at'])}" if x else "—"))
    out.append("\n💡 می‌توانید فایل APK / EXE / ZIP افزونه را مستقیم هم در ربات بفرستید؛ خودم تشخیص می‌دهم و تأیید می‌گیرم.")
    return "\n".join(out)


def do_restore(chat, rec, raw):
    try:
        d = json.loads(raw)
        assert isinstance(d, dict) and "users" in d and "ledger" in d
    except Exception:
        send(chat, "❌ این فایل پشتیبان دیتابیس ربات نیست. (برای فایل سازندهٔ کد از «📒 ورود فایل پشتیبان» استفاده کنید.)", ikb([BACK]))
        return
    with db.lock:
        try:
            with open(os.path.join(db.snap_dir, time.strftime("pre-restore-%Y%m%d-%H%M%S.json")), "w", encoding="utf-8") as f:
                f.write(db.dump())
        except Exception:
            pass
        keep_meta = {k: db.data["meta"][k] for k in ("tg_session",) if k in db.data.get("meta", {})}
        keep_peers = dict(db.data.get("peers") or {})
        db.data = db._migrate(d)
        db.data["meta"].pop("tg_session", None)
        db.data["meta"].update(keep_meta)
        peers = db.data.get("peers") if isinstance(db.data.get("peers"), dict) else {}
        peers.update(keep_peers)
        db.data["peers"] = peers
        me = get_user(rec["id"])
        if me:
            me["state"] = None
        db.save()
    send(chat, f"✅ دیتابیس بازیابی شد: {J.fa(len(db.data['users']))} کاربر، {J.fa(len(db.data['ledger']))} کد.", ikb([BACK]))


def show_found(chat, q):
    us = find_users(q)
    if not us:
        send(chat, "کاربری پیدا نشد.", ikb([BACK]))
    elif len(us) == 1:
        t, kb = user_profile(us[0]["id"])
        send(chat, t, kb)
    else:
        send(chat, f"🔎 {J.fa(len(us))} کاربر پیدا شد:",
             ikb([[btn(f"{full_name(u)[:24]} {('@' + u['username']) if u.get('username') else u['id']}", f"a:user:{u['id']}")] for u in us[:25]] + [BACK]))


def start_chat(chat, rec, uid):
    u = get_user(uid)
    if not u:
        send(chat, "کاربر پیدا نشد.")
        return
    set_state(rec, {"k": "a_chat", "uid": uid})
    send(chat, f"💬 <b>حالت گفتگو با</b> {ulink(u)}\n🆔 <code>{uid}</code>\n\n"
               "هر پیامی (متن، عکس، فایل، ویس…) بفرستید مستقیم برای این کاربر ارسال می‌شود؛ "
               "مثلاً شماره کارت و مبلغ. تا وقتی «پایان گفتگو» را نزنید این حالت ادامه دارد.\n"
               "پیام‌های کاربر هم برای شما فرستاده می‌شود.",
         ikb([[btn("⏹ پایان گفتگو", "a:endchat"), btn("🔑 صدور کد", f"a:uissue:{uid}")]]))


def admin_issue(chat, rec, st, typ, value, label):
    """Issues a code from the admin flow. st: {'dev', 'uid'?, 'rid'?, 'ios'?}"""
    if st.get("ios") or st.get("dev") == "IOS":
        return ios_admin_issue(chat, rec, st, typ, value, label)
    dev = st["dev"]
    target = get_user(st["uid"]) if st.get("uid") else None
    rid = st.get("rid")
    try:
        if typ == L.T_TRIAL:
            e = issue_code(dev, typ, value, TRIAL_WINDOW_H, target, source="admin", extend=False)
        else:
            e = issue_code(dev, typ, value, PAID_WINDOW_H, target, source="admin")
    except ValueError as ex:
        send(chat, f"⚠️ {esc(ex)}", ikb([BACK]))
        return
    set_state(rec, None)
    if rid:
        req = db.data["requests"].get(str(rid))
        if req:
            req["status"], req["serial"], req["closed"] = "done", e["serial"], int(time.time())
    db.save()
    delivered = ""
    if target:
        try:
            send(target["id"], "🎉 <b>اشتراک شما صادر شد!</b>\n\n"
                 + (f"🧾 درخواست #{J.fa(rid)}\n" if rid else "")
                 + f"📱 دستگاه: <code>{e['device']}</code>\n📦 اشتراک: {entry_desc(e)}\n"
                 + (f"⌛ مهلت فعال‌سازی: تا {J.jalali(e['activateBy'])}\n" if e["type"] in (L.T_DUR, L.T_TRIAL) else "")
                 + "\n📲 کد زیر را کپی کنید، در برنامه بچسبانید و «فعال‌سازی» را بزنید 👇")
            send(target["id"], f"<code>{e['code']}</code>")
            mark_answered(target["id"])
            delivered = "📨 کد برای کاربر ارسال شد ✅\n"
        except TGError as ex:
            delivered = f"⚠️ ارسال به کاربر ناموفق بود ({esc(ex)}) — کد را دستی بفرستید.\n"
    else:
        delivered = "ℹ️ این کد به کاربری در ربات وصل نیست؛ آن را خودتان بفرستید.\n"
    title = "🔑 <b>کد اشتراک صادر شد</b>" + (f" — درخواست #{J.fa(rid)}" if rid else "")
    txt = code_notice(title, target, e, extra=delivered)
    for a in ADMIN_IDS:
        try:
            m = send(a, txt, ikb([[btn("👤 پروفایل", f"a:user:{target['id']}")]] if target else [BACK]))
            if target:
                map_msg(a, m["message_id"], target["id"])
        except Exception:
            pass
    db.save()


def broadcast(admin_chat, mid):
    ids = [u["id"] for u in list(db.data["users"].values()) if not u.get("blocked") and not u.get("bot_blocked")]
    ok = fail = 0
    for uid in ids:
        try:
            copy(uid, admin_chat, mid)
            ok += 1
        except TGError as e:
            fail += 1
            if e.code == 403:
                u = get_user(uid)
                if u:
                    u["bot_blocked"] = True
        except Exception:
            fail += 1
        time.sleep(0.06)
    db.save()
    try:
        send(admin_chat, f"📣 پیام همگانی تمام شد.\n✅ موفق: {J.fa(ok)}\n❌ ناموفق: {J.fa(fail)}")
    except Exception:
        pass


# ============================================================ iPhone (ios.py)
def ios_configure():
    kw = dict(get_db=lambda: db, public_url=IOS_PUBLIC_URL, bot_username=ME.get("username") or "dorbargardoon_bot",
              event=lambda ev: IOS_EVENTS.put(ev), max_ips=IOS_MAX_IPS,
              auto_block=lambda: bool(db.data["settings"].get("ios_auto_block")))
    if IOS_UPSTREAMS:
        kw["upstreams"] = IOS_UPSTREAMS
    IOS.configure(**kw)

IOS_EVENTS = queue.Queue()
_ios_saved = [0.0]


def ios_ready():
    return bool(IOS.base_url())


def ios_kind(typ):
    return {L.T_DUR: "dur", L.T_UNTIL: "until", L.T_UNL: "unl", L.T_TRIAL: "trial"}[typ]


def ios_sub_line(s):
    return f"🍏 <b>{s['serial']}</b> — {esc(IOS.describe(s))}"


def ios_setup_kb(s):
    Lk = IOS.links(s)
    return {"inline_keyboard": [
        [{"text": "📲 نصب روی آیفون (باز کردن در Safari)", "url": Lk["page"]}],
        [btn("📄 فایل پروفایل", f"u:ios_f:{s['serial']}"), btn("🔄 تمدید", f"u:ios_br:{s['serial']}")],
        [btn("❓ راهنما", "u:ios_h"), btn(BTN_SUP, "u:sup")]]}


def ios_send_setup(chat, s, title):
    txt = (f"{title}\n\n🔖 شمارهٔ اشتراک: <b>{s['serial']}</b>\n📦 وضعیت: {esc(IOS.describe(s))}\n\n"
           "👇 <b>نصب در ۳۰ ثانیه:</b>\n"
           "۱. روی «📲 نصب روی آیفون» بزنید (اگر داخل تلگرام باز شد: ⋯ ← <b>Open in Safari</b>)\n"
           "۲. «نصب پروفایل» ← <b>Allow</b>\n"
           "۳. <b>Settings</b> ← <b>Profile Downloaded</b> ← <b>Install</b>\n\n"
           "✨ تمام! بدون نصب هیچ برنامه‌ای. زمان اشتراک از <b>اولین اتصال</b> حساب می‌شود.\n"
           "🔒 این لینک مخصوص شماست؛ آن را برای کسی نفرستید.\n\n"
           f"🌐 <b>کروم / اج کامپیوتر:</b> همین لینک را در افزونهٔ دوربرگردون بچسبانید (ربات ← «{BTN_CHROME}»):\n"
           f"<code>{IOS.links(s)['page']}</code>")
    send(chat, txt, ios_setup_kb(s))


def ios_send_file(chat, s):
    try:
        send_file(chat, f"Dorbargardoon-{s['serial']}.mobileconfig", IOS.mobileconfig(s),
                  "📄 <b>فایل پروفایل آیفون</b>\n"
                  "اگر لینک نصب باز نشد: روی فایل بزنید ← Share ← <b>Save to Files</b> ← از برنامهٔ Files بازش کنید "
                  "← Settings ← Profile Downloaded ← Install.")
    except TGError as e:
        send(chat, f"⚠️ ارسال فایل ممکن نشد: {esc(e)}")


def ios_help_text():
    return ("🍏 <b>دوربرگردون روی آیفون چطور کار می‌کند؟</b>\n\n"
            "روی آیفون برنامه نصب نمی‌کنید؛ یک <b>پروفایل DNS رمزنگاری‌شدهٔ شخصی</b> در تنظیمات گوشی نصب می‌شود "
            "که سایت‌ها و برنامه‌های مسدود را باز می‌کند.\n\n"
            "✅ همیشه روشن، بدون دکمه و بدون مصرف باتری\n"
            "✅ وقتی اشتراک تمام شود، گوشی خودکار به حالت عادی برمی‌گردد\n"
            "⚡ حالت قوی‌تر (اختیاری): با برنامهٔ رایگان <b>sing-box MT</b> از App Store — لینکش در صفحهٔ نصب هست\n\n"
            "🛡 <b>امن است:</b> فقط DNS را تنظیم می‌کند؛ به پیام‌ها، عکس‌ها و رمزها دسترسی ندارد و به تماس، پیامک و سیم‌کارت دست نمی‌زند. "
            "هر وقت بخواهید با یک لمس حذف می‌شود.\n\n"
            "🗑 حذف: Settings ← General ← VPN &amp; Device Management ← دوربرگردون ← Remove Profile\n"
            "💡 اگر بعد از نصب سایتی باز نشد، یک بار حالت هواپیما را روشن و خاموش کنید.")


def ios_menu(chat, rec):
    if not ios_ready():
        send(chat, "⏳ نسخهٔ آیفون به‌زودی فعال می‌شود. برای اطلاعات بیشتر به پشتیبانی پیام دهید.", ikb([[btn(BTN_SUP, "u:sup")]]))
        return
    subs = IOS.current_subs(rec["id"])
    if not subs:
        send(chat, "🍏 <b>دوربرگردون برای آیفون</b>\n\n" + ios_help_text().split("\n\n", 1)[1] +
             f"\n\n🎁 تست رایگان {J.fa(TRIAL_MINUTES)} دقیقه‌ای را همین الان امتحان کنید 👇",
             ikb([[btn("🎁 تست رایگان آیفون", "u:ios_t"), btn("💎 خرید اشتراک آیفون", "u:ios_b")],
                  [btn(BTN_SUP, "u:sup")]]))
        return
    lines = ["🍏 <b>اشتراک‌های آیفون شما</b>\n"]
    rows = []
    for s in subs[-4:]:
        lines.append(ios_sub_line(s))
        if s.get("last_seen"):
            lines.append(f"   📶 آخرین اتصال: {J.ago(s['last_seen'], time.time())}")
        if IOS.state(s) != "revoked":
            rows.append([{"text": f"📲 نصب / راهنمای {s['serial']}", "url": IOS.links(s)["page"]}])
            rows.append([btn("🔄 تمدید", f"u:ios_br:{s['serial']}"), btn("📄 فایل", f"u:ios_f:{s['serial']}"),
                         btn("♻️ لینک جدید", f"u:ios_r:{s['serial']}")])
    rows.append([btn("➕ آیفون دیگر", "u:ios_bn"), btn("❓ راهنما", "u:ios_h")])
    send(chat, "\n".join(lines), ikb(rows))


def ios_trial(chat, rec):
    if not ios_ready():
        return ios_menu(chat, rec)
    if not db.data["settings"].get("trial_enabled", True):
        send(chat, "⏸ در حال حاضر تست رایگان فعال نیست.", ikb([[btn("💎 خرید اشتراک آیفون", "u:ios_b"), btn(BTN_SUP, "u:sup")]]))
        return
    io = IOS.data()
    prev = io["trials"].get(str(rec["id"]))
    if prev or IOS.current_subs(rec["id"]):
        send(chat, "⛔️ شما قبلاً " + (f"در {J.jalali(prev)} " if prev else "") + "تست یا اشتراک آیفون گرفته‌اید.\n"
                   "هر حساب فقط <b>یک بار</b> تست رایگان دارد. برای ادامه اشتراک تهیه کنید 👇",
             ikb([[btn("💎 خرید اشتراک آیفون", "u:ios_b")], [btn("🍏 اشتراک‌های من", "u:ios")]]))
        return
    with db.lock:
        s = IOS.create_sub(rec["id"], customer_label(rec), source="bot-trial")
        IOS.grant(s, "trial", TRIAL_MINUTES * 60, by="bot")
        io["trials"][str(rec["id"])] = int(time.time())
        set_state(rec, None)
        db.save()
    ios_send_setup(chat, s, f"🎁 <b>تست رایگان {J.fa(TRIAL_MINUTES)} دقیقه‌ای آیفون آماده است!</b>")
    notify_admins(ios_notice("🎁 <b>تست رایگان آیفون صادر شد</b> (خودکار)", rec, s, f"تست {J.dur(TRIAL_MINUTES * 60)}"),
                  user_actions_kb(rec["id"], [[btn(f"🍏 {s['serial']}", f"a:ios_s:{s['serial']}")]]), map_uid=rec["id"])


def ios_buy(chat, rec, target=None):
    """target: None (ask) | 'new' | serial"""
    if not ios_ready():
        return ios_menu(chat, rec)
    subs = [s for s in IOS.current_subs(rec["id"]) if IOS.state(s) != "revoked"]
    if target is None and subs:
        rows = [[btn(f"🔄 تمدید {s['serial']} — {IOS.describe(s)[:28]}", f"u:ios_br:{s['serial']}")] for s in subs[-4:]]
        rows.append([btn("➕ اشتراک برای یک آیفون دیگر", "u:ios_bn")])
        rows.append(CANCEL_ROW)
        send(chat, "💎 <b>خرید اشتراک آیفون</b>\n\nتمدید اشتراک فعلی یا یک آیفون جدید؟", ikb(rows))
        return
    tok = "new"
    info = ""
    if target and target != "new":
        s = IOS.by_serial(target)
        if not s or s.get("uid") != rec["id"] or IOS.state(s) == "revoked":
            send(chat, "⚠️ این اشتراک پیدا نشد.", main_kb(rec["id"]))
            return
        tok = s["token"]
        info = f"\n🔄 تمدید: <b>{s['serial']}</b> — {esc(IOS.describe(s))}\nزمان جدید به انتهای اشتراک فعلی اضافه می‌شود.\n"
    set_state(rec, {"k": "buy_plan", "dev": "IOS", "ios": tok})
    price = db.data["settings"].get("price_text") or ""
    send(chat, f"💎 <b>انتخاب اشتراک آیفون</b> 🍏\n{info}" + (f"\n{price}\n" if price else "")
         + "\nنوع اشتراک را انتخاب کنید 👇", ikb(plan_rows("u:plan:") + [CANCEL_ROW]))


def ios_user_reset(chat, rec, serial, confirm):
    s = IOS.by_serial(serial)
    if not s or s.get("uid") != rec["id"] or IOS.state(s) == "revoked":
        send(chat, "⚠️ این اشتراک پیدا نشد.")
        return
    if not confirm:
        send(chat, f"♻️ <b>ساخت لینک جدید برای {s['serial']}</b>\n\n"
                   "اگر لینک یا فایل پروفایل را به کسی داده‌اید یا گوشی‌تان گم شده، لینک جدید بسازید؛ "
                   "لینک قبلی <b>فوراً از کار می‌افتد</b> و باید پروفایل جدید را دوباره نصب کنید. زمان اشتراک تغییری نمی‌کند.",
             ikb([[btn("✅ بله، لینک جدید", f"u:ios_rr:{s['serial']}"), btn(BTN_CANCEL, "u:cancel")]]))
        return
    today = time.strftime("%Y-%m-%d")
    n = sum(1 for h in s.get("history", []) if h["a"] == "reset" and time.strftime("%Y-%m-%d", time.localtime(h["at"])) == today)
    if n >= 3:
        send(chat, "⚠️ امروز به حد مجاز ساخت لینک جدید رسیده‌اید. فردا دوباره امتحان کنید یا به پشتیبانی پیام دهید.")
        return
    with db.lock:
        new = IOS.reset_token(s, by=f"user:{rec['id']}")
        db.save()
    ios_send_setup(chat, new, "♻️ <b>لینک جدید ساخته شد.</b> پروفایل قبلی را حذف و این را نصب کنید.")
    notify_admins(f"♻️ کاربر {ulink(rec)} برای <b>{new['serial']}</b> لینک جدید ساخت.", ikb([[btn(f"🍏 {new['serial']}", f"a:ios_s:{new['serial']}")]]))


def ios_notice(title, rec, s, desc, extra=""):
    return (f"{title}\n\n"
            f"👤 نام: {ulink(rec) if rec else esc(s.get('customer') or '—')}\n"
            f"🆔 آیدی عددی: <code>{rec['id'] if rec else '—'}</code>\n"
            f"📅 تاریخ: {J.jalali(time.time())}  ({J.greg(time.time())})\n"
            f"📱 دستگاه: 🍏 آیفون\n"
            f"🔖 شماره: <b>{s['serial']}</b>\n"
            f"📦 نوع: {esc(desc)}\n"
            f"⏳ وضعیت: {esc(IOS.describe(s))}\n{extra}"
            f"🔗 لینک نصب:\n<code>{IOS.links(s)['page']}</code>")


def ios_admin_issue(chat, rec, st, typ, value, label):
    if not ios_ready():
        send(chat, "⚠️ آدرس عمومی Space مشخص نیست (SPACE_HOST / IOS_PUBLIC_URL)؛ اشتراک آیفون قابل صدور نیست.", ikb([BACK]))
        return
    target = get_user(st["uid"]) if st.get("uid") else None
    rid = st.get("rid")
    s = None
    if st.get("ios") and st["ios"] != "new":
        s = IOS.data()["subs"].get(st["ios"])
        if s and (s.get("replaced_by") or IOS.state(s) == "revoked"):
            s = None
    with db.lock:
        renewed = s is not None
        if not s:
            s = IOS.create_sub(target["id"] if target else None,
                               customer_label(target) if target else (st.get("customer") or ""), source="admin")
        desc = IOS.grant(s, ios_kind(typ), value, by=f"admin:{rec['id']}")
        set_state(rec, None)
        if rid:
            req = db.data["requests"].get(str(rid))
            if req:
                req["status"], req["serial"], req["closed"] = "done", s["serial"], int(time.time())
        db.save()
    delivered = ""
    if target:
        try:
            ios_send_setup(target["id"], s, ("🔄 <b>اشتراک آیفون شما تمدید شد!</b>" if renewed else "🎉 <b>اشتراک آیفون شما آماده است!</b>")
                           + (f"\n🧾 درخواست #{J.fa(rid)}" if rid else "") + f"\n📦 {esc(desc)}")
            mark_answered(target["id"])
            delivered = "📨 لینک برای کاربر ارسال شد ✅\n"
        except TGError as ex:
            delivered = f"⚠️ ارسال به کاربر ناموفق بود ({esc(ex)}) — لینک زیر را دستی بفرستید.\n"
    else:
        delivered = "ℹ️ این اشتراک به کاربری در ربات وصل نیست؛ لینک زیر را خودتان برای مشتری بفرستید.\n"
    title = ("🔄 <b>اشتراک آیفون تمدید شد</b>" if renewed else "🍏 <b>اشتراک آیفون صادر شد</b>") + (f" — درخواست #{J.fa(rid)}" if rid else "")
    kb = [[btn(f"🍏 مدیریت {s['serial']}", f"a:ios_s:{s['serial']}")]]
    if target:
        kb.append([btn("👤 پروفایل", f"a:user:{target['id']}")])
    for a in ADMIN_IDS:
        try:
            m = send(a, ios_notice(title, target, s, desc, delivered), ikb(kb))
            if target:
                map_msg(a, m["message_id"], target["id"])
        except Exception:
            pass
    db.save()


def ios_admin_text(page=0):
    c, online = IOS.counts()
    st = IOS.STATE
    up = "✅" if st["upstream_ok"] else ("—" if st["upstream_ok"] is None else f"❌ {esc(st['last_error'])}")
    subs = sorted(IOS.current_subs(), key=lambda s: max(s.get("last_seen", 0), s["created"]), reverse=True)
    per = 8
    pages = max(1, (len(subs) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    lines = [f"🍏 <b>اشتراک‌های آیفون</b>\n",
             f"✅ فعال: {J.fa(c['active'])}  |  🕓 آماده: {J.fa(c['pending'])}  |  ⌛️ تمام‌شده: {J.fa(c['expired'])}  |  ⛔️ قطع: {J.fa(c['revoked'])}",
             f"📶 آنلاین (۱۵ دقیقهٔ اخیر): {J.fa(online)}   |   🔎 درخواست DNS امروز: {J.fa(st['today'])}",
             f"🧠 کش: {J.fa(st['cache_hits'])} از {J.fa(st['queries'])}   |   🌐 DNS بالادستی: {up}",
             f"🔗 آدرس: <code>{esc(IOS.base_url() or 'تنظیم نشده!')}</code>",
             f"🛡 قطع خودکار اشتراک‌گذاری: {'✅ روشن' if db.data['settings'].get('ios_auto_block') else '⏸ خاموش (فقط هشدار)'}\n"]
    rows = []
    for s in subs[page * per:(page + 1) * per]:
        u = get_user(s["uid"]) if s.get("uid") else None
        who = full_name(u) if u else (s.get("customer") or "بدون کاربر")
        lines.append(f"{ios_sub_line(s)}\n   👤 {esc(who)}" + (f" · 📶 {J.ago(s['last_seen'], time.time())}" if s.get("last_seen") else " · هنوز وصل نشده"))
        rows.append([btn(f"{s['serial']} · {who[:20]}", f"a:ios_s:{s['serial']}")])
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبلی", f"a:ios:{page - 1}"))
    if page < pages - 1:
        nav.append(btn("بعدی ▶️", f"a:ios:{page + 1}"))
    if nav:
        rows.append(nav)
    rows += [[btn("➕ صدور اشتراک آیفون", "a:ios_new"), btn("🔎 جستجو", "a:ios_find")],
             [btn("📄 خروجی CSV", "a:ios_csv"), btn("🛡 قطع خودکار", "a:ios_auto")],
             [btn("🔄 بروزرسانی", f"a:ios:{page}")], BACK]
    return "\n".join(lines), ikb(rows)


def ios_sub_text(s):
    now = time.time()
    u = get_user(s["uid"]) if s.get("uid") else None
    nets1 = IOS.ip_nets(s, 3600, now)
    nets24 = IOS.ip_nets(s, 86400, now)
    via = {"profile": "پروفایل آیفون", "singbox": "برنامهٔ sing-box"}.get(s.get("via"), "—")
    hist = "\n".join(f"• {J.jalali(h['at'])} — {esc({'grant': '➕', 'start': '▶️ اولین اتصال', 'revoke': '⛔️ قطع', 'unrevoke': '✅ وصل', 'reset': '♻️ لینک جدید', 'profile': '📄 دانلود پروفایل'}.get(h['a'], h['a']))} {esc(h.get('d', ''))}"
                     for h in s.get("history", [])[-8:])
    t = (f"🍏 <b>اشتراک آیفون {s['serial']}</b>\n\n"
         f"👤 کاربر: {ulink(u) if u else esc(s.get('customer') or '—')}\n"
         f"📦 وضعیت: {esc(IOS.describe(s, now))}\n"
         f"🗓 ساخته‌شده: {J.jalali(s['created'])}\n"
         f"▶️ اولین اتصال: {J.jalali(s['first_use']) if s.get('first_use') else '—'}\n"
         f"📶 آخرین اتصال: {J.ago(s['last_seen'], now) if s.get('last_seen') else 'هنوز وصل نشده'}  ({via})\n"
         f"🔢 درخواست‌ها: {J.fa(s.get('queries', 0))}" + (f"  |  ⛔️ ردشده بعد از پایان: {J.fa(s.get('refused', 0))}" if s.get("refused") else "") + "\n"
         f"🌐 شبکه‌ها: ۱ ساعت اخیر {J.fa(len(nets1))} | ۲۴ ساعت {J.fa(len(nets24))}"
         + (" ⚠️" if len(nets1) > IOS.CFG["max_ips"] else "") + "\n"
         f"🔗 <code>{IOS.links(s)['page']}</code>")
    if hist:
        t += "\n\n🧾 تاریخچه:\n" + hist
    st = IOS.state(s, now)
    rows = [[btn("➕ تمدید / افزودن زمان", f"a:ios_ext:{s['serial']}")],
            [btn("▶️ وصل دوباره", f"a:ios_on:{s['serial']}") if s.get("revoked") else btn("⛔️ قطع فوری", f"a:ios_off:{s['serial']}"),
             btn("♻️ لینک جدید", f"a:ios_rst:{s['serial']}")]]
    if u:
        rows.append([btn("📨 ارسال لینک برای کاربر", f"a:ios_snd:{s['serial']}"), btn("👤 پروفایل", f"a:user:{u['id']}")])
    rows.append([btn("🔙 لیست آیفون", "a:ios:0"), btn("🛠 پنل", "a:menu")])
    return t, ikb(rows)


def ios_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["serial", "status", "user_id", "name", "username", "customer", "created", "first_use", "expires",
                "unlimited", "trial", "queries", "last_seen", "networks_24h", "link"])
    for s in IOS.current_subs():
        u = get_user(s["uid"]) if s.get("uid") else None
        w.writerow([s["serial"], IOS.state(s), s.get("uid") or "", full_name(u) if u else "", (u or {}).get("username", ""),
                    s.get("customer", ""), J.jalali(s["created"]), J.jalali(s["first_use"]) if s.get("first_use") else "",
                    J.jalali(s["expires"]) if s.get("expires") else "", "yes" if s.get("unlimited") else "",
                    "yes" if s.get("trial") else "", s.get("queries", 0), J.jalali(s["last_seen"]) if s.get("last_seen") else "",
                    len(IOS.ip_nets(s, 86400)), IOS.links(s)["page"]])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def handle_ios_admin_cb(chat, mid, rec, act, arg):
    """Returns the callback answer text (or None if act isn't an iOS action)."""
    if act == "ios":
        t, kb = ios_admin_text(int(arg or 0))
        try:
            edit(chat, mid, t, kb)
        except TGError:
            send(chat, t, kb)
        return ""
    if act == "ios_new":
        set_state(rec, {"k": "a_ios_who"})
        send(chat, "🍏 <b>صدور اشتراک آیفون</b>\n\nآیدی عددی یا @یوزرنیم کاربر ربات را بفرستید.\n"
                   "برای مشتری خارج از ربات: <code>-</code> یا <code>- نام مشتری</code> (لینک را خودتان می‌فرستید).", ikb([BACK]))
        return ""
    if act == "ios_find":
        set_state(rec, {"k": "a_ios_find"})
        send(chat, "🔎 شمارهٔ اشتراک (مثل I-0007)، آیدی عددی، نام مشتری یا لینک را بفرستید:", ikb([BACK]))
        return ""
    if act == "ios_csv":
        send_file(chat, time.strftime("dorbargardoon-iphone-%Y-%m-%d.csv"), ios_csv(), "🍏 لیست اشتراک‌های آیفون — CSV (اکسل)")
        return ""
    if act == "ios_auto":
        s = db.data["settings"]
        s["ios_auto_block"] = not s.get("ios_auto_block")
        t, kb = ios_admin_text(0)
        edit(chat, mid, t, kb)
        return "قطع خودکار " + ("روشن شد" if s["ios_auto_block"] else "خاموش شد")
    if act == "ios_u":
        u = get_user(int(arg))
        subs = IOS.current_subs(int(arg))
        rows = [[btn(f"{s['serial']} — {IOS.describe(s)[:30]}", f"a:ios_s:{s['serial']}")] for s in subs[-6:]]
        rows.append([btn("➕ اشتراک آیفون جدید برای او", f"a:ios_nu:{arg}")])
        rows.append([btn("👤 پروفایل", f"a:user:{arg}")])
        send(chat, f"🍏 آیفون‌های {ulink(u) if u else arg}:\n" + ("\n".join(ios_sub_line(s) for s in subs) or "هیچ اشتراکی ندارد."), ikb(rows))
        return ""
    if act == "ios_nu":
        set_state(rec, {"k": "a_issue_plan", "dev": "IOS", "ios": "new", "uid": int(arg)})
        send(chat, f"🍏 اشتراک آیفون جدید برای {ulink(get_user(int(arg)) or {'id': int(arg), 'first_name': arg})}\nنوع اشتراک:",
             ikb(plan_rows("a:is_plan:", admin=True) + [BACK]))
        return ""
    s = IOS.by_serial(arg or "")
    if act.startswith("ios_") and not s:
        return "اشتراک پیدا نشد"
    if act == "ios_s":
        t, kb = ios_sub_text(s)
        send(chat, t, kb)
        return ""
    if act == "ios_ext":
        set_state(rec, {"k": "a_issue_plan", "dev": "IOS", "ios": s["token"], "uid": s.get("uid"), "customer": s.get("customer", "")})
        send(chat, f"➕ <b>افزودن زمان به {s['serial']}</b>\n{esc(IOS.describe(s))}\n\nنوع اشتراک:", ikb(plan_rows("a:is_plan:", admin=True) + [BACK]))
        return ""
    if act in ("ios_off", "ios_on"):
        with db.lock:
            if act == "ios_off":
                IOS.revoke(s, "توسط مدیر", by=f"admin:{rec['id']}")
            else:
                IOS.unrevoke(s, by=f"admin:{rec['id']}")
            db.save()
        t, kb = ios_sub_text(s)
        try:
            edit(chat, mid, t, kb)
        except TGError:
            send(chat, t, kb)
        return "⛔️ قطع شد" if act == "ios_off" else "✅ وصل شد"
    if act == "ios_rst":
        with db.lock:
            new = IOS.reset_token(s, by=f"admin:{rec['id']}")
            db.save()
        t, kb = ios_sub_text(new)
        send(chat, "♻️ لینک قبلی باطل شد. لینک جدید:\n" + t, kb)
        if new.get("uid"):
            try:
                ios_send_setup(new["uid"], new, "♻️ <b>لینک آیفون شما عوض شد.</b> پروفایل قبلی را حذف و این را نصب کنید.")
            except TGError:
                pass
        return "لینک جدید ساخته شد"
    if act == "ios_snd":
        ios_send_setup(s["uid"], s, "🍏 <b>لینک نصب دوربرگردون برای آیفون شما</b>")
        return "ارسال شد"
    return None


def ios_events_loop():
    """Sends alerts and expiry reminders; saves iOS usage stats now and then."""
    while True:
        try:
            try:
                ev = IOS_EVENTS.get(timeout=30)
            except queue.Empty:
                ev = None
            if ev:
                s = IOS.data()["subs"].get(ev.get("token"))
                if s and ev["type"] == "share":
                    u = get_user(s["uid"]) if s.get("uid") else None
                    notify_admins(f"⚠️ <b>احتمال اشتراک‌گذاری لینک آیفون {s['serial']}</b>\n\n"
                                  f"👤 {ulink(u) if u else esc(s.get('customer') or '—')}\n"
                                  f"🌐 {J.fa(len(ev['nets']))} شبکهٔ مختلف در یک ساعت اخیر:\n"
                                  + "\n".join(f"• <code>{esc(n)}</code>" for n in ev["nets"][:8])
                                  + ("\n\n⛔️ طبق تنظیمات، خودکار قطع شد." if ev.get("auto") else
                                     "\n\nممکن است عوض شدن وای‌فای/دیتا باشد؛ اگر زیاد تکرار شد قطع یا لینک جدید کنید."),
                                  ikb([[btn("⛔️ قطع", f"a:ios_off:{s['serial']}"), btn("♻️ لینک جدید", f"a:ios_rst:{s['serial']}")],
                                       [btn(f"🍏 {s['serial']}", f"a:ios_s:{s['serial']}")]]))
                elif s and ev["type"] == "start" and s.get("uid"):
                    try:
                        send(s["uid"], f"✅ <b>آیفون شما وصل شد!</b>\n🔖 {s['serial']} — {esc(IOS.describe(s))}\n\nاز اینترنت آزاد لذت ببرید 🌐")
                    except Exception:
                        pass
            ios_check_expiry()
            now = time.time()
            if IOS.STATE["dirty"] and (IOS.STATE["urgent"] or now - _ios_saved[0] > 300):
                IOS.STATE["dirty"] = IOS.STATE["urgent"] = False
                _ios_saved[0] = now
                db.save()
        except Exception as e:
            note_error(f"ios loop: {e}")
            time.sleep(5)


_ios_last_check = [0.0]


def ios_check_expiry():
    now = time.time()
    if now - _ios_last_check[0] < 60:
        return
    _ios_last_check[0] = now
    changed = False
    for s in IOS.current_subs():
        if not s.get("uid") or s.get("revoked") or s.get("unlimited") or not s.get("expires"):
            continue
        n = s.setdefault("notified", {})
        left = s["expires"] - now
        u = get_user(s["uid"])
        if u and u.get("bot_blocked"):
            continue
        try:
            if 0 < left < 86400 and not n.get("d1") and not s.get("trial") and (s["expires"] - (s.get("first_use") or s["created"])) > 3 * 86400:
                n["d1"] = int(now)
                changed = True
                send(s["uid"], f"⏳ <b>کمتر از {J.left(left)} تا پایان اشتراک آیفون {s['serial']} مانده.</b>\n"
                               "برای اینکه اینترنت‌تان قطع نشود، همین حالا تمدید کنید 👇",
                     ikb([[btn("🔄 تمدید", f"u:ios_br:{s['serial']}")]]))
            elif left <= 0 and not n.get("end") and left > -3 * 86400:
                n["end"] = int(now)
                changed = True
                if s.get("trial"):
                    head = "⌛️ <b>تست رایگان آیفون شما تمام شد.</b>\nامیدواریم راضی بوده باشید! برای ادامه اشتراک تهیه کنید 👇"
                    kb = ikb([[btn("💎 خرید اشتراک آیفون", "u:ios_b")]])
                else:
                    head = f"⌛️ <b>اشتراک آیفون {s['serial']} تمام شد.</b>\nبرای ادامه تمدید کنید 👇"
                    kb = ikb([[btn("🔄 تمدید", f"u:ios_br:{s['serial']}")]])
                send(s["uid"], head + "\n\n💡 اگر اینترنت عادی‌تان هم کار نکرد: یک بار حالت هواپیما را روشن و خاموش کنید "
                                      "(یا پروفایل را حذف کنید: Settings ← General ← VPN &amp; Device Management).", kb)
        except TGError as e:
            if e.code == 403 and u:
                u["bot_blocked"] = True
        except Exception as e:
            note_error(f"ios reminder: {e}")
    if changed:
        db.save()



# ============================================================ callbacks
def handle_callback(cb):
    frm = cb["from"]
    rec = user_rec(frm)
    data = cb.get("data", "")
    msg = cb.get("message") or {}
    chat = msg.get("chat", {}).get("id", frm["id"])
    mid = msg.get("message_id")
    if rec.get("blocked") and not is_admin(frm["id"]):
        answer(cb["id"], "دسترسی شما محدود شده است.")
        return
    if data.startswith("u:"):
        answer(cb["id"])
        return handle_user_cb(chat, rec, data)
    if data.startswith("a:"):
        if not is_admin(frm["id"]):
            answer(cb["id"], "⛔️")
            return
        return handle_admin_cb(cb, chat, mid, rec, data)
    answer(cb["id"])


def handle_user_cb(chat, rec, data):
    parts = data.split(":")
    if data == "u:trial":
        start_trial(chat, rec)
    elif data == "u:buy":
        start_buy(chat, rec)
    elif data == "u:sup":
        start_support(chat, rec)
    elif data == "u:apk":
        send_apk(chat, rec)
    elif data == "u:win":
        send_win(chat, rec)
    elif data == "u:chrome":
        chrome_menu(chat, rec)
    elif data == "u:ios":
        ios_menu(chat, rec)
    elif data == "u:ios_t":
        set_state(rec, None)
        ios_trial(chat, rec)
    elif data == "u:ios_b":
        ios_buy(chat, rec)
    elif data == "u:ios_bn":
        ios_buy(chat, rec, "new")
    elif data == "u:ios_h":
        send(chat, ios_help_text(), ikb([[btn("🍏 اشتراک‌های من", "u:ios"), btn(BTN_SUP, "u:sup")]]))
    elif parts[1] in ("ios_br", "ios_f", "ios_r", "ios_rr") and len(parts) == 3:
        if parts[1] == "ios_br":
            ios_buy(chat, rec, parts[2])
        elif parts[1] == "ios_f":
            s_ = IOS.by_serial(parts[2])
            if s_ and s_.get("uid") == rec["id"] and IOS.state(s_) != "revoked":
                ios_send_file(chat, s_)
        else:
            ios_user_reset(chat, rec, parts[2], parts[1] == "ios_rr")
    elif data == "u:cancel":
        set_state(rec, None)
        send(chat, "❎ لغو شد. از منوی زیر یکی از گزینه‌ها را انتخاب کنید.", main_kb(rec["id"]))
    elif parts[1] == "dev" and len(parts) == 4:
        purpose, dev = parts[2], parts[3]
        if purpose == "t":
            start_trial(chat, rec, dev)
        else:
            start_buy(chat, rec, dev)
    elif parts[1] == "plan":
        st = rec.get("state") or {}
        if st.get("k") not in ("buy_plan", "buy_until", "buy_custom") or not st.get("dev"):
            send(chat, "⌛ این مرحله منقضی شده است. دوباره «خرید اشتراک» را بزنید.", main_kb(rec["id"]))
            return
        p = plan_by_key(parts[2])
        if not p:
            return
        k, label, typ, val = p
        if typ == L.T_TRIAL and st["dev"] == "IOS":
            return ios_trial(chat, rec)
        if k == "until":
            set_state(rec, {"k": "buy_until", "dev": st["dev"], "ios": st.get("ios")})
            send(chat, "📅 <b>تاریخ پایان اشتراک</b> را بفرستید.\n\nمثال شمسی: <code>1405/12/29</code>\nیا میلادی: <code>2027-03-20</code>",
                 ikb([CANCEL_ROW]))
        elif k == "custom":
            set_state(rec, {"k": "buy_custom", "dev": st["dev"], "ios": st.get("ios")})
            send(chat, "✏️ <b>مدت اشتراک</b> را بنویسید.\n\nمثال: <code>45 روز</code> یا <code>2 ماه</code>", ikb([CANCEL_ROW]))
        else:
            buy_create_request(chat, rec, st["dev"], k, label.replace("♾ ", ""), typ, val)
    db.save()


def handle_admin_cb(cb, chat, mid, rec, data):
    parts = data.split(":")
    act = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    answer_text = ""
    try:
        if act.startswith("ios"):
            r_ = handle_ios_admin_cb(chat, mid, rec, act, arg)
            db.save()
            answer(cb["id"], r_ or "")
            return
        if act == "menu":
            set_state(rec, None)
            show_admin(chat, mid)
        elif act == "stats":
            edit(chat, mid, stats_text(), ikb([[btn("🔄 بروزرسانی", "a:stats")], BACK]))
        elif act == "users":
            t, kb = users_page(int(arg or 0))
            edit(chat, mid, t, kb)
        elif act == "user":
            t, kb = user_profile(int(arg))
            send(chat, t, kb)
        elif act == "find":
            set_state(rec, {"k": "a_find"})
            send(chat, "🔎 آیدی عددی، @یوزرنیم، نام یا کد دستگاه کاربر را بفرستید:", ikb([BACK]))
        elif act == "ufile":
            send_file(chat, time.strftime("users-%Y%m%d.html"), users_file(),
                      f"👥 لیست کامل کاربران ({J.fa(len(db.data['users']))} نفر)\nفایل را باز کنید؛ با زدن روی نام هر کاربر، چت خصوصی او باز می‌شود.")
        elif act == "chat":
            start_chat(chat, rec, int(arg))
        elif act == "endchat":
            set_state(rec, None)
            send(chat, "⏹ گفتگو پایان یافت.", main_kb(rec["id"]))
        elif act == "done":
            mark_answered(int(arg))
            answer_text = "✅ علامت‌گذاری شد"
        elif act in ("blk", "unblk"):
            u = get_user(int(arg))
            if u:
                if is_admin(u["id"]):
                    answer_text = "نمی‌توانید مدیر را بلاک کنید"
                else:
                    u["blocked"] = act == "blk"
                    if u["blocked"]:
                        mark_answered(u["id"])
                    answer_text = "🚫 کاربر بلاک شد" if u["blocked"] else "✅ کاربر آنبلاک شد"
                    send(chat, f"{answer_text}: {ulink(u)}",
                         ikb([[btn("✅ آنبلاک" if u["blocked"] else "🚫 بلاک", f"a:{'unblk' if u['blocked'] else 'blk'}:{u['id']}"),
                               btn("👤 پروفایل", f"a:user:{u['id']}")]]))
        elif act == "inbox":
            t, kb = inbox_view()
            edit(chat, mid, t, kb)
        elif act == "ibx":
            open_inbox_of(chat, int(arg))
        elif act == "reqs":
            t, kb = reqs_view()
            edit(chat, mid, t, kb)
        elif act == "req":
            req = db.data["requests"].get(arg)
            if req:
                m = send(chat, request_card(req), request_kb(req))
                map_msg(chat, m["message_id"], req["uid"])
        elif act == "rq_ok":
            req = db.data["requests"].get(arg)
            if not req or req["status"] != "open":
                answer_text = "این درخواست قبلاً بسته شده است"
            else:
                st = {"dev": req["device"], "uid": req["uid"], "rid": req["id"], "ios": req.get("ios")}
                if req["type"] == L.T_UNTIL and req["value"] <= time.time():
                    answer_text = "تاریخ درخواستی گذشته است؛ پلن دیگری انتخاب کنید"
                else:
                    admin_issue(chat, rec, st, req["type"], req["value"], req["label"])
                    edit(chat, mid, request_card(req), request_kb(req))
        elif act == "rq_pl":
            req = db.data["requests"].get(arg)
            if not req or req["status"] != "open":
                answer_text = "این درخواست قبلاً بسته شده است"
            else:
                set_state(rec, {"k": "a_issue_plan", "dev": req["device"], "uid": req["uid"], "rid": req["id"], "ios": req.get("ios")})
                send(chat, f"🔑 درخواست #{J.fa(req['id'])} — " + ("🍏 آیفون" if req.get("platform") == "ios" else f"دستگاه <code>{req['device']}</code>") + "\nنوع اشتراک را انتخاب کنید:",
                     ikb(plan_rows("a:is_plan:", admin=True) + [BACK]))
        elif act == "rq_no":
            req = db.data["requests"].get(arg)
            if not req or req["status"] != "open":
                answer_text = "این درخواست قبلاً بسته شده است"
            else:
                req["status"], req["closed"] = "rejected", int(time.time())
                try:
                    send(req["uid"], f"❌ درخواست خرید #{J.fa(req['id'])} لغو شد. برای هر سوالی می‌توانید به پشتیبانی پیام دهید.",
                         ikb([[btn(BTN_SUP, "u:sup")]]))
                except Exception:
                    pass
                mark_answered(req["uid"])
                edit(chat, mid, request_card(req), request_kb(req))
                answer_text = "درخواست رد شد"
        elif act == "issue":
            set_state(rec, {"k": "a_issue_dev"})
            send(chat, "🔑 <b>صدور کد دستی</b>\nکد دستگاه را بفرستید:", ikb([BACK]))
        elif act == "uissue":
            u = get_user(int(arg))
            devs = u.get("devices", []) if u else []
            if len(devs) == 1:
                set_state(rec, {"k": "a_issue_plan", "dev": devs[0], "uid": u["id"]})
                send(chat, f"🔑 صدور کد برای {ulink(u)}\n📱 دستگاه <code>{devs[0]}</code>\nنوع اشتراک را انتخاب کنید:",
                     ikb(plan_rows("a:is_plan:", admin=True) + [BACK]))
            else:
                set_state(rec, {"k": "a_issue_dev", "uid": int(arg)})
                rows = [[btn(f"📱 {d}", f"a:is_dev:{d.replace('-', '')}")] for d in devs[:5]]
                send(chat, "🔑 کد دستگاه را بفرستید" + (" یا انتخاب کنید:" if devs else ":"), ikb(rows + [BACK]))
        elif act == "is_dev":
            st = rec.get("state") or {}
            _, dev = L.parse_dev(arg)
            st.update({"k": "a_issue_plan", "dev": dev})
            set_state(rec, st)
            send(chat, f"📱 دستگاه <code>{dev}</code>\nنوع اشتراک را انتخاب کنید:", ikb(plan_rows("a:is_plan:", admin=True) + [BACK]))
        elif act == "is_plan":
            st = rec.get("state") or {}
            if st.get("k") != "a_issue_plan":
                answer_text = "این مرحله منقضی شده؛ دوباره شروع کنید"
            else:
                k, label, typ, val = plan_by_key(arg)
                if k == "until":
                    st["k"] = "a_issue_until"
                    send(chat, "📅 تاریخ پایان را بفرستید (مثال <code>1405/12/29</code> یا <code>2027-03-20</code>):", ikb([BACK]))
                elif k == "custom":
                    st["k"] = "a_issue_custom"
                    send(chat, "✏️ مدت را بفرستید (مثال <code>45 روز</code> ، <code>2 ماه</code> ، <code>12 ساعت</code>):", ikb([BACK]))
                else:
                    admin_issue(chat, rec, st, typ, val, label)
        elif act == "send":
            set_state(rec, {"k": "a_send_who"})
            send(chat, "✉️ آیدی عددی، @یوزرنیم یا کد دستگاه کاربر را بفرستید:", ikb([BACK]))
        elif act == "bc":
            set_state(rec, {"k": "a_bc"})
            send(chat, "📣 پیام همگانی را بفرستید (متن، عکس، فایل…). قبل از ارسال تأیید گرفته می‌شود.", ikb([BACK]))
        elif act == "bc_go":
            st = rec.get("state") or {}
            if st.get("k") == "a_bc_confirm":
                set_state(rec, None)
                threading.Thread(target=broadcast, args=(chat, st["mid"]), daemon=True).start()
                send(chat, "⏳ ارسال همگانی شروع شد؛ در پایان گزارش می‌دهم.")
        elif act == "apk":
            a = db.data.get("apk")
            set_state(rec, {"k": "a_apk"})
            send(chat, "📦 <b>فایل آخرین نسخه</b>\n"
                 + (f"فعلی: {esc(a.get('file_name'))} — {J.jalali(a['at'])}\n" if a else "فعلاً فایلی ثبت نشده.\n")
                 + "\nفایل APK جدید را همین‌جا بفرستید (در کپشن می‌توانید شماره نسخه/توضیح بنویسید).", ikb([BACK]))
        elif act == "apk_yes":
            st = rec.get("state") or {}
            if st.get("k") == "a_apk_confirm":
                set_apk(chat, rec, st["msg"])
        elif act == "files":
            send(chat, files_view(), ikb([[btn("🤖 اندروید (APK)", "a:apk")],
                                          [btn("💻 ویندوز (EXE)", "a:upl:win"), btn("🌐 کروم (ZIP)", "a:upl:chrome")], BACK]))
        elif act == "upl" and arg in APP_SLOTS:
            set_state(rec, {"k": "a_upl", "slot": arg})
            a = app_slot(arg)
            send(chat, f"{APP_SLOTS[arg][0]}\n" + (f"فعلی: {esc(a.get('file_name'))} — {J.jalali(a['at'])}\n" if a else "فعلاً فایلی ثبت نشده.\n")
                 + f"\nفایل {APP_SLOTS[arg][1]} جدید را همین‌جا بفرستید (در کپشن می‌توانید شماره نسخه/توضیح بنویسید).", ikb([BACK]))
        elif act == "upl_yes" and arg in APP_SLOTS:
            st = rec.get("state") or {}
            if st.get("k") == "a_upl_confirm" and st.get("slot") == arg:
                set_app(chat, rec, st["msg"], arg)
        elif act == "set":
            t, kb = settings_view()
            edit(chat, mid, t, kb)
        elif act == "trial_toggle":
            s = db.data["settings"]
            s["trial_enabled"] = not s.get("trial_enabled", True)
            t, kb = settings_view()
            edit(chat, mid, t, kb)
        elif act == "set_price":
            set_state(rec, {"k": "a_set_price"})
            send(chat, "💰 متن قیمت‌ها را بفرستید (مثلاً:\n۱ ماهه: ۱۰۰ هزار تومان\n۳ ماهه: ۲۵۰ هزار تومان)\nبرای پاک کردن: <code>-</code>", ikb([BACK]))
        elif act == "set_welcome":
            set_state(rec, {"k": "a_set_welcome"})
            send(chat, "👋 متن خوش‌آمد جدید را بفرستید. (تگ‌های HTML ساده مثل &lt;b&gt; مجازند.) برای پیش‌فرض: <code>-</code>", ikb([BACK]))
        elif act == "welcome_reset":
            db.data["settings"]["welcome_text"] = ""
            answer_text = "✅ پیش‌فرض شد"
        elif act == "backup":
            send_backup(chat)
        elif act == "restore":
            set_state(rec, {"k": "a_restore"})
            send(chat, "♻️ فایل پشتیبان دیتابیس ربات (dorbargardoon-bot-backup-….json) را بفرستید.\n"
                       "⚠️ اطلاعات فعلی با محتوای فایل جایگزین می‌شود (یک نسخه از وضعیت فعلی هم نگه داشته می‌شود).", ikb([BACK]))
        elif act == "fimp" or act == "frestore":
            st = rec.get("state") or {}
            if st.get("k") == "a_file_choice":
                raw = download(st["file_id"]).decode("utf-8-sig", errors="replace")
                if act == "frestore":
                    set_state(rec, None)
                    do_restore(chat, rec, raw)
                else:
                    added, dup, bad = import_codes(raw)
                    set_state(rec, None)
                    send(chat, f"📤 ➕ جدید: {J.fa(added)}  |  ♻️ تکراری: {J.fa(dup)}  |  ❌ نامعتبر: {J.fa(bad)}\n"
                               f"📒 مجموع دفتر: {J.fa(len(db.data['ledger']))}", ikb([[btn("📒 دفتر اشتراک‌ها", "a:led")], BACK]))
        elif act == "led":
            t, kb = ledger_view()
            edit(chat, mid, t, kb)
        elif act == "led_add":
            set_state(rec, {"k": "a_led_add"})
            send(chat, "➕ <b>افزودن دستی به دفتر</b>\nیک یا چند کد BNM1 را بفرستید (هر کد در یک خط). "
                       "هر متنی کنار کد در همان خط بنویسید (مثلاً نام مشتری) به‌عنوان توضیح ذخیره می‌شود.\n"
                       "اطلاعات دستگاه، نوع و مدت از خود کد خوانده و امضای آن بررسی می‌شود.", ikb([BACK]))
        elif act == "led_imp":
            set_state(rec, {"k": "a_led_imp"})
            send(chat, "📤 فایل پشتیبان سازندهٔ کد (binam-ledger-….json یا dorbargardoon-ledger-….json) یا فایل CSV آن را بفرستید.\n"
                       "فقط کدهای جدید اضافه می‌شوند؛ تکراری‌ها نادیده گرفته می‌شوند.", ikb([BACK]))
        elif act == "led_find":
            set_state(rec, {"k": "a_led_find"})
            send(chat, "🔎 کد دستگاه، نام/یوزرنیم مشتری، سریال یا آیدی عددی را بفرستید:", ikb([BACK]))
        elif act == "led_json":
            send_file(chat, time.strftime("dorbargardoon-ledger-%Y-%m-%d.json"), ledger_json().encode("utf-8"),
                      f"📒 دفتر اشتراک‌ها ({J.fa(len(db.data['ledger']))} کد) — قابل ورود در سازندهٔ کد HTML")
        elif act == "led_csv":
            send_file(chat, time.strftime("dorbargardoon-ledger-%Y-%m-%d.csv"), ledger_csv(),
                      "📒 دفتر اشتراک‌ها — CSV (با اکسل باز می‌شود)")
        else:
            answer_text = "?"
    except TGError as e:
        note_error(f"admin cb {data}: {e}")
        answer_text = "خطا: " + str(e)[:150]
    db.save()
    answer(cb["id"], answer_text)


# ============================================================ message router
def handle_message(msg):
    frm = msg.get("from")
    if not frm or msg.get("chat", {}).get("type") != "private":
        return
    rec = user_rec(frm)
    chat = msg["chat"]["id"]
    uid = frm["id"]
    text = msg.get("text", "") or ""

    if text.startswith("/id"):
        send(chat, f"🆔 آیدی عددی شما: <code>{uid}</code>")
        return
    if not ADMIN_IDS and text.startswith("/start"):
        send(chat, f"⚙️ ربات هنوز تنظیم نشده.\nآیدی عددی شما: <code>{uid}</code>\n"
                   "این عدد را در تنظیمات Space به‌عنوان ADMIN_ID قرار دهید.")
        return
    if rec.get("blocked") and not is_admin(uid):
        if not rec.get("told_blocked"):
            rec["told_blocked"] = True
            send(chat, "⛔️ دسترسی شما به ربات محدود شده است.")
            db.save()
        return

    if is_admin(uid):
        try:
            if handle_admin_message(msg, rec):
                db.save()
                return
        except TGError as e:
            note_error(f"admin msg: {e}")
            send(chat, f"❌ خطا: {esc(e)}")
            return

    st = rec.get("state") or {}
    k = st.get("k")
    if text.startswith("/start"):
        set_state(rec, None)
        handle_start(chat, rec, text[6:].strip())
    elif text in ("/cancel", BTN_CANCEL, "انصراف"):
        set_state(rec, None)
        send(chat, "❎ لغو شد.", main_kb(uid))
    elif text in (BTN_TRIAL, "/trial"):
        start_trial(chat, rec)
    elif text in (BTN_BUY, "/buy"):
        start_buy(chat, rec)
    elif text in (BTN_SUP, "/support"):
        start_support(chat, rec)
    elif text in (BTN_APK, BTN_APK_OLD, "/app", "/android"):
        send_apk(chat, rec)
        if text == BTN_APK_OLD:
            send(chat, "✨ منوی ربات به‌روز شد — حالا نسخهٔ 🍏 آیفون هم داریم!", main_kb(uid))
    elif text in (BTN_IOS, "/iphone", "/ios"):
        set_state(rec, None)
        ios_menu(chat, rec)
    elif text in (BTN_WIN, "/windows", "/win"):
        set_state(rec, None)
        send_win(chat, rec)
    elif text in (BTN_CHROME, "/chrome", "/edge"):
        set_state(rec, None)
        chrome_menu(chat, rec)
    elif text == "/help":
        send_welcome(chat, rec)
    elif k == "trial_dev" and text:
        do_trial(chat, rec, text)
    elif k == "buy_dev" and text:
        buy_got_device(chat, rec, text)
    elif k == "buy_until" and text:
        ts = J.parse_date(text)
        if not ts or ts <= time.time() + 3600:
            send(chat, "⚠️ تاریخ نامعتبر یا گذشته است.\nمثال: <code>1405/12/29</code> یا <code>2027-03-20</code>", ikb([CANCEL_ROW]))
        else:
            buy_create_request(chat, rec, st["dev"], "until", f"تا {J.jalali(ts, False)}", L.T_UNTIL, ts)
    elif k == "buy_custom" and text:
        sec = parse_duration(text)
        if not sec:
            send(chat, "⚠️ مدت نامعتبر است. مثال: <code>45 روز</code> یا <code>2 ماه</code>", ikb([CANCEL_ROW]))
        else:
            buy_create_request(chat, rec, st["dev"], "custom", J.dur(sec), L.T_DUR, sec)
    elif k == "buy_plan" and text and not text.startswith("/"):
        send(chat, "👆 لطفاً نوع اشتراک را از دکمه‌های پیام قبل انتخاب کنید، یا «انصراف» را بزنید.")
    elif is_admin(uid):
        send(chat, "ℹ️ برای پاسخ به یک کاربر، روی پیام او <b>Reply</b> بزنید یا از «🛠 پنل مدیریت» استفاده کنید.", main_kb(uid))
    elif text.startswith("/"):
        send_welcome(chat, rec)
    else:
        forward_to_admins(msg, rec)
    db.save()


def handle_update(upd):
    try:
        with db.lock:
            if "message" in upd:
                handle_message(upd["message"])
            elif "callback_query" in upd:
                handle_callback(upd["callback_query"])
            elif "my_chat_member" in upd:
                m = upd["my_chat_member"]
                rec = get_user(m["from"]["id"])
                if rec:
                    rec["bot_blocked"] = m.get("new_chat_member", {}).get("status") in ("kicked", "left")
                    db.save()
    except Exception as e:
        note_error(f"update failed: {e}")
        traceback.print_exc()


# ============================================================ runtime
UPDATES = queue.Queue()
_seen = []


def worker():
    while True:
        upd = UPDATES.get()
        uid = upd.get("update_id")
        if uid in _seen:
            continue
        _seen.append(uid)
        del _seen[:-500]
        handle_update(upd)


class Handler(BaseHTTPRequestHandler):
    server_version = "dorbargardoon"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _ios(self, method):
        """iPhone gateway (DoH + profiles). Never touches db.lock. Returns True if handled."""
        path, _, query = self.path.partition("?")
        if not (path.startswith("/dns-query/") or path.startswith("/ios/")):
            return False
        body = b""
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(n, 8192)) if n > 0 else b""
        xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        ip = xff or self.client_address[0]
        try:
            r = IOS.handle_http(method if method != "HEAD" else "GET", path, query, dict(self.headers.items()), body, ip)
        except Exception as e:
            note_error(f"ios http: {e}")
            r = (500, {"Content-Type": "text/plain"}, b"error")
        if r is None:
            return False
        code, hdrs, out = r
        out = out.encode("utf-8") if isinstance(out, str) else (out or b"")
        self.send_response(code)
        for k_, v_ in hdrs.items():
            v_ = str(v_)
            try:
                v_.encode("latin-1")
            except UnicodeEncodeError:
                v_ = v_.encode("utf-8").decode("latin-1")
            self.send_header(k_, v_)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(out)
        return True

    def do_GET(self):
        if self._ios("GET"):
            return
        if self.path.startswith("/health"):
            return self._send(200, "ok", "text/plain")
        if self.path.startswith("/status.json"):
            st = {"ok": STATUS["telegram"].startswith("✅"), "telegram": STATUS["telegram"],
                  "transport": transport_label(), "storage": STATUS["storage"], "keepalive": STATUS["keepalive"],
                  "mode": MODE, "started": J.jalali(STATUS["started"]), "errors": STATUS["errors"][-5:],
                  "users": len(db.data["users"]) if db else 0, "codes": len(db.data["ledger"]) if db else 0,
                  "ios": ({"active": IOS.counts()[0]["active"], "online": IOS.counts()[1], "dns_today": IOS.STATE["today"],
                           "public_url": IOS.base_url()} if db else None)}
            return self._send(200, json.dumps(st, ensure_ascii=False), "application/json; charset=utf-8")
        errs = "".join(f"<li>{html.escape(e)}</li>" for e in STATUS["errors"][-5:])
        page = f"""<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Dorbargardoon bot</title><body style="font-family:Tahoma,sans-serif;background:#070a14;color:#eef;padding:24px">
<h2>🔁 ربات دوربرگردون</h2><p>وضعیت: <b>{'روشن ✅' if STATUS['telegram'].startswith('✅') else html.escape(STATUS['telegram'])}</b></p>
<p>مسیر اتصال: {html.escape(transport_label())}</p><p>حالت: {MODE}{(' — وبهوک: ' + html.escape(str(STATUS['webhook']))) if MODE == 'webhook' else ''}</p><p>ذخیره‌سازی: {html.escape(STATUS['storage'])}</p><p>بیدار نگه‌داشتن: {html.escape(STATUS['keepalive'])}</p>
<p>روشن از: {J.jalali(STATUS['started'])}</p>{('<h4>خطاهای اخیر</h4><ul>' + errs + '</ul>') if errs else ''}</body></html>"""
        self._send(200, page)

    def do_HEAD(self):
        if self._ios("HEAD"):
            return
        self._send(200, "")

    def do_POST(self):
        if self._ios("POST"):
            return
        if not WEBHOOK_SECRET or self.path.rstrip("/") != f"/tg/{WEBHOOK_SECRET}":
            return self._send(404, "not found", "text/plain")
        if self.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
            return self._send(403, "forbidden", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            upd = json.loads(self.rfile.read(n).decode("utf-8"))
            UPDATES.put(upd)
        except Exception:
            pass
        self._send(200, "ok", "text/plain")


def polling():
    offset = None
    while True:
        try:
            res = api("getUpdates", {"timeout": 20, "offset": offset,
                                     "allowed_updates": ["message", "callback_query", "my_chat_member"]}, timeout=35)
            STATUS["telegram"] = "✅ متصل (polling)"
            for u in res:
                offset = u["update_id"] + 1
                UPDATES.put(u)
        except Exception as e:
            STATUS["telegram"] = f"❌ خطا: {e}"
            if getattr(e, "code", 0) == 409:
                note_error("polling: 409 Conflict — یک نسخهٔ دیگر از ربات هم‌زمان در حال اجراست "
                           "(یا روی سرور ابری وبهوک دارد). آن را خاموش کنید.")
            else:
                note_error(f"polling: {e}")
            time.sleep(5)


def transport_label():
    if TRANSPORT == "telethon":
        import dor_tgclient
        return f"MTProto مستقیم با Telethon (مثل ربات قبلی، بدون پراکسی) — {dor_tgclient.STATE['status']}"
    if _EXT_BASE:
        return f"پراکسی/سرور خارجی: {API_BASE}"
    return "api.telegram.org (مستقیم)"


def keepalive_loop():
    """Free Spaces sleep after 48h without visits.  Polling bots get no visits, so the Space
    visits its own public address regularly (plus you can add an external cron ping)."""
    if not PUBLIC_URL or KEEPALIVE_MIN <= 0:
        STATUS["keepalive"] = "غیرفعال (آدرس عمومی Space مشخص نیست)"
        return
    time.sleep(60)
    while True:
        try:
            r = requests.get(f"{PUBLIC_URL}{KEEPALIVE_PATH}", timeout=30, headers={"User-Agent": "dorbargardoon-keepalive"})
            STATUS["keepalive"] = f"✅ هر {J.fa(int(KEEPALIVE_MIN))} دقیقه — آخرین: {J.jalali(time.time())} (HTTP {r.status_code})"
        except Exception as e:
            STATUS["keepalive"] = f"⚠️ پینگ خودکار ناموفق ({type(e).__name__}) — از cron-job.org استفاده کنید"
        time.sleep(KEEPALIVE_MIN * 60)


def daily_backup_loop():
    while True:
        time.sleep(60)
        try:
            last = db.data["meta"].get("last_backup", 0)
            if time.time() - last >= 24 * 3600 and ADMIN_IDS and STATUS["telegram"].startswith("✅"):
                db.data["meta"]["last_backup"] = int(time.time())
                db.save()
                for a in ADMIN_IDS:
                    send_backup(a, "🗓 پشتیبان خودکار روزانهٔ دیتابیس ربات")
        except Exception as e:
            note_error(f"daily backup: {e}")


def setup_telegram():
    """Connects to Telegram and starts receiving updates; retries forever."""
    delay = 3 if TRANSPORT == "telethon" else 5
    while True:
        try:
            me = api("getMe", timeout=20)
            ME.update(me)
            IOS.CFG["bot_username"] = ME.get("username") or IOS.CFG["bot_username"]
            STATUS["telegram"] = f"✅ متصل به @{me.get('username')}"
            break
        except Exception as e:
            STATUS["telegram"] = f"❌ اتصال به تلگرام ممکن نشد: {e}"
            hint = ""
            if getattr(e, "code", 0) == 401:
                hint = " — BOT_TOKEN اشتباه است یا باطل شده"
            elif TRANSPORT == "telethon":
                import dor_tgclient
                if not dor_tgclient.STATE["status"].startswith("✅"):
                    hint = f" — {dor_tgclient.STATE['status']}"
            note_error(f"getMe via {'Telethon' if TRANSPORT == 'telethon' else API_BASE}: {e}{hint}")
            time.sleep(delay)
            delay = min(delay * 2, 60)
    try:
        api("setMyCommands", {"commands": [
            {"command": "start", "description": "منوی اصلی"},
            {"command": "trial", "description": "تست رایگان"},
            {"command": "buy", "description": "خرید اشتراک"},
            {"command": "support", "description": "پیام به پشتیبانی"},
            {"command": "app", "description": "دریافت آخرین نسخه"},
            {"command": "cancel", "description": "انصراف"}]})
        for a in ADMIN_IDS:
            try:
                api("setMyCommands", {"scope": {"type": "chat", "chat_id": a}, "commands": [
                    {"command": "admin", "description": "پنل مدیریت"},
                    {"command": "stats", "description": "وضعیت و آمار"},
                    {"command": "backup", "description": "دریافت پشتیبان"},
                    {"command": "start", "description": "منوی اصلی"},
                    {"command": "cancel", "description": "انصراف"}]})
            except Exception:
                pass
    except Exception as e:
        note_error(f"setMyCommands: {e}")
    if MODE == "webhook":
        url = f"{PUBLIC_URL}/tg/{WEBHOOK_SECRET}"
        while True:
            try:
                api("setWebhook", {"url": url, "secret_token": WEBHOOK_SECRET, "max_connections": 20,
                                   "allowed_updates": ["message", "callback_query", "my_chat_member"]})
                STATUS["webhook"] = "✅ ثبت شد"
                break
            except Exception as e:
                STATUS["webhook"] = f"❌ {e}"
                note_error(f"setWebhook: {e}")
                time.sleep(30)
    elif MODE == "telethon":
        STATUS["telegram"] = f"✅ متصل به @{ME.get('username')} (Telethon)"
    else:
        try:
            api("deleteWebhook", {"drop_pending_updates": False})
        except Exception:
            pass
        threading.Thread(target=polling, daemon=True).start()
    db.update_label()
    warn = "" if db.persistent else ("\n\n⚠️ <b>هشدار:</b> ذخیره‌سازی دائمی نیست و با ری‌استارت پاک می‌شود. "
                                     "یک Storage Bucket با مسیر <code>/data</code> متصل کنید یا Secret <code>HF_TOKEN</code> بسازید.")
    keywarn = "" if signer.matches_app else "\n\n❌ <b>کلید امضا با برنامه مطابقت ندارد؛ کدها در برنامه کار نمی‌کنند!</b>"
    notify_admins(f"🤖 ربات دوربرگردون روشن شد (v{VERSION})\n🛰 {esc(transport_label())}\n💾 {esc(STATUS['storage'])}\n"
                  f"👥 کاربران: {J.fa(len(db.data['users']))} | 📒 کدها: {J.fa(len(db.data['ledger']))}{warn}{keywarn}",
                  ikb([[btn(BTN_ADMIN, "a:menu")]]))


def start_telethon():
    import dor_tgclient

    def load_session():
        return db.data["meta"].get("tg_session", "")

    # called from the Telethon event loop → never block on db.lock (a worker may hold it while sending)
    def _bg_save():
        threading.Thread(target=db.save, daemon=True).start()

    def save_session(s):
        if s and db.data["meta"].get("tg_session") != s:
            db.data["meta"]["tg_session"] = s
            _bg_save()

    def get_peer(uid):
        return (db.data.get("peers") or {}).get(str(uid))

    def set_peer(uid, h):
        db.data.setdefault("peers", {})[str(uid)] = int(h)
        _bg_save()

    b = dor_tgclient.Bridge(TG_API_ID, TG_API_HASH, BOT_TOKEN, on_update=UPDATES.put, error_cls=TGError,
                        load_session=load_session, save_session=save_session, get_peer=get_peer, set_peer=set_peer)
    b.start()
    return b


def main():
    global db, signer
    global TT
    missing = [k for k, v in (("DOR_BOT_TOKEN", BOT_TOKEN), ("LICENSE_PRIVATE_KEY", _env("LICENSE_PRIVATE_KEY"))) if not v]
    if MODE == "webhook" and not WEBHOOK_SECRET:
        missing.append("WEBHOOK_SECRET")
    if not _EXT_BASE and SPACE_ID and not (TG_API_ID and TG_API_HASH):
        # On Hugging Face the direct Bot API is blocked → MTProto (Telethon) needs api_id/api_hash.
        missing.append("TG_API_ID, TG_API_HASH (از my.telegram.org)")
    db = DB(DATA_DIR)
    ios_configure()
    if not missing:
        try:
            signer = L.Signer(_env("LICENSE_PRIVATE_KEY"))
        except Exception as e:
            missing.append(f"LICENSE_PRIVATE_KEY (invalid: {e})")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("HTTP server on :%s  data=%s", PORT, STATUS["storage"])
    if missing:
        STATUS["telegram"] = "⚙️ تنظیمات ناقص: " + ", ".join(missing)
        note_error("missing config: " + ", ".join(missing))
        while True:
            time.sleep(3600)
    if not signer.matches_app:
        note_error("LICENSE_PRIVATE_KEY does not match the app's public key!")
    if TRANSPORT == "telethon":
        TT = start_telethon()
    log.info("telegram transport=%s mode=%s", TRANSPORT, MODE)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=daily_backup_loop, daemon=True).start()
    threading.Thread(target=keepalive_loop, daemon=True).start()
    threading.Thread(target=ios_events_loop, daemon=True).start()
    setup_telegram()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
