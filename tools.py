#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🧰 ماژول ابزارها — مجموعه‌ی امکانات به سبک یوزربات (v1.0)

همه رایگان و بدون کلید (جز removebg که کلید رایگان می‌خواهد):
- ترجمه (گوگل) — auto-detect
- متن به ویس فارسی (Edge TTS مایکروسافت — صدای طبیعی)
- اوقات شرعی (aladhan — پیش‌فرض کاشان)
- آب‌وهوا (open-meteo — فارسی، پیش‌فرض کاشان)
- متن آهنگ (lyrics.ovh)
- سرعت اینترنت (cloudflare)
- OCR متن از عکس (ocr.space)
- حذف پس‌زمینه (remove.bg — کلید env: REMOVE_BG_KEY)
- میم‌سازی (PIL)
"""
import asyncio
import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "tools-1.0"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"}

# مختصات شهرهای پرکاربرد — پشتیبان جستجوی شهر
_CITY_COORDS = {
    "کاشان": (33.9831, 51.4364), "kashan": (33.9831, 51.4364),
    "تهران": (35.6892, 51.3890), "tehran": (35.6892, 51.3890),
    "اصفهان": (32.6546, 51.6680), "esfahan": (32.6546, 51.6680),
    "شیراز": (29.5918, 52.5837), "شیراز": (29.5918, 52.5837),
    "مشهد": (36.2605, 59.6168), "mashhad": (36.2605, 59.6168),
    "قم": (34.6416, 50.8746), "qom": (34.6416, 50.8746),
    "نجف‌آباد": (32.6343, 51.3661), "برزک": (33.9906, 51.4206),
}

# کدهای آب‌وهوای WMO → فارسی
_WMO_FA = {
    0: "☀️ آفتابی", 1: "🌤 کم‌ابری", 2: "⛅ نیمه‌ابری", 3: "☁️ ابری",
    45: "🌫 مه", 48: "🌫 یخ‌زده", 51: "🌦 نم‌نم سبک", 53: "🌦 نم‌نم",
    55: "🌦 نم‌نم شدید", 61: "🌧 باران سبک", 63: "🌧 باران",
    65: "🌧 باران شدید", 66: "🌨 باران یخی", 67: "🌨 باران یخی شدید",
    71: "❄️ برف سبک", 73: "❄️ برف", 75: "❄️ برف سنگین",
    77: "🌨 دانه‌برف", 80: "🌦 رگبار سبک", 81: "🌦 رگبار",
    82: "⛈ رگبار شدید", 85: "🌨 رگبار برفی", 86: "🌨 رگبار برفی",
    95: "⛈ توفان", 96: "⛈ توفان با تگرگ", 99: "⛈ توفان شدید با تگرگ",
}


def _get(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ═══════════════════════════ ترجمه ═══════════════════════════

_LANG_FA = {"en": "انگلیسی", "fa": "فارسی", "ar": "عربی", "fr": "فرانسه",
            "de": "آلمانی", "tr": "ترکی", "es": "اسپانیایی", "ru": "روسی",
            "it": "ایتالیایی", "zh": "چینی", "ja": "ژاپنی", "ko": "کره‌ای", "hi": "هندی"}


def translate(text, to="en"):
    """ترجمه با تشخیص خودکار زبان مبدأ"""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("متنی برای ترجمه نیست")
    to = (to or "en").strip().lower()
    if to in ("english", "انگلیسی"): to = "en"
    elif to in ("farsi", "persian", "فارسی"): to = "fa"
    elif to in ("arabic", "عربی"): to = "ar"
    q = urllib.parse.quote(text[:1500])
    url = (f"https://translate.googleapis.com/translate_a/single"
           f"?client=gtx&sl=auto&tl={to}&dt=t&q={q}")
    d = json.loads(_get(url).decode("utf-8", "ignore"))
    out = "".join(x[0] for x in (d[0] or []) if x and x[0])
    if not out:
        raise RuntimeError("ترجمه خالی برگشت")
    src = (d[2] if len(d) > 2 else "?") or "?"
    return {"text": out.strip(), "from_lang": str(src), "to_lang": to,
            "to_name": _LANG_FA.get(to, to)}


# ═══════════════════════════ متن به ویس ═══════════════════════════

def tts(text, voice=None, lang="fa"):
    """متن به ویس با Edge TTS — فارسی طبیعی (فرید/دیلارا)"""
    text = (text or "").strip()[:600]
    if not text:
        raise RuntimeError("متنی برای ویس نیست")
    # انگلیسی → صدای انگلیسی
    if re.fullmatch(r"[\x00-\x7F\s]+", text):
        voice = voice or "en-US-GuyNeural"
    else:
        voice = voice or "fa-IR-FaridNeural"

    async def _run():
        import edge_tts
        c = edge_tts.Communicate(text, voice)
        await c.save(path)

    path = tempfile.mktemp(prefix="tts_", dir="/tmp") + ".mp3"
    asyncio.run(_run())
    if not Path(path).exists() or Path(path).stat().st_size < 1000:
        raise RuntimeError("ویس ساخته نشد")
    return path


# ═══════════════════════════ اوقات شرعی ═══════════════════════════

_AZAN_FA = {"Fajr": ("اذان صبح", "🌅"), "Sunrise": ("طلوع آفتاب", "🌄"),
            "Dhuhr": ("اذان ظهر", "☀️"), "Asr": ("اذان عصر", "🌤"),
            "Sunset": ("غروب آفتاب", "🌇"), "Maghrib": ("اذان مغرب", "🌆"),
            "Isha": ("اذان عشا", "🌙"), "Midnight": ("نیمه‌شب شرعی", "🌌")}


def azan(city=None):
    """اوقات شرعی — پیش‌فرض کاشان (method 7 = ایران)"""
    city = (city or "").strip() or "کاشان"
    api_city = "Kashan" if city in ("کاشان", "kashan", "") else urllib.parse.quote(city)
    url = (f"https://api.aladhan.com/v1/timingsByCity"
           f"?city={api_city}&country=Iran&method=7")
    d = json.loads(_get(url).decode("utf-8", "ignore"))
    t = d["data"]["timings"]
    out = []
    for k, (fa, icon) in _AZAN_FA.items():
        if k in t and k != "Midnight":
            tm = (t[k] or "").split(" ")[0]
            out.append(f"{icon} {fa}: {tm}")
    hijri = d["data"]["date"].get("hijri", {}).get("date", "")
    return {"city": city, "lines": out, "hijri": hijri}


# ═══════════════════════════ آب‌وهوا ═══════════════════════════

def _city_geo(city):
    city = (city or "").strip() or "کاشان"
    if city.lower() in _CITY_COORDS or city in _CITY_COORDS:
        lat, lon = _CITY_COORDS.get(city.lower()) or _CITY_COORDS[city]
        return city, lat, lon
    try:
        q = urllib.parse.quote(city)
        d = json.loads(_get(
            f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language=fa"
        ).decode("utf-8", "ignore"))
        r = (d.get("results") or [None])[0]
        if r:
            return r.get("name") or city, r["latitude"], r["longitude"]
    except Exception:
        pass
    # پیش‌فرض: کاشان
    return "کاشان", 33.9831, 51.4364


def weather(city=None):
    """آب‌وهوای الان + امروز — فارسی"""
    name, lat, lon = _city_geo(city)
    url = (f"https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code"
           f"&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
           f"&timezone=Asia%2FTehran&forecast_days=1")
    d = json.loads(_get(url).decode("utf-8", "ignore"))
    c = d.get("current") or {}
    dy = d.get("daily") or {}
    code = int(c.get("weather_code") or 0)
    return {
        "city": name, "temp": round(c.get("temperature_2m") or 0),
        "feels": round(c.get("apparent_temperature") or 0),
        "humidity": round(c.get("relative_humidity_2m") or 0),
        "wind": round(c.get("wind_speed_10m") or 0),
        "desc": _WMO_FA.get(code, f"کد {code}"),
        "max": round((dy.get("temperature_2m_max") or [0])[0]),
        "min": round((dy.get("temperature_2m_min") or [0])[0]),
        "rain_prob": round((dy.get("precipitation_probability_max") or [0])[0]),
    }


# ═══════════════════════════ متن آهنگ ═══════════════════════════

def lyrics(song, artist=""):
    """متن آهنگ — فرمت: «خواننده - آهنگ» یا جدا"""
    song = (song or "").strip()
    if not song:
        raise RuntimeError("نام آهنگ را بگو")
    if not artist:
        # فرمت «خواننده - آهنگ» یا «آهنگ - خواننده»
        if " - " in song:
            a, b = [x.strip() for x in song.split(" - ", 1)]
            artist, song = a, b
        elif " by " in song.lower():
            i = song.lower().rindex(" by ")
            song, artist = song[:i].strip(), song[i + 4:].strip()
        else:
            raise RuntimeError("فرمت درست: «خواننده - نام آهنگ»")
    s = urllib.parse.quote(song)
    art = urllib.parse.quote(artist)
    url = f"https://api.lyrics.ovh/v1/{art}/{s}"
    d = json.loads(_get(url, timeout=25).decode("utf-8", "ignore"))
    ly = ((d.get("lyrics") or "").strip())
    if not ly:
        raise RuntimeError("متن این آهنگ پیدا نشد")
    return ly[:3500]


# ═══════════════════════════ سرعت اینترنت ═══════════════════════════

def speedtest():
    """سرعت دانلود + پینگ — از cloudflare"""
    # پینگ
    t0 = time.time()
    _get("https://speed.cloudflare.com/__down?bytes=1000", timeout=10)
    ping_ms = int((time.time() - t0) * 1000)
    # دانلود ۱۰ مگابایت
    t0 = time.time()
    data = _get("https://speed.cloudflare.com/__down?bytes=10000000", timeout=60)
    dt = time.time() - t0
    down = data.__len__() * 8 / dt / 1e6
    return {"down_mbps": round(down, 1), "ping_ms": ping_ms,
            "mb": round(len(data) / 1e6, 1)}


# ═══════════════════════════ OCR ═══════════════════════════

def ocr(path):
    """متن داخل عکس — ocr.space"""
    import base64
    boundary = "----ocr" + str(int(time.time() * 1000))
    with open(path, "rb") as f:
        img = f.read(9_000_000)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"apikey\"\r\n\r\nhelloworld\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nauto\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"isOverlayRequired\"\r\n\r\nfalse\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"img.jpg\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + img + \
           f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        "https://api.ocr.space/parse/image", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=40) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    if d.get("IsErroredOnProcessing"):
        raise RuntimeError("OCR خطا داد")
    txt = (((d.get("ParsedResults") or [{}])[0].get("ParsedText") or "")).strip()
    if not txt:
        raise RuntimeError("متنی داخل عکس پیدا نشد")
    return txt[:2500]


# ═══════════════════════════ حذف پس‌زمینه ═══════════════════════════

def removebg(path):
    """حذف پس‌زمینه — نیاز به کلید رایگان remove.bg (env: REMOVE_BG_KEY)"""
    key = (os.environ.get("REMOVE_BG_KEY") or "").strip()
    if not key:
        raise RuntimeError("برای حذف پس‌زمینه کلید رایگان remove.bg لازم است"
                           " (remove.bg → api-key → در Secrets: REMOVE_BG_KEY)")
    boundary = "----rbg" + str(int(time.time() * 1000))
    with open(path, "rb") as f:
        img = f.read(9_000_000)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"size\"\r\n\r\nauto\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image_file\"; filename=\"i.jpg\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + img + \
           f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.remove.bg/v1.0/removebg", data=body,
                                 method="POST", headers={
                                     "X-Api-Key": key,
                                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out = r.read()
    if len(out) < 2000:
        raise RuntimeError("خروجی نامعتبر")
    p = tempfile.mktemp(prefix="rbg_", dir="/tmp") + ".png"
    open(p, "wb").write(out)
    return p


# ═══════════════════════════ میم ═══════════════════════════

def _meme_font(size):
    from PIL import ImageFont
    for p_ in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(p_, size)
        except Exception:
            continue
    return ImageFont.load_default()


def meme(path, top="", bottom=""):
    """ساخت میم — متن بالا/پایین عکس با قاب سفین دور سیاه"""
    from PIL import Image, ImageDraw, ImageFont
    top = (top or "").strip()[:60]
    bottom = (bottom or "").strip()[:60]
    if not top and not bottom:
        raise RuntimeError("متن میم را بگو")
    img = Image.open(path).convert("RGB")
    W, H = img.size
    # حداکثر عرض ۹۰۰
    if W > 900:
        img = img.resize((900, int(H * 900 / W)))
        W, H = img.size
    fs = max(24, W // 12)
    font = _meme_font(fs)
    d = ImageDraw.Draw(img)

    def draw_line(txt, y_center):
        shaped = _fa_shape(txt)
        bbox = _bbox_txt(d, shaped, font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (W - tw) // 2 - bbox[0]
        y = y_center - th // 2 - bbox[1]
        for dx in (-3, 0, 3):
            for dy in (-3, 0, 3):
                _draw_txt(d, (x + dx, y + dy), shaped, font, "black")
        _draw_txt(d, (x, y), shaped, font, "white")

    if top:
        draw_line(top, int(H * 0.09))
    if bottom:
        draw_line(bottom, int(H * 0.91))
    out = tempfile.mktemp(prefix="meme_", dir="/tmp") + ".jpg"
    img.save(out, "JPEG", quality=90)
    return out


# ═══════════════════════════ دسته‌ی دوم (به سبک CatUserBot) ═══════════════════════════

def carbon(code, lang="python"):
    """کد → عکس خوشگل (carbon.now.sh از طریق carbonara)"""
    import base64
    code = (code or "").strip()[:3000]
    if not code:
        raise RuntimeError("کدی برای عکس گرفتن نیست")
    body = json.dumps({"code": code, "language": (lang or "python"),
                       "theme": "vscode", "backgroundColor": "rgba(0,0,0,0)"}).encode()
    req = urllib.request.Request("https://carbonara.solopov.dev/api/cook", data=body,
                                 headers={"Content-Type": "application/json", **UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        img = r.read()
    if len(img) < 3000:
        raise RuntimeError("عکس کد ساخته نشد")
    p = tempfile.mktemp(prefix="carbon_", dir="/tmp") + ".png"
    open(p, "wb").write(img)
    return p


def define(word):
    """معنی کلمه‌ی انگلیسی — dictionaryapi.dev"""
    word = (word or "").strip().split()[0] if (word or "").strip() else ""
    if not word:
        raise RuntimeError("کلمه‌ای بگو")
    word = word.strip(".,!?").lower()
    d = json.loads(_get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}",
                        timeout=45).decode("utf-8", "ignore"))
    if not isinstance(d, list) or not d:
        raise RuntimeError(f"«{word}» پیدا نشد")
    e = d[0]
    out = [f"📖 {e.get('word', word)}" + (f" /{e.get('phonetic', '')}/" if e.get("phonetic") else "")]
    for m in (e.get("meanings") or [])[:3]:
        part = m.get("partOfSpeech", "")
        for df in (m.get("definitions") or [])[:2]:
            line = df.get("definition", "")
            if line:
                out.append(f"• ({part}) {line[:200]}")
            if df.get("example"):
                out.append(f"  «{df['example'][:120]}»")
        if m.get("synonyms"):
            out.append(f"🔗 مترادف: {', '.join(m['synonyms'][:6])}")
    if len(out) < 2:
        raise RuntimeError("معنی پیدا نشد")
    return "\n".join(out[:14])


_TG_TOKEN_CACHE = {"token": ""}


def paste(title, text):
    """متن → صفحه‌ی تلگراف (لینک قابل اشتراک)"""
    title = (title or "متن").strip()[:80]
    text = (text or "").strip()[:40000]
    if not text:
        raise RuntimeError("متنی برای انتقال نیست")
    import re as _re
    # تبدیل خطوط به پاراگراف‌های telegraph
    content = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # escape html-ish — telegraph children متن ساده
        content.append({"tag": "p", "children": [line[:1000]]})
    if not content:
        content = [{"tag": "p", "children": [text[:1000]]}]

    def _post(url, payload):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())

    if not _TG_TOKEN_CACHE["token"]:
        acc = _post("https://api.telegra.ph/createAccount",
                    {"short_name": "bot", "author_name": "assistant"})
        if not acc.get("ok"):
            raise RuntimeError("اتصال به تلگراف نشد")
        _TG_TOKEN_CACHE["token"] = acc["result"]["access_token"]
    page = _post("https://api.telegra.ph/createPage",
                 {"access_token": _TG_TOKEN_CACHE["token"], "title": title,
                  "content": content, "return_content": False})
    if not page.get("ok"):
        raise RuntimeError("صفحه ساخته نشد")
    return page["result"]["url"]


def geo(query):
    """جستجوی مکان → (نام، عرض، طول)"""
    query = (query or "").strip()
    if not query:
        raise RuntimeError("نام مکان را بگو")
    d = json.loads(_get(
        "https://nominatim.openstreetmap.org/search?q="
        + urllib.parse.quote(query) + "&format=json&limit=1&accept-language=fa").decode("utf-8", "ignore"))
    if not d:
        raise RuntimeError("چنین مکانی پیدا نشد")
    r = d[0]
    return {"name": r.get("display_name", "")[:120],
            "lat": float(r["lat"]), "lon": float(r["lon"])}


def mediainfo(path):
    """اطلاعات کامل فایل مدیا با ffprobe"""
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    d = json.loads(r.stdout or "{}")
    fmt = d.get("format", {})
    lines = [f"📄 {Path(path).name}",
             f"📦 حجم: {int(fmt.get('size', 0)) / 1e6:.1f} MB | ⏱ {float(fmt.get('duration', 0)):.0f} ثانیه"]
    for s in (d.get("streams") or [])[:4]:
        t = s.get("codec_type")
        if t == "video":
            lines.append(f"🎬 ویدیو: {s.get('codec_name')} {s.get('width')}×{s.get('height')}"
                         + (f" {eval(s.get('avg_frame_rate', '0')):.0f}fps" if s.get("avg_frame_rate", "0") not in ("0", "0/0", "N/A") else ""))
        elif t == "audio":
            lines.append(f"🎵 صدا: {s.get('codec_name')} {s.get('sample_rate', '?')}Hz"
                         f" {int(s.get('channels', 0))}کاناله")
    return "\n".join(lines)


def video_thumb(path):
    """تصویر بندانگشتی از ویدیو (ثانیه‌ی اول)"""
    import subprocess
    out = tempfile.mktemp(prefix="thumb_", dir="/tmp") + ".jpg"
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", "1",
                        "-i", str(path), "-vframes", "1", "-vf",
                        "scale=320:-2", out], capture_output=True, timeout=90)
    if not Path(out).exists() or Path(out).stat().st_size < 1000:
        raise RuntimeError("بندانگشتی ساخته نشد")
    return out


def calc(expr):
    """ماشین‌حساب امن — فقط اعداد و عملگر"""
    expr = (expr or "").strip()
    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**").replace("،", ".")
    expr = expr.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if not expr or not re.fullmatch(r"[0-9+\-*/().%\s*]+", expr):
        raise RuntimeError("فقط عدد و عملگر (+ - * / % پرانتز)")
    try:
        v = eval(expr, {"__builtins__": {}}, {})
    except Exception:
        raise RuntimeError("عبارت نامعتبر")
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        v = int(v)
    return f"🧮 {expr} = {v}"


def text_hash(text):
    """هش متن — md5 و sha256"""
    import hashlib
    t = (text or "").strip().encode("utf-8", "ignore")
    if not t:
        raise RuntimeError("متنی بده")
    return (f"🔑 SHA-256: {hashlib.sha256(t).hexdigest()}\n"
            f"🔑 MD5: {hashlib.md5(t).hexdigest()}")


# ═══════════════════════════ دسته‌ی سوم (به سبک CatUserBot) ═══════════════════════════

# ─── رندر متن فارسی/انگلیسی با PIL ───

_VAZIR_PATH = "/tmp/vazir_font.ttf"


def _ensure_font():
    """فونت وزیر — دانلود یک‌باره و کش"""
    if Path(_VAZIR_PATH).exists() and Path(_VAZIR_PATH).stat().st_size > 50000:
        return _VAZIR_PATH
    try:
        req = urllib.request.Request(
            "https://github.com/rastikerdar/vazirmatn/raw/master/fonts/ttf/Vazirmatn-Bold.ttf",
            headers=UA)
        with urllib.request.urlopen(req, timeout=40) as r:
            open(_VAZIR_PATH, "wb").write(r.read())
        if Path(_VAZIR_PATH).stat().st_size > 50000:
            return _VAZIR_PATH
    except Exception:
        pass
    # فونت سیستمی (انگلیسی)
    for p_ in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if Path(p_).exists():
            return p_
    return None


# 🔧 فیکس متن فارسی: Pillow با raqm خودش فارسی را درست می‌کشد (direction="rtl")
# — نیازی به پکیج خارجی نیست. فقط اگر raqm نبود از reshaper استفاده می‌کنیم.
try:
    from PIL import features as _pilfeat
    _PIL_RTL = bool(_pilfeat.check("raqm"))
except Exception:
    _PIL_RTL = False


def _has_rtl(text):
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def _fa_shape(text):
    """با raqm: متن خام (خودش درست می‌کشد)؛ بدون raqm: reshaper دستی"""
    if _PIL_RTL:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _draw_txt(d, xy, text, font, fill):
    """رسم متن با جهت درست (فارسی=rtl)"""
    if _PIL_RTL and _has_rtl(text):
        d.text(xy, text, font=font, fill=fill, direction="rtl")
    else:
        d.text(xy, text, font=font, fill=fill)


def _bbox_txt(d, text, font):
    """اندازه‌گیری متن با جهت درست"""
    if _PIL_RTL and _has_rtl(text):
        return d.textbbox((0, 0), text, font=font, direction="rtl")
    return d.textbbox((0, 0), text, font=font)


def _fit_font(text, font_path, max_w, start_size):
    """بزرگ‌ترین فونتی که متن در max_w جا شود"""
    from PIL import ImageFont, ImageDraw, Image
    size = start_size
    while size > 14:
        font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        bbox = _bbox_txt(d, _fa_shape(text), font)
        if bbox[2] - bbox[0] <= max_w:
            return font, bbox
        size -= 4
    font = ImageFont.truetype(font_path, 14) if font_path else ImageFont.load_default()
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    bbox = _bbox_txt(d, _fa_shape(text), font)
    return font, bbox


# ─── زمان جهانی ───

_CITY_TZ = {
    "کاشان": "Asia/Tehran", "تهران": "Asia/Tehran", "ایران": "Asia/Tehran",
    "مشهد": "Asia/Tehran", "اصفهان": "Asia/Tehran", "شیراز": "Asia/Tehran",
    "تبریز": "Asia/Tehran", "دبی": "Asia/Dubai", "استانبول": "Europe/Istanbul",
    "لندن": "Europe/London", "پاریس": "Europe/Paris", "برلین": "Europe/Berlin",
    "نیویورک": "America/New_York", "لوس‌آنجلس": "America/Los_Angeles",
    "توکیو": "Asia/Tokyo", "پکن": "Asia/Shanghai", "مسکو": "Europe/Moscow",
    "دوحه": "Asia/Qatar", "ریاض": "Asia/Riyadh", "دهلی": "Asia/Kolkata",
    "سیدنی": "Australia/Sydney", "تورنتو": "America/Toronto",
}
_TZ_ALIASES = {"tehran": "Asia/Tehran", "kashan": "Asia/Tehran", "iran": "Asia/Tehran",
               "london": "Europe/London", "newyork": "America/New_York",
               "tokyo": "Asia/Tokyo", "dubai": "Asia/Dubai", "paris": "Europe/Paris",
               "moscow": "Europe/Moscow", "berlin": "Europe/Berlin"}

_WEEKDAY_FA = {0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه",
               5: "شنبه", 6: "یکشنبه"}


def world_time(city="کاشان"):
    """ساعت و تاریخ شهرها — آفلاین (zoneinfo)"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    c = (city or "").strip() or "کاشان"
    tz_name = _CITY_TZ.get(c) or _TZ_ALIASES.get(c.lower().replace(" ", ""))
    if not tz_name:
        # جستجو با پیشوند
        cl = c.lower()
        for k, v in _TZ_ALIASES.items():
            if k in cl:
                tz_name = v
                break
    if not tz_name:
        tz_name = "Asia/Tehran"
        c = c + " (فرض: ایران)"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Tehran")
    now = datetime.now(tz)
    fa_months = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                 "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
    try:
        import jdatetime
        jd = jdatetime.datetime.fromgregorian(datetime=now)
        jdate = f"{jd.day} {fa_months[jd.month-1]} {jd.year}"
    except Exception:
        jdate = now.strftime("%Y/%m/%d")
    return {"city": c, "time": now.strftime("%H:%M:%S"),
            "date": jdate, "weekday": _WEEKDAY_FA.get(now.weekday(), ""),
            "tz": tz_name}


