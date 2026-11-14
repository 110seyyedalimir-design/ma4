#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎬 ماژول یوتیوب — بازسازی‌کامل v2.2 (yt-2.2)

v2.3: فالبک Piped زودتر می‌آید (بعد از فقط ۲ تلاش سریع مستقیم) — مرحله اطلاعات
     دیگر گیر نمی‌کند: مستقیم ۸ث → android_vr ۸ث → Piped ۲۰ث → بقیه.
v2.2: فالبک سرور واسط Piped + دانلود ترکیبی + استخراج صدا.
"""
import asyncio
import json
import os
import re
import shutil
import time
import tempfile
import urllib.request
from pathlib import Path

VERSION = "yt-2.13"

YTDLP_OPTS_BASE = {
    "quiet": True, "no_warnings": True, "noplaylist": True,
    "socket_timeout": 15, "retries": 1, "nocheckcertificate": True,
}
CLIENT_CHAIN = [None, "android_vr", "tv_embedded", "mweb", "tv", "web_safari", "ios", "android"]
MAX_SIZE = 48 * 1024 * 1024          # سقف فایل تلگرام
DL_CAP = 47 * 1024 * 1024            # سقف دانلود از Piped
COOKIE_FILE = "/tmp/yt_cookies.txt"
BUNDLED_COOKIE = Path(__file__).parent / "cookies.txt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# سرورهای واسط Piped — به‌ترتیب امتحان می‌شوند (تست‌شده از دیتاسنتر)
PIPED_INSTANCES = [
    "api.piped.private.coffee",     # تست‌شده ✅
    "pipedapi.ducks.party",         # تست‌شده ✅
    "pipedapi.reallyaweso.me",
    "pipedapi.leptons.xyz",
    "pipedapi.adminforge.de",
    "pipedapi.orangenet.cc",
    "pipedapi.nosebs.ru",
    "piapi.ggtyler.dev",
    "pipedapi.nikkosphere.com",
]

# vid → (client, use_cookie) — مسیر برنده‌ی هر ویدیو
_WINNERS: dict = {}
_WINNERS_MAX = 300

FFMPEG_OK = False


def _remember_winner(vid, client, use_cookie=False):
    if not vid:
        return
    if len(_WINNERS) > _WINNERS_MAX:
        _WINNERS.pop(next(iter(_WINNERS)), None)
    _WINNERS[vid] = (client, bool(use_cookie))


def _winner(vid):
    """(client, use_cookie) یا None"""
    w = _WINNERS.get(vid)
    return w if isinstance(w, tuple) else None


def ensure_ffmpeg() -> bool:
    """مطمئن شو ffmpeg/ffprobe در PATH هستند"""
    global FFMPEG_OK
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        FFMPEG_OK = True
        return True
    try:
        from static_ffmpeg import add_paths
        add_paths()
    except Exception:
        pass
    FFMPEG_OK = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    return FFMPEG_OK


def _is_guard_error(err) -> bool:
    s = str(err).lower()
    return any(k in s for k in (
        "sign in to confirm", "not a bot", "http error 403", "http error 429",
        "unable to extract", "player error", "page needs to be reloaded",
        "requested format", "empty response", "no video formats",
        "login required", "http error 400", "http error 401"))


def _is_fatal_error(err) -> bool:
    s = str(err).lower()
    return any(k in s for k in (
        "private video", "video unavailable", "removed", "does not exist",
        "age", "confirm your age", "members-only", "premieres", "live event",
        "not available in your country", "copyright"))


YT_RE = re.compile(
    r"https?://(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?|shorts/|embed/|live/)|youtu\.be/)[^\s]+",
    re.IGNORECASE)
VID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})")


def has_cookies() -> bool:
    try:
        if Path(COOKIE_FILE).exists() and Path(COOKIE_FILE).stat().st_size > 50:
            return True
    except OSError:
        pass
    try:
        if BUNDLED_COOKIE.exists() and BUNDLED_COOKIE.stat().st_size > 50:
            import shutil as _sh
            _sh.copy(BUNDLED_COOKIE, COOKIE_FILE)
            return True
    except OSError:
        pass
    return False


def _proxy():
    """پروکسی شخصی کاربر — /setkey proxy http://... یا socks5://..."""
    p = os.environ.get("PROXY_URL", "").strip()
    if not p:
        try:
            with open("/tmp/userkeys.json") as f:
                p = (json.loads(f.read()).get("proxy") or "").strip()
        except Exception:
            p = ""
    if p and not (p.startswith("http") or p.startswith("socks")):
        p = "http://" + p
    return p or None


