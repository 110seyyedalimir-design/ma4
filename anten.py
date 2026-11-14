#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
📡 بخش «آنتن» — کانفیگ‌های V2Ray و پروکسی تلگرام

- کانفیگ‌های V2Ray: از منابع عمومی گیت‌هاب (خودکار، هر ۶ ساعت تازه می‌شوند)
- پروکسی تلگرام: مدیر لینک t.me/proxy اضافه می‌کند (تحت کنترل خود مدیر)
- پیش از ارسال، سلامت هر سرور با اتصال واقعی TCP بررسی می‌شود — فقط سالم‌ها ارسال می‌شوند
- رأیگیری 👍👎 برای هر آیتم + پیشنهاد مشابه بهتر پس از رأی منفی
"""

import asyncio
import base64
import hashlib
import json
import logging
import random
import re
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("anten")

VOTE_FILE = Path("/tmp/anten_votes.json")
CUSTOM_FILE = Path("/tmp/anten_custom.json")

# کانفیگ‌ها فقط از کانال‌های معرفی‌شده‌ی مدیر (از طریق اکانت‌خوان) می‌آیند —
# هیچ منبع خارجی دیگری استفاده نمی‌شود.
SOURCES_V2 = []

MTProto_RE = re.compile(
    r"(?:https?://)?t\.me/proxy\?server=([^&\s]+)&port=(\d+)&secret=([^&\s]+)")
MTProto_CHANNELS = ["ProxyMTProto", "V2rayBaaz", "Kamkarstore", "ProxyHagh"]
VLESS_RE = re.compile(r"^(vless|trojan)://[^@]+@([^@:/]+):(\d+)")


def fetch_mtproto(max_keep=15):
    """واکشی پروکسی‌های تلگرام از کانال‌های متنی باز"""
    found = []
    chans = list(MTProto_CHANNELS)
    try:   # کانال‌های اضافه‌شده توسط مدیر
        chans += json.loads(Path("/tmp/extra_sources.json").read_text())
    except Exception:
        pass
    for ch in chans:
        try:
            req = urllib.request.Request(
                f"https://t.me/s/{ch}", headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
            import html as _h
            raw = _h.unescape(raw)
            seen = set()
            for s_, p_, sec_ in MTProto_RE.findall(raw):
                if (s_, p_) in seen:
                    continue
                seen.add((s_, p_))
                link = f"https://t.me/proxy?server={s_}&port={p_}&secret={sec_}"
                found.append({"link": link, "host": s_, "port": int(p_),
                              "h8": _h8(link), "auto": True, "src": "@" + ch})
        except Exception as e:
            log.warning("anten: کانال پروکسی ناموفق %s → %s", ch, str(e)[:60])
    if not found:
        return 0
    # دستی‌ها می‌مانند + تازه‌ها؛ رأیِ خیلی منفیِ خودکار حذف می‌شود
    manual = [e for e in custom["mtproto"] if not e.get("auto")]
    auto_old = [e for e in custom["mtproto"] if e.get("auto") and score(e.get("h8", "")) >= -1]
    by_host = {e["host"] + str(e["port"]): e for e in manual + auto_old}
    for f_ in found:
        k = f_["host"] + str(f_["port"])
        if k not in by_host:
            by_host[k] = f_
    all_e = list(by_host.values())
    # اول دستی‌ها، بعد خودکارهای محبوب‌تر
    manual_n = [e for e in all_e if not e.get("auto")]
    auto_n = sorted([e for e in all_e if e.get("auto")], key=lambda x: -score(x.get("h8", "")))
    custom["mtproto"] = (manual_n + auto_n)[:max(12, len(manual_n) + max_keep)]
    save_custom()
    log.info("anten: %d پروکسی تلگرام تازه شد", len(custom["mtproto"]))
    return len(custom["mtproto"])


on_need_refresh = None   # bot تنظیم می‌کند: async () → (px, cfg)


def _h8(link: str) -> str:
    return hashlib.md5(link.encode()).hexdigest()[:8]


# ---------------------------------------------------------------- ذخیره‌سازی

def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False))
    except OSError:
        pass
    try:
        import persist
        persist.mark_dirty()
    except Exception:
        pass


votes = load_json(VOTE_FILE, {})          # h8 → {"up": n, "dn": n, "voted": [chat_id]}
for _k, _v in votes.items():            # مهاجرت ساختار قدیمی
    if isinstance(_v, dict):
        _v.setdefault("voted", [])
custom = load_json(CUSTOM_FILE, {"mtproto": [], "v2": []})
cache = {"fetched_at": 0, "links": []}


def save_votes():
    save_json(VOTE_FILE, votes)


def save_custom():
    save_json(CUSTOM_FILE, custom)


def touch_fetched(entries):
    """ثبت زمان دریافت"""
    now = time.time()
    for e in entries:
        e["fetched"] = now


# ---------------------------------------------------------------- تجزیه

def parse_v2link(l: str):
    """vmess/vless/trojan → {link, proto, host, port, remark} یا None"""
    l = l.strip()
    try:
        if l.startswith("vmess://"):
            pad = l[8:] + "=" * (-len(l[8:]) % 4)
            d = json.loads(base64.b64decode(pad).decode("utf-8", "ignore"))
            host, port = d.get("add"), int(d.get("port", 0))
            if not host or not port:
                return None
            return {"link": l, "proto": "vmess", "host": host, "port": port,
                    "remark": (d.get("ps") or d.get("remark") or "")[:40]}
        m = VLESS_RE.match(l)
        if m:
            return {"link": l, "proto": m.group(1), "host": m.group(2),
                    "port": int(m.group(3)), "remark": ""}
        if l.startswith("ss://"):
            m2 = re.match(r"^ss://[^@]+@([^@:/]+):(\d+)", l)
            if m2:
                return {"link": l, "proto": "ss", "host": m2.group(1),
                        "port": int(m2.group(2)), "remark": ""}
    except Exception:
        return None
    return None


def fetch_v2(limit=900, quick=False):
    """واکشی منابع عمومی → فهرست کانفیگ‌ها (quick=True فقط منابع سبک و سریع)"""
    links, seen = [], set()
    for url in SOURCES_V2[:2] if quick else SOURCES_V2:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith(("vmess://", "vless://", "trojan://", "ss://")):
                    continue
                p = parse_v2link(line)
                if not p:
                    continue
                key = p["host"] + str(p["port"])
                if key in seen:
                    continue
                seen.add(key)
                p["h8"] = _h8(line)
                links.append(p)
        except Exception as e:
            log.warning("anten: منبع ناموفق %s → %s", url.split("/")[2], str(e)[:60])
    random.shuffle(links)
    cache["links"] = links[:limit]
    cache["fetched_at"] = time.time()
    log.info("anten: %d کانفیگ از منابع عمومی", len(cache["links"]))
    return len(cache["links"])


# ---------------------------------------------------------------- سلامت

async def _check_one(host, port, timeout=4.0):
    try:
        fut = asyncio.open_connection(host, port)
        r, w = await asyncio.wait_for(fut, timeout)
        w.close()
        return True
    except Exception:
        return False


async def alive_filter(entries, needed=4, tries=28):
    """از بین کاندیدها، {needed} موردِ واقعاً وصل‌شو را برمی‌گرداند"""
    out = []
    batch = entries[:tries]
    for i in range(0, len(batch), 8):
        chunk = batch[i:i + 8]
        res = await asyncio.gather(*[_check_one(e["host"], e["port"]) for e in chunk])
        out.extend(e for e, ok in zip(chunk, res) if ok)
        if len(out) >= needed:
            break
    return out[:needed]


# ---------------------------------------------------------------- رتبه و رأی

def score(h8):
    v = votes.get(h8, {})
    return v.get("up", 0) - v.get("dn", 0)


def rank(entries):
    return sorted(entries, key=lambda e: -score(e.get("h8", "")))


def vote(h8, up=True, chat_id=None):
    v = votes.setdefault(h8, {"up": 0, "dn": 0, "voted": []})
    v["up" if up else "dn"] += 1
    if chat_id is not None and chat_id not in v["voted"]:
        v["voted"].append(chat_id)
    save_votes()


def not_voted_by(entries, chat_id):
    """مواردی که این کاربر هنوز رویشان رأی نداده — برای تنوع هر بار"""
    return [e for e in entries
            if chat_id not in votes.get(e.get("h8", ""), {}).get("voted", [])]


def reset_all():
    """پاک‌سازی کامل پروکسی‌ها، کانفیگ‌ها و رأی‌ها"""
    custom["mtproto"] = []
    custom["v2"] = []
    votes.clear()
    save_custom()
    save_votes()


def similar_bad(h8, proto, pool, n=2):
    """بعد از رأی منفی: مشابه‌های بهترِ همان پروتکل"""
    others = [e for e in pool if e.get("h8") != h8]
    return rank(others)[:n]


# ---------------------------------------------------------------- افزودن دستی مدیر

def add_mtproto(text: str):
    """لینک t.me/proxy?... → ثبت"""
    m = MTProto_RE.search(text or "")
    if not m:
        return None
    server, port, secret = m.group(1), int(m.group(2)), m.group(3)
    link = f"https://t.me/proxy?server={server}&port={port}&secret={secret}"
    for e in custom["mtproto"]:
        if e["host"] == server and e["port"] == port:
            return e
    e = {"link": link, "host": server, "port": port,
         "h8": _h8(link), "added": time.time()}
    custom["mtproto"].append(e)
    save_custom()
    return e


# ---------------------------------------------------------------- ارائه

def _ts_label(ts):
    """زمان نسبی فارسی"""
    if not ts:
        return "?"
    d = time.time() - ts
    if d < 3600:
        return f"{int(d // 60)} \u062f\u0642\u06cc\u0642\u0647 \u067e\u06cc\u0634"
    if d < 86400:
        return f"{int(d // 3600)} \u0633\u0627\u0639\u062a \u067e\u06cc\u0634"
    return f"{int(d // 86400)} \u0631\u0648\u0632 \u067e\u06cc\u0634"


def _admin_note(entry, chat_id) -> str:
    """\u0632\u06cc\u0631\u0646\u0648\u06cc\u0633 \u0645\u0646\u0628\u0639 + \u0632\u0645\u0627\u0646\u200c\u0647\u0627 \u2014 \u0641\u0642\u0637 \u0628\u0631\u0627\u06cc \u0627\u062f\u0645\u06cc\u0646"""
    import os as _os
    admins = {int(x) for x in _os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",")
              if x.strip().isdigit()}
    if int(chat_id) not in admins:
        return ""
    parts = []
    if entry.get("src"):
        parts.append(f"\U0001f4f6 \u06a9\u0627\u0646\u0627\u0644: {entry['src']}")
    if entry.get("posted"):
        parts.append(f"\U0001f4e8 \u067e\u0633\u062a: {_ts_label(entry['posted'])}")
    if entry.get("fetched"):
        parts.append(f"\U0001f504 \u06af\u0631\u0641\u062a\u0645: {_ts_label(entry['fetched'])}")
    if not parts:
        return ""
    return "\n" + " | ".join(parts)



async def serve_v2(client, chat_id, count=3):
    import random as _r
    # فقط منبع: کانال‌های مدیر (توسط اکانت‌خوان جمع شده‌اند)
    pool = list(custom["v2"])
    if not pool and on_need_refresh:
        # مخزن خالی → همین لحظه از کانال‌ها بریز
        try:
            await client.send_message(chat_id, "⏳ دارم از کانال‌ها کانفیگ جمع می‌کنم…")
            await on_need_refresh()
            pool = list(custom["v2"])
        except Exception:
            pass
    if not pool:
        await client.send_message(
            chat_id,
            "🛰 فعلاً کانفیگی در مخزن نیست و کانال‌های منبع هم چیز تازه‌ای نداشتن.\n"
            "به‌محض پست جدید خودکار پر می‌شه — کمی بعد دوباره امتحان کن.")
        return
    entries = not_voted_by(pool, chat_id) or pool
    _r.shuffle(entries)
    if not entries:
        await client.send_message(chat_id, "هنوز کانفیگی بارگذاری نشده — چند لحظه بعد دوباره امتحان کن.")
        return
    alive = await alive_filter(entries, needed=count)
    if not alive:
        await client.send_message(chat_id, "😖 الان هیچ‌کدوم از کانفیگ‌های تست‌شده وصل نشدن.\n"
                                            "چند دقیقه بعد دوباره بزن — هر بار مجموعه‌ی جدیدی تست می‌شه.")
        return
    for e in alive:
        try:
            remark = f" | 🏷 {e['remark']}" if e.get("remark") else ""
        except Exception:
            remark = ""
        msg = (f"🛰 کانفیگ {e['proto'].upper()} — تست‌شده و وصل‌شو ✅\n"
               f"📍 {e['host']}:{e['port']}{remark}{_admin_note(e, chat_id)}\n\n"
               f"```\n{e['link']}\n```")
        await client.send_message(
            chat_id, msg,
            buttons=[[Inline("👍 خوب بود", f"t:up:{e['h8']}"),
                      Inline("👎 وصل نشد", f"t:dn:{e['h8']}")]])
    await client.send_message(
        chat_id, "💡 کانفیگ رو کپی کن و توی اپ v2rayNG / Hiddify وارد کن.\n"
                 "⚠️ کانفیگ‌ها از کانال‌های منبع جمع می‌شوند و سالم‌شان تست شده؛ برای کارهای خیلی حساس سرویس شخصی مطمئن‌تره.")


async def serve_mtproto(client, chat_id, count=4):
    if not custom["mtproto"]:
        await client.send_message(
            chat_id, "🔗 پروکسی تلگرام فعلاً خالیه.\n"
                     "مدیر محترم: لینک t.me/proxy رو برام بفرستید تا برای همه اضافه بشه.")
        return
    if not custom["mtproto"] or time.time() - custom.get("mt_fetched", 0) > 3600:
        try:
            await asyncio.to_thread(fetch_mtproto)
            custom["mt_fetched"] = time.time()
            save_custom()
        except Exception as e:
            log.warning("anten: واکشی پروکسی در لحظه ناموفق: %s", str(e)[:60])
    if not custom["mtproto"] and on_need_refresh:
        try:
            await client.send_message(chat_id, "⏳ دارم از کانال‌ها پروکسی جمع می‌کنم…")
            await on_need_refresh()
        except Exception:
            pass
    import random as _r
    pool = not_voted_by(rank(custom["mtproto"]), chat_id) or custom["mtproto"]
    _r.shuffle(pool)
    alive = await alive_filter(pool, needed=count)
    if not alive:
        await client.send_message(chat_id, "😖 پروکسی‌های تلگرام الان جواب نمی‌دن؛ بعداً امتحان کن.")
        return
    from telethon import Button
    for i, e in enumerate(alive, 1):
        await client.send_message(
            chat_id, f"🔗 پروکسی تلگرام {i} — تست‌شده ✅\n"
                     f"📍 {e['host']}:{e['port']}{_admin_note(e, chat_id)}",
            buttons=[
                [Button.url("⚡ اتصال با یک ضربه", e["link"])],
                [Inline("👍", f"t:up:{e['h8']}"), Inline("👎", f"t:dn:{e['h8']}")],
            ])


def Inline(text, data):
    from telethon import Button
    if isinstance(data, str):
        data = data.encode()
    return Button.inline(text, data)


# ---------------------------------------------------------------- حلقه‌ی تازه‌سازی

async def refresh_loop():
    """فقط پروکسی‌های تلگرام از کانال‌های معرفی‌شده؛ کانفیگ‌ها با اکانت‌خوان می‌آیند"""
    while True:
        try:
            await asyncio.to_thread(fetch_mtproto)
        except Exception:
            log.exception("anten: خطای تازه‌سازی")
        await asyncio.sleep(2 * 3600)
