#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
حافظه‌ی دائمی ربات — نسخهٔ چندپشتیبانه (GitHub ↔ Hugging Face)

چرا لازم است؟ روی GitHub Actions (و هر کانتینر رایگان دیگر) پوشهٔ /tmp با هر
ری‌استارت پاک می‌شود؛ این ماژول داده‌های مهم را بیرون از کانتینر نگه می‌دارد و
موقع روشن شدن برمی‌گرداند.

پشتیبان‌ها (به ترتیب اولویت):
  1) GitHub  — مخزن خصوصی با یک فایل data/bundle.json
               متغیرها: GH_PAT (توکن) و GH_DATA_REPO (اختیاری؛ پیش‌فرض <owner>/bot-data)
               اگر مخزن وجود نداشته باشد، خودکار **خصوصی** ساخته می‌شود.
  2) Hugging Face — همان روش قدیمی (dataset خصوصی، هر فایل جدا)
               متغیرها: HF_TOKEN + DATA_REPO (یا SPACE_ID روی اسپیس)
  3) هیچ‌کدام — حالت موقت (/tmp) + حافظهٔ تلگرامی tgstore.py که مستقل کار می‌کند

متغیرهای اختیاری:
  PERSIST_SECONDS   فاصلهٔ ذخیره‌سازی (پیش‌فرض ۹۰؛ روی GitHub Actions بگذار ۳۰)
  GH_BRANCH         شاخهٔ مخزن داده (اگر خودش تشخیص ندهد)