# ─── پروکسی رایگان خودکار (نجات آخر — بدون کلید و بدون پروکسی شخصی) ───
_FREE_PROXY_CACHE = {"ts": 0.0, "list": [], "good": [], "good_ts": 0.0}


def _free_proxies():
    """لیست پروکسی‌های رایگان عمومی (proxyscrape — بدون کلید)"""
    now = time.time()
    if _FREE_PROXY_CACHE["list"] and now - _FREE_PROXY_CACHE["ts"] < 600:
        return _FREE_PROXY_CACHE["list"]
    try:
        import urllib.request as _ur
        req = _ur.Request(
            "https://api.proxyscrape.com/v2/?request=displayproxies"
            "&protocol=http&timeout=4000&country=all&ssl=all&anonymity=all",
            headers={"User-Agent": UA})
        with _ur.urlopen(req, timeout=20) as r:
            lst = [x.strip() for x in r.read().decode().split() if x.strip()]
        _FREE_PROXY_CACHE.update(ts=now, list=lst[:120])
        return _FREE_PROXY_CACHE["list"]
    except Exception:
        return _FREE_PROXY_CACHE.get("list") or []


def _test_free_proxy(px):
    """تست سریع: این پروکسی به یوتیوب می‌رسد؟"""
    try:
        import urllib.request as _ur
        handler = _ur.ProxyHandler({"http": f"http://{px}", "https": f"http://{px}"})
        opener = _ur.build_opener(handler)
        req = _ur.Request("https://www.youtube.com/generate_204",
                          headers={"User-Agent": UA})
        with opener.open(req, timeout=6) as r:
            return r.status in (200, 204)
    except Exception:
        return False


def _find_free_proxies(n=3):
    """تست موازی پروکسی‌های رایگان → n مورد کارا (کش ۱۰ دقیقه)"""
    import concurrent.futures as _cf
    now = time.time()
    if _FREE_PROXY_CACHE["good"] and now - _FREE_PROXY_CACHE.get("good_ts", 0) < 600:
        return _FREE_PROXY_CACHE["good"]
    lst = _free_proxies()[:40]
    if not lst:
        return []
    good = []
    with _cf.ThreadPoolExecutor(max_workers=15) as ex:
        for px, ok in zip(lst, ex.map(_test_free_proxy, lst)):
            if ok:
                good.append(px)
                if len(good) >= n:
                    break
    _FREE_PROXY_CACHE["good"] = good
    _FREE_PROXY_CACHE["good_ts"] = time.time()
    return good


def _opts(client=None, use_cookie=False):
    opts = dict(YTDLP_OPTS_BASE)
    if client:
        opts["extractor_args"] = {"youtube": {"player_client": [client]}}
    # نکته: کوکی از IP دیتاسنتر گاهی خودش گارد می‌آورد → پیش‌فرض بدون کوکی
    if use_cookie and has_cookies():
        opts["cookiefile"] = COOKIE_FILE
    px = _proxy()
    if px:
        opts["proxy"] = px          # پروکسی شخصی → دور زدن قفل IP
    return opts


def is_yt_url(text: str) -> bool:
    return bool(YT_RE.search(text or ""))


def extract_url(text: str) -> str:
    m = YT_RE.search(text or "")
    return m.group(0).rstrip(").,;]>}") if m else ""


def video_id(url: str) -> str:
    m = VID_RE.search(url or "")
    return m.group(1) if m else ""


def _variant_order(url):
    """ترتیب مسیرها: برنده‌ی قبلی → هر کلاینت اول بدون کوکی بعد با کوکی"""
    vid = video_id(url)
    order = []
    w = _winner(vid)
    if w:
        order.append(w)
    for c in CLIENT_CHAIN:
        for ck in (False, True):
            if (c, ck) not in order:
                order.append((c, ck))
    return order


# ---------------------------------------------------------------- Piped (سرور واسط)