# ─── والپیپر و عکس ───

def wall(query="nature"):
    """والپیپر با موضوع — wallhaven → loremflickr → picsum (سه لایه)"""
    import random as _rnd
    q_raw = (query or "nature").strip()[:40] or "nature"
    q = urllib.parse.quote(q_raw)
    # لایه ۱: wallhaven (کیفیت HD)
    try:
        url = (f"https://wallhaven.cc/api/v1/search?q={q}&categories=100&purity=100"
               f"&sorting=random&atleast=1600x900")
        d = json.loads(_get(url, timeout=20).decode("utf-8", "ignore"))
        items = d.get("data") or []
        if items:
            pick = _rnd.choice(items[:10])
            img = _get(pick["path"], timeout=45)
            if len(img) > 20000:
                p = tempfile.mktemp(prefix="wall_", dir="/tmp") + ".jpg"
                open(p, "wb").write(img)
                return p
    except Exception:
        pass
    # لایه ۲: loremflickr (عکس با موضوع)
    try:
        img = _get(f"https://loremflickr.com/1600/900/{q}", timeout=30)
        if len(img) > 15000:
            p = tempfile.mktemp(prefix="wall_", dir="/tmp") + ".jpg"
            open(p, "wb").write(img)
            return p
    except Exception:
        pass
    # لایه ۳: picsum (عکس تصادفی — موضوع نادیده)
    img = _get("https://picsum.photos/1600/900.jpg", timeout=25)
    if len(img) < 10000:
        raise RuntimeError("هیچ منبع والپیپر جواب نداد — بعداً دوباره")
    p = tempfile.mktemp(prefix="wall_", dir="/tmp") + ".jpg"
    open(p, "wb").write(img)
    return p


