#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📸 ماژول اینستاگرام — بازسازی‌کامل v2 (ig-2.0) — جدید

مشکل قبلی: فقط یک مسیر (yt-dlp ساده + صفحه HTML پروفایل) — از IP دیتاسنتر
اینستاگرام صفحه‌ی HTML را با 429/لاگین می‌بندد و همه‌چیز fail می‌شد.

این نسخه چندمسیره است:
- رسانه (پست/ریلز/IGTV): yt-dlp با دو UA مختلف (تست‌شده از دیتاسنتر ✅)
  + پشتیبانی از کاروسل (چند عکس/ویدیو در یک پست)
  + لینک‌های share/v→ تبدیل به لینک اصلی
- پروفایل: API رسمی وب اینستا (x-ig-app-id) → ریدر جایگزین → عدم موفقیت صادقانه
"""
import asyncio
import json
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "ig-2.2"

UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
IG_APP_ID = "936619743392459"

POST_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?"
    r"(p|reel|reels|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)


def is_insta_url(text: str) -> bool:
    return bool(POST_RE.search(text or ""))


def extract_post(url: str):
    """(نوع, کد) از لینک"""
    m = POST_RE.search(url or "")
    return (m.group(1).lower(), m.group(2)) if m else (None, None)


async def _resolve_share(url: str) -> str:
    """لینک‌های instagram.com/share/... را به لینک اصلی تبدیل می‌کند"""
    if "/share/" not in url:
        return url

    def run():
        req = urllib.request.Request(url, headers={"User-Agent": UA_MOBILE})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.geturl()   # آدرس نهایی بعد از ریدایرکت
    try:
        return await asyncio.to_thread(run)
    except Exception:
        return url


def _ydl_opts(ua):
    return {
        "quiet": True, "no_warnings": True, "socket_timeout": 20,
        "retries": 1, "nocheckcertificate": True,
        "http_headers": {"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"},
    }


# ------------------------------------------------ رسانه

async def download_media(url):
    """دانلود پست/ریلز → (لیست فایل‌ها، info، روش)"""
    url = await _resolve_share(url)

    async def attempt(ua, tag):
        import yt_dlp

        def run():
            opts = _ydl_opts(ua)
            opts["outtmpl"] = tempfile.mktemp(prefix="ig_", dir="/tmp") + ".%(ext)s"
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(url, download=True)
            if info and info.get("_type") == "playlist":
                files = []
                for e in (info.get("entries") or []):
                    for d in (e.get("requested_downloads") or []):
                        if d.get("filepath") and Path(d["filepath"]).exists():
                            files.append(d["filepath"])
                return files, info
            files = [d["filepath"] for d in info.get("requested_downloads", [])
                     if d.get("filepath") and Path(d["filepath"]).exists()]
            if not files:   # بعضی نسخه‌ها filepath نمی‌دهند
                stem = re.sub(r"[^A-Za-z0-9_-]", "", Path(opts["outtmpl"]).stem)[:20]
                files = [str(p) for p in Path("/tmp").glob(f"ig_*{stem}*")
                         if p.suffix in (".mp4", ".jpg", ".jpeg", ".png", ".webp")]
            return files, info

        files, info = await asyncio.to_thread(run)
        if not files:
            raise RuntimeError("فایلی دانلود نشد")
        return files[:10], info, tag

    last = None
    for ua, tag in ((UA_DESKTOP, "yt-dlp"), (UA_MOBILE, "yt-dlp-mobile")):
        try:
            return await asyncio.wait_for(attempt(ua, tag), timeout=150)
        except Exception as e:
            last = e
    s = str(last or "").lower()
    if "login" in s or "rate" in s or "401" in s or "429" in s:
        raise RuntimeError("اینستا فعلاً به سرور ما محدودیت داده — چند دقیقه بعد دوباره.")
    raise last


# ------------------------------------------------ پروفایل

def _ig_api_profile(uname):
    """API رسمی وب اینستا — کامل‌ترین داده (اگر IP محدود نباشد)"""
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={uname}"
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_DESKTOP, "x-ig-app-id": IG_APP_ID,
        "Accept": "*/*", "Referer": f"https://www.instagram.com/{uname}/",
        "sec-fetch-site": "same-origin", "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    u = d["data"]["user"]
    return {
        "ok": True, "method": "api",
        "name": (u.get("full_name") or "").strip() or uname,
        "username": uname,
        "followers": u.get("edge_followed_by", {}).get("count"),
        "following": u.get("edge_follow", {}).get("count"),
        "posts": u.get("edge_owner_to_timeline_media", {}).get("count"),
        "private": bool(u.get("is_private")),
        "verified": bool(u.get("is_verified")),
        "bio": (u.get("biography") or "")[:200],
        "pic": u.get("profile_pic_url_hd") or u.get("profile_pic_url") or None,
    }


def _jina_profile(uname):
    """ریدر جایگزین — حداقل اسم/وجود"""
    req = urllib.request.Request(
        f"https://r.jina.ai/https://www.instagram.com/{uname}/",
        headers={"User-Agent": UA_DESKTOP})
    with urllib.request.urlopen(req, timeout=35) as r:
        txt = r.read().decode("utf-8", "ignore")
    m = re.search(r"Title:\s*(.+)", txt)
    if not m:
        raise RuntimeError("parse")
    title = m.group(1).strip()
    # «Instagram» یا «Login • Instagram» یعنی دیوار لاگین → شکست
    if title.strip().lower() in ("instagram", "login • instagram",
                                 "instagram • login", "log in • instagram"):
        raise RuntimeError("login_wall")
    if "page not found" in title.lower() or "sorry" in title.lower():
        raise RuntimeError("not_found")
    nm = re.match(r"^(.*?)\s*[(（]?@", title)
    if not nm:
        nm2 = re.match(r"^(.+?)\s+on Instagram", title)
        if nm2:
            return {"ok": True, "method": "reader", "name": nm2.group(1).strip(),
                    "username": uname, "followers": None, "following": None,
                    "posts": None, "private": None, "verified": None,
                    "bio": "", "pic": None}
        raise RuntimeError("login_wall")
    return {
        "ok": True, "method": "reader",
        "name": (nm.group(1).strip() if nm and nm.group(1).strip() else uname),
        "username": uname, "followers": None, "following": None, "posts": None,
        "private": None, "verified": None, "bio": "",
        "pic": None,
    }


async def profile(uname):
    """اطلاعات پروفایل — چندمسیره"""
    uname = uname.lstrip("@").strip()

    def run():
        last = None
        for fn in (_ig_api_profile, _jina_profile):
            try:
                return fn(uname)
            except Exception as e:
                s = str(e).lower()
                if "not_found" in s or "404" in s:
                    return {"ok": False, "reason": "not_found", "username": uname}
                last = e
        # مسیر طلایی: پروفایل از نتایج جستجو — مستقل از قفل اینستا
        try:
            r = profile_via_search(uname)
            if r:
                return r
        except Exception:
            pass
        return {"ok": False, "reason": "limited", "err": str(last or "")[:120],
                "username": uname}

    return await asyncio.to_thread(run)


async def download_pic(url, dest=None):
    """دانلود مستقیم عکس پروفایل"""
    dest = dest or tempfile.mktemp(prefix="ig_pp_", dir="/tmp") + ".jpg"

    def run():
        req = urllib.request.Request(url, headers={"User-Agent": UA_DESKTOP})
        with urllib.request.urlopen(req, timeout=25) as r, open(dest, "wb") as w:
            w.write(r.read(3_000_000))
        return dest if Path(dest).stat().st_size > 2000 else None
    return await asyncio.to_thread(run)


# ------------------------------------------------ جستجو (از طریق موتور جستجو)

_BING_SKIP = ("/p/", "/reel", "/tv/", "/explore", "/accounts", "/stories",
              "/web/", "about.", "legal.", "developer", "directory", "favicon")


def _bing_urls(query, n=10):
    """لینک‌های نتیجه از Bing — از دیتاسنتر کار می‌کند"""
    import urllib.parse
    import base64
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_DESKTOP, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=15) as r:
        pg = r.read().decode("utf-8", "ignore")
    urls = []
    for m in re.findall(r'u=a1([A-Za-z0-9_-]{20,})', pg):   # لینک‌های کدگذاری‌شده
        try:
            pad = m + "=" * (-len(m) % 4)
            urls.append(base64.urlsafe_b64decode(pad).decode("utf-8", "ignore"))
        except Exception:
            pass
    urls += re.findall(r'href="(https?://[^"]+)"', pg)
    out = []
    for u in urls:
        try:
            p = urllib.parse.urlparse(u)
        except Exception:
            continue
        if p.netloc not in ("instagram.com", "www.instagram.com"):
            continue
        m = re.fullmatch(r"/([A-Za-z0-9_.]{2,30})/?", p.path)
        if m and m.group(1).lower() not in (
                "p", "reel", "reels", "tv", "explore", "instagram",
                "accounts", "stories", "web", "about", "legal", "developer"):
            out.append(m.group(1))
    return list(dict.fromkeys(out))[:n]


# ------------------------------------------------ جستجو از طریق Bing RSS

_IG_RESERVED = ("p", "reel", "reels", "tv", "explore", "accounts", "stories",
                "web", "about", "legal", "developer", "directory", "instagram")


_BING_LAST = [0.0]          # گِیت نرخ: حداقل ۳ ثانیه بین درخواست‌های Bing
_SEARCH_CACHE = {}          # (کوئری) → (ts, نتیجه) — ۱۰ دقیقه


def _bing_gate():
    import time as _t
    wait = 3.0 - (_t.time() - _BING_LAST[0])
    if wait > 0:
        _t.sleep(min(wait, 6))
    _BING_LAST[0] = _t.time()


def _bing_rss(query, n=10):
    """نتایج Bing RSS — پایدار، با عنوان + لینک + توضیحات (تست‌شده از دیتاسنتر)"""
    import time as _t
    import urllib.parse
    import html as _h
    ck = query.lower()
    now = _t.time()
    if ck in _SEARCH_CACHE and now - _SEARCH_CACHE[ck][0] < 600:
        return _SEARCH_CACHE[ck][1]
    _bing_gate()
    url = ("https://www.bing.com/search?q=" + urllib.parse.quote(query)
           + "&format=rss")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_DESKTOP, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=15) as r:
        xml = r.read().decode("utf-8", "ignore")
    out = []
    for m in re.finditer(
            r"<item><title>(.*?)</title><link>(.*?)</link>(?:<description>(.*?)</description>)?",
            xml, re.S):
        title = _h.unescape(m.group(1) or "")
        link = (m.group(2) or "").strip()
        desc = _h.unescape(m.group(3) or "")
        if link.startswith("http"):
            out.append({"title": title, "url": link, "desc": desc})
        if len(out) >= n:
            break
    _SEARCH_CACHE[ck] = (now, out)
    return out


def _ig_users_from_rss(items):
    """یوزرنیم‌های پروفایل از نتایج RSS"""
    users = []
    for it in items:
        m = re.match(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})/?$",
                     it["url"].split("?")[0].rstrip("/"))
        if m and m.group(1).lower() not in _IG_RESERVED:
            users.append(m.group(1))
    return list(dict.fromkeys(users))


def ig_search(query, n=8):
    """جستجوی یوزرنیم اینستا — چندVariant کوئری تا یکی جواب دهد"""
    q = query.strip().lstrip("@").strip()
    if not q:
        return []
    users = []
    for fmt in (f"{q} instagram", f"instagram {q}", f"{q} insta"):
        try:
            users = _ig_users_from_rss(_bing_rss(fmt, 12))
        except Exception:
            users = []
        if users:
            break
    if not users:
        try:
            users = _bing_urls(f"{q} instagram", n)
        except Exception:
            users = []
    return users[:n]


def tag_search(tag, n=8):
    """جستجوی اکانت‌های مرتبط با تگ/موضوع"""
    tag = tag.strip().lstrip("#").strip()
    if not tag:
        return []
    users = []
    for fmt in (f"{tag} instagram", f"instagram {tag}", f"{tag} hashtag"):
        try:
            users = _ig_users_from_rss(_bing_rss(fmt, 12))
        except Exception:
            users = []
        if users:
            break
    return users[:n]


def profile_via_search(uname):
    """پروفایل کامل از نتایج جستجو — حتی وقتی اینستا خودش را قفل کرده"""
    uname = uname.lstrip("@").strip().lower()
    for fmt in (f"{uname} instagram", f"instagram {uname}", f'"{uname}"'):
        try:
            items = _bing_rss(fmt, 10)
        except Exception:
            continue
        for it in items:
            m = re.match(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})/?$",
                         it["url"].split("?")[0].rstrip("/"))
            if not m or m.group(1).lower() != uname:
                continue
            name = uname
            tm = re.match(r"^(.*?)\s*\(@", it["title"])
            if tm and tm.group(1).strip():
                name = tm.group(1).strip()
            flw = flg = posts = None
            bio = ""
            fm = re.search(r"([\d.,KM]+)\s*Followers?,\s*([\d.,KM]+)\s*Following?,\s*([\d.,KM]+)\s*Posts",
                           it["desc"], re.I)
            if fm:
                flw, flg, posts = fm.group(1), fm.group(2), fm.group(3)
            bm = re.search(r'on Instagram:\s*\u201c?(.{0,200})', it["desc"])
            if not bm:
                bm = re.search(r'on Instagram:\s*"(.{0,200})', it["desc"])
            if bm:
                bio = bm.group(1).rstrip("\u201d").rstrip('"').strip()
            return {"ok": True, "method": "search", "name": name, "username": uname,
                    "followers": flw, "following": flg, "posts": posts,
                    "private": None, "verified": None, "bio": bio, "pic": None}
    return None


async def profile(uname):
    """اطلاعات پروفایل — چندمسیره"""
    uname = uname.lstrip("@").strip()

    def run():
        last = None
        for fn in (_ig_api_profile, _jina_profile):
            try:
                return fn(uname)
            except Exception as e:
                s = str(e).lower()
                if "not_found" in s or "404" in s:
                    return {"ok": False, "reason": "not_found", "username": uname}
                last = e
        # مسیر طلایی: پروفایل از نتایج جستجو — مستقل از قفل اینستا
        try:
            r = profile_via_search(uname)
            if r:
                return r
        except Exception:
            pass
        return {"ok": False, "reason": "limited", "err": str(last or "")[:120],
                "username": uname}

    return await asyncio.to_thread(run)


async def download_pic(url, dest=None):
    """دانلود مستقیم عکس پروفایل"""
    dest = dest or tempfile.mktemp(prefix="ig_pp_", dir="/tmp") + ".jpg"

    def run():
        req = urllib.request.Request(url, headers={"User-Agent": UA_DESKTOP})
        with urllib.request.urlopen(req, timeout=25) as r, open(dest, "wb") as w:
            w.write(r.read(3_000_000))
        return dest if Path(dest).stat().st_size > 2000 else None
    return await asyncio.to_thread(run)


# ------------------------------------------------ جستجو (از طریق موتور جستجو)

_BING_SKIP = ("/p/", "/reel", "/tv/", "/explore", "/accounts", "/stories",
              "/web/", "about.", "legal.", "developer", "directory", "favicon")


def _bing_urls(query, n=10):
    """لینک‌های نتیجه از Bing — از دیتاسنتر کار می‌کند"""
    import urllib.parse
    import base64
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_DESKTOP, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=15) as r:
        pg = r.read().decode("utf-8", "ignore")
    urls = []
    for m in re.findall(r'u=a1([A-Za-z0-9_-]{20,})', pg):   # لینک‌های کدگذاری‌شده
        try:
            pad = m + "=" * (-len(m) % 4)
            urls.append(base64.urlsafe_b64decode(pad).decode("utf-8", "ignore"))
        except Exception:
            pass
    urls += re.findall(r'href="(https?://[^"]+)"', pg)
    out = []
    for u in urls:
        try:
            p = urllib.parse.urlparse(u)
        except Exception:
            continue
        if p.netloc not in ("instagram.com", "www.instagram.com"):
            continue
        m = re.fullmatch(r"/([A-Za-z0-9_.]{2,30})/?", p.path)
        if m and m.group(1).lower() not in (
                "p", "reel", "reels", "tv", "explore", "instagram",
                "accounts", "stories", "web", "about", "legal", "developer"):
            out.append(m.group(1))
    return list(dict.fromkeys(out))[:n]


# ------------------------------------------------ جستجو از طریق Bing RSS

_IG_RESERVED = ("p", "reel", "reels", "tv", "explore", "accounts", "stories",
                "web", "about", "legal", "developer", "directory", "instagram")


_BING_LAST = [0.0]          # گِیت نرخ: حداقل ۳ ثانیه بین درخواست‌های Bing
_SEARCH_CACHE = {}          # (کوئری) → (ts, نتیجه) — ۱۰ دقیقه


def _bing_gate():
    import time as _t
    wait = 3.0 - (_t.time() - _BING_LAST[0])
    if wait > 0:
        _t.sleep(min(wait, 6))
    _BING_LAST[0] = _t.time()


def _bing_rss(query, n=10):
    """نتایج Bing RSS — پایدار، با عنوان + لینک + توضیحات (تست‌شده از دیتاسنتر)"""
    import time as _t
    import urllib.parse
    import html as _h
    ck = query.lower()
    now = _t.time()
    if ck in _SEARCH_CACHE and now - _SEARCH_CACHE[ck][0] < 600:
        return _SEARCH_CACHE[ck][1]
    _bing_gate()
    url = ("https://www.bing.com/search?q=" + urllib.parse.quote(query)
           + "&format=rss")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_DESKTOP, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=15) as r:
        xml = r.read().decode("utf-8", "ignore")
    out = []
    for m in re.finditer(
            r"<item><title>(.*?)</title><link>(.*?)</link>(?:<description>(.*?)</description>)?",
            xml, re.S):
        title = _h.unescape(m.group(1) or "")
        link = (m.group(2) or "").strip()
        desc = _h.unescape(m.group(3) or "")
        if link.startswith("http"):
            out.append({"title": title, "url": link, "desc": desc})
        if len(out) >= n:
            break
    _SEARCH_CACHE[ck] = (now, out)
    return out


def _ig_users_from_rss(items):
    """یوزرنیم‌های پروفایل از نتایج RSS"""
    users = []
    for it in items:
        m = re.match(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})/?$",
                     it["url"].split("?")[0].rstrip("/"))
        if m and m.group(1).lower() not in _IG_RESERVED:
            users.append(m.group(1))
    return list(dict.fromkeys(users))


def ig_search(query, n=8):
    """جستجوی یوزرنیم اینستا — چندVariant کوئری تا یکی جواب دهد"""
    q = query.strip().lstrip("@").strip()
    if not q:
        return []
    users = []
    for fmt in (f"{q} instagram", f"instagram {q}", f"{q} insta"):
        try:
            users = _ig_users_from_rss(_bing_rss(fmt, 12))
        except Exception:
            users = []
        if users:
            break
    if not users:
        try:
            users = _bing_urls(f"{q} instagram", n)
        except Exception:
            users = []
    return users[:n]


def tag_search(tag, n=8):
    """جستجوی اکانت‌های مرتبط با تگ/موضوع"""
    tag = tag.strip().lstrip("#").strip()
    if not tag:
        return []
    users = []
    for fmt in (f"{tag} instagram", f"instagram {tag}", f"{tag} hashtag"):
        try:
            users = _ig_users_from_rss(_bing_rss(fmt, 12))
        except Exception:
            users = []
        if users:
            break
    return users[:n]


def profile_via_search(uname):
    """پروفایل کامل از نتایج جستجو — حتی وقتی اینستا خودش را قفل کرده"""
    uname = uname.lstrip("@").strip().lower()
    items = _bing_rss(f"{uname} instagram", 10)
    for it in items:
        m = re.match(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})/?$",
                     it["url"].split("?")[0].rstrip("/"))
        if not m or m.group(1).lower() != uname:
            continue
        name = uname
        tm = re.match(r"^(.*?)\s*\(@", it["title"])
        if tm and tm.group(1).strip():
            name = tm.group(1).strip()
        flw = flg = posts = None
        bio = ""
        fm = re.search(r"([\d.,KM]+)\s*Followers?,\s*([\d.,KM]+)\s*Following?,\s*([\d.,KM]+)\s*Posts", it["desc"], re.I)
        if fm:
            flw, flg, posts = fm.group(1), fm.group(2), fm.group(3)
        bm = re.search(r'on Instagram:\s*\u201c?(.{0,200})', it["desc"])
        if not bm:
            bm = re.search(r'on Instagram:\s*"(.{0,200})', it["desc"])
        if bm:
            bio = bm.group(1).rstrip("\u201d").rstrip('"').strip()
        return {"ok": True, "method": "search", "name": name, "username": uname,
                "followers": flw, "following": flg, "posts": posts,
                "private": None, "verified": None, "bio": bio, "pic": None}
    return None


def web_search(q, n=6):
    """جستجوی وب عمومی — عنوان + لینک (برای هوش مصنوعی)"""
    import urllib.parse
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_DESKTOP, "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=15) as r:
        pg = r.read().decode("utf-8", "ignore")
    out = []
    # تکه‌های <li class="b_algo">...</li>
    for blk in re.findall(r'<li class="b_algo".*?</li>', pg, re.S)[:n]:
        t = re.search(r'<h2[^>]*>.*?<a[^>]*>(.*?)</a>', blk, re.S)
        u = re.search(r'<a[^>]+href="(https?://[^"]+)"', blk)
        if t and u:
            title = re.sub(r"<[^>]+>", "", t.group(1)).strip()
            out.append({"title": title[:120], "url": u.group(1)[:300]})
    return out


def fetch_text(url, max_chars=2800):
    """متن یک صفحه‌ی وب — از طریق ریدر یا مستقیم"""
    try:
        req = urllib.request.Request(
            "https://r.jina.ai/" + url, headers={"User-Agent": UA_DESKTOP})
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.read().decode("utf-8", "ignore")[:max_chars]
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA_DESKTOP})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "ignore")
        txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
        txt = re.sub(r"<[^>]+>", " ", txt)
        return re.sub(r"\s+", " ", txt)[:max_chars]
    except Exception as e:
        raise RuntimeError(str(e)[:90])