def _piped_streams_sync(vid):
    """گرفتن استریم‌های ترکیبی از سرورهای واسط Piped"""
    last = None
    for inst in PIPED_INSTANCES:
        try:
            req = urllib.request.Request(
                f"https://{inst}/streams/{vid}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            if not d.get("title"):
                continue
            streams = []
            for s in (d.get("videoStreams") or []):
                if s.get("videoOnly") or s.get("mimeType") != "video/mp4":
                    continue
                q = s.get("quality") or ""
                if not re.match(r"^\d+p$", q):    # LBRY/HLS و امثالش نه
                    continue
                streams.append({"quality": q, "h": int(q[:-1]),
                                "url": s["url"]})
            streams.sort(key=lambda x: -x["h"])
            return {"title": d.get("title") or "?",
                    "duration": d.get("duration") or 0,
                    "uploader": d.get("uploader") or "",
                    "streams": streams}
        except Exception as e:
            last = e
    return None


def _piped_download_sync(vid, mode, src_dict=None, progress=None):
    """دانلود از سرور واسط: استریم ترکیبی (ویدیو+صدا) یا استخراج صدا با ffmpeg"""
    d = src_dict or _piped_streams_sync(vid)
    if not d or not d.get("streams"):
        raise RuntimeError("سرورهای واسط هم این ویدیو را ندادند")
    ensure_ffmpeg()

    want_h = 10**6 if mode == "audio" else int(mode)
    # نزدیک‌ترین کیفیت ≤ خواسته (برای صدا: کمترین حجم)
    cands = [s for s in d["streams"] if s["h"] <= want_h] or [d["streams"][-1]]
    pick = max(cands, key=lambda s: s["h"]) if mode != "audio" else min(cands, key=lambda s: s["h"])

    dest = tempfile.mktemp(prefix="yt_piped_", dir="/tmp") + ".mp4"
    if progress is not None:
        progress.update(stage="سرور واسط", done=0,
                        total=0, speed=0)
    req = urllib.request.Request(pick["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as w:
        try:
            clen = int(r.headers.get("Content-Length") or 0)
        except Exception:
            clen = 0
        if progress is not None and clen:
            progress["total"] = clen
        total = 0
        t0 = time.time()
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > DL_CAP:
                w.close()
                Path(dest).unlink(missing_ok=True)
                raise RuntimeError("از سرور واسط هم بزرگ‌تر از سقف آمد")
            w.write(chunk)
            if progress is not None:
                progress["done"] = total
                el = time.time() - t0
                if el > 0.5:
                    progress["speed"] = int(total / el)
    if Path(dest).stat().st_size < 10_000:
        Path(dest).unlink(missing_ok=True)
        raise RuntimeError("فایل سرور واسط ناقص بود")

    info = {"id": vid, "title": d["title"], "duration": d.get("duration") or 0,
            "uploader": d.get("uploader") or "", "_piped": True}

    if mode == "audio":
        if not FFMPEG_OK:
            return dest, info        # خودِ mp4 را بده؛ بد نیست
        out = dest.replace(".mp4", ".m4a")
        import subprocess as _sp
        r_ = _sp.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", dest,
                      "-vn", "-c:a", "aac", "-b:a", "128k", out],
                     capture_output=True, timeout=300)
        Path(dest).unlink(missing_ok=True)
        if r_.returncode == 0 and Path(out).exists():
            return out, info
        return dest, info
    return dest, info


async def piped_streams(vid):
    return await asyncio.to_thread(_piped_streams_sync, vid)


async def piped_download(vid, mode, progress=None):
    return await asyncio.to_thread(_piped_download_sync, vid, mode, None, progress)


# ---------------------------------------------------------------- جستجو

async def search(query: str, n=6):
    """جستجو — مسیر سرچ تقریباً همیشه باز است"""
    import yt_dlp

    def run(client):
        opts = _opts(client)
        opts["extract_flat"] = "in_playlist"
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"ytsearch{n}:{query}", download=False)
        out = []
        for e in (info.get("entries") or []):
            if e and e.get("id"):
                out.append({"id": e["id"], "title": (e.get("title") or "?")[:60],
                            "dur": e.get("duration") or 0})
        return out

    last = None
    for c in CLIENT_CHAIN[:4]:
        try:
            return await asyncio.wait_for(asyncio.to_thread(run, c), timeout=20)
        except Exception as e:
            last = e
    raise last


# ---------------------------------------------------------------- اطلاعات

