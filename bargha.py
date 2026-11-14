#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ماژول «برق کاشان» — رصد اطلاعیه‌های خاموشی برق کاشان

منابع (به ترتیب تلاش):
  ۱) eitaa.com/epedckashani (مستقیم، پروکسی allorigins، پروکسی jina)
  ۲) t.me/s/epedckashani (اگر پیش‌نمایش باز باشد)
  ۳) t.me/s/ahlekashanam — کانال اهل کاشانم (هر نوع خبر؛ با فیلتر، فقط قطعی برق جدا می‌شود)
  ۴) فوروارد دستی اطلاعیه توسط کاربر/مدیر

جستجوی منطقه:
  - نرمال‌سازی سنگین: نیم‌فاصله، حروف عربی (ي ك ئ أ إ آ ة)، اعراب، ارقام فارسی/عربی/انگلیسی
  - سه پله: زیررشته‌ی دقیق → همه‌ی کلمات (هر ترتیبی) → فازی (غلط املایی نزدیک)
  - از روز اول، اطلاعیه‌های واقعی اخیر به‌عنوان بذر داخل ربات هست
"""

import asyncio
import difflib
import hashlib
import html as html_mod
import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta

import persist
from pathlib import Path

log = logging.getLogger("bargha")

CHANNEL = "epedckashani"
CHANNEL_ALT = "ahlekashanam"
CHANNELS_TG = ["ahlekashanam", "kashsn", "kashania"]
POLL_MINUTES = 30
STATE_FILE = Path("/tmp/bargha_state.json")
REGIONS_FILE = Path("/tmp/bargha_regions.json")

# ---------------------------------------------------------------- نرمال‌سازی

_DIGITS = "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩"
_CHAR_MAP = {
    "ي": "ی", "ك": "ک", "ؤ": "و", "إ": "ا", "أ": "ا", "آ": "ا", "ٱ": "ا",
    "ئ": "ی", "ة": "ه", "ۀ": "ه", "ٔ": "", "\u200c": " ", "‌": " ",
    "“": '"', "”": '"', "«": " ", "»": " ", "(": " ", ")": " ",
}
_CHAR_MAP["٬"] = "،"      # جداکننده‌ی عربی
_CHAR_MAP["؛"] = "؛"
_TRANS = str.maketrans({**{d: str(i) for i, d in enumerate(_DIGITS)}, **_CHAR_MAP})


def norm(t: str) -> str:
    """نرمال‌سازی سنگین برای جستجو: رقم انگلیسی + حروف فارسی یکدست + بدون نیم‌فاصله"""
    t = (t or "").translate(_TRANS)
    t = "".join(ch for ch in t if ch not in "ًٌٍَُِّْـٰ")
    return re.sub(r"\s+", " ", t).strip()


def strip_html(raw: str) -> str:
    txt = html_mod.unescape(raw or "")
    txt = re.sub(r"<br\s*/?>", "\n", txt, flags=re.I)
    txt = re.sub(r"</p>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", "", txt)
    return txt.strip()


# ---------------------------------------------------------------- تاریخ شمسی

MONTHS = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4, "مرداد": 5, "شهریور": 6,
    "مهر": 7, "آبان": 8, "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
}


def jalali_to_gregorian(jy, jm, jd):
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 400 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 100 * (days // 36524)
    days %= 36524
    if days > 365:
        gy += 4 * (days // 1461)
        days %= 1461
        if days > 365:
            gy += (days - 1) // 365
            days = (days - 1) % 365
    gd = days + 1
    months = [0, 31, 29 if (gy % 4 == 0 and (gy % 100 != 0 or gy % 400 == 0)) else 28,
              31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    for i in range(1, 13):
        if gd <= months[i]:
            gm = i
            break
        gd -= months[i]
    return gy, gm, gd


_WEEKDAY_FA = {5: "شنبه", 6: "یکشنبه", 0: "دوشنبه", 1: "سه‌شنبه",
               2: "چهارشنبه", 3: "پنج‌شنبه", 4: "جمعه"}
_MONTH_FA = {1: "فروردین", 2: "اردیبهشت", 3: "خرداد", 4: "تیر", 5: "مرداد",
             6: "شهریور", 7: "مهر", 8: "آبان", 9: "آذر", 10: "دی", 11: "بهمن", 12: "اسفند"}


def fmt_jdate(dt) -> str:
    """نمایش کامل: شنبه ۳۱ مرداد"""
    if not dt:
        return ""
    wd = _WEEKDAY_FA.get(dt.weekday(), "")
    jm, jd = jalali_month_day(dt)
    return f"{wd} {jd} {_MONTH_FA.get(jm, '')}"


_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_num(n) -> str:
    return str(n).translate(_FA)


def slot_when(s) -> str:
    """متن زمان قطعی با روز هفته و تاریخ"""
    parts = []
    if s.get("date"):
        parts.append(f"📅 {fmt_jdate(s['date'])}")
    parts.append(f"🕐 ساعت {fa_num(s['start_h'])}:۰۰ تا {fa_num(s['end_h'])}:۰۰")
    return "\n".join(parts)


def jalali_month_day(gdt: datetime):
    gy, gm, gd = gdt.year, gdt.month, gdt.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + \
        ((gy2 + 399) // 400) + gd + g_d_m[gm - 1]
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jm, jd


# ---------------------------------------------------------------- دریافت پست‌ها

def _http_get(url: str, timeout=25) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def fetch_posts() -> list:
    sources = [
        ("e|", f"https://eitaa.com/{CHANNEL}"),
        ("e|", f"https://api.allorigins.win/raw?url=https://eitaa.com/{CHANNEL}"),
        ("e|", f"https://r.jina.ai/https://eitaa.com/{CHANNEL}"),
        ("e|", f"https://t.me/s/{CHANNEL}"),
        ("a|", f"https://t.me/s/{CHANNEL_ALT}"),
        ("a|", f"https://r.jina.ai/https://t.me/s/{CHANNEL_ALT}"),
        ("k|", "https://t.me/s/kashsn"),
        ("n|", "https://t.me/s/kashania"),
    ]
    collected = []
    for prefix, url in sources:
        try:
            raw = _http_get(url, timeout=30 if "jina" in url else 20)
            posts = _extract_tme(raw, prefix) if "t.me" in url else extract_posts(raw, prefix)
            if posts:
                log.info("bargha: %d پست از %s", len(posts), url.split("/")[2])
                collected.extend(posts)
        except Exception as e:
            log.warning("bargha: منبع ناموفق %s → %s", url, str(e)[:80])
    return collected


def _extract_tme(raw: str, prefix: str) -> list:
    out = []
    for m in re.finditer(
            r'data-post="\w+/(\d+)"[^>]*>(.*?)'
            r'(?=<div class="tgme_widget_message_wrap|<div class="tgme_widget_footer)',
            raw, re.S):
        pid, body = m.group(1), m.group(2)
        t = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', body, re.S)
        if t:
            txt = strip_html(t.group(1))
            if len(txt) > 40:
                out.append({"id": prefix + pid, "text": txt[:6000]})
    return out


def extract_posts(raw: str, prefix: str = "e|") -> list:
    if "<" in raw:
        parts = re.split(rf'(?:https?://(?:www\.)?(?:eitaa\.com|t\.me)/)\w+/(\d+)', raw)
        posts, seen = [], set()
        for i in range(1, len(parts) - 1, 2):
            pid, body = parts[i], parts[i + 1]
            if pid in seen:
                continue
            seen.add(pid)
            text = strip_html(body)
            if len(text) > 40:
                posts.append({"id": prefix + pid, "text": text[:6000]})
        if posts:
            return posts
        text = strip_html(raw)
    else:
        text = raw
    chunks = re.split(r"\n\s*(?=\d{2}:\d{2}|\[\d)", text)
    return [{"id": prefix + hashlib.md5(c[:300].encode()).hexdigest()[:10], "text": c[:6000]}
            for c in chunks if len(c) > 120]


# ---------------------------------------------------------------- تجزیه‌ی اطلاعیه

SLOT_RE = re.compile(r"^(\d{1,2})(?::\d{2})?\s*(?:[-–—]|تا)\s*(\d{1,2})(?::\d{2})?")


def is_announcement(text: str) -> bool:
    t = norm(text)
    return ("خاموشی" in t or "قطع" in t) and \
        re.search(r"\d{1,2}\s*(?:[-–—]|تا)\s*\d{1,2}", t[:600])


# خط امضا/تبلیغ کانال: هندل (@ahlekashanam، kashsn)، لینک (eitaa.com/...) —
# این خطوط منطقه نیستند و نباید در متن اطلاعیه بیایند
_SIG_LINE_RE = re.compile(
    r"^(?:@?[A-Za-z][A-Za-z0-9_]{2,31}"                      # هندل لاتین
    r"|(?:https?://)?(?:www\.)?(?:t\.me|eitaa\.com|telegram\.me|youtube\.com|instagram\.com)/?\S*"
    r"|https?://\S+)$", re.IGNORECASE)


def _is_signature(line: str) -> bool:
    t = (line or "").strip()
    if not t or len(t) > 100:
        return False
    # فقط خطِ «کاملاً لاتین/لینک» — خطوط فارسی منطقه هرگز امضا نیستند
    if re.search(r"[\u0600-\u06FF]", t):
        return False
    return bool(_SIG_LINE_RE.match(t))


def parse_announcement(text: str) -> list:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = []
    cur_date_label = ""
    cur_date = None
    cur_slot = None

    def flush_slot():
        if cur_slot and cur_slot.get("areas"):
            out.append({"date_label": cur_date_label, "date": cur_date,
                        "start_h": cur_slot["s"], "end_h": cur_slot["e"],
                        "areas": cur_slot["areas"]})

    for l in lines:
        line = re.sub(r"^\W+", "", l)   # حذف ایموجی/بولت اول خط
        m = SLOT_RE.match(norm(line))
        if m and m.group(1) != m.group(2):
            flush_slot()
            s, e = int(m.group(1)), int(m.group(2))
            if 0 <= s <= 24 and 0 <= e <= 24 and (e > s or e == 0):
                cur_slot = {"s": s, "e": e if e != 0 else 24, "areas": ""}
                rest = SLOT_RE.sub("", norm(line), count=1)
                if rest and len(rest) > 8:
                    cur_slot["areas"] += " " + rest
            continue
        d = detect_date(line)
        if d:
            flush_slot()
            cur_slot = None
            cur_date_label, cur_date = d
            continue
        if cur_slot is not None:
            if _is_signature(line):
                continue          # امضای کانال (@ahlekashanam، kashsn، لینک ایتا) — منطقه نیست
            cur_slot["areas"] += "\n" + norm(line)   # خط جدا = منطقه جدا
    flush_slot()
    return out


_WEEKDAY_WORDS = ("شنبه", "یکشنبه", "دوشنبه", "سه شنبه", "سه‌شنبه",
                  "چهارشنبه", "چهار شنبه", "پنجشنبه", "پنج شنبه", "جمعه")


def detect_date(line: str):
    """فقط سربرگ واقعی: خط کوتاه، بدون ویرگول، با روز هفته یا «برنامه/احتمالی».
    اسم خیابان‌هایی مثل «۲۲ بهمن» یا «۲۹ اسفند» دیگر تاریخ حساب نمی‌شوند."""
    t = norm(line)
    if len(t) > 75 or "،" in t or "," in t:
        return None
    if "امروز" in t and len(t) <= 45:
        return "امروز", iran_now().replace(hour=0, minute=0, second=0, microsecond=0)
    if "فردا" in t and len(t) <= 45 and not any(w in t for w in _WEEKDAY_WORDS):
        return "فردا", (iran_now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if any(w in t for w in _WEEKDAY_WORDS) or "برنامه" in t or "احتمالی" in t:
        md = re.search(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")", t)
        if md:
            day, month = int(md.group(1)), MONTHS[md.group(2)]
            for delta in range(-1, 11):   # فقط دیروز تا ۱۰ روز آینده
                dt = iran_now() + timedelta(days=delta)
                if jalali_month_day(dt) == (month, day):
                    return md.group(0), dt.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            # 🔧 روزِ هفته بدون عدد («ادامه برنامه خاموشی در روز پنجشنبه»)
            # → نزدیک‌ترین روزِ همین هفته (امروز تا ۷ روز بعد)
            # بلندترین تطبیق — «شنبه» زیررشته‌ی «پنجشنبه» است!
            wd_fa = max((w for w in _WEEKDAY_WORDS if w in t),
                        key=len, default=None)
            if wd_fa:
                # همه‌ی شکل‌ها (چسبیده/فاصله/نیم‌فاصله) → چسبیده
                key = wd_fa.replace("\u200c", "").replace(" ", "")
                wd_en = {"شنبه": "Saturday", "یکشنبه": "Sunday",
                         "دوشنبه": "Monday", "سهشنبه": "Tuesday",
                         "چهارشنبه": "Wednesday", "پنجشنبه": "Thursday",
                         "جمعه": "Friday"}.get(key)
                if wd_en:
                    for delta in range(0, 8):
                        dt = iran_now() + timedelta(days=delta)
                        if dt.strftime("%A") == wd_en:
                            return wd_fa, dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


# ---------------------------------------------------------------- بذر: اطلاعیه‌های واقعی

SEED_ANNOUNCEMENTS = [
    'برنامه خاموشی های احتمالی روز دوشنبه ۲ شهریور ماه ....\n⏰️۱۰ تا ۱۲\nخیابان کلیم کاشانی، دست چپ بلوار راوند از مدخل تا پل بهشتی ، بلوار دانشجو\nابتدای خیابان بعثت بلوار الغدیر خیابان تابنده میدان آب خیابان وطنی ، احمدی روشن\nروستای مرق ، سادیان\n⏰️۱۲ تا ۱۴\nبلوار مطهری ، میدان معلم ، بلوار باهنر ،بلوار مادر ، پشت زندان ، خیابان یاسر\nکمربندی از میدان زجاجی به سمت میدان بسیج سمت چپ ، بلوار میوه تره بار ، خیابان طالقانی دور میدان ۱۵ خرداد ابتدای ۲۲ بهمن ، خیابان شهدای محرم\nجاده قمصر، میدان زارع مجرد، بلوار ساحلی تا قبل از میدان ۹ دی ، کوچه میلاد\nجاده قمصر به سمت قمصر و قسمتی از شهر قمصر\nکوچه باسکول جاده قمصر، شهرک ۲۲ بهمن ، انتهای ۱۴ معصوم لتحر و اراضی دشت لتحر\nدست چپ خیابان امیرکبیر از چهارراه سلمان تا هتل نگارستان، خیابان شاداب ، حسن آباد و منطقه یزد آباد، انتهای خیابان شهیدان بخشی سمت حسن آباد\nخیابان های سلمان ، بخارایی ، بلوار سپیده کاشانی، خیابان اعتمادی ، خیابان همت ، لتحر ، بلوار فضیلت، خیابان شمس آبادی، بلوار مناقب، مشفق کاشانی، ملا فتح الله، محله سرفره\n⏰️۱۴ تا ۱۶\nبلوارهای گلستان ، نارنجستان، شهید رییسی ، میدان پلیس ، خیابان طبیعت ، بهارستان شمالی\nدست راست شهرک امیرالمومنین از بلوار امام رضا تا خیابان امیر کبیر خ مجدالدین قسمتی از بلوار الهیه\nبلوار ابوالرضا راوندی به طرف پلیس راه، طاهر آباد ، روستای یزدل ، انتهای بلوار حرم از سمت طاهرآباد\nخیابان آیت الله کاشانی، سه راه غزنوی، خیابان رجایی ، بیمارستان نقوی ، خیابان میرعماد ، دروازه دولت تا خرداد ، قسمتی از بازار\nجمال آباد قسمتی از ملک آباد تا زیارت طاهر منصور\n⏰️۱۶ تا ۱۸\nخیابان های نطنز ، اباذر ، غیاث الدین جمشید،از سر خیابان نطنز تا بریدگی بلوار چمران  و ۲۰ متری امام رضا\nدست راست بلوار امام رضا از میدان کتاب تا میدان جهاد ، خیابان ایمان ، خیابان بهشتی از معلم تا چهار راه شاهد، بلوار ستوده از میدان امام حسن\nاز راوند به طرف کاشان ، سمت راست بلوار قطب شهرک مطهری راوند ، میدان راوند ، بلوار فرازنده ، بلوار سهراب سپهری ، میدان سهراب سپهری، محله سادات ، کوچه شهید طحانی ، شهرک قطب ، شهرک آزادگان\nشهرک خاتم الانبیا(مسکن مهر ) \nقسمتی از مصلی فین\nاتوبان اصفهان ، روستاهای خنب و دره ، پشت باغ فین\nابتدای خیابان امیرکبیر از میدان امام حسین تا چهارراه سلمان \nخیابان مدرس ، بلوار مفتح ، خیابان کتابچی ،خیابان غزنوی ، خیابانهای سپاه ، بهشتی از چهارراه آیت الله کاشانی تا چهارراه شاهد\nخیابان نواب ، بلوار خاندایی، کوچه درمانگاه نواب، خیابان صالحی، خیابان پیام نور، میدان گاز\n⏰️۱۸ تا ۲۰\nخیابان\u200cهای امام ، راه آهن ،کمربندی از میدان بسیج سمت چپ تا پمپ بنزین ۲۹ اسفند\nاز میدان کتاب دست چپ تا میدان جهاد ، منطقه زیدی، دست چپ بلوار دانش از میدان جهاد تا بلوار زیتون\nخیابان پیروزی، خیابان بعثت ، میدان سیلک و کوچه های اطراف ، شهید عالمیان\nجاده قدیم قم ، مشکات شهرک مشکات\nاتوبان کاشان قم بعد از مشکات ، روستای سن سن ، آبشیرین، منطقه شوراب\nدانشگاه کاشان کوچه سنگ خارا\nروستاهای حسنارود، نشلج، علوی ، مشهد اردهال و باریکرسف\n\nhttps://eitaa.com/epedckashani',
    'خاموشی های احتمالی روز سه شنبه ۳ شهریور ماه...\n⏰️۱۰ تا۱۲\nدست راست بلوار امام رضا از میدان کتاب تا اتوبان کوچه مسجد صاحب الزمان، خط گاز ناجی آباد\n⏰️۱۲ تا ۱۴\nخیابان کلیم کاشانی، دست چپ بلوار راوند از مدخل تا پل بهشتی ، بلوار دانشجو\nخیابانهای زیارتی ، شاهد ، شریعتی ، کوچه شهید سالکی،  خیابان ۲۲ بهمن و رحمانی ، دست راست رجایی از سمت خرداد تا چهارراه\nخیابانهای میثاق ، گازرگاه ،کوچه های سجادیه و بلال ، بلوار ساحلی ، حسین پور ، خیابان رسالت\nاز میدان کتاب دست چپ تا میدان جهاد ، منطقه زیدی، دست چپ بلوار دانش از میدان جهاد تا بلوار زیتون\nدست راست شهرک امیرالمومنین از بلوار امام رضا تا خیابان امیر کبیر خ مجدالدین قسمتی از بلوار الهیه\nخیابان پیروزی، خیابان بعثت ، میدان سیلک و کوچه های اطراف ، شهید عالمیان\nقسمتی از شهر قمصر\nانتهای خیابان ملا محسن محدوده  کوچه چکبر و کوچه های اطراف ، کوچه مسجد آقا بزرگ، درب اصفهان، چهارراه امام جواد، قسمتی از بلوار چمران\n⏰️۱۴ تا ۱۶\nکمربندی از میدان زجاجی به سمت میدان بسیج سمت چپ ، بلوار میوه تره بار ، خیابان طالقانی دور میدان ۱۵ خرداد ابتدای ۲۲ بهمن ، خیابان شهدای محرم\nخیابان\u200cهای امام ، راه آهن ،کمربندی از میدان بسیج سمت چپ تا پمپ بنزین ۲۹ اسفند\nدست راست بلوار امام رضا از میدان کتاب تا میدان جهاد ، خیابان ایمان ، خیابان بهشتی از معلم تا چهار راه شاهد، بلوار ستوده از میدان امام حسن\nاز راوند به طرف کاشان ، سمت راست بلوار قطب شهرک مطهری راوند ، میدان راوند ، بلوار فرازنده ، بلوار سهراب سپهری ، میدان سهراب سپهری، محله سادات ، کوچه شهید طحانی ، شهرک قطب ، شهرک آزادگان\nکوچه باسکول جاده قمصر، شهرک ۲۲ بهمن ، انتهای ۱۴ معصوم لتحر و اراضی دشت لتحر\nخیابان نواب ، بلوار خاندایی، کوچه درمانگاه نواب، خیابان صالحی، خیابان پیام نور، میدان گاز\n⏰️۱۶ تا ۱۸\nشهرک انقلاب، کوچه خندان ، دست راست بلوار راوند از مدخل تا پل بهشتی ، بلوار بدیعی\nبلوار مطهری ، میدان معلم ، بلوار باهنر ،بلوار مادر ، پشت زندان ، خیابان یاسر\nخیابان های پاسگاه ، بهارستان ، مخابرات ، بهاران فاز ۲\nدانشگاه کاشان کوچه سنگ خارا\nبلوار ابوالرضا راوندی به طرف پلیس راه، طاهر آباد ، روستای یزدل ، انتهای بلوار حرم از سمت طاهرآباد\nجاده قمصر، میدان زارع مجرد، بلوار ساحلی تا قبل از میدان ۹ دی ، کوچه میلاد\nخیابان آیت الله کاشانی، سه راه غزنوی، خیابان رجایی ، بیمارستان نقوی ، خیابان میرعماد ، دروازه دولت تا خرداد ، قسمتی از بازار\nجمال آباد قسمتی از ملک آباد تا زیارت طاهر منصور\nدست چپ خیابان امیرکبیر از چهارراه سلمان تا هتل نگارستان، خیابان شاداب ، حسن آباد و منطقه یزد آباد، انتهای خیابان شهیدان بخشی سمت حسن آباد\n⏰️۱۸ تا ۲۰\nبلوار خادمی ، کوچه شورای شهر ، بلوار کارگر ، بلوار خلیج فارس ، خیابان سهیل\nدست چپ شهرک امیرالمومنین از بلوار امام رضا بلوار عباس زاده و اطراف کاشان مال ، بلوار ناجا\nابتدای خیابان بعثت بلوار الغدیر خیابان تابنده میدان آب خیابان وطنی ، احمدی روشن\nروستای خنچه ، بارونق ، استرک، جوشقان\nابتدای ملا محسن از میدان امام حسین، قسمتی از فاضل نراقی، میدان کمال الملک، خیابان بابا افضل، چهار راه آیت الله کاشانی، خیابان علوی\nخیابان های سلمان ، بخارایی ، بلوار سپیده کاشانی، خیابان اعتمادی ، خیابان همت ، لتحر ، بلوار فضیلت، خیابان شمس آبادی، بلوار مناقب، مشفق کاشانی، ملا فتح الله، محله سرفره\n⏰️۲۰ تا۲۲\nبلوار معمار ، میدان جهاد(شهید خامنه ای) سمت راست بلوار دانش تا انتهای  بلوار صنعت شامل همه کوچه ها و خیابانها از جهاد\nخیابان های نطنز ، اباذر ، غیاث الدین جمشید،از سر خیابان نطنز تا بریدگی بلوار چمران  و ۲۰ متری امام رضا\nبلوارهای گلستان ، نارنجستان، شهید رییسی ، میدان پلیس ، خیابان طبیعت ، بهارستان شمالی\nروستای مرق ، سادیان\nجاده قمصر به سمت قمصر و قسمتی از شهر قمصر\nاتوبان اصفهان ، روستاهای خنب و دره ، پشت باغ فین\nخیابان سلمان مجتمع فرهنگیان \nخیابان خراسانی ، باغچه قاضی ، ابتدای خیابان بخارایی ، ابتدای خیابان شهیدان بخشی از سمت سلمان\nمیدان قاضی اسدالله، قسمتی از ملا محسن محدوده میدان قاضی اسدالله و قسمتی از فاضل نراقی، خیابان محتشم، میدان امام ، ابتدای خیابان امام\n⏰️۲۲ تا ۲۴\nاز راوند به طرف کاشان سمت چپ بلوار ابوالرضا ، کوچه شهید کحال ، خیابان حرم ، سمت چپ بلوار قطب راوندی، کوچه نگارستان ، مسیر بلوار قطب تا پل بهشتی\nشهرک خاتم الانبیا(مسکن مهر ) \nقسمتی از مصلی فین\nابتدای خیابان امیرکبیر از میدان امام حسین تا چهارراه سلمان \nخیابان مدرس ، بلوار مفتح ، خیابان کتابچی ،خیابان غزنوی ، خیابانهای سپاه ، بهشتی از چهارراه آیت الله کاشانی تا چهارراه شاهد\nhttps://eitaa.com/epedckashani\n⏰️۲۰ تا ۲۲\nشهرک انقلاب، کوچه خندان ، دست راست بلوار راوند از مدخل تا پل بهشتی ، بلوار بدیعی\nبلوار خادمی ، کوچه شورای شهر ، بلوار کارگر ، بلوار خلیج فارس ، خیابان سهیل\nخیابانهای میثاق ، گازرگاه ،کوچه های سجادیه و بلال ، بلوار ساحلی ، حسین پور ، خیابان رسالت\nدست چپ شهرک امیرالمومنین از بلوار امام رضا بلوار عباس زاده و اطراف کاشان مال ، بلوار ناجا\nاز راوند به طرف کاشان سمت چپ بلوار ابوالرضا ، کوچه شهید کحال ، خیابان حرم ، سمت چپ بلوار قطب راوندی، کوچه نگارستان ، مسیر بلوار قطب تا پل بهشتی\nروستای خنچه ، بارونق ، استرک، جوشقان\nقسمتی از شهر قمصر\nابتدای ملا محسن از میدان امام حسین، قسمتی از فاضل نراقی، میدان کمال الملک، خیابان بابا افضل، چهار راه آیت الله کاشانی، خیابان علوی\nانتهای خیابان ملا محسن محدوده  کوچه چکبر و کوچه های اطراف ، کوچه مسجد آقا بزرگ، درب اصفهان، چهارراه امام جواد، قسمتی از بلوار چمران\n⏰️۲۲ تا ۲۴\nخیابانهای زیارتی ، شاهد ، شریعتی ، کوچه شهید سالکی،  خیابان ۲۲ بهمن و رحمانی ، دست راست رجایی از سمت خرداد تا چهارراه\nجاده برزک روستای نابر ، شهر برزک و روستاهای بخش برزک\nمسکن ملی کاشان ، قسمتی از فین بزرگ به سمت حسن آباد ، محله حیدری فین و کوچه های اطراف ، یزدآباد\nدست راست خیابان امیرکبیر از چهارراه سلمان تا هتل امیرکبیر \nخیابان صادق اصفهانی \nخیابان ملک الشعرای بهار \nبلوار الهیه از میدان عامریه تا میدان الهیه\nخیابان سلمان مجتمع فرهنگیان \nخیابان خراسانی ، باغچه قاضی ، ابتدای خیابان بخارایی ، ابتدای خیابان شهیدان بخشی از سمت سلمان\nhttps://eitaa.com/epedckashani',
]

# ---------------------------------------------------------------- ذخیره‌سازی

state = {"last_ids": [], "corpus": [], "processed_hashes": []}
regions = {}
_scheduled = set()


def load_state():
    global state, regions
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    state.setdefault("last_ids", [])
    state.setdefault("corpus", [])
    state.setdefault("processed_hashes", [])
    # هرس: اطلاعیه‌های مربوط به قبل از ۲ روز پیش یا بعد از ۱۰ روز آینده حذف می‌شوند
    # (مثل برنامه‌های ماه‌ها بعد که به درد الان نمی‌خورند)
    now = iran_now()
    t_lo = (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    t_hi = (now + timedelta(days=10)).replace(hour=0, minute=0, second=0, microsecond=0)
    kept = []
    for c in state["corpus"]:
        dates = [s["date"] for s in parse_announcement(c) if s.get("date")]
        if not dates or any(t_lo <= d <= t_hi for d in dates):
            kept.append(c)
    if len(kept) != len(state["corpus"]):
        log.info("bargha: %d اطلاعیه‌ی قدیمی/خیلی دور هرس شد",
                 len(state["corpus"]) - len(kept))
        state["corpus"] = kept

    # بذرهای تازه همیشه مطمئن باشند در پیکره هستند (حتی بعد از آپدیت ربات)
    changed = False
    for s in SEED_ANNOUNCEMENTS:
        if s not in state["corpus"]:
            state["corpus"].insert(0, s)
            changed = True
    if changed:
        state["corpus"] = state["corpus"][:40]
    # ضدتکرار: هر اطلاعیه فقط یک‌بار در پیکره
    seen, uniq = set(), []
    for c in state["corpus"]:
        h = norm(c)[:300]
        if h not in seen:
            seen.add(h)
            uniq.append(c)
    if len(uniq) != len(state["corpus"]):
        log.info("bargha: %d اطلاعیه‌ی تکراری حذف شد", len(state["corpus"]) - len(uniq))
        state["corpus"] = uniq
        changed = True
    if changed:
        save_state()
    try:
        regions = json.loads(REGIONS_FILE.read_text())
    except Exception:
        regions = {}


def save_state():
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()
    try:
        import tgstore
        tgstore.mark_dirty()
    except Exception:
        pass


def save_regions():
    try:
        REGIONS_FILE.write_text(json.dumps(regions, ensure_ascii=False))
    except OSError:
        pass
    persist.mark_dirty()
    try:
        import tgstore
        tgstore.mark_dirty()
    except Exception:
        pass


def add_region(chat_id, name, phrase, remind) -> bool:
    key = str(chat_id)
    lst = regions.setdefault(key, [])
    if len(lst) >= 5:
        return False
    lst.append({"name": name[:24], "phrase": phrase, "remind": int(remind), "mute": False})
    save_regions()
    return True


# ---------------------------------------------------------------- جستجوی منطقه (سه‌پله‌ای)

_SUB_SPLIT = re.compile(r"\s+(?:و|از|تا)\s+")   # برای شکستن تکه‌های خیلی چسبیده


def _split_segment(seg: str) -> list:
    """تکه‌ی جغرافیایی: اگر خیلی بلند و چسبیده است، در مرز طبیعی «و/از/تا» می‌شکند"""
    seg = re.sub(r"^\W+", "", seg.strip())
    n = norm(seg)
    if len(n) <= 90:
        return [seg]
    parts = [x.strip() for x in _SUB_SPLIT.split(seg) if x.strip()]
    out = []
    for x in parts:
        if len(norm(x)) >= 12:
            out.append(x)
        elif out:
            out[-1] += " و " + x      # دم کوتاه به تکه‌ی قبلی می‌چسبد
    return out if len(out) >= 2 else [seg]


def _clean_seg(seg: str) -> str:
    """پاکسازی یک تکه‌ی منطقه: حذف «و/از» ابتدایی، نقطه‌ها"""
    seg = seg.strip(" .•–-")
    for w in ("و ", "از ", "تا "):
        if seg.startswith(w):
            seg = seg[len(w):]
    return seg.strip()


def _all_segments() -> list:
    """همه‌ی تکه‌های جغرافیایی همه‌ی اطلاعیه‌ها — هر خیابان یک تکه، تمیز و بی‌تکرار"""
    segs, seen = [], set()
    for post in state.get("corpus", []):
        for slot in parse_announcement(post if isinstance(post, str) else post.get("text", "")):
            for raw in re.split(r"[،,؛;\n]+", slot["areas"]):
                for seg in _split_segment(raw):
                    seg = _clean_seg(seg)
                    nseg = norm(seg)
                    # نه خیلی کوتاه (نویسه‌های بی‌معنا) نه خیلی بلند (چند منطقه چسبیده)
                    if 6 <= len(nseg) <= 80 and nseg not in seen:
                        seen.add(nseg)
                        segs.append(seg)
    return segs


def search_phrases(query: str, limit=8) -> list:
    """سه‌پله‌ای: دقیق → همه‌ی کلمات → فازی (غلط املایی)"""
    q = norm(query).strip(" .:؛")
    if len(q) < 3:
        return []
    segs = _all_segments()
    if not segs:
        return []

    # پله ۱: زیررشته‌ی دقیق
    exact = [s for s in segs if q in norm(s)]

    # پله ۲: همه‌ی کلمات (هر ترتیبی) — کلمات کوچک (از/تا/و) نادیده
    qwords = [w for w in q.split() if len(w) >= 3]
    allwords = []
    if qwords:
        allwords = [s for s in segs if all(w in norm(s).split() or w in norm(s) for w in qwords)]
        allwords = [s for s in allwords if s not in exact]

    # پله ۳: فازی — بیشترین شباهت کلمات
    fuzzy = []
    if qwords:
        for s in segs:
            if s in exact or s in allwords:
                continue
            swordset = norm(s).split()
            best = 0.0
            for qw in qwords:
                for sw in swordset:
                    if len(sw) >= 3 and len(qw) >= 3:
                        r = difflib.SequenceMatcher(None, qw, sw).ratio()
                        best = max(best, r)
            if best >= 0.75:
                fuzzy.append((best, s))
        fuzzy.sort(reverse=True)
        fuzzy = [s for _, s in fuzzy]

    result, seen = [], set()
    for s in exact + allwords + fuzzy:
        n = norm(s)
        if n not in seen:
            seen.add(n)
            result.append(s)
        if len(result) >= limit:
            break
    return result


def match_slots(text: str, phrase: str) -> list:
    p = norm(phrase)
    return [s for s in parse_announcement(text) if p and p in norm(s["areas"])]


# ---------------------------------------------------------------- اطلاع‌رسانی

on_announcement = None   # bot آن را تنظیم می‌کند: (text, source) → گروه

def short_label(phrase: str, maxlen: int = 42) -> str:
    """برچسب کوتاه و خوانا برای نمایش منطقه (عبارت کامل فقط برای تطبیق)"""
    t = (phrase or "").strip()
    if len(t) <= maxlen:
        return t
    cut = t[:maxlen]
    for sep in (" از ", " تا ", " و ", "،", " "):
        i = cut.rfind(sep)
        if i > 15:
            return cut[:i].rstrip(" ،")
    return cut.rstrip(" ،") + "…"


def _slot_notified_key(chat_id, reg, s):
    return f"{chat_id}|{s['date'].strftime('%Y%m%d') if s.get('date') else '?'}|{s['start_h']}|{reg['phrase'][:40]}"


def _ADMIN_SET():
    import os as _os
    return {int(x) for x in _os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",")
            if x.strip().isdigit()}


async def process_announcement(client, text: str, source="کانال"):
    slots = parse_announcement(text)
    if not slots:
        return 0
    # فقط اسلات‌های دیروز تا ۳ روز آینده — تاریخ‌های دور/قدیمی رد می‌شوند
    _t0 = iran_now().replace(hour=0, minute=0, second=0, microsecond=0)
    slots = [s for s in slots if not s.get("date")
             or _t0 - timedelta(days=1) <= s["date"] <= _t0 + timedelta(days=3)]
    if not slots:
        return 0
    h = hashlib.md5(norm(text)[:500].encode()).hexdigest()
    processed = state.setdefault("processed_hashes", [])
    already = h in processed

    state["corpus"] = ([text] + state.get("corpus", []))[:40]
    processed.append(h)
    state["processed_hashes"] = processed[-200:]
    save_state()
    if already:
        return 0

    sent = 0
    for chat_id, lst in regions.items():
        for reg in lst:
            if reg.get("mute"):
                continue
            hits, seen_sk = [], set()
            _today0 = iran_now().replace(hour=0, minute=0, second=0, microsecond=0)
            _now_h = iran_now().hour
            for s in slots:
                if not s.get("date"):
                    continue   # اسلات بدون تاریخ (بازنشر ناقص) اعلام نمی‌شود
                # 🔧 فیکس: اسلاتِ گذشته اطلاع‌رسانی نمی‌شود
                # (روز قبل، یا امروز ولی ساعتش تمام شده)
                if s["date"] < _today0:
                    continue
                if s["date"] == _today0 and s["end_h"] <= _now_h:
                    continue
                if norm(reg["phrase"]) not in norm(s["areas"]):
                    continue
                k = (s["date"].strftime("%Y%m%d"), s["start_h"], s["end_h"])
                if k in seen_sk:
                    continue
                seen_sk.add(k)
                hits.append(s)
            for s in sorted(hits, key=lambda x: (x["date"], x["start_h"]))[:4]:
                nk = _slot_notified_key(chat_id, reg, s)
                if nk in state.get("notified_slots", []):
                    continue   # همین اسلات قبلاً از کانال دیگری اعلام شده
                state.setdefault("notified_slots", []).append(nk)
                state["notified_slots"] = state["notified_slots"][-500:]
                save_state()
                src_note = f"\n📶 منبع: {source}" if int(chat_id) in _ADMIN_SET() else ""
                msg = (f"⚡ قطعی برق «{reg['name']}»\n{slot_when(s)}\n\n"
                       f"📍 {short_label(reg['phrase'])}{src_note}\n\n"
                       f"این برنامه احتمالیه و ممکنه جابه‌جا بشه یا اصلاً قطعی نشه.")
                try:
                    await client.send_message(int(chat_id), msg)
                    sent += 1
                except Exception as e:
                    log.warning("notify %s failed: %s", chat_id, str(e)[:60])
                if reg.get("remind", 0) > 0 and s.get("date"):
                    schedule_reminder(client, int(chat_id), reg, s)
    if sent:
        log.info("bargha: برای %d منطقه اطلاعیه ارسال شد", sent)
    try:
        if on_announcement:
            await on_announcement(text, source)
    except Exception:
        pass
    return sent


def schedule_region_reminders(client, chat_id, reg):
    """برای همه‌ی اسلات‌های آینده‌ی منطبق با این منطقه، یادآوری می‌چیند
    (هم موقع ثبت منطقه جدید، هم بعد از ری‌استارت ربات)"""
    count = 0
    for c in state.get("corpus", []):
        for s in match_slots(c, reg["phrase"]):
            if s.get("date") and reg.get("remind", 0) > 0 and not reg.get("mute"):
                before = len(_scheduled)
                schedule_reminder(client, chat_id, reg, s)
                if len(_scheduled) > before:
                    count += 1
    return count


def reschedule_all(client):
    """بعد از هر بار راه‌اندازی، یادآوری همه‌ی منطقه‌ها دوباره چیده می‌شود"""
    n = 0
    for chat_id, lst in regions.items():
        for reg in lst:
            n += schedule_region_reminders(client, int(chat_id), reg)
    if n:
        log.info("bargha: %d یادآوری دوباره چیده شد", n)
    return n


def schedule_reminder(client, chat_id, reg, slot):
    """زمان‌بندی یادآوری — همه‌ی ساعت‌ها به وقت ایران محاسبه می‌شوند"""
    key = f"{chat_id}|{reg['phrase']}|{slot['date'].strftime('%Y%m%d')}|{slot['start_h']}"
    if key in _scheduled:
        return
    start_iran = slot["date"] + timedelta(hours=slot["start_h"])
    remind_at_iran = start_iran - timedelta(minutes=reg.get("remind", 10))
    delay = (remind_at_iran - iran_now()).total_seconds()
    if delay <= 0 or delay > 2 * 24 * 3600:
        return
    _scheduled.add(key)

    async def _remind():
        await asyncio.sleep(delay)
        _scheduled.discard(key)
        try:
            await client.send_message(
                chat_id,
                f"⏰ یادآوری: قطعی برق «{reg['name']}» تا {reg.get('remind', 10)} دقیقه‌ی دیگه "
                f"(ساعت {slot['start_h']}:۰۰ به وقت ایران) شروع می‌شه!")
        except Exception:
            pass

    asyncio.get_event_loop().create_task(_remind())


async def send_tomorrow_digest(client) -> int:
    """برنامه‌ی قطعی فردا را برای هر کاربر (بر اساس منطقه‌هایش) می‌فرستد"""
    tomorrow = (iran_now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    sent = 0
    for chat_id, lst in regions.items():
        lines = [f"🌙 برنامه‌ی قطعی برق فردا",
                 f"📅 {fmt_jdate(tomorrow)}", ""]
        any_hit = False
        for reg in lst:
            if reg.get("mute"):
                continue
            hits = []
            for c in state.get("corpus", []):
                for s in parse_announcement(c):
                    if s.get("date") == tomorrow and norm(reg["phrase"]) in norm(s["areas"]):
                        hits.append(s)
            if hits:
                any_hit = True
                lines.append(f"📍 «{reg['name']}»:")
                seen_h = set()
                for s in sorted(hits, key=lambda x: x["start_h"]):
                    key = (s["start_h"], s["end_h"])
                    if key in seen_h:
                        continue
                    seen_h.add(key)
                    lines.append(f"    🕐 ساعت {fa_num(s['start_h'])}:۰۰ تا {fa_num(s['end_h'])}:۰۰"
                                 f" ({fmt_jdate(s.get('date'))})")
                lines.append("")
        if any_hit:
            lines.append("(برنامه‌ی احتمالی — ممکنه تغییر کنه)")
            try:
                await client.send_message(int(chat_id), "\n".join(lines))
                sent += 1
            except Exception as e:
                log.warning("nightly %s failed: %s", chat_id, str(e)[:60])
    if sent:
        log.info("bargha: گزارش شبانه برای %d کاربر ارسال شد", sent)
    return sent


async def nightly_loop(client):
    """هر شب ساعت ۰۰:۰۰ به وقت ایران — گزارش فردا برای همه"""
    last_day = None
    while True:
        try:
            now = iran_now()
            next_mid = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            await asyncio.sleep(min((next_mid - now).total_seconds(), 6 * 3600))
            if iran_now().hour != 0:
                continue   # هنوز نیمه‌شب نشده؛ دوباره حساب کن
            today = iran_now().date()
            if last_day == today:
                continue   # امشب قبلاً فرستادیم
            last_day = today
            # قبل از ارسال، واکشی تازه از کانال‌ها
            try:
                posts = await asyncio.to_thread(fetch_posts)
                new = [p for p in posts if p["id"] not in state.get("last_ids", [])]
                state["last_ids"] = [p["id"] for p in posts[:10]]
                save_state()
                for p_ in new:
                    if is_announcement(p_["text"]):
                        await process_announcement(client, p_["text"])
            except Exception:
                pass
            await send_tomorrow_digest(client)
        except Exception:
            await asyncio.sleep(600)


async def poll_loop(client):
    posts = await asyncio.to_thread(fetch_posts)
    if posts:
        for p in posts:
            if p["text"] not in state["corpus"] and not is_announcement(p["text"]):
                continue
            if p["text"] not in state["corpus"]:
                state["corpus"] = ([p["text"]] + state.get("corpus", []))[:40]
        state["last_ids"] = [p["id"] for p in posts[:10]]
        save_state()
        log.info("bargha: واکشی اولیه %d پست", len(posts))
    reschedule_all(client)
    while True:
        await asyncio.sleep(POLL_MINUTES * 60)
        try:
            posts = await asyncio.to_thread(fetch_posts)
            if not posts:
                continue
            new = [p for p in posts if p["id"] not in state.get("last_ids", [])]
            state["last_ids"] = [p["id"] for p in posts[:10]]
            save_state()
            for p in new:
                if is_announcement(p["text"]):
                    await process_announcement(client, p["text"])
                    log.info("bargha: اطلاعیه جدید پردازش شد (%s)", p["id"])
        except Exception:
            log.exception("bargha: خطا در پایش کانال")


def latest_date():
    """جدیدترین تاریخ بین اسلات‌های همه‌ی اطلاعیه‌ها"""
    best = None
    for c in state.get("corpus", []):
        for s in parse_announcement(c):
            if s.get("date") and (best is None or s["date"] > best):
                best = s["date"]
    return best


def _today_slots():
    today = iran_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return [s for c in state.get("corpus", []) for s in parse_announcement(c)
            if s.get("date") and s["date"] == today]


def freshness_text() -> str:
    """آیا اطلاعیه‌ی مخصوص «امروز» رسیده؟ (وقت ایران)"""
    if not state.get("corpus"):
        return "❓ هنوز اطلاعیه‌ای ثبت نشده — یکی از کانال برق فوروارد کن"
    today = iran_now().replace(hour=0, minute=0, second=0, microsecond=0)
    if _today_slots():
        return "✅ اطلاعیه‌ی امروز دریافت و ثبت شده"
    d = latest_date()
    if d and d > today:
        diff = (d - today).days
        return (f"🟡 اطلاعیه برای {diff} روز آینده هست، "
                f"ولی اطلاعیه‌ی «امروز» هنوز نیامده")
    if d and d < today:
        return (f"⚠️ آخرین اطلاعیه {(today - d).days} روز پیش است — قدیمیه!\n"
                f"اگر برنامه‌ی امروز منتشر شده، از کانال برق فورواردش کن")
    return "🟡 اطلاعیه‌ی «امروز» هنوز نیامده — از کانال برق فوروارد کن"


def iran_now():
    """زمان فعلی ایران (سرور UTC است)"""
    return datetime.utcnow() + timedelta(hours=3, minutes=30)


async def freshness_loop(client):
    """روزی ۳ بار (۱۰، ۱۵ و ۲۱ به وقت ایران) تازگی اطلاعیه را به مدیر گزارش می‌دهد"""
    while True:
        try:
            now = iran_now()
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            nxt = None
            for h in (10, 15, 21):
                t = today.replace(hour=h)
                if t > now:
                    nxt = t
                    break
            if nxt is None:
                nxt = (today + timedelta(days=1)).replace(hour=10)
            await asyncio.sleep(min((nxt - now).total_seconds(), 6 * 3600))
            # رسیدیم به زمان چک
            if not _today_slots():
                msg = ("📋 گزارش تازگی اطلاعیه‌ها\n" + freshness_text() +
                       "\n\nاگر برنامه‌ی خاموشی امروز منتشر شده، از کانال برق فورواردش کن"
                       " تا برای همه‌ی منطقه‌ها ارسال بشه.")
                for aid in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(","):
                    if aid.strip().isdigit():
                        try:
                            await client.send_message(int(aid), msg)
                        except Exception:
                            pass
        except Exception:
            await asyncio.sleep(3600)


def latest_announcements() -> list:
    out = []
    for c in state.get("corpus", []):
        try:
            if parse_announcement(c):
                out.append(c)
        except Exception:
            pass
    return out
