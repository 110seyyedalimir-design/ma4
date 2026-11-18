#!/usr/bin/env python3
# ✅ نسخه‌ی درست: v25.43.0 — این خط را ببینی یعنی آپلود درست است
# -*- coding: utf-8 -*-
"""
ربات تلگرام فشرده‌سازی فایل (File Compressor Bot) — نسخه کامل

- ویدیو، عکس یا فایل صوتی بفرست → کم‌حجم‌شده تحویل بگیر (تا ~۱٫۸ گیگابایت)
- درصد پیشرفت زنده: دریافت، فشرده‌سازی و ارسال
- کیبورد دکمه‌ای دائمی: راهنما / وضعیت ربات / پنل مدیریت (فقط مدیر)
- پنل مدیر (/admin): کارهای در جریان + تاریخچه با لینک کاربر و کد فایل
- لیست کاربران (/users) و دریافت فایل کم‌حجم خودِ کاربر (/get کد)
"""

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from collections import deque
from pathlib import Path

from PIL import Image, ImageOps
from telethon import Button, TelegramClient, events
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

import bargha
import persist
import tgstore
# قلاب: آنتن وقتی مخزن خالی است، این را صدا می‌زند (bot تنظیم می‌کند)
async def _anten_refresh_hook():
    try:
        if user_client and user_client.is_connected():
            return await _backfill_all_sources()
    except Exception:
        pass
    return (0, 0)


try:
    import ytube
    YT_OK = True
except Exception as _e:
    YT_OK = False
    print("⚠️ ytube.py:", _e, flush=True)
try:
    import aichat
    AI_OK = True
except Exception as _e:
    AI_OK = False
    print("⚠️ aichat.py:", _e, flush=True)
try:
    import insta
    INSTA_OK = True
except Exception as _e:
    INSTA_OK = False
    print("⚠️ insta.py:", _e, flush=True)
try:
    import tools
    TOOLS_OK = True
except Exception as _e:
    TOOLS_OK = False
    print("⚠️ tools.py:", _e, flush=True)
try:
    import anten
    ANTEN_OK = True
except Exception as _e:
    ANTEN_OK = False
    print("⚠️ anten.py بارگذاری نشد:", _e, flush=True)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
os.environ["AI_API_KEY"] = os.environ.get("AI_API_KEY", "").strip().strip('"').strip("'").removeprefix("Bearer ").strip()
BOT_VERSION = "v25.49.0"

# ══════════════════════════════════════════════════════════════════
# 🔑 دو کلید مستقل:
# ۱) USERBOT_ENABLED (پیش‌فرض روشن) — اکانت‌خوان:
#    برق کاشان (خواندن کانال‌ها)، آنتن، مزاحم‌یاب، عکس خودمحو، /login
# ۲) AGENT_ENABLED (پیش‌فرض خاموش) — دستیار هوشمند روی اکانت شما:
#    /m و دستورات یوزربات (۸۹ دستور)، گروه AI، منشی، AFK، فیلتر،
#    خوش‌آمدگویی، ساعت زنده، اکشن‌های نمایشی، برودکست
# برای فعال‌سازی دستیار بعداً: Variables اسپیس → AGENT_ENABLED = 1
# ══════════════════════════════════════════════════════════════════
USERBOT_ENABLED = os.environ.get("USERBOT_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
AGENT_ENABLED = os.environ.get("AGENT_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
# ۳) SDSEND_ENABLED (پیش‌فرض روشن!) — فقط «ارسال خودموف به دیگران» (/sdto)
#    مستقل از دستیار: حتی وقتی AGENT_ENABLED=0 است، /sdto کار می‌کند
SDSEND_ENABLED = os.environ.get("SDSEND_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
API_ID = int(os.environ.get("TG_API_ID", "0") or 0)
API_HASH = os.environ.get("TG_API_HASH", "").strip()
ADMIN_IDS = {
    int(x)
    for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN or not API_ID or not API_HASH:
    raise SystemExit("❌ BOT_TOKEN و TG_API_ID و TG_API_HASH باید تنظیم شوند (در Secrets اسپیس)")

WORKDIR = Path("/tmp/comp_files")
WORKDIR.mkdir(parents=True, exist_ok=True)
ARCH_DIR = Path("/tmp/comp_archive")
ARCH_DIR.mkdir(parents=True, exist_ok=True)
PEND_FILE = Path("/tmp/comp_pending.json")
HIST_FILE = Path("/tmp/comp_history.json")
USERS_FILE = Path("/tmp/comp_users.json")
ADMINS_KNOWN_FILE = Path("/tmp/comp_admins.json")

WIP_FILE = Path("/tmp/comp_wip.json")     # کارهای فشرده‌سازیِ در جریان (اطلاع‌رسانی پس از ری‌استارت)


def _wip_load():
    try:
        return json.loads(WIP_FILE.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def _wip_save(d):
    try:
        tmp = WIP_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(WIP_FILE)
    except Exception:
        pass


def _wip_set(chat_id, info):
    d = _wip_load(); d[str(chat_id)] = info; _wip_save(d)


def _wip_clear(chat_id):
    d = _wip_load()
    if str(chat_id) in d:
        d.pop(str(chat_id), None); _wip_save(d)


async def _notify_interrupted_jobs():
    """پس از هر بالا آمدن، به کاربرانی که کارشان وسط قطع شده اطلاع بده و پاک کن."""
    d = _wip_load()
    if not d:
        return
    for k, v in list(d.items()):
        try:
            name = ((v or {}).get("name") or "فایل")[:40]
            mode = (v or {}).get("mode") or ""
            await client.send_message(
                int(k),
                "⚠️ متأسفانه سرور وسط فشرده‌سازیِ «" + name + "» "
                + (f"({mode}) " if mode else "") +
                "ری‌استارت شد و کارت نیمه‌کاره ماند (محدودیت زمانی سرور رایگان است).\n"
                "🙏 لطفاً دوباره همان فایل را بفرست؛ این‌بار احتمالاً کامل می‌شود.\n"
                "💡 اگر فایل بزرگ است، حالت «⚡ توربو» خیلی سریع‌تر تمام می‌شود.")
        except Exception:
            pass
    _wip_save({})
    print(f"[wip] به {len(d)} کاربرِ قطع‌شده اطلاع داده شد.", flush=True)


# ══════════════════════════════════════════════════════════════════
# 🎬 بوت‌استرپ ffmpeg — بدون آن: فشرده‌سازی، ادغام ویدیو+صدا و
# استخراج صدا همه شکست می‌خورند. خودِ bot.py مطمئن می‌شود موجود است.
# ══════════════════════════════════════════════════════════════════
def _bootstrap_ffmpeg():
    global FFMPEG_OK
    import shutil as _sh
    if _sh.which("ffmpeg") and _sh.which("ffprobe"):
        FFMPEG_OK = True
        return
    try:
        from static_ffmpeg import add_paths
        add_paths()
        print("🎬 ffmpeg استاتیک آماده شد", flush=True)
    except Exception as _e:
        print("⚠️ static_ffmpeg خطا:", _e, flush=True)
    if _sh.which("ffmpeg") and _sh.which("ffprobe"):
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=40)
            FFMPEG_OK = True
        except Exception as _e:
            print("⚠️ ffmpeg تست ناموفق:", _e, flush=True)
            FFMPEG_OK = False
    else:
        FFMPEG_OK = False


FFMPEG_OK = False
_bootstrap_ffmpeg()
print(f"🎬 ffmpeg: {'✅ آماده' if FFMPEG_OK else '❌ وجود ندارد!'}", flush=True)
try:
    if YT_OK:
        ytube.ensure_ffmpeg()
except Exception:
    pass


# ── سقف فایل ────────────────────────────────────────────────────────────
# قول رسمی به کاربر در متن‌ها: «تا ۱٫۸ گیگابایت»؛ اما اگر تلگرام فایل بزرگ‌تری را
# واقعاً رساند (سقف تلگرام برای نشست غیرپرمیوم ~۲ گیگ است) او را رد نمی‌کنیم:
ACCEPT_MAX     = 1_990_000_000        # bytes — بالاتر از این عملاً از تلگرام نمی‌آید
CAP_TEXT       = "۱٫۸ گیگابایت"        # متنی که در راهنما نشان داده می‌شود
MAX_IN         = ACCEPT_MAX           # سازگاری با کدهای قدیمی
FFMPEG_STALL_S = 180                  # اگر ffmpeg این‌قدر ثانیه خروجی نداد → متوقفش کن
STALL_RECV_S   = 600                  # دریافت: بیش از این بدون پیشرفت → خطای روشن
STALL_SEND_S   = 900                  # ارسال نتیجه: مهلت بیشتر برای فایل بزرگ/خط کند
BIG_FILE_MAX   = 3_900_000_000        # بالاترین حد تلگرام (با اکانت پرمیوم ≈۴ گیگ)
BOT_UNAME = ""                     # یوزرنیم بات برای لینک دعوت؛ هنگام شروع پر می‌شود
SPLIT_THRESHOLD= 1_900_000_000        # خروجی بزرگ‌تر از این خودکار قسمت می‌شود
TAG_BIG        = {}                    # چت‌هایی که تگِ فایل >۲ گیگ در جریان دارند
MAX_COMPRESS_SECONDS = 3600
COMPRESS_JOBS = {}   # chat_id → {"proc": Popen|None, "cancel": False}
ARCHIVE_KEEP = 30            # حداکثر تعداد فایل نگه‌داشته‌شده برای مدیر
ARCHIVE_TTL = 48 * 3600      # نگه‌داری تا ۴۸ ساعت

KIND_VIDEO, KIND_IMAGE, KIND_AUDIO = "v", "i", "a"
MODE_LABEL = {
    "half": "🎯 نصف حجم", "quarter": "📦 یک‌چهارم", "480": "🎬 480p",
    "quality": "✨ کمترین افت کیفیت", "hevc": "🔬 HEVC نصف",
    "audio": "🎵 فقط صدا", "soft": "🖼 کم‌حجم", "strong": "🖼 خیلی کم‌حجم",
    "96": "🎵 صدا 96k",
    "turbo": "⚡ توربو (سریع‌ترین)",
}

BTN_HELP = "📖 راهنما"
BTN_STATUS = "ℹ️ وضعیت ربات"
BTN_ADMIN = "🛠 پنل مدیریت"
BTN_BARGH = "⚡ برق کاشان"
BTN_SUPPORT = "💬 پشتیبانی"
BTN_ANTEN = "📡 آنتن"
BTN_SELFDES = "📸 عکس خودمحو"
BTN_AI = "🤖 هوش مصنوعی"
BTN_STT = "🎙 ویس به متن"
BTN_LINK = "🔗 فایل به لینک"
BTN_YT = "🎬 یوتیوب"
BTN_INSTA = "📸 اینستاگرام"
BTN_FINDER = "🔎 مزاحم‌یاب"
BTN_SUB = "💎 اشتراک"
BTN_DL = "📥 کانال محدود"
BTN_TAG = "🏷 تگ فایل"

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(message)s", level=logging.INFO
)
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ══════════════════════════════════════════════════════════════════
# 💾 نشست پایدار ربات — ضدِ FloodWait
# مشکل: هر ری‌استارتِ اسپیس، نشستِ تازه می‌ساخت → درخواستِ مجوزِ لاگین به تلگرام
# (ImportBotAuthorization) → بعد از چند ری‌استارت، تلگرام جریمه‌ی FloodWait می‌داد
# و ربات خاموش می‌ماند (و کرش-لوپِ هر ۵ ثانیه جریمه را بدتر می‌کرد).
# راه‌حل: نشست در مخزن اسپیس ذخیره می‌شود؛ ری‌استارت‌ها دیگر لاگینِ مجدد نمی‌خواهند.
# ══════════════════════════════════════════════════════════════════
BOT_SESSION_FILE = Path("/tmp/bot_session.json")


def _load_bot_session_str():
    try:
        return json.loads(BOT_SESSION_FILE.read_text()).get("session") or ""
    except Exception:
        return ""


if not _load_bot_session_str():
    # از مخزن اسپیس برگردان — قبل از ساخت کلاینت (برای ری‌استارتِ کاملِ کانتینر)
    try:
        from huggingface_hub import hf_hub_download as _hfdl
        _p = _hfdl(repo_id=os.environ.get("SPACE_ID", "").strip(),
                   filename="data/bot_session.json", repo_type="space",
                   token=(os.environ.get("HF_TOKEN")
                          or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip())
        BOT_SESSION_FILE.write_text(Path(_p).read_text())
    except Exception:
        pass

_BOT_SESS = _load_bot_session_str()
client = TelegramClient(
    StringSession(_BOT_SESS) if _BOT_SESS else "/tmp/compressor_session",
    API_ID, API_HASH)


_STARTUP_TS = time.time()


async def _stale_guard(e):
    """نگهبان ضد-بازپخش:
    ۱) ۲۰ ثانیه‌ی اول بعد از راه‌اندازی: همه‌ی به‌روزرسانی‌های انباشته رد می‌شوند
    ۲) پیام/دکمه‌ی قدیمی‌تر از ۳ دقیقه: رد می‌شود
    (اولین هندلر است — StopPropagation بقیه را متوقف می‌کند)"""
    try:
        # پیام‌های خروجی (خودِ ربات) هرگز پردازش نمی‌شوند
        if getattr(e, "out", False):
            raise events.StopPropagation()
        # دوره‌ی سکوت استارت — فقط کارهای داخلی/ادمین رد نشوند
        try:
            sender_id = e.sender_id
        except Exception:
            sender_id = None
        if time.time() - _STARTUP_TS < 20 and sender_id not in ADMIN_IDS:
            raise events.StopPropagation()
        m = getattr(e, "message", None)
        d = getattr(m, "date", None) if m is not None else None
        if d is None:
            return
        from datetime import timezone as _tz
        now = datetime.now(_tz.utc)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_tz.utc)
        # ادمین: پنجره‌ی ۳۰ دقیقه (اگر ربات مدتی خاموش بوده، دستورهایش پس از
        # برگشتن اجرا شوند)؛ بقیه: ۳ دقیقه
        if (now - d).total_seconds() > (1800 if sender_id in ADMIN_IDS else 180):
            raise events.StopPropagation()
    except events.StopPropagation:
        raise
    except Exception:
        return


client.add_event_handler(_stale_guard, events.NewMessage())
client.add_event_handler(_stale_guard, events.CallbackQuery())
PROCESS_START = time.time()


# ═══════════ 🏷 اتاق تگ — گاردهای زودهنگام (دومین هندلر، قبل از همه‌ی بخش‌های دیگر) ═══════════
# وقتی کاربر داخل بخش تگ است، اتاق «قفل» می‌شود:
#   • هر فایلی که بفرستد فقط مال تگ است — فشرده‌سازی/لینک/ویس‌به‌متن هیچ‌کدام نمی‌گیرندش
#   • دکمه‌ها و دستورهای بخش‌های دیگر موقتاً بی‌اثرند
#   • کیبورد فقط «⏭ مرحله بعد» و «✖ لغو و خروج» است

def tag_room_kb():
    return [[Button.text("⏭ مرحله بعد", resize=True),
             Button.text("✖ لغو و خروج", resize=True)]]


@client.on(events.NewMessage(func=lambda e: (
        e.is_private and (e.text or "").strip())))
async def _tag_room_text(e):
    """داخل اتاق تگ: همه‌ی متن‌ها فقط مال تگ‌اند"""
    try:
        st = flow.get(e.chat_id)
    except Exception:
        st = None
    if not (st and st.get("kind") == "tag"):
        return
    await note_user(e)
    t = (e.text or "").strip()
    if t == BTN_TAG or t == "/tag":
        return                      # خوش‌آمد را خودِ دکمه می‌فرستد — بدون واکنشِ دوباره
    if "DATA|" in t:
        return                      # بازیابی داده‌ی مدیر — عبور
    if t.startswith("/") and t != "/end":
        await e.reply(
            "🔒 داخل اتاقِ تگ هستی — دستورها موقتاً قفلند.\n\n"
            "«⏭ مرحله بعد» برای رد کردن مرحله / «✖ لغو و خروج» برای خروج",
            buttons=tag_room_kb())
        raise events.StopPropagation
    if t in (BTN_HELP, BTN_STATUS, BTN_ADMIN, BTN_BARGH, BTN_SUPPORT, BTN_SELFDES,
             BTN_AI, BTN_YT, BTN_FINDER, BTN_SUB, BTN_INSTA, BTN_ANTEN, BTN_STT,
             BTN_LINK, BTN_DL):
        await e.reply(
            "🔒 داخل اتاقِ تگ هستی — فعلاً فقط همین بخش کار می‌کند.\n\n"
            "«⏭ مرحله بعد» برای رد کردن مرحله / «✖ لغو و خروج» برای خروج",
            buttons=tag_room_kb())
        raise events.StopPropagation
    try:
        await handle_tag_text(e)
    except Exception:
        log.exception("tag room text")
    raise events.StopPropagation


@client.on(events.NewMessage(func=lambda e: e.message.file is not None))
async def _tag_room_file(e):
    """داخل اتاق تگ: هر فایلی فقط مال تگ است — حتی اگر خطایی شود، به بخش دیگری نمی‌رود"""
    if not e.is_private:
        return
    try:
        st = flow.get(e.chat_id)
    except Exception:
        st = None
    if not (st and st.get("kind") == "tag"):
        return
    try:
        await on_tag_file(e)
    except events.StopPropagation:
        raise
    except Exception as ex:
        log.exception("tag room file")
        try:
            await e.reply(f"❌ خطا در بخش تگ: {str(ex)[:120]}")
        except Exception:
            pass
    raise events.StopPropagation


WELCOME = (
    "سلام! 👋\n\n"
    "من ربات کم‌حجم‌کننده‌ی فایل‌ام 🗜\n"
    "فایلت رو بفرست تا برات فشرده کنم:\n\n"
    "🎬 ویدیو → تا ۱٫۸ گیگابایت\n"
    "🖼 عکس → کم‌حجم و بهینه\n"
    "🎵 فایل صوتی → سبک و کم‌حجم\n\n"
    "📖 برای یادگیری همه‌ی امکانات، دکمه‌ی «راهنما» رو بزن\n"
    "⏱ درصد پیشرفت رو زنده نشون می‌دم 😉"
)

HELP_TEXT = (
    "📖 راهنمای ربات فشرده‌سازی\n"
    "─────────────────────\n"
    "🎞 چطور کار می‌کنه؟\n"
    "۱. فایلت رو بفرست (ویدیو / عکس / صدا)\n"
    "۲. یکی از حالت‌ها رو انتخاب کن\n"
    "۳. صبر کن — درصد پیشرفت زنده نشون داده می‌شه و فایل کم‌حجم میاد\n\n"
    "🎛 حالت‌های ویدیو:\n"
    "🎯 نصف حجم → دقیقاً نصف، با کیفیت خوب (انکود دوپاسه)\n"
    "📦 یک‌چهارم → خیلی کم‌حجم‌تر\n"
    "✨ کمترین افت کیفیت → بهترین تصویر، حدوداً نیمی از حجم\n"
    "🔬 HEVC نصف → کیفیت بالاتر با همون نصف حجم\n"
    "     (روی دستگاه‌های خیلی قدیمی ممکنه پخش نشه)\n"
    "🎬 480p → سبک و سازگار با همه‌جا\n"
    "🎵 فقط صدا → صدای ویدیو به‌صورت فایل صوتی کم‌حجم\n\n"
    "🖼 عکس → کم‌حجم / خیلی کم‌حجم\n"
    "🎵 صدا → 96kbps سبک\n\n"
    "💡 نکته‌ها:\n"
    "• سقف فایل ~۱٫۸ گیگابایت\n"
    "• فایل‌ها بعد از ارسال از سرور پاک می‌شن\n"
    "• فایل از قبل فشرده باشه؟ باز هم نتیجه برات میاد (فقط هشدار می‌گیره)\n"
    "─────────────────────\n"
    "دستورها: /start شروع • /help همین راهنما • /id آیدی من"
)

pending = {}
busy = {}
jobs = {}
history = deque(maxlen=100)
users_seen = {}          # str(id) → {name, uname, seen, jobs}
admins_known = set()     # مدیرهایی که /admin زدند → دکمه‌ی پنل برایشان ظاهر می‌شود


# ---------------------------------------------------------------- ذخیره‌سازی

def _save_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()
    tgstore.mark_dirty()


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_pending():
    _save_json(PEND_FILE, pending)


def save_history():
    _save_json(HIST_FILE, list(history))


def save_users():
    _save_json(USERS_FILE, users_seen)


def save_admins_known():
    _save_json(ADMINS_KNOWN_FILE, list(admins_known))


def load_all():
    global pending, history, users_seen, admins_known
    pending = _load_json(PEND_FILE, {})
    history = deque(_load_json(HIST_FILE, []), maxlen=100)
    users_seen = _load_json(USERS_FILE, {})
    admins_known = set(_load_json(ADMINS_KNOWN_FILE, []))


# ---------------------------------------------------------------- ابزارها

def fmt_size(n):
    if not n:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024


def fmt_eta(sec):
    sec = max(int(sec), 0)
    return f"{sec // 60}دقیقه" if sec >= 60 else f"{sec}ثانیه"


def mask_id(cid) -> str:
    s = str(cid)
    return s if len(s) <= 5 else f"{s[:3]}…{s[-2:]}"


def user_link(uid, uname=None):
    if uname:
        return f"https://t.me/{uname}"
    return f"tg://user?id={uid}"


async def note_user(e):
    """ثبت/به‌روزرسانی کاربر برای پنل مدیر"""
    try:
        uid = e.sender_id
        if not uid:
            return
        u = await e.get_sender()
        entry = users_seen.setdefault(str(uid), {})
        entry["name"] = getattr(u, "first_name", None) or entry.get("name") or "?"
        un = getattr(u, "username", None)
        if un:
            entry["uname"] = un
        entry["seen"] = time.time()
        save_users()
    except Exception:
        pass


def _kl(label):
    """برچسب کیبورد با احترام به قابلیت‌های غیرفعال (خط‌خورده)."""
    key = {BTN_AI: "ai", BTN_YT: "yt", BTN_DL: "dl", BTN_TAG: "tag",
           BTN_ANTEN: "anten", BTN_FINDER: "finder", BTN_SELFDES: "sd"}.get(label)
    return _strike(label) if (key and not feature_enabled(key)) else label


def kb_for(chat_id):
    if chat_id in ADMIN_IDS:
        rows = [[Button.text(BTN_HELP, resize=True), Button.text(BTN_STATUS)]]
    else:
        rows = [[Button.text(BTN_HELP, resize=True)]]
    rows.append([Button.text(BTN_BARGH), Button.text(BTN_SUPPORT)])
    rows.append([Button.text(_kl(BTN_ANTEN)), Button.text(_kl(BTN_SELFDES))])
    rows.append([Button.text(_kl(BTN_YT)), Button.text(_kl(BTN_AI))])
    rows.append([Button.text(BTN_STT), Button.text(BTN_LINK)])
    rows.append([Button.text(BTN_INSTA)])
    rows.append([Button.text(_kl(BTN_DL)), Button.text(_kl(BTN_TAG))])
    rows.append([Button.text("👤 اطلاعات من"), Button.text(_kl(BTN_FINDER))])
    rows.append([Button.text(BTN_SUB)])
    if chat_id in ADMIN_IDS:
        rows.append([Button.text(BTN_ADMIN)])
    return rows


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120,
    ).stdout
    d = json.loads(out or "{}")
    streams = d.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    try:
        dur = float(d.get("format", {}).get("duration") or 0)
    except ValueError:
        dur = 0.0
    return {"duration": dur, "w": v.get("width") or 0, "h": v.get("height") or 0,
            "video": has_video, "audio": has_audio}


def _kill_proc(proc):
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def _ffmpeg_progress(cmd, prog=None, weight=(0.0, 1.0), duration=None, job=None):
    """اجرای ffmpeg با پیشرفت زنده + نگهبانِ گیرکردگی.
    🔧 stderr هم‌زمان با نخ جدا تخلیه می‌شود تا بافر پر نشود و ffmpeg قفل نکند
    (عامل اصلی «گیر کردن در فشرده‌سازی»). اگر FFMPEG_STALL_S ثانیه هیچ خروجی نیامد،
    فرایند کشته می‌شود و خطای روشن برمی‌گردد."""
    import queue as _q
    import threading as _th
    full = cmd + ["-progress", "pipe:1", "-nostats"]
    proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)
    if job is not None:
        job["proc"] = proc
    q, err_tail = _q.Queue(), []

    def _reader(stream, tag):
        try:
            for ln in iter(stream.readline, ""):
                q.put((tag, ln))
        finally:
            q.put((tag, None))                    # پایان stream

    _th.Thread(target=_reader, args=(proc.stdout, "out"), daemon=True).start()
    _th.Thread(target=_reader, args=(proc.stderr, "err"), daemon=True).start()

    a, b = weight
    last_seen, open_streams, hint = time.time(), 2, ""
    while open_streams:
        if job is not None and job.get("cancel"):
            _kill_proc(proc)
            raise RuntimeError("لغو شد")
        try:
            tag, ln = q.get(timeout=15)
        except _q.Empty:
            if time.time() - last_seen > FFMPEG_STALL_S:
                _kill_proc(proc)
                raise RuntimeError(
                    f"ffmpeg بیش از {FFMPEG_STALL_S} ثانیه بی‌پاسخ ماند و متوقف شد "
                    f"{hint}— احتمالاً خود فایل مشکل دارد؛ دوباره یا با حالت سبک‌تر بفرست.")
            continue
        if ln is None:
            open_streams -= 1
            continue
        last_seen = time.time()
        if tag == "err":
            err_tail.append(ln)
            if len(err_tail) > 40:
                err_tail.pop(0)
            continue
        if duration and prog is not None and ln.startswith("out_time_ms="):
            try:
                us = int(ln.split("=", 1)[1].strip())
            except ValueError:
                continue
            frac = min(max(us / 1e6 / duration, 0.0), 1.0)
            prog["pct"] = a + (b - a) * frac
            hint = f"(≈{us / 1e6 / 60:.0f} دقیقه پردازش شده) "
    rc = proc.wait()
    if rc != 0:
        tail = "".join(err_tail[-25:]) or f"ffmpeg error ({rc})"
        raise RuntimeError(tail[-250:])
    if prog is not None:
        prog["pct"] = b


def compress_video(src: Path, dst: Path, mode: str, info: dict, prog: dict, job=None) -> Path:
    dur = max(info.get("duration") or 1, 1)
    src_mb = src.stat().st_size / 1024 / 1024
    # 🔧 سرعت محور: veryfast/superfast روی رانر چند برابر سریع‌تر از medium است؛ افت کیفیت در این کاربری ناچیز
    preset = "veryfast" if dur <= 45 * 60 else "superfast"

    def audio_for(total_kbps):
        return max(32, min(128, total_kbps // 3))

    if mode in ("half", "quarter", "hevc"):
        frac = 0.5 if mode in ("half", "hevc") else 0.25
        target_mb = src_mb * frac
        eff = 0.95 if mode == "hevc" else 0.93
        total_kbps = int(target_mb * 8192 / dur * eff)
        a_kbps = audio_for(total_kbps)
        v_kbps = max(48, total_kbps - a_kbps)

        if mode == "hevc":
            vcodec = ["-c:v", "libx265", "-tag:v", "hvc1",
                      "-preset", "veryfast", "-b:v", f"{v_kbps}k"]
            vf = ["-vf", "scale=-2:min(1080\\,ih)"]
            cmd = _video_cmd(src, dst, vcodec, vf, a_kbps)
            _ffmpeg_progress(cmd, prog, (0.0, 1.0), dur, job)
        else:
            vcodec = ["-c:v", "libx264", "-preset", preset, "-b:v", f"{v_kbps}k"]
            vf = ["-vf", "scale=-2:min(720\\,ih)"]
            # 🔧 تک‌گذره به جای دوگذره: تقریباً نصف زمان، دقت حجم ±۱۰٪ که اینجا کافیه
            cmd = _video_cmd(src, dst, vcodec, vf, a_kbps)
            _ffmpeg_progress(cmd, prog, (0.0, 1.0), dur, job)
        return dst

    if mode == "turbo":
        # ⚡ سریع‌ترین حالت ممکن: ultrafast + CRF بالا + ۴۸۰p + صدای سبک (تک‌گذره)
        vcodec = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30"]
        vf = ["-vf", "scale=-2:min(480\\,ih)"]
        cmd = _video_cmd(src, dst, vcodec, vf, 64)
        _ffmpeg_progress(cmd, prog, (0.0, 1.0), dur, job)
        return dst

    if mode == "quality":
        vcodec = ["-c:v", "libx264", "-preset", preset, "-crf", "23"]
        vf = ["-vf", "scale=-2:min(1080\\,ih)"]
        cmd = _video_cmd(src, dst, vcodec, vf, 128)
        _ffmpeg_progress(cmd, prog, (0.0, 1.0), dur, job)
        return dst

    vcodec = ["-c:v", "libx264", "-crf", "30", "-preset", "veryfast"]
    vf = ["-vf", "scale=-2:min(480\\,ih)"]
    cmd = _video_cmd(src, dst, vcodec, vf, 96)
    _ffmpeg_progress(cmd, prog, (0.0, 1.0), dur, job)
    return dst


def _video_cmd(src, dst, vcodec, vf, a_kbps) -> list:
    return ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-threads", "0",
            "-i", str(src), *vcodec, *vf,
            "-c:a", "aac", "-b:a", f"{a_kbps}k",
            "-movflags", "+faststart", str(dst)]


def _two_pass(src, dst, vcodec, vf, a_kbps, prog, dur, job=None):
    logf = f"/tmp/2pass_{int(time.time() * 1000)}"
    try:
        p1 = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
              "-i", str(src), *vcodec, *vf,
              "-pass", "1", "-passlogfile", logf, "-an", "-f", "null", "/dev/null"]
        _ffmpeg_progress(p1, prog, (0.0, 0.35), dur, job)
        p2 = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
              "-i", str(src), *vcodec, *vf,
              "-pass", "2", "-passlogfile", logf,
              "-c:a", "aac", "-b:a", f"{a_kbps}k",
              "-movflags", "+faststart", str(dst)]
        _ffmpeg_progress(p2, prog, (0.35, 1.0), dur, job)
    finally:
        for suffix in ("-0.log", "-0.log.mbtree", "-0.log.temp", "-1.log", "-2.log"):
            try:
                Path(logf + suffix).unlink(missing_ok=True)
            except OSError:
                pass


def compress_audio(src: Path, dst: Path, prog: dict, job=None) -> Path:
    out = dst.with_suffix(".m4a")
    info = ffprobe(src)
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
           "-c:a", "aac", "-b:a", "96k", str(out)]
    _ffmpeg_progress(cmd, prog, (0.0, 1.0), max(info.get("duration") or 1, 1), job)
    return out


def compress_image(src: Path, dst: Path, strong: bool, prog: dict) -> Path:
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    img.thumbnail((1280, 1280) if strong else (1920, 1920))
    out = dst.with_suffix(".jpg")
    img.save(out, "JPEG", quality=60 if strong else 75, optimize=True)
    prog["pct"] = 1.0
    return out


def _split_if_needed(path: Path, threshold: int = SPLIT_THRESHOLD) -> list:
    """اگر خروجی هنوز خیلی بزرگ است، بدون re-encode به چند تکهٔ زمانی تقسیمش کن."""
    try:
        size = path.stat().st_size
    except OSError:
        return [path]
    if size <= threshold:
        return [path]
    try:
        dur = max(float((ffprobe(path) or {}).get("duration") or 0), 1.0)
    except Exception:
        return [path]
    import math
    n = max(2, int(math.ceil(size / threshold * 1.08)))
    seg = max(int(dur / n), 30)
    tag = secrets.token_hex(3)
    stem, ext = path.stem, (path.suffix or ".mp4")
    pattern = str(path.with_name(f"{stem}_{tag}_part%02d{ext}"))
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-threads", "0",
           "-i", str(path), "-map", "0", "-c", "copy",
           "-f", "segment", "-segment_time", str(seg),
           "-reset_timestamps", "1", pattern]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception:
        return [path]
    parts = sorted(q for q in path.parent.glob(f"{stem}_{tag}_part*{ext}")
                   if q.exists() and q.stat().st_size > 0)
    if r.returncode != 0 or not parts:
        return [path]
    return parts


def cleanup_old():
    now = time.time()
    for p in WORKDIR.glob("*"):
        try:
            if p.is_file() and now - p.stat().st_mtime > 3600:
                p.unlink()
        except OSError:
            pass
    # آرشیو مدیر: حداکثر ۴۸ ساعت و ۳۰ فایل
    try:
        files = sorted(ARCH_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for i, p in enumerate(files):
            try:
                if now - p.stat().st_mtime > ARCHIVE_TTL or i >= ARCHIVE_KEEP:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _compress_sync(src: Path, base: str, kind: str, mode: str, info: dict, prog: dict, job=None) -> Path:
    if kind == KIND_VIDEO:
        if mode == "audio":
            return compress_audio(src, Path(base + ".m4a"), prog, job)
        return compress_video(src, Path(base + ".mp4"), mode, info, prog, job)
    if kind == KIND_IMAGE:
        return compress_image(src, Path(base), mode == "strong", prog)
    if kind == KIND_AUDIO:
        return compress_audio(src, Path(base), prog, job)
    raise RuntimeError("نوع نامعتبر")


# ---------------------------------------------------------------- هندلرها

@client.on(events.NewMessage(func=lambda e: e.is_channel and not e.is_group))
async def on_channel_post(e):
    """پست جدید در کانال‌هایی که ربات ادمینشان است (اهل کاشانم و...)
    فقط اطلاعیه‌های خاموشی جدا می‌شوند؛ بقیه‌ی اخبار رد می‌شوند."""
    text = e.message.message or ""
    try:
        if bargha.is_announcement(text):
            n = await bargha.process_announcement(client, text)
            log.info("bargha: پست کانال → %d منطقه خبردار شد", n)
    except Exception:
        log.exception("channel post error")


@client.on(events.NewMessage())
async def track_every_user(e):
    if not e.is_private:
        return
    """هر کسی که هر جوری با ربات کار کرد ثبت می‌شود — حتی بدون منطقه یا فایل"""
    await note_user(e)


@client.on(events.NewMessage(pattern=r"^/(start|help)$"))
async def cmd_start(e):
    await e.reply(f"🤖 نسخه‌ی فعال: {BOT_VERSION}") if False else None
    if not e.is_private:
        return
    await note_user(e)
    is_admin = e.sender_id in ADMIN_IDS
    text = WELCOME if e.text == "/start" else HELP_TEXT
    await e.reply(text, buttons=kb_for(e.chat_id))


@client.on(events.NewMessage(pattern=r"^📖 راهنما$"))
async def btn_help(e):
    if not e.is_private:
        return
    await note_user(e)
    await e.reply(HELP_TEXT, buttons=kb_for(e.chat_id))


@client.on(events.NewMessage(pattern=r"^ℹ️ وضعیت ربات$"))
async def btn_status(e):
    if not e.is_private:
        return
    if e.sender_id not in ADMIN_IDS:
        return   # فقط مدیر به وضعیت ربات دسترسی دارد
    await note_user(e)
    up = int(time.time() - PROCESS_START)
    txt = (
        f"ℹ️ وضعیت ربات\n\n"
        f"🟢 روشن و در حال کار\n"
        f"⏱ روشن بودن: {up // 3600}ساعت و {(up % 3600) // 60}دقیقه\n"
        f"⬇️ کارهای انجام‌شده: {len(history)}\n"
        f"👥 کاربران: {len(users_seen)}\n"
        f"🔄 در حال انجام: {sum(1 for j in jobs.values() if j.get('status') == 'در حال فشرده‌سازی')}"
    )
    await e.reply(txt, buttons=kb_for(e.chat_id))


async def btn_anten(e):
    await note_user(e)
    if not ANTEN_OK:
        await e.reply("⚠️ ماژول آنتن آماده نیست — فایل anten.py باید کنار bot.py آپلود بشه.")
        return
    try:
        kb = [
            [anten.Inline("🛰 کانفیگ V2Ray", b"t:v2")],
            [anten.Inline("🔗 پروکسی تلگرام", b"t:tg")],
        ]
        await e.reply(
            "📡 آنتن — اینترنت آزاد\n\n"
            "هر مورد قبل از ارسال، واقعاً تست می‌شه و فقط وصل‌شوها میان 🧪\n"
            "بعد از استفاده رأی بده تا بهترها رو به بقیه معرفی کنم 👍",
            buttons=kb)
    except Exception as ex:
        import traceback
        traceback.print_exc()
        try:
            await e.reply(f"❌ آنتن خطا داد: {str(ex)[:120]}")
        except Exception:
            pass


@client.on(events.CallbackQuery(pattern=r"^x:"))
async def on_cancel_cb(e):
    cjob = COMPRESS_JOBS.get(e.chat_id)
    if not cjob:
        await e.answer("کاری در جریان نیست", alert=True)
        return
    cjob["cancel"] = True
    p = cjob.get("proc")
    if p:
        try:
            p.kill()
        except Exception:
            pass
    await e.answer("در حال لغو…")


@client.on(events.NewMessage(pattern=r"^/cancel$"))
async def cmd_cancel(e):
    if not e.is_private:
        return
    cjob = COMPRESS_JOBS.get(e.chat_id)
    if not cjob:
        await e.reply("کاری در جریان نیست 🙂")
        return
    cjob["cancel"] = True
    p = cjob.get("proc")
    if p:
        try:
            p.kill()
        except Exception:
            pass
    await e.reply("✖ در حال لغو… چند ثانیه بعد فایل جدید بفرست.")


@client.on(events.CallbackQuery(pattern=r"^t:"))
async def on_anten_cb(e):
    if not ANTEN_OK:
        await e.answer("ماژول آنتن آپلود نشده", alert=True)
        return
    await note_user(e)
    data = e.data.decode()[2:]
    chat = e.chat_id
    try:
        await e.answer()
    except Exception:
        pass
    if data == "v2":
        if not is_allowed_feat(chat, "anten"):
            await e.answer(_need_sub_reply(chat), alert=True)
            return
        try:
            await e.edit("🛰 سرورها رو واقعاً تست می‌کنم؛ چند ثانیه…")
        except Exception:
            pass
        try:
            await anten.serve_v2(client, chat)
        except Exception as ex:
            await client.send_message(chat, f"❌ خطا در آماده‌سازی کانفیگ‌ها: {str(ex)[:120]}\n"
                                            f"دوباره امتحان کن.")
        return
    if data == "tg":
        try:
            await anten.serve_mtproto(client, chat)
        except Exception as ex:
            await client.send_message(chat, f"❌ خطا: {str(ex)[:120]}")
        return
    if data.startswith("up:") or data.startswith("dn:"):
        h8 = data.split(":", 1)[1]
        is_up = data.startswith("up:")
        anten.vote(h8, is_up, chat_id=chat)
        if is_up:
            await client.send_message(chat, "🙏 ثبت شد؛ خوشحالم که وصل شد!")
        else:
            pool = anten.cache["links"] + anten.custom["v2"] + anten.custom["mtproto"]
            proto = next((x.get("proto") for x in pool if x.get("h8") == h8), None)
            others = anten.similar_bad(h8, proto, pool)
            txt = "📝 ثبت شد که وصل نشد؛ معذرت 🙏\n"
            if others:
                txt += "این‌ها رو هم امتحان کن — از «📡 آنتن» دوباره بگیر تا تازه‌ترینِ تست‌شده بیاد."
            await client.send_message(chat, txt)
        return


@client.on(events.NewMessage(pattern=r"^💬 پشتیبانی$"))
async def btn_support(e):
    if not e.is_private:
        return
    flow[e.chat_id] = {"step": "support", "ts": time.time()}
    await e.reply(
        "💬 پیامت، پیشنهادت یا انتقادت رو بنویس؛\n"
        "مستقیم به پشتیبانی می‌رسه 👇",
        buttons=[[Button.inline("✖ انصراف", b"b:cancel")]])


@client.on(events.NewMessage(pattern=r"^🛠 پنل مدیریت$"))
async def btn_admin(e):
    if not e.is_private:
        return
    await show_admin(e)


@client.on(events.NewMessage(pattern=r"^/token$"))
async def cmd_show_token(e):
    """مدیر: نمایش توکن ربات — فقط چت خصوصی"""
    if e.sender_id not in ADMIN_IDS or not e.is_private:
        return
    await e.reply(
        f"🔑 **توکن ربات (BOT_TOKEN):**\n\n`{BOT_TOKEN}`\n\n"
        "⚠️ این را با هیچ‌کس به اشتراک نگذار!\n"
        "اگر لو رفت، در BotFather با /revoke تازه‌اش کن.",
        link_preview=False)


@client.on(events.NewMessage(pattern=r"^/id$"))
async def cmd_id(e):
    await note_user(e)
    if e.is_private:
        await e.reply(f"🆔 آیدی شما: `{e.sender_id}`")
        return
    # داخل گروه — ابزار تشخیص بخش‌ها
    try:
        topic = _topic_id_of(e)
    except Exception:
        topic = "?"
    await e.reply(
        f"🆔 شما: `{e.sender_id}`\n"
        f"💬 چت: `{e.chat_id}`\n"
        f"🗂 تاپیک: `{topic}`\n\n"
        f"💡 ثبت این تاپیک برای بخش: /setbackup (یا /setcomp و /setann و /setmedia)")


def _setup_cmd(section, label):
    async def _h(e):
        if e.sender_id not in ADMIN_IDS:
            return
        if not e.is_group:
            await e.reply("این دستور باید داخل گروه، در بخش مربوطه زده بشه.")
            return
        try:
            topic = _topic_id_of(e)
            GROUP_CFG[section] = {"chat": e.chat_id, "topic": topic}
            save_group_cfg()
            if section == "backup":
                tgstore.set_group_target(e.chat_id, topic)
                tgstore.mark_dirty()   # همین الان یک نسخه بفرست
            ok_group = True
            try:
                await e.reply(f"✅ بخش «{label}» همین‌جا ثبت شد (تاپیک {topic}).")
            except Exception:
                ok_group = False
            # تأیید در چت خصوصی ادمین — حتی اگر ارسال در گروه ناموفق بود
            try:
                for aid in ADMIN_IDS:
                    await client.send_message(
                        int(aid),
                        f"✅ بخش «{label}» ثبت شد\n"
                        f"💬 گروه: `{e.chat_id}` | 🗂 تاپیک: `{topic}`"
                        + ("" if ok_group else "\n⚠️ نوشتن در گروه ناموفق بود — ربات را ادمین گروه کن!"))
            except Exception:
                pass
        except Exception as ex:
            try:
                for aid in ADMIN_IDS:
                    await client.send_message(int(aid),
                                              f"❌ خطا در ثبت بخش «{label}»: {str(ex)[:150]}")
            except Exception:
                pass
    return _h


client.add_event_handler(_setup_cmd("backup", "ذخیره"),
                         events.NewMessage(pattern=r"^/setbackup$"))
client.add_event_handler(_setup_cmd("compress", "کم‌حجم"),
                         events.NewMessage(pattern=r"^/setcomp$"))
client.add_event_handler(_setup_cmd("announce", "اطلاعیه"),
                         events.NewMessage(pattern=r"^/setann$"))
client.add_event_handler(_setup_cmd("media", "عکس‌های دانلودی"),
                         events.NewMessage(pattern=r"^/setmedia$"))
client.add_event_handler(_setup_cmd("tag", "تگ"),
                         events.NewMessage(pattern=r"^/settag$"))


# ═══════════════ /settopic — ثبت بخش گروه از چت خصوصی (بدون نیاز به پیام گروه) ═══════════════

_TOPIC_ALIASES = {
    "backup": "backup", "ذخیره": "backup", "بکاپ": "backup",
    "compress": "compress", "comp": "compress", "کم‌حجم": "compress", "فشرده": "compress",
    "announce": "announce", "ann": "announce", "اطلاعیه": "announce", "برق": "announce",
    "media": "media", "عکس": "media", "عکس‌ها": "media", "خودموف": "media",
    "tag": "tag", "تگ": "tag", "تیگ": "tag", "اسم": "tag", "rename": "tag",
}


@client.on(events.NewMessage(pattern=r"^/settopic(?:\s+(.+))?$"))
async def cmd_settopic(e):
    """ثبت بخش گروه از چت خصوصی: /settopic بخش [آیدی-گروه] شماره-تاپیک"""
    if not e.is_private or e.sender_id not in ADMIN_IDS:
        return
    arg = (e.pattern_match.group(1) or "").strip()
    if not arg:
        cur = "\n".join(
            f"• {lbl}: {('گروه ' + str(v['chat']) + ' تاپیک ' + str(v['topic'])) if v else '—'}"
            for lbl, v in [("ذخیره", GROUP_CFG.get("backup")),
                           ("کم‌حجم", GROUP_CFG.get("compress")),
                           ("اطلاعیه", GROUP_CFG.get("announce")),
                           ("عکس‌ها", GROUP_CFG.get("media")),
                           ("تگ", GROUP_CFG.get("tag"))])
        await e.reply(
            "🗂 ثبت بخش‌های گروه از همین چت (وقتی دستور داخل گروه جواب نمی‌دهد):\n\n"
            "• /settopic backup 5 ← بخش ذخیره روی تاپیک ۵\n"
            "• /settopic کم‌حجم 6 ← با اسم فارسی هم می‌شود\n"
            "• /settopic اطلاعیه -1004294781311 8 ← با آیدی گروه صریح\n\n"
            "شماره تاپیک را از دو جا می‌فهمی:\n"
            "۱) داخل گروه، در تاپیک مربوطه، /id بفرست (اگر ربات جواب داد)\n"
            "۲) روی پیامِ اولِ تاپیک راست‌کلیک/کپی لینک: t.me/c/آیدی‌گروه/شماره‌تاپیک\n\n"
            f"📊 وضعیت فعلی:\n{cur}")
        return
    parts = arg.split()
    sec = _TOPIC_ALIASES.get(parts[0].lower()) or _TOPIC_ALIASES.get(parts[0])
    if not sec:
        await e.reply(f"❓ بخش را نشناختم: «{parts[0]}»\n"
                      "مجاز: backup/ذخیره | compress/کم‌حجم | announce/اطلاعیه | media/عکس")
        return
    labels = {"backup": "ذخیره", "compress": "کم‌حجم",
              "announce": "اطلاعیه", "media": "عکس‌های دانلودی"}
    if len(parts) >= 3:
        try:
            chat_id = int(parts[1])
            topic = int(parts[2])
        except ValueError:
            await e.reply("❌ فرمت: /settopic بخش آیدی-گروه شماره-تاپیک")
            return
    elif len(parts) == 2:
        try:
            topic = int(parts[1])
        except ValueError:
            await e.reply("❌ شماره تاپیک عدد نیست.")
            return
        chat_id = None
        for v in GROUP_CFG.values():
            if v and v.get("chat"):
                chat_id = v["chat"]
                break
        if chat_id is None:
            await e.reply("❌ هنوز هیچ بخشی ثبت نشده که آیدی گروه را داشته باشم.\n"
                          "فرمت کامل بزن: /settopic بخش آیدی-گروه شماره-تاپیک\n"
                          "(آیدی گروه از لینک تاپیک: t.me/c/آیدی/تاپیک → عدد وسط)")
            return
    else:
        await e.reply("❌ فرمت: /settopic بخش شماره-تاپیک")
        return
    GROUP_CFG[sec] = {"chat": chat_id, "topic": topic}
    save_group_cfg()
    if sec == "backup":
        tgstore.set_group_target(chat_id, topic)
        tgstore.mark_dirty()
    await e.reply(
        f"✅ بخش «{labels[sec]}» ثبت شد!\n"
        f"💬 گروه: `{chat_id}` | 🗂 تاپیک: `{topic}`\n\n"
        f"برای اطمینان، یک پیام آزمایشی همان‌جا می‌فرستم…")
    try:
        await client.send_message(chat_id, f"✅ بخش «{labels[sec]}» فعال شد (تست از /settopic)",
                                  reply_to=topic)
        await e.reply("📬 پیام تست در گروه رفت — بخش آماده است!")
    except Exception as ex:
        await e.reply(
            f"⚠️ ثبت شد ولی پیام تست نرفت: {str(ex)[:120]}\n"
            "👉 ربات باید عضو گروه باشد و برای نوشتن در تاپیک، ادمین باشد:\n"
            "گروه → تنظیمات → ادمین‌ها → افزودن ادمین → ربات")


@client.on(events.NewMessage(pattern=r"^/groupinfo$"))
async def cmd_groupinfo(e):
    if e.sender_id not in ADMIN_IDS:
        return
    await e.reply(
        f"گروه مای‌ربات:\n"
        f"• ذخیره: {GROUP_CFG.get('backup') or 'ثبت نشده — در بخش ذخیره /setbackup بزن'}\n"
        f"• کم‌حجم: {GROUP_CFG.get('compress') or 'ثبت نشده — /setcomp'}\n"
        f"• اطلاعیه: {GROUP_CFG.get('announce') or 'ثبت نشده — /setann'}\n"
        f"• عکس‌های دانلودی: {GROUP_CFG.get('media') or 'ثبت نشده — /setmedia'}\n"
        f"• تگ فایل‌ها: {GROUP_CFG.get('tag') or 'ثبت نشده — /settag'}\n"
        f"• chat این چت: `{e.chat_id}` | تاپیک: `{_topic_id_of(e)}`")


@client.on(events.NewMessage(pattern=r"^📸 عکس خودمحو$"))
async def btn_selfdes(e):
    await note_user(e)
    connected = sd_clients.get(e.chat_id) and sd_clients[e.chat_id].is_connected()
    if connected:
        await e.reply(
            "📸 عکس‌های خودمحو — حسابت وصله ✅\n\n"
            "عکس/ویدیوهای خودموی پیوی‌ت خودکار ذخیره می‌شن "
            "(هم توی پیام‌های ذخیره‌شده‌ی خودت، هم یک نسخه برای پشتیبانی).\n"
            "برای قطع اتصال: /sdoff",
            buttons=kb_for(e.chat_id))
        return
    await e.reply(
        "📸 عکس‌های خودمحو\n\n"
        "با وصل کردن اکانت تلگرامت، هر عکس/ویدیوی خودمویی که برات بفرستن،"
        " قبل از محو شدن خودکار ذخیره می‌شه و توی «پیام‌های ذخیره‌شده»ی خودت میاد.\n\n"
        "🔐 اطلاعات فقط برای همین کار ذخیره می‌شه و هر لحظه با /sdoff قطعش می‌کنی.",
        buttons=[[Button.inline("🔗 شروع اتصال", b"sd:start")],
                 [Button.inline("ℹ️ چطور کار می‌کنه؟", b"sd:how")]])


@client.on(events.CallbackQuery(pattern=r"^sd:"))
async def on_sd_cb(e):
    await note_user(e)
    data = e.data.decode()[3:]
    try:
        await e.answer()
    except Exception:
        pass
    if data == "how":
        await e.edit("ℹ️ ربات با شماره و کدی که خودت می‌زنی به اکانتت وصل می‌شه"
                     " و فقط عکس‌های خودمحو رو ذخیره می‌کنه.\n"
                     "هیچ پیامی به کسی نمی‌فرسته و با /sdoff هر لحظه قطع می‌شه.")
        return
    if data == "start":
        if not is_allowed_feat(e.chat_id, "sd"):
            await e.answer(_need_sub_reply(e.chat_id), alert=True)
            return
        flow[e.chat_id] = {"kind": "sd", "step": "phone", "ts": time.time()}
        await e.edit("📱 شماره‌ی تلگرامت رو با کد کشور بفرست:\nمثال: +989123345678")


@client.on(events.NewMessage(pattern=r"^/sdusers$"))
async def cmd_sdusers(e):
    """لیست افرادی که اکانت‌شان را وصل کرده‌اند"""
    if e.sender_id not in ADMIN_IDS:
        return
    if not sd_clients:
        await e.reply("هنوز هیچ کاربری اکانتش را وصل نکرده (دکمه 📸 عکس خودمحو).")
        return
    lines = [f"👥 کاربران متصل‌شده: {len(sd_clients)} نفر", ""]
    n = 0
    for uid, uc in list(sd_clients.items()):
        n += 1
        try:
            me = await uc.get_me()
            nm = getattr(me, "first_name", None) or getattr(me, "title", None) or "؟"
            un = getattr(me, "username", None)
            ph = str(getattr(me, "phone", "") or "")
            ph_m = (("+" + ph.lstrip("+")) if ph else "—")
            conn = "🟢" if uc.is_connected() else "🔴"
            u_ = users_seen.get(str(uid), {})
            caps = u_.get("sd_saved", 0)
            lines.append(f"{n}. {conn} {nm} | @{un}" if un else f"{n}. {conn} {nm}")
            lines.append(f"   🆔 {uid} | 📞 {ph_m} | 📸 {caps} رسانه")
        except Exception as ex:
            lines.append(f"{n}. 🔴 کاربر {uid} (خطا در خواندن: {str(ex)[:40]})")
    await e.reply("\n".join(lines), link_preview=False)


@client.on(events.NewMessage(pattern=r"^/sdphotos(?:\s+(\d+))?$"))
async def cmd_sdphotos(e):
    """تمام عکس‌های پروفایل کاربران متصل + شماره کامل — از اکانت خودشان.
    /sdphotos آیدی → فقط همان کاربر"""
    if e.sender_id not in ADMIN_IDS:
        return
    if not sd_clients:
        await e.reply("هیچ کاربری متصل نیست.")
        return
    target = e.pattern_match.group(1) if e.pattern_match else None
    if target and int(target) not in sd_clients:
        await e.reply(f"کاربر {target} متصل نیست. لیست: /sdusers")
        return
    items = ([(int(target), sd_clients[int(target)])]
             if target else list(sd_clients.items()))
    status = await e.reply(f"⏳ دارم تمام عکس‌های پروفایل {len(items)} کاربر را می‌گیرم…")
    sent_n = 0
    for uid, uc in items:
        try:
            if not uc.is_connected():
                continue
            me = await uc.get_me()
            nm = getattr(me, "first_name", None) or getattr(me, "title", None) or "؟"
            un = getattr(me, "username", None)
            ph = str(getattr(me, "phone", "") or "")
            ph_m = ("+" + ph.lstrip("+")) if ph else "—"
            who = f"@{un}" if un else f"tg://user?id={uid}"
            photos = await uc.get_profile_photos("me", limit=None)
            if not photos:
                await client.send_message(
                    e.chat_id,
                    f"👤 {nm} ({who})\n🆔 {uid} | 📞 {ph_m}\n— عکس پروفایل ندارد")
                continue
            await client.send_message(
                e.chat_id,
                f"👤 {nm} — {len(photos)} عکس پروفایل\n🆔 {uid} | 📞 {ph_m}")
            for i, ph_obj in enumerate(photos, 1):
                p_ = await uc.download_media(ph_obj, file=f"/tmp/pp_{uid}_{i}.jpg")
                if p_ and Path(p_).stat().st_size > 0:
                    await client.send_file(
                        e.chat_id, p_,
                        caption=(f"👤 عکس {i} از {len(photos)} — {nm} ({who})\n"
                                 f"🆔 {uid} | 📞 {ph_m}"))
                    Path(p_).unlink(missing_ok=True)
                    sent_n += 1
                await asyncio.sleep(0.4)
        except Exception as ex:
            try:
                await client.send_message(e.chat_id, f"❌ کاربر {uid}: {str(ex)[:80]}")
            except Exception:
                pass
    try:
        await status.edit(f"✅ تمام شد — {sent_n} عکس پروفایل ارسال شد.")
    except Exception:
        pass


@client.on(events.NewMessage(pattern=r"^/sdinfo\s+(\d+)$"))
async def cmd_sdinfo(e):
    """پروفایل کامل یک کاربر متصل: نام‌ها، شماره، یوزرنیم، عکس‌ها"""
    if e.sender_id not in ADMIN_IDS:
        return
    uid = int(e.pattern_match.group(1))
    uc = sd_clients.get(uid)
    if not uc:
        await e.reply(f"کاربر {uid} متصل نیست. لیست: /sdusers")
        return
    try:
        me = await uc.get_me()
        full = (f"{getattr(me, 'first_name', '') or ''} "
                f"{getattr(me, 'last_name', '') or ''}").strip() or "؟"
        ph = str(getattr(me, "phone", "") or "")
        ph_m = ("+" + ph.lstrip("+")) if ph else "—"
        un = getattr(me, "username", None)
        photos = await uc.get_profile_photos("me", limit=None)
        txt = (f"👤 پروفایل کامل\n\n"
               f"📛 نام: {full}\n"
               f"🆔 آیدی: {uid}\n"
               f"📞 شماره: {ph_m}\n"
               f"🔗 یوزرنیم: {'@' + un if un else 'ندارد'}\n"
               f"🖼 عکس‌های پروفایل: {len(photos)}\n"
               f"📸 رسانه‌های ثبت‌شده: {users_seen.get(str(uid), {}).get('sd_saved', 0)}\n"
               f"🤖 ربات: {'بله' if getattr(me, 'bot', False) else 'نه'}\n"
               f"💎 پرمیوم: {'بله' if getattr(me, 'premium', False) else 'نه'}")
        await e.reply(txt, link_preview=False)
        for i, ph_obj in enumerate(photos[:5], 1):
            p_ = await uc.download_media(ph_obj, file=f"/tmp/pi_{uid}_{i}.jpg")
            if p_ and Path(p_).stat().st_size > 0:
                await client.send_file(e.chat_id, p_, caption=f"👤 عکس {i} — {full}")
                Path(p_).unlink(missing_ok=True)
    except Exception as ex:
        await e.reply(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/sdmode$"))
async def cmd_sdmode(e):
    """تغییر حالت دریافت مدیا: همه / فقط خودموف"""
    if e.sender_id not in ADMIN_IDS:
        return
    cur = SD_SETTINGS.get("capture_mode", "all")
    nxt = "ttl" if cur == "all" else "all"
    SD_SETTINGS["capture_mode"] = nxt
    save_sd_settings()
    label = "🎥 همه‌ی مدیاها (خودموف + معمولی)" if nxt == "all" else "🔥 فقط خودموشونده"
    await e.reply(f"⚙️ حالت دریافت مدیای کاربران:\n{label}\n"
                  f"(اکانت‌خوان همیشه فقط خودموف است)")


@client.on(events.NewMessage(pattern=r"^/sdoff$"))
async def cmd_sdoff(e):
    uc = sd_clients.pop(e.chat_id, None)
    try:
        if uc:
            await uc.disconnect()
    except Exception:
        pass
    store = _load_sd_store()
    if str(e.chat_id) in store:
        store.pop(str(e.chat_id))
        SD_FILE.write_text(json.dumps(store, ensure_ascii=False))
        persist.mark_dirty()
    await e.reply("🔌 اتصال عکسِ خودمحو قطع شد.", buttons=kb_for(e.chat_id))


# ═══════════════════ 📥 کانال محدود — دانلود از کانال/گروه‌های محدودشده ═══════════════════
# کانال‌هایی که «ذخیره محتوا» دارند و فوروارد/کپی‌شان ممکن نیست:
# با اکانتِ خودِ کاربر (یا اکانت‌خوان برای مدیر) دانلود می‌کنیم و به
# پیام‌های ذخیره‌شده (سیو مسیج) همان اکانت می‌فرستیم؛ برای مدیر یک نسخه هم
# در بخش «عکس‌های دانلودی» گروه مای‌ربات می‌گذاریم و در همان بخشِ گروه هم
# مدیر می‌تواند مستقیم @کانال + اسم فایل بفرستد.

DL_FILE = Path("/tmp/dl_sessions.json")     # نشست‌های کاربرانِ بخش کانال محدود
DL_SCAN_LIMIT = int(os.environ.get("DL_SCAN_LIMIT", "1500") or 1500)
DL_MAX_LIST = 20
dl_clients = {}      # chat_id(int) → TelegramClient
DL_CTX = {}          # chat_id → {"entity":…, "list":[msg…], "group":bool, "ts":…}
DL_BUSY = {}         # chat_id → bool

DL_LINK_RE = re.compile(
    r"(?:t\.me|telegram\.me)/(?:c/)?([A-Za-z0-9_]{4,64}|\d{2,20})/(\d{1,12})")


def _load_dl_store() -> dict:
    try:
        return json.loads(DL_FILE.read_text())
    except Exception:
        return {}


def _hsize_dl(n):
    try:
        n = int(n)
    except Exception:
        return "?"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} گیگ"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} مگ"
    if n >= 1024:
        return f"{n / 1024:.0f} کیلو"
    return f"{n} بایت"


def _dl_media_name(m):
    """نام فایل پیام — از attributeها؛ عکس → «عکس»"""
    d = getattr(m, "document", None)
    if d:
        for a in (getattr(d, "attributes", None) or []):
            fn = getattr(a, "file_name", None)
            if fn:
                return fn
        for a in (getattr(d, "attributes", None) or []):
            t_ = getattr(a, "title", None)
            if t_:
                return t_
        return None
    if getattr(m, "photo", None):
        return "عکس"
    return None


def _dl_media_size(m):
    d = getattr(m, "document", None)
    if d is not None:
        return int(getattr(d, "size", 0) or 0)
    if getattr(m, "photo", None):
        return 0
    return 0


def _dl_media_emoji(m):
    if getattr(m, "video", None) or getattr(m, "gif", None):
        return "🎞"
    if getattr(m, "voice", None) or getattr(m, "audio", None):
        return "🎵"
    if getattr(m, "photo", None):
        return "🖼"
    return "📄"


# ── نرمال‌سازی متن فارسی برای جستجو (ي/ی، ك/ک، نیم‌فاصله، ارقام و…) ──
_DL_NORM_MAP = {
    "ي": "ی", "ك": "ک", "ؤ": "و", "ئ": "ی", "إ": "ا", "أ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه", "ۀ": "ه", "ُ": "", "ِ": "", "َ": "", "ّ": "", "ْ": "", "ٌ": "", "ٍ": "", "ً": "",
    "\u200c": " ", "\u200d": "", "\u200e": "", "\u200f": "", "\ufeff": "", "\u0640": "",
}
_DL_TR1 = str.maketrans(_DL_NORM_MAP)
_DL_TR2 = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _dl_norm(s: str) -> str:
    try:
        s = (s or "").translate(_DL_TR1).translate(_DL_TR2)
        s = re.sub(r"\s+", " ", s)
        return s.strip().lower()
    except Exception:
        return (s or "").strip().lower()


def _dl_match(qq: str, nn: str) -> bool:
    """تطبیق جستجو: زیررشته یا همه‌ی کلمات (با نرمال‌سازی فارسی)"""
    if not qq or not nn:
        return False
    if qq in nn:
        return True
    ws = [w for w in qq.split() if len(w) >= 2]
    if len(ws) > 1 and all(w in nn for w in ws):
        return True
    return False


def _dl_dlg_emoji(ent):
    if getattr(ent, "broadcast", False) and not getattr(ent, "megagroup", False):
        return "📢"
    return "👥"


def _dl_mode_of(uc):
    try:
        if uc is user_client:
            return "shared"
    except Exception:
        pass
    return "personal"


def _dl_all_clients(uid, primary=None):
    """اکانت‌های قابل جستجو: اول اکانت شخصیِ کاربر، بعد (فقط برای مدیر) اکانت‌خوان"""
    out, seen = [], set()
    for uc in (primary, dl_clients.get(int(uid))):
        if uc and getattr(uc, "is_connected", lambda: False)() and id(uc) not in seen:
            seen.add(id(uc))
            out.append(uc)
    try:
        if (int(uid) in ADMIN_IDS and user_client
                and user_client.is_connected() and id(user_client) not in seen):
            out.append(user_client)
    except Exception:
        pass
    return out


async def _dl_search_dialogs_multi(uclist, query):
    """جستجوی اسم در چت‌های همه‌ی اکانت‌های داده‌شده — برای کانال‌هایی که آیدی ندارند.
    خروجی: [(uc, entity), …] — هر کانال فقط با همان اکانتی که پیدایش کرده معتبر است."""
    from telethon.tl import types as _tt
    qq = _dl_norm(query)
    if not qq or not uclist:
        return []
    hits, seen = [], set()
    for uc in uclist:
        try:
            async for d in uc.iter_dialogs():
                ent = d.entity
                if ent is None or isinstance(ent, _tt.User):
                    continue          # فقط کانال و گروه — نه چت خصوصی/ربات
                eid = getattr(ent, "id", None)
                if eid is not None and eid in seen:
                    continue
                nm = (d.name or getattr(ent, "title", None) or "")
                un = getattr(ent, "username", None) or ""
                if _dl_match(qq, _dl_norm(nm)) or (un and qq == _dl_norm(un)):
                    if eid is not None:
                        seen.add(eid)
                    hits.append((uc, ent))
                    if len(hits) >= 15:
                        return hits
        except Exception:
            continue
    return hits


async def _dl_name_search_and_show(e, text, group_mode, ucs):
    """جستجوی اسم در چت‌های اکانت‌ها و نمایش دکمه‌های انتخاب"""
    status = await e.reply(f"🔍 دنبال «{text[:40]}» در چت‌های اکانت می‌گردم…")
    try:
        pairs = await _dl_search_dialogs_multi(ucs, text)
    except Exception as ex:
        await status.edit(f"❌ خطا در جستجوی چت‌ها: {str(ex)[:100]}")
        return
    if not pairs:
        names = []
        for uc in ucs:
            try:
                me = await uc.get_me()
                ph = str(getattr(me, "phone", "") or "")
                names.append((getattr(me, "first_name", None) or "؟")
                             + (f" (+…{ph[-4:]})" if ph else ""))
            except Exception:
                names.append("؟")
        await status.edit(
            "😕 چیزی با این اسم پیدا نشد.\n\n"
            f"🧐 جستجو شد در چت‌های: {'، '.join(names) or '—'}\n\n"
            "• آن کانال/گروه باید در چت‌های همین اکانت باشد (عضو باشد)\n"
            "• اسم را کوتاه‌تر بنویس — بخشی از اسم کافی است\n"
            "• اگر آیدی لاتین دارد با @ بفرست: @اسمکانال اسم‌فایل")
        return
    DL_DLG[int(e.chat_id)] = {"pairs": pairs, "group": group_mode, "ts": time.time()}
    rows = [[Button.inline(
        f"{_dl_dlg_emoji(en)} {(getattr(en, 'title', None) or '?')}"[:60],
        f"dl:g:{i}")] for i, (u_, en) in enumerate(pairs)]
    await status.edit(f"🎯 {len(pairs)} کانال/گروه پیدا شد — کدام را می‌خواهی؟",
                      buttons=rows)


def _dl_client_for(uid):
    """کلاینت دانلود: اول اکانت شخصی کاربر؛ برای مدیر، اکانت‌خوان اصلی هم قبول است.
    خروجی: (client یا None, "personal"|"shared"|None)"""
    uc = dl_clients.get(int(uid))
    if uc and uc.is_connected():
        return uc, "personal"
    if int(uid) in ADMIN_IDS and user_client and user_client.is_connected():
        return user_client, "shared"
    return None, None


async def _start_dl_client(owner_id, uc):
    dl_clients[int(owner_id)] = uc
    asyncio.ensure_future(uc.run_until_disconnected())


async def _restore_dl_sessions():
    """بعد از ری‌استارت: نشست‌های کانالِ محدود کاربران خودکار وصل می‌شوند"""
    store = _load_dl_store()
    for k, sess in list(store.items()):
        try:
            uc = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await uc.connect()
            if await uc.is_user_authorized():
                await _start_dl_client(int(k), uc)
            else:
                store.pop(k)
                DL_FILE.write_text(json.dumps(store, ensure_ascii=False))
        except Exception:
            continue
    if dl_clients:
        log.info("dl: %d نشستِ کانال‌محدود وصل شد", len(dl_clients))


def _dl_parse_target(t):
    """«@کانال» / «t.me/کانال» / لینک پیام / آیدی عددی → (entity_ref, msg_id|None)"""
    t = (t or "").strip()
    m = DL_LINK_RE.search(t)
    if m:
        a, b = m.group(1), int(m.group(2))
        if a.isdigit():
            return int("-100" + a), b
        return a, b
    t2 = re.sub(r"^(?:https?://)?(?:t\.me|telegram\.me)/", "", t).strip().strip("/")
    t2 = t2.split("?")[0].rstrip("/")
    if re.match(r"^@[A-Za-z0-9_]{4,64}$", t2):
        return t2[1:], None
    if re.match(r"^[A-Za-z0-9_]{4,64}$", t2):
        return t2, None
    if re.match(r"^-100\d{5,}$", t2):
        return int(t2), None
    return None, None


def _dl_result_kb(lst):
    rows = []
    for i, m in enumerate(lst):
        nm = (_dl_media_name(m) or "فایل")[:34]
        sz = _dl_media_size(m)
        lbl = f"{_dl_media_emoji(m)} {nm}" + (f" · {_hsize_dl(sz)}" if sz else "")
        rows.append([Button.inline(lbl[:60], f"dl:p:{i}")])
    return rows


@client.on(events.NewMessage(pattern=r"^📥 کانال محدود$"))
async def btn_dl(e):
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "dl"):
        await e.reply(_need_sub_reply(e.chat_id),
                      buttons=[[Button.inline("🔑 فعال‌سازی اشتراک", b"sub:start")]])
        return
    uc, mode = _dl_client_for(e.sender_id)
    if mode == "personal":
        head = "📥 کانال محدود — اکانتت وصله ✅"
        rows = [[Button.inline("📂 شروع دانلود", b"dl:go")],
                [Button.inline("🔌 قطع اتصال اکانت من", b"dl:off")]]
    elif mode == "shared":
        head = ("📥 کانال محدود\n\n⚠️ اکانت شخصی‌ات وصل نیست — فعلاً با «اکانت‌خوان» اصلی "
                "دانلود می‌کنم و فایل‌ها به سیو مسیجِ همان اکانت می‌روند.\n"
                "برای اینکه به سیو مسیجِ خودت برود، اکانتت را وصل کن 👇")
        rows = [[Button.inline("🔗 وصل کردن اکانت من", b"dl:conn")],
                [Button.inline("📂 شروع دانلود (با اکانت‌خوان)", b"dl:go")]]
    else:
        head = "📥 کانال محدود"
        rows = [[Button.inline("🔗 وصل کردن اکانت من", b"dl:conn")]]
    await e.reply(
        f"{head}\n\n"
        "از کانال/گروه‌هایی که فوروارد و ذخیره‌شان بسته است فایل دانلود می‌کنم و به "
        "«پیام‌های ذخیره‌شده» (سیو مسیج) همان اکانت می‌فرستم.\n\n"
        "طریقه استفاده:\n"
        "1️⃣ آدرس کانال را بفرست — @اسمکانال یا لینک t.me/اسمکانال\n"
        "2️⃣ اسم فایل (یا بخشی از اسمش) را بفرست — از آخرین ۱۵۰۰ پیام جستجو می‌کنم\n\n"
        "• لینک مستقیم پیام هم قبول است: t.me/اسمکانال/123\n"
        "• «همه» → آخرین فایل‌های کانال را لیست می‌کنم\n"
        "• «پایان» → بستن بخش\n\n"
        "⚠️ اکانتی که وصل می‌کنی باید عضو همان کانال باشد.",
        buttons=rows)


@client.on(events.NewMessage(pattern=r"^/dl$"))
async def cmd_dl(e):
    await btn_dl(e)


@client.on(events.NewMessage(pattern=r"^/dloff$"))
async def cmd_dloff(e):
    """قطع اتصال اکانتِ بخش کانال محدود"""
    uc = dl_clients.pop(int(e.chat_id), None)
    try:
        if uc:
            await uc.disconnect()
    except Exception:
        pass
    store = _load_dl_store()
    if str(e.chat_id) in store:
        store.pop(str(e.chat_id))
        DL_FILE.write_text(json.dumps(store, ensure_ascii=False))
        persist.mark_dirty()
    await e.reply("🔌 اتصال کانالِ محدود قطع شد.", buttons=kb_for(e.chat_id))


@client.on(events.NewMessage(pattern=r"^/dlusers$"))
async def cmd_dlusers(e):
    """مدیر: لیست اکانت‌های وصل‌شده برای کانال محدود"""
    if e.sender_id not in ADMIN_IDS:
        return
    if not dl_clients:
        await e.reply("هنوز هیچکی برای «📥 کانال محدود» اکانتش را وصل نکرده.")
        return
    lines = [f"👥 اکانت‌های وصل‌شده (کانال محدود): {len(dl_clients)}", ""]
    n = 0
    for uid, uc in list(dl_clients.items()):
        n += 1
        try:
            me = await uc.get_me()
            nm = getattr(me, "first_name", None) or "؟"
            ph = str(getattr(me, "phone", "") or "")
            conn = "🟢" if uc.is_connected() else "🔴"
            lines.append(f"{n}. {conn} {nm} | 🆔 {uid} | 📞 +{ph.lstrip('+')}")
        except Exception as ex:
            lines.append(f"{n}. 🔴 کاربر {uid} (خطا: {str(ex)[:40]})")
    await e.reply("\n".join(lines), link_preview=False)


@client.on(events.CallbackQuery(pattern=r"^dl:"))
async def on_dl_cb(e):
    await note_user(e)
    data = e.data.decode()[3:]
    try:
        await e.answer()
    except Exception:
        pass
    if data == "conn":
        if not is_allowed_feat(e.chat_id, "dl"):
            await e.answer(_need_sub_reply(e.chat_id), alert=True)
            return
        flow[e.chat_id] = {"kind": "dl", "step": "phone", "ts": time.time()}
        await e.edit("📱 شماره‌ی تلگرامت را با کد کشور بفرست:\nمثال: +989123345678\n(لغو: «لغو»)")
        return
    if data == "off":
        flow.pop(e.chat_id, None)
        uc = dl_clients.pop(int(e.chat_id), None)
        try:
            if uc:
                await uc.disconnect()
        except Exception:
            pass
        store = _load_dl_store()
        if str(e.chat_id) in store:
            store.pop(str(e.chat_id))
            DL_FILE.write_text(json.dumps(store, ensure_ascii=False))
            persist.mark_dirty()
        await e.edit("🔌 اتصال کانالِ محدود قطع شد.\nدوباره وصل کنی: دکمه «📥 کانال محدود»")
        return
    if data == "go":
        uc, mode = _dl_client_for(e.sender_id)
        if not uc:
            await e.edit("🔗 اول اکانتت را وصل کن، بعد شروع می‌کنیم 👇",
                         buttons=[[Button.inline("🔗 وصل کردن اکانت من", b"dl:conn")]])
            return
        flow[e.chat_id] = {"kind": "dl", "step": "chat", "ts": time.time()}
        await e.edit("📂 آدرس یا اسم کانال/گروه را بفرست:\n"
                     "• @اسمکانال یا لینک t.me/اسمکانال\n"
                     "• فقط «اسم» کانال/گروه — از چت‌هایی که اکانتت عضوش است\n"
                     "• لینک مستقیم پیام: t.me/اسمکانال/123\n\n"
                     "⚠️ اکانتِ وصل‌شده باید عضو همان کانال باشد.\n(لغو: «لغو»)")
        return
    if data.startswith("g:"):
        try:
            idx = int(data.split(":")[1])
        except Exception:
            return
        dd = DL_DLG.get(int(e.chat_id))
        if not dd or time.time() - dd.get("ts", 0) > 1800:
            await e.answer("⌛ لیست منقضی شده — دوباره اسم را بفرست", alert=True)
            return
        pairs = dd.get("pairs") or []
        if idx >= len(pairs):
            await e.answer("این گزینه دیگر معتبر نیست", alert=True)
            return
        uc_d, entity = pairs[idx]
        if not uc_d or not uc_d.is_connected():
            await e.answer("اکانتی که این را پیدا کرد الان قطع است — دوباره تلاش کن",
                           alert=True)
            return
        title = (getattr(entity, "title", None) or "?")
        flow[int(e.chat_id)] = {"kind": "dl", "step": "query", "entity": entity,
                                "uc": uc_d, "mode": _dl_mode_of(uc_d),
                                "ts": time.time(),
                                **({"group": True} if dd.get("group") else {})}
        await e.edit(f"📂 «{title}» باز شد ✅\n\n"
                     "حالا اسم فایل (یا بخشی از اسمش) را بفرست.\n"
                     "«همه» → لیست آخرین فایل‌ها")
        return
    if data.startswith("p:"):
        try:
            idx = int(data.split(":")[1])
        except Exception:
            return
        ctx = DL_CTX.get(int(e.chat_id))
        if not ctx or time.time() - ctx.get("ts", 0) > 1800:
            await e.answer("⌛ لیست منقضی شده — دوباره جستجو کن", alert=True)
            return
        if idx >= len(ctx["list"]):
            await e.answer("این گزینه دیگر معتبر نیست", alert=True)
            return
        uc = ctx.get("uc")
        mode = ctx.get("mode") or "personal"
        if not (uc and uc.is_connected()):
            uc, mode = _dl_client_for(e.sender_id)
        if not uc:
            await e.answer("اکانت وصل نیست — دکمه 📥 کانال محدود", alert=True)
            return
        asyncio.ensure_future(_dl_deliver(int(e.chat_id), uc, mode,
                                          ctx["entity"], ctx["list"][idx],
                                          group_mode=bool(ctx.get("group"))))
        return


async def _dl_search(uc, entity, query, status, browse=False):
    q = (query or "").lower().strip()
    out, seen = [], 0
    async for m in uc.iter_messages(entity, limit=DL_SCAN_LIMIT):
        seen += 1
        if seen % 250 == 0:
            try:
                await status.edit(f"🔍 {seen} پیام بررسی شد — {len(out)} مورد پیدا شد…")
            except Exception:
                pass
        nm = _dl_media_name(m)
        if not nm:
            continue
        if browse or (q in nm.lower()):
            out.append(m)
            if len(out) >= DL_MAX_LIST:
                break
    return out, seen


async def _dl_run_search(e, uc, entity, query, status, browse=False, group_mode=False):
    try:
        lst, seen = await _dl_search(uc, entity, query, status, browse=browse)
    except Exception as ex:
        sec = getattr(ex, "seconds", None)
        if sec:
            await status.edit(f"⏳ تلگرام موقتاً محدود کرد — {int(sec)} ثانیه دیگر دوباره بزن.")
        else:
            await status.edit(f"❌ خطا در جستجو: {str(ex)[:130]}")
        return
    if not lst:
        await status.edit(
            "😕 چیزی پیدا نشد.\n\n"
            "• اسم را کوتاه‌تر بفرست — فقط بخشی از اسم فایل کافی است\n"
            "• «همه» را امتحان کن (لیست آخرین فایل‌ها)\n"
            f"• {seen} پیام آخر بررسی شد")
        return
    DL_CTX[int(e.chat_id)] = {"entity": entity, "list": lst,
                              "group": group_mode, "ts": time.time(),
                              "uc": uc, "mode": _dl_mode_of(uc)}
    await status.edit(
        f"🎯 {len(lst)} مورد پیدا شد (از {seen} پیام):\n"
        "روی فایل بزن تا دانلود شود" +
        (" و همین‌جا در گروه قرار بگیرد 👇" if group_mode else " و به سیو مسیج برود 👇"),
        buttons=_dl_result_kb(lst))


async def _dl_deliver(chat_id, uc, mode, entity, msg, group_mode=False):
    """دانلود پیام و تحویل: سیو مسیج اکانت (+ نسخه گروه برای مدیر) یا خودِ تاپیک گروه"""
    if DL_BUSY.get(chat_id):
        try:
            await client.send_message(chat_id, "⏳ یک دانلود دیگر در جریان است — کمی صبر کن.")
        except Exception:
            pass
        return
    DL_BUSY[chat_id] = True
    status = None
    path = None
    try:
        name = (_dl_media_name(msg) or "فایل")
        size = _dl_media_size(msg)
        if size > BIG_FILE_MAX:
            await client.send_message(chat_id, "❌ تلگرام حتی با اکانت پرمیوم بالاتر از ~۴ گیگابایت را منتقل نمی‌کند.")
            return
        if size > ACCEPT_MAX and not is_gold(chat_id):
            await client.send_message(
                chat_id, "👑 تحویل فایل بالای ۲ گیگابایت نیازمند «اکانت طلایی» است.\n"
                         "رایگان طلایی شو: دکمهٔ «👤 اطلاعات من» لینک دعوتت را نشان می‌دهد (سه نفر)، یا به پشتیبانی پیام بده.")
            return
        ent_title = (getattr(entity, "title", None)
                     or getattr(entity, "username", None) or "کانال")
        status = await client.send_message(chat_id, f"⬇️ در حال دانلود «{name[:60]}»…")
        last = [0.0]

        def _prog(cur, tot):
            now = time.time()
            if now - last[0] < 3 or not tot:
                return
            last[0] = now
            try:
                pct = int(cur * 100 / tot)
                asyncio.ensure_future(status.edit(
                    f"⬇️ «{name[:40]}» — {pct}% ({_hsize_dl(cur)} از {_hsize_dl(tot)})"))
            except Exception:
                pass

        path = await uc.download_media(
            msg, file=f"/tmp/dlfile_{int(chat_id) % 10000}_{int(time.time())}",
            progress_callback=_prog)
        if not path or not Path(path).exists() or Path(path).stat().st_size == 0:
            await status.edit("❌ دانلود ناموفق بود.")
            return
        cap = (f"📥 {name}\n"
               f"📡 منبع: {ent_title}\n"
               f"📦 حجم: {_hsize_dl(Path(path).stat().st_size)}\n"
               f"🤖 آچارفرانسه")
        if group_mode:
            cfg = GROUP_CFG.get("media") or {}
            await client.send_file(int(cfg.get("chat") or chat_id), path,
                                   caption=cap, reply_to=cfg.get("topic"),
                                   supports_streaming=True)
            await status.edit(f"✅ «{name[:50]}» در همین بخش قرار گرفت.")
        else:
            await uc.send_file("me", path, caption=cap, supports_streaming=True)
            extra = ""
            gcfg = GROUP_CFG.get("media")
            if gcfg and int(chat_id) in ADMIN_IDS:
                try:
                    await client.send_file(int(gcfg["chat"]), path, caption=cap,
                                           reply_to=gcfg["topic"],
                                           supports_streaming=True)
                    extra = "\n📬 یک نسخه هم در بخش «عکس‌های دانلودی» گروه گذاشته شد."
                except Exception:
                    pass
            where = ("اکانت‌خوان" if mode == "shared" else "اکانت خودت")
            await status.edit(
                f"✅ «{name[:50]}» به پیام‌های ذخیره‌شده (سیو مسیج) {where} رفت.{extra}")
    except Exception as ex:
        try:
            if status:
                await status.edit(f"❌ خطا: {str(ex)[:150]}")
            else:
                await client.send_message(chat_id, f"❌ خطا: {str(ex)[:150]}")
        except Exception:
            pass
    finally:
        DL_BUSY.pop(chat_id, None)
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass


async def _dl_finish(e, uc, st):
    flow.pop(e.chat_id, None)
    store = _load_dl_store()
    store[str(e.chat_id)] = uc.session.save()
    DL_FILE.write_text(json.dumps(store, ensure_ascii=False))
    persist.mark_dirty()
    await _start_dl_client(e.chat_id, uc)
    await e.reply(
        "✅ اکانتت وصل شد! حالا:\n"
        "1️⃣ آدرس کانال را بفرست (@اسم یا لینک)\n"
        "2️⃣ اسم فایل را بفرست\n\n"
        "فایل‌ها به سیو مسیج خودت می‌روند 📥\n"
        "قطع: /dloff",
        buttons=kb_for(e.chat_id))


async def _dl_login_text(e, st, t):
    """مراحل اتصال اکانت (مثل عکس خودموف): شماره → کد → رمز"""
    if t == "لغو":
        flow.pop(e.chat_id, None)
        await e.reply("لغو شد ✅", buttons=kb_for(e.chat_id))
        return
    if st["step"] == "phone":
        if not re.match(r"^\+?\d{10,15}$", t):
            await e.reply("شماره معتبر نیست؛ با +98 شروع کن (یا بنویس: لغو)")
            return
        phone = t if t.startswith("+") else "+" + t
        try:
            uc = TelegramClient(StringSession(), API_ID, API_HASH)
            await uc.connect()
            await uc.send_code_request(phone)
        except Exception as ex:
            flow.pop(e.chat_id, None)
            await e.reply(f"❌ ارسال کد ناموفق: {str(ex)[:120]}")
            return
        st.update({"step": "code", "phone": phone, "client": uc, "ts": time.time()})
        await e.reply("📲 کد را با کیبورد زیر بزن:", buttons=codepad())
    elif st["step"] == "code":
        uc = st.get("client")
        act, code = _code_input(st, t)
        if act == "progress":
            await e.reply(f"🔢 تا الان: {'●' * len(code)}\n(بعدش «✅ ثبت کد»)",
                          buttons=codepad())
            return
        if act != "submit" or not code:
            await e.reply("با کیبورد عددی بزن!", buttons=codepad())
            return
        st["ts"] = time.time()
        try:
            await uc.sign_in(st["phone"], code)
        except SessionPasswordNeededError:
            st.update({"step": "pass", "ts": time.time()})
            await e.reply("🔒 رمز دومرحله‌ای فعاله؛ پسوردت را بفرست:")
            return
        except Exception as ex:
            await e.reply(f"❌ کد درست نبود؛ دوباره بزن. ({str(ex)[:60]})",
                          buttons=codepad())
            return
        await _dl_finish(e, uc, st)
    elif st["step"] == "pass":
        uc = st.get("client")
        try:
            await uc.sign_in(password=t)
        except Exception:
            await e.reply("❌ پسورد درست نبود؛ دوباره بفرست.")
            return
        await _dl_finish(e, uc, st)


async def handle_dl_text(e):
    st = flow.get(e.chat_id)
    t = (e.text or "").strip()
    if st.get("step") in ("phone", "code", "pass"):
        await _dl_login_text(e, st, t)
        return
    if t in ("لغو", "پایان", "/end"):
        flow.pop(e.chat_id, None)
        await e.reply("📥 بخش کانال محدود بسته شد.", buttons=kb_for(e.chat_id))
        return
    if t in (BTN_HELP, BTN_STATUS, BTN_ADMIN, BTN_BARGH, BTN_SUPPORT, BTN_SELFDES,
             BTN_AI, BTN_YT, BTN_FINDER, BTN_SUB, BTN_INSTA, BTN_ANTEN, BTN_STT,
             BTN_LINK, BTN_DL, BTN_TAG):
        await e.reply("📥 داخل بخش کانال محدودی.\n"
                      "آدرس کانال یا اسم فایل را بفرست — یا «پایان» برای خروج.")
        return
    st["ts"] = time.time()
    uc = st.get("uc")
    mode = st.get("mode")
    if not (uc and uc.is_connected()):
        uc, mode = _dl_client_for(e.sender_id)
    if not uc:
        flow.pop(e.chat_id, None)
        await e.reply("🔗 اکانتت وصل نیست — دکمه «📥 کانال محدود» را بزن و اکانتت را وصل کن.",
                      buttons=kb_for(e.chat_id))
        return

    if st.get("step") == "chat":
        parts = t.split(None, 1)
        cand = parts[0]
        rest = parts[1].strip() if len(parts) > 1 else ""
        ent_full, mid_full = _dl_parse_target(t)
        ent1, mid1 = _dl_parse_target(cand)
        cand_is_addr = (cand.startswith("@") or "t.me/" in cand
                        or "telegram.me/" in cand or cand.startswith("http"))
        if ent_full and mid_full:
            target, query, mid = ent_full, None, mid_full
        elif cand_is_addr and ent1:
            target, query, mid = ent1, (rest or None), None
        elif ent1 and rest and cand.startswith("-100"):
            target, query, mid = ent1, rest, None
        elif ent1 and rest:
            # توکن اول لاتین است ولی کل متن ممکن است «اسم» باشد — اول چت‌ها را می‌گردیم
            ucs = _dl_all_clients(e.sender_id, uc)
            pairs = await _dl_search_dialogs_multi(ucs, t)
            if pairs:
                DL_DLG[int(e.chat_id)] = {"pairs": pairs, "group": False, "ts": time.time()}
                rows = [[Button.inline(
                    f"{_dl_dlg_emoji(en)} {(getattr(en, 'title', None) or '?')}"[:60],
                    f"dl:g:{i}")] for i, (u_, en) in enumerate(pairs)]
                await e.reply(f"🎯 {len(pairs)} کانال/گروه پیدا شد — کدام را می‌خواهی؟",
                              buttons=rows)
                return
            target, query, mid = ent1, rest, None          # پیدا نشد — مسیر آدرس
        elif ent_full and not mid_full and not rest:
            target, query, mid = ent_full, None, None
        else:
            # «اسم» کانال/گروه — جستجو در چت‌های اکانت (حتی بدون آیدی عمومی)
            await _dl_name_search_and_show(e, t, False, _dl_all_clients(e.sender_id, uc))
            return
        status = await e.reply("🔎 در حال باز کردن کانال…")
        try:
            entity = await uc.get_entity(target)
        except Exception as ex:
            ucs = _dl_all_clients(e.sender_id, uc)
            pairs = await _dl_search_dialogs_multi(ucs, t)
            if pairs:
                DL_DLG[int(e.chat_id)] = {"pairs": pairs, "group": False, "ts": time.time()}
                rows = [[Button.inline(
                    f"{_dl_dlg_emoji(en)} {(getattr(en, 'title', None) or '?')}"[:60],
                    f"dl:g:{i}")] for i, (u_, en) in enumerate(pairs)]
                await status.edit(f"🎯 {len(pairs)} کانال/گروه پیدا شد — کدام را می‌خواهی؟",
                                  buttons=rows)
                return
            await status.edit(f"❌ نشد بازش کنم: {str(ex)[:110]}\n\n"
                              "⚠️ اکانتِ وصل‌شده باید عضو این کانال باشد "
                              "(برای کانال خصوصی عضویت لازم است).")
            return
        if getattr(entity, "first_name", None) and not getattr(entity, "title", None):
            await status.edit("❌ این آدرس یک کاربر است، نه کانال/گروه.")
            return
        if mid:
            try:
                msgs = await uc.get_messages(entity, ids=[int(mid)])
                msg = msgs[0] if msgs else None
            except Exception as ex:
                await status.edit(f"❌ پیام پیدا نشد: {str(ex)[:100]}")
                return
            if not msg or not _dl_media_name(msg):
                await status.edit("❌ این پیام فایل ندارد (یا حذف شده).")
                return
            DL_CTX[int(e.chat_id)] = {"entity": entity, "list": [msg],
                                      "group": False, "ts": time.time(),
                                      "uc": uc, "mode": _dl_mode_of(uc)}
            await status.edit(
                f"✅ پیدا شد:\n"
                f"{_dl_media_emoji(msg)} «{_dl_media_name(msg)}» · {_hsize_dl(_dl_media_size(msg))}",
                buttons=[[Button.inline("⬇️ دانلود و ارسال به سیو مسیج", "dl:p:0")]])
            return
        st.update({"step": "query", "entity": entity, "ts": time.time()})
        if query:
            await _dl_run_search(e, uc, entity, query, status)
        else:
            await status.edit(
                f"📂 کانال «{getattr(entity, 'title', None) or target}» باز شد ✅\n\n"
                "حالا اسم فایل (یا بخشی از اسمش) را بفرست.\n"
                "«همه» → لیست آخرین فایل‌های کانال")
        return

    if st.get("step") == "query":
        entity = st.get("entity")
        if not entity:
            st.update({"step": "chat", "ts": time.time()})
            await e.reply("آدرس کانال را بفرست:")
            return
        # «@کانال‌دیگر» یا «@کانال‌دیگر اسم‌فایل» هم قبول است
        parts = t.split(None, 1)
        ent1, mid1 = _dl_parse_target(parts[0])
        if ent1 and not mid1:
            status = await e.reply("🔎 در حال باز کردن کانال…")
            try:
                entity = await uc.get_entity(ent1)
                st["entity"] = entity
                st["ts"] = time.time()
            except Exception as ex:
                await status.edit(f"❌ نشد بازش کنم: {str(ex)[:110]}")
                return
            rest = parts[1].strip() if len(parts) > 1 else ""
            if rest:
                await _dl_run_search(e, uc, entity, rest, status)
            else:
                await status.edit(
                    f"📂 کانال «{getattr(entity, 'title', None) or ent1}» باز شد ✅\n"
                    "اسم فایل را بفرست («همه» → لیست آخرین فایل‌ها)")
            return
        status = await e.reply("🔍 شروع جستجو…")
        await _dl_run_search(e, uc, entity, t, status,
                             browse=(t.strip() in ("همه", "all", "لیست")))
        return
    # مرحله نامعلوم
    st.update({"step": "chat", "ts": time.time()})
    await e.reply("آدرس کانال را بفرست (@اسم یا لینک):")


@client.on(events.NewMessage(func=lambda e: (
        e.is_group and (e.text or "").strip() and not (e.text or "").startswith("/"))))
async def on_dl_group(e):
    """نسخه‌ی گروهی: در بخش «عکس‌های دانلودی» گروه مای‌ربات، مدیر می‌فرستد:
    @کانال  یا  @کانال اسم‌فایل  یا  لینک پیام → فایل همین‌جا در تاپیک می‌آید."""
    cfg = GROUP_CFG.get("media")
    if not cfg:
        return
    try:
        if int(e.chat_id) != int(cfg["chat"]) or int(_topic_id_of(e)) != int(cfg["topic"]):
            return
    except Exception:
        return
    if e.sender_id not in ADMIN_IDS:
        return
    t = (e.text or "").strip()
    parts = t.split(None, 1)
    ent, mid = _dl_parse_target(parts[0])
    if not ent:
        # فلوی گروهی: اسم فایل برای کانالِ انتخاب‌شده (بعد از انتخاب از دکمه‌ها)
        st_g = flow.get(e.chat_id)
        if (st_g and st_g.get("kind") == "dl" and st_g.get("step") == "query"
                and st_g.get("group") and st_g.get("entity")):
            if t in ("لغو", "پایان", "/end"):
                flow.pop(e.chat_id, None)
                await e.reply("📥 جستجوی گروهی بسته شد.")
                return
            uc = st_g.get("uc")
            if not (uc and uc.is_connected()):
                uc, _m = _dl_client_for(e.sender_id)
            if not uc:
                await e.reply("🔗 اکانت دانلود قطع شده — دوباره وصل کن.")
                return
            status = await e.reply("🔍 شروع جستجو…")
            await _dl_run_search(e, uc, st_g["entity"], t, status,
                                 browse=t.strip() in ("همه", "all", "لیست"),
                                 group_mode=True)
            return
        # شاید «اسم» کانال/گروه است — در چت‌های اکانت می‌گردیم
        uc0, _m0 = _dl_client_for(e.sender_id)
        if uc0 and t and not t.startswith("/"):
            pairs = await _dl_search_dialogs_multi(
                _dl_all_clients(e.sender_id, uc0), t)
            if pairs:
                DL_DLG[int(e.chat_id)] = {"pairs": pairs, "group": True, "ts": time.time()}
                rows = [[Button.inline(
                    f"{_dl_dlg_emoji(en)} {(getattr(en, 'title', None) or '?')}"[:60],
                    f"dl:g:{i}")] for i, (u_, en) in enumerate(pairs)]
                await e.reply(f"🎯 {len(pairs)} کانال/گروه با این اسم پیدا شد — "
                             "کدام را می‌خواهی؟", buttons=rows)
                return
        return          # پیام عادی این بخش است — کاری نکن
    uc, mode = _dl_client_for(e.sender_id)
    if not uc:
        await e.reply("🔗 هنوز اکانتی برای دانلود وصل نیست — توی پیوی ربات "
                      "«📥 کانال محدود» اکانتت را وصل کن (یا /login اکانت‌خوان).")
        return
    if mid:
        status = await e.reply("🔎 در حال باز کردن پیام…")
        try:
            entity = await uc.get_entity(ent)
            msgs = await uc.get_messages(entity, ids=[int(mid)])
            msg = msgs[0] if msgs else None
        except Exception as ex:
            await status.edit(f"❌ {str(ex)[:120]}")
            return
        if not msg or not _dl_media_name(msg):
            await status.edit("❌ این پیام فایل ندارد (یا حذف شده).")
            return
        DL_CTX[int(e.chat_id)] = {"entity": entity, "list": [msg],
                                  "group": True, "ts": time.time(),
                                  "uc": uc, "mode": _dl_mode_of(uc)}
        await status.edit(
            f"✅ پیدا شد:\n"
            f"{_dl_media_emoji(msg)} «{_dl_media_name(msg)}» · {_hsize_dl(_dl_media_size(msg))}",
            buttons=[[Button.inline("⬇️ دانلود و قرار دادن در همین بخش", "dl:p:0")]])
        return
    query = parts[1].strip() if len(parts) > 1 else ""
    status = await e.reply("🔎 در حال باز کردن کانال…")
    try:
        entity = await uc.get_entity(ent)
    except Exception as ex:
        await status.edit(f"❌ نشد بازش کنم: {str(ex)[:110]}\n⚠️ اکانتِ وصل‌شده باید عضو باشد.")
        return
    if getattr(entity, "first_name", None) and not getattr(entity, "title", None):
        await status.edit("❌ این آدرس یک کاربر است، نه کانال/گروه.")
        return
    await _dl_run_search(e, uc, entity, query, status,
                         browse=not query, group_mode=True)


# ═══════════════════ 🏷 تگ فایل — تغییر اسم/تگ فایل‌های تلگرامی ═══════════════════
# کاربر فایل می‌فرستد → اسم/تگ جدید را می‌گوید → فایل با اسم جدید برمی‌گردد
# (دانلود + آپلود مجدد؛ محتوای فایل دست نمی‌خورد، فقط اسم و تگ‌های نمایشی)
# + یک نسخه از فایل تگ‌خورده برای مدیر در گروه مای‌ربات (بخش «تگ»، یا در نبودش «عکس‌های دانلودی»)

TAG_BUSY = {}   # chat_id → bool

_TAG_BAD_CHARS = re.compile(r"[\x00-\x1f/\\:\"*?<>|]")


def _tag_sanitize_name(name: str) -> str:
    """اسم فایل جدید را تمیز می‌کند (بدون مسیر، بدون کاراکتر ممنوع)"""
    name = _TAG_BAD_CHARS.sub(" ", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip().strip(". ")
    while len(name.encode("utf-8")) > 180:
        name = name[:-1]
    return name.strip()


def _tag_with_ext(new_name: str, old_name: str) -> str:
    """اگر اسم جدید پسوند ندارد، پسوند فایل قبلی نگه داشته می‌شود"""
    if "." in new_name.lstrip("."):
        return new_name
    ext = Path(old_name or "").suffix if "." in (old_name or "") else ""
    return (new_name + ext).strip()


def _tag_target_section():
    """بخش گروه برای نسخه‌ی مدیر: اول «تگ»، بعد «عکس‌های دانلودی»"""
    return GROUP_CFG.get("tag") or GROUP_CFG.get("media")


@client.on(events.NewMessage(pattern=r"^🏷 تگ فایل$"))
async def btn_tag(e):
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "tag"):
        await e.reply(_need_sub_reply(e.chat_id),
                      buttons=[[Button.inline("🔑 فعال‌سازی اشتراک", b"sub:start")]])
        return
    flow[e.chat_id] = {"kind": "tag", "step": "file", "ts": time.time()}
    await e.reply(
        "🏷 تگ فایل — اتاق تگ باز شد 🔒\n\n"
        "تا وقتی اینجا هستی، حواس من فقط به تگ است:\n"
        "1️⃣ **فایلت را بفرست** (فیلم، آهنگ، هر فایلی — تا ۱٫۸ گیگ)\n"
        "2️⃣ **اسم/تگ جدید** — اختیاری؛ رد کنی، اسم قبلی می‌ماند\n"
        "3️⃣ برای ویدیو و آهنگ: **کاور/تامنیل جدید** — اختیاری\n\n"
        "کیبوردت الان فقط دو دکمه دارد: «⏭ مرحله بعد» و «✖ لغو و خروج»\n"
        "با خروج، منوی اصلی برمی‌گردد.",
        buttons=tag_room_kb())


@client.on(events.CallbackQuery(pattern=r"^tag:"))
async def on_tag_cb(e):
    """دکمه‌ی شیشه‌ای خروج از بخش تگ"""
    await note_user(e)
    data = e.data.decode()[4:]
    try:
        await e.answer()
    except Exception:
        pass
    if data == "cancel":
        if TAG_BUSY.get(int(e.chat_id)):
            try:
                await e.answer("⏳ تگ در جریان است — بعد از تمام شدن می‌توانی خارج شوی", alert=True)
            except Exception:
                pass
            return
        st = flow.get(int(e.chat_id))
        if st and st.get("kind") == "tag":
            flow.pop(int(e.chat_id), None)
        try:
            await e.edit("🏷 بخش تگ فایل بسته شد ✅", buttons=None)
        except Exception:
            pass
        try:
            await client.send_message(int(e.chat_id), "🔙 به منوی اصلی برگشتی 👇",
                                      buttons=kb_for(int(e.chat_id)))
        except Exception:
            pass
        return
    if data == "skipname":
        st = flow.get(int(e.chat_id))
        if st and st.get("kind") == "tag" and st.get("step") == "name":
            try:
                await e.edit("⏭ اسم قبلی نگه داشته شد", buttons=None)
            except Exception:
                pass
            await _tag_after_name(int(e.chat_id), st, st.get("old") or "فایل")
        return
    if data == "skipthumb":
        st = flow.get(int(e.chat_id))
        if st and st.get("kind") == "tag" and st.get("step") == "thumb":
            msg = st.get("msg")
            name = st.get("new_name") or st.get("old") or "فایل"
            flow.pop(int(e.chat_id), None)
            try:
                await e.edit("⏭ کاور جدیدی گذاشته نمی‌شود — شروع تگ…", buttons=None)
            except Exception:
                pass
            asyncio.ensure_future(_tag_run(int(e.chat_id), msg, name))
        return


@client.on(events.NewMessage(pattern=r"^/tag$"))
async def cmd_tag(e):
    await btn_tag(e)


def _tag_is_media(m):
    """ویدیو یا موسیقی؟ (برای پیشنهاد کاور/تامنیل)"""
    try:
        if getattr(m, "voice", None):
            return False
        if getattr(m, "video", None) or getattr(m, "gif", None) or getattr(m, "audio", None):
            return True
        d = getattr(m, "document", None)
        mt = getattr(d, "mime_type", None) if d is not None else None
        return bool(mt and mt.startswith(("video/", "audio/")))
    except Exception:
        return False


@client.on(events.NewMessage(func=lambda e: e.message.file is not None))
async def on_tag_file(e):
    """فایل داخل بخش تگ — قبل از هندلر فشرده‌سازی می‌گیردش"""
    if not e.is_private:
        return
    st_t = flow.get(e.chat_id)
    if not (st_t and st_t.get("kind") == "tag"):
        return
    await note_user(e)
    if TAG_BUSY.get(e.chat_id):
        await e.reply("⏳ یک تگ دیگر در جریان است — اول تمامش کن.")
        raise events.StopPropagation
    m = e.message
    step = st_t.get("step")

    # ─── مرحله‌ی کاور: عکس جدید ───
    if step == "thumb":
        if getattr(m, "sticker", None):
            await e.reply("🏳 استیکر کاور نمی‌شود — عکس بفرست یا دکمه‌ی رد کردن را بزن.")
            raise events.StopPropagation
        _mt = (getattr(m.document, "mime_type", "") if m.document else "") or ""
        if not (m.photo or _mt.startswith("image/")):
            await e.reply("🖼 برای کاور فقط **عکس** بفرست — یا دکمه‌ی «⏭ بدون تغییر کاور».")
            raise events.StopPropagation
        msg = st_t.get("msg")
        name = st_t.get("new_name") or st_t.get("old") or "فایل"
        flow.pop(e.chat_id, None)
        asyncio.ensure_future(_tag_run(int(e.chat_id), msg, name, thumb_msg=m))
        raise events.StopPropagation

    if step not in ("file", "name"):
        return

    # ─── مرحله‌ی فایل (اگر در مرحله‌ی اسم فایل جدید فرستاد، جایگزین می‌شود) ───
    if getattr(m, "sticker", None):
        await e.reply("🏳 استیکر را نمی‌شود تگ زد — فیلم، آهنگ یا فایل بفرست.")
        raise events.StopPropagation
    if not (m.document or m.photo):
        await e.reply("این نوع پیام فایل ندارد — فیلم، آهنگ یا فایل بفرست.")
        raise events.StopPropagation
    f = m.file
    _tsize = f.size or 0
    if _tsize > BIG_FILE_MAX:
        await e.reply("❌ تلگرام حتی با اکانت پرمیوم بالاتر از ~۴ گیگابایت را منتقل نمی‌کند.")
        raise events.StopPropagation
    TAG_BIG.pop(e.chat_id, None)
    if _tsize > ACCEPT_MAX:
        if not (globals().get("user_client") and user_client.is_connected()):
            await e.reply("⚠️ برای تگِ فایل بالای ۲ گیگ باید «اکانت‌خوان» وصل باشد (مدیر: `/login`) یا فایل زیر سقف بفرست.")
            raise events.StopPropagation
        if not is_gold(e.sender_id):
            await e.reply("👑 تگ کردن فایل بالای ۲ گیگابایت به «اکانت طلایی» نیاز دارد.\n"
                          "رایگان طلایی شو: سه نفر را با لینک خودت بیاور (دکمهٔ «👤 اطلاعات من») یا به پشتیبانی پیام بده.",
                          buttons=[[Button.inline("📊 وضعیت من / لینک دعوت", b"me:open")]])
            raise events.StopPropagation
        TAG_BIG[e.chat_id] = True
    old_name = f.name or ("عکس.jpg" if m.photo else "فایل")
    st_t.update({"step": "name", "msg": m, "old": old_name,
                 "is_media": _tag_is_media(m), "ts": time.time()})
    replaced = "\n🔄 فایل جدید جایگزین قبلی شد" if step == "name" else ""
    await e.reply(
        f"📄 فایل گرفتیم: «{old_name[:60]}»{replaced}\n"
        f"📦 حجم: {_hsize_dl(f.size or 0)}\n\n"
        f"✏️ حالا **اسم/تگ جدید** را بفرست — *اختیاری*:\n"
        f"مثال: `Serial.E60 @MyChannel.mkv`\n"
        f"اگر رد کنی، همین اسم قبلی می‌ماند.\n\n"
        f"💡 اگر پسوند ننویسی، پسوند قبلی (`{Path(old_name).suffix or '—'}`) نگه داشته می‌شود.",
        buttons=[[Button.inline("⏭ همون اسم قبلی بمونه", b"tag:skipname")],
                 [Button.inline("✖ لغو و خروج", b"tag:cancel")]])
    raise events.StopPropagation


async def _tag_run(chat_id, m, new_name, thumb_msg=None):
    """دانلود فایل، تگ زدن (اسم جدید + تگ‌های نمایشی + کاور جدید اختیاری)
    و ارسال به کاربر + گروه مدیر"""
    from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeAudio
    if TAG_BUSY.get(chat_id):
        try:
            await client.send_message(chat_id, "⏳ یک تگ دیگر در جریان است — کمی صبر کن.")
        except Exception:
            pass
        return
    TAG_BUSY[chat_id] = True
    status = None
    tmpdir = None
    try:
        old_name = ((m.file.name if m.file else None)
                    or ("عکس.jpg" if m.photo else "فایل"))
        tmpdir = Path(f"/tmp/tag_{chat_id % 10000}_{int(time.time())}")
        tmpdir.mkdir(parents=True, exist_ok=True)
        status = await client.send_message(chat_id, f"⬇️ دانلود «{old_name[:40]}»…")
        last = [0.0]

        def _prog(cur, tot):
            now = time.time()
            if now - last[0] < 3 or not tot:
                return
            last[0] = now
            try:
                asyncio.ensure_future(status.edit(
                    f"⬇️ دانلود — {int(cur * 100 / tot)}%"
                    f" ({_hsize_dl(cur)} از {_hsize_dl(tot)})"))
            except Exception:
                pass

        # دانلود در پوشه‌ی موقت (با اسم اصلی) بعد نام‌گذاری با اسم جدید
        if TAG_BIG.pop(chat_id, None) and globals().get("user_client") and user_client.is_connected():
            try:
                await status.edit("⬇️ دریافت فایل بزرگ از مسیر امن…")
            except Exception:
                pass
            try:
                p = await _big_ingest_download(m, {"cur": 0, "total": (m.file.size or 0)})
            except Exception as ex:
                await status.edit(f"❌ دریافت فایل بزرگ ناموفق بود: {str(ex)[:130]}")
                return
        else:
            p = await m.download_media(file=str(tmpdir), progress_callback=_prog)
        if not p or not Path(p).exists() or Path(p).stat().st_size == 0:
            await status.edit("❌ دانلود ناموفق بود.")
            return
        out = tmpdir / new_name
        try:
            Path(p).rename(out)
        except Exception:
            out = Path(p)

        # صفت‌های فایل: اسم جدید + نگه‌داشتن بقیه (مدت، ابعاد و…)
        if m.photo:
            attrs = [DocumentAttributeFilename(new_name)]
        else:
            doc = m.document
            attrs = [a for a in (doc.attributes or [])
                     if not isinstance(a, DocumentAttributeFilename)]
            base = new_name.rsplit(".", 1)[0]
            for i, a in enumerate(attrs):
                if isinstance(a, DocumentAttributeAudio):
                    try:      # آهنگ؟ عنوان داخل پلیر هم تازه شود
                        attrs[i] = DocumentAttributeAudio(
                            duration=a.duration, voice=False, title=base[:64],
                            performer=a.performer, waveform=None)
                    except Exception:
                        pass
            attrs.append(DocumentAttributeFilename(new_name))

        # کاور: اول عکس جدید کاربر (اگر فرستاده)، بعد بندانگشتی اصلی
        thumb = None
        try:
            if thumb_msg is not None:
                tp = await thumb_msg.download_media(file=str(tmpdir / "t.jpg"))
                if tp and Path(tp).exists() and Path(tp).stat().st_size > 0:
                    thumb = tp
        except Exception:
            thumb = None
        if thumb is None:
            try:
                if m.document and m.document.thumbs:
                    tp = await m.download_media(file=str(tmpdir / "t.jpg"), thumb=-1)
                    if tp and Path(tp).exists() and Path(tp).stat().st_size > 0:
                        thumb = tp
            except Exception:
                thumb = None

        try:
            await status.edit(f"⬆️ آپلود «{new_name[:40]}»…")
        except Exception:
            pass
        up_last = [0.0]

        def _up(cur, tot):
            now = time.time()
            if now - up_last[0] < 3 or not tot:
                return
            up_last[0] = now
            try:
                asyncio.ensure_future(status.edit(
                    f"⬆️ آپلود — {int(cur * 100 / tot)}%"
                    f" ({_hsize_dl(cur)} از {_hsize_dl(tot)})"))
            except Exception:
                pass

        await client.send_file(chat_id, str(out), attributes=attrs, thumb=thumb,
                               force_document=True, progress_callback=_up)

        # ─── نسخه‌ی مدیر در گروه مای‌ربات ───
        u_ = users_seen.get(str(chat_id), {})
        uname = u_.get("uname")
        who = (f"@{uname}" if uname else u_.get("name") or str(chat_id))
        info_cap = (f"🏷 {new_name}\n"
                    f"📄 اسم قبلی: {old_name[:60]}\n"
                    f"👤 کاربر: {who}")
        cfg = _tag_target_section()
        sent_copy = False
        if cfg:
            try:
                await client.send_file(int(cfg["chat"]), str(out),
                                       reply_to=cfg.get("topic"), caption=info_cap,
                                       attributes=attrs, thumb=thumb,
                                       force_document=True)
                sent_copy = True
            except Exception:
                sent_copy = False
        if not sent_copy:
            for aid in ADMIN_IDS:
                try:
                    await client.send_file(int(aid), str(out), caption=info_cap,
                                           attributes=attrs, thumb=thumb,
                                           force_document=True)
                except Exception:
                    pass

        await status.edit(
            f"✅ تگ زده شد!\n\n"
            f"🏷 {new_name}\n"
            f"📄 اسم قبلی: {old_name[:60]}")
        try:
            await client.send_message(chat_id, "🔙 اتاق تگ بسته شد — به منوی اصلی برگشتی 👇",
                                      buttons=kb_for(chat_id))
        except Exception:
            pass
    except Exception as ex:
        try:
            if status:
                await status.edit(f"❌ خطا: {str(ex)[:150]}")
            else:
                await client.send_message(chat_id, f"❌ خطا: {str(ex)[:150]}")
        except Exception:
            pass
    finally:
        TAG_BUSY.pop(chat_id, None)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


_TAG_SKIP_NAME = ("رد", "رد کردن", "بدون تغییر", "همون قبلی", "همون اسم", "همین اسم", "نه",
                  "مرحله بعد", "⏭ مرحله بعد", "بعدی", "skip")
_TAG_SKIP_THUMB = ("رد", "رد کردن", "بدون تغییر", "بدون کاور", "همین خوبه", "نه",
                   "نمی‌خوام", "نمیخوام", "مرحله بعد", "⏭ مرحله بعد", "بعدی")
_TAG_CLOSE = ("لغو", "پایان", "/end", "✖ لغو و خروج", "خروج", "بستن")


async def _tag_after_name(chat_id, st, new_name):
    """بعد از مرحله‌ی اسم: اگر ویدیو/آهنگ بود → مرحله‌ی کاور؛ وگرنه همین حالا تگ بزن"""
    if st.get("is_media"):
        st.update({"step": "thumb", "new_name": new_name, "ts": time.time()})
        await client.send_message(
            chat_id,
            f"✅ اسم: «{new_name[:50]}»\n\n"
            f"🖼 این فایل ویدیویی/موسیقی است — اگر می‌خواهی **کاور (تامنیل) جدید** بگذار،"
            f" عکسش را بفرست.\nاختیاری است — با دکمه‌ی زیر رد می‌کنی:",
            buttons=[[Button.inline("⏭ بدون تغییر کاور", b"tag:skipthumb")],
                     [Button.inline("✖ لغو و خروج", b"tag:cancel")]])
    else:
        m = st.get("msg")
        flow.pop(chat_id, None)
        asyncio.ensure_future(_tag_run(chat_id, m, new_name))


async def handle_tag_text(e):
    st = flow.get(e.chat_id)
    t = (e.text or "").strip()
    if t == BTN_TAG:
        return          # دکمه‌ی شروع بخش — خوش‌آمد خودش قبلاً ارسال شده؛ بدون واکنش دوباره
    if t in _TAG_CLOSE:
        flow.pop(e.chat_id, None)
        await e.reply("🏷 بخش تگ فایل بسته شد.", buttons=kb_for(e.chat_id))
        return
    if t in (BTN_HELP, BTN_STATUS, BTN_ADMIN, BTN_BARGH, BTN_SUPPORT, BTN_SELFDES,
             BTN_AI, BTN_YT, BTN_FINDER, BTN_SUB, BTN_INSTA, BTN_ANTEN, BTN_STT,
             BTN_LINK, BTN_DL, BTN_TAG):
        await e.reply("🏷 داخل بخش تگ فایلی.\n"
                      "فایل بفرست؛ اسم و کاور اختیاری‌اند — یا «لغو» برای خروج.")
        return
    step = st.get("step")
    if step == "name":
        if t in _TAG_SKIP_NAME:
            await _tag_after_name(e.chat_id, st, st.get("old") or "فایل")
            return
        new_name = _tag_sanitize_name(t)
        if not new_name:
            await e.reply("اسم معتبر نیست — دوباره بفرست (یا «لغو»).")
            return
        new_name = _tag_with_ext(new_name, st.get("old") or "")
        await _tag_after_name(e.chat_id, st, new_name)
        return
    if step == "thumb":
        if t in _TAG_SKIP_THUMB:
            m = st.get("msg")
            name = st.get("new_name") or st.get("old") or "فایل"
            flow.pop(e.chat_id, None)
            asyncio.ensure_future(_tag_run(int(e.chat_id), m, name))
            return
        await e.reply("🖼 برای کاور **عکس** بفرست — یا دکمه‌ی «⏭ بدون تغییر کاور» را بزن.")
        return
    await e.reply("📄 اول فایل را بفرست — اسم و کاور بعدش (اختیاری).")


async def handle_sd_login_text(e):
    st = flow.get(e.chat_id)
    t = (e.text or "").strip()
    if t == "لغو":
        flow.pop(e.chat_id, None)
        await e.reply("لغو شد ✅", buttons=kb_for(e.chat_id))
        return
    if st["step"] == "phone":
        if not re.match(r"^\+?\d{10,15}$", t):
            await e.reply("شماره معتبر نیست؛ با +98 شروع کن (یا بنویس: لغو)")
            return
        phone = t if t.startswith("+") else "+" + t
        try:
            uc = TelegramClient(StringSession(), API_ID, API_HASH)
            await uc.connect()
            await uc.send_code_request(phone)
        except Exception as ex:
            flow.pop(e.chat_id, None)
            await e.reply(f"❌ ارسال کد ناموفق: {str(ex)[:120]}")
            return
        st.update({"step": "code", "phone": phone, "client": uc, "ts": time.time()})
        await e.reply("📲 کد رو با کیبورد زیر بزن:", buttons=codepad())
    elif st["step"] == "code":
        uc = st.get("client")
        act, code = _code_input(st, t)
        if act == "progress":
            await e.reply(f"🔢 تا الان: {'●' * len(code)}\n(بعدش «✅ ثبت کد»)",
                          buttons=codepad())
            return
        if act != "submit" or not code:
            await e.reply("با کیبورد عددی بزن!", buttons=codepad())
            return
        st["ts"] = time.time()
        try:
            await uc.sign_in(st["phone"], code)
        except SessionPasswordNeededError:
            st.update({"step": "pass", "ts": time.time()})
            await e.reply("🔒 رمز دومرحله‌ای فعاله؛ پسوردت رو بفرست:")
            return
        except Exception as ex:
            await e.reply(f"❌ کد درست نبود؛ دوباره بزن. ({str(ex)[:60]})",
                          buttons=codepad())
            return
        await _sd_finish(e, uc, st)
    elif st["step"] == "pass":
        uc = st.get("client")
        try:
            await uc.sign_in(password=t)
        except Exception:
            await e.reply("❌ پسورد درست نبود؛ دوباره بفرست.")
            return
        await _sd_finish(e, uc, st)


async def _sd_finish(e, uc, st):
    flow.pop(e.chat_id, None)
    store = _load_sd_store()
    store[str(e.chat_id)] = uc.session.save()
    SD_FILE.write_text(json.dumps(store, ensure_ascii=False))
    persist.mark_dirty()
    await _start_sd_client(e.chat_id, uc)
    await e.reply(
        "✅ وصل شد! از الان عکس/ویدیوهای خودموی پیوی‌ت خودکار ذخیره می‌شن 📸\n"
        "قطع: /sdoff",
        buttons=kb_for(e.chat_id))


def _is_admin_chat(chat_id) -> bool:
    return int(chat_id) in ADMIN_IDS


@client.on(events.NewMessage(pattern=r"^/addsource\s+@?([A-Za-z0-9_]{3,64})$"))
async def cmd_addsource(e):
    """مدیر: افزودن کانال منبع پروکسی/کانفیگ"""
    if e.sender_id not in ADMIN_IDS:
        return
    ch = e.pattern_match.group(1)
    if ch in EXTRA_SOURCES or ch in PROXY_CHANNELS:
        await e.reply(f"⚠️ @{ch} از قبل توی لیسته.")
        return
    EXTRA_SOURCES.append(ch)
    save_sources()
    joined, got = "", (0, 0)
    if user_client and user_client.is_connected():
        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            await user_client(JoinChannelRequest(ch))
            joined = "\n✅ اکانت‌خوان عضوش شد"
        except Exception as ex:
            joined = f"\n⚠️ عضویت: {str(ex)[:50]}"
        try:
            got = await _backfill_source(ch, limit=40)   # فوراً تاریخچه‌اش را بریز
        except Exception:
            pass
    await e.reply(
        f"✅ @{ch} اضافه شد.{joined}\n"
        f"📥 همین الان واکشی شد: +{got[0]} پروکسی، +{got[1]} کانفیگ\n"
        f"(اسکن کامل هر ۲ ساعت خودکار انجام می‌شود)")


@client.on(events.NewMessage(pattern=r"^/delsource\s+@?([A-Za-z0-9_]{3,64})$"))
async def cmd_delsource(e):
    if e.sender_id not in ADMIN_IDS:
        return
    ch = e.pattern_match.group(1)
    if ch in EXTRA_SOURCES:
        EXTRA_SOURCES.remove(ch)
        save_sources()
        await e.reply(f"🗑 @{ch} حذف شد.")
    elif ch in PROXY_CHANNELS:
        await e.reply(f"⚠️ @{ch} از منابع پیش‌فرض است و حذف نمی‌شود.")
    else:
        await e.reply("توی لیست نیست. لیست: /sources")


@client.on(events.NewMessage(pattern=r"^/sources$"))
async def cmd_sources(e):
    if e.sender_id not in ADMIN_IDS:
        return
    lines = ["📡 کانال‌های منبع پروکسی/کانفیگ:", ""]
    lines += [f"• @{c} (پیش‌فرض)" for c in PROXY_CHANNELS]
    lines += [f"• @{c}" for c in EXTRA_SOURCES]
    lines.append("")
    lines.append("افزودن: /addsource @channel | حذف: /delsource @channel")
    await e.reply("\n".join(lines))


@client.on(events.NewMessage(pattern=r"^/scan$"))
async def cmd_scan(e):
    """مدیر: واکشی عمیق دستی همه‌ی منابع"""
    if e.sender_id not in ADMIN_IDS:
        return
    st = await e.reply("⏳ دارم همه‌ی کانال‌های منبع را می‌گردم…")
    try:
        px, cfg = await _backfill_all_sources()
        await st.edit(f"✅ واکشی کامل شد:\n📡 +{px} پروکسی\n🛰 +{cfg} کانفیگ\n"
                      f"موجودی فعلی: {len(anten.custom['mtproto'])} پروکسی | "
                      f"{len(anten.custom['v2'])} کانفیگ")
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/subon$"))
async def cmd_subon(e):
    """ادمین: قفل کلی — فقط اعضای مجاز استفاده کنند"""
    if e.sender_id not in ADMIN_IDS:
        return
    SUBS["lock_all"] = True
    save_subs()
    await e.reply("🔒 قفل فعال شد — فقط اعضای مجاز می‌توانند استفاده کنند.\n"
                  "باز کردن: /suboff | مجوز: /grant آیدی روز | کلید: /genkey روز")


@client.on(events.NewMessage(pattern=r"^/suboff$"))
async def cmd_suboff(e):
    if e.sender_id not in ADMIN_IDS:
        return
    SUBS["lock_all"] = False
    save_subs()
    await e.reply("🔓 قفل برداشته شد — استفاده عمومی است.")


@client.on(events.NewMessage(pattern=r"^/grant\s+(\d+)(?:\s+(\d+))?$"))
async def cmd_grant(e):
    """مجوز دسترسی مستقیم: /grant آیدی [روزها]"""
    if e.sender_id not in ADMIN_IDS:
        return
    uid = e.pattern_match.group(1)
    days = int(e.pattern_match.group(2) or 30)
    SUBS["allowed"][uid] = time.time() + days * 86400
    save_subs()
    try:
        await client.send_message(int(uid),
                                  f"✅ دسترسی شما برای {days} روز فعال شد 🎉")
    except Exception:
        pass
    await e.reply(f"✅ کاربر {uid} برای {days} روز مجاز شد.")


@client.on(events.NewMessage(pattern=r"^/revoke\s+(\d+)$"))
async def cmd_revoke(e):
    """قطع دسترسی: /revoke آیدی"""
    if e.sender_id not in ADMIN_IDS:
        return
    uid = e.pattern_match.group(1)
    SUBS["allowed"].pop(uid, None)
    save_subs()
    await e.reply(f"🚫 دسترسی {uid} قطع شد.")


@client.on(events.NewMessage(pattern=r"^/genkey(?:\s+(\d+))?$"))
async def cmd_genkey(e):
    """ساخت کلید اشتراک: /genkey [روزها]"""
    if e.sender_id not in ADMIN_IDS:
        return
    days = int(e.pattern_match.group(1) or 30)
    key = "STAR-" + secrets.token_hex(3).upper()
    SUBS["keys"][key] = {"days": days, "used_by": None}
    save_subs()
    await e.reply(f"🔑 کلید ساخته شد ({days} روز):\n`{key}`\n"
                  f"به کاربر بده — با /redeem {key} فعال می‌کند.")


@client.on(events.NewMessage(pattern=r"^/redeem\s+([A-Za-z0-9\-]+)$"))
async def cmd_redeem(e):
    """کاربر: فعال‌سازی کلید"""
    key = e.pattern_match.group(1).strip().upper()
    info = SUBS["keys"].get(key)
    if not info:
        await e.reply("❌ کلید نامعتبر است.")
        return
    if info.get("used_by") and info["used_by"] != str(e.sender_id):
        await e.reply("❌ این کلید قبلاً استفاده شده.")
        return
    days = info["days"]
    now = time.time()
    cur = SUBS["allowed"].get(str(e.sender_id), 0)
    base = max(now, cur)
    SUBS["allowed"][str(e.sender_id)] = base + days * 86400
    info["used_by"] = str(e.sender_id)
    save_subs()
    until = datetime.fromtimestamp(SUBS["allowed"][str(e.sender_id)])
    await e.reply(f"🎉 اشتراک فعال شد تا {until.strftime('%Y-%m-%d')}!")


@client.on(events.NewMessage(pattern=r"^/mysub$"))
async def cmd_mysub(e):
    exp = SUBS["allowed"].get(str(e.sender_id), 0)
    if time.time() < exp:
        await e.reply(f"💎 اشتراک شما تا {datetime.fromtimestamp(exp).strftime('%Y-%m-%d')} فعال است.")
    else:
        await e.reply("اشتراک فعالی نداری. 🔑 /redeem کد")


def _norm_phone(t: str) -> str:
    """هر فرمتی → +989xxxxxxxxx"""
    d = re.sub(r"\D", "", (t or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    if d.startswith("0098"):
        d = "98" + d[4:]
    if d.startswith("98") and len(d) == 12:
        return "+" + d
    if d.startswith("0") and len(d) == 11:
        return "+98" + d[1:]
    if len(d) == 10:
        return "+98" + d
    return ""


@client.on(events.NewMessage(pattern=r"^🔎 مزاحم‌یاب$"))
async def btn_finder(e):
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "finder"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    if not user_client or not user_client.is_connected():
        await e.reply("⚠️ سرویس جستجو فعلاً در دسترس نیست — کمی بعد امتحان کن.")
        return
    flow[e.chat_id] = {"kind": "finder", "step": "phone", "ts": time.time()}
    await e.reply("🔎 شماره‌ی مزاحم رو بفرست (مثل ۰۹۱۲۳۴۵۶۷۸۹) تا اسمش رو دربیارم.\nانصراف: «لغو»")


FINDER_BOT = "Havij_robot"   # ربات مزاحم‌یاب (فرمت 09xxxxxxxxx)
finder_pending = {}   # chat_id کاربر → شماره (در انتظار پاسخ)


FINDER_WAIT_MSGS = ("در حال جستجو", "صبر کنید", "کمی صبر", "searching", "loading", "⏳")


def _finder_pick(msgs):
    """از پیام‌های ربات هویج، پیامِ اسم را انتخاب می‌کند (نه «در حال جستجو»، نه تبلیغ)"""
    for t in msgs:
        low = t.lower()
        if ("👤" in t or "name:" in low or "name :" in low) and "numberbox" not in low:
            return t
    for t in msgs:
        low = t.lower()
        if any(w in low for w in FINDER_WAIT_MSGS):
            continue
        if "numberbox" in low or "دانلود مستقیم" in low or "پیشنهاد می" in t:
            continue   # تبلیغ
        if len(t.strip()) > 2:
            return t
    return None


_finder_handler_on = False
FINDER_BOT_ID = None   # آیدی عددی ربات هویج (دقیق‌تر از یوزرنیم)


async def _resolve_finder_bot():
    """آیدی عددی ربات هویج را یک بار حل می‌کنیم"""
    global FINDER_BOT_ID
    if FINDER_BOT_ID:
        return
    try:
        ent = await user_client.get_entity(FINDER_BOT)
        FINDER_BOT_ID = ent.id
        log.info("finder: ربات هویج با آیدی %s شناسایی شد", FINDER_BOT_ID)
    except Exception as e:
        log.warning("finder: شناسایی هویج ناموفق: %s", str(e)[:60])


def _setup_finder_handler():
    """پاسخ‌های ربات هویج → صف درخواستِ فعال (فقط یک بار ثبت می‌شود)"""
    global _finder_handler_on
    if _finder_handler_on:
        return
    _finder_handler_on = True
    try:
        asyncio.get_running_loop().create_task(_resolve_finder_bot())
    except RuntimeError:
        pass   # بدون حلقه‌ی فعال — در اولین فراخوانی بعدی حل می‌شود

    async def _on_bot_reply(e):
        try:
            if not e.is_private:
                return
            un = (getattr(e.sender, "username", None) or "").lower() if e.sender else ""
            sid = e.sender_id
            # تطبیق با آیدی (دقیق) یا یوزرنیم (به‌صورت مقاوم به حروف)
            by_id = FINDER_BOT_ID and sid == FINDER_BOT_ID
            by_un = un == FINDER_BOT.lower()
            if not (by_id or by_un):
                return
            txt = (e.message.message or "").strip()
            if not txt:
                return
            # به آخرین درخواستِ در انتظارِ هر کاربر اضافه کن (همه‌ی صف‌ها؛
            # هر کاربر فقط یک درخواست همزمان دارد)
            for cid, st_ in list(finder_pending.items()):
                if not st_.get("done"):
                    st_["msgs"].append(txt)
        except Exception:
            log.exception("finder reply")
    user_client.add_event_handler(
        _on_bot_reply,
        events.NewMessage(incoming=True, func=lambda e: e.is_private))


async def handle_finder_text(e):
    # فقط خود کاربر — پیام ربات‌ها هرگز شماره حساب نمی‌شود
    try:
        if e.sender_id != e.chat_id:
            return
    except Exception:
        pass
    st = flow.get(e.chat_id) or {}
    t = (e.text or "").strip()
    if t == BTN_FINDER or t.startswith("/"):
        await e.reply("شماره رو بفرست (مثل ۰۹۱۲۳۴۵۶۷۸۹) — یا «لغو»")
        return
    if t in ("لغو", "/end", "پایان"):
        flow.pop(e.chat_id, None)
        await e.reply("لغو شد ✅", buttons=kb_for(e.chat_id))
        return
    ph = _norm_phone(t)
    if not ph:
        await e.reply("فرمت درست نیست — مثال: ۰۹۱۲۳۴۵۶۷۸۹ (یا «لغو»)")
        return
    flow.pop(e.chat_id, None)
    status = await e.reply(f"🔍 دارم {ph[:6]}*** رو می‌گردم… (تا ۶۰ ثانیه)")
    try:
        # همیشه از ربات هویج بپرس (نتایجش کامل‌تر است — نام خانوادگی و ...)
        # اگر مستقیم هم پیدا شد، بعد از هویج به‌عنوان مکمل می‌آید
        # ارسال شماره به ربات هویج (فرمت 09...) از طریق اکانت‌خوان
        domestic = ("0" + ph[3:]) if ph.startswith("+98") else ph
        pend = {"phone": ph, "msgs": []}
        finder_pending[e.chat_id] = pend
        try:
            await _resolve_finder_bot()
            await user_client.send_message(FINDER_BOT, domestic)
        except Exception as ex:
            finder_pending.pop(e.chat_id, None)
            await status.edit(f"❌ سرویس جستجو در دسترس نیست: {str(ex)[:80]}")
            return
        # صبر برای پیامِ «Name:» تا ۶۰ ثانیه — با پُل مستقیم (مقاوم به گم‌شدن هندلر)
        answer = None
        pull_every = 5   # هر ۵ ثانیه مستقیم از چت هویج هم بخوان
        ticks = 0
        while ticks < 60:
            await asyncio.sleep(1)
            ticks += 1
            answer = _finder_pick(pend["msgs"])
            if not answer and ticks % pull_every == 0:
                # پُل مستقیم: آخرین پیام‌های چت هویج را بخوان
                try:
                    msgs = await user_client.get_messages(FINDER_BOT, limit=4)
                    for m_ in msgs:
                        txt_ = (m_.message or "").strip()
                        if txt_ and txt_ not in pend["msgs"]:
                            pend["msgs"].append(txt_)
                    answer = _finder_pick(pend["msgs"])
                except Exception:
                    pass
            if answer:
                pend["done"] = True
                break
        finder_pending.pop(e.chat_id, None)
        if answer:
            await status.edit(
                f"🔎 نتیجه برای {ph[:6]}****{ph[-2:]}:\n\n{answer[:1500]}")
        else:
            await status.edit("⏳ ربات جستجو جواب نداد — دوباره امتحان کن.")
    except Exception as ex:
        finder_pending.pop(e.chat_id, None)
        await status.edit(f"❌ نتونستم دربیارم: {str(ex)[:100]}")


@client.on(events.NewMessage(pattern=r"^💎 اشتراک$"))
async def btn_sub(e):
    await note_user(e)
    exp = SUBS["allowed"].get(str(e.sender_id), 0)
    if time.time() < exp:
        await e.reply(f"💎 اشتراکت تا {datetime.fromtimestamp(exp).strftime('%Y-%m-%d')} فعال است ✅")
    elif SUBS.get("lock_all"):
        flow[e.chat_id] = {"kind": "sub", "step": "code", "ts": time.time()}
        await e.reply("💎 اشتراک نداری.\n\n🔑 کلیدت رو بفرست تا فعال بشه:",
                      buttons=[[Button.inline("✖ انصراف", b"sub:cancel")]])
    else:
        await e.reply("🔓 فعلاً استفاده از ربات برای همه آزاده — اشتراک لازم نیست 🙂")


@client.on(events.CallbackQuery(pattern=r"^sub:"))
async def on_sub_cb(e):
    if e.data == b"sub:start":
        await e.answer()
        flow[e.chat_id] = {"kind": "sub", "step": "code", "ts": time.time()}
        try:
            await e.edit("🔑 کلیدت رو بفرست (مثل STAR-1A2B3C):",
                         buttons=[[Button.inline("✖ انصراف", b"sub:cancel")]])
        except Exception:
            pass
        return
    if e.data == b"sub:cancel":
        flow.pop(e.chat_id, None)
        try:
            await e.answer()
            await e.edit("لغو شد ✅", buttons=None)
        except Exception:
            pass
        return
    await e.answer()


@client.on(events.NewMessage(pattern=r"^🤖 هوش مصنوعی$"))
async def btn_ai(e):
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    if not AI_OK:
        await e.reply("⚠️ ماژول هوش مصنوعی آماده نیست.")
        return
    flow[e.chat_id] = {"kind": "ai", "step": "chat", "ts": time.time(),
                       "history": []}
    await e.reply(
        "🤖 چت با هوش مصنوعی شروع شد!\n\n"
        "هرچی بخوای بپرس: توضیح، متن‌نویسی، ترجمه، کد، ایده…\n"
        "پایان: «پایان» یا /end\n\n"
        "💡 دستیار اکانت‌خوان: با /m شروع کن\n"
        "مثلا: /m اسم پروفایلمو بکن ماهی",
        buttons=[[Button.inline("✖ پایان گفتگو", b"ai:end")]])




# ═══════════════════════ 🧠 دستیار هوشمند اکانت (/m) + گروه AI ═══════════════════════

MAGENT_HELP = (
    "🧠 دستیار همه‌کاره‌ی اکانتت — به سبک یوزربات\n\n"
    "⚡ از هر چتی به خودت بنویس /m + حرفت — یا دستور مستقیم:\n\n"
    "👤 پروفایل و پیام:\n"
    "• /m اسم پروفایلمو بکن ماهی | بیو | یوزرنیم | عکس\n"
    "• /m به امین بگو کی بریم پارک | ببین چی گفته | 👍 بده\n"
    "• /m به آخرین پیوی سلام کن\n\n"
    "🧰 ابزارها (دستور مستقیم هم دارند):\n"
    "• /tr متن — ترجمه (فارسی↔انگلیسی خودکار)\n"
    "• /tts متن — ویس فارسی طبیعی 🎙\n"
    "• /azan — اوقات شرعی | /weather — آب‌وهوا\n"
    "• /lyrics خواننده - آهنگ | /speed — سرعت اینترنت\n"
    "• روی عکس ریپلای: /ocr (متن) | /rmbg (حذف پس‌زمینه) | /meme بالا | پایین\n\n"
    "🧹 مدیریت:\n"
    "• /purge 10 — پاک کردن ۱۰ پیام آخر همین چت\n"
    "• /whois — اطلاعات کسی (روی پیامش ریپلای کن)\n"
    "• /stats — آمار چت/گروه\n"
    "• /afk دلیل — حالت خارج از دسترس\n"
    "• /filter کلمه=جواب — پاسخ خودکار پیوی\n"
    "• /welcome — خوش‌آمدگویی خودکار گروه\n\n"
    "⏰ زمان‌بندی | 👩‍💼 منشی | ⏳ شمارش معکوس | 🌐 سرچ اینترنت\n"
    "🧰 بیشتر: /dict /calc /paste /gps /id /note /dice /sd /poto /mediainfo /thumb /carbon /hash\n"
    "🎨 سوم: /time /wall /img /logo /quote /fry /glitch /collage /poll /create /rename\n"
    "🛡 گروه: /invite /ban /unban /mute /unmute /pin /promote /demote /archive /md /mention /broadcast\n"
    "🎮 سرگرمی: /fun /coin /roll /8ball /joke /fact /love /hack /spam /stickify /kang /warn /lock /button\n"
    "🛠 پرو: /shot سایت /repo یوزر/ریپو /upload /transfer @کانال /py کد /figlet /setkey /reddit /np /nsfw\n"
    "🎭 نمایشی: /action typing|game|sticker... (تا /action off) | /clock — ساعت زنده روی اسم | /animate typewriter متن\n"
    "🎵 اسپاتیفای: /spotify لینک-یا-اسم | پلی‌لیست دکمه‌ای | 🔑 /setkey proxy پروکسی = یوتیوب همیشه باز\n"
    "🔥 خودموف: روی عکس/ویدیو ریپلای → /sdto اسم [ثانیه] — یک‌بارمصرف براش می‌رود\n"
    "🎙 ویس بفرست → متن فارسی‌اش خودکار می‌آید (Whisper) | /stt روی ویس ریپلای\n"
    "🔗 /link روی فایل ریپلای → لینک دائمی/موقت اینترنت — با هرکی بخواهی به اشتراک بگذار\n"
    "🟨 کد: /js کد-جاوااسکریپت (یا فایل .js ریپلای کن) | /py کد-پایتون | 🎨 /carbon کد→عکس\n"
    "🖥 ویندوزی: /cmd دستور-بچ (یا فایل .cmd/.bat ریپلای کن) — echo/set/if/for/goto/dir...\n"
    "🧠 حافظه دارد: «همون/ادمک/همون دی» را می‌فهمد\n"
    "👥 گروه گفتگوی اختصاصی: /aigroup\n\n"
    "👁 نیاز به اکانت‌خوان وصل دارد (/login)")

BOT_SELF_ID = 0        # آیدی خود ربات — /m داخل چت ربات از مسیر اصلی می‌رود

# ─── حافظه‌ی دستیار: هر چت → زمینه گفتگو ───
AUTO_REPLY = {}            # "peer_id" → {"name":..., "ts":...} — منشی هوشمند
AUTO_REPLY_FILE = Path("/tmp/autoreply.json")

# ─── وضعیت ابزارهای یوزربات (به سبک CatUserBot) ───
AFK = {"on": False, "reason": "", "since": 0.0}     # حالت خارج از دسترس
AFK_FILE = Path("/tmp/afk.json")
AFK_REPLIED = deque(maxlen=300)                     # پیام‌هایی که جواب AFK گرفتند
FILTERS = {}                                        # کلمه → پاسخ خودکار (پیوی)
FILTERS_FILE = Path("/tmp/filters.json")
_FILTER_SEEN = deque(maxlen=600)
WELCOME_GROUPS = {}                                 # chat_id → عنوان گروه
WELCOME_FILE = Path("/tmp/welcome.json")
_WELCOME_SEEN = deque(maxlen=400)


def _load_afk():
    try:
        d = json.loads(AFK_FILE.read_text())
        AFK.update({k: v for k, v in d.items() if k in ("on", "reason", "since")})
    except Exception:
        pass


def _save_afk():
    try:
        AFK_FILE.write_text(json.dumps(AFK, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


def _load_filters():
    try:
        d = json.loads(FILTERS_FILE.read_text())
        FILTERS.update({k: v for k, v in d.items() if isinstance(v, str)})
    except Exception:
        pass


def _save_filters():
    try:
        FILTERS_FILE.write_text(json.dumps(FILTERS, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


BROADCAST_PENDING = {}     # chat_id → {"text":..., "ts":...} — منتظر تأیید اره/نه

# ─── اکشن‌سپوفر (در حال نوشتن/بازی/...) و ساعت زنده ───
ACTIONS = {}          # chat_id → {"type": str, "task": asyncio.Task}
ACTION_TYPES = {
    "typing": "⌨️ در حال نوشتن",
    "game": "🎮 در حال بازی",
    "photo": "🖼 در حال ارسال عکس",
    "record_video": "🎥 در حال ضبط ویدیو",
    "upload_video": "🎬 در حال ارسال ویدیو",
    "record_audio": "🎤 در حال ضبط صدا",
    "upload_audio": "🎵 در حال ارسال صدا",
    "document": "📎 در حال ارسال فایل",
    "sticker": "😊 در حال انتخاب استیکر",
    "location": "📍 در حال ارسال مکان",
    "contact": "👤 در حال انتخاب مخاطب",
}
CLOCK = {"on": False, "task": None, "orig_name": "", "interval": 60}


def _action_obj(t):
    from telethon.tl.types import (SendMessageTypingAction, SendMessageGamePlayAction,
                                   SendMessageUploadPhotoAction, SendMessageRecordVideoAction,
                                   SendMessageUploadVideoAction, SendMessageRecordAudioAction,
                                   SendMessageUploadAudioAction, SendMessageUploadDocumentAction,
                                   SendMessageChooseStickerAction, SendMessageGeoLocationAction,
                                   SendMessageChooseContactAction)
    m = {"typing": SendMessageTypingAction,
         "game": SendMessageGamePlayAction,
         "photo": SendMessageUploadPhotoAction,
         "record_video": SendMessageRecordVideoAction,
         "upload_video": SendMessageUploadVideoAction,
         "record_audio": SendMessageRecordAudioAction,
         "upload_audio": SendMessageUploadAudioAction,
         "document": SendMessageUploadDocumentAction,
         "sticker": SendMessageChooseStickerAction,
         "location": SendMessageGeoLocationAction,
         "contact": SendMessageChooseContactAction}
    return m.get(t, SendMessageTypingAction)()


async def _action_loop(chat_id, t):
    """حالت در حال نوشتن/بازی را هر ۵ ثانیه تازه نگه می‌دارد تا خاموش شود"""
    from telethon.tl.functions.messages import SetTypingRequest
    while True:
        try:
            await user_client(SetTypingRequest(chat_id, _action_obj(t)))
        except Exception:
            pass
        await asyncio.sleep(5)


def _stop_action(chat_id):
    a = ACTIONS.pop(chat_id, None)
    if a and a.get("task"):
        try:
            a["task"].cancel()
        except Exception:
            pass
    return a


async def _clock_loop():
    """ساعت زنده روی اسم پروفایل — هر دقیقه به‌روز"""
    from telethon.tl.functions.account import UpdateProfileRequest
    while CLOCK.get("on"):
        try:
            me = await user_client.get_me()
            if not CLOCK.get("orig_name"):
                CLOCK["orig_name"] = me.first_name or ""
            t_ = __import__("datetime").datetime.now(
                __import__("zoneinfo").ZoneInfo("Asia/Tehran")).strftime("%H:%M")
            await user_client(UpdateProfileRequest(first_name=f"🕐 {t_} | {CLOCK['orig_name']}"[:64]))
        except Exception:
            pass
        await asyncio.sleep(CLOCK.get("interval", 60))
    # برگرداندن اسم اصلی
    try:
        from telethon.tl.functions.account import UpdateProfileRequest
        if CLOCK.get("orig_name"):
            await user_client(UpdateProfileRequest(first_name=CLOCK["orig_name"]))
    except Exception:
        pass


USERKEYS_FILE = Path("/tmp/userkeys.json")


def _load_userkeys():
    try:
        d = json.loads(USERKEYS_FILE.read_text())
        for k, v in d.items():
            os.environ.setdefault(k, v)
        return d
    except Exception:
        return {}


def _set_userkey(name, value):
    d = {}
    try:
        d = json.loads(USERKEYS_FILE.read_text())
    except Exception:
        pass
    d[name] = value
    try:
        USERKEYS_FILE.write_text(json.dumps(d, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


def _del_userkey(name):
    d = {}
    try:
        d = json.loads(USERKEYS_FILE.read_text())
    except Exception:
        pass
    if d.pop(name, None) is not None:
        USERKEYS_FILE.write_text(json.dumps(d, ensure_ascii=False))
        persist.mark_dirty()
        return True
    return False


KEYS_HELP = (
    "🔑 کلیدهای شخصی (رایگان):\n\n"
    "• /setkey reddit آیدی:رمز ← از reddit.com/prefs/apps (اسکریپت بساز)\n"
    "• /setkey lastfm کلید ← از last.fm/api/account/create\n"
    "• /setkey deepai کلید ← از deepai.org/dashboard/profile (منوی راست پایین API Keys)\n"
    "• /setkey removebg کلید ← remove.bg/api-key\n"
    "• /setkey spotify آیدی:رمز ← developer.spotify.com/dashboard (Create app)\n"
    "• /setkey proxy http://host:port ← پروکسی شخصی = یوتیوب همیشه باز! (socks5 هم می‌شود)\n\n"
    "/keys ← لیست کلیدهای فعال\n"
    "/delkey نام ← حذف\n\n"
    "کلیدها در مخزن خصوصی bot-data ذخیره می‌شوند — فقط خودت می‌بینی.")


WARNS = {}                 # "chat:user" → تعداد اخطار
WARNS_FILE = Path("/tmp/warns.json")


def _load_warns():
    try:
        d = json.loads(WARNS_FILE.read_text())
        WARNS.update({k: v for k, v in d.items() if isinstance(v, int)})
    except Exception:
        pass


def _save_warns():
    try:
        WARNS_FILE.write_text(json.dumps(WARNS, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


NOTES = {"items": []}                       # یادداشت‌های شخصی
NOTES_FILE = Path("/tmp/notes.json")


def _load_notes():
    try:
        d = json.loads(NOTES_FILE.read_text())
        if isinstance(d.get("items"), list):
            NOTES["items"] = d["items"][-50:]
    except Exception:
        pass


def _save_notes():
    try:
        NOTES_FILE.write_text(json.dumps(NOTES, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


def _load_welcome():
    try:
        d = json.loads(WELCOME_FILE.read_text())
        WELCOME_GROUPS.update({k: v for k, v in d.items() if isinstance(v, str)})
    except Exception:
        pass


def _save_welcome():
    try:
        WELCOME_FILE.write_text(json.dumps(WELCOME_GROUPS, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()
_IN_SEEN = deque(maxlen=600)   # آیدی پیام‌های ورودی پردازش‌شده (منشی)
_SCHED_TASKS = []             # کارهای زمان‌بندی‌شده فعال


def _load_autoreply():
    try:
        d = json.loads(AUTO_REPLY_FILE.read_text())
        AUTO_REPLY.update({k: v for k, v in d.items() if isinstance(v, dict)})
    except Exception:
        pass


def _save_autoreply():
    try:
        AUTO_REPLY_FILE.write_text(json.dumps(AUTO_REPLY, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


MAGENT_CTX = {}                                   # chat_id → dict
MAGENT_CTX_FILE = Path("/tmp/magent_ctx.json")
SAME_REF = ("همون دی", "همون ادمک", "همون قبلی", "همون آدم", "ادمک", "همونی",
            "همونش", "همون", "اون یکی", "قبلی",
            "adamak", "adamk", "hamoon", "hamun", "hamoon di", "oon yeki", "ghabli")
# «آخرین پیوی» = جدیدترین گفتگوی خصوصی (iter_dialogs جدیدترین اول است)
LAST_REF = ("آخرین پیوی", "اخرین پیوی", "آخرین چت", "اخرین چت",
            "آخرین گفتگو", "اخرین گفتگو", "آخرین پی وی", "اخرین پی وی",
            "last chat", "last dm")

# حروف‌چینی فارسی → لاتین تقریبی — برای تطبیق «ادمک» با «adamak»
_LATIN_MAP = str.maketrans({
    "ا": "a", "آ": "a", "ب": "b", "پ": "p", "ت": "t", "ث": "s", "ج": "j", "چ": "ch",
    "ح": "h", "خ": "kh", "د": "d", "ذ": "z", "ر": "r", "ز": "z", "ژ": "j", "س": "s",
    "ش": "sh", "ص": "s", "ض": "z", "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f",
    "ق": "gh", "ک": "k", "ك": "k", "گ": "g", "ل": "l", "م": "m", "ن": "n", "و": "v",
    "ه": "h", "ی": "i", "ي": "i", "ئ": "i", "ء": "",
})


def _to_latin(s):
    return _fa_norm(s).translate(_LATIN_MAP)
YES_WORDS = ("اره", "آره", "بله", "اره.", "آره.", "بله.", "چرا", "yes", "y", "اره ✓", "ارن", "ارهه")
NO_WORDS = ("نه", "نه.", "نخیر", "نخیر.", "نه بابا", "no", "n", "نهه")


def _mag_load_ctx():
    try:
        d = json.loads(MAGENT_CTX_FILE.read_text())
        MAGENT_CTX.update({k: v for k, v in d.items() if isinstance(v, dict)})
    except Exception:
        pass


def _mag_save_ctx():
    try:
        MAGENT_CTX_FILE.write_text(json.dumps(MAGENT_CTX, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


def _ctx(chat_id) -> dict:
    c = MAGENT_CTX.get(str(chat_id))
    if not isinstance(c, dict):
        c = {"history": [], "summary": "", "last_target": None,
             "confirm": None, "last_choices": None, "last_act": None,
             "last_req": "", "ts": time.time()}
        MAGENT_CTX[str(chat_id)] = c
    return c


def _fa_norm(s):
    return ((s or "").replace("ي", "ی").replace("ك", "ک")
            .replace("\u200c", " ").replace("آ", "ا").strip().lower())


def _fa_digits(s):
    return str(s).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))


# ─── گفتگوها: افراد + گروه‌ها + کانال‌ها (کش ۲ دقیقه‌ای) ───
_DIALOG_CACHE = {"ts": 0.0, "items": []}
_PROFILE_CACHE = {"ts": 0.0, "txt": ""}


async def _magent_dialogs():
    now = time.time()
    if _DIALOG_CACHE["items"] and now - _DIALOG_CACHE["ts"] < 120:
        return _DIALOG_CACHE["items"]
    out = []
    me_id = 0
    try:
        me = await user_client.get_me()
        me_id = me.id
    except Exception:
        pass
    async for d in user_client.iter_dialogs(limit=400):
        try:
            name = (d.name or "").strip()
            if not name or d.entity is None:
                continue
            did = getattr(d.entity, "id", None)
            if not did or did == me_id:
                continue
            t = "user" if d.is_user else ("group" if d.is_group else
                                          ("channel" if d.is_channel else None))
            if t is None:
                continue
            out.append({"id": did, "name": name,
                        "uname": (getattr(d.entity, "username", None) or ""),
                        "type": t, "unread": int(getattr(d, "unread_count", 0) or 0)})
        except Exception:
            continue
    stats = {"users": 0, "groups": 0, "channels": 0, "unread": 0}
    for d in out:
        k = {"user": "users", "group": "groups", "channel": "channels"}.get(d["type"])
        if k:
            stats[k] += 1
        stats["unread"] += int(d.get("unread") or 0)
    _DIALOG_CACHE.update(ts=now, items=out, stats=stats)
    return out


def _match_dialogs(req, dialogs, max_n=6):
    """تطبیق هوشمند: دقیق → کلمه‌ای → فازی (غلط املایی / فارسی‌انگلیسی)"""
    import difflib
    rq = _fa_norm(req)
    if not rq:
        return []
    # «آخرین پیوی» → جدیدترین گفتگوی خصوصی (اولین user در لیست)
    if any(p in rq for p in LAST_REF):
        for d in dialogs:
            if d.get("type") == "user":
                return [d]
        return []
    scored = []
    for d in dialogs:
        nm = _fa_norm(d["name"])
        un = _fa_norm(d["uname"])
        best = 99
        if nm and nm in rq:
            best = 0
        elif un and len(un) >= 3 and un in rq:
            best = 0
        else:
            words = [w for w in nm.split() if len(w) >= 3]
            if words:
                hits = sum(1 for w in words if w in rq)
                if hits == len(words):
                    best = 1
                elif hits >= 1:
                    best = 2
            if best == 99 and un and len(un) >= 3:
                # فازی روی یوزرنیم — غلط تایپی
                r = difflib.SequenceMatcher(None, un, rq).ratio()
                for tok in rq.split():
                    r = max(r, difflib.SequenceMatcher(None, un, tok).ratio())
                if r >= 0.75:
                    best = 3
            if best == 99:
                # فازی روی هر کلمه‌ی اسم
                for w in nm.split():
                    if len(w) < 3:
                        continue
                    for tok in rq.split():
                        if len(tok) >= 3 and difflib.SequenceMatcher(None, w, tok).ratio() >= 0.75:
                            best = min(best, 4)
            if best == 99:
                # فازی در فضای لاتین — «ادمک» ↔ «adamak»
                nm2 = _to_latin(nm)
                rq2 = _to_latin(rq)
                for w in nm2.split():
                    if len(w) < 3:
                        continue
                    for tok in rq2.split():
                        if len(tok) >= 3 and difflib.SequenceMatcher(None, w, tok).ratio() >= 0.75:
                            best = min(best, 4)
        if best < 99:
            scored.append((best, d))
    scored.sort(key=lambda x: x[0])
    return [d for _, d in scored[:max_n]]


MAGENT_SYSTEM = """تو «دستیار هوشمند شخصی» کاربر در تلگرام هستی و به حساب خودش وصل هستی. درخواست را به دقیقا یک JSON تبدیل کن — هیچ متن اضافی ننویس، فقط JSON.

پروفایل کاربر: {profile}

گفتگوهای مرتبط (برای اشاره به افراد/گروه/کانال فقط از این لیست انتخاب کن):
{dialogs}

{context}

اقدامات:
{{"action":"answer","text":"..."}} — گفتگوی عادی و پاسخ هر سوالی؛ کوتاه، صمیمی، فارسی محاوره‌ای
{{"action":"set_name","first":"...","last":"..."}} — تغییر اسم پروفایل کاربر
{{"action":"set_bio","bio":"..."}} — تغییر بیو
{{"action":"set_username","username":"..."}} — تغییر یوزرنیم (انگلیسی)
{{"action":"set_photo","url":"https://..."}} — عکس پروفایل از لینک
{{"action":"send_msg","to_id":<id>,"text":"..."}} — ارسال پیام به شخص/گروه
{{"action":"read_chat","to_id":<id>,"limit":5}} — خواندن آخرین پیام‌ها
{{"action":"delete_last","to_id":<id>}} — حذف آخرین پیام در گفتگو
{{"action":"react_last","to_id":<id>}} — واکنش 👍 به آخرین پیام
{{"action":"chat_info","to_id":<id>}} — اطلاعات کامل شخص/گروه
{{"action":"join","username":"..."}} — عضویت در کانال/گروه
{{"action":"web_search","query":"..."}} — جستجو در اینترنت (اخبار، قیمت، اطلاعات روز)
{{"action":"schedule","delay_sec":<عدد ثانیه>,"do":{{...اقدام دیگر...}}}} — زمان‌بندی: «یک دقیقه دیگه به فلانی پیام بده» → delay_sec=60 و do=send_msg
{{"action":"auto_reply","to_id":<id>,"on":true}} — روشن‌کردن منشی: هر چی اون شخص بفرسته، خودت به جای کاربر جواب بده
{{"action":"auto_reply","to_id":<id>,"on":false}} — خاموش‌کردن منشی همان شخص
{{"action":"countdown","seconds":<عدد>,"text":"..."}} — شمارش معکوس با ویرایش پیام هر ثانیه (۳ تا ۳۰۰ ثانیه)
{{"action":"web_read","url":"https://..."}} — خواندن متن یک صفحه‌ی وب خاص
{{"action":"translate","text":"...","to":"en|fa|ar..."}} — ترجمه متن
{{"action":"tts","text":"..."}} — تبدیل متن به پیام صوتی فارسی
{{"action":"azan","city":"..."}} — اوقات شرعی (پیش‌فرض کاشان)
{{"action":"weather","city":"..."}} — آب‌وهوای الان (پیش‌فرض کاشان)
{{"action":"lyrics","song":"...","artist":"..."}} — متن آهنگ
{{"action":"speed"}} — تست سرعت اینترنت
{{"action":"purge","count":N}} — پاک کردن N پیام آخر همین گفتگو
{{"action":"stats"}} — آمار پیام همین چت/گروه
{{"action":"whois","to_id":<id>}} — اطلاعات کامل شخص
{{"action":"meme","top":"...","bottom":"..."}} — میم از عکس ریپلای‌شده
{{"action":"ocr"}} — خواندن متن عکس ریپلای‌شده
{{"action":"rmbg"}} — حذف پس‌زمینه عکس ریپلای‌شده
{{"action":"afk","reason":"..."}}, {{"action":"afk","off":true}} — حالت خارج از دسترس (جواب خودکار پیوی)
{{"action":"filter_add","keyword":"...","reply":"..."}}, {{"action":"filter_del","keyword":"..."}} — پاسخ خودکار به کلمه‌ای در پیوی
{{"action":"welcome","on":true|false}} — خوش‌آمدگویی خودکار همین گروه
{{"action":"define","word":"..."}} — معنی کلمه‌ی انگلیسی
{{"action":"calc","expr":"..."}} — ماشین‌حساب (فقط عدد و عملگر)
{{"action":"hash","text":"..."}} — هش SHA-256/MD5 متن
{{"action":"paste","title":"...","text":"..."}} — متن بلند → لینک تلگراف
{{"action":"geo","query":"..."}} — پیدا کردن مکان + مختصات
{{"action":"carbon","code":"..."}} — کد → عکس خوشگل
{{"action":"mediainfo"}} — اطلاعات فایل مدیای ریپلای‌شده
{{"action":"thumb"}} — بندانگشتی ویدیوی ریپلای‌شده
{{"action":"poto","to_id":<id>}} — عکس‌های پروفایل کسی
{{"action":"dice"}} — تاس بینداز در همین چت 🎲
{{"action":"sd","seconds":N,"text":"..."}} — پیام خودمحو: بعد N ثانیه پاک می‌شود
{{"action":"time","city":"..."}} — ساعت و تاریخ شهر (پیش‌فرض کاشان)
{{"action":"wall","query":"..."}} — والپیپر با موضوع
{{"action":"img"}} — عکس تصادفی
{{"action":"logo","text":"..."}} — لوگوی متنی
{{"action":"quote","text":"...","author":"..."}} — کارت نقل‌قول
{{"action":"fry"}} / {{"action":"glitch"}} — افکت روی عکس ریپلای‌شده
{{"action":"poll","question":"...","options":["...","..."]}} — ساخت نظرسنجی در همین چت
{{"action":"create","title":"...","type":"group|channel"}} — ساخت گروه/کانال
{{"action":"rename","name":"..."}} — تغییر نام فایل ریپلای‌شده
{{"action":"archive","on":true|false}} — آرشیو/خارج‌کردن همین چت
{{"action":"invite","username":"..."}} — افزودن کاربر به همین گروه
{{"action":"ban","reply_user":true}} / {{"action":"unban"}} / {{"action":"mute","minutes":N}} / {{"action":"unmute"}} — مدیریت گروه (روی ریپلای)
{{"action":"pin"}} — پین آخرین پیام همین چت
{{"action":"promote"}} / {{"action":"demote"}} — ادمین کردن/برداشتن (روی ریپلای)
{{"action":"md","text":"...**bold**..."}} — متن با فرمت (bold/italic/code...)
{{"action":"broadcast_confirm","text":"..."}} — فقط اگر کاربر صریحاً «به همه بفرست» گفت: ارسال به همه چت‌ها (اول تأیید بگیر)
{{"action":"pro","what":"shot|repo|reddit|np","args":"..."}} — اسکرین‌شات سایت، اطلاعات ریپوی گیت‌هاب، پست‌های ردیت، LastFM (args=آدرس/سابردیت/یوزر)
{{"action":"spoof","type":"typing|game|photo|record_video|upload_video|record_audio|sticker|document|location","off":false}} — حالت نمایشی همیشگی: «در حال نوشتن/بازی/ضبط...» تا وقتی off نشود
{{"action":"clock","on":true|false}} — ساعت زنده روی اسم پروفایل
{{"action":"animate","kind":"typewriter|wave|pop","text":"..."}} — انیمیشن متنی
{{"action":"spotify","query":"لینک یا اسم آهنگ"}} — آهنگ اسپاتیفای را می‌گیرد و می‌فرستد (لینک ترک/پلی‌لیست یا اسم)
{{"action":"sd_send","to_id":<id>,"ttl":<ثانیه؛ پیش‌فرض ۱ = یک‌بارمصرف>}} — عکس/ویدیوی ریپلای‌شده را برای کسی به‌صورت خودموف/یک‌بارمصرف می‌فرستد («این عکس رو خودموف کن برای امین»)
{{"action":"fun","what":"coin|roll|8ball|joke|fact|love|stickify|hack","args":"..."}} — سرگرمی: سکه، تاس، فال، جوک، دانستینی، عشق‌سنج (args=اسم1 | اسم2)، هک نمایشی
{{"action":"warn"}} / {{"action":"warns"}} — اخطار گروهی (روی ریپلای؛ ۳ تا = بن خودکار)
{{"action":"lock","what":"links|media|sticker|poll|all","on":true|false}} — قفل گروه
{{"action":"button","text":"...","buttons":[["label","url"],...]}} — پیام با دکمه‌ی لینک‌دار
{{"action":"confirm","to_id":<id>,"text":"..."}} — وقتی فقط یک گمانه‌ی محتمل داری: سوال کوتاه بپرس («منظورت X هست؟») و کاربر با اره/نه جواب می‌دهد

قواعد هوشمندی:
- غلط املایی، فارسی/انگلیسی قاطی و املای غیررسمی را درست بفهم
- «همون / ادمک / همون دی» یعنی آخرین شخص هدفِ قبل (در زمینه آمده) — دوباره نپرس
- مطمئن نیستی کدام شخص؟ بهترین گمانه را با confirm بپرس — فقط یک سوال کوتاه
- کاملاً نامعلوم؟ answer با یک سوال کوتاه
- هیچ‌وقت جواب خالی نده؛ هر درخواستی جوابی دارد"""


def _md_parse(text):
    """مارک‌داون ساده → (متن، entities) — **bold** *italic* __underline__ ~~strike~~ ||spoiler|| `code`"""
    from telethon.tl.types import (MessageEntityBold, MessageEntityItalic,
                                   MessageEntityUnderline, MessageEntityStrike,
                                   MessageEntitySpoiler, MessageEntityCode)
    rules = [("**", MessageEntityBold), ("__", MessageEntityUnderline),
             ("~~", MessageEntityStrike), ("||", MessageEntitySpoiler),
             ("*", MessageEntityItalic), ("`", MessageEntityCode)]
    s = text or ""
    out = ""
    entities = []
    pos = 0
    while pos < len(s):
        matched = False
        for mark, cls in rules:
            if s.startswith(mark, pos):
                close = s.find(mark, pos + len(mark))
                if close != -1 and close > pos + len(mark):
                    inner = s[pos + len(mark):close]
                    start_idx = len(out)
                    out += inner
                    entities.append(cls(start_idx, len(inner)))
                    pos = close + len(mark)
                    matched = True
                    break
        if not matched:
            out += s[pos]
            pos += 1
    return out, entities


def _parse_magent_json(txt):
    """اولین JSON متوازن — با پشتیبانی از آکولاد تودرتو (زمان‌بندی)"""
    if not txt:
        return None
    start = (txt or "").find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(txt)):
        c = txt[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        d = json.loads(txt[start:i + 1])
                        if isinstance(d, dict) and d.get("action"):
                            return d
                    except Exception:
                        pass
                    return None
    return None


# ─── اقدامات پروفایل/عضویت ───

async def _prof_set_name(first, last):
    from telethon.tl.functions.account import UpdateProfileRequest
    me = await user_client.get_me()
    first = str(first or me.first_name or "")[:64]
    last = str(last if last is not None else (me.last_name or ""))[:64]
    await user_client(UpdateProfileRequest(first_name=first, last_name=last))
    return f"✅ اسم پروفایلت شد: «{(first + ' ' + last).strip()}»"


async def _prof_set_bio(bio):
    from telethon.tl.functions.account import UpdateProfileRequest
    bio = str(bio or "")[:70]
    await user_client(UpdateProfileRequest(about=bio))
    return f"✅ بیوی پروفایلت شد:\n📝 {bio}"


async def _prof_set_username(un):
    from telethon.tl.functions.account import UpdateUsernameRequest
    un = str(un or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", un):
        return "❌ یوزرنیم باید ۵ تا ۳۲ حرف انگلیسی، عدد یا _ باشد."
    await user_client(UpdateUsernameRequest(un))
    return f"✅ یوزرنیمت شد: @{un}"


async def _prof_set_photo_url(url):
    import urllib.request as _u
    url = str(url or "").strip()
    if not url.startswith("http"):
        return "❌ لینک عکس معتبر نیست."
    tmp = "/tmp/magent_pp.jpg"
    req = _u.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _u.urlopen(req, timeout=30) as r, open(tmp, "wb") as w:
        w.write(r.read(8_000_000))
    if Path(tmp).stat().st_size < 2000:
        return "❌ فایل عکس معتبر نبود."
    from telethon.tl.functions.photos import UploadProfilePhotoRequest
    file = await user_client.upload_file(tmp)
    await user_client(UploadProfilePhotoRequest(file=file))
    Path(tmp).unlink(missing_ok=True)
    return "✅ عکس پروفایلت عوض شد."


async def _join_channel(username):
    from telethon.tl.functions.channels import JoinChannelRequest
    un = str(username or "").strip().lstrip("@")
    m_ = re.search(r"t\.me/([A-Za-z0-9_]{4,32})", un)
    if m_:
        un = m_.group(1)
    if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", un):
        return "❌ یوزرنیم کانال/گروه معتبر نیست."
    await user_client(JoinChannelRequest(un))
    return f"✅ عضو «{un}» شدی."


async def _magnet_chat_action(a, target):
    """اقدامات وابسته به مخاطب — None یعنی مربوط نیست"""
    if a == "delete_last":
        msgs = await user_client.get_messages(int(target["id"]), limit=1)
        if not msgs:
            return "❌ پیامی برای حذف نبود."
        await msgs[0].delete()
        return f"✅ آخرین پیام «{target['name']}» حذف شد."
    if a == "react_last":
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            msgs = await user_client.get_messages(int(target["id"]), limit=1)
            if not msgs:
                return "❌ پیامی نبود."
            await user_client(SendReactionRequest(
                peer=int(target["id"]), msg_id=msgs[0].id,
                reaction=[ReactionEmoji(emotion="\U0001F44D")]))
            return f"✅ به آخرین پیام «{target['name']}» 👍 دادم."
        except Exception as ex:
            return f"❌ واکنش نشد: {str(ex)[:80]}"
    if a == "chat_info":
        ent = await user_client.get_entity(int(target["id"]))
        lines = [f"👤 {target['name']}",
                 f"🆔 {ent.id} | @{getattr(ent, 'username', None) or '—'} | نوع: {target.get('type', '?')}"]
        try:
            ph = getattr(ent, "phone", None) or ""
            if ph:
                lines.append(f"📞 +{ph}")
        except Exception:
            pass
        try:
            from telethon.tl.functions.users import GetFullUserRequest
            full = await user_client(GetFullUserRequest(int(target["id"])))
            if full.full_user.about:
                lines.append(f"📝 {full.full_user.about[:120]}")
        except Exception:
            pass
        return "\n".join(lines)
    return None


# ─── خط لوله مشترک دستیار (چت ربات + همه‌جا + گروه AI) ───

def _ctx_summary_block(ctx):
    parts = []
    if ctx.get("summary"):
        parts.append(f"خلاصه‌ی قبلی گفتگو: {ctx['summary'][:800]}")
    lt = ctx.get("last_target")
    if lt:
        parts.append(f"آخرین شخص هدف: {lt.get('name')} (id={lt.get('id')})")
    for h in (ctx.get("history") or [])[-3:]:
        parts.append(f"{('کاربر' if h.get('r') == 'user' else 'تو')}: {h.get('t', '')[:150]}")
    return "زمینه‌ی گفتگو (حافظه):\n" + "\n".join(parts) if parts else "زمینه‌ای نیست — اولین پیام است."


async def _magent_summarize(ctx, dropped):
    """خلاصه‌سازی پیام‌های قدیمی — حافظه‌ی بلندمدت"""
    try:
        txt = "\n".join(f"{('کاربر' if h.get('r') == 'user' else 'تو')}: {h.get('t', '')[:200]}"
                        for h in dropped)
        prompt = (f"این گفتگوی قبلی است:\n{txt[:2500]}\n"
                  "در دو سه خط خلاصه کن: چه کسانی/گروه‌هایی مطرح شد و چه خواسته‌ها و نتیجه‌ها. فقط خلاصه:")
        s = await asyncio.to_thread(aichat.ask_raw, "خلاصه‌نویس مختصر فارسی.", prompt, None, 300)
        ctx["summary"] = ((ctx.get("summary") or "") + "\n" + (s or ""))[-1200:].strip()
    except Exception:
        pass


async def _resolve_sd_target(q):
    """پیدا کردن هدف از اسم/@یوزر/آیدی"""
    q = (q or "").strip()
    if not q:
        return None
    # آیدی عددی
    if q.lstrip("@").isdigit() and not q.startswith("@"):
        try:
            ent = await user_client.get_entity(int(q))
            nm = ((getattr(ent, "first_name", "") or "") + " " +
                  (getattr(ent, "last_name", "") or "")).strip() or str(ent.id)
            return {"id": ent.id, "name": nm, "uname": getattr(ent, "username", None) or "", "type": "user"}
        except Exception:
            return None
    # @یوزرنیم
    if q.startswith("@") or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,30}", q):
        try:
            ent = await user_client.get_entity(q.lstrip("@"))
            nm = ((getattr(ent, "first_name", "") or "") + " " +
                  (getattr(ent, "last_name", "") or "")).strip() or q
            return {"id": ent.id, "name": nm, "uname": getattr(ent, "username", None) or "", "type": "user"}
        except Exception:
            pass
    # تطبیق با گفتگوها
    try:
        dialogs = await _magent_dialogs()
        ms = _match_dialogs(q, dialogs)
        if ms:
            return ms[0]
    except Exception:
        pass
    return None


async def _sd_notify(text):
    """تأییدهای خودموف فقط به چت خصوصی ادمین با ربات — هرگز به چت هدف نمی‌رود"""
    try:
        for aid in ADMIN_IDS:
            await client.send_message(int(aid), str(text)[:1000])
    except Exception:
        pass


async def _sd_send_media(peer_id, path, ttl=1, caption=""):
    """ارسال عکس/ویدیو به‌صورت خودموف (یک‌بارمصرف) از اکانت کاربر"""
    import mimetypes
    from telethon.tl.functions.messages import SendMediaRequest
    from telethon.tl.types import (InputMediaUploadedPhoto,
                                   InputMediaUploadedDocument,
                                   DocumentAttributeVideo)
    if not user_client or not user_client.is_connected():
        raise RuntimeError("اکانت‌خوان وصل نیست")
    p = Path(path)
    if not p.exists():
        raise RuntimeError("فایل پیدا نشد")
    handle = await user_client.upload_file(str(p))
    ttl = max(1, min(int(ttl or 1), 3600))
    # تشخیص نوع از محتوای فایل — فایل‌های دانلودی تلگرام پسوند ندارند!
    kind = None
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(p) as _im:
            _im.verify()
        kind = "photo"
    except Exception:
        pass
    info = {}
    if kind is None:
        try:
            info = ffprobe(p)
            if info.get("video"):
                kind = "video"
        except Exception:
            pass
    if kind == "photo":
        media = InputMediaUploadedPhoto(file=handle, ttl_seconds=ttl)
    elif kind == "video":
        if not info:
            try:
                info = ffprobe(p)
            except Exception:
                info = {}
        attrs = [DocumentAttributeVideo(
            duration=max(1, int(info.get("duration") or 1)),
            w=info.get("w") or 1, h=info.get("h") or 1,
            supports_streaming=True)]
        mime = mimetypes.guess_type(str(p))[0] or "video/mp4"
        media = InputMediaUploadedDocument(
            file=handle, mime_type=mime, attributes=attrs, ttl_seconds=ttl)
    else:
        raise RuntimeError("خودموف فقط برای عکس و ویدیو ممکن است")
    peer = await user_client.get_input_entity(int(peer_id))
    m_ = await user_client(SendMediaRequest(
        peer=peer, media=media, message=(caption or "")[:900],
        random_id=int.from_bytes(os.urandom(8), "big", signed=True)))
    _own_send_guard(m_)
    return m_


async def _magent_exec(chat_id, act, target, send, req="", system=""):
    """اجرای اقدامِ دارای هدف — متن نتیجه را برمی‌گرداند"""
    a = act.get("action")
    if a == "send_msg":
        text = str(act.get("text") or "").strip()[:3500]
        if not text:
            await send("❌ متن پیام مشخص نشد.")
            return "متن پیام مشخص نبود"
        await user_client.send_message(int(target["id"]), text)
        await send(f"✅ به «{target['name']}» گفتم:\n💬 {text}")
        return f"به {target['name']} پیام دادم"
    if a == "read_chat":
        limit = 5
        try:
            limit = max(1, min(int(act.get("limit") or 5), 20))
        except Exception:
            pass
        msgs = []
        async for m_ in user_client.iter_messages(int(target["id"]), limit=limit):
            if m_.message:
                who = "من" if m_.out else target["name"]
                msgs.append(f"[{who}]: {m_.message[:250]}")
        convo = ("\n".join(msgs))[:2500] or "(پیام متنی پیدا نشد)"
        follow = (f"آخرین پیام‌های گفتگو با «{target['name']}»:\n{convo}\n\n"
                  f"درخواست اصلی کاربر: {req}\n"
                  "حالا با action=answer به درخواست کاربر جواب کوتاه بده.")
        extra = ""
        try:
            ans2 = await asyncio.to_thread(aichat.ask_raw, system or MAGENT_SYSTEM.format(
                profile="—", dialogs="—", context="—"), follow, None, 400)
            act2 = _parse_magent_json(ans2)
            extra = (act2 or {}).get("text") or ans2 or ""
        except Exception:
            pass
        await send(f"👁 آخرین پیام‌های «{target['name']}»:\n\n{convo}"
                   + (f"\n\n🧠 {extra[:800]}" if extra else ""))
        return f"گفتگو با {target['name']} را خواندم"
    r_ = await _magnet_chat_action(a, target)
    await send(r_ or "🤔 اقدام نامشخص بود")
    return r_ or "انجام شد"


async def _magent_pipeline(chat_id, req, send, send_btn=None, forced=None,
                           hist_keep=8, send_file=None, photo=None, real_chat=False,
                           media=None, reply_user=None):
    """خط لوله کامل: حافظه → تطبیق → AI → اجرا.
    send: async (text) → None ؛ send_btn: async (text, rows) → None (اختیاری، چت ربات)"""
    ctx = _ctx(chat_id)
    ctx["ts"] = time.time()
    ctx.pop("awaiting", None)      # سوال قبلی جواب گرفت
    rq = _fa_norm(req)

    # ── جواب اره/نه به تایید قبلی ──
    if ctx.get("confirm") and rq in YES_WORDS:
        conf = ctx.pop("confirm")
        _mag_save_ctx()
        return await _magent_pipeline(chat_id, conf["req"], send, send_btn,
                                      forced=[conf["target"]], hist_keep=hist_keep)
    if ctx.get("confirm") and rq in NO_WORDS:
        ctx.pop("confirm")
        others = [d for d in (ctx.get("last_choices") or [])
                  if d["id"] != (ctx.get("last_target") or {}).get("id")]
        ctx["confirm"] = None
        _mag_save_ctx()
        if others:
            names = "\n".join(f"• {d['name']} (@{d['uname'] or '—'})" for d in others[:5])
            await send(f"باشه 🙂 منظورت کدومه؟\n{names}")
        else:
            await send("باشه — اسم دقیقش یا یوزرنیمش رو بگو 🙂")
        return

    # ── انتخاب عددی از لیست قبلی ──
    if ctx.get("last_choices") and ctx.get("last_act") and re.fullmatch(r"[0-9]{1,2}", _fa_digits(rq)):
        idx = int(_fa_digits(rq)) - 1
        ch = ctx["last_choices"]
        if 0 <= idx < len(ch):
            target = ch[idx]
            act = ctx["last_act"]
            ctx["last_target"] = target
            ctx["last_choices"] = None
            ctx["last_act"] = None
            _mag_save_ctx()
            await _magent_exec(chat_id, act, target, send, ctx.get("last_req", ""), "")
            return
        await send("عدد خارج از لیست بود — دوباره بگو.")
        return

    # ── گفتگوها + تطبیق («همون دی» → آخرین هدف) ──
    dialogs = await _magent_dialogs()
    matches = []
    if forced:
        matches = list(forced)
    else:
        ref_hit = any(_fa_norm(w) in rq for w in SAME_REF)
        lt = ctx.get("last_target")
        if ref_hit and lt:
            matches = [lt] + [d for d in _match_dialogs(req, dialogs) if d["id"] != lt["id"]]
        else:
            matches = _match_dialogs(req, dialogs)

    # ── زمینه + AI ──
    profile = _PROFILE_CACHE.get("txt")
    if not profile or time.time() - _PROFILE_CACHE.get("ts", 0) > 300:
        me = await user_client.get_me()
        bio = ""
        try:
            from telethon.tl.functions.users import GetFullUserRequest
            full = await user_client(GetFullUserRequest("me"))
            bio = full.full_user.about or ""
        except Exception:
            pass
        st_ = _DIALOG_CACHE.get("stats") or {}
        prem = "دارد" if getattr(me, "premium", False) else "ندارد"
        n_u = st_.get("users", 0)
        n_g = st_.get("groups", 0)
        n_c = st_.get("channels", 0)
        n_un = st_.get("unread", 0)
        n_all = len(_DIALOG_CACHE.get("items") or [])
        profile = (f"اسم: {(me.first_name or '')} {(me.last_name or '')} | بیو: {bio or '—'}\n"
                   f"آمار حساب: گفتگوها {n_all} (کاربر {n_u}، گروه {n_g}، کانال {n_c})"
                   f" | خوانده‌نشده: {n_un} | پرمیوم: {prem}")
        _PROFILE_CACHE.update(txt=profile, ts=time.time())
    dtxt = "\n".join(f"- id={d['id']} | {d['name']} (@{d['uname'] or '—'}) [{d['type']}]"
                     for d in matches) or "(تطبیقی پیدا نشد — اگر شخصی خواستی از زمینه/لیست قبلی یا بپرس)"
    system = MAGENT_SYSTEM.format(profile=profile, dialogs=dtxt,
                                  context=_ctx_summary_block(ctx))
    if photo or media:
        system += "\nکاربر روی یک فایل ریپلای کرده (عکس/ویدیو) — برای ocr/meme/rmbg/mediainfo/thumb/fry/glitch/rename قابل استفاده است."
    if reply_user:
        system += f"\nکاربر روی پیام کسی ریپلای کرده (user_id={reply_user}) — برای ban/mute/promote/whois/poto/invite قابل استفاده است."
    history = (ctx.get("history") or [])[-hist_keep:]
    ans = await asyncio.to_thread(aichat.ask_raw, system, req, history, 700)
    act = _parse_magent_json(ans)

    if not act:
        await send(f"🧠 {ans[:2000]}")
        ctx["history"] = (ctx.get("history") or [])[-hist_keep:] + [
            {"r": "user", "t": req[:300]}, {"r": "assistant", "t": ans[:300]}]
        if ans.rstrip().endswith(("؟", "?")):
            ctx["awaiting"] = {"ts": time.time(), "topic": req[:150]}
        _mag_save_ctx()
        return

    a = act.get("action")

    # ── تایید (اره/نه) ──
    if a == "confirm":
        to_id = act.get("to_id")
        target = next((d for d in matches if str(d["id"]) == str(to_id)), None) or (matches[0] if matches else None)
        if target is None:
            await send("🤔 کسی برای تایید پیدا نشد — اسم دقیقش رو بگو.")
            return
        ctx["confirm"] = {"req": req, "target": target}
        ctx["last_choices"] = matches
        ctx["last_target"] = target
        _mag_save_ctx()
        q = act.get("text") or f"منظورت «{target['name']}» هست؟"
        if send_btn:
            await send_btn(f"❓ {q}",
                           [[Button.inline("✅ اره", b"m:yes"), Button.inline("❌ نه", b"m:no")]])
        else:
            await send(f"❓ {q}\n(اره / نه)")
        return

    # ── 🧰 ابزارها: ترجمه/ویس/اذان/آب‌وهوا/آهنگ/سرعت ──
    if a == "translate":
        if not TOOLS_OK:
            await send("⚠️ ماژول tools.py آپلود نشده.")
            return
        try:
            r_ = await asyncio.to_thread(tools.translate,
                                         act.get("text") or req, act.get("to") or "en")
            await send(f"🌐 ترجمه به {r_['to_name']}:\n\n{r_['text'][:2500]}")
        except Exception as ex:
            await send(f"❌ ترجمه: {str(ex)[:100]}")
        return
    if a == "tts":
        if not TOOLS_OK:
            await send("⚠️ ماژول tools.py آپلود نشده.")
            return
        try:
            p_ = await asyncio.to_thread(tools.tts, act.get("text") or req)
            if send_file:
                await send_file(p_, voice=True)
                Path(p_).unlink(missing_ok=True)
            else:
                Path(p_).unlink(missing_ok=True)
                await send("❌ اینجا نمی‌توانم ویس بفرستم — در چت خودت با /m یا /tts بزن.")
        except Exception as ex:
            await send(f"❌ ویس: {str(ex)[:100]}")
        return
    if a == "azan" and TOOLS_OK:
        try:
            r_ = await asyncio.to_thread(tools.azan, act.get("city"))
            await send(f"🕌 اوقات شرعی {r_['city']}\n" + "\n".join(r_["lines"])
                       + (f"\n📅 {r_['hijri']}" if r_["hijri"] else ""))
        except Exception as ex:
            await send(f"❌ اوقات شرعی: {str(ex)[:100]}")
        return
    if a == "weather" and TOOLS_OK:
        try:
            r_ = await asyncio.to_thread(tools.weather, act.get("city"))
            await send(
                f"🌤 آب‌وهوای {r_['city']}\n{r_['desc']}\n"
                f"🌡 الان: {r_['temp']}° (حس {r_['feels']}°)\n"
                f"📈 امروز: {r_['min']}° تا {r_['max']}°\n"
                f"💧 رطوبت: {r_['humidity']}% | 💨 باد: {r_['wind']}km/h\n"
                f"☔ احتمال بارش: {r_['rain_prob']}%")
        except Exception as ex:
            await send(f"❌ آب‌وهوا: {str(ex)[:100]}")
        return
    if a == "lyrics" and TOOLS_OK:
        try:
            ly = await asyncio.to_thread(tools.lyrics, act.get("song") or req,
                                         act.get("artist") or "")
            for i in range(0, min(len(ly), 3500), 3800):
                await send(f"🎵 متن آهنگ:\n\n{ly[i:i+3800]}")
        except Exception as ex:
            await send(f"❌ متن آهنگ: {str(ex)[:100]} — فرمت: «خواننده - نام آهنگ»")
        return
    if a == "speed" and TOOLS_OK:
        try:
            await send("⚡ دارم می‌سنجم…")
            r_ = await asyncio.to_thread(tools.speedtest)
            await send(f"⚡ سرعت اینترنت سرور:\n⬇️ دانلود: {r_['down_mbps']} Mbps\n⏱ پینگ: {r_['ping_ms']}ms")
        except Exception as ex:
            await send(f"❌ سرعت‌سنج: {str(ex)[:100]}")
        return

    # ── 🧹 پاک‌سازی / آمار / whois ──
    if a == "purge":
        if not real_chat:
            await send("🧹 این کار را در همان چت انجام بده — همان‌جا /purge یا «/m ده پیام آخر رو پاک کن» بزن.")
            return
        try:
            n_ = 5
            try:
                n_ = max(1, min(int(act.get("count") or 5), 100))
            except Exception:
                n_ = 5
            msgs = await user_client.get_messages(chat_id, limit=n_)
            ids = [x.id for x in msgs if x]
            await user_client.delete_messages(chat_id, ids)
            await send(f"🧹 {len(ids)} پیام آخر پاک شد.")
        except Exception as ex:
            await send(f"❌ پاک‌سازی: {str(ex)[:100]}")
        return
    if a == "stats":
        if not real_chat:
            await send("📊 آمار در همان چت/گروه قابل گرفتن است — همان‌جا «/m آمار این گروه» بزن.")
            return
        try:
            counts = {}
            async for mm in user_client.iter_messages(chat_id, limit=500):
                if not mm.sender_id:
                    continue
                try:
                    nm = ((getattr(mm.sender, "first_name", "") or "") + " " +
                          (getattr(mm.sender, "last_name", "") or "")).strip() or str(mm.sender_id)
                except Exception:
                    nm = str(mm.sender_id)
                counts[nm] = counts.get(nm, 0) + 1
            if not counts:
                await send("آماری پیدا نشد.")
                return
            top = sorted(counts.items(), key=lambda x: -x[1])[:8]
            total = sum(counts.values())
            medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 5
            lines = [f"📊 آمار {total} پیام آخر:", ""]
            for i, (nm, c) in enumerate(top):
                lines.append(f"{medals[i]} {nm[:25]}: {c} پیام ({c * 100 // total}%)")
            await send("\n".join(lines))
        except Exception as ex:
            await send(f"❌ آمار: {str(ex)[:100]}")
        return
    if a == "whois":
        to_id = act.get("to_id")
        target = next((d for d in matches if str(d["id"]) == str(to_id)), None)
        if target is None and matches:
            target = matches[0]
        if target is None:
            await send("❌ کدام شخص؟ اسمش را بگو.")
            return
        r_ = await _magnet_chat_action("chat_info", target)
        await send(r_ or "اطلاعاتی نیامد.")
        return

    # ── 🖼 عکس: میم / OCR / حذف پس‌زمینه ──
    if a in ("meme", "ocr", "rmbg"):
        if not TOOLS_OK:
            await send("⚠️ ماژول tools.py آپلود نشده.")
            return
        if not photo:
            await send("🖼 برای این کار، روی یک عکس ریپلای کن و بعد همان دستور را بزن.")
            return
        try:
            if a == "meme":
                p_ = await asyncio.to_thread(tools.meme, photo,
                                             act.get("top") or "", act.get("bottom") or "")
                if send_file:
                    await send_file(p_, caption="😂 میم آماده‌ست")
                    Path(p_).unlink(missing_ok=True)
                else:
                    Path(p_).unlink(missing_ok=True)
                    await send("✅ ساخته شد ولی اینجا فایل نمی‌توانم بفرستم.")
            elif a == "ocr":
                t_ = await asyncio.to_thread(tools.ocr, photo)
                await send(f"📝 متن داخل عکس:\n\n{t_[:2000]}")
            else:
                p_ = await asyncio.to_thread(tools.removebg, photo)
                if send_file:
                    await send_file(p_, caption="✂️ پس‌زمینه حذف شد")
                    Path(p_).unlink(missing_ok=True)
                else:
                    Path(p_).unlink(missing_ok=True)
                    await send("✅ انجام شد ولی اینجا فایل نمی‌توانم بفرستم.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:150]}")
        return

    # ── 🏃 AFK / 💬 فیلتر / 👋 خوش‌آمد ──
    if a == "afk":
        if act.get("off"):
            AFK.update(on=False)
            _save_afk()
            await send("🏃 حالت خارج از دسترس خاموش شد.")
        else:
            AFK.update(on=True, reason=(act.get("reason") or "")[:100], since=time.time())
            AFK_REPLIED.clear()
            _save_afk()
            extra = f" — {AFK['reason']}" if AFK["reason"] else ""
            await send(f"🏃 حالت AFK روشن شد{extra}.\n"
                       "هر کسی در پیوی پیام بدهد خودکار جواب می‌گیرد. "
                       "با اولین پیام دادن خودت خاموش می‌شود.")
        return
    if a == "filter_add":
        kw = (act.get("keyword") or "").strip()[:60]
        rep = (act.get("reply") or "").strip()[:500]
        if not kw or not rep:
            await send("❌ کلمه و جوابش را بگو.")
            return
        FILTERS[kw] = rep
        _save_filters()
        await send(f"💬 فیلتر ثبت شد: «{kw}» → «{rep[:40]}»\n(در پیوی، اگر کسی این کلمه را بفرستد خودکار جواب می‌گیرد)")
        return
    if a == "filter_del":
        kw = (act.get("keyword") or "").strip()
        if FILTERS.pop(kw, None) is not None:
            _save_filters()
            await send(f"🗑 فیلتر «{kw}» حذف شد.")
        else:
            await send("چنین فیلتری نیست.")
        return
    if a == "welcome":
        if act.get("on"):
            if not real_chat:
                await send("👋 خوش‌آمدگویی برای گروه‌هاست — در همان گروه «/m خوش‌آمدگویی رو روشن کن» بزن.")
                return
            try:
                ent_ = await user_client.get_entity(chat_id)
                title = getattr(ent_, "title", None) or "گروه"
            except Exception:
                title = "گروه"
            WELCOME_GROUPS[str(chat_id)] = title
            _save_welcome()
            await send(f"👋 خوش‌آمدگویی گروه «{title}» روشن شد — هر کی عضو شود خودکار خوش‌آمد می‌گیرد.")
        else:
            WELCOME_GROUPS.pop(str(chat_id), None)
            _save_welcome()
            await send("👋 خوش‌آمدگویی این گروه خاموش شد.")
        return

    # ── 🧰 ابزارهای دسته‌ی دوم ──
    if a == "define" and TOOLS_OK:
        try:
            r_ = await asyncio.to_thread(tools.define, act.get("word") or req)
            await send(r_)
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "calc" and TOOLS_OK:
        try:
            r_ = await asyncio.to_thread(tools.calc, act.get("expr") or req)
            await send(r_)
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "hash" and TOOLS_OK:
        try:
            r_ = await asyncio.to_thread(tools.text_hash, act.get("text") or req)
            await send(r_)
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "paste" and TOOLS_OK:
        try:
            body_ = act.get("text") or req
            title_ = act.get("title") or "متن"
            u_ = await asyncio.to_thread(tools.paste, title_, body_)
            await send(f"📄 متن منتشر شد:\n{u_}")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "geo" and TOOLS_OK:
        try:
            r_ = await asyncio.to_thread(tools.geo, act.get("query") or req)
            await send(f"📍 {r_['name']}\n"
                       f"🌐 مختصات: {r_['lat']:.5f}, {r_['lon']:.5f}\n"
                       f"🗺 https://www.google.com/maps?q={r_['lat']},{r_['lon']}")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "carbon" and TOOLS_OK:
        try:
            p_ = await asyncio.to_thread(tools.carbon, act.get("code") or req,
                                         act.get("lang") or "python")
            if send_file:
                await send_file(p_, caption="🎨 کد")
                Path(p_).unlink(missing_ok=True)
            else:
                Path(p_).unlink(missing_ok=True)
                await send("✅ ساخته شد ولی اینجا فایل نمی‌توانم بفرستم.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "mediainfo" and TOOLS_OK:
        f_ = media or photo
        if not f_:
            await send("🎬 روی یک ویدیو/فایل ریپلای کن و بگو اطلاعاتش را بده.")
            return
        try:
            r_ = await asyncio.to_thread(tools.mediainfo, f_)
            await send(r_)
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "thumb" and TOOLS_OK:
        f_ = media or photo
        if not f_:
            await send("🎬 روی یک ویدیو ریپلای کن و بگو بندانگشتیش را بده.")
            return
        try:
            p_ = await asyncio.to_thread(tools.video_thumb, f_)
            if send_file:
                await send_file(p_, caption="🖼 بندانگشتی")
                Path(p_).unlink(missing_ok=True)
            else:
                Path(p_).unlink(missing_ok=True)
                await send("✅ ساخته شد ولی اینجا فایل نمی‌توانم بفرستم.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "poto":
        to_id = act.get("to_id")
        target = next((d for d in matches if str(d["id"]) == str(to_id)), None)
        if target is None and matches:
            target = matches[0]
        if target is None:
            await send("❌ کدام شخص؟ اسمش را بگو.")
            return
        try:
            sent_p = 0
            for i in range(1, 3):
                pf = f"/tmp/poto_{i}.jpg"
                p_ = await user_client.download_profile_photo(int(target["id"]), file=pf)
                if p_ and Path(pf).exists() and Path(pf).stat().st_size > 1000 and send_file:
                    await send_file(pf, caption=f"👤 عکس {i} — {target['name']}")
                    Path(pf).unlink(missing_ok=True)
                    sent_p += 1
                else:
                    break
            if not sent_p:
                await send(f"❌ عکس پروفایلی برای «{target['name']}» پیدا نشد.")
            elif send_file is None:
                await send(f"✅ دانلود شد ولی اینجا فایل نمی‌توانم بفرستم.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "dice":
        if not real_chat:
            await send("🎲 تاس را در همان چت بریز — همان‌جا «/dice» بزن.")
            return
        try:
            from telethon.tl.types import InputMediaDice
            from telethon.tl.functions.messages import SendMediaRequest
            m_ = await user_client(SendMediaRequest(
                peer=chat_id, media=InputMediaDice("🎲"),
                message="", random_id=int.from_bytes(os.urandom(8), "big", signed=True)))
            _own_send_guard(m_)
            await send("🎲 تاس ریخته شد!")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "sd":
        if not real_chat:
            await send("💥 پیام خودمحو را در همان چت بفرست — همان‌جا «/sd 30 متن» بزن.")
            return
        try:
            n_ = 60
            try:
                n_ = max(5, min(int(act.get("seconds") or 60), 3600))
            except Exception:
                n_ = 60
            txt_ = (act.get("text") or "").strip()[:1500] or "💥 این پیام خودمحوست"
            m_ = await user_client.send_message(chat_id, f"💣 {txt_}\n(⏱ {n_} ثانیه تا پاک شدن)")
            _own_send_guard(m_)

            async def _boom():
                await asyncio.sleep(n_)
                try:
                    await m_.delete()
                except Exception:
                    pass
            asyncio.ensure_future(_boom())
            await send(f"💣 پیام خودمحو فرستاده شد — {n_} ثانیه دیگر پاک می‌شود.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return

    # ── 🧰 ابزارهای دسته‌ی سوم ──
    if a == "time" and TOOLS_OK:
        try:
            r_ = await asyncio.to_thread(tools.world_time, act.get("city") or "کاشان")
            await send(f"🕐 {r_['city']} — {r_['weekday']} {r_['date']}\n⏰ ساعت: {r_['time']}")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "wall" and TOOLS_OK:
        try:
            p_ = await asyncio.to_thread(tools.wall, act.get("query") or "nature")
            if send_file:
                await send_file(p_, caption="🖼 والپیپر")
                Path(p_).unlink(missing_ok=True)
            else:
                Path(p_).unlink(missing_ok=True)
                await send("✅ آمد ولی اینجا فایل نمی‌فرستم.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "img" and TOOLS_OK:
        try:
            p_ = await asyncio.to_thread(tools.random_img)
            if send_file:
                await send_file(p_, caption="🖼")
                Path(p_).unlink(missing_ok=True)
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "logo" and TOOLS_OK:
        try:
            st_ = 0
            try:
                st_ = int(act.get("style") or 0)
            except Exception:
                st_ = 0
            p_ = await asyncio.to_thread(tools.logo, act.get("text") or req, st_)
            if send_file:
                await send_file(p_, caption="🎨 لوگو")
                Path(p_).unlink(missing_ok=True)
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "quote" and TOOLS_OK:
        try:
            p_ = await asyncio.to_thread(tools.quote, act.get("text") or req,
                                          act.get("author") or "")
            if send_file:
                await send_file(p_, caption="💬")
                Path(p_).unlink(missing_ok=True)
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a in ("fry", "glitch") and TOOLS_OK:
        if not photo:
            await send("🖼 روی یک عکس ریپلای کن و بگو افکت بده.")
            return
        try:
            fn_ = tools.fryer if a == "fry" else tools.glitch
            p_ = await asyncio.to_thread(fn_, photo)
            if send_file:
                await send_file(p_, caption="🔥" if a == "fry" else "✨")
                Path(p_).unlink(missing_ok=True)
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return

    # ── 👥 گروه و چت (در همان چت) ──
    if a == "poll":
        if not real_chat:
            await send("📊 نظرسنجی را در همان چت بساز — «/poll سوال | گزینه ۱ | گزینه ۲»")
            return
        try:
            from telethon.tl.types import InputMediaPoll, Poll, PollAnswer
            from telethon.tl.functions.messages import SendMediaRequest
            q_ = (act.get("question") or req)[:255]
            opts = [str(o).strip()[:100] for o in (act.get("options") or []) if str(o).strip()][:10]
            if len(opts) < 2:
                await send("❌ نظرسنجی حداقل ۲ گزینه می‌خواهد.")
                return
            poll = Poll(id=int.from_bytes(os.urandom(8), "big", signed=True),
                        question=q_,
                        answers=[PollAnswer(text=o, option=bytes([i])) for i, o in enumerate(opts)],
                        hash=0, closed=False, multiple_choice=False, quiz=False,
                        public_voters=False)
            m_ = await user_client(SendMediaRequest(
                peer=chat_id, media=InputMediaPoll(poll=poll), message="",
                random_id=int.from_bytes(os.urandom(8), "big", signed=True)))
            _own_send_guard(m_)
            await send(f"📊 نظرسنجی ساخته شد ({len(opts)} گزینه).")
        except Exception as ex:
            await send(f"❌ {str(ex)[:120]}")
        return
    if a == "create":
        try:
            from telethon.tl.functions.channels import CreateChannelRequest
            title = (act.get("title") or "").strip()[:100]
            if not title:
                await send("❌ اسم گروه/کانال را بگو.")
                return
            is_ch = (act.get("type") or "group") == "channel"
            res = await user_client(CreateChannelRequest(
                title=title, about="ساخته‌شده توسط دستیار 🤖", megagroup=not is_ch))
            ch = next((c for c in (res.chats or []) if getattr(c, "id", None)), None)
            if ch is None:
                await send("❌ ساخته نشد.")
                return
            link = f"https://t.me/c/{abs(ch.id)}" if not getattr(ch, "username", None) else f"https://t.me/{ch.username}"
            await send(f"✅ {'کانال' if is_ch else 'گروه'} «{title}» ساخته شد!\n🔗 {link}")
        except Exception as ex:
            await send(f"❌ {str(ex)[:120]}")
        return
    if a == "rename":
        f_ = media
        if not f_:
            await send("📎 روی یک فایل ریپلای کن و بگو اسمش را عوض کنم.")
            return
        new_name = (act.get("name") or "").strip()[:120]
        if not new_name:
            await send("❌ اسم جدید را بگو.")
            return
        try:
            from telethon.tl.types import DocumentAttributeFilename
            if "." not in new_name:
                old_ext = Path(f_).suffix
                if old_ext:
                    new_name += old_ext
            if not real_chat:
                Path(f_).unlink(missing_ok=True)
                await send("✅ آماده بود ولی در همان چت باید بفرستم — همان‌جا روی فایل ریپلای کن و /rename بزن.")
                return
            sent_m = await user_client.send_file(
                chat_id, f_, force_document=True, caption=None,
                attributes=[DocumentAttributeFilename(new_name)])
            _own_send_guard(sent_m)
            Path(f_).unlink(missing_ok=True)
            await send(f"📎 فایل با نام «{new_name}» ارسال شد.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:120]}")
        return
    if a == "archive":
        if not real_chat:
            await send("📁 در همان چت بزن — همان‌جا /archive یا /unarchive.")
            return
        try:
            await user_client.edit_folder(chat_id, folder=1 if act.get("on", True) else 0)
            await send("📁 چت آرشیو شد." if act.get("on", True) else "📤 چت از آرشیو درآمد.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:120]}")
        return
    if a == "invite":
        if not real_chat:
            await send("👥 در همان گروه بزن — «/invite @user».")
            return
        try:
            from telethon.tl.functions.channels import InviteToChannelRequest
            un_ = (act.get("username") or "").strip().lstrip("@")
            if not un_:
                await send("❌ یوزرنیم را بگو.")
                return
            ent = await user_client.get_entity(un_)
            await user_client(InviteToChannelRequest(chat_id, [ent]))
            await send(f"👥 «{un_}» به گروه اضافه شد.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:120]} — شاید حریم خصوصی اجازه نمی‌دهد.")
        return
    if a in ("ban", "unban", "mute", "unmute"):
        if not real_chat:
            await send("🛡 مدیریت فقط در همان گروه — روی پیام کسی ریپلای کن و دستور را بزن.")
            return
        if not reply_user:
            await send("🛡 روی پیام کسی ریپلای کن و بعد بگو.")
            return
        try:
            from telethon.tl.functions.channels import EditBannedRequest
            from telethon.tl.types import ChatBannedRights
            from datetime import datetime, timezone
            if a == "ban":
                rights = ChatBannedRights(view_messages=True, send_messages=True,
                                          until_date=0)
            elif a == "unban":
                rights = ChatBannedRights(view_messages=False, send_messages=False,
                                          until_date=0)
            elif a == "mute":
                mins_ = 60
                try:
                    mins_ = max(1, min(int(act.get("minutes") or 60), 1440))
                except Exception:
                    mins_ = 60
                until = datetime.now(timezone.utc).timestamp() + mins_ * 60
                rights = ChatBannedRights(send_messages=True, until_date=int(until))
            else:
                rights = ChatBannedRights(send_messages=False, until_date=0)
            await user_client(EditBannedRequest(chat_id, reply_user, rights))
            acts_fa = {"ban": "⛔ بن شد", "unban": "✅ بن برداشته شد",
                       "mute": "🔇 میوت شد", "unmute": "🔊 صدا باز شد"}
            await send(acts_fa[a])
        except Exception as ex:
            await send(f"❌ {str(ex)[:120]} — باید ادمین گروه باشی.")
        return
    if a == "pin":
        if not real_chat:
            await send("📌 در همان چت بزن — /pin")
            return
        try:
            from telethon.tl.functions.messages import UpdatePinnedMessageRequest
            msgs = await user_client.get_messages(chat_id, limit=1)
            if not msgs:
                await send("❌ پیامی نیست.")
                return
            await user_client(UpdatePinnedMessageRequest(chat_id, msgs[0].id))
            await send("📌 پیام پین شد.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:120]} — باید ادمین باشی.")
        return
    if a in ("promote", "demote"):
        if not real_chat or not reply_user:
            await send("🛡 در گروه، روی پیام کسی ریپلای کن و بگو ادمینش کن.")
            return
        try:
            from telethon.tl.functions.channels import EditAdminRequest
            from telethon.tl.types import ChatAdminRights
            if a == "promote":
                rights = ChatAdminRights(change_info=True, delete_messages=True,
                                         ban_users=True, invite_users=True,
                                         pin_messages=True, add_admins=False)
                rank = "ادمین"
            else:
                rights = ChatAdminRights()
                rank = ""
            await user_client(EditAdminRequest(chat_id, reply_user, rights, rank))
            await send("⭐ ادمین شد." if a == "promote" else "➖ ادمینی برداشته شد.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:120]} — باید مالک/ادمین‌کل باشی.")
        return
    if a == "md":
        try:
            txt_, ents_ = _md_parse(act.get("text") or req)
            if not txt_:
                await send("❌ متنی بده.")
                return
            if not real_chat:
                await send("✍️ در همان چت بفرست — «/md **متن**»")
                return
            m_ = await user_client.send_message(chat_id, txt_[:3500], entities=ents_)
            _own_send_guard(m_)
        except Exception as ex:
            await send(f"❌ {str(ex)[:120]}")
        return

    # ── 🛠 ابزارهای پرو: اسکرین‌شات/گیت‌هاب/ردیت/لست‌اف‌ام ──
    if a == "pro" and TOOLS_OK:
        what = (act.get("what") or "").lower()
        args_ = act.get("args") or req
        try:
            if what == "shot":
                p_ = await asyncio.to_thread(tools.screenshot, args_)
                if send_file:
                    await send_file(p_, caption=f"📸 {args_[:50]}")
                    Path(p_).unlink(missing_ok=True)
            elif what == "repo":
                r_ = await asyncio.to_thread(tools.repo_info, args_)
                await send(f"📦 {r_['name']}\n⭐ {r_['stars']:,} | 🍴 {r_['forks']:,}\n"
                           f"💻 {r_['lang']} | 📜 {r_['license']}\n📝 {r_['desc']}\n🔗 {r_['url']}")
            elif what == "reddit":
                posts = await asyncio.to_thread(tools.reddit_hot, (args_ or "interestingasfuck").split()[0], None, 5)
                lines_ = [f"🔥 r/{args_}:", ""]
                for i, p_ in enumerate(posts, 1):
                    lines_.append(f"{i}. {p_['title']} ({p_['ups']}⬆️)")
                    lines_.append(p_['url'])
                await send("\n".join(lines_)[:3800])
            elif what == "np":
                r_ = await asyncio.to_thread(tools.lastfm_np, (args_ or "").strip())
                status_ = "🎧 الان در حال پخش" if r_["live"] else "🎵 آخرین آهنگ"
                await send(f"{status_}:\n🎶 {r_['track']}\n🎤 {r_['artist']}")
            else:
                await send("چیزی نشناختم.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:130]}")
        return

    # ── 🎵 اسپاتیفای ──
    if a == "spotify":
        import tools as _tl
        q_ = act.get("query") or req
        try:
            kind, sid = _tl.spotify_parse(q_)
            if kind == "track":
                await send("🎵 دارم می‌گیرم…")
                tr = await asyncio.to_thread(_tl.spotify_track, q_)
                if send_file:
                    await _sp_send_track(chat_id, tr["title"], tr["artists"], tr["duration"])
                else:
                    await send(f"🎧 {tr['title']} — {tr['artists']} (در چت خودت با /spotify می‌گیرم)")
            elif kind in ("playlist", "album"):
                pl = await asyncio.to_thread(_tl.spotify_list, q_, 50)
                SP_CACHE[chat_id] = pl
                await send(f"📋 {pl['name']} — {pl['count']} ترک. دکمه‌ها در چت ربات/یوزربات می‌آید؛ "
                           "اینجا شماره بگو (۱ تا ۱۰) تا بگیرم.")
                ctx["sp_list"] = [t["title"] for t in pl["tracks"][:10]]
                _mag_save_ctx()
            else:
                await send("🎵 دارم می‌گیرم…")
                if send_file:
                    await _sp_send_track(chat_id, q_, "", 0)
                else:
                    await send(f"🎧 برای گرفتن «{q_[:30]}» در چت خودت /spotify {q_[:30]} بزن.")
        except Exception as ex:
            await send(f"❌ {str(ex)[:130]}")
        return

    # ── 🎭 اکشن‌سپوفر / 🕐 ساعت زنده / انیمیشن ──
    if a == "spoof":
        if act.get("off"):
            had = _stop_action(chat_id)
            await send("⏹ حالت نمایشی خاموش شد." if had else "حالتی فعال نبود.")
            return
        t_ = (act.get("type") or "typing").lower()
        if t_ not in ACTION_TYPES:
            await send("نوع نامعتبر.")
            return
        if not real_chat:
            await send("🎭 در همان چت اجرا کن — «/action typing» یا «/m در حال نوشتن رو روشن کن»")
            return
        _stop_action(chat_id)
        task = asyncio.ensure_future(_action_loop(chat_id, t_))
        ACTIONS[chat_id] = {"type": t_, "task": task}
        await send(f"🎭 {ACTION_TYPES[t_]} فعال شد — تا وقتی خاموش نکنی می‌ماند!")
        return
    if a == "clock":
        if act.get("on"):
            if not CLOCK.get("on"):
                CLOCK["on"] = True
                CLOCK["orig_name"] = ""
                CLOCK["task"] = asyncio.ensure_future(_clock_loop())
            await send("🕐 ساعت زنده روی اسمت روشن شد! خاموش: «ساعت رو خاموش کن» یا /clock off")
        else:
            if CLOCK.get("task"):
                CLOCK["task"].cancel()
            CLOCK["on"] = False
            await send("🕐 خاموش شد — اسم برمی‌گردد.")
        return
    if a == "animate":
        try:
            kind = (act.get("kind") or "typewriter").lower()
            text_ = (act.get("text") or "").strip()[:80]
            if not text_:
                await send("متنی بگو.")
                return
            if not real_chat or send_file is None:
                await send("🎨 انیمیشن را در همان چت اجرا کن — «/animate typewriter متن»")
                return
            mm = await user_client.send_message(chat_id, "…")
            _own_send_guard(mm)
            if kind == "typewriter":
                for i in range(1, len(text_) + 1):
                    await mm.edit(text_[:i] + "▌")
                    await asyncio.sleep(0.35)
                await mm.edit(text_)
            elif kind == "wave":
                for i in range(12):
                    await mm.edit("🌊" + " " * (i % 8) + text_)
                    await asyncio.sleep(0.4)
                await mm.edit(text_)
            else:
                for s_ in ["▫", "◻", "◼", "◼", "◻", "▫"] * 2:
                    await mm.edit(f"{s_} {text_} {s_}")
                    await asyncio.sleep(0.45)
                await mm.edit(text_)
        except Exception:
            pass
        return

    # ── 🔥 ارسال خودموف (یک‌بارمصرف) ──
    if a == "sd_send":
        to_id = act.get("to_id")
        target = next((d for d in matches if str(d["id"]) == str(to_id)), None)
        if target is None and matches:
            target = matches[0]
        if target is None:
            # شاید اسم در خود متن باشد
            target = await _resolve_sd_target(req.replace("خودموف", "").replace("بفرست", "")
                                              .replace("یکبارمصرف", "").replace("یک بار مصرف", "")
                                              .replace("برای", "").replace("به", "").strip())
        if target is None:
            await send("🔥 برای کی؟ اسمش یا یوزرنیمش را بگو.")
            return
        f_ = photo or media
        if not f_:
            await send("🖼 روی یک عکس/ویدیو ریپلای کن و بگو برای کی خودموفش کنم.")
            return
        ttl_ = 1
        try:
            ttl_ = max(1, int(act.get("ttl") or 1))
        except Exception:
            ttl_ = 1
        try:
            await _sd_send_media(target["id"], f_, ttl_)
            ctx["last_target"] = target
            # تأیید فقط به چت خصوصی ادمین — به چت هدف هیچ متنی نمی‌رود
            await _sd_notify(f"🔥 خودموف برای «{target['name']}» رفت"
                             + (" — یک‌بارمصرف" if ttl_ <= 1 else f" — {ttl_} ثانیه") + ".")
        except Exception as ex:
            await _sd_notify(f"❌ خودموف: {str(ex)[:120]}")
        return

    # ── 🎮 سرگرمی و دکمه و اخطار ──
    if a == "fun" and TOOLS_OK:
        what = (act.get("what") or "").lower()
        try:
            if what == "coin":
                await send(tools.coinflip())
            elif what == "roll":
                await send(tools.roll(int(act.get("args") or 100) if str(act.get("args") or "").isdigit() else 100))
            elif what == "8ball":
                await send(tools.eightball())
            elif what == "joke":
                await send(tools.joke())
            elif what == "fact":
                await send(tools.fact())
            elif what == "love":
                names_ = [x.strip() for x in str(act.get("args") or "").split("|") if x.strip()]
                if len(names_) < 2:
                    await send("دو اسم بگو 😄")
                else:
                    await send(tools.love(names_[0], names_[1]))
            elif what == "hack":
                import random as _rnd
                if real_chat:
                    mm = await user_client.send_message(chat_id, "🔒 اتصال...")
                    _own_send_guard(mm)
                    for line_ in tools._HACK_LINES:
                        await asyncio.sleep(_rnd.uniform(0.8, 1.6))
                        try:
                            await mm.edit(line_)
                        except Exception:
                            pass
                else:
                    await send("🔒 هک نمایشی را در همان چت اجرا کن — «/hack» بزن.")
            elif what == "stickify":
                if not photo:
                    await send("🖼 روی یک عکس ریپلای کن و بگو استیکرش کن.")
                    return
                p_ = await asyncio.to_thread(tools.stickify, photo)
                if send_file:
                    await send_file(p_)
                Path(p_).unlink(missing_ok=True)
            else:
                await send("چیزی برای سرگرمی نشناختم 😅")
        except Exception as ex:
            await send(f"❌ {str(ex)[:100]}")
        return
    if a == "warn":
        if not real_chat or not reply_user:
            await send("⚖️ در گروه، روی پیام کسی ریپلای کن و بگو اخطارش بده.")
            return
        key = f"{chat_id}:{reply_user}"
        WARNS[key] = WARNS.get(key, 0) + 1
        _save_warns()
        n_ = WARNS[key]
        if n_ >= 3:
            try:
                from telethon.tl.functions.channels import EditBannedRequest
                from telethon.tl.types import ChatBannedRights
                await user_client(EditBannedRequest(
                    chat_id, reply_user,
                    ChatBannedRights(view_messages=True, send_messages=True, until_date=0)))
                WARNS.pop(key, None)
                _save_warns()
                await send("⚖️ ۳ اخطار کامل — ⛔ بن خودکار!")
            except Exception:
                await send("⚖️ اخطار ۳ از ۳! (بن نشد — ادمین نیستی)")
        else:
            await send(f"⚖️ اخطار {n_} از ۳ ثبت شد.")
        return
    if a == "warns":
        items_ = {k: v for k, v in WARNS.items() if k.startswith(f"{chat_id}:")}
        if not items_:
            await send("⚖️ هیچ اخطاری ثبت نشده.")
        else:
            lines_ = ["⚖️ اخطارها:", ""]
            for k, v in list(items_.items())[:15]:
                lines_.append(f"• {k.split(':')[1]}: {v}")
            await send("\n".join(lines_))
        return
    if a == "lock":
        if not real_chat:
            await send("🔒 قفل مال گروه است — در همان گروه بزن.")
            return
        from telethon.tl.functions.channels import EditBannedRequest
        from telethon.tl.types import ChatBannedRights
        what = (act.get("what") or "all").lower()
        on_ = bool(act.get("on", True))
        flag = {"links": "embed_links", "media": "send_media", "sticker": "send_stickers",
                "poll": "send_polls", "gif": "send_gifs", "games": "send_games",
                "inline": "send_inline", "reactions": "send_reactions"}.get(what)
        base = dict(embed_links=False, send_media=False, send_stickers=False,
                    send_polls=False, send_gifs=False, send_games=False,
                    send_inline=False, send_reactions=False)
        if flag:
            base[flag] = on_
        try:
            await user_client(EditBannedRequest(chat_id, chat_id,
                                                ChatBannedRights(until_date=0, **base)))
            await send(("🔒 قفل " if on_ else "🔓 قفل ") + what + (" روشن شد." if on_ else " خاموش شد."))
        except Exception:
            await send("❌ نشد — باید ادمین باشی.")
        return
    if a == "button":
        if not real_chat:
            await send("✅ دکمه را در همان چت بساز — «/button متن | دکمه:لینک»")
            return
        from telethon.tl.types import KeyboardButtonUrl
        txt_ = (act.get("text") or "👇").strip()[:3000]
        rows_ = []
        for b_ in (act.get("buttons") or [])[:8]:
            try:
                label, url = str(b_[0])[:60], str(b_[1])
                if not url.startswith("http"):
                    url = "https://" + url
                rows_.append([KeyboardButtonUrl(label, url[:300])])
            except Exception:
                continue
        if not rows_:
            await send("❌ دکمه‌ای معتبر نیست.")
            return
        m_ = await user_client.send_message(chat_id, txt_, buttons=rows_)
        _own_send_guard(m_)
        return

    # ── زمان‌بندی: «یک دقیقه دیگه به فلانی پیام بده» ──
    if a == "schedule":
        try:
            delay = max(5, min(int(act.get("delay_sec") or 0), 86400))
        except Exception:
            delay = 60
        inner = act.get("do") or {}
        if not isinstance(inner, dict) or not inner.get("action"):
            await send("❌ کار زمان‌بندی‌شده مشخص نشد.")
            return
        mins = delay // 60
        secs = delay % 60
        when = (f"{mins} دقیقه" if mins >= 1 else f"{delay} ثانیه")
        await send(f"⏰ ثبت شد — {when} دیگه انجامش می‌دم")

        async def _later():
            await asyncio.sleep(delay)
            try:
                async def send_later(t):
                    await send(f"⏰ (زمان‌بندی) {t}")
                inner2 = dict(inner)
                ai_ = inner2.get("action")
                to_id = inner2.get("to_id")
                if ai_ in ("send_msg", "read_chat", "delete_last", "react_last", "chat_info"):
                    target = next((d for d in (matches or []) if str(d["id"]) == str(to_id)), None)                         or ctx.get("last_target")
                    if target:
                        await _magnet_exec(chat_id, inner2, target, send_later)
                        return
                    await send_later(f"❌ دیگه «{(inner2.get('text') or '')[:30]}» نرسید — هدف پیدا نشد")
                    return
                if ai_ == "answer":
                    await send_later(inner2.get("text") or "انجام شد")
                    return
                await send_later("⏰ زمان رسید — ولی این نوع کار زمان‌بندی پشتیبانی نمی‌شود")
            except Exception:
                pass

        t_ = asyncio.ensure_future(_later())
        _SCHED_TASKS.append(t_)
        return

    # ── منشی: پاسخ خودکار به یک شخص ──
    if a == "auto_reply":
        to_id = act.get("to_id")
        target = next((d for d in matches if str(d["id"]) == str(to_id)), None)             or (matches[0] if matches else None) or ctx.get("last_target")
        if target is None:
            await send("❌ برای منشی باید بگیم به کی — اسمش را بگو.")
            return
        if act.get("on"):
            AUTO_REPLY[str(target["id"])] = {"name": target["name"], "ts": time.time()}
            _save_autoreply()
            n = len(AUTO_REPLY)
            await send(f"👩‍💼 منشی روشن شد برای «{target['name']}»\n"
                       f"هر چی بفرسته، خودم به جای تو جواب می‌دم.\n"
                       f"خاموش کردن: «منشی {target['name']} رو خاموش کن»\n"
                       f"(فعال: {n} نفر)")
        else:
            AUTO_REPLY.pop(str(target["id"]), None)
            _save_autoreply()
            await send(f"👩‍💼 منشی «{target['name']}» خاموش شد.")
        return

    # ── شمارش معکوس با ویرایش هر ثانیه ──
    if a == "countdown":
        try:
            n_ = max(3, min(int(act.get("seconds") or 10), 300))
        except Exception:
            n_ = 10
        label = (act.get("text") or "").strip()[:60]
        try:
            msg = await user_client.send_message(chat_id, f"⏳ {n_}" + (f" — {label}" if label else ""))
            _own_send_guard(msg)
            for i in range(n_ - 1, 0, -1):
                await asyncio.sleep(1)
                try:
                    await msg.edit(f"⏳ {i}" + (f" — {label}" if label else ""))
                except Exception:
                    pass
            await asyncio.sleep(1)
            await msg.edit(("🚀 " + (label or "شروع!")) if label else "🚀 شروع!")
        except Exception as ex:
            await send(f"❌ شمارش معکوس: {str(ex)[:80]}")
        return

    # ── اقدامات وب: جستجو و خواندن اینترنت ──
    if a == "web_search":
        q_ = str(act.get("query") or req)[:200]
        try:
            results = await asyncio.to_thread(insta.web_search, q_, 6)
            if not results:
                await send("🔍 چیزی پیدا نکردم — سوال را ساده‌تر یا انگلیسی‌تر بنویس.")
                return
            srcs = "\n".join(f"- {r['title']} | {r['url']}" for r in results)
            follow = (f"نتیجه‌های جستجوی «{q_}»:\n{srcs}\n\n"
                      f"سوال اصلی کاربر: {req}\n"
                      "با action=answer جواب کوتاه و کاربردی بده و منبع(ها) را هم ذکر کن.")
            try:
                ans2 = await asyncio.to_thread(aichat.ask_raw, system, follow, None, 500)
                act2 = _parse_magent_json(ans2)
                txt_ = (act2 or {}).get("text") or ans2 or ""
            except Exception:
                txt_ = ""
            body = (f"🔍 {q_[:80]}\n\n{txt_[:1500]}" if txt_ else
                    "🔍 نتایج:\n" + "\n".join(f"• {r['title'][:70]}\n  {r['url'][:80]}" for r in results[:5]))
            await send(body)
        except Exception as ex:
            await send(f"❌ جستجو ناموفق: {str(ex)[:100]}")
        return
    if a == "web_read":
        u_ = str(act.get("url") or "").strip()
        if not u_.startswith("http"):
            await send("❌ لینک معتبر نیست.")
            return
        try:
            txt_ = await asyncio.to_thread(insta.fetch_text, u_, 3000)
            follow = (f"متن صفحه‌ی {u_}:\n{txt_}\n\n"
                      f"درخواست کاربر: {req}\n"
                      "با action=answer خلاصه/جواب کوتاه بده.")
            ans2 = await asyncio.to_thread(aichat.ask_raw, system, follow, None, 500)
            act2 = _parse_magent_json(ans2)
            await send(f"📄 {u_[:70]}\n\n{((act2 or {}).get('text') or ans2 or '')[:1800]}")
        except Exception as ex:
            await send(f"❌ خواندن صفحه ناموفق: {str(ex)[:100]}")
        return

    # ── اقدامات بدون هدف ──
    if a == "answer":
        txt_a = (act.get('text') or '')
        await send(f"🧠 {txt_a[:2000]}")
        ctx["history"] = (ctx.get("history") or [])[-hist_keep:] + [
            {"r": "user", "t": req[:300]},
            {"r": "assistant", "t": txt_a[:300]}]
        if txt_a.rstrip().endswith(("؟", "?")):
            ctx["awaiting"] = {"ts": time.time(), "topic": req[:150]}
        _mag_save_ctx()
        return
    if a == "set_name":
        r = await _prof_set_name(act.get("first"), act.get("last"))
    elif a == "set_bio":
        r = await _prof_set_bio(act.get("bio"))
    elif a == "set_username":
        r = await _prof_set_username(act.get("username"))
    elif a == "set_photo":
        r = await _prof_set_photo_url(act.get("url"))
    elif a == "join":
        r = await _join_channel(act.get("username"))
    else:
        # ── اقدامات دارای هدف ──
        to_id = act.get("to_id")
        target = next((d for d in matches if str(d["id"]) == str(to_id)), None)
        if target is None and len(matches) == 1:
            target = matches[0]
        if target is None and len(matches) > 1:
            ctx["last_choices"] = matches
            ctx["last_act"] = act
            ctx["last_req"] = req
            _mag_save_ctx()
            if send_btn:
                await send_btn("🤔 چند نفر پیدا کردم — کدوم؟",
                               [[Button.inline(f"👤 {m_['name'][:32]}", f"m:p:{i}".encode())]
                                for i, m_ in enumerate(matches[:6])])
            else:
                lst = "\n".join(f"{i+1}. {m_['name']} (@{m_['uname'] or '—'})"
                                for i, m_ in enumerate(matches[:6]))
                await send(f"🤔 چند نفر پیدا کردم — شماره‌ش رو بگو:\n{lst}")
            return
        if target is None:
            await send("❌ کسی با این اسم در گفتگوهایت پیدا نشد — اسم دقیق یا یوزرنیمش رو بگو.")
            _mag_save_ctx()
            return
        ctx["last_target"] = target
        if len(matches) > 1:
            others = "، ".join(m_["name"] for m_ in matches[1:3])
            await send(f"ℹ️ چند نفر پیدا کردم؛ برای «{target['name']}» انجام دادم (بقیه: {others})")
        res = await _magent_exec(chat_id, act, target, send, req, system)
        ctx["history"] = (ctx.get("history") or [])[-hist_keep:] + [
            {"r": "user", "t": req[:300]}, {"r": "assistant", "t": (res or "")[:300]}]
        _mag_save_ctx()
        return
    await send(r)
    ctx["history"] = (ctx.get("history") or [])[-hist_keep:] + [
        {"r": "user", "t": req[:300]}, {"r": "assistant", "t": r[:300]}]
    _mag_save_ctx()


# ─── /m داخل چت ربات ───

@client.on(events.NewMessage(pattern=r"^/m(?:\s+(.+))?$"))
async def cmd_magent(e):
    if e.sender_id not in ADMIN_IDS or not e.is_private:
        return
    if not AGENT_ENABLED:
        return          # کاملاً بی‌واکنش — مثل اینکه وجود ندارد
    await note_user(e)
    req = (e.pattern_match.group(1) or "").strip()
    if not req:
        await e.reply(MAGENT_HELP)
        return
    if not (user_client and user_client.is_connected()):
        await e.reply("👁 اکانت‌خوان وصل نیست — اول /login بزن، بعد /m بده.")
        return
    st = await e.reply("🧠 دارم فکر می‌کنم…")

    async def send(text):
        try:
            await st.edit(str(text)[:4000])
        except Exception:
            await e.reply(str(text)[:4000])

    async def send_btn(text, rows):
        try:
            await st.edit(str(text)[:1000], buttons=rows)
        except Exception:
            await e.reply(str(text)[:1000], buttons=rows)

    async def send_file_(p, caption=None, voice=False):
        kw = {}
        ps = str(p)
        if ps.endswith(".webm") or ps.endswith(".webp"):
            kw["attributes"] = _sticker_attrs(ps)   # استیکر واقعی
        await client.send_file(e.chat_id, p, caption=caption,
                               voice_note=True if voice else None, **kw)

    # عکس/مدیای ریپلای‌شده + فرستنده‌ی پیام ریپلای‌شده
    photo = None
    media = None
    reply_user = None
    try:
        if e.reply_to_msg_id:
            r_ = await e.get_reply_message()
            if r_:
                reply_user = r_.sender_id
                if getattr(r_, "photo", None):
                    photo = await r_.download_media(file="/tmp/mag_photo")
                elif r_.file:
                    media = await r_.download_media(file="/tmp/mag_media")
    except Exception:
        photo = None

    try:
        await _magent_pipeline(e.chat_id, req, send, send_btn,
                               send_file=send_file_, photo=photo, media=media,
                               reply_user=reply_user)
    except Exception as ex:
        try:
            await st.edit(f"❌ {str(ex)[:200]}")
        except Exception:
            pass


@client.on(events.CallbackQuery(pattern=r"^m:(yes|no|p:\d+)$"))
async def on_magent_cb(e):
    if e.sender_id not in ADMIN_IDS:
        return
    data = e.data.decode()
    ctx = _ctx(e.chat_id)

    async def send(text):
        await e.reply(str(text)[:4000])

    if data == "m:yes" and ctx.get("confirm"):
        await e.answer()
        conf = ctx.pop("confirm")
        _mag_save_ctx()
        st = await e.reply("⏳ انجامش می‌دم…")
        async def send2(t):
            try:
                await st.edit(str(t)[:4000])
            except Exception:
                await e.reply(str(t)[:4000])
        await _magent_pipeline(e.chat_id, conf["req"], send2, None, forced=[conf["target"]])
        return
    if data == "m:no":
        await e.answer()
        ctx.pop("confirm", None)
        _mag_save_ctx()
        await e.reply("باشه — اسم دقیقش یا یوزرنیمش رو بگو 🙂")
        return
    if data.startswith("m:p:"):
        await e.answer()
        idx = int(data.split(":")[2])
        ch = ctx.get("last_choices") or []
        act = ctx.get("last_act")
        if not (0 <= idx < len(ch)) or not act:
            await e.reply("منقضی شد — دوباره /m بده.")
            return
        target = ch[idx]
        ctx["last_target"] = target
        ctx["last_choices"] = None
        ctx["last_act"] = None
        _mag_save_ctx()
        st = await e.reply("⏳ دارم انجام می‌دم…")
        async def send3(t):
            try:
                await st.edit(str(t)[:4000])
            except Exception:
                await e.reply(str(t)[:4000])
        await _magent_exec(e.chat_id, act, target, send3, ctx.get("last_req", ""), "")
        return
    await e.answer()


# ─── /m همه‌جا + گروه AI — پیام‌محور (push + poll سبک) ───
# نکته‌ی مهم: سیستم push اکانت‌خوان روی این سرور قابل‌اعتماد نیست
# (خودموف و هویج هم برای همین polling دارند) → هر دو مسیر با ضدتکرار

_OUT_SEEN = deque(maxlen=600)        # آیدی پیام‌های خروجی پردازش‌شده
_OWN_POLL_TASK = None


def _own_send_guard(msg):
    """پیامِ ارسال‌شده توسط خودمان → در ضدتکرار ثبت کن"""
    if msg is not None and getattr(msg, "id", None):
        _OUT_SEEN.append(msg.id)


def _msg_text(m):
    """متن امن پیام — Telethon فیلد caption ندارد (caption همان message است)"""
    try:
        return ((getattr(m, "message", "") or "")
                + " " + (getattr(m, "caption", "") or "")).strip()
    except Exception:
        return (getattr(m, "message", "") or "").strip()


def _sticker_attrs(path=None):
    """صفات لازم برای ارسال فایل به‌عنوان استیکر واقعی"""
    from telethon.tl.types import (DocumentAttributeSticker,
                                   DocumentAttributeVideo, InputStickerSetEmpty)
    attrs = [DocumentAttributeSticker(alt="🎨", stickerset=InputStickerSetEmpty())]
    if path is None or str(path).endswith(".webm"):
        attrs.append(DocumentAttributeVideo(duration=3, w=512, h=512))
    return attrs


async def _get_reply_photo(m):
    """عکس ریپلای‌شده (در چت خود کاربر)"""
    try:
        if m.reply_to_msg_id:
            r_ = await user_client.get_messages(m.chat_id, ids=m.reply_to_msg_id)
            if r_ and getattr(r_, "photo", None):
                return await r_.download_media(file="/tmp/ub_cmd_photo")
    except Exception:
        pass
    return None


_UB_CMDS = ("/purge", "/whois", "/stats", "/afk", "/filter", "/welcome",
            "/tr", "/tts", "/azan", "/weather", "/lyrics", "/speed",
            "/ocr", "/rmbg", "/meme",
            "/dict", "/carbon", "/paste", "/gps", "/calc", "/hash", "/id",
            "/mediainfo", "/thumb", "/poto", "/sd", "/dice", "/note", "/notes",
            "/time", "/wall", "/img", "/logo", "/quote", "/fry", "/glitch",
            "/collage", "/poll", "/create", "/rename", "/archive", "/unarchive",
            "/invite", "/ban", "/unban", "/mute", "/unmute", "/pin",
            "/promote", "/demote", "/md", "/mention", "/broadcast",
            "/button", "/warn", "/warns", "/lock", "/unlock",
            "/stickify", "/kang", "/spam", "/coin", "/roll",
            "/8ball", "/joke", "/fact", "/love", "/hack",
            "/shot", "/repo", "/upload", "/transfer", "/py",
            "/figlet", "/setkey", "/keys", "/delkey",
            "/reddit", "/np", "/nsfw",
            "/action", "/typing", "/clock", "/animate", "/spotify", "/sp", "/js", "/cmd", "/sdto")


async def _ub_command(m, text):
    """دستورهای یوزربات به سبک CatUserBot — در هر چت با اکانت خودت"""
    try:
        parts = text.split(None, 1)
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd not in _UB_CMDS:
            return False
        # بدون دستیار (AGENT_ENABLED=0) فقط /sdto پاسخ می‌دهد
        if not AGENT_ENABLED and cmd != "/sdto":
            return False
        chat = m.chat_id
        if not (user_client and user_client.is_connected()):
            return False
        # 🔧 پیام دستور پاک می‌شود — مثل یوزربات‌های کلاسیک
        # (چت دیگران تمیز می‌ماند؛ جواب/خروجی جداگانه می‌آید)
        try:
            await m.delete()
        except Exception:
            pass

        async def say(t):
            _own_send_guard(await user_client.send_message(chat, str(t)[:3800]))

        async def fsend(p, caption=None, voice=False):
            _own_send_guard(await user_client.send_file(
                chat, p, caption=caption, voice_note=True if voice else None))

        # ── پاک‌سازی ──
        if cmd == "/purge":
            n_ = max(1, min(int(arg) if arg.isdigit() else 5, 100))
            msgs = await user_client.get_messages(chat, limit=n_)
            ids = [x.id for x in msgs if x]
            await user_client.delete_messages(chat, ids)
            await say(f"🧹 {len(ids)} پیام آخر پاک شد.")
            return True
        # ── اطلاعات ──
        if cmd == "/whois":
            target = None
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.sender_id:
                        ent = await user_client.get_entity(r_.sender_id)
                        target = {"id": ent.id, "name": ((getattr(ent, "first_name", "") or "")
                                                        + " " + (getattr(ent, "last_name", "") or "")).strip(),
                                  "uname": getattr(ent, "username", None) or "", "type": "user"}
            except Exception:
                target = None
            if target is None:
                try:
                    ent = await user_client.get_entity(chat)
                    if getattr(ent, "id", None) and not getattr(ent, "title", None):
                        target = {"id": ent.id,
                                  "name": ((getattr(ent, "first_name", "") or "")
                                           + " " + (getattr(ent, "last_name", "") or "")).strip(),
                                  "uname": getattr(ent, "username", None) or "", "type": "user"}
                except Exception:
                    pass
            if target is None:
                await say("❌ روی پیام کسی ریپلای کن و /whois بزن.")
                return True
            r_ = await _magnet_chat_action("chat_info", target)
            await say(r_ or "اطلاعاتی نیامد.")
            return True
        # ── آمار ──
        if cmd == "/stats":
            counts = {}
            async for mm in user_client.iter_messages(chat, limit=500):
                if not mm.sender_id:
                    continue
                try:
                    nm = ((getattr(mm.sender, "first_name", "") or "") + " " +
                          (getattr(mm.sender, "last_name", "") or "")).strip() or str(mm.sender_id)
                except Exception:
                    nm = str(mm.sender_id)
                counts[nm] = counts.get(nm, 0) + 1
            if not counts:
                await say("آماری پیدا نشد.")
                return True
            top = sorted(counts.items(), key=lambda x: -x[1])[:8]
            total = sum(counts.values())
            medals = ["🥇", "🥈", "🥉"] + ["▫️"] * 5
            lines = [f"📊 آمار {total} پیام آخر:", ""]
            for i, (nm, c) in enumerate(top):
                lines.append(f"{medals[i]} {nm[:25]}: {c} پیام ({c * 100 // total}%)")
            await say("\n".join(lines))
            return True
        # ── AFK ──
        if cmd == "/afk":
            if arg.lower() in ("off", "خاموش"):
                AFK.update(on=False)
                _save_afk()
                await say("🏃 حالت AFK خاموش شد.")
            else:
                AFK.update(on=True, reason=arg[:100], since=time.time())
                AFK_REPLIED.clear()
                _save_afk()
                await say(f"🏃 حالت AFK روشن شد{(' — ' + arg) if arg else ''}.")
            return True
        # ── فیلترها ──
        if cmd == "/filter":
            if not arg or arg.lower() in ("list", "لیست"):
                if not FILTERS:
                    await say("💬 فیلتری نداری. افزودن: /filter کلمه=جواب")
                else:
                    await say("💬 فیلترها:\n" + "\n".join(
                        f"• «{k}» → «{v[:30]}»" for k, v in list(FILTERS.items())[:15]))
            elif arg.lower().startswith("del ") or arg.lower().startswith("حذف "):
                kw = arg.split(None, 1)[1].strip() if " " in arg else ""
                if kw and FILTERS.pop(kw, None) is not None:
                    _save_filters()
                    await say(f"🗑 فیلتر «{kw}» حذف شد.")
                else:
                    await say("چنین فیلتری نیست.")
            elif "=" in arg:
                kw, rep = [x.strip() for x in arg.split("=", 1)]
                if kw and rep:
                    FILTERS[kw[:60]] = rep[:500]
                    _save_filters()
                    await say(f"💬 فیلتر ثبت شد: «{kw}» → «{rep[:40]}»")
                else:
                    await say("فرمت: /filter کلمه=جواب")
            else:
                await say("فرمت: /filter کلمه=جواب | لیست: /filter list")
            return True
        # ── خوش‌آمد ──
        if cmd == "/welcome":
            if arg.lower() in ("off", "خاموش"):
                WELCOME_GROUPS.pop(str(chat), None)
                _save_welcome()
                await say("👋 خوش‌آمدگویی این گروه خاموش شد.")
            else:
                try:
                    ent_ = await user_client.get_entity(chat)
                    title = getattr(ent_, "title", None) or "گروه"
                except Exception:
                    title = "گروه"
                WELCOME_GROUPS[str(chat)] = title
                _save_welcome()
                await say(f"👋 خوش‌آمدگویی گروه «{title}» روشن شد.")
            return True
        # ── دستورهای دسته‌ی دوم ──
        if cmd == "/id":
            if m.reply_to_msg_id:
                try:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    await say(f"🆔 کاربر: {r_.sender_id} | چت: {chat}")
                except Exception:
                    await say(f"🆔 چت: {chat}")
            else:
                await say(f"🆔 چت: {chat} | خودت: {m.sender_id}")
            return True
        if cmd == "/calc":
            if not arg:
                await say("فرمت: /calc 25*4+10")
                return True
            r_ = await asyncio.to_thread(tools.calc, arg)
            await say(r_)
            return True
        if cmd == "/hash":
            if not arg:
                await say("فرمت: /hash متن")
                return True
            r_ = await asyncio.to_thread(tools.text_hash, arg)
            await say(r_)
            return True
        if cmd == "/dict":
            if not arg:
                await say("فرمت: /dict word")
                return True
            r_ = await asyncio.to_thread(tools.define, arg)
            await say(r_)
            return True
        if cmd == "/carbon":
            if not arg:
                await say("فرمت: /carbon کد")
                return True
            p_ = await asyncio.to_thread(tools.carbon, arg)
            await fsend(p_, caption="🎨")
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/paste":
            if not arg:
                await say("فرمت: /paste متن بلند")
                return True
            u_ = await asyncio.to_thread(tools.paste, "متن", arg)
            await say(f"📄 منتشر شد:\n{u_}")
            return True
        if cmd == "/gps":
            if not arg:
                await say("فرمت: /gps نام مکان")
                return True
            r_ = await asyncio.to_thread(tools.geo, arg)
            try:
                _own_send_guard(await user_client.send_location(chat, r_["lat"], r_["lon"]))
                await say(f"📍 {r_['name'][:80]}")
            except Exception:
                await say(f"📍 {r_['name']}\n🗺 https://www.google.com/maps?q={r_['lat']},{r_['lon']}")
            return True
        if cmd == "/dice":
            from telethon.tl.types import InputMediaDice
            from telethon.tl.functions.messages import SendMediaRequest
            m_ = await user_client(SendMediaRequest(
                peer=chat, media=InputMediaDice("🎲"),
                message="", random_id=int.from_bytes(os.urandom(8), "big", signed=True)))
            _own_send_guard(m_)
            return True
        if cmd == "/sd":
            parts_ = arg.split(None, 1)
            n_ = int(parts_[0]) if parts_ and parts_[0].isdigit() else 60
            txt_ = parts_[1] if len(parts_) > 1 else "💥 خودمحو"
            if not txt_:
                txt_ = "💥 خودمحو"
            mm = await user_client.send_message(
                chat, f"💣 {txt_}\n(⏱ {n_} ثانیه تا پاک شدن)")
            _own_send_guard(mm)

            async def _del_later():
                await asyncio.sleep(max(5, min(n_, 3600)))
                try:
                    await mm.delete()
                except Exception:
                    pass
            asyncio.ensure_future(_del_later())
            return True
        if cmd == "/poto":
            target_id = None
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.sender_id:
                        target_id = r_.sender_id
                elif arg.lstrip("@").isdigit():
                    target_id = int(arg)
            except Exception:
                target_id = None
            if target_id is None:
                await say("👤 روی پیام کسی ریپلای کن و /poto بزن (یا /poto آیدی)")
                return True
            sent_p = 0
            for i in range(1, 3):
                pf = f"/tmp/ub_poto_{i}.jpg"
                p_ = await user_client.download_profile_photo(target_id, file=pf)
                if p_ and Path(pf).exists() and Path(pf).stat().st_size > 1000:
                    await fsend(pf, caption=f"👤 عکس {i}")
                    Path(pf).unlink(missing_ok=True)
                    sent_p += 1
                else:
                    break
            if not sent_p:
                await say("❌ عکس پروفایلی پیدا نشد.")
            return True
        if cmd in ("/mediainfo", "/thumb"):
            f_ = None
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.file:
                        f_ = await r_.download_media(file="/tmp/ub_mi")
            except Exception:
                f_ = None
            if not f_:
                await say("🎬 روی یک ویدیو/فایل ریپلای کن و دستور را بزن.")
                return True
            if cmd == "/mediainfo":
                r_ = await asyncio.to_thread(tools.mediainfo, f_)
                Path(f_).unlink(missing_ok=True)
                await say(r_)
            else:
                p_ = await asyncio.to_thread(tools.video_thumb, f_)
                Path(f_).unlink(missing_ok=True)
                await fsend(p_, caption="🖼 بندانگشتی")
                Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/note":
            if not arg:
                await say("فرمت: /note متن | لیست: /notes | حذف: /note del شماره")
                return True
            if arg.lower().startswith("del") and len(arg.split()) > 1 and arg.split()[1].isdigit():
                i_ = int(arg.split()[1]) - 1
                notes_l = NOTES.get("items", [])
                if 0 <= i_ < len(notes_l):
                    removed = notes_l.pop(i_)
                    _save_notes()
                    await say(f"🗑 یادداشت حذف شد: {removed['t'][:40]}")
                else:
                    await say("چنین شماره‌ای نیست.")
                return True
            NOTES.setdefault("items", []).append({"t": arg[:500], "ts": time.time()})
            NOTES["items"] = NOTES["items"][-50:]
            _save_notes()
            await say(f"📝 یادداشت شماره {len(NOTES['items'])} ذخیره شد.")
            return True
        if cmd == "/notes":
            items = NOTES.get("items", [])
            if not items:
                await say("📝 یادداشتی نداری — /note متن")
            else:
                lines = ["📝 یادداشت‌ها:", ""]
                for i, it in enumerate(items[:20], 1):
                    lines.append(f"{i}. {it['t'][:60]}")
                await say("\n".join(lines))
            return True

        # ── دستورهای دسته‌ی سوم ──
        if cmd == "/time":
            r_ = await asyncio.to_thread(tools.world_time, arg or "کاشان")
            await say(f"🕐 {r_['city']} — {r_['weekday']} {r_['date']}\n⏰ ساعت: {r_['time']}")
            return True
        if cmd == "/wall":
            p_ = await asyncio.to_thread(tools.wall, arg or "nature")
            await fsend(p_, caption="🖼 والپیپر")
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/img":
            p_ = await asyncio.to_thread(tools.random_img)
            await fsend(p_)
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/logo":
            style = 0
            body = arg
            if body and body.split() and body.split()[0].isdigit():
                style = int(body.split()[0])
                body = body.split(None, 1)[1] if len(body.split()) > 1 else ""
            if not body:
                await say("فرمت: /logo [شماره استایل ۰-۵] متن")
                return True
            p_ = await asyncio.to_thread(tools.logo, body, style)
            await fsend(p_, caption="🎨")
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/quote":
            body = arg
            author = ""
            if body and " | " in body:
                body, author = body.split(" | ", 1)
            if not body and m.reply_to_msg_id:
                try:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    body = (r_.text or r_.message or "").strip()
                    if r_.sender:
                        author = ((getattr(r_.sender, "first_name", "") or "") + " " +
                                  (getattr(r_.sender, "last_name", "") or "")).strip()
                except Exception:
                    pass
            if not body:
                await say("فرمت: /quote متن | نویسنده — یا روی پیامی ریپلای کن")
                return True
            p_ = await asyncio.to_thread(tools.quote, body, author)
            await fsend(p_, caption="💬")
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd in ("/fry", "/glitch"):
            photo = await _get_reply_photo(m)
            if not photo:
                await say("🖼 روی یک عکس ریپلای کن.")
                return True
            fn_ = tools.fryer if cmd == "/fry" else tools.glitch
            p_ = await asyncio.to_thread(fn_, photo)
            Path(photo).unlink(missing_ok=True)
            await fsend(p_)
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/collage":
            # عکس‌های اخیر چت (تا ۹ تا)
            pics = []
            async for mm in user_client.iter_messages(chat, limit=30):
                if mm and getattr(mm, "photo", None):
                    pth = await mm.download_media(file=f"/tmp/cg_{len(pics)}")
                    if pth:
                        pics.append(pth)
                if len(pics) >= 6:
                    break
            if len(pics) < 2:
                await say("🖼 حداقل ۲ عکس اخیر در چت لازم است.")
                return True
            p_ = await asyncio.to_thread(tools.collage, pics)
            for x_ in pics:
                Path(x_).unlink(missing_ok=True)
            await fsend(p_, caption="🖼 کولاز")
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/poll":
            parts_ = [x.strip() for x in arg.split("|") if x.strip()]
            if len(parts_) < 3:
                await say("فرمت: /poll سوال | گزینه ۱ | گزینه ۲ | ...")
                return True
            from telethon.tl.types import InputMediaPoll, Poll, PollAnswer
            from telethon.tl.functions.messages import SendMediaRequest
            poll = Poll(id=int.from_bytes(os.urandom(8), "big", signed=True),
                        question=parts_[0][:255],
                        answers=[PollAnswer(text=o[:100], option=bytes([i]))
                                 for i, o in enumerate(parts_[1:11])],
                        hash=0, closed=False, multiple_choice=False, quiz=False,
                        public_voters=False)
            m_ = await user_client(SendMediaRequest(
                peer=chat, media=InputMediaPoll(poll=poll), message="",
                random_id=int.from_bytes(os.urandom(8), "big", signed=True)))
            _own_send_guard(m_)
            return True
        if cmd == "/create":
            parts_ = arg.split(None, 1)
            if len(parts_) < 2:
                await say("فرمت: /create group|channel اسم")
                return True
            kind = parts_[0].lower()
            title = parts_[1].strip()[:100]
            from telethon.tl.functions.channels import CreateChannelRequest
            res = await user_client(CreateChannelRequest(
                title=title, about="ساخته‌شده توسط دستیار 🤖",
                megagroup=(kind != "channel")))
            ch = next((c for c in (res.chats or []) if getattr(c, "id", None)), None)
            if ch:
                link = f"https://t.me/c/{abs(ch.id)}"
                await say(f"✅ ساخته شد!\n🔗 {link}")
            return True
        if cmd == "/rename":
            if not arg:
                await say("فرمت: /rename اسم-جدید — روی فایل ریپلای کن")
                return True
            f_ = None
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.file:
                        f_ = await r_.download_media(file="/tmp/ren_f")
            except Exception:
                f_ = None
            if not f_:
                await say("📎 روی یک فایل ریپلای کن.")
                return True
            from telethon.tl.types import DocumentAttributeFilename
            new_name = arg.strip()[:120]
            if "." not in new_name and Path(f_).suffix:
                new_name += Path(f_).suffix
            sent_m = await user_client.send_file(
                chat, f_, force_document=True,
                attributes=[DocumentAttributeFilename(new_name)])
            _own_send_guard(sent_m)
            Path(f_).unlink(missing_ok=True)
            await say(f"📎 «{new_name}» ارسال شد.")
            return True
        if cmd in ("/archive", "/unarchive"):
            await user_client.edit_folder(chat, folder=1 if cmd == "/archive" else 0)
            await say("📁 چت آرشیو شد." if cmd == "/archive" else "📤 چت از آرشیو درآمد.")
            return True
        if cmd == "/invite":
            if not arg:
                await say("فرمت: /invite @user")
                return True
            from telethon.tl.functions.channels import InviteToChannelRequest
            ent = await user_client.get_entity(arg.strip().lstrip("@"))
            await user_client(InviteToChannelRequest(chat, [ent]))
            await say(f"👥 «{arg.strip().lstrip('@')}» اضافه شد.")
            return True
        if cmd in ("/ban", "/unban", "/mute", "/unmute"):
            uid = None
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.sender_id:
                        uid = r_.sender_id
                elif arg.lstrip("@").isdigit():
                    uid = int(arg)
            except Exception:
                uid = None
            if not uid:
                await say("🛡 روی پیام کسی ریپلای کن (یا /ban آیدی).")
                return True
            from telethon.tl.functions.channels import EditBannedRequest
            from telethon.tl.types import ChatBannedRights
            from datetime import datetime, timezone
            if cmd == "/ban":
                rights = ChatBannedRights(view_messages=True, send_messages=True, until_date=0)
            elif cmd == "/unban":
                rights = ChatBannedRights(view_messages=False, send_messages=False, until_date=0)
            elif cmd == "/mute":
                mins_ = int(arg.split()[0]) if arg.split() and arg.split()[0].isdigit() else 60
                mins_ = max(1, min(mins_, 1440))
                until = datetime.now(timezone.utc).timestamp() + mins_ * 60
                rights = ChatBannedRights(send_messages=True, until_date=int(until))
            else:
                rights = ChatBannedRights(send_messages=False, until_date=0)
            await user_client(EditBannedRequest(chat, uid, rights))
            msg_fa = {"ban": "⛔ بن شد", "unban": "✅ بن برداشته شد",
                      "mute": "🔇 میوت شد", "unmute": "🔊 صدا باز شد"}[cmd]
            await say(msg_fa)
            return True
        if cmd == "/pin":
            from telethon.tl.functions.messages import UpdatePinnedMessageRequest
            msgs = await user_client.get_messages(chat, limit=1)
            if not msgs:
                await say("❌ پیامی نیست.")
                return True
            await user_client(UpdatePinnedMessageRequest(chat, msgs[0].id))
            await say("📌 پین شد.")
            return True
        if cmd in ("/promote", "/demote"):
            uid = None
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.sender_id:
                        uid = r_.sender_id
            except Exception:
                uid = None
            if not uid:
                await say("⭐ روی پیام کسی ریپلای کن.")
                return True
            from telethon.tl.functions.channels import EditAdminRequest
            from telethon.tl.types import ChatAdminRights
            if cmd == "/promote":
                rights = ChatAdminRights(change_info=True, delete_messages=True,
                                         ban_users=True, invite_users=True,
                                         pin_messages=True, add_admins=False)
                await user_client(EditAdminRequest(chat, uid, rights, "ادمین"))
                await say("⭐ ادمین شد.")
            else:
                await user_client(EditAdminRequest(chat, uid, ChatAdminRights(), ""))
                await say("➖ ادمینی برداشته شد.")
            return True
        if cmd == "/md":
            if not arg:
                await say("فرمت: /md **متن** *ایتالیک* ~~خط‌خورده~~ ||اسپویل|| `کد`")
                return True
            txt_, ents_ = _md_parse(arg)
            m_ = await user_client.send_message(chat, txt_[:3500], entities=ents_)
            _own_send_guard(m_)
            try:
                await m.delete()
            except Exception:
                pass
            return True
        if cmd == "/mention":
            # /mention @user اسم-سفارشی
            parts_ = arg.split(None, 1)
            if not parts_:
                await say("فرمت: /mention @user اسم — یا روی پیام کسی ریپلای کن")
                return True
            uname = parts_[0].strip().lstrip("@")
            label = parts_[1].strip()[:40] if len(parts_) > 1 else ("@" + uname)
            from telethon.tl.types import MessageEntityMentionName
            ent_ = await user_client.get_entity(uname)
            m_ = await user_client.send_message(
                chat, label, entities=[MessageEntityMentionName(0, len(label), ent_.id)])
            _own_send_guard(m_)
            try:
                await m.delete()
            except Exception:
                pass
            return True
        if cmd == "/broadcast":
            if not arg:
                await say("فرمت: /broadcast متن — به همه چت‌هایت می‌فرستد (تأیید می‌گیرد)")
                return True
            # شمارش و تأیید
            n_dialogs = 0
            async for d_ in user_client.iter_dialogs(limit=200):
                if not d_.is_user or getattr(d_.entity, "bot", False) is False:
                    n_dialogs += 1
            confirm_m = await user_client.send_message(
                chat, f"📣 به {n_dialogs} چت فرستاده شود؟ (اره / نه)")
            _own_send_guard(confirm_m)
            BROADCAST_PENDING[m.chat_id] = {"text": arg[:3000], "ts": time.time()}
            return True

        # ── دسته‌ی چهارم: دکمه/اخطار/قفل/استیکر/سرگرمی ──
        if cmd == "/button":
            # /button متن | دکمه:لینک | دکمه:لینک
            parts_ = [x.strip() for x in arg.split("|") if x.strip()]
            if len(parts_) < 2:
                await say("فرمت: /button متن | دکمه:https://... | دکمه:https://...")
                return True
            from telethon.tl.types import KeyboardButtonUrl
            rows_ = []
            for p_ in parts_[1:9]:
                if ":" not in p_:
                    continue
                label, url = p_.split(":", 1)
                if url.startswith("//"):
                    url = "https:" + url
                if not url.startswith("http"):
                    url = "https://" + url
                rows_.append([KeyboardButtonUrl(label.strip()[:60], url.strip()[:300])])
            if not rows_:
                await say("فرمت: /button متن | دکمه:https://...")
                return True
            m_ = await user_client.send_message(chat, parts_[0][:3000], buttons=rows_)
            _own_send_guard(m_)
            try:
                await m.delete()
            except Exception:
                pass
            return True
        if cmd in ("/warn", "/warns"):
            if cmd == "/warns":
                items_ = {k: v for k, v in WARNS.items() if k.startswith(f"{chat}:")}
                if not items_:
                    await say("⚖️ هیچ اخطاری در این چت ثبت نشده.")
                else:
                    lines_ = ["⚖️ اخطارهای این چت:", ""]
                    for k, v in list(items_.items())[:15]:
                        lines_.append(f"• {k.split(':')[1]}: {v} اخطار")
                    await say("\n".join(lines_))
                return True
            uid = None
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.sender_id:
                        uid = r_.sender_id
                elif arg.strip().lstrip("@").isdigit():
                    uid = int(arg)
            except Exception:
                uid = None
            if not uid:
                await say("⚖️ روی پیام کسی ریپلای کن و /warn بزن.")
                return True
            key = f"{chat}:{uid}"
            WARNS[key] = WARNS.get(key, 0) + 1
            _save_warns()
            n_ = WARNS[key]
            if n_ >= 3:
                # بن خودکار
                try:
                    from telethon.tl.functions.channels import EditBannedRequest
                    from telethon.tl.types import ChatBannedRights
                    await user_client(EditBannedRequest(
                        chat, uid, ChatBannedRights(view_messages=True,
                                                    send_messages=True, until_date=0)))
                    WARNS.pop(key, None)
                    _save_warns()
                    await say(f"⚖️ ۳ اخطار کامل شد — ⛔ بن خودکار!")
                except Exception:
                    await say(f"⚖️ اخطار ۳ از ۳! (بن نشد — ادمین نیستی)")
            else:
                await say(f"⚖️ اخطار {n_} از ۳ ثبت شد.")
            return True
        if cmd in ("/lock", "/unlock"):
            what = arg.strip().lower() or "all"
            from telethon.tl.functions.channels import EditBannedRequest
            from telethon.tl.types import ChatBannedRights
            on_ = (cmd == "/lock")
            flag = {"links": "embed_links", "media": "send_media",
                    "sticker": "send_stickers", "poll": "send_polls",
                    "gif": "send_gifs", "games": "send_games",
                    "inline": "send_inline", "reactions": "send_reactions",
                    "all": None}.get(what)
            base = dict(embed_links=False, send_media=False, send_stickers=False,
                        send_polls=False, send_gifs=False, send_games=False,
                        send_inline=False, send_reactions=False)
            if flag:
                base[flag] = on_
            rights = ChatBannedRights(until_date=0, **base)
            try:
                await user_client(EditBannedRequest(chat, chat, rights))
                fa_ = {"links": "لینک", "media": "مدیا", "sticker": "استیکر",
                       "poll": "نظرسنجی", "gif": "گیف", "games": "بازی",
                       "inline": "اینلاین", "all": "همه‌ی"}.get(what, what)
                await say(f"🔒 قفل {fa_} " + ("روشن شد." if on_ else "خاموش شد."))
            except Exception:
                await say("❌ نشد — باید ادمین گروه باشی.")
            return True
        if cmd == "/stickify":
            photo = await _get_reply_photo(m)
            if not photo:
                await say("🖼 روی یک عکس ریپلای کن و /stickify بزن — استیکرش می‌کنم.")
                return True
            try:
                p_ = await asyncio.to_thread(tools.stickify, photo)
                Path(photo).unlink(missing_ok=True)
                await fsend(p_)
                Path(p_).unlink(missing_ok=True)
            except Exception as ex:
                await say(f"❌ {str(ex)[:100]}")
            return True
        if cmd == "/kang":
            # دزدیدن استیکر: روی استیکر ریپلای کن → همان را برایت می‌فرستد
            try:
                if not m.reply_to_msg_id:
                    await say("🐒 روی یک استیکر ریپلای کن و /kang بزن.")
                    return True
                r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                if not (r_ and getattr(r_, "sticker", None)):
                    await say("این استیکر نیست! روی استیکر ریپلای کن.")
                    return True
                p_ = await r_.download_media(file="/tmp/kang")
                if not p_:
                    await say("❌ دانلود استیکر نشد.")
                    return True
                _own_send_guard(await user_client.send_file(
                    chat, p_, attributes=_sticker_attrs(p_)))
                Path(p_).unlink(missing_ok=True)
                await say("🐒 استیکر دزدیده شد و برات فرستادم! (برای پک خودت: عکس/استیکر را در چت با @Stickers فوروارد کن)")
            except Exception as ex:
                await say(f"❌ {str(ex)[:100]}")
            return True
        if cmd == "/spam":
            # /spam N متن — سقف ۱۵
            parts_ = arg.split(None, 1)
            n_ = int(parts_[0]) if parts_ and parts_[0].isdigit() else 5
            n_ = max(1, min(n_, 15))
            txt_ = parts_[1] if len(parts_) > 1 else "🐝"
            for i_ in range(n_):
                mm = await user_client.send_message(chat, txt_[:500])
                _own_send_guard(mm)
                await asyncio.sleep(0.8)
            return True
        if cmd == "/coin":
            await say(tools.coinflip())
            return True
        if cmd == "/roll":
            n_ = int(arg) if arg.isdigit() else 100
            await say(tools.roll(n_))
            return True
        if cmd == "/8ball":
            await say(tools.eightball())
            return True
        if cmd == "/joke":
            await say(tools.joke())
            return True
        if cmd == "/fact":
            await say(tools.fact())
            return True
        if cmd == "/love":
            names_ = [x.strip() for x in arg.split("|") if x.strip()] if "|" in arg else arg.split(None, 1)
            if len(names_) < 2:
                await say("فرمت: /love علی | سارا 💘")
                return True
            await say(tools.love(names_[0], names_[1]))
            return True
        if cmd == "/hack":
            import random as _rnd
            mm = await user_client.send_message(chat, "🔒 اتصال...")
            _own_send_guard(mm)
            for line_ in tools._HACK_LINES:
                await asyncio.sleep(_rnd.uniform(0.8, 1.6))
                try:
                    await mm.edit(line_)
                except Exception:
                    pass
            return True

        # ── دسته‌ی پنجم: اکانت‌دار و ابزارهای پرو ──
        if cmd == "/setkey":
            parts_ = arg.split(None, 1)
            if len(parts_) < 2 or parts_[0].lower() not in ("reddit", "lastfm", "deepai", "removebg", "spotify", "proxy"):
                await say(KEYS_HELP)
                return True
            _set_userkey(parts_[0].lower(), parts_[1].strip())
            os.environ[parts_[0].lower()] = parts_[1].strip()
            await say(f"🔑 کلید «{parts_[0].lower()}» ذخیره شد — الان فعال است!")
            return True
        if cmd == "/keys":
            try:
                d_ = json.loads(Path("/tmp/userkeys.json").read_text())
            except Exception:
                d_ = {}
            d2 = {k: ("✅ " + v[:4] + "…") for k, v in d_.items()}
            if os.environ.get("REMOVE_BG_KEY"):
                d2.setdefault("removebg", "✅ از Secrets")
            if os.environ.get("AI_API_KEY"):
                d2.setdefault("ai", "✅ از Secrets")
            await say("🔑 کلیدهای فعال:\n" + ("\n".join(f"• {k}: {v}" for k, v in d2.items()) or "هیچ — /setkey")) if d2 else await say(KEYS_HELP)
            return True
        if cmd == "/delkey":
            if not arg:
                await say("فرمت: /delkey reddit")
                return True
            ok_ = _del_userkey(arg.strip().lower())
            await say("🗑 حذف شد." if ok_ else "چنین کلیدی نداری.")
            return True
        if cmd == "/shot":
            if not arg:
                await say("فرمت: /shot آدرس-سایت 📸")
                return True
            p_ = await asyncio.to_thread(tools.screenshot, arg)
            await fsend(p_, caption=f"📸 اسکرین‌شات {arg[:60]}")
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/repo":
            if not arg:
                await say("فرمت: /repo user/repo")
                return True
            r_ = await asyncio.to_thread(tools.repo_info, arg)
            await say(f"📦 {r_['name']}\n⭐ {r_['stars']:,} | 🍴 {r_['forks']:,} | ⚠️ {r_['issues']}"
                      f"\n💻 {r_['lang']} | 📜 {r_['license']} | 📅 {r_['created']}\n"
                      f"📝 {r_['desc']}\n🔗 {r_['url']}")
            return True
        if cmd == "/upload":
            f_ = None
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.file:
                        f_ = await r_.download_media(file="/tmp/up_f")
            except Exception:
                f_ = None
            if not f_:
                await say("📎 روی یک فایل ریپلای کن و /upload بزن — لینکش را می‌دهم (۳ ساعت معتبر)")
                return True
            try:
                await say("⏳ آپلود…")
                u_ = await asyncio.to_thread(tools.uguu_upload, f_)
                Path(f_).unlink(missing_ok=True)
                await say(f"📎 آپلود شد:\n{u_}")
            except Exception as ex:
                Path(f_).unlink(missing_ok=True)
                await say(f"❌ {str(ex)[:100]}")
            return True
        if cmd == "/transfer":
            # /transfer @channel [تعداد] — آخرین پیام‌های کانال را همین‌جا فوروارد کن
            parts_ = arg.split()
            if not parts_:
                await say("فرمت: /transfer @کانال [تعداد=10] — پیام‌های کانال را همین‌جا می‌آورد")
                return True
            src_ = parts_[0]
            n_ = int(parts_[1]) if len(parts_) > 1 and parts_[1].isdigit() else 10
            n_ = max(1, min(n_, 50))
            try:
                msgs = await user_client.get_messages(src_, limit=n_)
                await say(f"📤 {len(msgs)} پیام از {src_} می‌آید…")
                for mm in reversed([x for x in msgs if x]):
                    try:
                        fwd = await user_client.forward_messages(chat, mm)
                        _own_send_guard(fwd)
                    except Exception:
                        break
            except Exception as ex:
                await say(f"❌ {str(ex)[:100]} — کانال خصوصی است یا وجود ندارد")
            return True
        if cmd == "/py":
            if not arg:
                await say("فرمت: /py print(2+2) — اجرای پایتون (فقط خودت)")
                return True
            import io as _io, contextlib as _cl
            buf = _io.StringIO()
            try:
                with _cl.redirect_stdout(buf):
                    exec(arg[:2000], {"__builtins__": __builtins__}, {})
                out_ = buf.getvalue().strip()[:3000] or "✅ (بدون خروجی)"
            except Exception as ex:
                out_ = f"❌ {type(ex).__name__}: {str(ex)[:200]}"
            await say(f"🐍 {out_}")
            return True
        if cmd == "/figlet":
            if not arg:
                await say("فرمت: /figlet TEXT")
                return True
            try:
                import pyfiglet
                art = pyfiglet.figlet_format(arg[:30])
                await say("🎨 ```\n" + art[:3000] + "\n```")
            except Exception as ex:
                await say(f"❌ {str(ex)[:80]}")
            return True
        if cmd == "/reddit":
            sub = arg.strip() or "interestingasfuck"
            try:
                posts = await asyncio.to_thread(tools.reddit_hot, sub, None, 5)
                lines_ = [f"🔥 r/{sub}:", ""]
                for i, p_ in enumerate(posts, 1):
                    lines_.append(f"{i}. {'⚠️ ' if p_['nsfw'] else ''}{p_['title']} ({p_['ups']}⬆️)")
                    lines_.append(p_['url'])
                await say("\n".join(lines_)[:3800])
            except Exception as ex:
                await say(f"❌ {str(ex)[:120]}")
            return True
        if cmd == "/np":
            try:
                r_ = await asyncio.to_thread(tools.lastfm_np, arg.strip())
                status_ = "🎧 الان در حال پخش" if r_["live"] else "🎵 آخرین آهنگ"
                await say(f"{status_}:\n🎶 {r_['track']}\n🎤 {r_['artist']}"
                          + (f"\n💿 {r_['album']}" if r_["album"] else ""))
            except Exception as ex:
                await say(f"❌ {str(ex)[:120]}")
            return True
        if cmd == "/nsfw":
            photo = await _get_reply_photo(m)
            if not photo:
                await say("🖼 روی یک عکس ریپلای کن و /nsfw بزن.")
                return True
            try:
                score = await asyncio.to_thread(tools.nsfw_detect, photo)
                Path(photo).unlink(missing_ok=True)
                if score >= 70:
                    verdict = "🔴 نامناسب"
                elif score >= 30:
                    verdict = "🟡 مرزی"
                else:
                    verdict = "🟢 امن"
                await say(f"🕵️ تحلیل عکس:\nامتیاز NSFW: {score}%\nنتیجه: {verdict}")
            except Exception as ex:
                Path(photo).unlink(missing_ok=True)
                await say(f"❌ {str(ex)[:120]}")
            return True

        # ── اکشن‌سپوفر / ساعت زنده / انیمیشن ──
        if cmd in ("/action", "/typing"):
            if cmd == "/typing":
                arg = "typing" if not arg.lower() in ("off", "خاموش") else "off"
            low = arg.strip().lower()
            if low in ("off", "خاموش", "off کردن", "بس"):
                had = _stop_action(chat)
                await say("⏹ حالت نمایشی خاموش شد." if had else "حالتی فعال نبود.")
                return True
            t = None
            for k, fa in ACTION_TYPES.items():
                if k in low or fa.split(" ", 1)[1] in low or low in fa:
                    t = k
                    break
            if not t:
                await say("🎭 نوع اکشن؟ یکی از این‌ها:\n" +
                          "\n".join(f"• {fa} ← /action {k}" for k, fa in ACTION_TYPES.items())
                          + "\n\nخاموش: /action off")
                return True
            _stop_action(chat)
            task = asyncio.ensure_future(_action_loop(chat, t))
            ACTIONS[chat] = {"type": t, "task": task}
            await say(f"🎭 {ACTION_TYPES[t]} فعال شد — تا وقتی /action off نزنی می‌ماند!")
            return True
        if cmd == "/clock":
            low = arg.strip().lower()
            if low in ("off", "خاموش"):
                if CLOCK.get("task"):
                    CLOCK["task"].cancel()
                CLOCK["on"] = False
                await say("🕐 ساعت زنده خاموش شد — اسم برمی‌گردد.")
                return True
            if CLOCK.get("on"):
                await say("🕐 ساعت از قبل روشن است.")
                return True
            CLOCK["on"] = True
            CLOCK["orig_name"] = ""
            CLOCK["task"] = asyncio.ensure_future(_clock_loop())
            await say("🕐 ساعت زنده روی اسم پروفایلت روشن شد!\n"
                      "خاموش: /clock off\n"
                      "⚠️ اسم هر دقیقه به‌روز می‌شود.")
            return True
        if cmd == "/animate":
            parts_ = arg.split(None, 1)
            if len(parts_) < 2:
                await say("فرمت: /animate typewriter|wave|pop متن\n"
                          "• typewriter — ماشین‌نویسی ⌨️\n"
                          "• wave — موجی 🌊\n"
                          "• pop — پاپ 🎈")
                return True
            kind, text_ = parts_[0].lower(), parts_[1].strip()[:80]
            mm = await user_client.send_message(chat, "…")
            _own_send_guard(mm)
            import asyncio as _aio
            try:
                if kind == "typewriter":
                    for i in range(1, len(text_) + 1):
                        await mm.edit(text_[:i] + "▌")
                        await _aio.sleep(0.35)
                    await mm.edit(text_)
                elif kind == "wave":
                    em = "🌊"
                    for i in range(12):
                        pos = " " * (i % 8)
                        await mm.edit(f"{em}{pos}{text_}")
                        await _aio.sleep(0.4)
                    await mm.edit(text_)
                elif kind == "pop":
                    sizes = ["▫", "◻", "◼", "◼", "◻", "▫"]
                    for s_ in sizes * 2:
                        await mm.edit(f"{s_} {text_} {s_}")
                        await _aio.sleep(0.45)
                    await mm.edit(text_)
                else:
                    await mm.edit("نوع نامعتبر — typewriter|wave|pop")
            except Exception:
                pass
            return True

        if cmd in ("/spotify", "/sp"):
            if not arg:
                _own_send_guard(await user_client.send_message(
                    chat, "🎵 /spotify لینک-ترک | لینک-پلی‌لیست | اسم-آهنگ\n"
                          "لینک بفرستی مستقیم می‌گیرم؛ اسم بگویی از یوتیوب می‌گیرم."))
                return True
            import tools as _tl
            try:
                kind, sid = _tl.spotify_parse(arg)
                if kind == "track":
                    _own_send_guard(await user_client.send_message(chat, "🎵 دارم می‌گیرم…"))
                    tr = await asyncio.to_thread(_tl.spotify_track, arg)
                    await _sp_send_track(chat, tr["title"], tr["artists"], tr["duration"])
                elif kind in ("playlist", "album"):
                    pl = await asyncio.to_thread(_tl.spotify_list, arg, 50)
                    SP_CACHE[chat] = pl
                    rows = []
                    for i, t in enumerate(pl["tracks"][:10]):
                        label = f"{i+1}. {t['title'][:35]}"
                        if t.get("subtitle"):
                            label += f" — {t['subtitle'].split(' • ')[0][:15]}"
                        rows.append([Button.inline(label, f"sp:{i}")])
                    rows.append([Button.inline("📥 همه ۱۰ تا", b"sp:all")])
                    _own_send_guard(await user_client.send_message(
                        chat, f"🎵 {pl['name'][:50]} — {pl['count']} ترک:\nروی هرکدوم بزن 👇",
                        buttons=rows))
                else:
                    # اسم آهنگ → مستقیم از یوتیوب
                    _own_send_guard(await user_client.send_message(chat, "🎵 دارم می‌گیرم…"))
                    await _sp_send_track(chat, arg, "", 0)
            except Exception as ex:
                _own_send_guard(await user_client.send_message(chat, f"❌ {str(ex)[:150]}"))
            return True

        if cmd == "/js":
            # کد داخل پیام، یا فایل .js ریپلای‌شده
            code = arg
            fpath = None
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.file:
                        fname = (r_.file.name or "")
                        if fname.endswith(".js"):
                            fpath = await r_.download_media(file="/tmp/run.js")
                        else:
                            await say("📄 این فایل .js نیست!")
                            return True
            except Exception:
                fpath = None
            if not code and not fpath:
                await say("🟨 فرمت: /js کد — یا روی فایل .js ریپلای کن و /js بزن\n"
                          "مثال: /js console.log(2+2)")
                return True
            try:
                out_ = await asyncio.to_thread(tools.js_run, code, fpath)
                await say("🟨 " + out_[:3500])
            except Exception as ex:
                if fpath:
                    Path(fpath).unlink(missing_ok=True)
                await say(f"❌ {str(ex)[:200]}")
            return True

        if cmd == "/cmd":
            fpath = None
            code = arg
            try:
                if m.reply_to_msg_id:
                    r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                    if r_ and r_.file:
                        fname = (r_.file.name or "").lower()
                        if fname.endswith((".cmd", ".bat")):
                            fpath = await r_.download_media(file="/tmp/run.cmd")
                        else:
                            await say("📄 این فایل .cmd یا .bat نیست!")
                            return True
            except Exception:
                fpath = None
            if not code and not fpath:
                await say("🖥 فرمت: /cmd دستور | فایل .cmd/.bat را بفرست، ریپلای کن و /cmd بزن\n"
                          "مثال: /cmd echo Salam & set /a 6*7\n"
                          "⚠️ دستورات اسکریپتی اجرا می‌شوند؛ برنامه‌های exe ویندوزی نه")
                return True
            try:
                out_ = await asyncio.to_thread(tools.cmd_run, code, fpath)
                if fpath:
                    Path(fpath).unlink(missing_ok=True)
                await say("🖥\n" + out_[:3500])
            except Exception as ex:
                if fpath:
                    Path(fpath).unlink(missing_ok=True)
                await say(f"❌ {str(ex)[:200]}")
            return True

        if cmd == "/sdto":
            # /sdto [اسم|@یوزر|آیدی] [ثانیه] — روی عکس/ویدیو ریپلای کن
            if not arg:
                await say("🔥 ارسال خودموف — قدم‌به‌قدم:\n\n"
                          "۱) عکس/ویدیو را در هر چت خودت بفرست (یا از قبل هست)\n"
                          "۲) روی آن ریپلای کن\n"
                          "۳) بنویس: /sdto اسم [یا @یوزر یا آیدی]\n\n"
                          "مثال‌ها:\n"
                          "• /sdto ادمک ← یک‌بارمصرف (پس از دیدن می‌پرد)\n"
                          "• /sdto ادمک 10 ← ۱۰ ثانیه‌ای\n"
                          "• /sdto @adamak 5 ← با یوزرنیم\n"
                          "• /sdto 1734367307 ← با آیدی عددی\n\n"
                          "💡 این دستور مستقل از دستیار است — همیشه روشن!")
                return True
            if not m.reply_to_msg_id:
                await say("🖼 اول روی یک عکس/ویدیو ریپلای کن، بعد /sdto بزن.")
                return True
            parts_ = arg.split()
            q_ = parts_[0]
            ttl_ = 1
            if len(parts_) > 1 and parts_[1].isdigit():
                ttl_ = max(1, min(int(parts_[1]), 3600))
            target = await _resolve_sd_target(q_)
            if not target:
                await _sd_notify(f"❌ «{q_}» پیدا نشد — اسم دقیق یا @یوزرنیم بگو.")
                return True
            try:
                r_ = await user_client.get_messages(chat, ids=m.reply_to_msg_id)
                if not (r_ and (getattr(r_, "photo", None) or getattr(r_, "video", None)
                                or getattr(r_, "document", None))):
                    await _sd_notify("🖼 این پیام عکس/ویدیو نیست (خودموف فقط برای عکس و ویدیو).")
                    return True
                path_ = await r_.download_media(file="/tmp/sd_media")
                if not path_:
                    await _sd_notify("❌ دانلود مدیا نشد.")
                    return True
                await _sd_send_media(target["id"], path_, ttl_)
                Path(path_).unlink(missing_ok=True)
                # تأیید فقط به چت خصوصی ادمین — چت هدف کاملاً تمیز می‌ماند
                await _sd_notify(f"🔥 خودموف برای «{target['name']}» رفت"
                                 + (" — یک‌بارمصرف" if ttl_ <= 1 else f" — {ttl_} ثانیه") + ".")
            except Exception as ex:
                await _sd_notify(f"❌ خودموف: {str(ex)[:120]}")
            return True

        # ── ابزارها ──
        if not TOOLS_OK:
            await say("⚠️ ماژول tools.py آپلود نشده.")
            return True
        if cmd == "/tr":
            body = arg or ""
            if not body:
                await say("فرمت: /tr متن")
                return True
            to = "en" if re.search(r"[\u0600-\u06FF]", body) else "fa"
            if body.startswith("en:") or body.startswith("fa:") or body.startswith("ar:"):
                to, body = body[:2], body[3:].strip()
            r_ = await asyncio.to_thread(tools.translate, body, to)
            await say(f"🌐 ترجمه به {r_['to_name']}:\n\n{r_['text'][:2500]}")
            return True
        if cmd == "/tts":
            if not arg:
                await say("فرمت: /tts متن")
                return True
            p_ = await asyncio.to_thread(tools.tts, arg)
            await fsend(p_, voice=True)
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/azan":
            r_ = await asyncio.to_thread(tools.azan, arg or None)
            await say(f"🕌 اوقات شرعی {r_['city']}\n" + "\n".join(r_["lines"])
                      + (f"\n📅 {r_['hijri']}" if r_["hijri"] else ""))
            return True
        if cmd == "/weather":
            r_ = await asyncio.to_thread(tools.weather, arg or None)
            await say(
                f"🌤 آب‌وهوای {r_['city']}\n{r_['desc']}\n"
                f"🌡 الان: {r_['temp']}° (حس {r_['feels']}°)\n"
                f"📈 امروز: {r_['min']}° تا {r_['max']}°\n"
                f"💧 رطوبت: {r_['humidity']}% | 💨 باد: {r_['wind']}km/h\n"
                f"☔ احتمال بارش: {r_['rain_prob']}%")
            return True
        if cmd == "/lyrics":
            ly = await asyncio.to_thread(tools.lyrics, arg, "")
            await say(f"🎵 متن آهنگ:\n\n{ly[:3800]}")
            return True
        if cmd == "/speed":
            await say("⚡ دارم می‌سنجم…")
            r_ = await asyncio.to_thread(tools.speedtest)
            await say(f"⚡ سرعت اینترنت سرور:\n⬇️ دانلود: {r_['down_mbps']} Mbps\n⏱ پینگ: {r_['ping_ms']}ms")
            return True
        if cmd == "/ocr":
            photo = await _get_reply_photo(m)
            if not photo:
                await say("🖼 روی یک عکس ریپلای کن و /ocr بزن.")
                return True
            t_ = await asyncio.to_thread(tools.ocr, photo)
            Path(photo).unlink(missing_ok=True)
            await say(f"📝 متن داخل عکس:\n\n{t_[:2000]}")
            return True
        if cmd == "/rmbg":
            photo = await _get_reply_photo(m)
            if not photo:
                await say("🖼 روی یک عکس ریپلای کن و /rmbg بزن.")
                return True
            p_ = await asyncio.to_thread(tools.removebg, photo)
            Path(photo).unlink(missing_ok=True)
            await fsend(p_, caption="✂️ پس‌زمینه حذف شد")
            Path(p_).unlink(missing_ok=True)
            return True
        if cmd == "/meme":
            photo = await _get_reply_photo(m)
            if not photo:
                await say("🖼 روی یک عکس ریپلای کن و /meme متن بالا | متن پایین بزن.")
                return True
            top_, bottom_ = (" | " in arg) and arg.split(" | ", 1) or (arg, "")
            p_ = await asyncio.to_thread(tools.meme, photo, top_, bottom_)
            Path(photo).unlink(missing_ok=True)
            await fsend(p_, caption="😂")
            Path(p_).unlink(missing_ok=True)
            return True
        return False
    except Exception as ex:
        try:
            _own_send_guard(await user_client.send_message(
                getattr(m, "chat_id", None), f"❌ {str(ex)[:150]}"))
        except Exception:
            pass
        return True


async def _process_own_message(m):
    """یک پیام خروجیِ خود کاربر → گروه AI (گفتگوی آزاد) یا /m در هر چت"""
    try:
        if m is None or not getattr(m, "out", False):
            return
        mid = getattr(m, "id", None)
        if mid in _OUT_SEEN:
            return
        _OUT_SEEN.append(mid)
        chat_id = m.chat_id
        text = _msg_text(m)
        # قطع‌کننده‌ی حلقه‌ی خطا: پیام‌های خطای خودمان هرگز پردازش نمی‌شوند
        if text.startswith("🧠 ❌") or text.startswith("🧠 🎙"):
            return

        # ── گروه AI: گفتگوی آزاد بدون دستور ──
        if AI_GROUP.get("id") and chat_id == AI_GROUP["id"]:
            if m.voice:
                await _aigroup_voice_msg(m)
                return
            if text and not text.startswith("/") and not text.startswith("!"):
                await _aigroup_reply_msg(m, text)
            return

        # ── تأیید برودکست ──
        bp = BROADCAST_PENDING.get(chat_id)
        if bp and time.time() - bp.get("ts", 0) < 300:
            low = _fa_norm(text)
            if low in YES_WORDS:
                BROADCAST_PENDING.pop(chat_id, None)
                sent_ok, sent_err = 0, 0
                _own_send_guard(await user_client.send_message(
                    chat_id, "📣 شروع ارسال انبوه…"))
                async for d_ in user_client.iter_dialogs(limit=200):
                    try:
                        if d_.is_user and getattr(d_.entity, "bot", False):
                            continue
                        mm = await user_client.send_message(d_.entity, bp["text"])
                        _own_send_guard(mm)
                        sent_ok += 1
                        await asyncio.sleep(0.7)
                    except Exception:
                        sent_err += 1
                        continue
                _own_send_guard(await user_client.send_message(
                    chat_id, f"📣 تمام: ✅ {sent_ok} چت | ❌ {sent_err} ناموفق"))
                return
            if low in NO_WORDS:
                BROADCAST_PENDING.pop(chat_id, None)
                _own_send_guard(await user_client.send_message(chat_id, "🏳 لغو شد."))
                return
        # ── AFK: اولین پیام خود کاربر = برگشتن ──
        if AFK.get("on") and not text.startswith("/afk"):
            AFK.update(on=False)
            _save_afk()
            mins = int((time.time() - (AFK.get("since") or time.time())) / 60)
            _own_send_guard(await user_client.send_message(
                chat_id, f"🏃 برگشتی! ({mins} دقیقه نبودی) — حالت AFK خاموش شد."))
            return
        # ─── /m است یا دستور یوزربات؟ (ترتیب مهم: /meme دستور است نه /m!) ───
        is_m = text == "/m" or text.startswith("/m ") or text.startswith("/m\n")
        # ── دستورهای یوزربات (سبک CatUserBot) — قبل از /m ──
        if text.startswith("/") and not is_m:
            handled = await _ub_command(m, text)
            if handled:
                return
        # ── /m در هر چت دیگر ──
        voice_m = text == "/m" and m.voice      # ویس با کپشن /m
        if not is_m:
            return
        # /m بدون دستیار خاموش است (فقط /sdto از بالای همین تابع می‌گذرد)
        if not AGENT_ENABLED:
            return
        if BOT_SELF_ID and m.is_private and chat_id == BOT_SELF_ID:
            return          # داخل چت خود ربات → مسیر اصلی
        if not (user_client and user_client.is_connected()):
            _own_send_guard(await user_client.send_message(
                chat_id, "🧠 ❌ اکانت‌خوان قطع است — /login"))
            return
        try:
            await m.delete()
        except Exception:
            pass
        req = text[2:].strip()
        if voice_m or (not req and m.voice):
            # ویس با /m → تبدیل به متن
            path = await m.download_media(file="/tmp/ub_voice.ogg")
            req2 = await asyncio.to_thread(aichat.transcribe, path) if path else None
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass
            if not req2:
                _own_send_guard(await user_client.send_message(
                    chat_id, "🧠 نتونستم ویس رو بخونم — کلید Groq لازم است"))
                return
            _own_send_guard(await user_client.send_message(chat_id, f"🎙 {req2[:300]}"))
            req = req2
        if not req:
            return

        async def send(t):
            _own_send_guard(await user_client.send_message(chat_id, str(t)[:3800]))

        async def send_file_(p, caption=None, voice=False):
            kw = {}
            ps = str(p)
            if ps.endswith(".webm") or ps.endswith(".webp"):
                kw["attributes"] = _sticker_attrs(ps)   # استیکر واقعی
            _own_send_guard(await user_client.send_file(
                chat_id, p, caption=caption,
                voice_note=True if voice else None, **kw))

        # عکس/مدیای ریپلای‌شده + فرستنده
        photo = None
        media = None
        reply_user = None
        try:
            if m.reply_to_msg_id:
                r_ = await user_client.get_messages(chat_id, ids=m.reply_to_msg_id)
                if r_:
                    reply_user = r_.sender_id
                    if getattr(r_, "photo", None):
                        photo = await r_.download_media(file="/tmp/ub_photo")
                    elif r_.file:
                        media = await r_.download_media(file="/tmp/ub_media")
        except Exception:
            photo = None

        await _magent_pipeline(chat_id, req, send, None,
                               send_file=send_file_, photo=photo, real_chat=True,
                               media=media, reply_user=reply_user)
    except Exception as ex:
        try:
            _own_send_guard(await user_client.send_message(
                getattr(m, "chat_id", None), f"🧠 ❌ {str(ex)[:200]}"))
        except Exception:
            pass


async def _aigroup_voice_msg(m):
    """ویس در گروه AI → متن → اجرا"""
    try:
        if not (user_client and user_client.is_connected()):
            return
        path = await m.download_media(file="/tmp/aig_voice.ogg")
        if not path:
            return
        text = await asyncio.to_thread(aichat.transcribe, path)
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
        if not text:
            _own_send_guard(await user_client.send_message(
                m.chat_id, "🧠 نتونستم ویس رو بخونم — کلید Groq لازم است",
                reply_to=m.id))
            return
        _own_send_guard(await user_client.send_message(
            m.chat_id, f"🎙 {text[:300]}", reply_to=m.id))
        await _aigroup_reply_msg(m, text)
    except Exception:
        pass


async def _aigroup_reply_msg(m, text):
    """پاسخ آزاد در گروه AI — با حافظه‌ی بلند"""
    try:
        if not (user_client and user_client.is_connected()):
            return

        async def send(t):
            _own_send_guard(await user_client.send_message(
                m.chat_id, str(t)[:3800], reply_to=m.id))

        async def send_file_(p, caption=None, voice=False):
            kw = {}
            ps = str(p)
            if ps.endswith(".webm") or ps.endswith(".webp"):
                kw["attributes"] = _sticker_attrs(ps)   # استیکر واقعی
            _own_send_guard(await user_client.send_file(
                m.chat_id, p, caption=caption,
                voice_note=True if voice else None, **kw))

        photo = None
        media = None
        reply_user = None
        try:
            if m.reply_to_msg_id:
                r_ = await user_client.get_messages(m.chat_id, ids=m.reply_to_msg_id)
                if r_:
                    reply_user = r_.sender_id
                    if getattr(r_, "photo", None):
                        photo = await r_.download_media(file="/tmp/aig_photo")
                    elif r_ and r_.file:
                        media = await r_.download_media(file="/tmp/aig_media")
        except Exception:
            photo = None

        await _magent_pipeline(m.chat_id, text, send, None, hist_keep=24,
                               send_file=send_file_, photo=photo, real_chat=True,
                               media=media, reply_user=reply_user)
    except Exception as ex:
        try:
            _own_send_guard(await user_client.send_message(
                m.chat_id, f"🧠 ❌ {str(ex)[:200]}", reply_to=m.id))
        except Exception:
            pass


async def _magent_ub_event(event):
    """مسیر push — اگر کار کند فوری است (ضدتکرار با poll)"""
    try:
        if not event.out:
            return
        await _process_own_message(event.message)
    except Exception:
        pass


async def _secretary_reply(m):
    """منشی: به پیام ورودیِ یک شخص، به جای کاربر جواب بده"""
    try:
        if m.id in _IN_SEEN or getattr(m, "out", False):
            return
        _IN_SEEN.append(m.id)
        if not m.message or m.message.startswith("/"):
            return
        info = AUTO_REPLY.get(str(m.chat_id))
        if not info:
            return
        name = info.get("name") or "او"
        ctx = _ctx(f"sec_{m.chat_id}")
        history = (ctx.get("history") or [])[-12:]
        system = (f"تو منشی هوشمند صاحب حساب تلگرام هستی. «{name}» پیام داده. "
                  "به جای صاحب حساب، محترم و کوتاه و محاوره‌ای جواب بده. "
                  "اگر سوال شخصی درباره‌ی صاحب حساب بود که نمی‌دانی، بگو الان می‌پرسم و بعداً جواب می‌ده. "
                  "فقط یک پیام کوتاه — آخرش امضا نگذار.")
        try:
            ans = await asyncio.to_thread(aichat.ask_raw, system,
                                          f"«{name}» گفت: {m.message[:800]}", history, 300)
        except Exception:
            return
        if not ans:
            return
        msg = await user_client.send_message(m.chat_id, ans[:2000])
        _own_send_guard(msg)
        ctx["history"] = (ctx.get("history") or [])[-12:] + [
            {"r": "user", "t": f"{name}: {m.message[:200]}"},
            {"r": "assistant", "t": ans[:200]}]
        _mag_save_ctx()
    except Exception:
        pass


async def _userbot_own_poll():
    """پمپ پیام‌ها — هر ۴ ثانیه: /m و گروه AI (خروجی) + منشی (ورودی)"""
    from datetime import timezone as _tz
    while True:
        try:
            await asyncio.sleep(4)
            if not user_client or not user_client.is_connected():
                continue
            # ── منشی: پیام‌های ورودیِ اهداف فعال ──
            if AUTO_REPLY:
                for pid, info in list(AUTO_REPLY.items()):
                    try:
                        msgs = await user_client.get_messages(int(pid), limit=3)
                        for mm in msgs:
                            if (mm and not getattr(mm, "out", False)
                                    and mm.id not in _IN_SEEN and mm.message):
                                await _secretary_reply(mm)
                    except Exception:
                        continue
            # ── AFK و فیلترها: پیام‌های ورودی پیوی ──
            if (AFK.get("on") or FILTERS):
                try:
                    async for d in user_client.iter_dialogs(limit=25):
                        try:
                            if not d.is_user:
                                continue
                            top = d.message
                            if (top is None or getattr(top, "out", False)
                                    or not top.id or not (top.message or "").strip()):
                                continue
                            if top.id in _FILTER_SEEN:
                                continue
                            _FILTER_SEEN.append(top.id)
                            # فقط پیام‌های تازه (۳ دقیقه اخیر)
                            from datetime import datetime as _dt2
                            td2 = top.date
                            if td2:
                                if td2.tzinfo is None:
                                    td2 = td2.replace(tzinfo=_tz.utc)
                                if (_dt2.now(_tz.utc) - td2).total_seconds() > 180:
                                    continue
                            txt_in = top.message.strip()
                            if AFK.get("on") and top.id not in AFK_REPLIED:
                                AFK_REPLIED.append(top.id)
                                reason = f" — {AFK['reason']}" if AFK.get("reason") else ""
                                try:
                                    _own_send_guard(await user_client.send_message(
                                        top.chat_id,
                                        f"🏃 الان نیستم{reason}.\n"
                                        "به محض برگشتم جوابت را می‌دهم. 🙏"))
                                except Exception:
                                    pass
                                continue
                            for kw_, rep_ in list(FILTERS.items()):
                                if kw_ in txt_in:
                                    try:
                                        _own_send_guard(await user_client.send_message(
                                            top.chat_id, rep_))
                                    except Exception:
                                        pass
                                    break
                        except Exception:
                            continue
                except Exception:
                    pass
            async for d in user_client.iter_dialogs(limit=12):
                try:
                    top = d.message
                    if top is None or not getattr(top, "out", False):
                        continue
                    if top.id in _OUT_SEEN:
                        continue
                    # فقط پیام‌های تازه — قدیمی‌ها (قبل از روشن شدن) رد
                    if top.date:
                        from datetime import datetime as _dt
                        td = top.date if top.date.tzinfo else top.date.replace(tzinfo=_tz.utc)
                        if (_dt.now(_tz.utc) - td).total_seconds() > 180:
                            _OUT_SEEN.append(top.id)
                            continue
                    # همه‌ی پیام‌های خروجی دیده‌نشده‌ی این چت
                    msgs = await user_client.get_messages(d.entity, limit=4)
                    for mm in sorted(
                            [x for x in msgs if x and getattr(x, "out", False)],
                            key=lambda x: x.id):
                        if mm.id not in _OUT_SEEN:
                            await _process_own_message(mm)
                except Exception:
                    continue
        except Exception:
            pass


async def _welcome_loop():
    """خوش‌آمدگویی خودکار اعضای جدید گروه‌های فعال — هر ۴۵ ثانیه"""
    from telethon.tl.types import MessageService
    while True:
        try:
            await asyncio.sleep(45)
            if not WELCOME_GROUPS or not user_client or not user_client.is_connected():
                continue
            for cid, title in list(WELCOME_GROUPS.items()):
                try:
                    msgs = await user_client.get_messages(int(cid), limit=8)
                    for m_ in msgs:
                        if not (m_ and isinstance(m_, MessageService)):
                            continue
                        users_ = getattr(m_.action, "users", None) if m_.action else None
                        if not users_ or m_.id in _WELCOME_SEEN:
                            continue
                        _WELCOME_SEEN.append(m_.id)
                        names = []
                        for uid in users_[:5]:
                            try:
                                ent = await user_client.get_entity(uid)
                                nm = (getattr(ent, "first_name", "") or "کاربر").strip()
                                if uid == (await user_client.get_me()).id:
                                    continue       # خود کاربر
                                names.append(nm)
                            except Exception:
                                continue
                        if not names:
                            continue
                        _own_send_guard(await user_client.send_message(
                            int(cid),
                            f"👋 خوش اومدید {', '.join(names)}! به «{title}» 🎉"))
                except Exception:
                    continue
        except Exception:
            pass


def _setup_magent_handler():
    """فعال‌سازی /m همه‌جا: push + poll — هر دو با ضدتکرار"""
    global _OWN_POLL_TASK
    if (not AGENT_ENABLED and not SDSEND_ENABLED) or user_client is None:
        return
    for h, _ev in user_client.list_event_handlers():
        if getattr(h, "__name__", "") == "_magent_ub_event":
            break
    else:
        user_client.add_event_handler(_magent_ub_event, events.NewMessage(outgoing=True))
    if _OWN_POLL_TASK is None or _OWN_POLL_TASK.done():
        _OWN_POLL_TASK = asyncio.ensure_future(_userbot_own_poll())
        print("🧠 دستیار /m فعال شد (push + poll) — همه‌جا جواب می‌دهد", flush=True)


# ═══════════════ 👥 گروه گفتگو با هوش مصنوعی (/aigroup) ═══════════════

AI_GROUP_FILE = Path("/tmp/aigroup.json")
AI_GROUP = {"id": None, "title": ""}
_AIGROUP_SENT = deque(maxlen=100)     # آیدی پیام‌های خودِ دستیار — ضدحلقه


def _load_aigroup():
    global AI_GROUP
    try:
        d = json.loads(AI_GROUP_FILE.read_text())
        if d.get("id"):
            AI_GROUP = {"id": d["id"], "title": d.get("title") or ""}
    except Exception:
        pass


def _save_aigroup():
    try:
        AI_GROUP_FILE.write_text(json.dumps(AI_GROUP, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


@client.on(events.NewMessage(pattern=r"^/aigroup(?:\s+(\S+))?$"))
async def cmd_aigroup(e):
    """👥 ساخت/فعال‌سازی گروه گفتگوی اختصاصی با هوش مصنوعی"""
    if e.sender_id not in ADMIN_IDS:
        return
    if not AGENT_ENABLED:
        return          # خاموش
    arg = (e.pattern_match.group(1) or "").lower()
    if arg == "status":
        st = "✅ فعال" if AI_GROUP.get("id") else "خاموش"
        await e.reply(f"👥 گروه هوش مصنوعی: {st}"
                      + (f"\n🆔 {AI_GROUP['id']} | {AI_GROUP.get('title') or ''}" if AI_GROUP.get("id") else "")
                      + "\n\nبرای ساخت: /aigroup (تو چت خصوصی ربات)")
        return
    if not (user_client and user_client.is_connected()):
        await e.reply("👁 اول /login بزن — گروه با اکانت خودت ساخته می‌شود.")
        return
    if arg == "off":
        AI_GROUP["id"] = None
        _save_aigroup()
        await e.reply("👥 گروه هوش مصنوعی خاموش شد.")
        return
    if e.is_private:
        # ساخت گروه جدید با اکانت خود کاربر
        st = await e.reply("⏳ دارم گروه اختصاصی می‌سازم…")
        try:
            from telethon.tl.functions.channels import CreateChannelRequest
            res = await user_client(CreateChannelRequest(
                title="🧠 هوش مصنوعی",
                about="گفتگوی اختصاصی با دستیار هوشمند — بدون پیشوند، فقط بنویس",
                megagroup=True))
            ch = next((c for c in (res.chats or []) if getattr(c, "id", None)), None)
            if ch is None:
                await st.edit("❌ گروه ساخته نشد.")
                return
            AI_GROUP.update(id=ch.id, title="🧠 هوش مصنوعی")
            _save_aigroup()
            await user_client.send_message(
                ch.id,
                "🧠 سلام! این گروه گفتگوی اختصاصیته.\n\n"
                "• هرچی بنویسی جواب می‌دم — بدون /m و بدون دستور\n"
                "• حافظه دارم — موضوع قبلی یادم می‌مونه\n"
                "• کارهایی مثل «به امین بگو بیاد» هم انجام می‌دم\n"
                "• اگر جواب نخواستی، اول پیامت ! بذار")
            await st.edit(
                f"✅ گروه ساخته شد!\n\n"
                f"🔗 برو به: https://t.me/c/{ch.id}\n\n"
                "همون‌جا هرچی بنویسی، با اکانت خودت جواب می‌گیری.")
        except Exception as ex:
            await st.edit(f"❌ {str(ex)[:200]}")
        return
    # داخل گروه → همین گروه فعال شود
    AI_GROUP.update(id=e.chat_id, title=(getattr(e.chat, "title", None) or "گروه"))
    _save_aigroup()
    await e.reply("✅ این گروه شد گروه گفتگو با هوش مصنوعی — حالا هرچی بنویسی جواب می‌گیری.")




@client.on(events.CallbackQuery(pattern=r"^ai:"))
async def on_ai_cb(e):
    if e.data == b"ai:end":
        flow.pop(e.chat_id, None)
        try:
            await e.answer()
            await e.edit("💬 گفتگو تمام شد ✅", buttons=None)
        except Exception:
            pass
        return
    await e.answer()


async def handle_ai_text(e):
    st = flow.get(e.chat_id) or {}
    st["errs"] = (st.get("errs") or 0)
    t = (e.text or "").strip()
    if t in ("پایان", "/end", "لغو"):
        flow.pop(e.chat_id, None)
        await e.reply("💬 گفتگو تمام شد ✅", buttons=kb_for(e.chat_id))
        return
    if t.startswith("/"):
        await e.reply("دستور معتبر نیست — یا سوال بپرس یا بنویس: پایان")
        return
    status = await e.reply("🧠 دارم فکر می‌کنم…")
    stop_btn = [[Button.inline("✖ پایان گفتگو", b"ai:end")]]
    try:
        ans = await asyncio.to_thread(aichat.ask, t, st.get("history"))
        st["ts"] = time.time()
        st.setdefault("history", []).append({"r": "user", "t": t})
        st["history"].append({"r": "assistant", "t": ans[:800]})
        st["history"] = st["history"][-8:]
        try:
            await status.delete()
        except Exception:
            pass
        # آخرین تکه پاسخ، دکمه‌ی پایان را دارد؛ گفتگو باز می‌ماند
        chunks = [ans[i:i + 3800] for i in range(0, len(ans), 3800)] or ["…"]
        for ci, ch in enumerate(chunks):
            btns = stop_btn if ci == len(chunks) - 1 else None
            await client.send_message(e.chat_id, ch, buttons=btns)
    except Exception as ex:
        # گفتجو باز می‌ماند — فقط همین سوال خطا خورد
        st["errs"] = (st.get("errs") or 0) + 1
        try:
            await status.edit(f"❌ {str(ex)[:300]}",
                              buttons=stop_btn)
        except Exception:
            pass


YT_FLOW = {}   # chat_id → ts (فعال بودن بخش یوتیوب تا ۱۰ دقیقه)


INSTA_RE = re.compile(
    r'https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)',
    re.IGNORECASE)


@client.on(events.NewMessage(pattern=r"^📸 اینستاگرام$"))
async def btn_insta(e):
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "yt"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    if not INSTA_OK:
        await e.reply("⚠️ ماژول insta.py آپلود نشده — همراه بقیه‌ی فایل‌ها آپلودش کن.")
        return
    flow[e.chat_id] = {"kind": "insta", "step": "wait", "ts": time.time()}
    await e.reply(
        "📸 بخش اینستاگرام\n\n"
        "• لینک پست / ریلز → ویدیو/عکسش می‌آید\n"
        "• @آیدی یا اسم → اطلاعات پروفایل\n"
        "• 🔍 سرچ <اسم> → یوزرنیم‌های اینستا پیدا می‌شود\n"
        "• #تگ → اکانت‌های مرتبط با تگ\n\n"
        "پایان: «پایان»")


async def _insta_profile_flow(chat_id, uname, status):
    """نمایش پروفایل — مشترک بین حالت‌ها (لینک مستقیم، سرچ، دکمه)"""
    try:
        d = await asyncio.wait_for(insta.profile(uname), timeout=70)
        if not d.get("ok"):
            if d.get("reason") == "not_found":
                await status.edit(f"❌ @{uname} وجود ندارد.")
            else:
                nm = uname
                try:
                    if user_client and user_client.is_connected():
                        ent = await user_client.get_entity("https://instagram.com/" + uname)
                        nm = ((getattr(ent, "first_name", "") or "") + " " +
                              (getattr(ent, "last_name", "") or "")).strip() or uname
                except Exception:
                    pass
                await status.edit(
                    f"👤 {nm} (@{uname})\n\n"
                    f"(اینستا آمار را برای سرور ما مخفی کرد)")
            return
        name = d.get("name") or uname
        flw = d.get("followers")
        flg = d.get("following")
        posts = d.get("posts")
        priv = "🔒 پرایوت" if d.get("private") else "🌍 عمومی"
        ver = " ✔️" if d.get("verified") else ""
        bio = (f"\n📝 {d['bio']}" if d.get("bio") else "")
        stat_txt = ""
        if flw is not None:
            fmt = lambda v: (f"{v:,}" if isinstance(v, int) else str(v))
            stat_txt = (f"\n👥 دنبال‌کننده: {fmt(flw)}\n➡️ دنبال‌شده: {fmt(flg)}"
                        f"\n🖼 پست‌ها: {fmt(posts)}")
        await status.edit(
            f"👤 {name}{ver} (@{uname}) — {priv}{bio}{stat_txt}")
        pic_url = d.get("pic")
        pp = None
        if pic_url:
            try:
                pp = await asyncio.wait_for(insta.download_pic(pic_url), timeout=30)
            except Exception:
                pp = None
        if not pp:
            try:
                if user_client and user_client.is_connected():
                    pp = await user_client.download_profile_photo(
                        "https://instagram.com/" + uname, file="/tmp/ig_pp.jpg")
            except Exception:
                pp = None
        if pp and Path(pp).exists() and Path(pp).stat().st_size > 1000:
            await client.send_file(chat_id, pp, caption=f"👤 {name}")
            Path(pp).unlink(missing_ok=True)
    except Exception as ex:
        await status.edit(f"❌ {str(ex)[:120]} (اینستا ممکن است محدود کرده باشد)")


async def handle_insta_text(e):
    t = (e.text or "").strip()
    # دکمه‌های کیبورد هرگز کوئری سرچ نیستند!
    if t in (BTN_HELP, BTN_STATUS, BTN_ADMIN, BTN_BARGH, BTN_SUPPORT,
             BTN_SELFDES, BTN_AI, BTN_YT, BTN_FINDER, BTN_SUB, BTN_INSTA, BTN_ANTEN,
             BTN_STT, BTN_LINK, BTN_DL, BTN_TAG):
        await e.reply(
            "📸 بخش اینستاگرام\n\n"
            "• لینک پست / ریلز → ویدیو/عکسش می‌آید\n"
            "• @آیدی یا اسم → اطلاعات پروفایل\n"
            "• «سرچ <اسم>» → یوزرنیم‌های اینستا\n"
            "• #تگ → اکانت‌های مرتبط\n\n"
            "پایان: «پایان»")
        return
    if t in ("پایان", "/end", "لغو"):
        flow.pop(e.chat_id, None)
        await e.reply("📸 بسته شد ✅", buttons=kb_for(e.chat_id))
        return
    if t.startswith("/"):
        await e.reply("لینک، اسم یا «سرچ ...» بفرست — یا «پایان»")
        return
    if not INSTA_OK:
        await e.reply("⚠️ ماژول insta.py آپلود نشده.")
        return
    # ─── لینک پست/ریلز ───
    m = INSTA_RE.search(t)
    if m:
        status = await e.reply("⏳ دارم دانلود می‌کنم…")
        try:
            files, info, method = await asyncio.wait_for(
                insta.download_media(m.group(0)), timeout=160)
            sent = 0
            cap_base = f"📸 {(info.get('title') or 'پست اینستا')[:80]}"
            for pth in files:
                p = Path(pth)
                if not p.exists():
                    continue
                if p.stat().st_size > 48 * 1024 * 1024:
                    p.unlink(missing_ok=True)
                    continue
                cap = cap_base if sent == 0 else f"{cap_base} ({sent + 1})"
                try:
                    if p.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv"):
                        await client.send_file(e.chat_id, str(p), caption=cap,
                                               supports_streaming=True)
                    else:
                        await client.send_file(e.chat_id, str(p), caption=cap)
                    sent += 1
                finally:
                    p.unlink(missing_ok=True)
            if sent:
                try:
                    await status.delete()
                except Exception:
                    pass
            else:
                await status.edit("❌ نتونستم بگیرمش (شاید پرایوته یا حذف شده).")
        except Exception as ex:
            s_ = str(ex).lower()
            if "login" in s_ or "rate" in s_ or "401" in s_ or "429" in s_:
                await status.edit("😖 اینستا فعلاً محدود کرده — چند دقیقه بعد دوباره.")
            else:
                await status.edit(f"❌ {str(ex)[:150]}")
        return
    # ─── سرچ: «سرچ X» / «جستجوی X» / متن ساده بدون @ ───
    mq = re.match(r"^(?:سرچ|جستجو|جستجوی|search)\s+(.+)$", t, re.I)
    is_tag = t.startswith("#")
    if mq or is_tag or not t.startswith("@"):
        query = mq.group(1).strip() if mq else (t.lstrip("#").strip() if is_tag else t.strip())
        if not query or query.startswith("http"):
            await e.reply("فرمت درست نیست — لینک، @آیدی، «سرچ <اسم>» یا #تگ.")
            return
        status = await e.reply(f"🔍 دارم دنبال «{query[:40]}» می‌گردم…")
        try:
            users = await asyncio.to_thread(
                insta.tag_search if is_tag else insta.ig_search, query, 8)
            users = [u for u in users if u.lower() != "instagram"][:8]
            if not users:
                await status.edit(
                    f"😕 برای «{query[:40]}» چیزی پیدا نشد.\n"
                    "💡 اسم را انگلیسی بنویس (مثل: سرچ drzahra entezam)")
                return
            rows = [[Button.inline(f"👤 @{u}", f"i:s:{u}".encode())]
                    for u in users[:8]]
            tag_note = (f"\n🔗 صفحه‌ی تگ: instagram.com/explore/tags/{query.replace('#','')}/"
                        if is_tag else "")
            await status.edit(
                f"🔍 نتایج برای «{query[:40]}» — روی هرکدوم بزن:{tag_note}",
                buttons=rows)
        except Exception as ex:
            await status.edit(f"❌ جستجو ناموفق: {str(ex)[:120]}")
        return
    # ─── @آیدی مستقیم ───
    mu = re.match(r"^@?([A-Za-z0-9_.]{2,30})$", t)
    if not mu:
        await e.reply("فرمت درست نیست — لینک، @آیدی، «سرچ <اسم>» یا #تگ.")
        return
    uname = mu.group(1)
    status = await e.reply(f"⏳ پروفایل @{uname}…")
    await _insta_profile_flow(e.chat_id, uname, status)


@client.on(events.CallbackQuery(pattern=r"^i:s:[A-Za-z0-9_.]{2,30}$"))
async def on_insta_search_cb(e):
    """نتیجه‌ی سرچ اینستا → نمایش پروفایل همان یوزرنیم"""
    await note_user(e)
    uname = e.data.decode()[4:]
    await e.answer()
    status = await client.send_message(e.chat_id, f"⏳ پروفایل @{uname}…")
    await _insta_profile_flow(e.chat_id, uname, status)
    raise events.StopPropagation()   # دکمه‌های فشرده‌سازی (i:) نباید اجرا شوند


@client.on(events.NewMessage(pattern=r"^🎬 یوتیوب$"))
async def btn_yt(e):
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "yt"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    if not YT_OK:
        await e.reply("⚠️ ماژول یوتیوب آماده نیست.")
        return
    YT_FLOW[e.chat_id] = True
    ck = "✅ کوکی فعال" if ytube.has_cookies() else "⚠️ کوکی نامعتبر (فایل cookies.txt تازه بفرست)"
    from telethon import Button as _B
    kb_room = [[_B.text("✖ خروج از یوتیوب")]]
    await e.reply(
        "🎬 اتاق یوتیوب\n\n"
        "• لینک ویدیو بفرست → کیفیت‌ها میاد\n"
        "• یا اسم چیزی بنویس → سرچ می‌کنم\n"
        f"🔑 {ck}\n\n"
        "تا وقتی «✖ خروج» نزنی، همین اتاقی — بقیه‌ی بخش‌ها موقتی قفلند.",
        buttons=kb_room)


def _yt_quality_kb(vid):
    return [
        [Button.inline("🎬 360p سبک", f"y:d:{vid}:360".encode()),
         Button.inline("🎬 480p", f"y:d:{vid}:480".encode())],
        [Button.inline("🎬 720p", f"y:d:{vid}:720".encode()),
         Button.inline("🎵 فقط صدا", f"y:d:{vid}:audio".encode())],
    ]


@client.on(events.CallbackQuery(pattern=r"^y:"))
async def on_yt_cb(e):
    await note_user(e)
    data = e.data.decode()[2:]
    chat = e.chat_id
    if data == "close":
        YT_FLOW.pop(chat, None)
        try:
            await e.answer()
            await e.edit("🎬 بخش یوتیوب بسته شد ✅", buttons=None)
            await client.send_message(chat, "به منوی اصلی برگشتی 👇",
                                      reply_markup=kb_for(chat))
        except Exception:
            pass
        return
    if not YT_OK:
        await e.answer("ماژول یوتیوب نیست", alert=True)
        return
    if data.startswith("s:"):
        vid = data[2:]
        await e.answer()
        try:
            await e.edit("⏳ اطلاعات ویدیو…")
        except Exception:
            pass
        stage = {"txt": "شروع"}

        def _st(s_):
            stage["txt"] = s_

        task = asyncio.ensure_future(
            ytube.fetch_info(f"https://www.youtube.com/watch?v={vid}", progress=_st))
        t0 = time.time()
        info = None
        try:
            while True:
                _done, _ = await asyncio.wait({task}, timeout=7)
                if _done:
                    info = task.result()
                    break
                el = int(time.time() - t0)
                try:
                    await e.edit(f"⏳ اطلاعات ویدیو… {el}ث\n🔄 {stage['txt']}")
                except Exception:
                    pass
                if el >= 63:
                    task.cancel()
                    raise TimeoutError("طول کشید")
            title = (info.get("title") or "?")[:60]
            m, s = divmod(int(info.get("duration") or 0), 60)
            await client.send_message(
                chat, f"🎬 {title}\n⏱ {m}:{s:02d}\n\nکیفیت رو انتخاب کن:",
                buttons=_yt_quality_kb(vid))
        except Exception:
            # اطلاعات نیامد ولی دانلود مسیر خودش را دارد — دکمه‌ها را بده
            try:
                await client.send_message(
                    chat,
                    "⚠️ اطلاعات ویدیو کند آمد — ولی می‌تونی مستقیم دانلود کنی:",
                    buttons=_yt_quality_kb(vid))
            except Exception:
                pass
        return
    if data.startswith("d:"):
        vid, mode = data[2:].rsplit(":", 1)
        await e.answer()
        try:
            await e.edit("⏳ دارم دانلود می‌کنم…")
        except Exception:
            pass
        prog = {"stage": "آماده‌سازی", "done": 0, "total": 0, "speed": 0,
                "attempt": 0, "attempts_total": 0}

        task = asyncio.ensure_future(ytube.download(
            f"https://www.youtube.com/watch?v={vid}", mode, progress=prog))
        t0 = time.time()
        prev_done, prev_t = 0, t0
        prev_att = 0
        stalled_since = t0
        timed_out = False
        try:
            while True:
                _done, _ = await asyncio.wait({task}, timeout=4)
                if _done:
                    break
                now = time.time()
                el = int(now - t0)
                att = int(prog.get("attempt") or 0)
                tot = int(prog.get("attempts_total") or 0)
                done_b = prog.get("done") or 0
                # تلاش جدید شروع شده = پیشرفت → ریست توقف
                if att != prev_att:
                    prev_att = att
                    stalled_since = now
                if done_b != prev_done:
                    prev_done, prev_t = done_b, now
                    stalled_since = now
                elif now - stalled_since > 150:
                    # ۱۵۰ ثانیه نه بایت آمده نه تلاش عوض شده — مسیر خوابیده
                    task.cancel()
                    timed_out = True
                    break
                if el >= 840:
                    task.cancel()
                    timed_out = True
                    break
                spd = prog.get("speed") or 0
                if not spd and now - prev_t > 0:
                    spd = int(max(0, done_b - prev_done) / max(now - prev_t, 1))
                # ── سطر اول: تلاش چندم از چند + درصد مسیر ──
                head = (f"⏳ دانلود… تلاش {att} از {tot}"
                        + (f" ({int(att * 100 / tot)}% مسیر)" if tot else ""))
                txt = f"{head}\n🔄 {prog.get('stage', '')}"
                # ── نوار پیشرفت بایت + ETA ──
                if prog.get("total"):
                    pct = min(int(done_b * 100 / max(prog["total"], 1)), 100)
                    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                    extra = f"\n{bar} {pct}%\n📊 {fmt_size(done_b)} از {fmt_size(prog['total'])}"
                    if spd:
                        extra += f" | ⚡ {fmt_size(spd)}/s"
                        remain = prog["total"] - done_b
                        if remain > 0 and spd > 1024:
                            eta = int(remain / spd)
                            if eta < 3600:
                                extra += f" | ⏱ ~{eta}ث مونده"
                    txt += extra
                elif done_b:
                    txt += f"\n📊 {fmt_size(done_b)} دریافت شد" + (f" | ⚡ {fmt_size(spd)}/s" if spd else "")
                try:
                    await e.edit(txt)
                except Exception:
                    pass
            if timed_out:
                att = int(prog.get("attempt") or 0)
                tot = int(prog.get("attempts_total") or 0)
                await client.send_message(
                    chat,
                    f"⏳ بعد از {att} تلاش از {tot}، این ویدیو برای سرور ما خیلی کند/قفل شده.\n"
                    "دوباره بزن (هر بار مسیر متفاوتی امتحان می‌شود) یا کیفیت پایین‌تر / 🎵 صدا را امتحان کن.")
                return
            path, info = task.result()
            import pathlib
            if not path:
                await client.send_message(chat, "❌ دانلود ناموفق بود.")
                return
            p = pathlib.Path(path)
            if p.stat().st_size > 48 * 1024 * 1024:
                p.unlink()
                await client.send_message(
                    chat, "⚠️ از سقف ~۵۰ مگ تلگرام بزرگ‌تره — کیفیت پایین‌تر یا 🎵 صدا رو بزن.")
                return
            cap = f"🎬 {(info.get('title') or '')[:60]}"
            if mode == "audio":
                await client.send_file(chat, str(p), caption=cap,
                                       attributes=[DocumentAttributeAudio(
                                           int(info.get("duration") or 1),
                                           title=(info.get('title') or 'audio')[:60])])
            else:
                await client.send_file(chat, str(p), caption=cap,
                                       supports_streaming=True)
            p.unlink(missing_ok=True)
        except Exception as ex:
            s_ = str(ex).lower()
            att = int(prog.get("attempt") or 0)
            tot = int(prog.get("attempts_total") or 0)
            tried = f" ({att} تلاش از {tot})" if tot else ""
            if "ffmpeg is not installed" in s_ or "ffmpeg not found" in s_ or "merging of multiple formats" in s_:
                await client.send_message(
                    chat, "⚠️ ابزار ادغام ویدیو (ffmpeg) آماده نیست — مدیر با /selftest بررسی کند.")
            elif any(k in s_ for k in ("sign in", "not a bot", "403", "429")):
                await client.send_message(
                    chat, f"😖 یوتیوب این ویدیو را برای سرور ما قفل کرده{tried}.\n"
                          "دوباره بزن — هر بار مسیر متفاوتی امتحان می‌شود. یا ویدیوی دیگری را امتحان کن.")
            elif "timeout" in s_ or "timed out" in s_:
                await client.send_message(chat, f"⏳ دانلود طول کشید و قطع شد{tried} — دوباره امتحان کن.")
            else:
                await client.send_message(chat, f"❌ {str(ex)[:150]}{tried}")
        return
    await e.answer()


@client.on(events.NewMessage(pattern=r"^/aitest$"))
async def cmd_aitest(e):
    """تشخیص زنده‌ی هوش مصنوعی — تک‌تک مسیرها با کلید واقعی شما"""
    if e.sender_id not in ADMIN_IDS:
        return
    st = await e.reply("🧪 در حال تست زنده‌ی سرویس‌ها…")
    try:
        rep = await asyncio.to_thread(aichat.diagnose)
        await st.edit(rep[:4000])
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:150]}")


# ═══════════════ 🧰 دستورهای ابزار در چت ربات ═══════════════

@client.on(events.NewMessage(pattern=r"^/(tr|translate)(?:\s+(.+))?$"))
async def cmd_tr(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    body = (e.pattern_match.group(2) or "").strip()
    if not body:
        # متن از ریپلای
        try:
            if e.reply_to_msg_id:
                r_ = await e.get_reply_message()
                body = (r_.text or "").strip()
        except Exception:
            pass
    if not body:
        await e.reply("فرمت: /tr متن (فارسی→انگلیسی، انگلیسی→فارسی، یا /tr en: متن)")
        return
    to = "en" if re.search(r"[\u0600-\u06FF]", body) else "fa"
    if body[:3] in ("en:", "fa:", "ar:"):
        to, body = body[:2], body[3:].strip()
    st = await e.reply("🌐 دارم ترجمه می‌کنم…")
    try:
        import tools as _tl
        r_ = await asyncio.to_thread(_tl.translate, body, to)
        await st.edit(f"🌐 ترجمه به {r_['to_name']}:\n\n{r_['text'][:3500]}")
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/tts(?:\s+(.+))?$"))
async def cmd_tts(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    body = (e.pattern_match.group(1) or "").strip()
    if not body:
        await e.reply("فرمت: /tts متن — ویس فارسی برمی‌گردانم 🎙")
        return
    st = await e.reply("🎙 دارم ویس می‌سازم…")
    try:
        import tools as _tl
        p_ = await asyncio.to_thread(_tl.tts, body)
        await client.send_file(e.chat_id, p_, voice_note=True,
                               caption="🎙 " + body[:40])
        Path(p_).unlink(missing_ok=True)
        await st.delete()
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/(azan|owghat)(?:\s+(\S+))?$"))
async def cmd_azan(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    city = e.pattern_match.group(2) or "کاشان"
    st = await e.reply("🕌 …")
    try:
        import tools as _tl
        r_ = await asyncio.to_thread(_tl.azan, city)
        await st.edit(f"🕌 اوقات شرعی {r_['city']}\n" + "\n".join(r_["lines"])
                      + (f"\n📅 {r_['hijri']}" if r_["hijri"] else ""))
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/(weather|abayohava)(?:\s+(.+))?$"))
async def cmd_weather(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    city = (e.pattern_match.group(2) or "").strip() or "کاشان"
    st = await e.reply("🌤 …")
    try:
        import tools as _tl
        r_ = await asyncio.to_thread(_tl.weather, city)
        await st.edit(
            f"🌤 آب‌وهوای {r_['city']}\n{r_['desc']}\n"
            f"🌡 الان: {r_['temp']}° (حس {r_['feels']}°)\n"
            f"📈 امروز: {r_['min']}° تا {r_['max']}°\n"
            f"💧 رطوبت: {r_['humidity']}% | 💨 باد: {r_['wind']}km/h\n"
            f"☔ احتمال بارش: {r_['rain_prob']}%")
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/(lyrics|matnahang)(?:\s+(.+))?$"))
async def cmd_lyrics(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    body = (e.pattern_match.group(2) or "").strip()
    if not body:
        await e.reply("فرمت: /lyrics خواننده - نام آهنگ")
        return
    st = await e.reply("🎵 دارم می‌گردم…")
    try:
        import tools as _tl
        ly = await asyncio.to_thread(_tl.lyrics, body, "")
        await st.edit(f"🎵 متن آهنگ:\n\n{ly[:3900]}")
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]} — فرمت: «خواننده - نام آهنگ»")


@client.on(events.NewMessage(pattern=r"^/speed$"))
async def cmd_speed(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    st = await e.reply("⚡ دارم می‌سنجم…")
    try:
        import tools as _tl
        r_ = await asyncio.to_thread(_tl.speedtest)
        await st.edit(f"⚡ سرعت اینترنت سرور:\n⬇️ دانلود: {r_['down_mbps']} Mbps\n⏱ پینگ: {r_['ping_ms']}ms")
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


async def _bot_reply_photo(e):
    try:
        if e.reply_to_msg_id:
            r_ = await e.get_reply_message()
            if r_ and getattr(r_, "photo", None):
                return await r_.download_media(file="/tmp/bot_cmd_photo")
    except Exception:
        pass
    return None


@client.on(events.NewMessage(pattern=r"^/ocr$"))
async def cmd_ocr(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    photo = await _bot_reply_photo(e)
    if not photo:
        await e.reply("🖼 روی یک عکس ریپلای کن و /ocr بزن — متنش را می‌خوانم.")
        return
    st = await e.reply("📝 دارم می‌خونم…")
    try:
        import tools as _tl
        t_ = await asyncio.to_thread(_tl.ocr, photo)
        Path(photo).unlink(missing_ok=True)
        await st.edit(f"📝 متن داخل عکس:\n\n{t_[:3500]}")
    except Exception as ex:
        Path(photo).unlink(missing_ok=True)
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/rmbg$"))
async def cmd_rmbg(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    photo = await _bot_reply_photo(e)
    if not photo:
        await e.reply("🖼 روی یک عکس ریپلای کن و /rmbg بزن.")
        return
    st = await e.reply("✂️ …")
    try:
        import tools as _tl
        p_ = await asyncio.to_thread(_tl.removebg, photo)
        Path(photo).unlink(missing_ok=True)
        await client.send_file(e.chat_id, p_, caption="✂️ پس‌زمینه حذف شد")
        Path(p_).unlink(missing_ok=True)
        await st.delete()
    except Exception as ex:
        Path(photo).unlink(missing_ok=True)
        await st.edit(f"❌ {str(ex)[:150]}")


@client.on(events.NewMessage(pattern=r"^/meme(?:\s+(.+))?$"))
async def cmd_meme(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    body = (e.pattern_match.group(1) or "").strip()
    photo = await _bot_reply_photo(e)
    if not photo:
        await e.reply("🖼 روی یک عکس ریپلای کن و /meme متن بالا | متن پایین بزن.")
        return
    top_, bottom_ = (" | " in body) and body.split(" | ", 1) or (body, "")
    st = await e.reply("😂 دارم می‌سازم…")
    try:
        import tools as _tl
        p_ = await asyncio.to_thread(_tl.meme, photo, top_, bottom_)
        Path(photo).unlink(missing_ok=True)
        await client.send_file(e.chat_id, p_, caption="😂")
        Path(p_).unlink(missing_ok=True)
        await st.delete()
    except Exception as ex:
        Path(photo).unlink(missing_ok=True)
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/(dict|dictword)(?:\s+(.+))?$"))
async def cmd_dict(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    w = (e.pattern_match.group(2) or "").strip()
    if not w:
        await e.reply("فرمت: /dict word — معنی کلمه‌ی انگلیسی 📖")
        return
    st = await e.reply("📖 …")
    try:
        import tools as _tl
        r_ = await asyncio.to_thread(_tl.define, w)
        await st.edit(r_[:3900])
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/calc(?:\s+(.+))?$"))
async def cmd_calc(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    expr = (e.pattern_match.group(1) or "").strip()
    if not expr:
        await e.reply("فرمت: /calc 25*4+10 🧮")
        return
    try:
        import tools as _tl
        r_ = await asyncio.to_thread(_tl.calc, expr)
        await e.reply(r_)
    except Exception as ex:
        await e.reply(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/paste(?:\s+(.+))?$"))
async def cmd_paste(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    body = (e.pattern_match.group(1) or "").strip()
    if not body:
        try:
            if e.reply_to_msg_id:
                r_ = await e.get_reply_message()
                body = (r_.text or "").strip()
        except Exception:
            pass
    if not body:
        await e.reply("فرمت: /paste متن — یا روی پیام ریپلای کن و /paste بزن 📄")
        return
    st = await e.reply("📄 دارم منتشر می‌کنم…")
    try:
        import tools as _tl
        u_ = await asyncio.to_thread(_tl.paste, "متن", body)
        await st.edit(f"📄 متن منتشر شد:\n{u_}")
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/notes?(?:\s+(.+))?$"))
async def cmd_notes(e):
    if not e.is_private:
        return
    if e.sender_id not in ADMIN_IDS:
        return
    arg = (e.pattern_match.group(1) or "").strip()
    if not arg:
        items = NOTES.get("items", [])
        if not items:
            await e.reply("📝 یادداشتی نداری — /note متن")
        else:
            lines = ["📝 یادداشت‌ها:", ""]
            for i, it in enumerate(items[:20], 1):
                lines.append(f"{i}. {it['t'][:60]}")
            await e.reply("\n".join(lines))
        return
    low = arg.lower()
    if low.startswith("del") and len(arg.split()) > 1 and arg.split()[1].isdigit():
        i_ = int(arg.split()[1]) - 1
        items = NOTES.get("items", [])
        if 0 <= i_ < len(items):
            removed = items.pop(i_)
            _save_notes()
            await e.reply(f"🗑 حذف شد: {removed['t'][:40]}")
        else:
            await e.reply("چنین شماره‌ای نیست.")
        return
    NOTES.setdefault("items", []).append({"t": arg[:500], "ts": time.time()})
    NOTES["items"] = NOTES["items"][-50:]
    _save_notes()
    await e.reply(f"📝 یادداشت شماره {len(NOTES['items'])} ذخیره شد.")


@client.on(events.NewMessage(pattern=r"^/time(?:\s+(.+))?$"))
async def cmd_time(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    city = (e.pattern_match.group(1) or "کاشان").strip()
    try:
        import tools as _tl
        r_ = await asyncio.to_thread(_tl.world_time, city)
        await e.reply(f"🕐 {r_['city']} — {r_['weekday']} {r_['date']}\n⏰ ساعت: {r_['time']}")
    except Exception as ex:
        await e.reply(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/wall(?:\s+(.+))?$"))
async def cmd_wall(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    q = (e.pattern_match.group(1) or "nature").strip()
    st = await e.reply("🖼 دارم والپیپر می‌گیرم…")
    try:
        import tools as _tl
        p_ = await asyncio.to_thread(_tl.wall, q)
        await client.send_file(e.chat_id, p_, caption=f"🖼 والپیپر «{q}»")
        Path(p_).unlink(missing_ok=True)
        await st.delete()
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/img$"))
async def cmd_img(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    try:
        import tools as _tl
        p_ = await asyncio.to_thread(_tl.random_img)
        await client.send_file(e.chat_id, p_, caption="🖼 عکس تصادفی")
        Path(p_).unlink(missing_ok=True)
    except Exception as ex:
        await e.reply(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/logo(?:\s+(.+))?$"))
async def cmd_logo(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    body = (e.pattern_match.group(1) or "").strip()
    style = 0
    if body and body.split()[0].isdigit():
        style = int(body.split()[0])
        body = body.split(None, 1)[1] if len(body.split()) > 1 else ""
    if not body:
        await e.reply("فرمت: /logo [استایل ۰-۵] متن — لوگوی فارسی/انگلیسی 🎨")
        return
    st = await e.reply("🎨 دارم لوگو می‌سازم…")
    try:
        import tools as _tl
        p_ = await asyncio.to_thread(_tl.logo, body, style)
        await client.send_file(e.chat_id, p_, caption="🎨")
        Path(p_).unlink(missing_ok=True)
        await st.delete()
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/quote(?:\s+(.+))?$"))
async def cmd_quote(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    body = (e.pattern_match.group(1) or "").strip()
    author = ""
    if " | " in body:
        body, author = body.split(" | ", 1)
    if not body:
        try:
            if e.reply_to_msg_id:
                r_ = await e.get_reply_message()
                body = (r_.text or "").strip()
                if r_.sender:
                    author = ((getattr(r_.sender, "first_name", "") or "") + " " +
                              (getattr(r_.sender, "last_name", "") or "")).strip()
        except Exception:
            pass
    if not body:
        await e.reply("فرمت: /quote متن | نویسنده — یا روی پیامی ریپلای کن 💬")
        return
    st = await e.reply("💬 …")
    try:
        import tools as _tl
        p_ = await asyncio.to_thread(_tl.quote, body, author)
        await client.send_file(e.chat_id, p_, caption="💬")
        Path(p_).unlink(missing_ok=True)
        await st.delete()
    except Exception as ex:
        await st.edit(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/md(?:\s+(.+))?$"))
async def cmd_md(e):
    if not e.is_private or e.sender_id not in ADMIN_IDS:
        return
    body = (e.pattern_match.group(1) or "").strip()
    if not body:
        await e.reply("فرمت: /md **بولد** *ایتالیک* __زیرخط__ ~~خط‌خورده~~ ||اسپویل|| `کد`")
        return
    txt_, ents_ = _md_parse(body)
    await client.send_message(e.chat_id, txt_[:3500], entities=ents_)


@client.on(events.NewMessage(pattern=r"^/fun$"))
async def cmd_fun(e):
    if not e.is_private:
        return
    await note_user(e)
    await e.reply(
        "🎮 سرگرمی:\n\n"
        "🪙 /coin — شیر یا خط\n"
        "🎲 /roll [عدد] — تاس\n"
        "🎱 /8ball سوال — فال\n"
        "😂 /joke — جوک\n"
        "💡 /fact — دانستنی\n"
        "💘 /love علی | سارا — عشق‌سنج\n"
        "🔒 /hack — هک نمایشی\n"
        "🎨 /logo 1 متن | /quote متن | /wall | /img\n"
        "🎲 /dice | 🐒 /kang | 🖼 /stickify | 😂 /meme | 🔥 /fry\n\n"
        "همه‌ی این‌ها با اکانت خودت (اکانت‌خوان) هم کار می‌کنند — در هر چتی بنویس.")


@client.on(events.NewMessage(pattern=r"^/(coin|coinflip)$"))
async def cmd_coin(e):
    if not e.is_private:
        return
    await note_user(e)
    import tools as _tl
    await e.reply(_tl.coinflip())


@client.on(events.NewMessage(pattern=r"^/(roll|dice2)(?:\s+(\d+))?$"))
async def cmd_roll(e):
    if not e.is_private:
        return
    await note_user(e)
    import tools as _tl
    n = 100
    try:
        n = int(e.pattern_match.group(2) or 100)
    except Exception:
        n = 100
    await e.reply(_tl.roll(n))


@client.on(events.NewMessage(pattern=r"^/(8ball|fal)(?:\s+(.+))?$"))
async def cmd_8ball(e):
    if not e.is_private:
        return
    await note_user(e)
    import tools as _tl
    await e.reply(_tl.eightball())


@client.on(events.NewMessage(pattern=r"^/joke$"))
async def cmd_joke(e):
    if not e.is_private:
        return
    await note_user(e)
    import tools as _tl
    await e.reply(_tl.joke())


@client.on(events.NewMessage(pattern=r"^/fact$"))
async def cmd_fact(e):
    if not e.is_private:
        return
    await note_user(e)
    import tools as _tl
    await e.reply(_tl.fact())


@client.on(events.NewMessage(pattern=r"^/love(?:\s+(.+))?$"))
async def cmd_love(e):
    if not e.is_private:
        return
    await note_user(e)
    body = (e.pattern_match.group(1) or "").strip()
    names = [x.strip() for x in body.split("|") if x.strip()] if "|" in body else body.split(None, 1)
    if len(names) < 2 or not names[0]:
        await e.reply("فرمت: /love علی | سارا 💘")
        return
    import tools as _tl
    await e.reply(_tl.love(names[0], names[1]))


# ═══════════════ 🎵 اسپاتیفای ═══════════════

SP_CACHE = {}      # chat_id → پلی‌لیست اخیر (برای دکمه‌ها)


def _sp_q(tr):
    """کوئری جستجوی یوتیوب از ترک اسپاتیفای"""
    if tr.get("artists"):
        return f'{tr["title"]} {tr["artists"]} audio'
    sub = (tr.get("subtitle") or "").replace(" • ", " ")
    return f'{tr["title"]} {sub} audio'.strip()


async def _sp_send_track(chat_id, title, artists, dur, reply_btn=None):
    """دانلود ترک از یوتیوب (بر اساس نام) → ارسال موزیک"""
    q = f"{title} {artists} audio".strip()
    status = None
    try:
        if reply_btn:
            await reply_btn.edit(f"🎵 دارم «{title[:40]}» رو می‌گیرم… (تا ۲ دقیقه)")
    except Exception:
        pass
    # جستجو در یوتیوب
    results = await asyncio.wait_for(ytube.search(q, 3), timeout=60)
    if not results:
        raise RuntimeError("در یوتیوب پیدا نشد")
    vid = results[0]["id"]
    path, info = await asyncio.wait_for(
        ytube.download(f"https://www.youtube.com/watch?v={vid}", "audio"), timeout=600)
    p = Path(path)
    if not p.exists():
        raise RuntimeError("دانلود نشد")
    if p.stat().st_size > 48 * 1024 * 1024:
        p.unlink(missing_ok=True)
        raise RuntimeError("بزرگ‌تر از سقف تلگرام شد")
    caption = f"🎵 {title[:60]}" + (f"\n🎤 {artists[:60]}" if artists else "") + "\n🎧 از اسپاتیفای"
    await client.send_file(
        chat_id, str(p), caption=caption,
        attributes=[DocumentAttributeAudio(
            int(dur or info.get("duration") or 1),
            title=title[:60], performer=(artists or "Spotify")[:60])])
    p.unlink(missing_ok=True)


@client.on(events.NewMessage(pattern=r"^/spotify(?:\s+(.+))?$"))
async def cmd_spotify(e):
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "yt"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    arg = (e.pattern_match.group(1) or "").strip()
    if not arg:
        await e.reply(
            "🎵 اسپاتیفای:\n\n"
            "• لینک ترک بفرست → آهنگش رو می‌گیرم\n"
            "• لینک پلی‌لیست/آلبوم → لیست می‌آید، دکمه‌ای انتخاب کن\n"
            "• /spotify اسم آهنگ → سرچ (کلید لازم)\n"
            "• /spotify user یوزرنیم → پلی‌لیست‌های عمومی (کلید لازم)\n\n"
            "🔑 کلید رایگان: /setkey spotify آیدی:رمز — از developer.spotify.com/dashboard")
        return
    await _spotify_flow(e, arg, is_bot_chat=True)


async def _spotify_flow(e, arg, is_bot_chat=False):
    """مسیر مشترک اسپاتیفای در چت ربات و یوزربات"""
    import tools as _tl
    try:
        kind, sid = _tl.spotify_parse(arg)
        if kind == "track":
            st = await e.reply("🎵 دارم می‌گیرم…")
            tr = await asyncio.to_thread(_tl.spotify_track, arg)
            await _sp_send_track(e.chat_id, tr["title"], tr["artists"], tr["duration"])
            try:
                await st.delete()
            except Exception:
                pass
            return
        if kind in ("playlist", "album"):
            st = await e.reply("📋 دارم لیست می‌گیرم…")
            pl = await asyncio.to_thread(_tl.spotify_list, arg, 50)
            SP_CACHE[e.chat_id] = pl
            rows = []
            for i, t in enumerate(pl["tracks"][:10]):
                label = f"{i+1}. {t['title'][:35]}"
                if t.get("subtitle"):
                    label += f" — {t['subtitle'].split(' • ')[0][:15]}"
                rows.append([Button.inline(label, f"sp:{i}")])
            rows.append([Button.inline("📥 همه ۱۰ تا", b"sp:all"),
                         Button.inline("صفحه بعد ⏭", b"sp:next:10")])
            try:
                await st.edit(
                    f"🎵 {pl['name'][:50]} — {pl['count']} ترک (۱۰ تای اول):\n"
                    "روی هرکدوم بزن تا بیاد 👇", buttons=rows)
            except Exception:
                await e.reply("لیست بزرگ است — ۱۰ تای اول:", buttons=rows)
            return
        if kind == "artist":
            await e.reply("🎧 لینک هنرمند است — اسم آهنگش رو بگو تا بگیرم.")
            return
        # جستجو با کلید یا یوتیوب
        if arg.lower().startswith("user "):
            un = arg.split(None, 1)[1].strip()
            pls = await asyncio.to_thread(_tl.spotify_user_playlists, un)
            if not pls:
                await e.reply("پلی‌لیست عمومی پیدا نشد.")
                return
            lines = [f"👤 پلی‌لیست‌های {un}:", ""]
            for i, p in enumerate(pls, 1):
                lines.append(f"{i}. {p['name'][:40]} ({p['count']} ترک)\nopen.spotify.com/playlist/{p['id']}")
            await e.reply("\n".join(lines)[:3900])
            return
        # جستجو
        try:
            results = await asyncio.to_thread(_tl.spotify_search, arg, 5)
            rows = [[Button.inline(f"🎵 {r['title'][:30]} — {r['artists'][:20]}",
                                   f"spq:{r['id']}")] for r in results[:5]]
            await e.reply("نتایج اسپاتیفای 👇", buttons=rows)
        except RuntimeError:
            # بدون کلید → مستقیم یوتیوب
            st = await e.reply("🔍 (بدون کلید اسپاتیفای — از یوتیوب می‌گیرم)…")
            await _sp_send_track(e.chat_id, arg, "", 0)
            try:
                await st.delete()
            except Exception:
                pass
    except Exception as ex:
        try:
            await e.reply(f"❌ {str(ex)[:200]}")
        except Exception:
            pass


@client.on(events.CallbackQuery(pattern=r"^sp:(?:\d+|all|next:\d+)$"))
async def on_sp_cb(e):
    await note_user(e)
    data = e.data.decode()
    pl = SP_CACHE.get(e.chat_id)
    if not pl:
        await e.answer("منقضی — دوباره لینک بفرست", alert=True)
        return
    await e.answer()
    tracks = pl["tracks"]
    if data == "sp:all":
        for i in range(10):
            if i >= len(tracks):
                break
            t = tracks[i]
            try:
                await _sp_send_track(e.chat_id, t["title"], t.get("subtitle", ""), 0)
                await asyncio.sleep(2)
            except Exception as ex:
                try:
                    await client.send_message(e.chat_id, f"❌ {t['title'][:30]}: {str(ex)[:80]}")
                except Exception:
                    pass
        return
    if data.startswith("sp:next:"):
        off = int(data.split(":")[2])
        rows = []
        for i, t in enumerate(tracks[off:off + 10], off):
            label = f"{i+1}. {t['title'][:35]}"
            if t.get("subtitle"):
                label += f" — {t['subtitle'].split(' • ')[0][:15]}"
            rows.append([Button.inline(label, f"sp:{i}")])
        rows.append([Button.inline("📥 همه", b"sp:all")])
        if off + 10 < len(tracks):
            rows.append([Button.inline("صفحه بعد ⏭", f"sp:next:{off+10}".encode())])
        try:
            await e.edit(f"🎵 {pl['name'][:40]} — ترک {off+1} به بعد:", buttons=rows)
        except Exception:
            pass
        return
    # یک ترک
    idx = int(data.split(":")[1])
    if idx >= len(tracks):
        return
    t = tracks[idx]
    await _sp_send_track(e.chat_id, t["title"], t.get("subtitle", ""), 0, reply_btn=e)


@client.on(events.CallbackQuery(pattern=r"^spq:[A-Za-z0-9]{22}$"))
async def on_spq_cb(e):
    await note_user(e)
    sid = e.data.decode()[4:]
    await e.answer()
    import tools as _tl
    st = await client.send_message(e.chat_id, "🎵 دارم می‌گیرم…")
    try:
        tr = await asyncio.to_thread(_tl.spotify_track, sid)
        await _sp_send_track(e.chat_id, tr["title"], tr["artists"], tr["duration"])
        try:
            await st.delete()
        except Exception:
            pass
    except Exception as ex:
        try:
            await st.edit(f"❌ {str(ex)[:150]}")
        except Exception:
            pass




@client.on(events.NewMessage(pattern=r"^/js(?:\s+([\s\S]+))?$"))
async def cmd_js(e):
    if not e.is_private or e.sender_id not in ADMIN_IDS:
        return
    code = (e.pattern_match.group(1) or "").strip()
    fpath = None
    try:
        if e.reply_to_msg_id and not code:
            r_ = await e.get_reply_message()
            if r_ and r_.file and (r_.file.name or "").endswith(".js"):
                fpath = await r_.download_media(file="/tmp/bot_run.js")
    except Exception:
        fpath = None
    if not code and not fpath:
        await e.reply("🟨 فرمت: /js کد — یا روی فایل .js ریپلای کن\nمثال: /js console.log(\"سلام\")")
        return
    st = await e.reply("🟨 در حال اجرا…")
    try:
        import tools as _tl
        out_ = await asyncio.to_thread(_tl.js_run, code, fpath)
        if fpath:
            Path(fpath).unlink(missing_ok=True)
        await st.edit("🟨 " + out_[:3900])
    except Exception as ex:
        if fpath:
            Path(fpath).unlink(missing_ok=True)
        await st.edit(f"❌ {str(ex)[:200]}")


@client.on(events.NewMessage(pattern=r"^/cmd(?:\s+([\s\S]+))?$"))
async def cmd_cmdbatch(e):
    if not e.is_private or e.sender_id not in ADMIN_IDS:
        return
    code = (e.pattern_match.group(1) or "").strip()
    fpath = None
    try:
        if e.reply_to_msg_id and not code:
            r_ = await e.get_reply_message()
            if r_ and r_.file and (r_.file.name or "").lower().endswith((".cmd", ".bat")):
                fpath = await r_.download_media(file="/tmp/bot_run.cmd")
    except Exception:
        fpath = None
    if not code and not fpath:
        await e.reply("🖥 فرمت: /cmd دستور — یا فایل .cmd/.bat را بفرست و ریپلای کن\n"
                      "مثال: /cmd echo Salam %USERNAME%")
        return
    st = await e.reply("🖥 در حال اجرا…")
    try:
        import tools as _tl
        out_ = await asyncio.to_thread(_tl.cmd_run, code, fpath)
        if fpath:
            Path(fpath).unlink(missing_ok=True)
        await st.edit("🖥\n" + out_[:3900])
    except Exception as ex:
        if fpath:
            Path(fpath).unlink(missing_ok=True)
        await st.edit(f"❌ {str(ex)[:200]}")


@client.on(events.NewMessage(pattern=r"^/stt$"))
async def cmd_stt(e):
    """🎙 تبدیل ویس/صدا/ویدیو-پیام به متن فارسی — روی پیامش ریپلای کن"""
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    if not AI_OK:
        await e.reply("⚠️ ماژول هوش مصنوعی آماده نیست.")
        return
    if not e.reply_to_msg_id:
        await e.reply("🎙 روی یک ویس / ویدیو-پیام / فایل صوتی ریپلای کن و /stt بزن\n"
                      "💡 ویس‌ها را مستقیم بفرستی هم خودکار متن می‌آید!")
        return
    r_ = await e.get_reply_message()
    if not (r_ and (r_.voice or getattr(r_, "video_note", None) or r_.audio
                    or (r_.file and (r_.file.name or "").lower().endswith(
                        (".mp3", ".m4a", ".ogg", ".wav", ".opus", ".flac", ".mp4", ".mkv", ".webm"))))):
        await e.reply("❌ این پیام ویس/صدا/ویدیو نیست.")
        return
    try:
        path = await r_.download_media(file="/tmp/stt_cmd")
        if not path:
            await e.reply("❌ دانلود نشد.")
            return
        kind = "ویس" if r_.voice else ("ویدیو-پیام" if getattr(r_, "video_note", None) else "صدا")
        await _stt_flow(e, path, kind)
    except Exception as ex:
        await e.reply(f"❌ {str(ex)[:150]}")


@client.on(events.NewMessage(pattern=r"^/link$"))
async def cmd_link(e):
    """🔗 فایل → لینک اینترنت (دائمی یا موقت) — روی فایل ریپلای کن"""
    if not e.is_private:
        return
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    if not TOOLS_OK:
        await e.reply("⚠️ ماژول tools.py آماده نیست.")
        return
    if not e.reply_to_msg_id:
        await e.reply("🔗 روی هر فایلی (عکس/ویدیو/آهنگ/زیپ/هرچی) ریپلای کن و /link بزن\n"
                      "لینکش را می‌دهم — دائمی، تا وقتی خودش کار کند 😉\n"
                      "💡 لینک را با هر کسی به اشتراک بگذار")
        return
    r_ = await e.get_reply_message()
    if not (r_ and r_.file):
        await e.reply("❌ روی فایل ریپلای کن (نه متن).")
        return
    if (r_.file.size or 0) > ACCEPT_MAX:
        await e.reply(f"⚠️ سقف عملی {CAP_TEXT} است؛ تلگرام هم بالاتر از ~۲ گیگ را منتقل نمی‌کند.")
        return
    st = await e.reply("⏳ دارم آپلود می‌کنم… (ممکن است کمی طول بکشد)")
    try:
        path = await r_.download_media(file="/tmp/link_up")
        if not path:
            await st.edit("❌ دانلود فایل ناموفق بود.")
            return
        import tools as _tl
        res = await asyncio.to_thread(_tl.upload_file_link, path)
        Path(path).unlink(missing_ok=True)
        name = (r_.file.name or "فایل")[:40]
        await st.edit(
            f"🔗 لینک آماده! ({res['kind']})\n\n"
            f"📄 {name}\n"
            f"🔗 {res['url']}\n\n"
            "با دکمه‌ی زیر هم می‌توانی به اشتراک بگذاری 👇",
            buttons=[[Button.url("🔗 باز کردن لینک", res["url"])],
                     [Button.switch_inline("📤 اشتراک با دیگران", res["url"])]],
            link_preview=False)
    except Exception as ex:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass
        await st.edit(f"❌ {str(ex)[:150]}")


@client.on(events.NewMessage(pattern=r"^/selftest$"))
async def cmd_selftest(e):
    """🧪 تست زنده‌ی هر ۴ بخش: ffmpeg، یوتیوب، اینستا، هوش مصنوعی، فشرده‌سازی"""
    if e.sender_id not in ADMIN_IDS:
        return
    st = await e.reply("🧪 خودآزمایی شروع شد… (۲–۳ دقیقه)")
    ub = "✅ وصل" if (user_client and user_client.is_connected()) else "❌ قطع (/login)"
    lines = [f"🧪 خودآزمایی ربات ({BOT_VERSION})", f"👁 اکانت‌خوان: {ub}", ""]

    async def upd():
        try:
            await st.edit("\n".join(lines)[:4000])
        except Exception:
            pass

    # ── ۱) ffmpeg
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
             "-loglevel", "error", "/tmp/st_ffmpeg.mp4"],
            capture_output=True, text=True, timeout=60)
        ok = r.returncode == 0 and Path("/tmp/st_ffmpeg.mp4").exists()
        v = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=30)
        ver = v.stdout.splitlines()[0].split("version")[-1].strip()[:20] if v.returncode == 0 else "?"
        lines.append(f"1️⃣ ffmpeg: {'✅ ' + ver if ok else '❌ ' + r.stderr[-80:]}")
    except Exception as ex:
        lines.append(f"1️⃣ ffmpeg: ❌ {str(ex)[:80]}")
    await upd()

    # ── ۲) جستجوی یوتیوب
    try:
        if not YT_OK:
            raise RuntimeError("ماژول ytube.py آپلود نشده")
        res = await asyncio.wait_for(ytube.search("old town road", 2), timeout=60)
        lines.append(f"2️⃣ سرچ یوتیوب: ✅ {len(res)} نتیجه — {res[0]['title'][:40] if res else ''}")
    except Exception as ex:
        lines.append(f"2️⃣ سرچ یوتیوب: ❌ {str(ex)[:80]}")
    await upd()

    # ── ۳) اطلاعات + دانلود یوتیوب (صدا — سبک)
    try:
        if not YT_OK:
            raise RuntimeError("ماژول ytube.py آپلود نشده")
        info = await asyncio.wait_for(
            ytube.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), timeout=100)
        title = (info.get("title") or "?")[:35]
        via = " (سرور واسط 📡)" if info.get("_piped") else ""
        lines.append(f"3️⃣ اطلاعات یوتیوب: ✅ «{title}»{via}")
        await upd()
        path, inf2 = await asyncio.wait_for(
            ytube.download("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "audio"), timeout=300)
        sz = Path(path).stat().st_size // 1024
        via2 = " (سرور واسط 📡)" if (inf2 or {}).get("_piped") else ""
        Path(path).unlink(missing_ok=True)
        lines.append(f"   دانلود صدا: ✅ {sz}KB{via2}")
    except Exception as ex:
        lines.append(f"3️⃣ دانلود یوتیوب: ❌ {str(ex)[:90]}")
    await upd()

    # ── ۴) اینستاگرام (ریلز تستی عمومی)
    try:
        if not INSTA_OK:
            raise RuntimeError("ماژول insta.py آپلود نشده")
        files, info, method = await asyncio.wait_for(
            insta.download_media("https://www.instagram.com/reel/DO5tIDME6t-/"), timeout=150)
        sz = sum(Path(f).stat().st_size for f in files) // 1024
        for f in files:
            Path(f).unlink(missing_ok=True)
        lines.append(f"4️⃣ اینستا ریلز: ✅ {len(files)} فایل {sz}KB ({method})")
    except Exception as ex:
        lines.append(f"4️⃣ اینستا ریلز: ❌ {str(ex)[:90]}")
    await upd()

    # ── ۵) هوش مصنوعی
    try:
        if not AI_OK:
            raise RuntimeError("ماژول aichat.py آپلود نشده")
        ans = await asyncio.to_thread(aichat.ask, "فقط یک کلمه جواب بده: سلام")
        model = getattr(aichat, "LAST_OK", "") or "?"
        lines.append(f"5️⃣ هوش مصنوعی: ✅ «{ans[:25]}» ({model[:40]})")
    except Exception as ex:
        lines.append(f"5️⃣ هوش مصنوعی: ❌ {str(ex)[:90]}")
    await upd()

    # ── ۶) فشرده‌سازی (همان ویدیوی تست ffmpeg)
    try:
        src = Path("/tmp/st_ffmpeg.mp4")
        if not src.exists():
            raise RuntimeError("فایل تست نیست")
        out = await asyncio.to_thread(
            _compress_sync, src, "/tmp/st_comp", KIND_VIDEO, "480",
            ffprobe(src), {"pct": 0.0}, None)
        o = Path(out)
        a, b = src.stat().st_size, o.stat().st_size
        o.unlink(missing_ok=True)
        src.unlink(missing_ok=True)
        lines.append(f"6️⃣ فشرده‌سازی: ✅ {a // 1024}KB → {b // 1024}KB")
    except Exception as ex:
        lines.append(f"6️⃣ فشرده‌سازی: ❌ {str(ex)[:90]}")
    await upd()

    n_ok = sum(1 for l_ in lines if "✅" in l_)
    lines += ["", f"📊 نتیجه: {n_ok} از ۶ بخش سالم"]
    if n_ok < 6:
        lines.append("خروجی همین پیام را برای سازنده بفرست.")
    await upd()


@client.on(events.NewMessage(pattern=r"^/testdl$"))
async def cmd_testdl(e):
    """تست کامل: آیا اکانت‌خوان می‌تواند رسانه دانلود کند؟"""
    if e.sender_id not in ADMIN_IDS:
        return
    if not USERBOT_ENABLED:
        await e.reply("🔴 اکانت‌خوان خاموش است (حالت تست).")
        return
    lines = ["🧪 تست دانلود اکانت‌خوان", ""]
    # ۱) وضعیت اتصال
    if not user_client:
        await e.reply("❌ اکانت‌خوان اصلاً وجود ندارد — اول /login")
        return
    lines.append(f"۱. وجود: ✅")
    try:
        conn = user_client.is_connected()
        lines.append(f"۲. اتصال: {'✅ وصل' if conn else '❌ قطع'}")
    except Exception as ex:
        lines.append(f"۲. اتصال: ❌ {str(ex)[:60]}")
    try:
        auth = await user_client.is_user_authorized()
        lines.append(f"۳. احراز هویت: {'✅' if auth else '❌ نشست منقضی'}")
    except Exception as ex:
        lines.append(f"۳. احراز هویت: ❌ {str(ex)[:60]}")
    try:
        me = await user_client.get_me()
        ph = str(getattr(me, "phone", "") or "")
        lines.append(f"۴. اکانت: {getattr(me, 'first_name', '?')} (+{ph[:4]}**{ph[-2:] if len(ph) > 6 else ''})")
    except Exception as ex:
        lines.append(f"۴. اکانت: ❌ {str(ex)[:60]}")
    # ۵) دریافت آخرین پیام رسانه‌ای
    try:
        found = False
        async for dialog in user_client.iter_dialogs(limit=5):
            if not dialog.is_user:
                continue
            msgs = await user_client.get_messages(dialog.entity, limit=3)
            for msg in msgs:
                if msg.media and not msg.out:
                    try:
                        path = await user_client.download_media(msg)
                        if path:
                            sz = Path(path).stat().st_size
                            Path(path).unlink(missing_ok=True)
                            lines.append(f"۵. دانلود تست: ✅ ({fmt_size(sz)} از {dialog.name})")
                            found = True
                        break
                    except Exception as ex:
                        lines.append(f"۵. دانلود تست: ❌ {str(ex)[:80]}")
                        found = True
                    break
            if found:
                break
        if not found:
            lines.append("۵. دانلود تست: ⚠️ پیام رسانه‌ای جدید برای تست پیدا نشد")
            lines.append("   → یه عکس به اکانت بفرست و دوباره /testdl بزن")
    except Exception as ex:
        lines.append(f"۵. دانلود: ❌ {str(ex)[:80]}")
    # ۶) آمار
    lines.append(f"")
    lines.append(f"📊 ذخیره‌شده: {TTL_STATS['saved']} | خطا: {TTL_STATS['errors']}")
    if TTL_STATS["last_err"]:
        lines.append(f"   آخرین خطا: {TTL_STATS['last_err']}")
    await e.reply("\n".join(lines))


@client.on(events.NewMessage(pattern=r"^/resetanten$"))
async def cmd_resetanten(e):
    if e.sender_id not in ADMIN_IDS:
        return
    if not ANTEN_OK:
        await e.reply("anten.py نیست.")
        return
    anten.reset_all()
    n = anten.fetch_mtproto()
    await e.reply(f"🧹 همه‌ی پروکسی‌ها/کانفیگ‌ها و رأی‌ها پاک شد.\n"
                  f"🔄 {n} پروکسی تازه از منبع متنی اومد؛ بقیه با اکانت‌خوان خودکار پر می‌شن.")


@client.on(events.NewMessage(pattern=r"^/backup$"))
async def cmd_backup(e):
    if e.sender_id not in ADMIN_IDS:
        return
    try:
        payload = tgstore.export_payload()
        await e.reply("💾 نسخه‌ی پشتیبان — برای بازگردانی، روی همین پیام ریپلای کن و بفرست:\n/restore",
                      file=None) if False else None
        await client.send_message(e.chat_id,
                                  "💾 نسخه‌ی پشتیبان حافظه — برای بازگردانی روی این پیام ریپلای کن و /restore بفرست:\n\n" + payload)
        await e.reply("✅ پشتیبان ساخته شد (پیام بالایی)")
    except Exception as ex:
        await e.reply(f"❌ {str(ex)[:120]}")


@client.on(events.NewMessage(pattern=r"^/restore$"))
async def cmd_restore(e):
    if e.sender_id not in ADMIN_IDS:
        return
    reply = await e.get_reply_message()
    if not reply or "DATA|" not in (reply.text or ""):
        await e.reply("روی پیامِ «💾 نسخه‌ی پشتیبان» ریپلای کن و بعد /restore بفرست.")
        return
    tgstore.import_payload(reply.text)
    rep = await apply_restore(reconnect_userbot=True)
    await e.reply(rep)


@client.on(events.NewMessage(pattern=r"^/debug$"))
async def cmd_debug(e):
    if not e.is_private:
        return
    if e.sender_id not in ADMIN_IDS:
        return
    try:
        import tgstore as _tg
        st_ = _tg.stats()
        tg_line = f"متصل ({st_['msg_found']})" if _tg.available() else "خاموش"
    except Exception:
        tg_line = "خطا"
    up = int(time.time() - PROCESS_START)
    yt_ver = getattr(ytube, "VERSION", "?") if YT_OK else "—"
    ai_ver = getattr(aichat, "VERSION", "?") if AI_OK else "—"
    ig_ver = getattr(insta, "VERSION", "?") if INSTA_OK else "—"
    ai_line = aichat.status_line() if AI_OK else "❌ ماژول aichat.py نیست"
    txt = (
        f"🔍 وضعیت فنی ربات ({BOT_VERSION})\n\n"
        f"🟢 روشن: {up // 3600}ساعت {(up % 3600) // 60}دقیقه\n"
        f"🔧 هندلرها: {len(client.list_event_handlers())}\n"
        f"📦 ماژول‌ها: ytube {yt_ver} | aichat {ai_ver} | insta {ig_ver}\n"
        f"🎬 ffmpeg: {'✅ آماده' if FFMPEG_OK else '❌ ندارد (فشرده‌سازی/ادغام خراب)'}\n"
        f"🧠 هوش مصنوعی: {ai_line}\n"
        f"📡 آنتن: {'فعال ✅' if ANTEN_OK else 'غیرفعال ❌ (anten.py آپلود نشده)'}\n"
        f"🔑 کوکی یوتیوب: {'✅ فعال' if (YT_OK and ytube.has_cookies()) else '❌ ندارد — فایل cookies.txt بفرست'}\n"
        f"🛰 کانفیگ‌های کانال‌ها: {len(anten.custom['v2']) if ANTEN_OK else 0}"
        f" | 📡 پروکسی‌ها: {len(anten.custom['mtproto']) if ANTEN_OK else 0}\n"
        f"🔗 پروکسی‌های دستی: {len(anten.custom['mtproto']) if ANTEN_OK else 0}\n"
        f"🗳 رأی‌های آنتن: {len(anten.votes) if ANTEN_OK else 0}\n"
        f"💾 حافظه تلگرامی: {tg_line}\n"
        f"💾 مخزن اسپیس: {persist.status_reason()}\n"
        f"📍 منطقه‌ها: {len(bargha.regions)} | 👥 کاربران: {len(users_seen)}\n"
        f"⚡ اطلاعیه‌ها: {len(bargha.state.get('corpus', []))}\n"
        f"📸 رسانه‌های ذخیره‌شده: {TTL_STATS['saved']} | خطا: {TTL_STATS['errors']}\n"
        f"   آخرین خطا: {TTL_STATS['last_err'] or '—'}\n"
        f"📥 حالت مدیا: {'همه' if SD_SETTINGS.get('capture_mode') == 'all' else 'فقط خودموف'}"
        f" | 👥 کاربران متصل: {len(sd_clients)}\n"
        f"👁 اکانت‌خوان: {'خاموش' if not USERBOT_ENABLED else ('وصل ✅' if (user_client and user_client.is_connected()) else 'قطع (با /login وصل شو)')}\n"
        f"🧠 دستیار /m: {'فعال ✅' if AGENT_ENABLED else 'خاموش (AGENT_ENABLED=0)'}\n"
        f"👥 گروه AI: {'✅ فعال' if (AGENT_ENABLED and AI_GROUP.get('id')) else ('خاموش' if not AGENT_ENABLED else 'خاموش — /aigroup')}\n\n"
        f"🧪 تست زنده‌ی هر ۴ بخش: /selftest")
    await e.reply(txt)


@client.on(events.NewMessage(pattern=r"^/admin$"))
async def cmd_admin(e):
    if not e.is_private:
        return
    await show_admin(e)


def _users_pick_kb(page=0):
    """لیست کاربران دکمه‌ای (صفحه‌بندی ۸ نفر)"""
    uids = sorted(users_seen.keys(),
                  key=lambda u: users_seen[u].get("seen", 0), reverse=True)
    PER = 8
    pages = max(1, (len(uids) + PER - 1) // PER)
    page = max(0, min(page, pages - 1))
    rows = []
    for uid in uids[page * PER:(page + 1) * PER]:
        u_ = users_seen.get(uid, {})
        nm = (u_.get("name") or uid)[:20]
        exp = SUBS["allowed"].get(uid, 0)
        if int(uid) in ADMIN_IDS:
            badge = "👑"
        elif time.time() < exp:
            badge = "💎"
        elif SUBS.get("lock_all"):
            badge = "🚫"
        else:
            badge = "🟢"
        rows.append([Button.inline(f"{badge} {nm}",
                                   f"a:pick:{uid}".encode())])
    nav = []
    if page > 0:
        nav.append(Button.inline("⬅️", f"a:ulist:{page-1}".encode()))
    nav.append(Button.inline(f"📄 {page+1}/{pages}", "a:noop".encode()))
    if page < pages - 1:
        nav.append(Button.inline("➡️", f"a:ulist:{page+1}".encode()))
    rows.append(nav)
    rows.append([Button.inline("🔙 بازگشت", b"a:sub")])
    return rows


def _sub_admin_menu():
    lock = SUBS.get("lock_all")
    n_keys = sum(1 for k, v in SUBS["keys"].items() if not v.get("used_by"))
    n_ok = sum(1 for u, t_ in SUBS["allowed"].items() if time.time() < t_)
    kb = [
        [Button.inline(f"{'🔒 قفل: روشن' if lock else '🔓 قفل: خاموش'} — تغییر", b"a:sublock")],
        [Button.inline("🔑 کلید ۷ روزه", b"a:key7"), Button.inline("🔑 کلید ۳۰ روزه", b"a:key30")],
        [Button.inline("👥 انتخاب کاربر (محدودیت/اشتراک)", b"a:ulist:0")],
        [Button.inline("📋 فقط اعضای فعال", b"a:sublist")],
        [Button.inline("🔙 بازگشت", b"a:menu")],
    ]
    txt = (f"🔑 مدیریت اشتراک‌ها\n\n"
           f"وضعیت قفل: {'🔒 فقط اعضا' if lock else '🔓 عمومی'}\n"
           f"اعضای فعال: {n_ok} | کلیدهای آزاد: {n_keys}\n\n"
           f"🎛 محدودکردن امکاناتِ هر کاربر:\n"
           f"«/perms آیدی» بفرست تا دکمه‌های تنظیمش بیاد.")
    return txt, kb


async def _perm_menu(uid):
    uid = str(uid)
    p = SUBS["perms"].get(uid, {})
    exp = SUBS["allowed"].get(uid, 0)
    st = "💎 فعال" if time.time() < exp else ("ادمین ✅" if int(uid) in ADMIN_IDS else "ندارد")
    rows = []
    for i in range(0, len(FEATS), 2):
        row = []
        for f_, lbl in FEATS[i:i + 2]:
            on = p.get(f_, True)
            row.append(Button.inline(f"{'✅' if on else '⛔️'} {lbl}",
                                     f"a:perm:{uid}:{f_}".encode()))
        rows.append(row)
    rows.append([Button.inline("➕ مجوز ۳۰ روزه", f"a:grant:{uid}".encode()),
                 Button.inline("🚫 قطع کامل", f"a:rvk:{uid}".encode())])
    rows.append([Button.inline("🔙 لیست کاربران", b"a:ulist:0")])
    u_ = users_seen.get(uid, {})
    nm = u_.get("name") or f"کاربر {uid}"
    txt = (f"🎛 دسترسی‌های {nm} (`{uid}`)\n"
           f"اشتراک: {st}\n\n"
           f"با زدن هر دکمه، همون امکان روشن/خاموش می‌شه.")
    return txt, rows


@client.on(events.NewMessage(pattern=r"^/perms\s+(\d+)$"))
async def cmd_perms(e):
    if e.sender_id not in ADMIN_IDS:
        return
    uid = e.pattern_match.group(1)
    txt, kb = await _perm_menu(uid)
    await e.reply(txt, buttons=kb)


def _admin_kb():
    return [
        [Button.inline("📣 پیام به کاربران", b"a:bc")],
        [Button.inline("🔑 اشتراک‌ها و مجوزها", b"a:sub")],
        [Button.inline("💬 صندوق پشتیبانی", b"a:support")],
        [Button.inline("👥 کاربران", b"a:users")],
        [Button.inline("👑 طلایی — جستجو/فعال‌سازی", b"a:gold")],
        [Button.inline("⚙️ قابلیت‌های روشن/خاموش", b"a:feat")],
        [Button.inline("📡 لیست پروکسی/کانفیگ", b"a:anten")],
        [Button.inline("🖥 اکانت‌خوان (وصل/قطع)", b"a:login")],
        [Button.inline("🏛 تنظیم گروه مای‌ربات", b"a:groupinfo")],
        [Button.inline("🔑 توکن ربات", b"a:token")],
        [Button.inline("🔍 وضعیت فنی کامل", b"a:debug")],
        [Button.inline("🧪 تست هوش مصنوعی", b"a:aitest")],
        [Button.inline("🧹 پاکسازی", b"a:clean"),
         Button.inline("🔄 ری‌استارت", b"a:restart")],
    ]


async def show_admin(e):
    if e.sender_id not in ADMIN_IDS:
        await e.reply("⛔ این بخش فقط برای مدیر رباته.")
        return
    if e.chat_id not in admins_known:
        admins_known.add(e.chat_id)
        save_admins_known()
    kb = _admin_kb()
    await e.reply("🛠 پنل مدیریت — چی کار کنیم؟", buttons=kb, link_preview=False)


@client.on(events.CallbackQuery(pattern=r"^a:"))
async def on_admin_cb(e):
    if e.sender_id not in ADMIN_IDS:
        await e.answer("⛔ فقط مدیر", alert=True)
        return
    data = e.data.decode()[2:]
    chat = e.chat_id
    try:
        await e.answer()
    except Exception:
        pass

    async def say(txt, kb=None):
        try:
            await e.edit(txt[:4000], buttons=kb)
        except Exception:
            await client.send_message(chat, txt[:4000], buttons=kb)

    # ─── 👑 مدیریت طلایی / ⚙️ قابلیت‌ها ───────────────────────
    if data == "gold":
        await say("👑 مدیریت اکانت‌های طلایی\n\n"
                  "روش سریع: روی دکمهٔ زیر بزن، سپس **اسم، @یوزرنیم یا آیدی عددی** را تایپ و ارسال کن.\n"
                  "یا دستور: `/gsearch <عبارت>`\n\n"
                  "در نتایج با یک ضربه، طلایی را فعال/غیرفعال کن؛ 🔄 هم اطلاعات کامل کاربر را نشان می\u200cدهد.",
                  [[Button.switch_inline("🔍 شروع جستجو", "gfind ", same_peer=True)],
                   [Button.inline("📊 آمار و فهرست طلایی‌ها", b"a:gstats")],
                   [Button.inline("🔙 بازگشت", b"a:menu")]])
        return
    if data == "gstats":
        await say(_golds_stats(), [[Button.inline("🔙 مدیریت طلایی", b"a:gold")]])
        return
    if data == "feat":
        t, k = _feat_menu()
        await say(t, k)
        return
    # ─── 📣 پیام به کاربران ────────────────────────────────
    if data == "bc":
        await bc_intro(chat)
        return

    # ─── 🔑 توکن ربات — فقط مدیر، فقط چت خصوصی، با تأیید ───
    if data == "token":
        if not e.is_private:
            await e.answer("⛔ توکن فقط در چت خصوصی فرستاده می‌شود", alert=True)
            return
        await say("🔑 **توکن ربات** (که از BotFather گرفتیم) را همین‌جا بفرستم؟\n\n"
                  "⚠️ اطلاعات حساسی است — بعد از استفاده، این پیام را پاک کن.",
                  [[Button.inline("✅ بله، بفرست", b"a:tokyes")],
                   [Button.inline("🔙 بازگشت", b"a:menu")]])
        return
    if data == "tokyes":
        if not e.is_private:
            await e.answer("⛔ توکن فقط در چت خصوصی فرستاده می‌شود", alert=True)
            return
        try:
            await e.edit("🔑 توکن ارسال شد — پیام پایین 👇", buttons=None)
        except Exception:
            pass
        await client.send_message(
            chat,
            f"🔑 **توکن ربات (BOT_TOKEN):**\n\n`{BOT_TOKEN}`\n\n"
            "⚠️ این را با هیچ‌کس به اشتراک نگذار!\n"
            "اگر لو رفت، در BotFather با /revoke تازه‌اش کن.",
            link_preview=False)
        return

    if data == "sub":
        txt, kb = _sub_admin_menu()
        await say(txt, kb)
    elif data == "sublock":
        SUBS["lock_all"] = not SUBS.get("lock_all")
        save_subs()
        txt, kb = _sub_admin_menu()
        await say(("🔒 قفل شد — فقط اعضا" if SUBS["lock_all"] else "🔓 باز شد — عمومی"), kb)
    elif data in ("key7", "key30"):
        days = 7 if data == "key7" else 30
        key = "STAR-" + secrets.token_hex(3).upper()
        SUBS["keys"][key] = {"days": days, "used_by": None}
        save_subs()
        await say(f"🔑 کلید {days} روزه:\n`{key}`\nبرای کاربر بفرست — با دکمه‌ی 💎 اشتراک فعالش می‌کند.")
    elif data == "sublist":
        lines = ["👥 اعضای فعال:"]
        found = False
        for uid, t_ in SUBS["allowed"].items():
            if time.time() < t_:
                found = True
                u_ = users_seen.get(uid, {})
                nm = u_.get("name") or uid
                lines.append(f"— {nm} (`{uid}`) تا {datetime.fromtimestamp(t_).strftime('%m-%d')}")
        if not found:
            lines.append("— عضوی نیست")
        lines.append("\n🎛 تنظیم تک‌تک امکانات هر کاربر: /perms آیدی")
        await say("\n".join(lines))
    elif data == "anten":
        if not ANTEN_OK:
            await say("⚠️ anten.py نیست.")
            return
        await say("⏳ تازه‌سازی همه‌ی منابع…")
        try:
            px, cfg = await _backfill_all_sources()
        except Exception:
            px, cfg = 0, 0
        lines = [f"📡 مخزن آنتن — تازه‌سازی: +{px} پروکسی، +{cfg} کانفیگ", ""]
        lines.append(f"🔗 پروکسی‌ها ({len(anten.custom['mtproto'])}):")
        for e_ in anten.custom["mtproto"][:25]:
            t_ = anten._admin_note(e_, chat).replace("\n", " | ")
            lines.append(f"• {e_['host']}:{e_['port']}{t_}")
        if len(anten.custom["mtproto"]) > 25:
            lines.append(f"… و {len(anten.custom['mtproto']) - 25} مورد دیگر")
        lines.append("")
        lines.append(f"🛰 کانفیگ‌ها ({len(anten.custom['v2'])}):")
        for e_ in anten.custom["v2"][:25]:
            t_ = anten._admin_note(e_, chat).replace("\n", " | ")
            lines.append(f"• {e_['proto']} {e_['host']}:{e_['port']}{t_}")
        if len(anten.custom["v2"]) > 25:
            lines.append(f"… و {len(anten.custom['v2']) - 25} مورد دیگر")
        lines.append("")
        lines.append("اسکن دوباره: /scan | افزودن کانال: /addsource @channel")
        await say("\n".join(lines))
    elif data == "support":
        un = [s_ for s_ in SUPPORT_INBOX if not s_.get("done")]
        if not un:
            await say("💬 صندوق پشتیبانی خاله — پیام خوانده‌نشده نیست ✅")
        else:
            rows = [[Button.inline(
                f"✉️ {s_['name'][:16]}: {s_['text'][:28]}",
                f"a:spv:{s_['i']}".encode())] for s_ in un[-8:]]
            rows.append([Button.inline("🔄 بازخوانی", b"a:support")])
            await say(f"💬 صندوق پشتیبانی — {len(un)} پیام خوانده‌نشده:", rows)
    elif data.startswith("spv:"):
        sid = int(data.split(":", 1)[1])
        s_ = next((x for x in SUPPORT_INBOX if x["i"] == sid), None)
        if not s_:
            await say("پیام پیدا نشد (قدیمی).")
        else:
            dt_ = datetime.fromtimestamp(s_["ts"]).strftime("%m-%d %H:%M")
            lk = f"https://t.me/{s_['uname']}" if s_.get("uname") else f"tg://user?id={s_['uid']}"
            kb = [
                [Button.inline("↩️ پاسخ", f"a:spr:{sid}".encode()),
                 Button.url("🔗 چت", lk)],
                [Button.inline("✔️ خواندم", f"a:spd:{sid}".encode()),
                 Button.inline("🔙 صندوق", b"a:support")],
            ]
            await say(f"✉️ از {s_['name']} — {dt_}\n\n{s_['text']}", kb)
    elif data.startswith("spr:"):
        sid = int(data.split(":", 1)[1])
        s_ = next((x for x in SUPPORT_INBOX if x["i"] == sid), None)
        if not s_:
            await say("پیدا نشد.")
        else:
            flow[chat] = {"kind": "reply", "target": s_["uid"], "sid": sid, "ts": time.time()}
            await say("✍️ متن پاسخت رو بنویس تا بره برای کاربر:")
    elif data.startswith("spd:"):
        sid = int(data.split(":", 1)[1])
        for x in SUPPORT_INBOX:
            if x["i"] == sid:
                x["done"] = True
        save_support()
        await say("✔️ خوانده‌شده علامت خورد.")
    elif data == "noop":
        pass
    elif data.startswith("ulist:"):
        pg = int(data.split(":", 1)[1])
        await say("👥 کاربر را انتخاب کن (👑ادمین 💎اشتراک 🟢آزاد 🚫قفل):",
                  _users_pick_kb(pg))
    elif data.startswith("pick:"):
        uid = data.split(":", 1)[1]
        txt, kb = await _perm_menu(uid)
        await say(txt, kb)
    elif data.startswith("perm:"):
        parts = data.split(":", 2)
        uid, feat = parts[1], parts[2]
        p = SUBS["perms"].setdefault(uid, {})
        p[feat] = not p.get(feat, True)
        save_subs()
        txt, kb = await _perm_menu(uid)
        await say(txt, kb)
    elif data.startswith("grant:"):
        uid = data.split(":", 1)[1]
        SUBS["allowed"][uid] = time.time() + 30 * 86400
        save_subs()
        try:
            await client.send_message(int(uid), "✅ ۳۰ روز دسترسی به ربات به شما داده شد 🎉")
        except Exception:
            pass
        txt, kb = await _perm_menu(uid)
        await say(f"✅ {uid} → ۳۰ روز")
    elif data.startswith("rvk:"):
        uid = data.split(":", 1)[1]
        SUBS["allowed"].pop(uid, None)
        SUBS["perms"][uid] = {f_: False for f_, _ in FEATS}
        save_subs()
        await say(f"🚫 دسترسی {uid} قطع شد (همه‌ی امکانات خاموش).")
    elif data == "users":
        rows = []
        items = sorted(users_seen.items(),
                       key=lambda kv: kv[1].get("seen", 0), reverse=True)[:20]
        for uid, u_ in items:
            nm = u_.get("name") or uid
            regs = len(bargha.regions.get(uid, []))
            reg_txt = f" | 📍{regs}" if regs else ""
            rows.append([Button.inline(f"{nm}{reg_txt}", f"a:pick:{uid}".encode())])
        rows.append([Button.inline("🖥 اکانت‌های متصل (📸)", b"a:sdlist")])
        rows.append([Button.inline("🔙 بازگشت", b"a:menu")])
        await say(f"👥 کاربران ({len(users_seen)}) — برای تنظیمات، انتخاب کن:", rows)
    elif data == "sdlist":
        if not sd_clients:
            await say("هیچ کاربری اکانتش را وصل نکرده.")
        else:
            rows = []
            for uid, uc in list(sd_clients.items()):
                try:
                    me = await uc.get_me()
                    nm = getattr(me, "first_name", None) or uid
                    conn = "🟢" if uc.is_connected() else "🔴"
                except Exception:
                    nm, conn = str(uid), "🔴"
                rows.append([Button.inline(f"{conn} {nm} — اطلاعات و عکس‌ها",
                                           f"a:sdph:{uid}".encode())])
            rows.append([Button.inline("🔙 بازگشت", b"a:users")])
            await say(f"🖥 اکانت‌های متصل: {len(sd_clients)}", rows)
    elif data.startswith("sdph:"):
        uid = int(data.split(":", 1)[1])
        uc = sd_clients.get(uid)
        if not uc:
            await say("متصل نیست.")
        else:
            try:
                me = await uc.get_me()
                nm = getattr(me, "first_name", None) or uid
                ph = str(getattr(me, "phone", "") or "")
                ph_f = ("+" + ph.lstrip("+")) if ph else "—"
                un = getattr(me, "username", None)
                photos = await uc.get_profile_photos("me", limit=None)
                await say(f"👤 {nm}\n🆔 {uid} | 📞 {ph_f}\n"
                          f"🔗 {'@' + un if un else 'ندارد'}\n🖼 {len(photos)} عکس")
                for i, pho in enumerate(photos[:3], 1):
                    p_ = await uc.download_media(pho, file=f"/tmp/pp_{uid}_{i}.jpg")
                    if p_ and Path(p_).stat().st_size > 0:
                        await client.send_file(chat, p_, caption=f"👤 {i} — {nm}")
                        Path(p_).unlink(missing_ok=True)
            except Exception as ex:
                await say(f"❌ {str(ex)[:100]}")
    elif data == "login":
        if user_client and user_client.is_connected():
            try:
                await user_client.disconnect()
            except Exception:
                pass
            SESSION_FILE.unlink(missing_ok=True)
            globals()["user_client"] = None
            await say("🔌 اکانت‌خوان قطع شد.")
        else:
            login_flow[chat] = {"step": "phone"}
            await client.send_message(chat, "🔐 شماره‌ی اکانت‌خوان را با +98 بفرست:")
    elif data == "groupinfo":
        await say(
            "🏛 گروه مای‌ربات:\n"
            f"• ذخیره: {GROUP_CFG.get('backup') or '—'}\n"
            f"• کم‌حجم: {GROUP_CFG.get('compress') or '—'}\n"
            f"• اطلاعیه: {GROUP_CFG.get('announce') or '—'}\n"
            f"• عکس‌ها: {GROUP_CFG.get('media') or '—'}\n"
            f"• تگ فایل‌ها: {GROUP_CFG.get('tag') or '—'}\n\n"
            "⚠️ ثبت هر بخش فقط داخل خود گروه و در تاپیک مربوطه:\n"
            "/setbackup /setcomp /setann /setmedia")
    elif data == "debug":
        ub = ""
        try:
            if user_client and user_client.is_connected():
                me_ = await user_client.get_me()
                ph = str(getattr(me_, "phone", "") or "")
                ub = ("\n👁 اکانت‌خوان: " + str(getattr(me_, "first_name", "?"))
                      + " (+" + (ph[:4] + "**" + ph[-2:] if len(ph) > 6 else "**") + ")")
            else:
                ub = "\n👁 اکانت‌خوان: قطع"
        except Exception:
            ub = "\n👁 اکانت‌خوان: قطع"
        up = int(time.time() - PROCESS_START)
        await say(
            f"🔍 وضعیت فنی ({BOT_VERSION})\n\n"
            f"🟢 روشن: {up // 3600}ساعت {(up % 3600) // 60}دقیقه{ub}\n"
            f"📍 منطقه‌ها: {len(bargha.regions)} | 👥 کاربران: {len(users_seen)}\n"
            f"💾 حافظه تلگرامی: {'✅' if tgstore.stats()['msg_found'] else '❌'}\n"
            f"🏛 گروه: {sum(1 for s in GROUP_CFG.values() if s)}/۴ بخش\n"
            f"🔑 کوکی یوتیوب: {'✅' if (YT_OK and ytube.has_cookies()) else '❌'}\n"
            f"🛰 کانفیگ: {len(anten.custom['v2']) if ANTEN_OK else 0} | "
            f"📡 پروکسی: {len(anten.custom['mtproto']) if ANTEN_OK else 0}\n"
            f"📥 مدیا: {'همه' if SD_SETTINGS.get('capture_mode') == 'all' else 'فقط خودموف'}"
            f" | 👥 متصل‌ها: {len(sd_clients)}\n"
            f"💬 پشتیبانی خوانده‌نشده: {sum(1 for s_ in SUPPORT_INBOX if not s_.get('done'))}")
    elif data == "aitest":
        await say("🧪 در حال تست زنده…")
        try:
            rep = await asyncio.to_thread(aichat.diagnose)
            await say(rep[:4000])
        except Exception as ex:
            await say(f"❌ {str(ex)[:150]}")
    elif data == "stats":
        await say("🔍 وضعیت کامل در دکمه‌ی «وضعیت فنی»", None)
    elif data == "clean":
        freed, count = 0, 0
        now = time.time()
        for p in list(WORKDIR.glob("*")) + list(ARCH_DIR.glob("*")):
            try:
                if p.is_file() and now - p.stat().st_mtime > 600:
                    freed += p.stat().st_size
                    p.unlink()
                    count += 1
            except OSError:
                pass
        await say(f"🧹 {count} فایل حذف شد؛ {fmt_size(freed)} آزاد شد.")
    elif data == "menu":
        await say("🛠 پنل مدیریت — چی کار کنیم؟", _admin_kb())
    elif data == "restart":
        await say("🔄 در حال راه‌اندازی مجدد…")
        await asyncio.sleep(1)
        os._exit(7)


def build_latest_text(regs) -> str:
    """یک پیام واحد: همه‌ی قطعی‌های امروز و آینده‌ی همه‌ی منطقه‌های کاربر"""
    t0 = bargha.iran_now().replace(hour=0, minute=0, second=0, microsecond=0)
    lines = ["🔎 قطعی‌های پیش‌روی منطقه‌های تو:", ""]
    any_hit = False
    for r in regs:
        hits = []
        for c in bargha.state.get("corpus", []):
            for s in bargha.match_slots(c, r["phrase"]):
                if s.get("date") and s["date"] >= t0:
                    hits.append(s)
        hits.sort(key=lambda s: (s["date"], s["start_h"]))
        seen_h = set()
        for s in hits[:6]:
            k = (s["date"], s["start_h"], s["end_h"])
            if k in seen_h:
                continue
            seen_h.add(k)
            any_hit = True
            lines.append(f"📍 «{r['name']}»")
            lines.append(f"   {bargha.slot_when(s)}")
    if not any_hit:
        return ""
    lines.append("")
    lines.append("(برنامه‌ی احتمالی — ممکنه تغییر کنه)")
    return "\n".join(lines)


def build_admin_text() -> str:
    lines = ["🛠 پنل مدیریت — وضعیت کارها", ""]

    active = [(cid, j) for cid, j in jobs.items() if j.get("status") == "در حال فشرده‌سازی"]
    waiting = [(cid, j) for cid, j in jobs.items()
               if j.get("status") not in ("در حال فشرده‌سازی", "تمام شد", "ناموفق", None)]

    if active:
        lines.append("🔄 در حال انجام:")
        for cid, j in active:
            pct = int(j.get("pct", 0) * 100)
            mm = int((time.time() - j.get("started", time.time())) // 60)
            lines.append(
                f"— {j.get('name', '?')[:32]} ({fmt_size(j.get('size'))}) "
                f"| {MODE_LABEL.get(j.get('mode', ''), '')} | {pct}% | {mm}دقیقه"
            )
        lines.append("")
    if waiting:
        lines.append("⏳ در انتظار انتخاب کاربر:")
        for cid, j in waiting[-5:]:
            lines.append(f"— {j.get('name', '?')[:32]} ({fmt_size(j.get('size'))})")
        lines.append("")
    if not active and not waiting:
        lines.append("الان کاری در جریان نیست.")
        lines.append("")

    lines.append(f"🕐 آخرین کارها (از {len(history)}):")
    if history:
        for h in list(history)[-8:][::-1]:
            lines.append(
                f"— {h['name'][:26]}: {h['old']} ← {h['new']} ({h['pct']}٪)\n"
                f"   👤 {h.get('uname') or h.get('chat', '?')} | "
                f"[بازکردن چت]({h.get('link', '')}) | 📎 /get {h.get('code', '—')}"
            )
    else:
        lines.append("— هنوز کاری ثبت نشده.")

    lines.append("")
    lines.append(f"👥 کاربران: {len(users_seen)} → لیست کامل: /users")
    lines.append("📎 دریافت فایل کم‌حجم کاربر: /get کد")
    return "\n".join(lines)


@client.on(events.NewMessage(pattern=r"^/reply\s+(\d+)\s+([\s\S]+)$"))
async def cmd_reply(e):
    if not e.is_private:
        return
    if e.sender_id not in ADMIN_IDS:
        await e.reply("⛔ فقط مدیر.")
        return
    target = int(e.pattern_match.group(1))
    text = e.pattern_match.group(2).strip()
    try:
        await client.send_message(target, f"📩 پاسخ پشتیبانی:\n\n{text[:3000]}")
        await e.reply("✅ ارسال شد.")
    except Exception as ex:
        await e.reply(f"❌ ارسال نشد: {str(ex)[:100]}")


@client.on(events.NewMessage(pattern=r"^/users$"))
async def cmd_users(e):
    if not e.is_private:
        return
    if e.sender_id not in ADMIN_IDS:
        await e.reply("⛔ فقط مدیر.")
        return
    if not users_seen:
        await e.reply("هنوز کاربری ثبت نشده.")
        return
    lines = [f"👥 کاربران ربات ({len(users_seen)}):", ""]
    items = sorted(users_seen.items(), key=lambda kv: kv[1].get("seen", 0), reverse=True)
    for uid, u in items[:50]:
        ago = int((time.time() - u.get("seen", 0)) // 60)
        ago_txt = f"{ago // 60}ساعت پیش" if ago >= 60 else f"{ago}دقیقه پیش"
        nm = u.get("name", "?")
        un = u.get("uname")
        who = f"@{un}" if un else f"`{uid}`"
        regs = [r.get("name", "?") for r in bargha.regions.get(uid, [])]
        reg_txt = f" | 📍 {', '.join(regs)}" if regs else " | بدون منطقه"
        lines.append(f"— {nm} | {who} | {u.get('jobs', 0)} کار | {ago_txt}{reg_txt}")
        if un:
            lines.append(f"   🔗 https://t.me/{un}")
    await e.reply("\n".join(lines), link_preview=False)


@client.on(events.NewMessage(pattern=r"^/udel\s+(\d+)\s+(\d+)$"))
async def cmd_udel(e):
    if not e.is_private:
        return
    """حذف منطقه‌ی یک کاربر توسط مدیر: /udel آیدی شماره"""
    if e.sender_id not in ADMIN_IDS:
        await e.reply("⛔ فقط مدیر.")
        return
    uid, idx = e.pattern_match.group(1), int(e.pattern_match.group(2)) - 1
    lst = bargha.regions.get(uid, [])
    if idx < 0 or idx >= len(lst):
        await e.reply("چنین شماره‌ای نیست. لیست: /users")
        return
    nm = lst[idx].get("name", "?")
    lst.pop(idx)
    if lst:
        bargha.regions[uid] = lst
    else:
        bargha.regions.pop(uid, None)
    bargha.save_regions()
    await e.reply(f"🗑 منطقه «{nm}» کاربر {uid} حذف شد.")


@client.on(events.NewMessage(pattern=r"^/get\s+([A-Za-z0-9]+)$"))
async def cmd_get(e):
    if not e.is_private:
        return
    if e.sender_id not in ADMIN_IDS:
        await e.reply("⛔ فقط مدیر.")
        return
    code = e.pattern_match.group(1).lower()
    files = list(ARCH_DIR.glob(f"{code}.*"))
    if not files:
        await e.reply("❌ فایلی با این کد پیدا نشد (کد اشتباهه یا بیشتر از ۴۸ ساعت گذشته).")
        return
    h = next((x for x in history if x.get("code") == code), {})
    cap = (f"📎 فایل آرشیو `{code}`\n"
           f"🎬 {h.get('name', '?')}\n"
           f"🗜 {h.get('old', '?')} ← {h.get('new', '?')}\n"
           f"👤 کاربر: {h.get('uname') or h.get('chat', '?')}")
    await client.send_file(e.chat_id, str(files[0]), caption=cap,
                           supports_streaming=True)
    await e.reply("✅ فایل بالا رفت.")


@client.on(events.NewMessage(func=lambda e: e.message.file is None
                             and (e.text or "").strip()
                             and not (e.text or "").startswith("/")))
async def on_text(e):
    if not e.is_private:
        return
    # مدیر پیام پشتیبان را فرستاد/فوروارد کرد → بازیابی خودکار
    if e.sender_id in ADMIN_IDS and "DATA|" in (e.text or "") and "🗂" in (e.text or ""):
        tgstore.import_payload(e.text)
        rep = await apply_restore(reconnect_userbot=True)
        await e.reply(rep)
        return
    if e.chat_id in login_flow and e.sender_id in ADMIN_IDS:
        await handle_login_text(e)
        return
    st_sd = flow.get(e.chat_id)
    if st_sd and st_sd.get("kind") == "sd":
        await handle_sd_login_text(e)
        return
    if st_sd and st_sd.get("kind") == "dl":
        await handle_dl_text(e)
        return
    if st_sd and st_sd.get("kind") == "tag":
        await handle_tag_text(e)
        return
    if st_sd and st_sd.get("kind") == "ai":
        await handle_ai_text(e)
        return
    if st_sd and st_sd.get("kind") == "finder":
        await handle_finder_text(e)
        return
    if st_sd and st_sd.get("kind") == "insta":
        await handle_insta_text(e)
        return
    if st_sd and st_sd.get("kind") == "stt":
        await handle_stt_text(e)
        return
    if st_sd and st_sd.get("kind") == "link":
        await handle_link_text(e)
        return
    if st_sd and st_sd.get("kind") == "sub":
        t_c = (e.text or "").strip()
        if t_c == BTN_SUB or t_c.startswith("/"):
            st_sd["ts"] = time.time()
            await e.reply("🔑 کلیدت رو بفرست (مثل STAR-1A2B3C) — یا «لغو»")
            return
        t_c = t_c.upper()
        flow.pop(e.chat_id, None)
        info = SUBS["keys"].get(t_c)
        if not info:
            await e.reply("❌ کلید نامعتبره. دوباره: 💎 اشتراک")
        elif info.get("used_by") and info["used_by"] != str(e.sender_id):
            await e.reply("❌ این کلید قبلاً استفاده شده.")
        else:
            base = max(time.time(), SUBS["allowed"].get(str(e.sender_id), 0))
            SUBS["allowed"][str(e.sender_id)] = base + info["days"] * 86400
            info["used_by"] = str(e.sender_id)
            save_subs()
            await e.reply(
                "🎉 اشتراکت تا "
                f"{datetime.fromtimestamp(SUBS['allowed'][str(e.sender_id)]).strftime('%Y-%m-%d')} فعال شد!")
        return
    # ─── ادامه‌ی گفتگو با دستیار /m: جواب کوتاه به سوال قبلی‌اش ───
    if (e.sender_id in ADMIN_IDS and e.is_private and AGENT_ENABLED
            and not (e.text or "").startswith("http")):
        mctx = MAGENT_CTX.get(str(e.chat_id))
        if isinstance(mctx, dict):
            t_fu = (e.text or "").strip()
            pend_q = mctx.get("awaiting")
            has_choices = bool(mctx.get("last_choices"))
            if ((pend_q and time.time() - pend_q.get("ts", 0) < 600) or has_choices) \
                    and 0 < len(t_fu) <= 60 and not t_fu.startswith("/"):
                await note_user(e)
                st = await e.reply("🧠 …")

                async def _fsend(t):
                    try:
                        await st.edit(str(t)[:4000])
                    except Exception:
                        await e.reply(str(t)[:4000])

                try:
                    await _magent_pipeline(e.chat_id, t_fu, _fsend, None)
                except Exception as ex:
                    try:
                        await st.edit(f"❌ {str(ex)[:150]}")
                    except Exception:
                        pass
                return
    if e.message.fwd_from:
        return
    # مدیر: افزودن پروکسی تلگرام با فرستادن لینک
    if e.sender_id in ADMIN_IDS and "proxy?server=" in (e.text or ""):
        entry = anten.add_mtproto(e.text)
        if entry:
            await e.reply(f"✅ پروکسی تلگرام اضافه شد: {entry['host']}:{entry['port']}\n"
                          f"الان توی «📡 آنتن → پروکسی تلگرام» برای همه هست.")
        else:
            await e.reply("❌ لینک پروکسی درست نیست؛ باید t.me/proxy?server=...&port=...&secret=... باشه.")
        return
    t = (e.text or "").strip()
    # دکمه‌های کیبورد هیچ‌وقت به‌عنوان پیامِ جریان (پشتیبانی و…) حساب نمی‌شوند
    if t in (BTN_HELP, BTN_STATUS, BTN_ADMIN, BTN_BARGH, BTN_SUPPORT, BTN_SELFDES, BTN_AI, BTN_YT, BTN_FINDER, BTN_SUB, BTN_INSTA, BTN_STT, BTN_LINK, BTN_DL, BTN_TAG):
        return
    if t == BTN_ANTEN:
        await btn_anten(e)
        return
    # ═══ قفل اشتراک: وقتی قفل است، فقط راهنما/پشتیبانی/اشتراک آزاد است ═══
    FREE_TXT = (BTN_HELP, BTN_SUPPORT, BTN_SUB, "✖ خروج از یوتیوب", "لغو", "پایان")
    if (SUBS.get("lock_all") and e.sender_id not in ADMIN_IDS
            and t not in FREE_TXT and not t.startswith("/start")
            and not t.startswith("/help") and not t.startswith("/redeem")
            and not t.startswith("/mysub") and not t.startswith("/id")):
        await e.reply(_need_sub_reply(e.chat_id),
                      buttons=[[Button.inline("🔑 فعال‌سازی اشتراک", b"sub:start")]])
        try:   # خبر به مدیر — یک بار در ساعت برای هر کاربر
            u_ = users_seen.get(str(e.sender_id), {})
            nm = u_.get("name") or "?"
            key = f"want_{e.sender_id}"
            if time.time() - SUBS.get(key, 0) > 3600:
                SUBS[key] = time.time()
                for aid in ADMIN_IDS:
                    await client.send_message(
                        int(aid),
                        f"🔔 {nm} (`{e.sender_id}`) می‌خواهد از ربات استفاده کند.\n"
                        f"مجوز ۳۰ روزه: /grant {e.sender_id} 30\n"
                        f"تنظیم تک‌تک امکانات: /perms {e.sender_id}")
        except Exception:
            pass
        return
    # خروج کیبوردی از اتاق یوتیوب — اول از همه، همیشه کار می‌کند
    if t == "✖ خروج از یوتیوب":
        YT_FLOW.pop(e.chat_id, None)
        await e.reply("🎬 اتاق یوتیوب بسته شد — به منوی اصلی برگشتی 👇",
                      buttons=kb_for(e.chat_id))
        return
    # انقضای جریان‌های قدیمی (۱۰ دقیقه) — به‌جز گفتگوی AI (تا کاربر خودش ببندد)
    st_flow = flow.get(e.chat_id)
    if st_flow and st_flow.get("kind") == "ai":
        st_flow["ts"] = time.time()   # گفتگوی AI هرگز با زمان بسته نمی‌شود
    elif st_flow and time.time() - st_flow.get("ts", time.time()) > 600:
        flow.pop(e.chat_id, None)
        st_flow = None
    if st_flow:
        await handle_flow_text(e)
        return
    await note_user(e)
    # یوتیوب: فقط داخل بخش خودش (تا وقتی کاربر نبسته)
    if YT_OK and YT_FLOW.get(e.chat_id):
        if ytube.is_yt_url(e.text or ""):
            url = ytube.extract_url(e.text)
            status = await e.reply("⏳ اطلاعات ویدیو…")
            stage = {"txt": "شروع"}

            def _st(s_):
                stage["txt"] = s_

            task = asyncio.ensure_future(ytube.fetch_info(url, progress=_st))
            t0 = time.time()
            info = None
            try:
                while True:
                    _done, _ = await asyncio.wait({task}, timeout=7)
                    if _done:
                        info = task.result()
                        break
                    el = int(time.time() - t0)
                    try:
                        await status.edit(f"⏳ اطلاعات ویدیو… {el}ث\n🔄 {stage['txt']}")
                    except Exception:
                        pass
                    if el >= 63:
                        task.cancel()
                        break
                if info:
                    title = (info.get("title") or "?")[:60]
                    m_, s_ = divmod(int(info.get("duration") or 0), 60)
                    vid = info.get("id") or url.split("=")[-1].split("/")[-1][:11]
                    await status.delete()
                    await e.reply(f"🎬 {title}\n⏱ {m_}:{s_:02d}\n\nکیفیت:",
                                  buttons=_yt_quality_kb(vid))
                else:
                    # اطلاعات نیامد — ولی دکمه‌های دانلود کار می‌کنند
                    m_vid = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url)
                    vid = m_vid.group(1) if m_vid else ""
                    if vid:
                        await status.delete()
                        await e.reply(
                            "⚠️ اطلاعات ویدیو کند آمد — ولی می‌تونی مستقیم دانلود کنی:",
                            buttons=_yt_quality_kb(vid))
                    else:
                        await safe_edit(status, "😖 یوتیوب الان به سرور ما گیر داد — بعداً دوباره.")
            except Exception as ex:
                await safe_edit(status, f"❌ {str(ex)[:150]}")
            return
        if not re.search(r"https?://", e.text or "") and len(t) >= 2:
            status = await e.reply("🔍 دارم توی یوتیوب می‌گردم… (تا ۲ دقیقه)")
            try:
                results = await asyncio.wait_for(ytube.search(t, 6), timeout=110)
                if results:
                    rows = []
                    for i, r in enumerate(results):
                        mm, ss = divmod(int(r.get("dur") or 0), 60)
                        rows.append([Button.inline(
                            f"{i+1}. {r['title'][:38]} ({mm}:{ss:02d})",
                            f"y:s:{r['id']}".encode())])
                    await status.delete()
                    await e.reply("🎬 نتایج — روی هرکدوم بزن:", buttons=rows)
                else:
                    await safe_edit(status, "😕 چیزی پیدا نشد.")
            except Exception as ex:
                s_ = str(ex).lower()
                if "timeout" in s_ or "timed out" in s_ or "sign in" in s_ or "403" in s_:
                    await safe_edit(status,
                        "😖 یوتیوب الان به سرور ما گیر داد و جواب نداد.\n"
                        "چند دقیقه بعد دوباره امتحان کن — هر بار مسیر متفاوتی امتحان می‌شه.")
                else:
                    await safe_edit(status, f"❌ {str(ex)[:120]}")
            return
    await e.reply(
        "🤖 فایلت رو بفرست تا کم‌حجمش کنم 🗜\n"
        "راهنمای کامل: /help",
        buttons=kb_for(e.chat_id),
    )


def _save_yt_cookies(text: str) -> bool:
    """ذخیره‌ی کوکی یوتیوب (Netscape format) — در دو فایل: فعال + پشتیبان JSON"""
    if "# netscape" not in text.lower() and ".youtube.com" not in text:
        return False
    Path("/tmp/yt_cookies.txt").write_text(text, encoding="utf-8")
    Path("/tmp/yt_cookie.json").write_text(
        json.dumps({"data": text}, ensure_ascii=False), encoding="utf-8")
    persist.mark_dirty()
    tgstore.mark_dirty()
    return True


def _restore_yt_cookies():
    """بعد از ری‌استارت: کوکی از پشتیبان JSON برمی‌گردد"""
    try:
        d = json.loads(Path("/tmp/yt_cookie.json").read_text())
        if d.get("data") and not Path("/tmp/yt_cookies.txt").exists():
            Path("/tmp/yt_cookies.txt").write_text(d["data"], encoding="utf-8")
            log.info("yt: کوکی از پشتیبان بازیابی شد")
    except Exception:
        pass


# ═══════════════ 🎙 ویس به متن — بخش جداگانه ═══════════════

STT_FLOW = {}      # chat_id → {"ts":..., "waiting": bool}


@client.on(events.NewMessage(pattern=r"^🎙 ویس به متن$"))
async def btn_stt(e):
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    if not AI_OK:
        await e.reply("⚠️ ماژول هوش مصنوعی آماده نیست.")
        return
    flow[e.chat_id] = {"kind": "stt", "step": "wait", "ts": time.time()}
    await e.reply(
        "🎙 بخش ویس به متن\n\n"
        "• ویس / ویدیو-پیام / فایل صوتی بفرست → متن فارسی‌اش می‌آید\n"
        "  (ویس که مستقیم بفرستی هم خودکار جواب می‌گیری — این بخش فقط برای شروع تازه است)\n\n"
        "پایان: «پایان»")


async def handle_stt_text(e):
    st = flow.get(e.chat_id) or {}
    t = (e.text or "").strip()
    if t in ("پایان", "/end", "لغو"):
        flow.pop(e.chat_id, None)
        await e.reply("🎙 بسته شد ✅", buttons=kb_for(e.chat_id))
        return
    if t.startswith("/"):
        await e.reply("فایل صوتی بفرست — یا «پایان»")
        return
    await e.reply("🎙 فقط ویس/فایل صوتی بپذیرم — فایل بفرست یا «پایان»")


# ═══════════════ 🔗 فایل به لینک — بخش جداگانه ═══════════════

LINK_FLOW = {}     # chat_id → {"ts":...}


@client.on(events.NewMessage(pattern=r"^🔗 فایل به لینک$"))
async def btn_link(e):
    await note_user(e)
    if not is_allowed_feat(e.chat_id, "ai"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    if not TOOLS_OK:
        await e.reply("⚠️ ماژول tools.py آماده نیست.")
        return
    flow[e.chat_id] = {"kind": "link", "step": "wait", "ts": time.time()}
    await e.reply(
        "🔗 بخش فایل به لینک\n\n"
        "• هر فایلی بفرست (عکس/ویدیو/آهنگ/زیپ/هرچی تا ۲۰۰ مگ)\n"
        "  → لینک دائمی (یا ۷۲ ساعته) می‌گیری\n\n"
        "پایان: «پایان»")


async def handle_link_text(e):
    st = flow.get(e.chat_id) or {}
    t = (e.text or "").strip()
    if t in ("پایان", "/end", "لغو"):
        flow.pop(e.chat_id, None)
        await e.reply("🔗 بسته شد ✅", buttons=kb_for(e.chat_id))
        return
    if t.startswith("/"):
        await e.reply("فایل بفرست — یا «پایان»")
        return
    await e.reply("📎 فقط فایل می‌پذیرم — فایل بفرست یا «پایان»")


# ═══════════════ 🎙 ویس → متن فارسی (برای همه‌ی کاربران) ═══════════════

async def _stt_flow(e, path, kind_label="ویس", delete_after=True):
    """مسیر مشترک تبدیل به متن: دانلود شده → (در صورت نیاز تبدیل) → Whisper"""
    chat_id = e.chat_id if hasattr(e, "chat_id") else e
    status = None
    try:
        if hasattr(e, "reply"):
            status = await e.reply("🎙 دارم گوش می‌دم…")
    except Exception:
        status = None
    try:
        import tools as _tl
        p_ = path
        # ویس تلگرام (ogg/opus) مستقیم به Whisper — بقیه: استخراج mp3
        if not str(path).endswith((".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav")):
            p_ = await asyncio.to_thread(_tl.audio_extract, path)
            if delete_after:
                Path(path).unlink(missing_ok=True)
        # 🔧 Groq خطا داد یا خالی بود؟ → فالبک HF (مستقل از Groq)
        text = None
        try:
            text = await asyncio.to_thread(aichat.transcribe, p_)
        except Exception:
            text = None
        if not text:
            try:
                p2 = await asyncio.to_thread(_tl.audio_extract, p_, "mp3", True)
                Path(p_).unlink(missing_ok=True)
                p_ = p2
                text = await asyncio.to_thread(aichat.transcribe, p_)
            except Exception:
                text = None
        if not text:
            try:
                text = await asyncio.to_thread(aichat._hf_whisper, p_)
            except Exception:
                text = None
        Path(p_).unlink(missing_ok=True)
        if text is None:
            msg = ("🎙 برای تبدیل ویس به متن، کلید هوش مصنوعی (Groq) لازم است.\n"
                   "مدیر: /aitest را بزن.")
        elif text:
            dur = ""
            try:
                if hasattr(e, "message") and e.message:
                    d_ = e.message.duration if hasattr(e.message, "duration") else None
                    if d_:
                        dur = f" ⏱ {int(d_)}ث"
            except Exception:
                pass
            msg = f"🎙 متن {kind_label}{dur}:\n\n{text[:3800]}"
        else:
            msg = "🎙 صدایی واضح توی این ویس پیدا نکردم — ویس بهتری بفرست."
        if status:
            try:
                await status.edit(msg)
                return
            except Exception:
                pass
        # fallback ارسال
        if hasattr(e, "reply"):
            await e.reply(msg)
        elif hasattr(e, "send_message"):
            await e.send_message(chat_id, msg)
    except Exception as ex:
        try:
            if status:
                await status.edit(f"❌ {str(ex)[:150]}")
            elif hasattr(e, "reply"):
                await e.reply(f"❌ {str(ex)[:150]}")
        except Exception:
            pass


# 🔧 باگ تاریخی فیکس شد: دکوریتور قبلاً به‌اشتباه روی _save_yt_cookies بود
# و on_file هیچ‌وقت ثبت نمی‌شد → فشرده‌سازی کاملاً مرده بود!
@client.on(events.NewMessage(func=lambda e: e.message.file is not None))
async def on_file(e):
    if not e.is_private:
        return
    await note_user(e)
    chat_id = e.chat_id
    f = e.message.file
    size = f.size or 0

    # ─── بخش «🔗 فایل به لینک» فعال است؟ این فایل → لینک ───
    _f_active = flow.get(chat_id) or {}
    if _f_active.get("kind") == "link" and TOOLS_OK:
        try:
            if size > ACCEPT_MAX:
                await e.reply(f"⚠️ سقف عملی {CAP_TEXT} است؛ تلگرام بالاتر از ~۲ گیگ را منتقل نمی‌کند.")
                return
            st = await e.reply("⏳ دارم آپلود می‌کنم…")
            path = await e.message.download_media(file="/tmp/link_up")
            import tools as _tl
            res = await asyncio.to_thread(_tl.upload_file_link, path)
            Path(path).unlink(missing_ok=True)
            await st.edit(
                f"🔗 لینک آماده! ({res['kind']})\n\n"
                f"📄 {(f.name or 'فایل')[:40]}\n"
                f"🔗 {res['url']}",
                buttons=[[Button.url("🔗 باز کردن", res["url"])],
                         [Button.switch_inline("📤 اشتراک", res["url"])]],
                link_preview=False)
        except Exception as ex:
            await e.reply(f"❌ {str(ex)[:150]}")
        return

    # ─── بخش «🎙 ویس به متن» فعال است؟ هر صوت/ویدیویی → متن ───
    if _f_active.get("kind") == "stt" and AI_OK:
        try:
            if e.message.voice or getattr(e.message, "video_note", None) or e.message.audio \
                    or (f.name or "").lower().endswith((".mp3",".m4a",".ogg",".wav",".opus",".flac",".mp4",".mkv",".webm")):
                path = await e.message.download_media(file="/tmp/stt_flow_f")
                kind = ("ویس" if e.message.voice else
                        "ویدیو-پیام" if getattr(e.message, "video_note", None) else
                        "صدا" if e.message.audio else "فایل صوتی")
                await _stt_flow(e, path, kind)
            else:
                await e.reply("🎙 این فایل صوتی/ویدیویی نیست — فایل صوتی بفرست یا «پایان»")
        except Exception:
            pass
        return

    # 🎙 ویس هر کاربری → متن فارسی خودکار (Whisper)
    # (ویس ادمین وقتی دستیار روشن است → دستیار هوشمند هندل می‌کند، نه اینجا)
    _voice_to_agent = (e.sender_id in ADMIN_IDS and AGENT_ENABLED
                       and user_client and user_client.is_connected())
    if e.message.voice and AI_OK and not _voice_to_agent and is_allowed_feat(chat_id, "ai"):
        try:
            path = await e.message.download_media(file="/tmp/stt_voice.ogg")
            await _stt_flow(e, path, "ویس")
        except Exception:
            pass
        return
    # 🎥 ویدیو-پیام (بطره) → متن فارسی
    if getattr(e.message, "video_note", None) and AI_OK and is_allowed_feat(chat_id, "ai"):
        try:
            path = await e.message.download_media(file="/tmp/stt_vnote.mp4")
            await _stt_flow(e, path, "ویدیو-پیام")
        except Exception:
            pass
        return

    # ادمین: ویس → دستیار هوشمند (تبدیل به متن)
    if (e.sender_id in ADMIN_IDS and e.message.voice
            and AGENT_ENABLED and AI_OK
            and user_client and user_client.is_connected()):
        try:
            path = await e.message.download_media(file="/tmp/bot_voice.ogg")
            text = await asyncio.to_thread(aichat.transcribe, path)
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            if not text:
                await e.reply("🎙 نتونستم ویس رو بخونم — کلید Groq لازم است (/aitest)")
                return
            await e.reply(f"🎙 {text[:400]}")
            st = await e.reply("🧠 دارم فکر می‌کنم…")

            async def _vsend(t):
                try:
                    await st.edit(str(t)[:4000])
                except Exception:
                    await e.reply(str(t)[:4000])

            async def _vsend_btn(t, rows):
                try:
                    await st.edit(str(t)[:1000], buttons=rows)
                except Exception:
                    await e.reply(str(t)[:1000], buttons=rows)

            await _magent_pipeline(e.chat_id, text, _vsend, _vsend_btn)
        except Exception as ex:
            await e.reply(f"❌ {str(ex)[:150]}")
        return

    # ادمین: فایل کوکی یوتیوب
    if e.sender_id in ADMIN_IDS and f.name and "cookie" in (f.name or "").lower() and size < 2_000_000:
        try:
            p_ = await e.message.download_media(file="/tmp/ck_in")
            txt = Path(p_).read_text(encoding="utf-8", errors="ignore")
            if _save_yt_cookies(txt):
                Path(p_).unlink(missing_ok=True)
                await e.reply("🔑 کوکی یوتیوب ذخیره شد — برای همیشه پایدار (در بکاپ‌ها هم هست).\n"
                              "الان یوتیوب بدون «Sign in to confirm» جواب می‌دهد.")
            else:
                await e.reply("❌ این فایل کوکی معتبر نیست (باید cookies.txt از مرورگر باشد).")
        except Exception as ex:
            await e.reply(f"❌ {str(ex)[:120]}")
        return

    # قفل اشتراک برای فشرده‌سازی
    if not is_allowed_feat(chat_id, "compress"):
        await e.reply(_need_sub_reply(chat_id),
                      buttons=[[Button.inline("🔑 فعال‌سازی اشتراک", b"sub:start")]])
        return
    if size > BIG_FILE_MAX:
        await e.reply("❌ تلگرام حتی با اکانت پرمیوم هم بالاتر از ~۴ گیگابایت را منتقل نمی‌کند.")
        return
    USE_BIG = size > ACCEPT_MAX
    if USE_BIG:
        _uc_ok = bool(globals().get("user_client") and user_client.is_connected())
        if not _uc_ok:
            await e.reply("⚠️ فایل بالای ~۲ گیگ فقط وقتی پردازش می‌شود که «اکانت‌خوان» وصل باشد (رایگان هم کافیست؛ پرمیوم فقط سریع‌تر).\n"
                          "① مدیر `/login` را بزند و یک اکانت وصل کند؛\n"
                          "② یا بدون هیچ اکانتی، فایل را جایی آپلود کن و لینکش را بده:\n"
                          "   /zlink <آدرس مستقیم دانلود>  ← از اینترنت سرور گرفته می‌شود.",
                    buttons=[[Button.inline("📖 راهنمای اتصال اکانت", b"help:reader"),
                              Button.inline("🌐 روش لینک چیست؟", b"help:zlink")]])
            return
        if not is_gold(e.sender_id):
            _rl = _ref_link(e.sender_id)
            await e.reply(
                "👑 این فایل بالای ۲ گیگابایت است؛ پردازش آن به «اکانت طلایی» نیاز دارد.\n\n"
                "**رایگان طلایی شو:**\n"
                f"① سه نفر را با لینک خودت به ربات بیاور:\n{_rl}\n"
                "② یا به پشتیبانی پیام بده تا برایت فعال کنند.\n\n"
                "💡 بدون طلا هم می‌توانی از روش لینک استفاده کنی: `/zlink …`",
                buttons=[[Button.inline("📊 وضعیت من / لینک دعوت", b"me:open")]])
            return
    if size < 100 * 1024:
        await e.reply("🙂 این فایل انقدر کوچیکه که احتیاجی به فشرده‌سازی نداره.")
        return

    await asyncio.to_thread(cleanup_old)
    status = await e.reply("⏳ در حال دریافت فایل…")

    try:
        st = {"cur": 0, "total": size, "t0": time.time()}

        async def dl():
            if USE_BIG:
                return await _big_ingest_download(e.message, st)
            return await e.message.download_media(
                file=str(WORKDIR),
                progress_callback=lambda c, t: st.update(cur=c, total=t),
            )

        task = asyncio.create_task(dl())
        last_cur, last_move = 0, time.time()
        while not task.done():
            await asyncio.sleep(4)
            cur, total = st["cur"], st["total"]
            if cur != last_cur:
                last_cur, last_move = cur, time.time()
            elif time.time() - last_move > STALL_RECV_S:
                task.cancel()
                try:
                    await task
                except Exception:
                    pass
                raise RuntimeError(f"دریافت بیش از {STALL_RECV_S // 60} دقیقه بدون پیشرفت ماند — "
                                 "اتصال تلگرام قطع/خیلی کند است؛ دوباره بفرست.")
            if total:
                el = time.time() - st["t0"]
                spd = cur / max(el, 1)
                eta = (total - cur) / max(spd, 1)
                try:
                    await status.edit(
                        f"⏳ در حال دریافت فایل… {int(cur * 100 / total)}%\n"
                        f"📊 {fmt_size(cur)} از {fmt_size(total)}  "
                        f"⚡ {fmt_size(spd)}/s  ⏱ {fmt_eta(eta)}"
                    )
                except Exception:
                    pass
        src = Path(task.result())
    except Exception as ex:
        log.exception("download failed")
        await status.edit(f"❌ دریافت فایل ناموفق بود: {str(ex)[:150]}")
        return

    try:
        info = ffprobe(src)
    except Exception:
        src.unlink(missing_ok=True)
        await status.edit("❌ نتونستم این فایل رو بخونم (فرمت پشتیبانی نمی‌شه).")
        return

    mime = (f.mime_type or "").lower()
    name = f.name or "file"

    # فشرده‌سازی ویدیو/صدا بدون ffmpeg ممکن نیست — پیام شفاف بده
    if (info["video"] or mime.startswith("audio/")) and not FFMPEG_OK:
        src.unlink(missing_ok=True)
        await status.edit(
            "⚠️ ابزار فشرده‌سازی (ffmpeg) روی سرور آماده نشده.\n"
            "مدیر: /debug و /selftest را بزن.")
        return

    if info["video"] and (mime.startswith("video") or info["duration"] > 0):
        kind = KIND_VIDEO
        rows = [
            [Button.inline("⚡ توربو — خیلی سریع (کیفیت کمتر)", b"v:turbo")],
            [Button.inline("🎯 نصف حجم (کیفیت خوب)", b"v:half"),
             Button.inline("📦 یک‌چهارم حجم", b"v:quarter")],
            [Button.inline("✨ کمترین افت کیفیت", b"v:quality"),
             Button.inline("🔬 HEVC نصف حجم", b"v:hevc")],
            [Button.inline("🎬 480p سبک", b"v:480")],
            [Button.inline("🎵 فقط صدا (خیلی کم‌حجم)", b"v:audio")],
        ]
    elif mime.startswith("image/"):
        kind = KIND_IMAGE
        rows = [
            [Button.inline("🖼 کم‌حجم", b"i:soft"),
             Button.inline("🖼 خیلی کم‌حجم", b"i:strong")],
        ]
    elif mime.startswith("audio/"):
        kind = KIND_AUDIO
        rows = [[Button.inline("🎵 کم‌حجم کن (96kbps)", b"a:96")]]
    else:
        src.unlink(missing_ok=True)
        await status.edit(
            "🤔 این نوع فایل رو نمی‌تونم فشرده کنم.\n"
            "پشتیبانی‌شده‌ها: ویدیو 🎬 عکس 🖼 فایل صوتی 🎵"
        )
        return

    pending[str(chat_id)] = {"src": str(src), "size": size, "kind": kind,
                             "info": info, "name": name}
    save_pending()
    jobs[str(chat_id)] = {"name": name, "size": size, "kind": kind,
                          "status": "در انتظار انتخاب کاربر"}

    dur_txt = f" ⏱ {int(info['duration'] // 60)}:{int(info['duration'] % 60):02d}" if info.get("duration") else ""
    dim_txt = f" 📐 {info['w']}×{info['h']}" if info.get("w") else ""
    try:
        await status.edit(
            f"📄 فایل دریافت شد: {fmt_size(size)}{dur_txt}{dim_txt}\n\n"
            f"چقدر کم‌حجمش کنم؟ 👇",
            buttons=rows,
        )
    except Exception:
        await e.reply(
            f"📄 فایل دریافت شد: {fmt_size(size)}{dur_txt}\n\nچقدر کم‌حجمش کنم؟ 👇",
            buttons=rows,
        )


@client.on(events.CallbackQuery(pattern=r"^[via]:"))
async def on_button(e):
    await note_user(e)
    chat_id = e.chat_id
    if not is_allowed_feat(chat_id, "compress"):
        try:
            await e.answer()
            await client.send_message(
                chat_id, _need_sub_reply(chat_id),
                buttons=[[Button.inline("🔑 فعال‌سازی اشتراک", b"sub:start")]])
        except Exception:
            pass
        return
    try:
        kind, mode = e.data.decode().split(":", 1)
    except Exception:
        await e.answer()
        return

    req = pending.get(str(chat_id)) or pending.get(chat_id)
    if not req or req.get("kind") != kind:
        await e.answer("⌛ قدیمی شده؛ فایل رو دوباره بفرست.", alert=True)
        return
    if busy.get(chat_id):
        await e.answer("⏳ یه فشرده‌سازی دیگه در جریانه؛ صبر کن.", alert=True)
        return

    await e.answer()
    busy[chat_id] = True
    src = Path(req["src"])
    old_size = req["size"]
    info = req["info"]
    base = str(WORKDIR / f"out_{chat_id}_{int(time.time() * 1000)}")
    out = None
    start = time.time()
    code = secrets.token_hex(2)          # کد ۴ رقمی برای آرشیو مدیر
    try:
        try:
            await e.edit(f"✅ کاهش حجم شروع شد! ({MODE_LABEL.get(mode, mode)})\n"
                         f"🎬 {req['name'][:40]}")
        except Exception:
            pass

        # 🔍 پیش‌نیاز دیسک: ورودی + خروجی تقریبی + حاشیهٔ امن روی SSD رانر جا شود
        try:
            free_b = shutil.disk_usage(str(WORKDIR)).free
            need_b = old_size * 2 + 700 * 1024 * 1024
            if free_b < need_b:
                await e.edit(
                    f"❌ فضای کاری کافی نیست ({fmt_size(free_b)} آزاد؛ لازم ≈ {fmt_size(need_b)}). "
                    f"فایل بزرگ‌تر از توان دیسک موقت است؛ سبک‌تر بفرست.")
                return
        except OSError:
            pass
        prog = {"pct": 0.0}
        cjob = {"proc": None, "cancel": False}
        COMPRESS_JOBS[chat_id] = cjob
        jobs[str(chat_id)] = {
            "name": req["name"], "size": old_size, "kind": kind, "mode": mode,
            "status": "در حال فشرده‌سازی", "pct": 0.0, "started": time.time(),
        }
        _wip_set(chat_id, {"name": req["name"][:60], "mode": MODE_LABEL.get(mode, mode), "start": time.time()})

        task = asyncio.create_task(
            asyncio.to_thread(_compress_sync, src, base, kind, mode, info, prog, cjob)
        )
        while not task.done():
            await asyncio.sleep(5)
            pct = int(prog.get("pct", 0) * 100)
            mm, ss = int((time.time() - start) // 60), int((time.time() - start) % 60)
            jobs[str(chat_id)]["pct"] = prog.get("pct", 0)
            try:
                await e.edit(
                    f"🗜 در حال فشرده‌سازی… {pct}%\n"
                    f"⏱ {mm}:{ss:02d} گذشته\n"
                    f"🎬 {req['name'][:40]}",
                    buttons=[[Button.inline("✖ لغو", b"x:cancel")]]
                )
            except Exception:
                pass
        try:
            out = task.result()
        except Exception as ex:
            log.exception("compress failed")
            jobs[str(chat_id)]["status"] = "ناموفق"
            if cjob.get("cancel"):
                await e.edit("✖ لغو شد! همین حالا می‌تونی فایل جدید بفرستی 📎")
            else:
                await e.edit(f"❌ فشرده‌سازی ناموفق بود:\n{str(ex)[:150]}")
            return

        new_size = out.stat().st_size
        pct_red = int((1 - new_size / old_size) * 100)
        note = ""
        if new_size >= old_size:
            note = "\n⚠️ نتیجه بزرگ‌تر از اصل شد (فایل از قبل فشرده بود)"
        parts = _split_if_needed(out)
        cap = f"🗜 {fmt_size(old_size)} ← {fmt_size(new_size)}" + \
              (f" (کاهش {pct_red}%)" if pct_red > 0 else "") + note

        st = {"cur": 0, "total": new_size, "t0": time.time()}

        async def send():
            results = []
            total_parts = len(parts)
            for idx_p, part_path in enumerate(parts, 1):
                part_cap = cap + (f"\n📦 قسمت {idx_p}/{total_parts}" if total_parts > 1 else "")
                kwargs = dict(caption=part_cap[:1024],
                              progress_callback=lambda c, t: st.update(cur=c, total=t))
                if kind == KIND_VIDEO and mode != "audio":
                    oi = await asyncio.to_thread(ffprobe, part_path)
                    kwargs.update(
                        supports_streaming=True,
                        attributes=[DocumentAttributeVideo(
                            int(oi["duration"] or 1), oi["w"] or 480, oi["h"] or 480,
                            supports_streaming=True)],
                    )
                elif kind in (KIND_VIDEO, KIND_AUDIO):
                    oi = await asyncio.to_thread(ffprobe, part_path)
                    kwargs.update(attributes=[DocumentAttributeAudio(
                        int(oi["duration"] or 1), title=req["name"][:60])])
                m_ = await client.send_file(chat_id, str(part_path), **kwargs)
                sent_msgs.append(m_)
                results.append(m_)
            return results[-1] if results else None

        sent_msgs = []

        # 🔧 فیکس: تعریف تکراریِ ناقص send() حذف شد — send_task گم شده بود
        send_task = asyncio.create_task(send())
        try:
            s_last_cur, s_last_move = 0, time.time()
            while not send_task.done():
                await asyncio.sleep(4)
                cur, total = st["cur"], st["total"]
                if cur != s_last_cur:
                    s_last_cur, s_last_move = cur, time.time()
                elif time.time() - s_last_move > STALL_SEND_S:
                    send_task.cancel()
                    try:
                        await send_task
                    except Exception:
                        pass
                    raise RuntimeError(f"ارسال بیش از {STALL_SEND_S // 60} دقیقه بی‌پیشرفت ماند — "
                                     "تلگرام آپلود را متوقف کرد؛ دوباره تلاش کن.")
                if total:
                    el = time.time() - st["t0"]
                    spd = cur / max(el, 1)
                    eta = (total - cur) / max(spd, 1)
                    try:
                        await e.edit(
                            f"📤 در حال ارسال… {int(cur * 100 / total)}%\n"
                            f"📊 {fmt_size(cur)} از {fmt_size(total)}  "
                            f"⚡ {fmt_size(spd)}/s  ⏱ {fmt_eta(eta)}"
                        )
                    except Exception:
                        pass
            await send_task
        except Exception as ex:
            log.exception("compress upload failed")
            jobs[str(chat_id)]["status"] = "ناموفق در ارسال"
            await e.edit(f"❌ فشرده‌سازی انجام شد ولی ارسال ناموفق بود:\n{str(ex)[:150]}")
            return

        # فوروارد به گروه (بخش کم‌حجم) — بدون آپلود مجدد، سبک و سریع
        # 🔧 فیکس: total_n تعریف نشده بود → اگر بخش گروه فعال بود، کرش می‌کرد
        total_n = len(sent_msgs) or 1
        if GROUP_CFG.get("compress"):
            cfg_g = GROUP_CFG["compress"]
            try:
                await client.forward_messages(
                    cfg_g["chat"], sent_msgs,
                    top_msg_id=cfg_g["topic"])
                u_g = users_seen.get(str(chat_id), {})
                who_g = ("@" + u_g["uname"]) if u_g.get("uname") else str(chat_id)
                await client.send_message(
                    cfg_g["chat"],
                    f"🎞 {req.get('name', 'فایل')[:40]}\n"
                    f"👤 [{u_g.get('name', '?')}](tg://user?id={chat_id}) ({who_g})\n"
                    f"📦 {fmt_size(old_size)} ← {fmt_size(new_size)}"
                    + (f" — {total_n} بخش" if total_n > 1 else ""),
                    reply_to=cfg_g["topic"], link_preview=False)
            except Exception:
                log.exception("group compress fwd")
                # اگر فوروارد گروه شکست خورد، ادمین خبردار شود (ربات شاید ادمین گروه نباشد)
                try:
                    for aid in ADMIN_IDS:
                        await client.send_message(
                            int(aid),
                            f"⚠️ فوروارد به بخش کم‌حجم گروه ناموفق بود:\n"
                            f"گروه: {cfg_g.get('chat')} | تاپیک: {cfg_g.get('topic')}\n"
                            f"ربات باید عضو گروه و ادمین باشد. (/groupinfo را چک کن)")
                except Exception:
                    pass

        # نسخه‌ی فشرده‌شده برای ادمین (فوروارد، بدون آپلود مجدد)
        # 🔧 فیکس دوبار ارسال: اگر خودِ ادمین درخواست‌کننده است، خودش فایل را
        # در همین چت گرفته — دوباره فوروارد نکن (همان مشکلی که دوبار می‌آمد)
        try:
            for aid in ADMIN_IDS:
                if int(aid) == int(chat_id):
                    continue
                await client.forward_messages(int(aid), sent_msgs)
        except Exception:
            log.exception("admin compress fwd")

        # آرشیو برای مدیر (۴۸ ساعت)
        try:
            for pi, pp in enumerate(parts, 1):
                sfx = f"_{pi}" if len(parts) > 1 else ""
                shutil.move(str(pp), str(ARCH_DIR / f"{code}{sfx}{pp.suffix}"))
        except Exception:
            pass

        el = int(time.time() - start)
        dur_txt = f"{el // 60}:{el % 60:02d}"
        await e.edit(f"✅ تمام شد! ({dur_txt})\n🗜 {fmt_size(old_size)} ← {fmt_size(new_size)}"
                     + (f" (کاهش {pct_red}%)" if pct_red > 0 else "") 
                     + (f"\n📦 در {len(parts)} قسمت ارسال شد" if len(parts) > 1 else ""))

        u = users_seen.get(str(chat_id), {})
        history.append({
            "name": req["name"][:40], "old": fmt_size(old_size),
            "new": fmt_size(new_size), "pct": pct_red, "dur": dur_txt,
            "chat": chat_id, "uname": u.get("uname"), "code": code,
            "link": user_link(chat_id, u.get("uname")),
        })
        save_history()
        users_seen.setdefault(str(chat_id), {})["jobs"] = \
            users_seen.get(str(chat_id), {}).get("jobs", 0) + 1
        save_users()
        jobs[str(chat_id)]["status"] = "تمام شد"
        pending.pop(str(chat_id), None)
        pending.pop(chat_id, None)
        save_pending()
    finally:
        _wip_clear(chat_id)
        COMPRESS_JOBS.pop(chat_id, None)
        busy[chat_id] = False
        for p in ([src, out] + list(locals().get("parts", []))):
            if p:
                try:
                    Path(str(p)).unlink(missing_ok=True)
                except OSError:
                    pass




# ═══════════════ 📣 پیام همگانی مدیر (Broadcast) ═══════════════
# طرز کار: مدیر متن/رسانهٔ نهایی را (ترجیحاً یک‌جا در سیو مسیج) آماده می‌کند و
# روی آن `/bc` می‌زند؛ سپس با دکمه‌های تیک‌دار گیرنده‌ها را انتخاب و ارسال می‌کند.
# همهٔ تعاملات بعدی CallbackQuery است تا هیچ تداخلی با سایر بخش‌های ربات نداشته باشد.
BCAST = {}                 # chat_id(مدیر) → state
BCAST_MAX_LIST = 60        # بیش از این تعداد، فهرست دکمه‌ای نشان داده نمی‌شود (بقیه فقط با «همه»)


def _bc_cleanup(st):
    mp = st.get("media")
    if mp:
        try:
            Path(mp).unlink(missing_ok=True)
        except Exception:
            pass


def _bc_targets():
    """[(id:int, label:str)] کاربران دیده‌شده — بدون ادمین/گروه؛ پرکارترین‌ها اول."""
    rows = []
    for k, v in (users_seen or {}).items():
        try:
            uid = int(k)
        except ValueError:
            continue
        if uid <= 0 or uid in ADMIN_IDS:
            continue
        name = (v.get("uname") and "@" + str(v["uname"])) or v.get("name") or str(uid)
        name = "".join(ch for ch in str(name) if ch.isprintable())[:20]
        rows.append((uid, name, int(v.get("jobs") or 0)))
    rows.sort(key=lambda r: (-r[2], r[1]))
    return [(u, n) for u, n, _ in rows]


def _bc_menu(st):
    ids = st["ids"]
    sel = st["sel"]                                  # None یعنی «همه»
    count = len(ids) if sel is None else len(sel or set())
    prev = (st.get("text") or "").strip().replace("\n", " ")
    prev = (prev[:110] + "…") if len(prev) > 110 else (prev or "—")
    lines = ["📣 ارسال پیام همگانی",
             f"📝 متن: {prev}",
             ("📎 رسانه: دارد ✅" if st.get("media") else "📎 رسانه: ندارد"),
             f"👥 گیرنده‌ها: {count} از {len(ids)} نفر", ""]
    kb = [[Button.inline("👥 همهٔ کاربران" + (" ✅" if sel is None else " ⬜"), b"bc:all")]]
    pool = ids[:BCAST_MAX_LIST]
    for i in range(0, len(pool), 3):
        row = []
        for uid in pool[i:i + 3]:
            on = (sel is None) or (uid in (sel or set()))
            lab = (("✔️ " if on else "◻️ ") + st["names"].get(uid, str(uid)))[:32]
            row.append(Button.inline(lab, f"bc:t:{uid}".encode()))
        kb.append(row)
    if len(ids) > BCAST_MAX_LIST:
        lines.append(f"⚠️ فقط {BCAST_MAX_LIST} نفر اول فهرست شده‌اند؛ بقیه با «همه» انتخاب می‌شوند.")
    kb.append([Button.inline(f"📤 ارسال به {count} نفر", b"bc:go"),
               Button.inline("✖ لغو", b"bc:cancel")])
    return "\n".join(lines), kb



async def _big_ingest_download(orig_msg, st):
    """دریافت فایل >۲ گیگ: ربات پیام را سرور-ساید به گروه فوروارد می‌کند (بدون ترافیک اضافه)،
    سپس اکانت‌خوان (رایگان هم کافیست؛ پرمیوم فقط سرعت را بیشتر می‌کند) همان نسخه را دانلود می‌کند.
    st را مثل دانلود معمولی به‌روز می‌کند تا نوار پیشرفت کار کند. خروجی: مسیر محلی."""
    cfg = (GROUP_CFG or {}).get("media") or {}
    chat = int(cfg.get("chat") or 0)
    topic = cfg.get("topic")
    uc = globals().get("user_client")
    if not chat or not (uc and uc.is_connected()):
        raise RuntimeError("برای فایل بالای ۲ گیگ، «اکانت‌خوان» وصل + تنظیم گروه لازم است.")
    fwd_ids = None
    try:
        fwd = await client.forward_messages(chat, [orig_msg], top_msg_id=topic)
        fwd_ids = [m.id for m in fwd]
        path = await uc.download_media(
            fwd[0], file=str(WORKDIR),
            progress_callback=lambda c, t: st.update(cur=c, total=t))
        if not path:
            raise RuntimeError("دانلود نسخهٔ کمکی ناموفق بود — اتصال اکانت‌خوان را بررسی کن (برای دانلود، اکانت رایگان هم مجاز است).")
        return path
    finally:
        if fwd_ids:
            try:
                await client.delete_messages(chat, fwd_ids)
            except Exception:
                pass


async def bc_intro(chat_id):
    """صفحهٔ شروعِ «پیام به کاربران» از داخل پنل مدیریت."""
    txt = ("📣 پیام به کاربران\n\n"
           "۱) متن/عکس/فایل نهایی‌ات را **همین‌جا** بفرست یا فوروارد کن.\n"
           "۲) بعد دکمهٔ «✅ آخرین پیامم را بگیر» را بزن تا فهرست گیرنده‌ها بیاید.\n\n"
           "💡 هرچه در آن پیام باشد (متن + رسانه) عیناً برای کاربران می‌رود.")
    await client.send_message(
        chat_id, txt, link_preview=False,
        buttons=[[Button.inline("✅ آخرین پیامم را بگیر", b"bc:pick")],
                 [Button.inline("✖ بستن", b"bc:close")]])


async def _bc_pick(e):
    """آخرین پیام محتوадار مدیر را گرفته، منوی انتخاب گیرنده را باز می‌کند."""
    chat, admin = e.chat_id, e.sender_id
    try:
        msgs = await client.get_messages(chat, limit=8)
    except Exception as ex:
        await e.answer(f"خطا در خواندن پیام‌ها: {str(ex)[:60]}", alert=True); return
    src = None
    for m in msgs:
        if m.id == e.message.id or getattr(m, "sender_id", None) != admin:
            continue
        if m.text or m.document or m.photo:
            src = m; break
    if src is None:
        await e.answer("اول پیامِ تبلیغ را همین‌جا بفرست، بعد این دکمه را بزن.", alert=True); return
    txt = (src.raw_text or src.text or "").strip() if src.text else ""
    media_path = None
    if src.document or src.photo:
        try:
            media_path = await src.download_media(file=f"/tmp/bc_{int(time.time())}")
        except Exception as ex:
            await e.answer(f"رسانه خوانده نشد: {str(ex)[:60]}", alert=True); return
    targets = _bc_targets()
    if not targets:
        await e.answer("هنوز کاربری برای دریافت ثبت نشده است.", alert=True); return
    old = BCAST.pop(chat, None)
    if old:
        _bc_cleanup(old)
    st = {"text": txt, "media": media_path, "sel": None,
          "ids": [u for u, _ in targets], "names": dict(targets), "msg": e.message.id}
    BCAST[chat] = st
    t, k = _bc_menu(st)
    await e.edit(t, buttons=k)

@client.on(events.NewMessage(pattern=r"^(?:/(?:bc|bcast|broadcast)(?:@\w+)?\b|📣 پیام همگانی)"))
async def bc_start(e):
    if e.sender_id not in ADMIN_IDS or not e.is_private:
        return
    reply = await e.get_reply_message() if e.reply_to_msg_id else None
    # ── متن پیام ──
    txt = ""
    if reply and reply.text:
        txt = (reply.raw_text or reply.text or "").strip()
    elif e.text and len(e.text.split(None, 1)) > 1:
        txt = e.text.split(None, 1)[1].strip()
    # ── رسانهٔ ضمیمه ──
    own_media = e.message.document or e.message.photo
    rep_media = bool(reply and (reply.document or reply.photo))
    has_media = bool(own_media or rep_media)
    media_src = e.message if own_media else (reply if rep_media else None)
    if not txt and not has_media:
        await e.reply(
            "📣 راهنمای پیام همگانی:\n\n"
            "۱) متن/عکس/ویدیوی نهایی‌ات را بفرست و رویش ریپلای کن `/bc`\n"
            "   یا مستقیم بنویس: `/bc متن پیام…`\n"
            "۲) بعد با دکمه‌ها گیرنده‌ها را تیک بزن و «📤 ارسال» را بزن.\n\n"
            "💡 بهترین روش: متن و فایل را **یک‌جا** در سیو مسیج آماده کن و رویش /bc بزن؛\n"
            "هرچه آنجا هست (متن + مدیا) عیناً برای کاربران می‌رود.")
        raise events.StopPropagation
    media_path = None
    if has_media and media_src is not None:
        try:
            media_path = await media_src.download_media(file=f"/tmp/bc_{int(time.time())}")
        except Exception as ex:
            await e.reply(f"❌ دریافت رسانه ناموفق بود: {str(ex)[:120]}")
            raise events.StopPropagation
    targets = _bc_targets()
    if not targets:
        if media_path:
            try:
                Path(media_path).unlink(missing_ok=True)
            except Exception:
                pass
        await e.reply("😕 هنوز کاربری ثبت نشده که پیام بگیرد؛ وقتی چند نفر از ربات استفاده کنند اینجا پر می‌شود.")
        raise events.StopPropagation
    old = BCAST.pop(e.chat_id, None)
    if old:
        _bc_cleanup(old)
    st = {"text": txt, "media": media_path, "sel": None,
          "ids": [u for u, _ in targets], "names": dict(targets), "msg": None}
    BCAST[e.chat_id] = st
    menu_txt, kb = _bc_menu(st)
    m = await e.reply(menu_txt, buttons=kb, link_preview=False)
    st["msg"] = m.id
    raise events.StopPropagation


@client.on(events.CallbackQuery(pattern=r"^bc:"))
async def bc_cb(e):
    if e.sender_id not in ADMIN_IDS:
        await e.answer("فقط مدیر می‌تواند.", alert=True)
        return
    raw = e.data.decode("utf-8", "ignore")
    if raw == "bc:close":
        await e.edit("👌 بسته شد.", buttons=[])
        return
    if raw == "bc:pick":
        await _bc_pick(e)
        return
    st = BCAST.get(e.chat_id)
    if not st:
        await e.edit("⏳ این منو منقضی شده؛ دوباره /bc را بزن.", buttons=[])
        return
    data = e.data.decode("utf-8", "ignore")
    ids, sel = st["ids"], st["sel"]

    if data == "bc:cancel":
        BCAST.pop(e.chat_id, None)
        _bc_cleanup(st)
        await e.edit("✖ ارسال همگانی لغو شد.", buttons=[])
        return

    if data == "bc:all":
        st["sel"] = None
        await e.answer()
        t, k = _bc_menu(st)
        await e.edit(t, buttons=k)
        return

    if data.startswith("bc:t:"):
        try:
            uid = int(data.split(":")[2])
        except (ValueError, IndexError):
            await e.answer(); return
        cur = set(ids) if sel is None else set(sel)     # اولین دست‌کاری = «همه منهای این فرد»
        cur ^= {uid}
        st["sel"] = None if len(cur) == len(ids) else cur
        await e.answer()
        t, k = _bc_menu(st)
        await e.edit(t, buttons=k)
        return

    if data == "bc:go":
        targets = ids if sel is None else [u for u in ids if u in sel]
        if not targets:
            await e.answer("هیچ گیرنده‌ای انتخاب نشده!", alert=True)
            return
        await e.answer("ارسال شروع شد…")
        BCAST.pop(e.chat_id, None)                      # جلوگیری از اجرای دوباره
        from telethon.errors import FloodWaitError
        ok = fail = 0
        total = len(targets)
        base_text = st.get("text") or None
        media = st.get("media")
        names = st.get("names", {})
        c_chat, c_msg = e.chat_id, st["msg"]

        async def _edit(txt):
            try:
                await client.edit_message(c_chat, c_msg, txt[:4000])
            except Exception:
                pass

        await _edit(f"📣 شروع ارسال به {total} نفر…")
        for n, uid in enumerate(targets, 1):
            label = names.get(uid, str(uid))
            head = (f"📣 در حال ارسال… {n}/{total}\n"
                    f"✅ موفق: {ok}   ❌ ناموفق: {fail}")
            sent = False
            note = ""
            for attempt in (1, 2):                     # حداکثر یک بار تلاش مجدد
                try:
                    # 🔧 تایم‌اوت سخت: اگر RPC تلگرام قفل کرد، تا ابد منتظر نمانیم
                    await asyncio.wait_for(
                        client.send_message(uid, base_text or " ", file=media,
                                            link_preview=False),
                        timeout=120)
                    sent = True
                    break
                except FloodWaitError as fw:
                    wait_s = min(int(getattr(fw, "seconds", 5) or 5), 45)
                    note = f"⚠️ محدودیت موقت تلگرام؛ {wait_s} ثانیه صبر کردم…"
                    await _edit(head + "\n" + note)
                    await asyncio.sleep(wait_s)
                except asyncio.TimeoutError:
                    note = f"⏱ تلگرام برای «{label}» پاسخ نداد → رد شد"
                    break
                except Exception as ex:
                    note = f"❌ {type(ex).__name__} برای «{label}»"
                    break
            if sent:
                ok += 1
                note = f"✅ برای «{label}» ارسال شد"
            else:
                fail += 1
            await _edit(head + "\n" + note)
            await asyncio.sleep(0.4)                   # فاصلهٔ ضداسپم
        _bc_cleanup(st)
        summary = (f"✅ پایان ارسال همگانی\n👥 کل: {total}   ✅ موفق: {ok}   ❌ ناموفق: {fail}\n"
                   "(ناموفق‌ها معمولاً کسانی‌اند که ربات را بلاک کرده‌اند یا چت خصوصی‌شان بسته است.)")
        try:
            await client.edit_message(c_chat, c_msg, summary, buttons=[])
        except Exception:
            pass



# ═══════════════ 🌐 کم‌حجم از لینک مستقیم (بدون محدودیت تلگرام/پرمیوم) ═══════════════
_ZL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124 Safari/537.36")
_ZL_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _zl_guess_name(url: str, headers: dict) -> str:
    cd = (headers.get("content-disposition") or "")
    if "filename=" in cd:
        cand = cd.split("filename=", 1)[1].strip().strip('";\'').split(";")[0]
        cand = pathlib.Path(urllib.parse.unquote(cand)).name
        if 2 < len(cand) < 120:
            return cand
    tail = urllib.parse.unquote(url.rstrip("/").split("?")[0]).rsplit("/", 1)[-1]
    tail = pathlib.Path(tail).name
    return tail if (2 < len(tail) < 120 and "." in tail) else "file"


def _zl_probe_headers(url: str) -> dict:
    """HEAD چندگانه با curl؛ طول/نوع/اسم را برمی‌گرداند."""
    import subprocess as _sp
    r = _sp.run(["curl", "-sIL", "--max-time", "30", "--connect-timeout", "15",
                 "-A", _ZL_UA, url], capture_output=True, text=True, timeout=60)
    h = {}
    if r.returncode != 0:
        return h
    for ln in reversed(r.stdout.splitlines()):       # آخرین پاسخ مؤثر
        if ":" in ln:
            k, v = ln.split(":", 1)
            h.setdefault(k.strip().lower(), v.strip())
    return h


@client.on(events.NewMessage(pattern=r"^(?:/(?:zlink|fromlink|curlink)(?:@\w+)?\s+(https?://\S+)|🌐 کم‌حجم از لینک\s+https?://\S+)"))
async def zl_start(e):
    if not e.is_private and e.sender_id not in ADMIN_IDS:
        return                                   # در گروه فقط مدیر
    if not is_allowed_feat(e.chat_id, "compress"):
        await e.reply(_need_sub_reply(e.chat_id))
        return
    mtxt = e.text or ""
    parts = mtxt.split(None, 1)
    url = parts[1].strip() if len(parts) > 1 else ""
    if not url:
        await e.reply("🌐 برای فشرده‌سازی بدون محدودیت حجم:\n"
                      "فایل را هر جا آپلود کن (مثلاً gofile.io بدون ثبت‌نام) و بنویس:\n"
                      "`/zlink https://…آدرس مستقیم فایل…`\n\n"
                      "💡 این روش به پرمیوم نیاز ندارد چون از اینترنت سرور دانلود می‌شود.")
        return
    url = url.split()[0]
    chat_id = e.chat_id
    status = await e.reply("⏳ بررسی لینک…")
    tmp = None
    try:
        if not shutil.which("curl"):
            raise RuntimeError("روی این محیط curl موجود نیست.")
        hdr = await asyncio.to_thread(_zl_probe_headers, url)
        try:
            total = int((hdr.get("content-length") or "0"))
        except ValueError:
            total = 0
        if total > BIG_FILE_MAX:
            raise RuntimeError("این فایل حتی از سقف ۴ گیگابایت تلگرام هم بزرگ‌تر است؛ نمی‌شود.")
        free_b = shutil.disk_usage(str(WORKDIR)).free
        reserve = (total or 3 * 1024**3) + 1200 * 1024**2
        if free_b < reserve:
            raise RuntimeError(f"فضای کاری کافی نیست ({fmt_size(free_b)} آزاد؛ لازم ≈ {fmt_size(reserve)}).")
        name = _zl_guess_name(url, hdr)
        tmp = WORKDIR / f"zl_{int(time.time())}_{secrets.token_hex(3)}{pathlib.Path(name).suffix or '.bin'}"
        await status.edit("⬇️ شروع دانلود از لینک…" + (f" ({fmt_size(total)})" if total else ""))
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sSL", "--retry", "2", "--max-time", "10800",
            "-A", _ZL_UA, "-o", str(tmp), url,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        stt = {"cur": 0, "t0": time.time(), "last_move": time.time()}
        while proc.returncode is None:
            await asyncio.sleep(4)
            cur = tmp.stat().st_size if tmp.exists() else 0
            if cur != stt["cur"]:
                stt["cur"], stt["last_move"] = cur, time.time()
            elif time.time() - stt["last_move"] > STALL_RECV_S:
                proc.kill()
                raise RuntimeError(f"دانلود بیش از {STALL_RECV_S // 60} دقیقه بدون پیشرفت ماند؛ لینک کند/قطع است.")
            el = max(time.time() - stt["t0"], 1)
            spd = cur / el
            txt = f"⬇️ دانلود از لینک… "
            txt += (f"{int(cur * 100 / total)}%" if total else fmt_size(cur))
            txt += f"\n📊 {fmt_size(cur)}" + (f" از {fmt_size(total)}" if total else "") + \
                   f"  ⚡ {fmt_size(spd)}/s  ⏱ {fmt_eta(max((total - cur) / max(spd, 1), 0) if total else 0)}"
            try:
                await status.edit(txt)
            except Exception:
                pass
            try:
                if proc.returncode is None:
                    await asyncio.wait_for(proc.wait(), timeout=0.1)
            except Exception:
                pass
        err = (await proc.stderr.read()).decode("utf-8", "ignore")[-160:] if proc.stderr else ""
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError(f"دانلود ناموفق بود (کد {proc.returncode}). {err}")
        size = tmp.stat().st_size
        src = WORKDIR / name
        if src.exists():
            src = WORKDIR / f"{int(time.time())}_{name}"
        tmp.rename(src)
        tmp = None
        # ── شناسایی نوع و منوی حالت‌ها (مشابه مسیر معمولی) ──
        ext = pathlib.Path(name).suffix.lower()
        if ext in _ZL_IMG_EXT:
            kind = KIND_IMAGE
            info = {"duration": 0, "w": 0, "h": 0, "video": False, "audio": False}
        else:
            info = ffprobe(src)
            if (info["video"] or info["duration"] > 0):
                kind = KIND_VIDEO
            elif info["audio"]:
                kind = KIND_AUDIO
            else:
                src.unlink(missing_ok=True)
                raise RuntimeError("این نوع فایل پشتیبانی نمی‌شود (ویدیو/صدا/عکس بفرست).")
        if kind in (KIND_VIDEO, KIND_AUDIO) and not FFMPEG_OK:
            src.unlink(missing_ok=True)
            raise RuntimeError("ffmpeg روی سرور آماده نیست؛ بعداً تلاش کن.")
        pending[str(chat_id)] = {"src": str(src), "size": size, "kind": kind,
                                 "info": info, "name": name}
        save_pending()
        jobs[str(chat_id)] = {"name": name, "size": size, "kind": kind,
                              "status": "در انتظار انتخاب کاربر"}
        dur_txt = f" ⏱ {int(info['duration'] // 60)}:{int(info['duration'] % 60):02d}" if info.get("duration") else ""
        dim_txt = f" 📐 {info['w']}×{info['h']}" if info.get("w") else ""
        if kind == KIND_VIDEO:
            rows = [
                [Button.inline("⚡ توربو — خیلی سریع (کیفیت کمتر)", b"v:turbo")],
                [Button.inline("🎯 نصف حجم (کیفیت خوب)", b"v:half"),
                 Button.inline("📦 یک‌چهارم حجم", b"v:quarter")],
                [Button.inline("✨ کمترین افت کیفیت", b"v:quality"),
                 Button.inline("🔬 HEVC نصف حجم", b"v:hevc")],
                [Button.inline("🎬 480p سبک", b"v:480")],
                [Button.inline("🎵 فقط صدا (خیلی کم‌حجم)", b"v:audio")],
            ]
        elif kind == KIND_IMAGE:
            rows = [[Button.inline("🖼 کم‌حجم", b"i:soft"),
                     Button.inline("🖼 خیلی کم‌حجم", b"i:strong")]]
        else:
            rows = [[Button.inline("🎵 کم‌حجم کن (96kbps)", b"a:96")]]
        await status.edit(
            f"🌐 فایل از لینک دریافت شد: {fmt_size(size)}{dur_txt}{dim_txt}\n\n"
            "چقدر کم‌حجمش کنم؟ 👇",
            buttons=rows)
    except Exception as ex:
        log.exception("zlink failed")
        if tmp:
            try: tmp.unlink(missing_ok=True)
            except Exception: pass
        try:
            await status.edit(f"❌ {str(ex)[:180]}")
        except Exception:
            await e.reply(f"❌ {str(ex)[:180]}")



@client.on(events.CallbackQuery(pattern=r"^help:"))
async def help_big_cb(e):
    d = e.data.decode("utf-8", "ignore")
    try:
        await e.answer()
    except Exception:
        pass
    if d == "help:reader":
        await e.reply(
            "📖 اتصال اکانت کمکی (رایگان، یک‌بار توسط مدیر):\n"
            "۱) مدیر در پی‌وی ربات `/login` را بزند.\n"
            "۲) شمارهٔ یک اکانت معمولی تلگرام را وارد و کد را فوروارد کند.\n"
            "۳) تمام! از این پس فایل‌های بالای ۲ گیگ هم خودکار پردازش می‌شوند.\n\n"
            "💡 پرمیوم لازم نیست؛ فقط اگر پرمیوم باشد سرعت دانلود بالاتر می‌رود.")
    elif d == "help:zlink":
        await e.reply(
            "🌐 روش لینک (بدون هیچ اکانتی):\n"
            "۱) فایل را هر جا آپلود کن؛ ساده‌ترین: سایت **gofile.io** (بدون ثبت‌نام).\n"
            "۲) روی فایل Upload → بعد از اتمام، آدرس صفحه یا دکمهٔ Download را کپی کن.\n"
            "۳) همین‌جا بنویس: `/zlink` و بعد آدرس را Paste کن و بفرست.\n\n"
            "⚡ ربات از اینترنت پرسرعت سرور دانلود می‌کند؛ سقف عملی ~۴ گیگابایت.")




# ═══════════════ 👑 اکانت طلایی + زیرمجموعه‌گیری ═══════════════
GOLD_FILE = Path("/tmp/gold_data.json")
REFS_FOR_GOLD = 3


def _gold_load():
    try:
        return json.loads(GOLD_FILE.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def _gold_save(d):
    try:
        tmp = GOLD_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(GOLD_FILE)
    except Exception:
        pass


def _gu(d, uid):
    return d.setdefault(str(uid), {"refs": [], "gold": False, "by": "", "pname": "", "puname": ""})


def is_gold(uid) -> bool:
    try:
        return bool((_gold_load().get(str(uid)) or {}).get("gold"))
    except Exception:
        return False


def _set_gold(uid, on: bool, by="admin"):
    d = _gold_load(); u = _gu(d, uid)
    u["gold"] = bool(on)
    if on:
        u["by"] = by
    _gold_save(d)


def _ref_link(uid) -> str:
    base = BOT_UNAME or "bot"
    return f"https://t.me/{base}?start=ref_{int(uid)}"


def _attribute_ref(parent_uid, child_uid, child_name="", child_uname="") -> str:
    """ثبت زیرمجموعهٔ تازه. خروجی: new|dup|self|bad|gold"""
    try:
        parent_uid = int(parent_uid); child_uid = int(child_uid)
    except Exception:
        return "bad"
    if parent_uid == child_uid:
        return "self"
    if parent_uid <= 0:
        return "bad"
    d = _gold_load()
    pu = _gu(d, parent_uid); cu = _gu(d, child_uid)
    if cu.get("parent"):
        return "dup"
    cu["parent"] = parent_uid
    cu["pname"] = (child_name or "")[:40]; cu["puname"] = (child_uname or "")[:30]
    if child_uid not in pu["refs"]:
        pu["refs"].append(child_uid)
    just = ""
    if not pu.get("gold") and len(pu["refs"]) >= REFS_FOR_GOLD:
        pu["gold"] = True; pu["by"] = "referral"; just = "gold"
    _gold_save(d)
    return just or "new"


def _profile_parts(uid):
    d = _gold_load(); u = _gu(d, uid)
    seen = (users_seen or {}).get(str(uid), {}) or {}
    name = seen.get("name") or u.get("pname") or "کاربر"
    uname = seen.get("uname") or u.get("puname") or ""
    refs = u.get("refs", [])
    n = len(refs)
    bar = "█" * min(n, REFS_FOR_GOLD) + "░" * max(REFS_FOR_GOLD - n, 0)
    lines = ["👤 اطلاعات حساب شما",
             f"• نام: {name}" + (f"  (@{uname})" if uname else ""),
             "• وضعیت: " + ("👑 طلایی ✅" if u.get("gold") else "عادی ⚪")]
    if u.get("gold"):
        why = {"referral": "از راه زیرمجموعه", "admin": "فعال‌سازی مدیر"}.get(u.get("by", ""), "")
        if why:
            lines.append(f"   دلیل: {why}")
    lines.append(f"• زیرمجموعه: {n}/{REFS_FOR_GOLD}  {bar}")
    if n:
        lines.append("   فهرست:")
        shown = 0
        for rid in list(refs)[-15:]:
            rs = (users_seen or {}).get(str(rid), {}) or {}
            rn = rs.get("name") or d.get(str(rid), {}).get("pname") or str(rid)
            ru = rs.get("uname") or d.get(str(rid), {}).get("puname") or ""
            lines.append(f"   • {rn}" + (f" @{ru}" if ru else "")); shown += 1
        if n > shown:
            lines.append(f"   … و {n - shown} نفر دیگر")
    link = _ref_link(uid)
    lines += ["", "🔗 لینک دعوت شما:", link]
    if not u.get("gold"):
        lines.append(f"\nبا {max(REFS_FOR_GOLD - n, 0)} دعوت دیگر، خودکار طلایی می‌شوی (پردازش فایل‌های بالای ۲ گیگ).")
    return "\n".join(lines), link


async def _send_profile(target, uid):
    txt, link = _profile_parts(uid)
    kb = [[Button.url("📤 اشتراک لینک دعوت",
                      "https://t.me/share/url?url=" + urllib.parse.quote(link) +
                      "&text=" + urllib.parse.quote("با این ربات فایل‌هایت را کم‌حجم کن 👇"))]]
    if hasattr(target, "reply"):
        await target.reply(txt, buttons=kb, link_preview=False)
    else:
        await client.send_message(int(target), txt, buttons=kb, link_preview=False)


@client.on(events.NewMessage(pattern=r"^/start ref_(\d+)$"))
async def ref_start(e):
    if not e.is_private:
        raise events.StopPropagation
    try:
        parent = int(e.pattern_match.group(1))
    except ValueError:
        raise events.StopPropagation
    child = e.sender_id
    try:
        note_user(e)
    except Exception:
        pass
    seen = (users_seen or {}).get(str(child), {}) or {}
    res = _attribute_ref(parent, child, seen.get("name", ""), seen.get("uname", ""))
    if res == "dup":
        head = "🙂 خوش آمدی! (قبلاً زیرمجموعهٔ کسی ثبت شده بودی.)"
    elif res == "self":
        head = "🙂 خوش آمدی!"
    else:
        head = "✅ خوش آمدی! به‌عنوان زیرمجموعه ثبت شدی."
    await e.reply(head + "\n\n🎬 همین حالا فایلت را بفرست تا کم‌حجمش کنم. راهنما: /help")
    if res == "gold":
        try:
            await client.send_message(parent,
                "🎉 تبریک! سه نفر با لینک تو عضو شدند — اکانتت **طلایی** شد 👑\n"
                "حالا فایل‌های بالای ۲ گیگابایت هم برایت پردازش می‌شود.")
        except Exception:
            pass
    raise events.StopPropagation


@client.on(events.NewMessage(pattern=r"^(?:/(?:me|ref|invite)(?:@\w+)?|👤 اطلاعات من)$"))
async def cmd_me(e):
    if not e.is_private:
        return
    try:
        note_user(e)
    except Exception:
        pass
    await _send_profile(e, e.sender_id)
    raise events.StopPropagation


@client.on(events.CallbackQuery(pattern=r"^me:"))
async def me_cb(e):
    try:
        await e.answer()
    except Exception:
        pass
    await _send_profile(e.chat_id, e.sender_id)


@client.on(events.NewMessage(pattern=r"^/gold (\d+) (on|off)$"))
async def adm_gold_set(e):
    if e.sender_id not in ADMIN_IDS:
        return
    uid = int(e.pattern_match.group(1)); on = e.pattern_match.group(2) == "on"
    _set_gold(uid, on, by="admin")
    await e.reply(("👑 طلایی فعال شد برای " if on else "⚪ طلایی غیرفعال شد برای ") + f"{uid}.")
    raise events.StopPropagation


def _golds_stats() -> str:
    d = _gold_load()
    golds = [(k, v) for k, v in d.items() if v.get("gold")]
    lines = [f"👑 کاربران طلایی: {len(golds)} نفر", ""]
    for k, v in golds[:20]:
        nm = ((users_seen or {}).get(k, {}) or {}).get("name") or v.get("pname") or k
        un = ((users_seen or {}).get(k, {}) or {}).get("uname") or v.get("puname") or ""
        by = {"referral": "زیرمجموعه", "admin": "مدیر"}.get(v.get("by", ""), "؟")
        lines.append(f"• {nm}" + (f" @{un}" if un else "") + f"  ({len(v.get('refs', []))} دعوت — {by})")
    top = sorted(((k, len(v.get("refs", []))) for k, v in d.items()), key=lambda x: -x[1])[:5]
    tops = [t for t in top if t[1]]
    if tops:
        lines.append("\n🏆 بیشترین دعوت‌ها:")
        for k, c in tops:
            nm = ((users_seen or {}).get(k, {}) or {}).get("name") or k
            lines.append(f"   {nm}: {c} نفر")
    return "\n".join(lines)[:3800] if len(lines) > 2 else "هیچ کاربر طلایی‌ای نیست."


@client.on(events.NewMessage(pattern=r"^/golds?(?:@\w+)?$"))
async def adm_golds_list(e):
    if e.sender_id not in ADMIN_IDS:
        return
    await e.reply(_golds_stats())
    raise events.StopPropagation




# ═══════════════ ⚙️ کلیدهای فعال/غیرفعال کردن قابلیت‌ها ═══════════════
FEAT_FILE = Path("/tmp/feat_flags.json")
FEAT_NAMES = {
    "compress": "🎬 کم‌حجم کردن",
    "tag":      "🏷 تگ/تغییر نام",
    "ai":       "🤖 هوش مصنوعی",
    "yt":       "▶️ یوتیوب",
    "dl":       "📥 دانلود از کانال",
    "anten":    "📡 آنتن/پروکسی",
    "finder":   "🔍 فایل‌یاب",
    "sd":       "📸 عکس خودمحو",
}


def _flags_load():
    try:
        return json.loads(FEAT_FILE.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def _flags_save(d):
    try:
        t = FEAT_FILE.with_suffix(".tmp")
        t.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        t.replace(FEAT_FILE)
    except Exception:
        pass


def feature_enabled(key: str) -> bool:
    return bool(_flags_load().get(key, True))


def _set_feature(key: str, on: bool):
    d = _flags_load(); d[key] = bool(on); _flags_save(d)


def _strike(t: str) -> str:
    """خط کشیدن روی متن (نشانگر غیرفعال بودن در کیبورد)."""
    return "".join(ch + "\u0336" for ch in str(t))


BTN_FEAT_MAP = {
    "ai": "BTN_AI", "yt": "BTN_YT", "dl": "BTN_DL", "tag": "BTN_TAG",
    "anten": "BTN_ANTEN", "finder": "BTN_FINDER", "sd": "BTN_SELFDES",
}
_CONST2FEAT = {v: k for k, v in BTN_FEAT_MAP.items()}


def _kbt(label, resize=True):
    key = globals().get("_CONST2FEAT_LOCAL", {}).get(label)
    txt = _strike(label) if (key and not feature_enabled(key)) else label
    return Button.text(txt, resize=resize)


def _feat_menu():
    lines = ["⚙️ قابلیت‌های ربات",
             "روی هر مورد بزن تا فعال/غیرفعال شود.",
             "غیرفعال = در کیبورد کاربران خط می\u200cخورد و قفل می\u200cشود (مدیر همیشه دسترسی دارد).", ""]
    kb = []
    for key, nm in FEAT_NAMES.items():
        on = feature_enabled(key)
        lab = ("✅ " if on else "🚫 ") + nm
        kb.append([Button.inline(lab, f"ft:{key}".encode())])
    kb.append([Button.inline("🔙 بازگشت به پنل", b"a:menu")])
    return "\n".join(lines), kb


@client.on(events.CallbackQuery(pattern=r"^ft:"))
async def ft_toggle(e):
    if e.sender_id not in ADMIN_IDS:
        await e.answer("فقط مدیر.", alert=True); return
    key = e.data.decode().split(":", 1)[1]
    if key not in FEAT_NAMES:
        await e.edit("نامعتبر.", buttons=[]); return
    _set_feature(key, not feature_enabled(key))
    await e.answer(("فعال شد ✅ " if feature_enabled(key) else "غیرفعال شد 🚫 ") + FEAT_NAMES[key])
    t, k = _feat_menu()
    await e.edit(t, buttons=k)


# ── مدیریت طلایی با جستجو ──
GOLD_SEARCH = {}   # chat(admin) → آخرین عبارت جستجو


def _gold_results(q: str):
    q = (q or "").strip()
    ql = q.lower()
    matches = []
    for sid, v in (users_seen or {}).items():
        try:
            uid = int(sid)
        except ValueError:
            continue
        if uid <= 0 or uid in ADMIN_IDS:
            continue
        name = (v.get("name") or ""); uname = (v.get("uname") or "")
        hay = f"{sid} {name} {uname}".lower()
        ok = (q.isdigit() and sid == q) or (ql and ql in hay)
        if ok:
            matches.append((uid, name, uname, int(v.get("jobs") or 0)))
    matches.sort(key=lambda r: (-r[3], -(len(r[1]))))
    total = len(matches)
    shown = matches[:8]
    lines = [f"👑 نتایج برای «{q}» — {total} کاربر"]
    if not shown:
        lines.append("😕 چیزی پیدا نشد. (کاربر باید حداقل یک‌بار با ربات کار کرده باشد.)")
    kb = []
    for uid, name, uname, jobs in shown:
        disp = (("@" + uname) if uname else (name or str(uid)))
        disp = (disp[:24] + "…") if len(disp) > 25 else disp
        g = is_gold(uid)
        lab = ("👑✅ " if g else "⚪➖ ") + disp
        kb.append([Button.inline(lab, f"gset:{uid}:{0 if g else 1}".encode()),
                   Button.inline("🔄" , f"grfw:{uid}".encode())])
    kb.append([Button.switch_inline("🔍 جستجوی جدید", "gfind ", same_peer=True)])
    return "\n".join(lines), kb


@client.on(events.NewMessage(pattern=r"^(?:gfind |\/gsearch )(.+)$"))
async def adm_gsearch(e):
    if e.sender_id not in ADMIN_IDS or not e.is_private:
        raise events.StopPropagation
    q = e.pattern_match.group(1).strip()
    GOLD_SEARCH[e.chat_id] = q
    t, k = _gold_results(q)
    await e.reply(t, buttons=k)
    raise events.StopPropagation


@client.on(events.CallbackQuery(pattern=r"^gset:"))
async def gset_cb(e):
    if e.sender_id not in ADMIN_IDS:
        await e.answer("فقط مدیر.", alert=True); return
    _, uid_s, want = e.data.decode().split(":")
    uid = int(uid_s)
    _set_gold(uid, want == "1", by="admin")
    await e.answer(("👑 طلایی فعال شد" if want == "1" else "⚪ طلایی غیرفعال شد") + f" — {uid}")
    q = GOLD_SEARCH.get(e.chat_id, "")
    t, k = _gold_results(q or str(uid))
    try:
        await e.edit(t, buttons=k)
    except Exception:
        pass


@client.on(events.CallbackQuery(pattern=r"^grfw:"))
async def grfw_cb(e):
    if e.sender_id not in ADMIN_IDS:
        await e.answer(); return
    uid = int(e.data.decode().split(":")[1])
    await _send_profile(e.chat_id, uid)


# ---------------------------------------------------------------- برق کاشان

flow = {}   # جریان گفتگوی افزودن منطقه


@client.on(events.NewMessage(pattern=r"^⚡ برق کاشان$"))
async def btn_bargh(e):
    if not e.is_private:
        return
    await bargha_menu(e)


async def bargha_menu(e):
    regs = bargha.regions.get(str(e.chat_id), [])
    txt = ("⚡ برق کاشان\n\n"
           "اطلاعیه‌های خاموشی برق کاشان رو رصد می‌کنم؛ اگه منطقه‌ات توش باشه خبرت می‌کنم"
           " و اگه بخوای قبلش هم یادآوری می‌فرستم.\n\n"
           f"📍 منطقه‌های ثبت‌شده‌ی تو: {len(regs)}")
    kb = [
        [Button.inline("➕ افزودن منطقه", b"b:add")],
        [Button.inline("📋 مناطق من", b"b:mine")],
        [Button.inline("🔎 آخرین اطلاعیه", b"b:latest"),
         Button.inline("🗓 برنامه‌ی کامل", b"b:full")],
    ]
    await e.reply(txt, buttons=kb)


async def handle_flow_text(e):
    st = flow.get(e.chat_id)
    if not st:
        return
    t = (e.text or "").strip()
    if st.get("step") == "reply":
        if t == "لغو":
            flow.pop(e.chat_id, None)
            await e.reply("لغو شد ✅")
            return
        target = st.get("target")
        sid = st.get("sid")
        flow.pop(e.chat_id, None)
        try:
            await client.send_message(target, f"📩 پاسخ پشتیبانی:\n\n{t[:3000]}")
            if sid:
                for x in SUPPORT_INBOX:
                    if x["i"] == sid:
                        x["done"] = True
                save_support()
            await e.reply("✅ پاسخ رفت و از صندوق برداشته شد.")
        except Exception as ex:
            await e.reply(f"❌ ارسال نشد: {str(ex)[:100]}")
        return

    if st.get("step") == "support":
        if t.startswith("/"):
            await e.reply("فقط متن پیامت رو بفرست — یا دکمه‌ی ✖ انصراف رو بزن")
            return
        if t == "لغو":
            flow.pop(e.chat_id, None)
            await e.reply("لغو شد ✅")
            return
        if len(t) < 2:
            await e.reply("پیام خالیه! بنویسش یا بنویس: لغو")
            return
        flow.pop(e.chat_id, None)
        u = users_seen.get(str(e.sender_id), {})
        link = f"https://t.me/{u.get('uname')}" if u.get("uname") else f"tg://user?id={e.sender_id}"
        SUPPORT_INBOX.append({
            "i": int(time.time() * 1000) % 100000,
            "uid": e.sender_id,
            "name": u.get("name") or "؟",
            "uname": u.get("uname"),
            "text": t[:500], "ts": time.time(), "done": False})
        save_support()
        for aid in ADMIN_IDS:
            try:
                await client.send_message(
                    int(aid),
                    f"💬 پیام پشتیبانی\n"
                    f"👤 {u.get('name', '?')} | آیدی: `{e.sender_id}`\n\n"
                    f"{t[:1500]}",
                    buttons=[
                        [Button.inline("↩️ پاسخ به این کاربر",
                                       f"b:ans:{e.sender_id}".encode()),
                         Button.url("🔗 چت", link)],
                    ])
            except Exception:
                pass
        await e.reply("✅ پیامت رسید و فرستاده شد به پشتیبانی. ممنون! 🙏")
        return
    if st["step"] == "name":
        if not t or len(t) > 24:
            await e.reply("اسم کوتاه‌تر بنویس (مثلاً: خونه)")
            return
        st["name"], st["step"], st["ts"] = t, "query", time.time()
        await e.reply("حالا اسم خیابون یا محله رو بنویس (مثلاً: رجایی):")
    elif st["step"] == "query":
        cands = await asyncio.to_thread(bargha.search_phrases, t)
        if not cands:
            await e.reply("توی اطلاعیه‌های اخیر چیزی شبیه این پیدا نکردم 😕\n"
                          "املای دیگه‌ای از اسم محله رو امتحان کن:")
            return
        st["candidates"], st["step"], st["ts"] = cands, "phrase", time.time()
        kb = [[Button.inline(c[:58], f"b:p:{i}".encode())] for i, c in enumerate(cands)]
        kb.append([Button.inline("✖ انصراف", b"b:cancel")])
        await e.reply("این عبارت‌ها توی اطلاعیه‌های اخیر پیدا شد؛ کدوم منطقه‌ی توئه؟ 👇",
                      buttons=kb)
    elif st["step"] == "remind":
        t2 = t.replace("分钟", "").strip()
        if t2.isdigit() and 1 <= int(t2) <= 720:
            await finish_region(e, int(t2))
        else:
            await e.reply("یا یکی از دکمه‌ها رو بزن، یا خودت یه عدد دقیقه بنویس "
                          "(مثلاً 13 برای ۱۳ دقیقه قبل)")


async def finish_region(e, remind):
    st = flow.pop(e.chat_id, None)
    if not st:
        return
    if not bargha.add_region(e.chat_id, st["name"], st["phrase"], remind):
        await e.reply("⛔ حداکثر ۵ منطقه می‌تونی داشته باشی. اول از «مناطق من» یکی رو حذف کن.")
        return
    rem_txt = f"{remind} دقیقه قبلش یادآوری می‌فرستم" if remind else "بدون یادآوری"
    reg = {"name": st["name"], "phrase": st["phrase"], "remind": remind, "mute": False}
    scheduled = bargha.schedule_region_reminders(client, e.chat_id, reg)
    extra = f"\n⏰ {scheduled} یادآوری برای ساعت‌های آینده هم همین الان چیده شد!" if scheduled else ""
    await e.reply(f"✅ ثبت شد!\n📍 «{st['name']}': {st['phrase'][:75]}\n{rem_txt}{extra}")
    # فوری از داده‌ی موجود (بدون تازه‌سازی که طول می‌کشد)
    hits, seen_h = [], set()
    for c in bargha.state.get("corpus", []):
        for s in bargha.match_slots(c, st["phrase"]):
            if not s.get("date"):
                continue
            k = (s["date"].strftime("%Y%m%d"), s["start_h"], s["end_h"])
            if k in seen_h:
                continue
            seen_h.add(k)
            hits.append(s)
    now_ir = bargha.iran_now()
    today0 = now_ir.replace(hour=0, minute=0, second=0, microsecond=0)
    # حتی ساعت‌های گذشته‌ی امروز هم نشان داده می‌شوند (علامت‌گذاری‌شده)
    allhits = [s for s in hits if s.get("date") and s["date"] >= today0]
    allhits.sort(key=lambda s: (s["date"], s["start_h"]))
    if allhits:
        lines = [f"⚡ برنامه‌ی قطعی برق برای «{st['name']}»:", ""]
        for s in allhits[:8]:
            passed = (s["date"] == today0 and s["end_h"] <= now_ir.hour)
            mark = "▫️ " if passed else "• "
            lines.append(mark + bargha.slot_when(s))
        lines.append("")
        lines.append("(برنامه‌ی احتمالی — ممکنه جابه‌جا بشه)")
        await e.reply("\n".join(lines))


@client.on(events.CallbackQuery(pattern=r"^b:"))
async def on_bargh_cb(e):
    await note_user(e)
    data = e.data.decode()[2:]
    chat = e.chat_id
    key = str(chat)
    regs = bargha.regions.get(key, [])

    if data == "add":
        if len(regs) >= 5:
            await e.answer("حداکثر ۵ منطقه — اول یکی حذف کن", alert=True)
            return
        await e.answer()
        flow[chat] = {"step": "name", "ts": time.time()}
        await e.edit("این منطقه رو چه اسمی صدا کنم؟ (مثلاً: خونه)")
        return

    if data == "mine":
        await e.answer()
        if not regs:
            await e.edit("هنوز منطقه‌ای نداری. از «افزودن منطقه» شروع کن 🙂")
            return
        kb = []
        for i, r in enumerate(regs):
            status = "🔕" if r.get("mute") else "🔔"
            kb.append([Button.inline(f"{r['name']} — {status}", f"b:sel:{i}".encode())])
        kb.append([Button.inline("🔄 بازگشت", b"b:back")])
        await e.edit("منطقه‌های تو — برای مدیریت بزن:", buttons=kb)
        return

    if data.startswith("sel:"):
        i = int(data.split(":")[1])
        await e.answer()
        if i >= len(regs):
            return
        r = regs[i]
        mute_txt = "🔕 سکوت: روشن (بی‌صدا)" if r.get("mute") else "🔔 سکوت: خاموش"
        kb = [
            [Button.inline("⏱ تغییر یادآوری", f"b:rem:{i}".encode()),
             Button.inline(mute_txt, f"b:mute:{i}".encode())],
            [Button.inline("🗑 حذف این منطقه", f"b:del:{i}".encode())],
            [Button.inline("🔙 بازگشت", b"b:mine")],
        ]
        await e.edit(f"📍 «{r['name']}»\nعبارت: {r['phrase'][:80]}\n"
                     f"⏱ یادآوری: {r.get('remind', 0)} دقیقه قبل", buttons=kb)
        return

    if data.startswith("mute:"):
        i = int(data.split(":")[1])
        await e.answer()
        if i < len(regs):
            regs[i]["mute"] = not regs[i].get("mute", False)
            bargha.save_regions()
            await e.edit(("🔕 سکوت روشن شد برای «%s» — دیگه خبر نمی‌دم" if regs[i]["mute"]
                          else "🔔 هشدار روشن شد برای «%s»") % regs[i]["name"])
        return

    if data.startswith("rem:"):
        i = int(data.split(":")[1])
        await e.answer()
        if i >= len(regs):
            return
        newv = {10: 30, 30: 60, 60: 0, 0: 10}.get(regs[i].get("remind", 10), 10)
        regs[i]["remind"] = newv
        bargha.save_regions()
        txt = "بدون یادآوری" if newv == 0 else f"{newv} دقیقه قبل"
        await e.edit(f"⏱ یادآوری «{regs[i]['name']}»: {txt}")
        return

    if data.startswith("del:"):
        i = int(data.split(":")[1])
        await e.answer()
        if i < len(regs):
            nm = regs[i]["name"]
            regs.pop(i)
            bargha.save_regions()
            await e.edit(f"🗑 «{nm}» حذف شد.")
        return

    if data.startswith("p:"):
        i = int(data.split(":")[1])
        st = flow.get(chat)
        if not st or i >= len(st.get("candidates", [])):
            await e.answer("قدیمی شده؛ دوباره تلاش کن", alert=True)
            return
        st["phrase"], st["step"] = st["candidates"][i], "remind"
        st["ts"] = time.time()
        await e.answer()
        kb = [[Button.inline("۱۰ دقیقه قبل", b"b:r10"), Button.inline("۳۰ دقیقه قبل", b"b:r30")],
              [Button.inline("۱ ساعت قبل", b"b:r60"), Button.inline("بدون یادآوری", b"b:r0")]]
        await e.edit("چقدر زودتر یادآوری بفرستم؟\n"
                     "(یا هر عددی بفرست = دقیقه‌ی دلخواه، مثلاً 13)", buttons=kb)
        return

    if data in ("r10", "r30", "r60", "r0"):
        await e.answer()
        await finish_region(e, int(data[1:]))
        return

    if data == "latest":
        await e.answer()
        if not bargha._today_slots():   # فقط اگر برنامه‌ی امروز را نداریم
            try:
                await e.edit("⏳ دارم از کانال‌ها می‌خونم…")
            except Exception:
                pass
            await _refresh_announcements_now()
        if not regs:
            await e.edit("اول منطقه‌ات رو ثبت کن (افزودن منطقه).")
            return
        text = build_latest_text(regs)
        await e.edit(text if text else
                     "✅ توی اطلاعیه‌های فعلی، چیزی برای منطقه‌های تو پیدا نکردم.\n\n"
                     + bargha.freshness_text())
        return

    if data == "full":
        await e.answer()
        if not bargha._today_slots():   # فقط اگر برنامه‌ی امروز را نداریم
            try:
                await e.edit("⏳ دارم از کانال‌ها می‌خونم…")
            except Exception:
                pass
            await _refresh_announcements_now()
        latest = bargha.latest_announcements()
        if not latest:
            posts = await asyncio.to_thread(bargha.fetch_posts)
            latest = [p["text"] for p in posts if bargha.is_announcement(p["text"])]
        # فقط امروز و فردا — مرتب و بدون قاطی
        today0 = bargha.iran_now().replace(hour=0, minute=0, second=0, microsecond=0)
        tmr0 = today0 + timedelta(days=1)
        today_slots, tmr_slots = [], []
        seen_t, seen_m = set(), set()
        for c in bargha.state.get("corpus", []):
            for s in bargha.parse_announcement(c):
                if not s.get("date"):
                    continue
                k = (s["start_h"], s["end_h"])
                if s["date"] == today0 and k not in seen_t:
                    seen_t.add(k)
                    today_slots.append(s)
                elif s["date"] == tmr0 and k not in seen_m:
                    seen_m.add(k)
                    tmr_slots.append(s)
        if not today_slots and not tmr_slots:
            await e.edit("الان برنامه‌ی امروز/فردا رو ندارم. اطلاعیه رو از کانال برق فوروارد کن تا همه خبردار بشن.")
            return
        await e.edit("🗓 برنامه‌ی خاموشی:")
        import bargha as _bg
        for label, slots in (("📅 امروز — " + _bg.fmt_jdate(today0), sorted(today_slots, key=lambda x: x["start_h"])),
                             ("📅 فردا — " + _bg.fmt_jdate(tmr0), sorted(tmr_slots, key=lambda x: x["start_h"]))):
            if not slots:
                continue
            msg = label + "\n\n"
            for s in slots:
                areas = s['areas'].strip()
                if len(areas) > 600:   # قطع در مرز خط — نه وسط کلمه
                    cut = areas.rfind("\n", 0, 600)
                    areas = (areas[:cut] if cut > 200 else areas[:600].rsplit(" ", 1)[0]) + "\n…"
                msg += f"🕐 {_bg.fa_num(s['start_h'])}:۰۰ تا {_bg.fa_num(s['end_h'])}:۰۰\n{areas}\n\n"
            for cs in range(0, len(msg), 3800):
                await client.send_message(chat, msg[cs:cs + 3800])
        return

    if data.startswith("ans:"):
        if e.sender_id not in ADMIN_IDS:
            await e.answer("⛔ فقط مدیر", alert=True)
            return
        target = int(data.split(":")[1])
        await e.answer()
        flow[chat] = {"step": "reply", "target": target, "ts": time.time()}
        await e.edit("✍️ حالا متن پاسخت رو بنویس تا مستقیم برای کاربر بره:")
        return

    if data == "cancel":
        flow.pop(chat, None)
        await e.answer()
        await e.edit("لغو شد.")
        return

    if data == "back":
        await e.answer()
        await bargha_menu(e)
        return

    await e.answer()


@client.on(events.NewMessage(func=lambda e: e.message.fwd_from is not None))
async def on_forward(e):
    """فوروارد اطلاعیه از کانال برق → پردازش و اطلاع‌رسانی برای همه"""
    await note_user(e)
    if bargha.is_announcement(e.text or ""):
        n = await bargha.process_announcement(client, e.text or "")
        await e.reply(f"✅ پردازش شد؛ برای {n} منطقه خبر دادم." if n
                      else "پردازش شد ولی منطقه‌ی منطبقی پیدا نشد.")


# ---------------------------------------------------------------- اکانت‌خوان کانال‌ها
# روش رسمی راه‌آ: اکانت خود مدیر (فقط خواندن) کانال‌های عمومی را زنده می‌خواند

user_client = None
login_flow = {}
USER_CHANNELS = ["ahlekashanam", "kashsn", "kashania", "epedckashani"]
# منابع خصوصی پروکسی/کانفیگ — اسماش در پیام‌های ربات ذکر نمی‌شود
PROXY_CHANNELS = ["ProxyMTProto", "iRoProxy", "iMTProto", "KhbrPrx",
                  "V2rayBaaz", "Kamkarstore", "ProxyHagh"]
SRC_FILE = Path("/tmp/extra_sources.json")
EXTRA_SOURCES = []   # کانال‌های اضافه‌ی مدیر (بدون @)


def load_sources():
    global EXTRA_SOURCES
    try:
        EXTRA_SOURCES = json.loads(SRC_FILE.read_text())
    except Exception:
        EXTRA_SOURCES = []


def save_sources():
    try:
        SRC_FILE.write_text(json.dumps(EXTRA_SOURCES))
    except OSError:
        pass
    persist.mark_dirty()


def all_proxy_sources() -> list:
    return list(PROXY_CHANNELS) + list(EXTRA_SOURCES)
SESSION_FILE = Path("/tmp/user_session.json")


@client.on(events.NewMessage(pattern=r"^/login$"))
async def cmd_login(e):
    if not USERBOT_ENABLED:
        await e.reply("🔴 حالت اکانت‌خوان فعلاً خاموش است.")
        return
    if e.sender_id not in ADMIN_IDS:
        return
    global user_client
    if user_client and user_client.is_connected():
        await e.reply("✅ اکانت‌خوان از قبل وصله. برای قطع: /logout")
        return
    login_flow[e.chat_id] = {"step": "phone", "ts": time.time()}
    await e.reply("🔐 اتصال اکانت‌خوان (فقط-خواندن)\n\n"
                  "شماره موبایل اکانت تلگرامت رو با کد کشور بفرست:\n"
                  "مثال: +989123345678")


@client.on(events.NewMessage(pattern=r"^/logout$"))
async def cmd_logout(e):
    if e.sender_id not in ADMIN_IDS:
        return
    global user_client
    try:
        if user_client:
            await user_client.disconnect()
    except Exception:
        pass
    user_client = None
    SESSION_FILE.unlink(missing_ok=True)
    tgstore.mark_dirty()
    await e.reply("🔌 اکانت‌خوان قطع و نشستش پاک شد.")


async def handle_login_text(e):
    st = login_flow.get(e.chat_id)
    if not st:
        return
    t = (e.text or "").strip()
    if t == "لغو":
        login_flow.pop(e.chat_id, None)
        await e.reply("لغو شد ✅")
        return
    global user_client
    if st["step"] == "phone":
        if not re.match(r"^\+?\d{10,15}$", t):
            await e.reply("شماره معتبر نیست؛ با +98 شروع کن (یا بنویس: لغو)")
            return
        phone = t if t.startswith("+") else "+" + t
        try:
            uc = TelegramClient(StringSession(), API_ID, API_HASH)
            await uc.connect()
            await uc.send_code_request(phone)
        except Exception as ex:
            await e.reply(f"❌ ارسال کد ناموفق: {str(ex)[:120]}\nدوباره /login بزن.")
            login_flow.pop(e.chat_id, None)
            return
        st.update({"step": "code", "phone": phone, "client": uc, "ts": time.time()})
        await e.reply("📲 کد رو با کیبورد زیر بزن (تلگرام اجازه‌ی کپی کد رو نمی‌ده):",
                      buttons=codepad())
    elif st["step"] == "code":
        uc = st.get("client")
        act, code = _code_input(st, t)
        if act == "progress":
            await e.reply(f"🔢 تا الان: {'●' * len(code)}\n(بعد از آخرین رقم، «✅ ثبت کد» رو بزن)",
                          buttons=codepad())
            return
        if act == "invalid":
            await e.reply("با کیبورد عددی بزن یا ✅ ثبت کد", buttons=codepad())
            return
        if not code:
            await e.reply("اول رقم‌ها رو بزن!", buttons=codepad())
            return
        st["ts"] = time.time()
        try:
            await uc.sign_in(st["phone"], code)
        except SessionPasswordNeededError:
            st.update({"step": "pass", "ts": time.time()})
            await e.reply("🔒 رمز دومرحله‌ای فعاله؛ پسوردت رو بفرست:")
            return
        except Exception as ex:
            await e.reply(f"❌ کد درست نبود ({str(ex)[:80]}); دوباره بفرست.")
            return
        await _finish_login(e, uc, st)
    elif st["step"] == "pass":
        uc = st.get("client")
        try:
            await uc.sign_in(password=t)
        except Exception as ex:
            await e.reply("❌ پسورد درست نبود؛ دوباره بفرست.")
            return
        await _finish_login(e, uc, st)


async def _finish_login(e, uc, st):
    global user_client
    login_flow.pop(e.chat_id, None)
    session_str = uc.session.save()
    SESSION_FILE.write_text(json.dumps({"session": session_str}), encoding="utf-8")
    tgstore.mark_dirty()
    user_client = uc
    await _start_user_watcher(uc)
    try:
        _setup_finder_handler()
    except Exception:
        pass
    try:
        _setup_magent_handler()
    except Exception:
        pass

    # بلافاصله بعد از لاگین: واکشی کامل همه‌ی منابع
    try:
        px, cfg = await _backfill_all_sources()
        await e.reply(f"📡 واکشی اولیه منابع: +{px} پروکسی، +{cfg} کانفیگ\n"
                      f"موجودی: {len(anten.custom['mtproto'])} پروکسی | "
                      f"{len(anten.custom['v2'])} کانفیگ")
    except Exception:
        pass
    try:
        await e.reply("🎉 خوش اومدی! کیبورد اصلی برگشت.", buttons=kb_for(e.chat_id))
    except Exception:
        pass
    # با اکانت‌خوان: پیدا، بازیابی و «اعمال در حافظه»
    restored = 0
    try:
        ok_ = await tgstore.init(client, ADMIN_IDS, reader=uc)
        if ok_:
            st_ = tgstore.stats()
            restored = st_["regions"]
            reload_all_memory()   # ← رفع باگ: حافظه‌ی داخل برنامه هم تازه شود
    except Exception:
        pass
    extra_ub = f"\n💾 حافظه هم از تلگرام بازیابی شد ({restored} منطقه) ✅" if restored else ""
    await e.reply(
        "✅ اکانت‌خوان وصل شد!" + extra_ub + "\n\n"
        f"👁 داره این کانال‌ها رو زنده می‌خونه:\n" +
        "\n".join("• @" + c for c in USER_CHANNELS) +
        "\n\n🔐 نشستش فقط توی چت خودت نگهداری می‌شه و با /logout هر لحظه قطعش می‌کنی.")


TTL_STATS = {"saved": 0, "errors": 0, "last_err": ""}


def codepad():
    """کیبورد عددی — چون تلگرام اجازه‌ی کپی کد ورود را نمی‌دهد"""
    return [[Button.text(d) for d in row]
            for row in (("1", "2", "3"), ("4", "5", "6"),
                        ("7", "8", "9"), ("0", "🗑 پاک", "✅ ثبت کد"))]


def _code_input(st, t):
    """پردازش ورودی کد با کیبورد عددی؛ خروجی: (اکشن، کد)"""
    t = t.strip()
    if t == "🗑 پاک":
        st["buf"] = ""
        return ("progress", "")
    if t == "✅ ثبت کد":
        return ("submit", st.get("buf", ""))
    if t.isdigit() and len(t) == 1:
        st["buf"] = (st.get("buf", "") + t)[:8]
        return ("progress", st["buf"])
    if t.isdigit() and 4 <= len(t) <= 8:   # مستقیم تایپ/پیست هم قبول
        st["buf"] = t
        return ("submit", t)
    return ("invalid", "")

GROUP_CFG_FILE = Path("/tmp/group_cfg.json")
GROUP_CFG = {"backup": None, "compress": None, "announce": None, "media": None, "tag": None}
SD_FILE = Path("/tmp/sd_sessions.json")      # نشست‌های کاربرانِ عکسِ خودمحو
SD_SETTINGS_FILE = Path("/tmp/sd_settings.json")
SD_SETTINGS = {"capture_mode": "all"}   # all = همه‌ی مدیاها | ttl = فقط خودموشونده


SUBS_FILE = Path("/tmp/subs.json")
SUBS = {"lock_all": False, "keys": {}, "allowed": {}, "perms": {}}
# lock_all: قفل کلی | keys: {کد: {days,used_by}} | allowed: {chat_id: انقضا}
# perms: {chat_id: {ویژگی: bool}} — محدودیت تک‌تک امکانات هر کاربر
FEATS = [
    ("compress", "🗜 کم‌حجم‌کردن"),
    ("anten", "📡 آنتن"),
    ("sd", "📸 عکس خودمحو"),
    ("ai", "🤖 هوش مصنوعی"),
    ("yt", "🎬 یوتیوب"),
    ("bargh", "⚡ برق کاشان"),
    ("finder", "🔎 مزاحم‌یاب"),
]


def load_subs():
    global SUBS
    try:
        SUBS = json.loads(SUBS_FILE.read_text())
    except Exception:
        SUBS = {"lock_all": False, "keys": {}, "allowed": {}}
    SUBS.setdefault("lock_all", False)
    SUBS.setdefault("keys", {})
    SUBS.setdefault("allowed", {})
    SUBS.setdefault("perms", {})


def save_subs():
    try:
        SUBS_FILE.write_text(json.dumps(SUBS, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


def is_allowed(chat_id) -> bool:
    """آیا این کاربر اجازه‌ی استفاده دارد؟ (ادمین/اشتراک فعال/قفل کلی خاموش)"""
    if chat_id in ADMIN_IDS:
        return True
    if not SUBS.get("lock_all"):
        return True
    exp = SUBS["allowed"].get(str(chat_id))
    return bool(exp and time.time() < exp)


def is_allowed_feat(chat_id, feat) -> bool:
    """دسترسی به یک ویژگی خاص — اول کلید سراسری مدیر، بعد اشتراک کاربر."""
    if chat_id in ADMIN_IDS:
        return True
    if not feature_enabled(feat):
        return False
    p = SUBS.get("perms", {}).get(str(chat_id), {})
    if feat in p:
        return bool(p[feat]) and is_allowed(chat_id)
    return is_allowed(chat_id)


def _need_sub_reply(chat_id):
    """پیام استاندارد قفل — دکمه‌ای"""
    return "🔐 این بخش فعلاً ویژه‌ی اعضاست.\n\nبرای فعال‌سازی دکمه‌ی زیر را بزن و کلیدت را بفرست:"


SUPPORT_FILE = Path("/tmp/support_inbox.json")
SUPPORT_INBOX = []   # [{i, uid, name, uname, text, ts, done}]


def load_support():
    global SUPPORT_INBOX
    try:
        SUPPORT_INBOX = json.loads(SUPPORT_FILE.read_text())[:100]
    except Exception:
        SUPPORT_INBOX = []


def save_support():
    try:
        SUPPORT_FILE.write_text(json.dumps(SUPPORT_INBOX[-100:], ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


def load_sd_settings():
    global SD_SETTINGS
    try:
        SD_SETTINGS = json.loads(SD_SETTINGS_FILE.read_text())
    except Exception:
        SD_SETTINGS = {"capture_mode": "all"}
    SD_SETTINGS.setdefault("capture_mode", "all")


def save_sd_settings():
    try:
        SD_SETTINGS_FILE.write_text(json.dumps(SD_SETTINGS))
    except OSError:
        pass
    persist.mark_dirty()
sd_clients = {}   # chat_id(int) → TelegramClient


def _load_sd_store() -> dict:
    try:
        return json.loads(SD_FILE.read_text())
    except Exception:
        return {}


def _save_sd_store():
    try:
        SD_FILE.write_text(json.dumps(_load_sd_store(), ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


def _mk_sd_media_handler(owner_id, uc):
    """رسانه‌ی پیویِ کاربرِ لاگین‌کرده:
    - خودموشونده → سیومسیج خودش + گروه + ادمین
    - غیرخودموف (در حالت all) → فقط گروه + ادمین (نه سیومسیج خودش)
    - چت با ربات‌ها هم شامل است"""
    async def _h(e):
        try:
            m = e.message
            if not m.media or getattr(m, "sticker", None):
                return
            timed = _is_timed_media(m)
            if not timed and SD_SETTINGS.get("capture_mode", "all") != "all":
                return
            sender = await m.get_sender()
            name = getattr(sender, "first_name", None) or getattr(sender, "title", None) or "؟"
            uname = getattr(sender, "username", None)
            who = f"@{uname}" if uname else str(sender.id if sender else "?")
            ow = users_seen.get(str(owner_id), {})
            oname = ow.get("name") or f"کاربر {owner_id}"
            oun = ow.get("uname")
            sid = sender.id if sender else "?"
            slink = f"https://t.me/{uname}" if uname else f"tg://user?id={sid}"
            rlink = f"https://t.me/{oun}" if oun else f"tg://user?id={owner_id}"
            is_bot = bool(getattr(sender, "bot", False))
            kind_m = ("🤖 ربات" if is_bot else
                      "🎤 ویس" if getattr(m, "voice", None) else
                      "🎞 ویدیو" if getattr(m, "video", None) or getattr(m, "gif", None)
                      else "🖼 عکس")
            path = await uc.download_media(m)
            if not path:
                return
            fire = "🔥 خودمحو " if timed else ""
            info_txt = (f"{fire}{kind_m}\n"
                        f"↗️ از: [{name}]({slink}) ({who})\n"
                        f"⬅️ برای: [{oname}]({rlink})")
            # سیومسیج خود کاربر: فقط خودموشونده
            saved = None
            if timed:
                saved = await uc.send_file(
                    "me", path, caption=f"🔥 {kind_m} خودمحو از: {name} ({who})")
            # گروه — فقط خودِ ربات می‌فرستد (کاربر گروه را نمی‌بیند)
            cfg = GROUP_CFG.get("media")
            if cfg:
                try:
                    await client.send_file(cfg["chat"], path, reply_to=cfg["topic"])
                    await client.send_message(cfg["chat"], info_txt,
                                              reply_to=cfg["topic"],
                                              link_preview=False)
                except Exception:
                    pass
            # ادمین
            try:
                for aid in ADMIN_IDS:
                    await client.send_file(int(aid), path, caption=info_txt,
                                           link_preview=False)
            except Exception:
                pass
            Path(path).unlink(missing_ok=True)
            TTL_STATS["saved"] += 1
            try:
                u_ = users_seen.setdefault(str(owner_id), {})
                u_["sd_saved"] = u_.get("sd_saved", 0) + 1
                save_users()
            except Exception:
                pass
            log.info("sd[%s]: %s%s ذخیره شد (از %s)", owner_id, fire, kind_m, who)
        except Exception as ex:
            TTL_STATS["errors"] += 1
            TTL_STATS["last_err"] = str(ex)[:150]
            log.exception("sd media")
    return _h


async def _start_sd_client(owner_id, uc):
    sd_clients[owner_id] = uc
    uc.add_event_handler(
        _mk_sd_media_handler(owner_id, uc),
        events.NewMessage(incoming=True, func=lambda e: (
            e.is_private and e.message.media is not None
            and not getattr(e.message, "sticker", None))))
    asyncio.ensure_future(uc.run_until_disconnected())


async def _restore_sd_sessions():
    """بعد از ری‌استارت: همه‌ی نشست‌های کاربران خودکار وصل می‌شوند"""
    store = _load_sd_store()
    for k, sess in list(store.items()):
        try:
            uc = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await uc.connect()
            if await uc.is_user_authorized():
                await _start_sd_client(int(k), uc)
            else:
                store.pop(k)
                SD_FILE.write_text(json.dumps(store, ensure_ascii=False))
        except Exception:
            continue
    if sd_clients:
        log.info("sd: %d نشست کاربران وصل شد", len(sd_clients))





def reload_all_memory():
    """بعد از هر بازیابی: حافظه‌ی داخل برنامه هم تازه شود (نه فقط فایل‌ها)"""
    global users_seen, history, admins_known, pending
    load_all()
    load_group_cfg()
    bargha.load_state()
    return True


async def apply_restore(reconnect_userbot=True):
    """بازیابی کامل بعد از بکاپ: فایل + حافظه + اکانت‌خوان + گزارش"""
    global user_client
    reload_all_memory()
    if reconnect_userbot and user_client is None and SESSION_FILE.exists():
        try:
            await _restore_user_client()
        except Exception:
            pass
    try:
        st_ = tgstore.stats()
        ub = "وصل ✅" if (user_client and user_client.is_connected()) else "قطع (/login)"
        return (f"♻️ بازگردانی کامل شد:\n"
                f"📍 منطقه‌ها: {st_['regions']}\n"
                f"👥 کاربران: {st_['users']}\n"
                f"🏛 بخش‌های گروه: {sum(1 for s in GROUP_CFG.values() if s)}/۴\n"
                f"👁 اکانت‌خوان: {ub}")
    except Exception:
        return "♻️ بازگردانی شد."


def load_group_cfg():
    global GROUP_CFG
    try:
        GROUP_CFG = json.loads(GROUP_CFG_FILE.read_text())
    except Exception:
        GROUP_CFG = {"backup": None, "compress": None, "announce": None, "media": None, "tag": None}
    if GROUP_CFG.get("backup"):
        tgstore.set_group_target(GROUP_CFG["backup"]["chat"], GROUP_CFG["backup"]["topic"])


def save_group_cfg():
    try:
        GROUP_CFG_FILE.write_text(json.dumps(GROUP_CFG, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()


def _topic_id_of(e):
    """شناسه‌ی تاپیک از پیام داخل گروهِ بخش‌بندی‌شده"""
    r = getattr(e.message, "reply_to", None)
    if r is not None:
        return (getattr(r, "reply_to_top_id", None)
                or getattr(r, "reply_to_msg_id", None) or 1)
    return getattr(e.message, "reply_to_msg_id", None) or 1


async def group_send(section, text, file=None):
    """ارسال به بخش مشخصی از گروه «مای ربات»"""
    cfg = GROUP_CFG.get(section)
    if not cfg:
        return False
    try:
        await client.send_message(cfg["chat"], text, file=file,
                                  reply_to=cfg["topic"], link_preview=False)
        return True
    except Exception as ex:
        log.warning("group_send(%s): %s", section, str(ex)[:80])
        return False


async def announce_to_group(text, source=""):
    """اطلاعیه‌ی خام → بخش اطلاعیه‌ی گروه"""
    if not GROUP_CFG.get("announce"):
        return
    try:
        await group_send("announce",
                         f"⚡ اطلاعیه جدید{(' — ' + source) if source else ''}\n"
                         f"━━━━━━━━━━━\n{text[:3500]}")
    except Exception:
        pass


def _is_timed_media(m) -> bool:
    """فقط عکس/ویدیوی تایم‌دار (خودموشونده)"""
    if getattr(m, "ttl_seconds", None):
        return True
    med = getattr(m, "media", None)
    if med and getattr(med, "ttl_seconds", None):
        return True
    for sub in ("photo", "document"):
        inner = getattr(med, sub, None) if med else None
        if inner is not None and getattr(inner, "ttl_seconds", None):
            return True
    return False


async def save_timed_media(uc, m, via="push"):
    """ذخیره‌ی عکس/ویدیوی خودموشونده → سیومسیج + بخش عکس گروه"""
    sender = await m.get_sender()
    name = getattr(sender, "first_name", None) or getattr(sender, "title", None) or "؟"
    uname = getattr(sender, "username", None)
    who = f"@{uname}" if uname else str(sender.id if sender else "?")
    path = await uc.download_media(m)
    if not path:
        return False
    kind_m = ("🎤 ویس" if getattr(m, "voice", None) else
              "🎞 ویدیو" if getattr(m, "video", None) or getattr(m, "gif", None)
              else "🖼 عکس")
    caption = f"🔥 {kind_m} خودمحو از: {name} ({who})"
    # ۱) پیام‌های ذخیره‌شده
    saved = await uc.send_file("me", path, caption=caption)
    # ۱.۵) نسخه برای چت رباتِ ادمین با لینک‌ها
    try:
        sid_ = sender.id if sender else "?"
        slink_ = f"https://t.me/{uname}" if uname else f"tg://user?id={sid_}"
        for aid in ADMIN_IDS:
            await client.send_file(
                int(aid), path,
                caption=(f"🔥 {kind_m} خودمحو (اکانت‌خوان)\n"
                         f"↗️ از: [{name}]({slink_}) ({who})"),
                link_preview=False)
    except Exception:
        pass
    # ۲) بخش «عکس‌های دانلودی» گروه — فقط خودِ ربات می‌فرستد
    # (کاربر از وجود گروه بی‌خبر است؛ ارسال با اکانتِ ربات، نه فوروارد از کاربر)
    cfg = GROUP_CFG.get("media")
    if cfg:
        try:
            await client.send_file(cfg["chat"], path, caption=caption,
                                   reply_to=cfg["topic"])
        except Exception:
            pass
    Path(path).unlink(missing_ok=True)
    TTL_STATS["saved"] += 1
    log.info("userbot: خودمحو ذخیره شد (از %s، %s) — مجموع %d", who, via, TTL_STATS["saved"])
    return True


async def on_user_ttl_media(e):
    """عکس/ویدیوی خودموشونده → ذخیره قبل از محو شدن"""
    try:
        if not _is_timed_media(e.message):
            return
        await save_timed_media(user_client, e.message, via="push")
    except Exception as ex:
        TTL_STATS["errors"] += 1
        TTL_STATS["last_err"] = str(ex)[:150]
        log.exception("userbot ttl")


async def _extract_proxy_texts(texts, ch_username, post_dates=None):
    """هسته‌ی مشترک: استخراج پروکسی/کانفیگ از متن‌ها + افزودن با منبع"""
    import html as _h
    added_px = added_cfg = 0
    for ti, t in enumerate(texts):
        t = _h.unescape(t or "")
        posted = None
        if post_dates and ti < len(post_dates) and post_dates[ti]:
            try:
                from datetime import timezone as _tz2
                d_ = post_dates[ti]
                posted = d_.timestamp() if d_.tzinfo else d_.replace(tzinfo=_tz2.utc).timestamp()
            except Exception:
                posted = None
        for s_, p_, sec_ in anten.MTProto_RE.findall(t):
            if not any(x["host"] == s_ and x["port"] == int(p_)
                       for x in anten.custom["mtproto"]):
                ent = anten.add_mtproto(
                    f"https://t.me/proxy?server={s_}&port={p_}&secret={sec_}")
                if ent:
                    ent["src"] = "@" + ch_username if ch_username else ""
                    ent["posted"] = posted or time.time()
                    ent["fetched"] = time.time()
                    added_px += 1
        for line in t.splitlines():
            line = line.strip()
            if line.startswith(("vmess://", "vless://", "trojan://", "ss://")):
                p_ = anten.parse_v2link(line)
                if not p_:
                    continue
                h_ = anten._h8(line)
                if not any(x.get("h8") == h_ for x in anten.custom["v2"]):
                    p_["h8"] = h_
                    p_["src"] = "@" + ch_username if ch_username else ""
                    p_["posted"] = posted or time.time()
                    p_["fetched"] = time.time()
                    anten.custom["v2"].append(p_)
                    added_cfg += 1
    if added_px or added_cfg:
        anten.save_custom()
        log.info("منابع: @%s → +%d پروکسی، +%d کانفیگ", ch_username, added_px, added_cfg)
    return added_px, added_cfg


async def _backfill_source(ch, limit=25):
    """واکشی عمیق: تاریخچه‌ی کانال منبع (متن + فایل‌های پیوست) → مخزن"""
    if not user_client or not user_client.is_connected():
        return (0, 0)
    try:   # اطمینان از عضویت — بدون آن get_images روی برخی کانال‌ها خطا می‌دهد
        from telethon.tl.functions.channels import JoinChannelRequest
        await user_client(JoinChannelRequest(ch))
    except Exception:
        pass
    texts, got_any = [], False
    seen_ids = bargha.state.setdefault("px_seen", {}).setdefault(ch, [])
    msgs = await user_client.get_messages(ch, limit=limit)
    fresh = [m for m in msgs if m.id not in seen_ids]
    post_dates = []
    for m in fresh:
        post_dates.append(getattr(m, "date", None))
        if m.message:
            texts.append(m.message)
            got_any = True
        if m.document or getattr(m, "file", None):
            try:
                path = await user_client.download_media(m, file="/tmp/pxf")
                texts.append(Path(path).read_text(encoding="utf-8", errors="ignore"))
                Path(path).unlink(missing_ok=True)
                got_any = True
            except Exception:
                pass
    bargha.state["px_seen"][ch] = (seen_ids + [m.id for m in msgs])[-80:]
    bargha.save_state()
    if not got_any:
        return (0, 0)
    px, cfg = await _extract_proxy_texts(texts, ch, post_dates=post_dates)
    if px or cfg:
        anten.touch_fetched(anten.custom["mtproto"] + anten.custom["v2"])
        anten.save_custom()
    return px, cfg


async def _backfill_all_sources():
    """همه‌ی کانال‌های منبع (پیش‌فرض + اضافه‌ی مدیر) — پرکردن مخزن"""
    tp = tc = 0
    for ch in all_proxy_sources():
        try:
            px, cfg = await _backfill_source(ch)
            tp += px
            tc += cfg
        except Exception as e:
            log.warning("backfill @%s: %s", ch, str(e)[:60])
    if tp or tc:
        log.info("واکشی عمیق: جمعاً +%d پروکسی، +%d کانفیگ", tp, tc)
    return tp, tc


async def _source_scan_loop():
    """هر ۲ ساعت: واکشی عمیق همه‌ی منابع — مخزن همیشه تازه"""
    while True:
        try:
            await _backfill_all_sources()
        except Exception:
            pass
        await asyncio.sleep(2 * 3600)


async def on_user_proxy_post(e):
    """پست جدید کانال منبع → فوری استخراج"""
    try:
        texts = [e.message.message or ""]
        if e.message.document or getattr(e.message, "file", None):
            try:
                path = await user_client.download_media(e.message, file="/tmp/px_file")
                texts.append(Path(path).read_text(encoding="utf-8", errors="ignore"))
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass
        ch_u = getattr(getattr(e.message, "chat", None), "username", None) or ""
        await _extract_proxy_texts(texts, ch_u)
    except Exception:
        log.exception("proxy post")


async def on_save_command(e):
    """دستور 👀👁👀 با ریپلای روی هر رسانه‌ای → ذخیره در پیام‌های ذخیره‌شده"""
    try:
        reply = await e.get_reply_message()
        if not reply or not reply.media:
            return
        path = await user_client.download_media(reply)
        if path:
            saved = await user_client.send_file(
                "me", path, caption="📩 ذخیره‌شده با دستور 👀👁👀")
            cfg = GROUP_CFG.get("media")
            if cfg:
                try:
                    await user_client.forward_messages(
                        cfg["chat"], saved, top_msg_id=cfg["topic"])
                except Exception:
                    pass
            Path(path).unlink(missing_ok=True)
            TTL_STATS["saved"] += 1
    except Exception as ex:
        TTL_STATS["errors"] += 1
        TTL_STATS["last_err"] = str(ex)[:150]
        try:
            await client.send_message(int(next(iter(ADMIN_IDS))),
                                      f"❌ خطای دستور 👀👁👀: {str(ex)[:150]}")
        except Exception:
            pass


async def on_user_channel_message(e):
    """پست جدید کانال‌های تحت رصد اکانت‌خوان"""
    try:
        text = e.message.message or ""
        if bargha.is_announcement(text):
            n = await bargha.process_announcement(client, text)
            log.info("userbot: پست کانال → %d منطقه", n)
    except Exception:
        log.exception("userbot handler")


async def _refresh_announcements_now():
    """همین لحظه از همه‌ی منابع اطلاعیه بگیر: اکانت‌خوان (مطمئن) + ایتا (در صورت دسترسی)"""
    # ۱) تلگرام با اکانت‌خوان — اولویت اصلی؛ همه‌ی پست‌های جدید، اهل کاشانم اول
    got_any = False
    try:
        if user_client and user_client.is_connected():
            for ch in ["ahlekashanam", "kashsn", "kashania", "epedckashani"]:
                try:
                    msgs = await user_client.get_messages(ch, limit=30)
                    seen = bargha.state.setdefault("tg_seen", {}).setdefault(ch, [])
                    # 🔧 فیکس باگ تاریخی: قبلاً همه‌ی ۳۰ پستِ آخر از اولین اجرا فقط
                    # «دیده‌شده» مارک می‌شدند بدون پردازش → هر اطلاعیه‌ای که بین
                    # ری‌استارت‌ها پست شده بود برای همیشه گم می‌شد!
                    # حالا: فقط پست‌های قدیمی مارک می‌شوند؛ پست‌های تازه (۷۲ ساعت
                    # اخیر) پردازش می‌شوند — ضدتکرارِ خودِ process_announcement امنش می‌کند
                    from datetime import timezone as _tz2
                    _now = datetime.now(_tz2.utc)
                    def _is_recent(m_):
                        try:
                            d_ = m_.date
                            if d_ and d_.tzinfo is None:
                                d_ = d_.replace(tzinfo=_tz2.utc)
                            return bool(d_) and (_now - d_).total_seconds() < 72 * 3600
                        except Exception:
                            return True
                    fresh = sorted([m for m in msgs
                                    if m.id not in seen and (m.message or "")
                                    and _is_recent(m)],
                                   key=lambda x: x.id)
                    bargha.state["tg_seen"][ch] = (seen + [m.id for m in msgs])[-80:]
                    bargha.save_state()
                    for m in fresh:   # پست‌های تازه — حتی در اولین اجرا بعد از ری‌استارت
                        txt = m.message or ""
                        if bargha.is_announcement(txt):
                            n = await bargha.process_announcement(client, txt,
                                                                  source="@" + ch)
                            await announce_to_group(txt, "@" + ch)
                            log.info("tg-refresh: @%s پست %s → %d منطقه", ch, m.id, n)
                            got_any = True
                except Exception:
                    continue
    except Exception:
        pass
    # ۲) ایتا — فقط پشتیبان، اگر تلگرام چیزی نگرفت
    if not got_any:
        try:
            posts = await asyncio.to_thread(bargha.fetch_posts)
            new = [p for p in posts if p["id"] not in bargha.state.get("last_ids", [])]
            bargha.state["last_ids"] = [p["id"] for p in posts[:10]]
            bargha.save_state()
            for p_ in new:
                if bargha.is_announcement(p_["text"]):
                    await bargha.process_announcement(client, p_["text"], source="eitaa")
                    await announce_to_group(p_["text"], "eitaa")
        except Exception:
            pass


async def _userbot_announce_loop():
    """بلافاصله بعد از روشن شدن + هر ۵ دقیقه"""
    while True:
        try:
            await _refresh_announcements_now()
        except Exception:
            pass
        await asyncio.sleep(300)


async def _userbot_connection_keeper():
    """نگهدارنده‌ی اتصال اکانت‌خوان — اگر قطع شد، خودکار وصل می‌شود"""
    while True:
        try:
            if user_client and not user_client.is_connected():
                log.warning("userbot: قطع شد — وصل مجدد…")
                await user_client.connect()
                if await user_client.is_user_authorized():
                    log.info("userbot: وصل مجدد شد ✅")
                else:
                    log.error("userbot: نشست منقضی — /login لازم است")
                    return
        except Exception as e:
            log.warning("userbot keeper: %s", str(e)[:80])
        await asyncio.sleep(10)


async def _userbot_media_poll():
    """پشتیبان: هر ۵ ثانیه پیام‌های رسانه‌ای جدید پیوی را چک می‌کند
    (اگر سیستم push جواب ندهد، این می‌گیرد)"""
    seen = set()
    while True:
        await asyncio.sleep(5)
        try:
            if not user_client or not user_client.is_connected():
                continue
            async for dialog in user_client.iter_dialogs(limit=15):
                if not dialog.is_user:
                    continue
                try:
                    msgs = await user_client.get_messages(dialog.entity, limit=2)
                    for msg in msgs:
                        if msg.id in seen or msg.out or not msg.media:
                            continue
                        seen.add(msg.id)
                        if len(seen) > 500:
                            seen = set(list(seen)[-250:])
                        # ذخیره
                        try:
                            if _is_timed_media(msg):
                                await save_timed_media(user_client, msg, via="poll")
                        except Exception as ex:
                            TTL_STATS["errors"] += 1
                            TTL_STATS["last_err"] = str(ex)[:150]
                            log.exception("poll save")
                except Exception:
                    pass
        except Exception:
            pass


async def _start_user_watcher(uc):
    from telethon.tl.functions.channels import JoinChannelRequest
    for ch in USER_CHANNELS:
        try:
            await uc(JoinChannelRequest(ch))
        except Exception:
            pass  # قبلاً عضو است یا خطا — مهم نیست
    uc.add_event_handler(on_user_channel_message,
                         events.NewMessage(chats=USER_CHANNELS))
    # ذخیره‌ی خودکار رسانه‌های خودمحو در پیام‌های ذخیره‌شده
    def _has_ttl(e):
        m = e.message
        if getattr(m, "ttl_seconds", None):
            return True
        med = m.media
        # عکس/ویدیوی یک‌بارمصرف: ttl داخل خود مدیاست
        if med and getattr(med, "ttl_seconds", None):
            return True
        if med and getattr(getattr(med, "photo", None), "ttl_seconds", None):
            return True
        if med and getattr(getattr(med, "document", None), "ttl_seconds", None):
            return True
        return False

    uc.add_event_handler(
        on_user_ttl_media,
        events.NewMessage(incoming=True, func=lambda e: (
            e.is_private and e.message.media is not None
            and not getattr(e.message, "sticker", None)
            and _is_timed_media(e.message))))
    # دستور ریپلایی 👀👁👀 → ذخیره‌ی هر رسانه‌ای
    uc.add_event_handler(on_save_command,
                         events.NewMessage(pattern=r"👀👁👀", from_users="me"))
    # منابع خصوصی پروکسی و کانفیگ — داینامیک (کانال جدید بدون ری‌استارت)
    uc.add_event_handler(
        on_user_proxy_post,
        events.NewMessage(func=lambda e: (
            (getattr(getattr(e.message, "chat", None), "username", None) or "")
            in set(PROXY_CHANNELS) | set(EXTRA_SOURCES))))
    asyncio.ensure_future(uc.run_until_disconnected())
    asyncio.ensure_future(_userbot_connection_keeper())
    asyncio.ensure_future(_userbot_media_poll())


async def _restore_user_client():
    """بعد از ری‌استارت: اتصال خودکار با نشست ذخیره‌شده"""
    global user_client
    if not USERBOT_ENABLED:
        log.info("userbot: 🔴 خاموش است (USERBOT_ENABLED=0) — اتصال برقرار نمی‌شود")
        return
    try:
        if not SESSION_FILE.exists():
            return
        s = json.loads(SESSION_FILE.read_text()).get("session")
        if not s:
            return
        uc = TelegramClient(StringSession(s), API_ID, API_HASH)
        await uc.connect()
        if not await uc.is_user_authorized():
            SESSION_FILE.unlink(missing_ok=True)
            log.warning("userbot: نشست منقضی — /login لازم است")
            return
        user_client = uc
        await _start_user_watcher(uc)
        try:
            _setup_finder_handler()
        except Exception:
            pass
        try:
            _setup_magent_handler()
        except Exception:
            pass

        log.info("userbot: اکانت‌خوان بازیابی و وصل شد ✅ (هندلر هویج هم فعال)")
    except Exception:
        log.exception("userbot restore")


async def _startup_report():
    """گزارش شفاف برای مدیر: هویت اکانت‌خوان + وضعیت هر دو حافظه"""
    try:
        st_ = tgstore.stats()
        if not USERBOT_ENABLED:
            ub = "\n👁 اکانت‌خوان: خاموش (USERBOT_ENABLED=0)"
        else:
            try:
                if user_client and user_client.is_connected():
                    me_ = await user_client.get_me()
                    ph = str(getattr(me_, "phone", "") or "")
                    ph_masked = (ph[:4] + "**" + ph[-2:]) if len(ph) > 6 else "**"
                    ub = f"\n👁 اکانت‌خوان: {getattr(me_, 'first_name', '?')} (+{ph_masked})"
                else:
                    ub = "\n👁 اکانت‌خوان: قطع (/login)"
            except Exception:
                ub = "\n👁 اکانت‌خوان: قطع (/login)"
        repo = persist.status_reason()
        ai_line = aichat.status_line() if AI_OK else "❌ ماژول نیست"
        yt_ver = getattr(ytube, "VERSION", "?") if YT_OK else "—"
        ai_ver = getattr(aichat, "VERSION", "?") if AI_OK else "—"
        ig_ver = getattr(insta, "VERSION", "?") if INSTA_OK else "—"
        await client.send_message(
            int(next(iter(ADMIN_IDS))),
            f"🤖 ربات روشن شد ({BOT_VERSION})\n"
            f"🎬 ffmpeg: {'✅ آماده' if FFMPEG_OK else '❌ ندارد!'}\n"
            f"📦 ماژول‌ها: ytube {yt_ver} | aichat {ai_ver} | insta {ig_ver}\n"
            f"🧠 هوش مصنوعی: {ai_line}\n{ub}\n"
            f"💾 مخزن اسپیس: {repo}\n"
            f"💾 حافظه تلگرامی: {'✅' if st_['msg_found'] else '❌'}"
            f" — منبع: {st_.get('restored_from', '—')}\n"
            f"🏛 گروه: {sum(1 for s in GROUP_CFG.values() if s)}/۳ بخش تنظیم‌شده\n"
            f"📍 منطقه‌ها: {st_['regions']} | 👥 کاربران: {st_['users']}\n"
            f"🛰 کانفیگ‌های کانال‌ها: {len(anten.custom['v2']) if ANTEN_OK else 0}"
        f" | 📡 پروکسی‌ها: {len(anten.custom['mtproto']) if ANTEN_OK else 0}")
    except Exception:
        log.exception("startup report")


async def _userbot_keepalive_loop():
    """اگر اکانت‌خوان بی‌صدا قطع شد → خودکار وصل شود (هر ۲ دقیقه چک)"""
    while True:
        try:
            await asyncio.sleep(120)
            if user_client is not None and not user_client.is_connected():
                await user_client.connect()
                if user_client.is_connected():
                    print("👁 اکانت‌خوان دوباره وصل شد (keepalive)", flush=True)
        except Exception:
            pass


def main():
    # ۱) بازیابی از مخزن اسپیس (اگر توکن باشد)
    persist.pull()
    load_all()
    # 🛡 شروع ضد-FloodWait: به‌جای کرش و ری‌استارتِ هر ۵ ثانیه (که جریمه‌ی تلگرام را
    # بدتر می‌کرد)، بی‌صدا صبر می‌کنیم تا رد شود — ربات زنده می‌ماند و منتظر می‌ماند
    from telethon.errors import FloodWaitError
    _tries = 0
    while True:
        try:
            client.start(bot_token=BOT_TOKEN)
            break
        except FloodWaitError as _fw:
            _wait = min(int(getattr(_fw, "seconds", 60) or 60) + 30, 3600)
            print(f"⏳ تلگرام جریمه‌ی FloodWait داده — {_wait} ثانیه صبر می‌کنم و دوباره…", flush=True)
            time.sleep(_wait)
            _tries += 1
            if _tries > 30:
                raise
        except Exception as _ex:
            # نشستِ ذخیره‌شده خراب شده → پاک کن تا ری‌استارت بعدی تازه شروع کند
            _s = str(_ex)
            if any(k in _s for k in ("AuthKey", "SESSION_REVOKED", "ungenerated",
                                      "auth key", "server salt")):
                try:
                    BOT_SESSION_FILE.unlink(missing_ok=True)
                except Exception:
                    pass
            raise
    # 💾 ذخیره‌ی نشست ربات — ری‌استارت‌های بعدی دیگر درخواستِ مجوز نمی‌خواهند
    try:
        _s2 = StringSession.save(client.session)
        if _s2:
            BOT_SESSION_FILE.write_text(json.dumps({"session": _s2}, ensure_ascii=False))
            persist.mark_dirty()
            print("💾 نشست ربات ذخیره شد — ری‌استارت‌ها دیگر جریمه نمی‌خورند", flush=True)
    except Exception:
        pass
    # ۲) حافظه تلگرامی — اول با ربات، بعد با کمک اکانت‌خوان
    client.loop.run_until_complete(tgstore.init(client, ADMIN_IDS))
    client.loop.run_until_complete(_restore_user_client())
    client.loop.run_until_complete(_restore_sd_sessions())
    client.loop.run_until_complete(_restore_dl_sessions())
    if user_client:
        client.loop.run_until_complete(
            tgstore.init(client, ADMIN_IDS, reader=user_client))
        # واکشی عمیق اولیه‌ی همه‌ی منابع (پس زمینه — راه‌اندازی را نگه ندارد)
        client.loop.call_soon(lambda: asyncio.ensure_future(_backfill_all_sources()))
    # ۳) داده‌ها
    bargha.load_state()
    _restore_yt_cookies()
    _mag_load_ctx()
    _load_aigroup()
    _load_autoreply()
    _load_afk()
    _load_filters()
    _load_welcome()
    _load_notes()
    _load_warns()
    _load_userkeys()
    load_support()
    try:
        anten.on_need_refresh = _anten_refresh_hook
    except Exception:
        pass
    load_group_cfg()
    load_sources()
    load_subs()
    load_sd_settings()
    bargha.on_announcement = announce_to_group
    # اگر هیچ داده‌ای نیست، راهنمای یک‌قدمی به مدیر
    if not bargha.regions and not user_client:
        try:
            client.loop.run_until_complete(client.send_message(
                int(next(iter(ADMIN_IDS))),
                "💡 هنوز داده‌ای بازیابی نشده.\n"
                "پیام «🗂 داده‌های ربات» (در گروه مای‌ربات / چت من) رو فوروارد کن —\n"
                "همه‌چیز یک‌جا برمی‌گرده: منطقه‌ها، کاربران، بخش‌های گروه، اکانت‌خوان."))
        except Exception:
            pass
    client.loop.run_until_complete(_startup_report())
    # ۴) حلقه‌ها
    persist.start()
    client.loop.call_soon(lambda: asyncio.ensure_future(tgstore.loop()))
    client.loop.call_soon(lambda: asyncio.ensure_future(bargha.poll_loop(client)))
    client.loop.call_soon(lambda: asyncio.ensure_future(bargha.freshness_loop(client)))
    client.loop.call_soon(lambda: asyncio.ensure_future(bargha.nightly_loop(client)))
    client.loop.call_soon(lambda: asyncio.ensure_future(anten.refresh_loop()))
    if USERBOT_ENABLED:
        client.loop.call_soon(lambda: asyncio.ensure_future(_userbot_keepalive_loop()))
        client.loop.call_soon(lambda: asyncio.ensure_future(_userbot_announce_loop()))
        client.loop.call_soon(lambda: asyncio.ensure_future(_source_scan_loop()))
    if AGENT_ENABLED:
        client.loop.call_soon(lambda: asyncio.ensure_future(_welcome_loop()))
    if AI_OK:
        client.loop.call_soon(lambda: asyncio.ensure_future(
            asyncio.to_thread(aichat.warm_cache)))
    me = client.loop.run_until_complete(client.get_me())
    global BOT_SELF_ID
    BOT_SELF_ID = me.id
    log.info("Compressor bot @%s started (admins: %s)", me.username, ADMIN_IDS or "—")
    print(f"✅ BOT_UP: @{me.username} (compressor)", flush=True)
    try:
        client.loop.run_until_complete(_notify_interrupted_jobs())
    except Exception as _e:
        print("[wip] notify:", str(_e)[:80], flush=True)
    try:
        _me_obj = client.loop.run_until_complete(client.get_me())
        BOT_UNAME = (_me_obj.username or "") if _me_obj else ""
        print(f"[init] BOT_UNAME = {BOT_UNAME or '(نامشخص)'}", flush=True)
    except Exception:
        pass
    client.run_until_disconnected()


if __name__ == "__main__":
    main()