async def fetch_info(url: str, progress=None):
    """اطلاعات ویدیو — مسابقه‌ی موازی:
    مستقیم (۸ث) و سرور واسط Piped (۲۰ث) همزمان اجرا می‌شوند؛ اولین موفق برنده است.
    دیگر هیچ‌وقت پشت زنجیره‌ی تلاش گیر نمی‌کند."""
    import yt_dlp

    def run(client, use_cookie):
        opts = _opts(client, use_cookie)
        with yt_dlp.YoutubeDL(opts) as y:
            return y.extract_info(url, download=False)

    vid = video_id(url)
    last = None

    async def _direct():
        nonlocal last
        for i, (c, ck) in enumerate(((None, False), ("android_vr", False)), 1):
            if progress:
                progress(f"مسیر مستقیم {i} از ۲")
            try:
                info = await asyncio.wait_for(asyncio.to_thread(run, c, ck), timeout=6)
                if info:
                    return info
            except Exception as e:
                last = e
                if _is_fatal_error(e):
                    raise
        return None

    async def _piped():
        if not vid:
            return None
        if progress:
            progress("سرور واسط")
        try:
            return await asyncio.wait_for(piped_streams(vid), timeout=14)
        except Exception:
            return None

    d_task = asyncio.ensure_future(_direct())
    p_task = asyncio.ensure_future(_piped())
    try:
        done, _pending = await asyncio.wait(
            [d_task, p_task], return_when=asyncio.FIRST_COMPLETED,
            timeout=18)
        # اگر مستقیم موفق بود → بهترین کیفیت اطلاعات
        if d_task in done and not d_task.cancelled():
            try:
                info = d_task.result()
                if info:
                    _remember_winner(info.get("id") or vid, None, False)
                    p_task.cancel()
                    return info
            except Exception as e:
                if _is_fatal_error(e):
                    p_task.cancel()
                    raise
        # سرور واسط جواب داد؟
        if p_task in done and not p_task.cancelled():
            d = p_task.result()
            if d:
                if not (d_task in done and not d_task.cancelled()
                        and d_task.exception() is None and d_task.result()):
                    d_task.cancel()
                _remember_winner(vid, "piped", False)
                return {"id": vid, "title": d["title"], "duration": d["duration"],
                        "uploader": d.get("uploader") or "", "_piped": True,
                        "formats": []}
        # هنوز هیچ‌کدام تمام نشده؟ صبر کوتاه روی مستقیم
        try:
            info = await asyncio.wait_for(asyncio.shield(d_task), timeout=4)
            if info:
                _remember_winner(info.get("id") or vid, None, False)
                p_task.cancel()
                return info
        except Exception as e:
            last = e or last
        p_task.cancel()
        # ممکن است piped هنوز در جریان باشد — نتیجه‌اش را بگیر
        try:
            d = await asyncio.wait_for(asyncio.shield(p_task), timeout=12)
            if d:
                _remember_winner(vid, "piped", False)
                return {"id": vid, "title": d["title"], "duration": d["duration"],
                        "uploader": d.get("uploader") or "", "_piped": True,
                        "formats": []}
        except Exception:
            pass
    finally:
        for t_ in (d_task, p_task):
            try:
                t_.cancel()
            except Exception:
                pass

    # مرحله‌ی بعد: بقیه‌ی کلاینت‌ها (کوتاه) — بدون فالبک جستجو
    # (فالبک جستجو حذف شد: ویدیوی غلط برمی‌گرداند و ۱۰۰+ ثانیه گیر می‌کرد)
    for i, (c, ck) in enumerate((("tv_embedded", False), (None, True), ("mweb", False)), 1):
        if progress:
            progress(f"کلاینت جایگزین {i} از ۳")
        try:
            info = await asyncio.wait_for(asyncio.to_thread(run, c, ck), timeout=6)
            if info:
                _remember_winner(info.get("id") or vid, c, ck)
                return info
        except Exception as e:
            last = e
            if _is_fatal_error(e):
                raise
            continue
    raise last


async def fetch_info_via_search(hint: str, n=6):
    """جستجو بر اساس آیدی/عنوان → اطلاعات ویدیوی پیدا شده"""
    import yt_dlp
    vid = video_id(hint)
    query = vid or hint[:60]

    def run_search():
        opts = dict(YTDLP_OPTS_BASE)
        opts["extract_flat"] = "in_playlist"
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"ytsearch{n}:{query}", download=False)
        for e in (info.get("entries") or []):
            if e and e.get("id"):
                return e["id"]
        return None

    found = await asyncio.wait_for(asyncio.to_thread(run_search), timeout=25)
    if not found:
        return None

    def run_fetch(client, use_cookie):
        opts = _opts(client, use_cookie)
        with yt_dlp.YoutubeDL(opts) as y:
            return y.extract_info(f"https://www.youtube.com/watch?v={found}", download=False)

    for c, ck in ((None, False), (None, True), ("android_vr", False), ("tv_embedded", False)):
        try:
            info = await asyncio.wait_for(asyncio.to_thread(run_fetch, c, ck), timeout=15)
            if info:
                _remember_winner(info.get("id") or found, c, ck)
                return info
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- دانلود