"""

import atexit
import base64
import json
import os
import signal
import threading
import time
from pathlib import Path

# فایل‌های مهم که باید زنده بمانند
FILES = [
    "bargha_regions.json",   # منطقه‌های کاربران
    "bargha_state.json",     # اطلاعیه‌ها + وضعیت
    "comp_history.json",     # تاریخچه فشرده‌سازی
    "comp_users.json",       # کاربران
    "comp_wip.json",         # کارهای فشرده‌سازی نیمه‌کاره (اعتذار پس از ری‌استارت)
    "gold_data.json",        # اکانت‌های طلایی و زیرمجموعه‌ها
    "feat_flags.json",       # کلیدهای فعال/غیرفعال کردن قابلیت‌ها
    "comp_admins.json",      # مدیران شناخته‌شده
    "anten_votes.json",      # رأی‌های آنتن
    "anten_custom.json",     # پروکسی‌های آنتن
    "user_session.json",     # نشست اکانت‌خوان
    "tgstore_msgid.json",    # شناسه پیام حافظه
    "tgstore_group_msgid.json",
    "group_cfg.json",        # تنظیمات گروه «مای ربات»
    "sd_sessions.json",      # نشست‌های عکسِ خودموی کاربران
    "dl_sessions.json",      # نشست‌های بخش کانال محدود (دانلود از کانال‌های محدود)
    "sd_settings.json",      # حالت دریافت مدیا
    "extra_sources.json",    # کانال‌های منبع اضافه‌ی مدیر
    "subs.json",              # اشتراک‌ها و کلیدها
    "yt_cookie.json",         # کوکی یوتیوب (ضد بلاک)
    "magent_ctx.json",        # حافظه‌ی دستیار /m
    "aigroup.json",           # تنظیم گروه هوش مصنوعی
    "autoreply.json",         # منشی‌های فعال
    "bot_session.json",       # نشست ربات (ضد FloodWait تلگرام)
    "afk.json",               # حالت خارج از دسترس
    "filters.json",           # فیلترهای خودکار پیوی
    "welcome.json",           # گروه‌های خوش‌آمدگویی
    "notes.json",             # یادداشت‌های شخصی
    "warns.json",             # اخطارهای گروه
    "userkeys.json",          # کلیدهای شخصی
    "support_inbox.json",     # صندوق پیام پشتیبانی
]

# ⚠️ در حالت عادی دست نزن: bot.py و tgstore.py مسیر /tmp را سخت‌کد دارند،
#    پس PERSIST_DIR فقط برای تست محلی است (در محیط واقعی باید /tmp بماند).
TMP = Path(os.environ.get("PERSIST_DIR", "/tmp"))

# ── Hugging Face (مسیر قدیمی) ────────────────────────────────────────────
REPO = os.environ.get("SPACE_ID", "").strip()          # مثل: Matin0013/utube
TOKEN = (os.environ.get("HF_TOKEN")
         or os.environ.get("HUGGING_FACE_HUB_TOKEN")
         or os.environ.get("HF_API_TOKEN") or "").strip()
DATA_REPO = os.environ.get("DATA_REPO", "").strip()

# ── GitHub (مسیر جدید، بدون نیاز به HF) ───────────────────────────────────
GH_PAT = (os.environ.get("GH_PAT") or "").strip()
GH_DATA_REPO = (os.environ.get("GH_DATA_REPO") or "").strip()
GH_API = "https://api.github.com"
_gh_cache = {"owner": "", "branch": "", "ensured": False}

INTERVAL = max(15, int(float(os.environ.get("PERSIST_SECONDS", "90") or 90)))

_dirty = False
_lock = threading.Lock()


def _log(msg):
    print(f"[persist] {msg}", flush=True)


# ══════════════════════════════════════════════════════════════ انتخاب پشتیبان
def _data_repo():
    """نام مخزن دادهٔ HF: <کاربر>/bot-data"""
    if DATA_REPO:
        return DATA_REPO
    if REPO and "/" in REPO:
        return REPO.split("/")[0] + "/bot-data"
    return ""


def _backend() -> str:
    if GH_PAT:
        return "github"
    if TOKEN and _data_repo():
        return "hf"
    return ""


def status_reason() -> str:
    b = _backend()
    if b == "github":
        return f"✅ فعال (GitHub → {GH_DATA_REPO or '<owner>/bot-data'})"
    if b == "hf":
        return "✅ فعال (HF → " + _data_repo() + ")" + ("" if REPO else " — بیرون از HF")
    if GH_PAT and not GH_DATA_REPO:
        return "❌ GH_PAT هست ولی نام کاربری گرفته نشد — GH_DATA_REPO را دستی بگذار"
    if TOKEN and not _data_repo():
        return "❌ توکن HF هست ولی DATA_REPO/SPACE_ID نیست"
    return "❌ هیچ پشتیبانی فعال نیست — GH_PAT (گیت‌هاب) یا HF_TOKEN+DATA_REPO را بگذار"


def available() -> bool:
    return bool(_backend())


# ══════════════════════════════════════════════════════════════ پشتیبان GitHub
def _gh_headers(raw=False):
    h = {"Authorization": f"Bearer {GH_PAT}",
         "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28",
         "User-Agent": "tg-bot-persist"}
    return h


def _gh_repo_full() -> str:
    """owner/name — اگر GH_DATA_REPO نباشد، از روی توکن، owner را می‌گیرد"""
    if GH_DATA_REPO and "/" in GH_DATA_REPO:
        return GH_DATA_REPO.strip("/")
    if _gh_cache["owner"]:
        return f"{_gh_cache['owner']}/bot-data"
    try:
        import requests
        r = requests.get(f"{GH_API}/user", headers=_gh_headers(), timeout=20)
        if r.status_code == 200:
            _gh_cache["owner"] = (r.json().get("login") or "").strip()
        else:
            _log(f"گرفتن نام کاربری گیت‌هاب ناموفق (کد {r.status_code}) — "
                 + ("توکن GH_PAT نامعتبر یا منقضی است" if r.status_code in (401, 403)
                    else "دسترسی شبکه را چک کن") + "؛ یا GH_DATA_REPO را دستی بگذار")
    except Exception as e:
        _log(f"گرفتن owner از گیت‌هاب ناموفق: {e}")
    return f"{_gh_cache['owner']}/bot-data" if _gh_cache["owner"] else ""


def _gh_ensure_repo(full: str) -> bool:
    """مخزن داده را (خصوصی) بساز اگر نیست"""
    if _gh_cache["ensured"]:
        return True
    try:
        import requests
        r = requests.get(f"{GH_API}/repos/{full}", headers=_gh_headers(), timeout=20)
        if r.status_code == 200:
            _gh_cache["branch"] = (r.json().get("default_branch") or "main").strip()
            _gh_cache["ensured"] = True
            return True
        if r.status_code == 404:
            name = full.split("/", 1)[1]
            r2 = requests.post(f"{GH_API}/user/repos", headers=_gh_headers(), timeout=40,
                               json={"name": name, "private": True, "auto_init": True,
                                     "description": "💾 داده‌های ربات تلگرام (خودکار — خصوصی)"})
            if r2.status_code in (200, 201):
                _gh_cache["branch"] = (r2.json().get("default_branch") or "main").strip()
                _gh_cache["ensured"] = True
                _log(f"مخزن خصوصی «{full}» ساخته شد ✓")
                return True
            _log(f"ساخت مخزن ناموفق ({r2.status_code}): {r2.text[:160]}")
        else:
            hint = ("توکن GH_PAT معتبر نیست یا دسترسی repo ندارد"
                    if r.status_code in (401, 403) else "نام مخزن/توکن را چک کن")
            _log(f"دسترسی به مخزن {full} ناموفق ({r.status_code}) — {hint}")
    except Exception as e:
        _log(f"خطای GitHub: {e}")
    return False


def _gh_branch(full: str) -> str:
    if _gh_cache["branch"]:
        return _gh_cache["branch"]
    if os.environ.get("GH_BRANCH"):
        return os.environ["GH_BRANCH"].strip()
    try:
        import requests
        r = requests.get(f"{GH_API}/repos/{full}", headers=_gh_headers(), timeout=20)
        if r.status_code == 200:
            _gh_cache["branch"] = (r.json().get("default_branch") or "main").strip()
    except Exception:
        pass
    return _gh_cache["branch"] or "main"


def _gh_pull() -> int:
    import requests
    full = _gh_repo_full()
    if not full:
        _log("نام مخزن داده مشخص نشد — GH_DATA_REPO را دستی بگذار (مثل: yourname/bot-data)")
        return 0
    if not _gh_ensure_repo(full):
        return 0
    try:
        r = requests.get(f"{GH_API}/repos/{full}/contents/data/bundle.json",
                         headers=_gh_headers(raw=True), timeout=40)
        if r.status_code != 200:
            _log(f"bundle.json در {full} نیست (کد {r.status_code}) — اولین ذخیره ساخته می‌شود")
            return 0
        data = json.loads(r.text)
        ok = 0
        for name in FILES:
            v = data.get(name)
            if v is None:
                continue
            try:
                (TMP / name).write_text(json.dumps(v, ensure_ascii=False))
                ok += 1
            except Exception:
                pass
        return ok
    except Exception as e:
        _log(f"بازیابی از GitHub ناموفق: {e}")
        return 0


def _gh_push() -> bool:
    import requests
    full = _gh_repo_full()
    if not full:
        _log("نام مخزن داده مشخص نشد — GH_DATA_REPO را دستی بگذار (مثل: yourname/bot-data)")
        return False
    if not _gh_ensure_repo(full):
        return False
    bundle = {}
    for name in FILES:
        p = TMP / name
        if p.exists():
            try:
                bundle[name] = json.loads(p.read_text() or "null")
            except Exception:
                pass
    if not bundle:
        return False
    url = f"{GH_API}/repos/{full}/contents/data/bundle.json"
    body = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    for attempt in (1, 2):
        try:
            sha = None
            r = requests.get(url, headers=_gh_headers(), timeout=30)
            if r.status_code == 200:
                sha = (r.json() or {}).get("sha")
            payload = {
                "message": f"💾 backup {time.strftime('%Y-%m-%d %H:%M:%S')} ({len(bundle)} فایل)",
                "content": base64.b64encode(body).decode("ascii"),
                "branch": _gh_branch(full),
            }
            if sha:
                payload["sha"] = sha
            r = requests.put(url, headers=_gh_headers(), json=payload, timeout=60)
            if r.status_code in (200, 201):
                return True
            if r.status_code == 409 and attempt == 1:      # sha عوض شده → دوباره
                time.sleep(2)
                continue
            _log(f"ذخیره در GitHub ناموفق ({r.status_code}): {r.text[:200]}")
            return False
        except Exception as e:
            _log(f"خطای ذخیرهٔ GitHub: {e}")
            if attempt == 1:
                time.sleep(2)
    return False


# ══════════════════════════════════════════════════════════════ پشتیبان HF (قدیمی)
def _hf_pull() -> int:
    try:
        from huggingface_hub import hf_hub_download
        dr = _data_repo()

        def _pull_from(repo_id, repo_type):
            ok = 0
            for name in FILES:
                try:
                    p = hf_hub_download(repo_id=repo_id, filename=f"data/{name}",
                                        repo_type=repo_type, token=TOKEN)
                    data = json.loads(Path(p).read_text())
                    (TMP / name).write_text(json.dumps(data, ensure_ascii=False))
                    ok += 1
                except Exception:
                    pass
            return ok

        ok = _pull_from(dr, "dataset") if dr else 0
        if not ok and REPO:
            ok = _pull_from(REPO, "space")   # مهاجرت از اسپیس قدیمی
        return ok
    except Exception as e:
        _log(f"بازیابی HF ناموفق: {e}")
        return 0


def _hf_push() -> bool:
    try:
        from huggingface_hub import CommitOperationAdd, HfApi
        dr = _data_repo()
        if not dr:
            return False
        api = HfApi(token=TOKEN)
        api.create_repo(repo_id=dr, repo_type="dataset", private=True, exist_ok=True)
        ops = []
        for name in FILES:
            p = TMP / name
            if p.exists():
                ops.append(CommitOperationAdd(path_in_repo=f"data/{name}", path_or_fileobj=str(p)))
        if ops:
            api.create_commit(repo_id=dr, repo_type="dataset", operations=ops,
                              commit_message="backup: bot data 💾")
        return True
    except Exception as e:
        _log(f"ذخیرهٔ HF ناموفق: {e}")
        return False


# ══════════════════════════════════════════════════════════════ رابط عمومی
def pull():
    """بازیابی داده‌ها موقع روشن شدن ربات"""
    b = _backend()
    if not b:
        _log("حالت موقت — " + status_reason())
        return
    ok = _gh_pull() if b == "github" else _hf_pull()
    _log(f"{ok} فایل از {b.upper()} برگشت" + ("" if ok else " (خالی بود — اولین اجرا؟)"))


def mark_dirty():
    global _dirty
    with _lock:
        _dirty = True


def _push_now() -> bool:
    b = _backend()
    if b == "github":
        return _gh_push()
    if b == "hf":
        return _hf_push()
    return False


def _flush(tag: str = "exit"):
    """ذخیرهٔ فوری — تا داده‌های آخرین دقایق با ری‌استارت نپرند"""
    global _dirty
    with _lock:
        d = _dirty
        _dirty = False
    if not (d and available()):
        return
    if _push_now():
        _log(f"💾 ذخیرهٔ پایانی ({tag}) انجام شد")


def _worker():
    global _dirty
    while True:
        time.sleep(INTERVAL)
        with _lock:
            d = _dirty
            _dirty = False
        if not d or not available():
            continue
        _push_now()


def _sig_handler(signum, _frame):
    _flush(f"signal {signum}")
    try:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    except Exception:
        os._exit(0)


def start():
    threading.Thread(target=_worker, daemon=True).start()
    # 🔧 ذخیرهٔ پایانی: هم در خروج عادی، هم با SIGTERM (timeout در GitHub Actions)
    try:
        atexit.register(_flush, "atexit")
    except Exception:
        pass
    for _s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_s, _sig_handler)
        except Exception:
            pass