def random_img():
    """عکس تصادفی — picsum"""
    img = _get("https://picsum.photos/1000/700.jpg", timeout=25)
    p = tempfile.mktemp(prefix="pic_", dir="/tmp") + ".jpg"
    open(p, "wb").write(img)
    return p


# ─── لوگو ───

_LOGO_PALETTES = [
    ((88, 101, 242), (35, 39, 62)),      # بنفش دیسکورد
    ((255, 107, 107), (56, 26, 32)),     # قرمز
    ((0, 210, 255), (10, 34, 51)),       # آبی نئون
    ((46, 204, 113), (16, 46, 32)),      # سبز
    ((253, 203, 110), (59, 42, 21)),     # طلایی
    ((255, 94, 168), (52, 20, 40)),      # صورتی
]


def logo(text, style=0):
    """لوگوی متنی فارسی/انگلیسی — PIL + فونت وزیر"""
    from PIL import Image, ImageDraw
    text = (text or "").strip()[:30]
    if not text:
        raise RuntimeError("متن لوگو را بگو")
    font_path = _ensure_font()
    pal = _LOGO_PALETTES[style % len(_LOGO_PALETTES)]
    W, H = 1000, 400
    img = Image.new("RGB", (W, H), pal[1])
    d = ImageDraw.Draw(img)
    # گرادیان پس‌زمینه
    for y in range(H):
        t_ = y / H
        r = int(pal[1][0] + (pal[0][0] - pal[1][0]) * t_ * 0.35)
        g = int(pal[1][1] + (pal[0][1] - pal[1][1]) * t_ * 0.35)
        b = int(pal[1][2] + (pal[0][2] - pal[1][2]) * t_ * 0.35)
        d.line([(0, y), (W, y)], fill=(r, g, b))
    # متن
    font, bbox = _fit_font(text, font_path, W - 120, 130)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (W - tw) // 2 - bbox[0]
    y = (H - th) // 2 - bbox[1]
    # سایه
    _shaped = _fa_shape(text)
    _draw_txt(d, (x + 5, y + 5), _shaped, font, (0, 0, 0))
    _draw_txt(d, (x, y), _shaped, font, (255, 255, 255))
    # خط تزیینی
    d.rounded_rectangle([100, H - 70, W - 100, H - 62], radius=4, fill=pal[0])
    out = tempfile.mktemp(prefix="logo_", dir="/tmp") + ".png"
    img.save(out, "PNG")
    return out