def _format_chain(mode: str):
    """زنجیره‌ی فرمت‌ها — انعطاف‌پذیر:
    بدون قید ext=mp4 روی ادغام (ffmpeg خودش خروجی mp4 می‌دهد)
    + فرمت ۱۸ کلاسیک (۳۶۰p تک‌فایلی) به‌عنوان نجات‌دهنده"""
    if mode == "audio":
        return ["bestaudio/best", "best"]
    h = int(mode)
    return [
        f"bestvideo[height<={h}]+bestaudio"
        f"/best[height<={h}][ext=mp4]/best[height<={h}]/best",
        f"18/best[height<={h}]/best",
        "best",
    ]


async def download(url, mode="480", progress=None):
    """دانلود — ترتیبی و شفاف:
    هر تلاش در progress ثبت می‌شود (شماره تلاش، کل تلاش‌ها، نام مرحله، بایت‌ها)
    → کاربر می‌بیند چند درصد مسیر رفته و چی مانده"""
    ensure_ffmpeg()
    vid = video_id(url)
    fmts = _format_chain(mode)
    prog = progress if progress is not None else {}
    prog.setdefault("attempt", 0)
    prog.setdefault("attempts_total", 0)
    prog.setdefault("stage", "آماده‌سازی")
    prog.setdefault("done", 0)
    prog.setdefault("total", 0)
    prog.setdefault("speed", 0)

    def _begin(stage):
        prog["attempt"] = int(prog.get("attempt") or 0) + 1
        prog["stage"] = stage
        prog["done"] = 0
        prog["total"] = 0
        prog["speed"] = 0

    def run(client, use_cookie, fmt):
        import yt_dlp
        opts = _opts(client, use_cookie)
        prefix = tempfile.mktemp(prefix="yt_", dir="/tmp")
        opts["outtmpl"] = prefix + ".%(ext)s"
        opts["format"] = fmt
        if mode == "audio" and FFMPEG_OK:
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                       "preferredcodec": "m4a"}]
        elif mode != "audio" and FFMPEG_OK:
            opts["merge_output_format"] = "mp4"
        if progress is not None:
            def _hook(d):
                try:
                    if d.get("status") == "downloading":
                        prog["done"] = d.get("downloaded_bytes") or 0
                        prog["total"] = (d.get("total_bytes")
                                         or d.get("total_bytes_estimate") or 0)
                        prog["speed"] = d.get("speed") or 0
                    elif d.get("status") == "finished":
                        prog["stage"] = "پرداخت نهایی (ادغام/صدا)"
                except Exception:
                    pass
            opts["progress_hooks"] = [_hook]
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=True)
        files = [d["filepath"] for d in info.get("requested_downloads", [])
                 if d.get("filepath")]
        if not files:
            files = [str(p) for p in Path("/tmp").glob(Path(prefix).name + ".*")]
        return (files[0] if files else None), info

    w = _winner(vid)
    fast_order = []
    if w and w[0] != "piped":
        fast_order.append(w)
    for x in ((None, False), ("android_vr", False), ("tv_embedded", False)):
        if x not in fast_order:
            fast_order.append(x)
    full_order = [x for x in _variant_order(url) if x[0] != "piped"][:3]

    # ترتیب تلاش‌ها: مسیر برنده → مستقیم‌های سریع → سرور واسط → فرمت‌های جایگزین
    plan = []          # (نام مرحله، تابع async)
    if w and w[0] == "piped" and vid:
        plan.append("سرور واسط (مسیر برنده)")
    for i, (c, ck) in enumerate(fast_order, 1):
        plan.append(f"مسیر مستقیم {i} از {len(fast_order)}")
    if vid:
        plan.append("سرور واسط")
    for fi, fmt in enumerate(fmts[1:], 2):
        for ci, (c, ck) in enumerate(full_order, 1):
            plan.append(f"فرمت جایگزین {fi} — کلاینت {ci}")
    prog["attempts_total"] = len(plan)

    last = None
    idx = -1

    async def _try_piped(label):
        _begin(label)
        try:
            return await asyncio.wait_for(piped_download(vid, mode, prog), timeout=240)
        except Exception as e:
            return ("ERR", e)

    async def _try_direct(label, c, ck, fmt):
        _begin(label)
        try:
            path, info = await asyncio.wait_for(
                asyncio.to_thread(run, c, ck, fmt), timeout=240)
            if path:
                return (path, info)
            return None
        except asyncio.TimeoutError:
            return ("ERR", TimeoutError("زمان تمام شد"))
        except Exception as e:
            if _is_fatal_error(e):
                raise
            return ("ERR", e)

    for label in plan:
        idx += 1
        # آیا این تلاش مستقیم است یا piped؟
        is_piped = label.startswith("سرور واسط")
        if is_piped:
            r = await _try_piped(label)
            if isinstance(r, tuple) and len(r) == 2 and r[0] == "ERR":
                last = r[1]
                continue
            if r:
                path, info = r
                _remember_winner(vid, "piped", False)
                return path, info
            last = RuntimeError("سرور واسط فایل نداد")
            continue
        # مستقیم: کلاینت را از متن مرحله پیدا کن — ساده‌تر: بازسازی ترتیب
        # (ترتیب plan و ترتیب ساخته‌شده یکسان‌اند)
        if idx == 0 and w and w[0] == "piped":
            continue   # جای مسیر برنده‌ی piped — بالا هندل شد
        # محاسبه‌ی (c, ck, fmt) بر اساس idx
        offset = 1 if (w and w[0] == "piped" and vid) else 0
        if idx < offset + len(fast_order):
            c, ck = fast_order[idx - offset]
            fmt = fmts[0]
        else:
            j = idx - offset - len(fast_order)
            if vid:
                j -= 1          # جای piped
            fmt_i, c_i = divmod(j, max(len(full_order), 1))
            fmt = fmts[1 + fmt_i]
            c, ck = full_order[c_i]
        r = await _try_direct(label, c, ck, fmt)
        if isinstance(r, tuple) and r[0] == "ERR":
            last = r[1]
            continue
        if r:
            path, info = r
            _remember_winner((info or {}).get("id") or vid, c, ck)
            return path, info
        last = last or RuntimeError("فایلی دانلود نشد")

    # 🆕 مرحله‌ی نجات: پروکسی رایگان خودکار
    # (وقتی IP سرور قفل است و پروکسی شخصی هم نداریم)
    try:
        if progress is not None:
            progress["stage"] = "جستجوی پروکسی رایگان"
            progress["attempt"] = int(progress.get("attempt") or 0) + 1
        proxies = await asyncio.to_thread(_find_free_proxies, 3)
        for i, px in enumerate(proxies, 1):
            if progress is not None:
                progress["stage"] = f"پروکسی رایگان {i} از {len(proxies)}"
            try:
                def run_px():
                    import yt_dlp as _ydl
                    opts = dict(YTDLP_OPTS_BASE)
                    opts["proxy"] = f"http://{px}"
                    prefix = tempfile.mktemp(prefix="yt_px_", dir="/tmp")
                    opts["outtmpl"] = prefix + ".%(ext)s"
                    opts["format"] = fmts[0]
                    if mode != "audio" and FFMPEG_OK:
                        opts["merge_output_format"] = "mp4"
                    if mode == "audio" and FFMPEG_OK:
                        opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                                   "preferredcodec": "m4a"}]
                    if progress is not None:
                        def _hook(d):
                            try:
                                if d.get("status") == "downloading":
                                    progress["done"] = d.get("downloaded_bytes") or 0
                                    progress["total"] = (d.get("total_bytes")
                                                         or d.get("total_bytes_estimate") or 0)
                                    progress["speed"] = d.get("speed") or 0
                            except Exception:
                                pass
                        opts["progress_hooks"] = [_hook]
                    with _ydl.YoutubeDL(opts) as y:
                        info = y.extract_info(url, download=True)
                    files = [d["filepath"] for d in info.get("requested_downloads", [])
                             if d.get("filepath")]
                    if not files:
                        files = [str(p) for p in Path("/tmp").glob(Path(prefix).name + ".*")]
                    return (files[0] if files else None), info
                path, info = await asyncio.wait_for(
                    asyncio.to_thread(run_px), timeout=300)
                if path:
                    return path, info
            except Exception:
                continue
    except Exception:
        pass
    raise last or RuntimeError("دانلود ناموفق بود")
