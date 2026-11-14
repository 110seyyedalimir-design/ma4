#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
حافظه‌ی دائمی ربات — ذخیره‌ی داده‌ها در مخزن خود اسپیس (Hugging Face)

روی اسپیس رایگان، فایل‌ها با هر ری‌استارت پاک می‌شوند؛ این ماژول داده‌های
مهم (منطقه‌ها، کاربران، تاریخچه، وضعیت اطلاعیه‌ها) را به‌صورت دوره‌ای در
مخزن خودِ اسپیس commit می‌کند و موقع روشن شدن دوباره برمی‌گرداند.
اگر توکن موجود نباشد، بی‌صدا به حالت موقت (/tmp) برمی‌گردد.
"""

import atexit
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

REPO = os.environ.get("SPACE_ID", "").strip()          # مثل: Matin0013/utube
TOKEN = (os.environ.get("HF_TOKEN")
         or os.environ.get("HUGGING_FACE_HUB_TOKEN")
         or os.environ.get("HF_API_TOKEN") or "").strip()

# ══════════════════════════════════════════════════════════════════
# 🔧 فیکس ری‌استارت‌های مکرر:
# هر کامیت به مخزن خودِ اسپیس → هاتینگ‌فیس اسپیس را ری‌بیلد/ری‌استارت می‌کند!
# (این باعث می‌شد ربات هر ۹۰ ثانیه خاموش/روشن شود و دانلودها وسط راه بمیرند)
# راه‌حل: داده‌ها به مخزنِ جداگانه‌ی «bot-data» می‌روند — کامیت به مخزن دیگر
# هیچ ری‌استارتی ندارد. مخزن در اولین اجرا خودکار ساخته می‌شود.
# ══════════════════════════════════════════════════════════════════
DATA_REPO = os.environ.get("DATA_REPO", "").strip()     # قابل تنظیم دستی


def _data_repo():
    """نام مخزن داده: <کاربر>/bot-data"""
    if DATA_REPO:
        return DATA_REPO
    if REPO and "/" in REPO:
        return REPO.split("/")[0] + "/bot-data"
    return ""

_dirty = False
_lock = threading.Lock()


def status_reason() -> str:
    """چرا مخزن داده فعال/غیرفعال است"""
    if _data_repo() and TOKEN:
        return "✅ فعال" + ("" if REPO else " (با DATA_REPO — بیرون از HF)")
    if not _data_repo():
        return "❌ نه SPACE_ID هست نه DATA_REPO — متغیر DATA_REPO را تنظیم کنید (مثل: yourname/bot-data)"
    return "❌ توکن HF نیست — متغیر/سکرت HF_TOKEN را بسازید (توکن Write)"


def available() -> bool:
    # 🔧 پرتابل: بیرون از هاتینگ‌فیس، SPACE_ID وجود ندارد؛ فقط DATA_REPO + HF_TOKEN لازم است
    return bool(_data_repo() and TOKEN)


def pull():
    """بازیابی داده‌ها از مخزن اسپیس (موقع روشن شدن ربات)"""
    if not available():
        print("[persist] حالت موقت — توکن/اسپیس‌آیدی نیست، داده‌ها تا ری‌استارت می‌مانند", flush=True)
        return
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
                    Path(f"/tmp/{name}").write_text(json.dumps(data, ensure_ascii=False))
                    ok += 1
                except Exception:
                    pass
            return ok

        ok = 0
        if dr:
            ok = _pull_from(dr, "dataset")
        if not ok and REPO:
            # مهاجرت: هنوز مخزن داده خالی است — از اسپیسِ قدیمی برگردان
            ok = _pull_from(REPO, "space")
            if ok:
                global _dirty
                _dirty = True      # همان‌ها را در مخزن داده‌ی جدید ذخیره کن
        print(f"[persist] {ok} فایل از مخزن برگشت", flush=True)
    except Exception as e:
        print(f"[persist] بازیابی ناموفق: {e}", flush=True)


def mark_dirty():
    global _dirty
    with _lock:
        _dirty = True


def _push_now():
    from huggingface_hub import CommitOperationAdd, HfApi
    dr = _data_repo()
    if not dr:
        return
    api = HfApi(token=TOKEN)
    # مخزن داده اگر نباشد، خودکار ساخته می‌شود (خصوصی)
    try:
        api.create_repo(repo_id=dr, repo_type="dataset", private=True, exist_ok=True)
    except Exception as e:
        print(f"[persist] ساخت مخزن داده ناموفق: {e}", flush=True)
        return
    ops = []
    for name in FILES:
        p = Path(f"/tmp/{name}")
        if p.exists():
            ops.append(CommitOperationAdd(path_in_repo=f"data/{name}",
                                          path_or_fileobj=str(p)))
    if ops:
        api.create_commit(repo_id=dr, repo_type="dataset", operations=ops,
                          commit_message="backup: bot data 💾")


# فاصلهٔ ذخیره‌سازی (ثانیه) — روی GitHub Actions که هر ~۶ ساعت ری‌استارت دارد، ۳۰ بگذارید
INTERVAL = max(15, int(float(os.environ.get("PERSIST_SECONDS", "90") or 90)))


def _worker():
    global _dirty
    while True:
        time.sleep(INTERVAL)
        with _lock:
            d = _dirty
            _dirty = False
        if not d or not available():
            continue
        try:
            _push_now()
        except Exception as e:
            print(f"[persist] ذخیره ناموفق: {e}", flush=True)


def _flush(tag: str = "exit"):
    """ذخیرهٔ فوری موقع خاموش شدن — تا داده‌های آخرین دقایق با ری‌استارت نپرند"""
    global _dirty
    with _lock:
        d = _dirty
        _dirty = False
    if not (d and available()):
        return
    try:
        _push_now()
        print(f"[persist] 💾 ذخیرهٔ پایانی ({tag}) انجام شد", flush=True)
    except Exception as e:
        print(f"[persist] ذخیرهٔ پایانی ناموفق: {e}", flush=True)


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