# ─── نقل‌قول (جایگزین quotly — با PIL) ───

def quote(text, author=""):
    """نقل‌قول استیکری‌مانند — کارت تیره با PIL"""
    from PIL import Image, ImageDraw
    text = (text or "").strip()[:600]
    if not text:
        raise RuntimeError("متن نقل‌قول را بگو")
    author = (author or "").strip()[:40]
    font_path = _ensure_font()
    W = 900
    pad = 50
    # فونت متن
    font, bbox = _fit_font(text, font_path, W - 2 * pad - 90, 40)
    from PIL import ImageFont, Image
    d0 = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    # شکستن خطوط طولانی
    words = text.split(" ")
    lines, cur = [], ""
    for w_ in words:
        t_ = (cur + " " + w_).strip()
        bb = _bbox_txt(d0, _fa_shape(t_), font)
        if bb[2] - bb[0] > W - 2 * pad - 90 and cur:
            lines.append(cur)
            cur = w_
        else:
            cur = t_
    if cur:
        lines.append(cur)
    lines = lines[:12]
    lh = 52
    H = pad * 2 + 40 + len(lines) * lh + (50 if author else 0)
    img = Image.new("RGB", (W, H), (27, 20, 41))
    d = ImageDraw.Draw(img)
    # نوار تزیینی راست (RTL)
    d.rounded_rectangle([W - pad - 12, pad + 10, W - pad, H - pad - 10], radius=6,
                        fill=(88, 101, 242))
    # خطوط متن — راست‌چین
    y = pad + 10
    for ln in lines:
        shaped = _fa_shape(ln)
        bb = _bbox_txt(d, shaped, font)
        lw = bb[2] - bb[0]
        x = W - pad - 40 - lw - bb[0]
        _draw_txt(d, (x, y), shaped, font, (235, 235, 245))
        y += lh
    # نویسنده
    if author:
        af = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()
        a_txt = "— " + author
        shaped_a = _fa_shape(a_txt)
        bb = _bbox_txt(d, shaped_a, af)
        aw = bb[2] - bb[0]
        _draw_txt(d, (W - pad - 40 - aw - bb[0], y + 10), shaped_a, af, (130, 140, 170))
    out = tempfile.mktemp(prefix="quote_", dir="/tmp") + ".png"
    img.save(out, "PNG")
    return out


# ─── افکت‌های عکس ───

def fryer(path):
    """سرخ کردن عکس — اشباع و کنتراست بالا"""
    from PIL import Image, ImageEnhance, ImageFilter
    import random as _rnd
    img = Image.open(path).convert("RGB")
    if img.width > 800:
        img = img.resize((800, int(img.height * 800 / img.width)))
    img = ImageEnhance.Color(img).enhance(3.5)
    img = ImageEnhance.Contrast(img).enhance(2.2)
    img = ImageEnhance.Sharpness(img).enhance(12)
    # نویز
    px = img.load()
    for _ in range(img.width * img.height // 40):
        x, y = _rnd.randint(0, img.width - 1), _rnd.randint(0, img.height - 1)
        r, g, b = px[x, y]
        px[x, y] = (min(255, r + _rnd.randint(30, 90)),
                    min(255, g + _rnd.randint(-40, 40)),
                    min(255, b + _rnd.randint(-60, 40)))
    out = tempfile.mktemp(prefix="fry_", dir="/tmp") + ".jpg"
    img.save(out, "JPEG", quality=60)
    return out


def glitch(path):
    """گلیچ — جابه‌جایی کانال‌های رنگ"""
    from PIL import Image, ImageChops
    import random as _rnd
    img = Image.open(path).convert("RGB")
    W, H = img.size
    r, g, b = img.split()
    # شیفت کانال‌ها
    off1 = _rnd.randint(8, 30)
    off2 = _rnd.randint(8, 30)
    r = ImageChops.offset(r, off1, 0)
    b = ImageChops.offset(b, -off2, 0)
    img = Image.merge("RGB", (r, g, b))
    # نوارهای افقی جابه‌جا
    d = img.load()
    for _ in range(7):
        y = _rnd.randint(0, max(H - 40, 1))
        h_ = _rnd.randint(6, 30)
        shift = _rnd.randint(-60, 60)
        for yy in range(y, min(y + h_, H)):
            row = [d[x, yy] for x in range(W)]
            for x in range(W):
                d[x, yy] = row[(x - shift) % W]
    out = tempfile.mktemp(prefix="glitch_", dir="/tmp") + ".png"
    img.save(out, "PNG")
    return out


def collage(paths):
    """کولاز — شبکه‌ی عکس‌ها (۲ تا ۹ عکس)"""
    from PIL import Image
    paths = [p for p in paths if Path(p).exists()][:9]
    if len(paths) < 2:
        raise RuntimeError("حداقل ۲ عکس لازم است — روی چند عکس ریپلای کن")
    import math as _math
    n = len(paths)
    cols = _math.ceil(_math.sqrt(n))
    rows = _math.ceil(n / cols)
    CW, CH = 500, 500
    out = Image.new("RGB", (cols * CW, rows * CH), (20, 20, 30))
    for i, p_ in enumerate(paths):
        img = Image.open(p_).convert("RGB")
        img = img.resize((CW, CH))
        out.paste(img, ((i % cols) * CW, (i // cols) * CH))
    out_p = tempfile.mktemp(prefix="collage_", dir="/tmp") + ".jpg"
    out.save(out_p, "JPEG", quality=90)
    return out_p


# ═══════════════════════════ دسته‌ی چهارم: استیکر + سرگرمی ═══════════════════════════

def stickify(path):
    """تبدیل عکس به استیکر (webm — ۵۱۲×۵۱۲، بدون صدا)"""
    import subprocess
    out = tempfile.mktemp(prefix="sticker_", dir="/tmp") + ".webm"
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
         "-vf", "scale='if(gt(iw,ih),512,-2)':'if(gt(iw,ih),-2,512)',"
                "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0",
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-an", "-t", "3",
         "-b:v", "250k", out],
        capture_output=True, timeout=120)
    if not Path(out).exists() or Path(out).stat().st_size < 200:
        err = (r.stderr or b"")[-120:]
        if isinstance(err, bytes):
            err = err.decode("utf-8", "ignore")
        raise RuntimeError(f"استیکر ساخته نشد: {err}")
    return out


# ─── سرگرمی ───

import random as _random

_EIGHT_BALL = [
    "قطعاً بله ✅", "به نظره بله", "احتمالاً", "شانس بالاست 🍀",
    "شک دارم 🤔", "به نظرم نه", "قطعاً نه ❌", "الان معلوم نیست",
    "بپرس بعداً 😴", "تصادفیه 🎲", "خودت بهتر می‌دونی", "بله، ولی حواست باشه ⚠️",
    "نه، ولی غصه نخور", "آره! چرا که نه 😄", "مخالفم", "همین امشب معلوم می‌شه 🌙",
]

_JOKES = [
    "به یارو می‌گن کدوم دستت رو برای خوردن استفاده می‌کنی؟ می‌گه هیچ‌کدوم، با قاشق می‌خورم! 🥄",
    "معلم: جمله بساز با «مردم». دانش‌آموز: مردم رفت خوابید چون معلم حرف الکی می‌زد! 😴",
    "می‌گن چرا اسپرم‌ها تنبلن؟ چون همش منتظرن یکی دیگه کار رو انجام بده! 😂 (ولی جدی می‌گن چون اکتیو هستن!)",
    "یک نفر به دوستش زنگ می‌زنه: سلام، باباتونه؟ می‌گه نه. خب پس کی جواب می‌ده؟ می‌گه خودم، پسرشم! 📞",
    "به یارو می‌گن چرا عینکت رو تو یخچال گذاشتی؟ می‌گه خواستم نمای سرد داشته باشم! 🥶",
    "معلم ریاضی: اگر ۵ تا سیب داشته باشی و ۲تاشو بخوری، چی می‌مونه؟ دانش‌آموز: دندون‌هایم درد می‌کنه! 🍎",
    "دو تا هوادار رفتن مسجد. یکی گفت: خدایا بذار تیمم ببره! اون یکی گفت: آمین! وای تو هم هوادار همون تیمی؟! ⚽",
    "مامان به بچه: چرا نمره‌ت کم شده؟ بچه: چون معلم از من متنفره! مامان: چرا؟ بچه: چون من از کلاسش متنفرم! 😅",
    "به دوستم گفتم یه دروازه‌بان حسابی پیدا کردم که هیچ توپی از دستش در نمی‌ره. گفت کجاست؟ گفتم تو خوابش می‌بینی! 😴",
    "یه سوال فنی: اگه زحمت نباشه می‌تونی زحمت بکشی؟ 😂",
    "رفتم دکتر گفتم دکترا حافظه‌م ضعیف شده. گفت از کی؟ گفتم از کی چی؟ 🩺",
    "همسایه: پسرت تو خونه؟ می‌خوام باهاش فوتبال بازی کنم. مامان: نه نیست، بیا بعداً. پسر (از تو خونه): مامان من اینجام! مامان: خفه شو، اومدن که تو رو نبخوان! 😂",
]

_FACTS = [
    "🐙 اختapus سه قلب و خون آبی‌رنگ دارد.",
    "🍯 عسل هرگز فاسد نمی‌شود — عسل‌های ۳۰۰۰ ساله سالم پیدا شده‌اند!",
    "🦈 کوسه‌ها قبل از درخت‌ها روی زمین وجود داشتند.",
    "🍌 موز از نظر گیاه‌شناسی توت است، نه میوه!",
    "🌍 زمین هر سال ۱.۵ سانتی‌متر از ماه دورتر می‌شود.",
    "🐧 پنگوئن‌ها زانو دارند ولی دیده نمی‌شود.",
    "🧠 مغز انسان خودش درد ندارد — سردرد از پوست و عضلات سر است.",
    "🦒 زرافه‌ها همان تعداد مهره گردن دارند که ما: ۷ تا!",
    "🌙 در ماه وزن‌ت فقط یک ششم زمین است.",
    "🐌 حلزون می‌تواند ۳ سال خواب زمستانی کند.",
    "🐝 فقط زنبور عسل بعد از نیش زدن می‌میرد، نه زنبورهای دیگر.",
    "🌋 آتشفشان‌ها بیشتر از مریخ سیاره شناخته‌شده‌اند؟ نه! ما درباره کف اقیانوس‌ها کمتر از مریخ می‌دانیم.",
]


def eightball():
    return f"🎱 {_random.choice(_EIGHT_BALL)}"


def joke():
    return f"😂 {_random.choice(_JOKES)}"


def fact():
    return f"💡 {_random.choice(_FACTS)}"


def coinflip():
    return "🪙 شیر!" if _random.randint(0, 1) else "🪙 خط!"


def roll(sides=100):
    return f"🎲 {_random.randint(1, max(2, min(sides, 1000)))}"


def love(name1, name2):
    """محاسبه‌ی عشق — قطعی بر اساس اسم‌ها"""
    import hashlib
    n1 = _fa_norm_love(name1)
    n2 = _fa_norm_love(name2)
    if not n1 or not n2:
        raise RuntimeError("دو اسم بگو: /love علی | سارا")
    h = int(hashlib.md5((n1 + "♥" + n2).encode()).hexdigest(), 16)
    pct = 40 + (h % 61)      # ۴۰ تا ۱۰۰ — دلگرم‌کننده 😄
    if n1 == n2:
        pct = 100
    bar = "❤" * (pct // 10) + "🖤" * (10 - pct // 10)
    return f"💘 {name1} ❤️ {name2}\n{bar}\n💖 {pct}% عشق!"


def _fa_norm_love(s):
    return (s or "").strip().replace("ي", "ی").replace("ك", "ک").lower()


_HACK_LINES = [
    "در حال اتصال به سرور اصلی...",
    "دور زدن فایروال... 25%",
    "هک مودم همسایه... 47%",
    "کپی کردن اطلاعات پنتاگون... 69%",
    "پاک کردن ردپاها... 84%",
    "نصب ویروس جدید... 97%",
    "ᴛᴇʀᴍɪɴᴀʟ ꜱᴇᴄᴜʀᴇ ᴄᴏɴɴᴇᴄᴛɪᴏɴ ✅",
    "هک کامل شد! 🔓 (شوخی کردم 😄)",
]


# ═══════════════ دسته‌ی پنجم: اکانت‌دار + بدون‌کلیدهای جدید ═══════════════

def screenshot(url):
    """اسکرین‌شات سایت — thum.io (رایگان، بدون کلید)"""
    url = (url or "").strip()
    if not url.startswith("http"):
        url = "https://" + url
    img = _get(f"https://image.thum.io/get/width/1000/crop/900/{url}", timeout=60)
    if len(img) < 3000:
        raise RuntimeError("اسکرین‌شات گرفته نشد")
    p = tempfile.mktemp(prefix="shot_", dir="/tmp") + ".jpg"
    open(p, "wb").write(img)
    return p


def repo_info(full):
    """اطلاعات مخزن گیت‌هاب"""
    full = (full or "").strip().strip("/")
    if "/" not in full:
        raise RuntimeError("فرمت: user/repo")
    d = json.loads(_get(f"https://api.github.com/repos/{full}", timeout=20).decode())
    return {"name": d.get("full_name"), "stars": d.get("stargazers_count"),
            "forks": d.get("forks_count"), "lang": d.get("language") or "—",
            "desc": (d.get("description") or "—")[:150],
            "url": d.get("html_url"), "issues": d.get("open_issues_count"),
            "license": (d.get("license") or {}).get("name") or "—",
            "created": (d.get("created_at") or "")[:10]}


def uguu_upload(path):
    """فایل → لینک (uguu.se — رایگان، نگهداری ~۳ ساعت)"""
    import uuid as _uuid
    boundary = "----ug" + _uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read(90_000_000)
    import os as _os
    fname = _os.path.basename(path)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[]\"; "
            f"filename=\"{fname}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() \
        + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://uguu.se/upload?output=json", data=body,
                                 method="POST",
                                 headers={"Content-Type":
                                          f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    url = ((d.get("files") or [{}])[0].get("url") or "")
    if not url:
        raise RuntimeError("آپلود نشد")
    return url


def _userkey(name):
    """خواندن کلید شخصی از /tmp/userkeys.json"""
    try:
        d = json.loads(Path("/tmp/userkeys.json").read_text())
        return d.get(name) or ""
    except Exception:
        return ""


def reddit_hot(sub, keys=None, n=5):
    """پست‌های داغ سابردیت — کلید رایگان reddit (client_id:secret)"""
    import base64
    key = (keys or _userkey("reddit") or "").strip()
    if not key:
        raise RuntimeError("کلید ردیت لازم است: /setkey reddit آیدی:رمز (از reddit.com/prefs/apps)")
    cid, _, secret = key.partition(":")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request(
        "https://www.reddit.com/api/v1/access_token",
        data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "persian-bot/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        tok = json.loads(r.read().decode())["access_token"]
    req2 = urllib.request.Request(
        f"https://oauth.reddit.com/r/{sub}/hot?limit={n}",
        headers={"Authorization": f"Bearer {tok}", "User-Agent": "persian-bot/1.0"})
    with urllib.request.urlopen(req2, timeout=20) as r:
        d = json.loads(r.read().decode())
    out = []
    for c in (d.get("data") or {}).get("children", [])[:n]:
        p = c.get("data") or {}
        out.append({"title": (p.get("title") or "")[:90],
                    "ups": p.get("ups") or 0,
                    "url": "https://reddit.com" + (p.get("permalink") or ""),
                    "nsfw": bool(p.get("over_18"))})
    if not out:
        raise RuntimeError("چیزی نیامد — اسم سابردیت را چک کن")
    return out


def lastfm_np(user, keys=None):
    """آخرین آهنگ شنیده‌شده — کلید رایگان lastfm"""
    key = (keys or _userkey("lastfm") or "").strip()
    if not key:
        raise RuntimeError("کلید LastFM لازم است: /setkey lastfm کلید (از last.fm/api)")
    user = (user or "").strip()
    if not user:
        raise RuntimeError("یوزرنیم LastFM را بگو")
    url = (f"http://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks"
           f"&user={urllib.parse.quote(user)}&api_key={key}&format=json&limit=1")
    d = json.loads(_get(url, timeout=20).decode("utf-8", "ignore"))
    trs = ((d.get("recenttracks") or {}).get("track")) or []
    if not trs:
        raise RuntimeError("چیزی پیدا نشد — یوزرنیم یا کلید غلط؟")
    t = trs[0]
    artist = (t.get("artist") or {}).get("#text") if isinstance(t.get("artist"), dict) else t.get("artist")
    live = "@attr" in (t or {})
    return {"track": t.get("name"), "artist": artist,
            "album": ((t.get("album") or {}).get("#text") if isinstance(t.get("album"), dict) else "") or "",
            "live": live}


def nsfw_detect(path, keys=None):
    """تشخیص محتوای نامناسب عکس — کلید رایگان deepai"""
    key = (keys or _userkey("deepai") or "").strip()
    if not key:
        raise RuntimeError("کلید DeepAI لازم است: /setkey deepai کلید (از deepai.org)")
    import uuid as _uuid
    boundary = "----da" + _uuid.uuid4().hex
    with open(path, "rb") as f:
        img = f.read(9_000_000)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"i.jpg\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() \
        + img + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("https://api.deepai.org/api/nsfw-detector", data=body,
                                 method="POST", headers={
                                     "api-key": key,
                                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    score = ((d.get("output") or {}).get("nsfw_score"))
    if score is None:
        raise RuntimeError("پاسخ نامعتبر — کلید را چک کن")
    return round(float(score) * 100, 1)


# ═══════════════ اسپاتیفای (بدون لاگین + کلید اختیاری) ═══════════════

_SPOT_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist|artist)/|spotify:(track|album|playlist|artist):)"
    r"([A-Za-z0-9]{22})")


def spotify_parse(text):
    """لینک/URI اسپاتیفای → (نوع، آیدی)"""
    m = _SPOT_RE.search(text or "")
    if not m:
        return None, None
    return (m.group(1) or m.group(2)), m.group(3)


def _spot_embed(kind, sid):
    """متادیتا از صفحه‌ی embed اسپاتیفای — بدون لاگین"""
    url = f"https://open.spotify.com/embed/{kind}/{sid}"
    raw = _get(url, timeout=25).decode("utf-8", "ignore")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', raw)
    if not m:
        raise RuntimeError("اسپاتیفای جواب نداد")
    d = json.loads(m.group(1))
    ent = (((d.get("props") or {}).get("pageProps") or {})
           .get("state", {}).get("data", {}).get("entity"))
    if not ent:
        raise RuntimeError("پیدا نشد — لینک خصوصی است یا غلط")
    return ent


def spotify_track(url_or_id):
    """اطلاعات یک ترک"""
    kind, sid = spotify_parse(url_or_id) if "spotify" in str(url_or_id) else ("track", url_or_id)
    if kind != "track":
        raise RuntimeError("لینک ترک نیست")
    ent = _spot_embed("track", sid)
    artists = ", ".join(a.get("name", "") for a in (ent.get("artists") or []))
    title = ent.get("title") or ent.get("name") or "؟"
    dur_ms = ent.get("duration") or 0
    cover = ""
    try:
        cover = (ent.get("coverArt") or {}).get("sources", [{}])[-1].get("url", "")
    except Exception:
        pass
    return {"id": sid, "title": title, "artists": artists,
            "duration": dur_ms // 1000, "cover": cover}


def spotify_list(url_or_id, limit=50):
    """پلی‌لیست/آلبوم → لیست ترک‌ها"""
    kind, sid = spotify_parse(url_or_id) if "spotify" in str(url_or_id) else (None, url_or_id)
    if kind not in ("playlist", "album"):
        raise RuntimeError("لینک پلی‌لیست/آلبوم نیست")
    ent = _spot_embed(kind, sid)
    tl = ent.get("trackList") or []
    out = []
    for t in tl[:limit]:
        title = t.get("title") or "?"
        sub = (t.get("subtitle") or "").strip()
        out.append({"title": title, "subtitle": sub,
                    "uri": t.get("uri") or ""})
    return {"name": ent.get("name") or "پلی‌لیست", "kind": kind,
            "count": len(tl), "tracks": out}


def _spot_token():
    """توکن API با کلید شخصی (client_id:secret)"""
    import base64
    key = _userkey("spotify")
    if not key:
        return None
    cid, _, secret = key.partition(":")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())["access_token"]


def spotify_search(q, n=6):
    """جستجو در اسپاتیفای — نیاز به کلید رایگان"""
    tok = _spot_token()
    if not tok:
        raise RuntimeError("برای جستجوی اسپاتیفای کلید رایگان لازم است: /setkey spotify آیدی:رمز (از developer.spotify.com/dashboard)")
    url = ("https://api.spotify.com/v1/search?q=" + urllib.parse.quote(q)
           + f"&type=track&limit={n}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode())
    out = []
    for t in ((d.get("tracks") or {}).get("items")) or []:
        out.append({"id": t["id"],
                    "title": t.get("name", "?"),
                    "artists": ", ".join(a["name"] for a in (t.get("artists") or [])),
                    "duration": (t.get("duration_ms") or 0) // 1000})
    return out


def spotify_user_playlists(username):
    """پلی‌لیست‌های عمومی یک کاربر — نیاز به کلید"""
    tok = _spot_token()
    if not tok:
        raise RuntimeError("کلید اسپاتیفای لازم است: /setkey spotify آیدی:رمز")
    url = (f"https://api.spotify.com/v1/users/{urllib.parse.quote(username)}/playlists?limit=20")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode())
    return [{"name": p.get("name", "?"), "id": p.get("id"),
             "count": (p.get("tracks") or {}).get("total", 0)}
            for p in (d.get("items") or [])]


# ═══════════════ اجرای جاوااسکریپت ═══════════════

def js_run(code, path=None):
    """اجرای کد/فایل JS — موتورها به‌ترتیب: node → V8 (py_mini_racer)
    خروجی: متن stdout/نتیجه"""
    import subprocess as _sp
    import shutil as _sh
    # ── لایه ۱: node (کامل‌ترین — دسترسی کامل به ماژول‌ها) ──
    if _sh.which("node"):
        try:
            if path:
                r = _sp.run(["node", str(path)], capture_output=True,
                            text=True, timeout=30)
            else:
                r = _sp.run(["node", "-e", (code or "")[:20000]],
                            capture_output=True, text=True, timeout=30)
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            if r.returncode != 0 and not out:
                lines_ = [l for l in err.splitlines() if l.strip()]
                msg = next((l for l in lines_ if "Error" in l), lines_[-1] if lines_ else "")
                raise RuntimeError((msg or f"خطای node ({r.returncode})")[:200])
            if out:
                return out[:3500]
            if err:
                return f"⚠️ {err[:3500]}"
            return "✅ اجرا شد (بدون خروجی)"
        except _sp.TimeoutExpired:
            raise RuntimeError("زمان اجرای node تمام شد (۳۰ ثانیه)")
    # ── لایه ۲: V8 داخلی (py_mini_racer) ──
    try:
        from py_mini_racer import py_mini_racer as _pmr
        ctx = _pmr.MiniRacer()
        # شِمِ console برای V8 داخلی
        shim = ("var __out = []; var console = {log: function(){"
                "__out.push(Array.prototype.slice.call(arguments).join(' '));},"
                "error: function(){__out.push(Array.prototype.slice.call(arguments).join(' '));}};")
        ctx.eval(shim)
        src = (code or (Path(path).read_text(encoding='utf-8', errors='ignore')
                        if path else ""))[:20000]
        # اگر فایل است، کلش را eval کن
        try:
            result = ctx.eval(src)
        except Exception as e:
            raise RuntimeError(str(e)[:300])
        out = []
        try:
            logs = ctx.eval("__out.join('\\n')")
            if logs:
                out.append(str(logs))
        except Exception:
            pass
        if result is not None and str(result) != "undefined":
            out.append(str(result))
        return ("\n".join(out) or "✅ اجرا شد (بدون خروجی)")[:3500]
    except ImportError:
        raise RuntimeError("موتور JS در دسترس نیست — node روی سرور نصب نیست و py-mini-racer هم لود نشد")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(str(e)[:300])


# ═══════════════ اجرای فایل .cmd/.bat (مفسر batch ویندوز روی لینوکس) ═══════════════

def cmd_run(code=None, path=None):
    """اجرای اسکریپت batch ویندوز — مفسر کوچک:
    echo/set/set /a/if/for /l/goto/cd/md/rd/del/copy/move/ren/type/dir/
    redirection/timeout/pause/exit — در پوشه‌ی امن /tmp
    ⚠️ برنامه‌های exe ویندوزی اجرا نمی‌شوند (فقط منطق اسکریپت)"""
    import re as _re
    import time as _time
    import shutil as _sh

    if path:
        code = Path(path).read_text(encoding="utf-8", errors="ignore")
    if not (code or "").strip():
        raise RuntimeError("فایل/کد خالی است")

    lines = code.replace("\r\n", "\n").split("\n")
    root = Path(tempfile.mkdtemp(prefix="cmdrun_", dir="/tmp"))
    cwd = root
    out = []
    vars_ = {"CD": ".", "USERNAME": "user"}
    echo_on = False
    total_sleep = 0.0

    def _emit(s):
        if len(out) < 400:
            out.append(str(s))

    def _expand(s):
        def rep(m):
            return vars_.get(m.group(1).upper(), "")   # مثل cmd: تعریف‌نشده = خالی
        s = _re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", rep, s)
        return s.replace("%%", "%")

    def _resolve(p):
        p = _expand(str(p).strip().strip('"')).replace("\\", "/")
        cand = (cwd / p) if not p.startswith(("/", "\\")) else (root / p.lstrip("\\/"))
        try:
            cand = cand.resolve()
            cand.relative_to(root.resolve())
        except Exception:
            return None
        return cand

    def _split_redirect(line):
        """جداسازی `> file` و `>> file`"""
        m = _re.search(r"\s(>>|>)\s*(\S+)\s*$", line)
        if not m:
            return line, None, None
        return line[:m.start()].strip(), m.group(1), _resolve(m.group(2))

    def _write_out(text, redir, append):
        if redir is None:
            _emit(text)
            return
        if append:
            with open(redir, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        else:
            redir.parent.mkdir(parents=True, exist_ok=True)
            with open(redir, "w", encoding="utf-8") as f:
                f.write(text + "\n")

    def _find_label(lbl):
        lbl = lbl.lower()
        for j in range(len(lines)):
            s_ = lines[j].strip().lower()
            if s_ == f":{lbl}" or s_.startswith(f":{lbl} ") or s_ == f":{lbl}:":
                return j + 1
        return None

    def _exec_one(raw):
        """اجرای یک دستور → True/False/'EXIT'/('GOTO', label)"""
        nonlocal cwd
        low = raw.lower()
        m_g = _re.match(r"(?i)^goto\s+:?(\w+)$", raw)
        if m_g:
            return ("GOTO", m_g.group(1))
        # echo
        m = _re.match(r"(?i)^echo\s*(.*)$", raw)
        if m:
            arg = m.group(1).strip()
            if arg.lower() in ("off", "on"):
                nonlocal_echo(arg.lower() == "on")
                return True
            body, mode, redir = _split_redirect(arg)
            if body in (".", ""):
                _write_out("", redir, mode == ">>")
            else:
                _write_out(_expand(body), redir, mode == ">>")
            return True
        # set
        m = _re.match(r"(?i)^set\s+(/a\s+)?([A-Za-z_][A-Za-z0-9_]*)(\+?=)\s*(.+)$", raw)
        if m:
            arith, name, op, val = m.groups()
            if arith or op == "+=":
                expr = _re.sub(r"[^0-9+\-*/()%\s]", "", _expand(val)) or "0"
                try:
                    base = int(vars_.get(name.upper(), "0") or 0) if op == "+=" else 0
                    vars_[name.upper()] = str(base + int(eval(expr, {"__builtins__": {}}, {})))
                except Exception:
                    vars_[name.upper()] = "0"
            else:
                vars_[name.upper()] = _expand(val.strip().strip('"'))
            return True
        # cd
        m = _re.match(r"(?i)^cd(?:\s+(.+))?$", raw)
        if m:
            tgt = (m.group(1) or "/").strip().strip('"')
            if tgt in ("/", "\\"):
                cwd = root
            else:
                cand = _resolve(tgt)
                if cand and cand.is_dir():
                    cwd = cand
                else:
                    _emit("The system cannot find the path specified.")
            return True
        # md / mkdir
        m = _re.match(r"(?i)^(?:md|mkdir)\s+(.+)$", raw)
        if m:
            for part in _re.split(r"\s+", m.group(1)):
                cand = _resolve(part)
                if cand:
                    cand.mkdir(parents=True, exist_ok=True)
            return True
        # rd / rmdir / del / erase
        m = _re.match(r"(?i)^(?:rd|rmdir)\s+(?:/s\s+)?/q\s+(.+)$|^(?:rd|rmdir)\s+(.+)$|^(?:del|erase)\s+(.+)$", raw)
        if m:
            tgt = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            cand = _resolve(tgt)
            if cand and cand.exists():
                try:
                    if cand.is_dir():
                        _sh.rmtree(cand)
                    else:
                        cand.unlink()
                except Exception as e:
                    _emit(f"خطا: {str(e)[:60]}")
            return True
        # copy A B
        m = _re.match(r"(?i)^copy\s+(?:/y\s+)?\"?([^\"]+?)\"?\s+\"?([^\"]+?)\"?\s*$", raw)
        if m:
            src, dst = _resolve(m.group(1)), _resolve(m.group(2))
            if src and src.exists() and dst:
                dst.parent.mkdir(parents=True, exist_ok=True)
                _sh.copy2(src, dst)
                _emit("        1 file(s) copied.")
            else:
                _emit("The system cannot find the file specified.")
            return True
        # move / ren
        m = _re.match(r"(?i)^(?:move|ren|rename)\s+\"?([^\"]+?)\"?\s+\"?([^\"]+?)\"?\s*$", raw)
        if m:
            src, dst = _resolve(m.group(1)), _resolve(m.group(2))
            if src and src.exists() and dst:
                dst.parent.mkdir(parents=True, exist_ok=True)
                _sh.move(str(src), str(dst))
            else:
                _emit("The system cannot find the file specified.")
            return True
        # type
        m = _re.match(r"(?i)^type\s+(.+)$", raw)
        if m:
            f_ = _resolve(m.group(1))
            if f_ and f_.exists():
                _emit(f_.read_text(encoding="utf-8", errors="ignore")[:2000])
            else:
                _emit("The system cannot find the file specified.")
            return True
        # dir
        if low.startswith("dir"):
            _emit(f" Directory of {cwd}")
            _emit("")
            for x_ in sorted(cwd.iterdir()):
                tag = "<DIR>" if x_.is_dir() else f"{x_.stat().st_size:,}"
                _emit(f"{x_.name[:40]:<40} {tag}")
            return True
        # cls
        if low == "cls":
            out.clear()
            return True
        # ver
        if low == "ver":
            _emit("Linux CMD Interpreter [Version 1.0] — بخشی از ربات")
            return True
        # timeout
        m = _re.match(r"(?i)^timeout\s+/t\s+(\d+)", raw)
        if m:
            nonlocal_sleep(min(int(m.group(1)), 3))
            return True
        # pause
        if low.startswith("pause"):
            _emit("Press any key to continue . . . (ادامه خودکار)")
            return True
        # exit
        if low.startswith("exit"):
            return "EXIT"
        # title / color / echo. / set (بدون مقدار) / cd. → نادیده
        if low.startswith(("title ", "color ", "mode ", "chcp")) or low in ("cd.", "ver"):
            return True
        return False

    def nonlocal_echo(v):
        nonlocal echo_on
        echo_on = v

    def nonlocal_sleep(s):
        nonlocal total_sleep
        if total_sleep < 8:
            _time.sleep(s)
            total_sleep += s

    def _do_if(raw):
        """if [not] exist <f> CMD | if [not] "%a%"=="b" CMD | if [not] a LSS b CMD"""
        m = _re.match(r"(?i)^if\s+(not\s+)?(.+)$", raw)
        if not m:
            return None
        negate = bool(m.group(1))
        rest = m.group(2).strip()
        cond_ok = None
        # exist
        mm = _re.match(r"(?i)^exist\s+(\"[^\"]+\"|\S+)\s+(.+)$", rest)
        if mm:
            f_ = _resolve(mm.group(1))
            cond_ok = bool(f_ and f_.exists())
            tail = mm.group(2)
        else:
            # مقایسه: "a"=="b" یا a EQU b
            mm = _re.match(r'^"([^"]*)"\s*==\s*"([^"]*)"\s+(.+)$', rest)
            if mm:
                cond_ok = _expand(mm.group(1)) == _expand(mm.group(2))
                tail = mm.group(3)
            else:
                mm = _re.match(r'(\S+)\s+(EQU|NEQ|LSS|LEQ|GTR|GEQ)\s+(\S+)\s+(.+)$', rest, _re.I)
                if mm:
                    try:
                        a_, b_ = float(_expand(mm.group(1))), float(_expand(mm.group(3)))
                    except ValueError:
                        a_, b_ = _expand(mm.group(1)), _expand(mm.group(3))
                    op = mm.group(2).upper()
                    cond_ok = {"EQU": a_ == b_, "NEQ": a_ != b_, "LSS": a_ < b_,
                               "LEQ": a_ <= b_, "GTR": a_ > b_, "GEQ": a_ >= b_}[op]
                    tail = mm.group(4)
                else:
                    return None
        if negate:
            cond_ok = not cond_ok
        tail = tail.strip()
        # (cmd) else (cmd2) — با پرانتز
        mm = _re.match(r"^\((.+?)\)\s*else\s*\((.+)\)$", tail, _re.I)
        if mm:
            return _exec_one(mm.group(1 if cond_ok else 2).strip())
        # cmd else cmd2 — بدون پرانتز
        mm = _re.match(r"^(.+?)\s+else\s+(.+)$", tail, _re.I)
        if mm:
            return _exec_one((mm.group(1) if cond_ok else mm.group(2)).strip())
        # فقط (cmd)
        mm = _re.match(r"^\((.+)\)$", tail)
        if mm:
            return _exec_one(mm.group(1).strip()) if cond_ok else True
        return _exec_one(tail) if cond_ok else True

    def _split_amp(line):
        """جدا کردن دستورات با & و && (خارج از کوتیشن)"""
        parts, cur, in_q = [], "", False
        k = 0
        while k < len(line):
            c = line[k]
            if c == '"':
                in_q = not in_q
                cur += c
            elif c == "&" and not in_q:
                if k + 1 < len(line) and line[k + 1] == "&":
                    k += 1
                parts.append(cur.strip())
                cur = ""
            else:
                cur += c
            k += 1
        parts.append(cur.strip())
        return [p for p in parts if p]

    i, steps = 0, 0
    try:
        while i < len(lines):
            steps += 1
            if steps > 600:
                _emit("… (سقف ۶۰۰ دستور)")
                break
            raw = lines[i].strip()
            i += 1
            if not raw or raw.startswith("::") or raw.lower().startswith("rem "):
                continue
            if raw.startswith("@"):
                raw = raw[1:].strip()
                if not raw:
                    continue
            if raw.startswith(":"):
                continue
            jumped = False
            exited = False
            for part_ in _split_amp(raw):
                m = _re.match(r"(?i)^goto\s+:?(\w+)$", part_)
                if m:
                    j_ = _find_label(m.group(1))
                    if j_ is not None:
                        i = j_
                    else:
                        _emit(f"The system cannot find the label :{m.group(1)}")
                    jumped = True
                    break
                m = _re.match(r'(?i)^for\s+/l\s+%%(\w)\s+in\s*\(\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\)\s+do\s+(.+)$', part_)
                if m:
                    var, a_, st_, b_ = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
                    body = m.group(5)
                    n_ = 0
                    v_ = a_
                    while (st_ > 0 and v_ <= b_) or (st_ < 0 and v_ >= b_):
                        n_ += 1
                        if n_ > 500:
                            _emit("… (سقف ۵۰۰ تکرار)")
                            break
                        body_v = _re.sub(r"%%" + var, str(v_), body, flags=_re.I)
                        vars_[var.upper()] = str(v_)
                        r_ = _exec_one(_expand(body_v))
                        if r_ == "EXIT":
                            raise SystemExit
                        if isinstance(r_, tuple) and r_[0] == "GOTO":
                            raise SystemExit
                        v_ += st_
                    continue
                if part_.lower().startswith("if "):
                    r_ = _do_if(part_)
                    if isinstance(r_, tuple) and r_ and r_[0] == "GOTO":
                        j_ = _find_label(r_[1])
                        if j_ is not None:
                            i = j_
                        else:
                            _emit(f"The system cannot find the label :{r_[1]}")
                        jumped = True
                        break
                    elif r_ == "EXIT":
                        exited = True
                        break
                    continue
                r_ = _exec_one(part_)
                if r_ == "EXIT":
                    exited = True
                    break
                if r_ is False:
                    _emit(f"'{part_.split()[0]}' is not recognized as an internal or external command.")
            if exited:
                break
            if jumped:
                continue
    except SystemExit:
        pass
    result = "\n".join(out[:400])[:3500]
    return result or "✅ اجرا شد (بدون خروجی)"


# ═══════════════ فایل → لینک + ابزار صدای Whisper ═══════════════

def _mp_fields(boundary, fields, files):
    """ساخت multipart/form-data"""
    import uuid as _uuid
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (fn, blob, ct) in files.items():
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\n"
                      f"Content-Type: {ct}\r\n\r\n").encode() + blob + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


# ── سقف واقعی هر میزبان آپلود (بررسی‌شده) ────────────────────────────────
GOFILE_MAX  = 2000 * 1024 * 1024     # gofile فایل‌های چند گیگی را هم قبول می‌کند
FILEBIN_MAX = 1900 * 1024 * 1024     # همان چیزی که کد قبلی فرض می‌کرد (~۱٫۹ گیگ)
CATBOX_MAX  = 200  * 1024 * 1024     # محدودیت رسمی catbox/litterbox — مهم!
UGUU_MAX    = 256  * 1024 * 1024     # سقف تقریبی uguu


def _have_curl() -> bool:
    import shutil as _sh
    return bool(_sh.which("curl"))


def _curl_post(url, fields, file_field, file_path, timeout=1800, want_json=False):
    """آپلود جریانی (بدون خواندن کل فایل در RAM) با curl سیستم."""
    import subprocess as _sp
    args = ["curl", "-sS", "--fail-with-body", "--max-time", str(timeout),
            "--connect-timeout", "20", "--retry", "1",
            "-A", UA["User-Agent"], "-X", "POST"]
    for k, v in (fields or {}).items():
        args += ["-F", f"{k}={v}"]
    args += ["-F", f"{file_field}=@{file_path};type=application/octet-stream", url]
    r = _sp.run(args, capture_output=True, text=True, timeout=timeout + 60)
    if r.returncode != 0:
        raise RuntimeError(f"آپلود قطع شد ({r.returncode}): {(r.stderr or '').strip()[-140:]}")
    out = (r.stdout or "").strip()
    if want_json:
        try:
            return json.loads(out or "{}")
        except Exception:
            raise RuntimeError(f"جواب نامعتبر بود: {out[:80]}")
    return out


def upload_gofile(path):
    """فایل → لینک دائمی (gofile.io) — جریانی، بدون بُریدن فایل."""
    size = Path(path).stat().st_size
    if size > GOFILE_MAX:
        raise RuntimeError(f"gofile: فایل {size // 1024 ** 2}MB از سقف این میزبان بزرگ‌تر است")
    # سرور فعال را بگیر
    req = urllib.request.Request("https://api.gofile.io/servers", headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode())
    servers = (d.get("data") or {}).get("servers") or []
    if not servers:
        raise RuntimeError("سرور gofile در دسترس نیست")
    url_api = f"https://{servers[0]['name']}.gofile.io/contents/uploadfile"
    if _have_curl():                                  # مسیر اصلی: جریانی و مطمئن
        d = _curl_post(url_api, {}, "file", path, timeout=1800, want_json=True)
    else:                                             # fallback کم‌حجم (بدون curl)
        if size > 900_000_000:
            raise RuntimeError("gofile: بدون curl فقط تا ~۸۶۰MB")
        import uuid as _uuid
        with open(path, "rb") as f:
            data = f.read(size)                       # دقیقاً به اندازهٔ فایل (نه بیشتر!)
        b = "----gf" + _uuid.uuid4().hex
        body = _mp_fields(b, {}, {"file": (Path(path).name, data, "application/octet-stream")})
        rq = urllib.request.Request(url_api, data=body, method="POST",
                                    headers={**UA, "Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(rq, timeout=1800) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
    url = ((d.get("data") or {}).get("downloadPage") or "")
    if d.get("status") != "ok" or not url:
        raise RuntimeError(f"gofile: {str(d)[:80]}")
    return url


def upload_litterbox(path, time_="72h"):
    """فایل → لینک موقت (litterbox/catbox — ۷۲ ساعت، سقف رسمی ۲۰۰MB)"""
    size = Path(path).stat().st_size
    if size > CATBOX_MAX:
        raise RuntimeError(f"litterbox: سقف این سرویس ۲۰۰MB است (فایل {size // 1024 ** 2}MB)")
    LB_API = "https://litterbox.catbox.moe/resources/internals/api.php"
    if _have_curl():
        url = _curl_post(LB_API, {"reqtype": "fileupload", "time": time_},
                         "fileToUpload", path, timeout=900)
    else:
        import uuid as _uuid
        with open(path, "rb") as f:
            data = f.read(size)
        b = "----lb" + _uuid.uuid4().hex
        body = _mp_fields(b, {"reqtype": "fileupload", "time": time_},
                          {"fileToUpload": (Path(path).name, data, "application/octet-stream")})
        req = urllib.request.Request(LB_API, data=body, method="POST",
                                     headers={**UA, "Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=900) as r:
            url = r.read().decode().strip()
    url = (url or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"litterbox: {url[:60]}")
    return url


def upload_file_link(path):
    """فایل → لینک — زنجیرهٔ میزبان‌ها؛ هر کدام که سقفش به فایل بخورد امتحان می‌شود.
    خروجی: {"url":..., "kind": str}"""
    size = Path(path).stat().st_size

    def _uguu_guarded(p):
        if Path(p).stat().st_size > UGUU_MAX:
            raise RuntimeError(f"uguu: سقف ~{UGUU_MAX // 1024 ** 2}MB")
        return uguu_upload(p)

    chain = [
        (upload_gofile,      "دائمی",    "gofile (دائمی)",         GOFILE_MAX),
        (upload_filebin,     "۶ روزه",   "filebin (۶ روز)",        FILEBIN_MAX),
        (upload_litterbox,   "۷۲ ساعته", "litterbox (۷۲ ساعت)",    CATBOX_MAX),
        (_uguu_guarded,      "۳ ساعته",  "uguu (۳ ساعت)",          UGUU_MAX),
    ]
    fails = []
    for fn, kind, label, cap in chain:
        if size > cap:
            print(f"[link] ⤵️ «{label}» رد شد — سقفش {cap // 1024 ** 2}MB است "
                  f"(فایل {size // 1024 ** 2}MB)", flush=True)
            continue
        try:
            url = fn(path)
            print(f"[link] ✅ لینک ساخته شد → {label}", flush=True)
            return {"url": url, "kind": kind}
        except Exception as ex:
            fails.append(f"{label}: {str(ex)[:70]}")
            print(f"[link] ❌ «{label}» ناموفق بود: {str(ex)[:90]}", flush=True)
    raise RuntimeError("هیچ میزبان آپلودی جواب نداد:\n" + "\n".join(fails)[-350:] +
                       "\n⏳ چند دقیقه دیگر دوباره /link بزن.")


def audio_extract(path, out_ext="mp3", enhance=False):
    """استخراج/بهبود صدا برای Whisper — ویس‌های غیر ogg و ویدیوها"""
    import subprocess as _sp
    import shutil as _sh
    out = tempfile.mktemp(prefix="aud_", dir="/tmp") + "." + out_ext
    af = ["-af", "highpass=f=80,lowpass=f=7800,loudnorm=I=-18:TP=-1.5"] if enhance else []
    r = _sp.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(path),
                 "-vn"] + af + ["-c:a", "libmp3lame", "-b:a", "96k", out],
                capture_output=True, timeout=300)
    if not Path(out).exists() or Path(out).stat().st_size < 500:
        raise RuntimeError("استخراج صدا ناموفق: " + (r.stderr or b"")[-100:].decode("utf-8", "ignore"))
    return out


def upload_filebin(path):
    """فایل → لینک (filebin.net — بدون کلید، فایل‌های بزرگ)
    ذخیره‌سازی: تا ۶ روز پس از آخرین دسترسی"""
    import uuid as _uuid
    import random as _rnd
    import string as _str
    bin_id = "".join(_rnd.choices(_str.ascii_lowercase + _str.digits, k=10))
    with open(path, "rb") as f:
        data = f.read(1_900_000_000)      # تا ~۱.۹ گیگ
    b = "----fb" + _uuid.uuid4().hex
    fname = Path(path).name
    body = ((f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\n"
             f"Content-Type: application/octet-stream\r\n\r\n").encode()
            + data + f"\r\n--{b}--\r\n".encode())
    req = urllib.request.Request(
        f"https://filebin.net/{bin_id}/{urllib.parse.quote(fname)}",
        data=body, method="POST",
        headers={**UA, "Content-Type": f"multipart/form-data; boundary={b}",
                 "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    f_info = d.get("file") or {}
    if not f_info.get("filename"):
        raise RuntimeError(f"filebin: {str(d)[:80]}")
    return f"https://filebin.net/{bin_id}/{urllib.parse.quote(fname)}"
