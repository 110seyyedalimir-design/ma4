#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
حافظه‌ی پشتیبان تلگرامی — داده‌های حیاتی (منطقه‌ها + کاربران) در یک پیام
خودکار در چت مدیر ذخیره و ویرایش می‌شود؛ حتی اگر اسپیس از نو ساخته شود،
داده‌ها از تلگرام برمی‌گردند. (پشتیبانِ persist.py)
"""

import asyncio
import json
from pathlib import Path

MARKER = "🗂 داده‌های ربات — خودکار، دست نزنید"
# برچسب هر بخش — برای حذف/شناخت دستی مدیر
SECTION_INFO = {
    "bargha_regions.json": "📍 منطقه‌های کاربران (برق کاشان)",
    "bargha_state.json": "⚡ اطلاعیه‌های برق + وضعیت رصد",
    "comp_users.json": "👥 لیست کاربران",
    "comp_history.json": "🗜 تاریخچه فشرده‌سازی",
    "anten_votes.json": "🗳 رأی‌های آنتن",
    "anten_custom.json": "📡 پروکسی/کانفیگ‌های کانال‌ها",
    "user_session.json": "👁 نشست اکانت‌خوان (قفل با لاگین)",
    "tgstore_msgid.json": "🧭 شناسه‌ی پیام حافظه",
    "tgstore_group_msgid.json": "🧭 شناسه‌ی پیام گروه",
    "group_cfg.json": "🏛 تنظیمات بخش‌های گروه مای‌ربات",
    "sd_sessions.json": "📸 نشست کاربران عکس خودمحو",
    "sd_settings.json": "⚙️ حالت دریافت مدیا",
    "extra_sources.json": "📡 کانال‌های منبع اضافه",
    "subs.json": "🔑 اشتراک‌ها و کلیدها و قفل‌ها",
    "yt_cookie.json": "🔑 کوکی یوتیوب (ضد بلاک)",
    "support_inbox.json": "💬 پیام‌های پشتیبانی",
    "subs.json": "🔑 اشتراک‌ها و کلیدها و قفل‌ها",
}
FILES = ["bargha_regions.json", "comp_users.json", "user_session.json",
         "tgstore_msgid.json", "tgstore_group_msgid.json", "group_cfg.json", "sd_sessions.json", "sd_settings.json", "extra_sources.json", "subs.json", "yt_cookie.json", "support_inbox.json"]
GROUP_MSGID_FILE = Path("/tmp/tgstore_group_msgid.json")

_state = {"client": None, "admin": None, "msg": None, "dirty": False,
          "group": None, "group_msg": None, "restored_from": "—"}


def set_group_target(chat_id, topic_id):
    """مقصد گروه (بخش ذخیره) — با /setbackup تنظیم می‌شود"""
    _state["group"] = {"chat": int(chat_id), "topic": int(topic_id)}
    mid = None
    try:
        mid = json.loads(GROUP_MSGID_FILE.read_text()).get("id")
    except Exception:
        pass
    _state["group_msg"] = {"id": mid} if mid else None


def _save_group_msgid(mid):
    try:
        GROUP_MSGID_FILE.write_text(json.dumps({"id": mid}))
    except OSError:
        pass
    mark_dirty()


def available() -> bool:
    return _state["client"] is not None


def export_payload() -> str:
    """نسخه‌ی پشتیبان با راهنمای بخش‌ها — مدیر بتواند دستی بخشی را حذف کند"""
    guide = "\n".join(f"{v} ⟵ کلید: {k}" for k, v in SECTION_INFO.items()
                      if k != "user_session.json")   # نشست در راهنما نیست (امنیت)
    return (MARKER + "\n"
            "📋 راهنمای بخش‌ها (برای حذف دستی، خط DATA را نگه دار و فقط بخشی را که "
            "می‌خواهی در JSON حذف کن):\n" + guide +
            "\nDATA|" + _snapshot())


def import_payload(text: str) -> int:
    """بازگردانی از پیام پشتیبان (ریپلای /restore)"""
    if "DATA|" not in text:
        return 0
    return _restore(text.split("DATA|", 1)[1])


def stats() -> dict:
    """آمار حافظه برای گزارش مدیر"""
    out = {"msg_found": _state["msg"] is not None, "regions": 0, "users": 0,
           "restored_from": _state.get("restored_from", "—"),
           "group": bool(_state.get("group"))}
    for f, key in (("bargha_regions.json", "regions"), ("comp_users.json", "users")):
        try:
            d = json.loads(Path(f"/tmp/{f}").read_text())
            out[key] = len(d)
        except Exception:
            pass
    return out


def mark_dirty():
    _state["dirty"] = True


def _snapshot() -> str:
    data = {}
    for f in FILES:
        p = Path(f"/tmp/{f}")
        try:
            data[f] = json.loads(p.read_text()) if p.exists() else None
        except Exception:
            data[f] = None
    # محافظ طول پیام تلگرام (۴۰۹۶): اول کاربرها سبک می‌شوند
    s = json.dumps(data, ensure_ascii=False)
    if len(s) > 3800 and data.get("comp_users.json"):
        slim = {k: {"name": v.get("name"), "uname": v.get("uname"), "jobs": v.get("jobs", 0)}
                for k, v in list(data["comp_users.json"].items())[:250]}
        data["comp_users.json"] = slim
        s = json.dumps(data, ensure_ascii=False)
    if len(s) > 3800 and data.get("comp_users.json"):
        data["comp_users.json"] = {}
        s = json.dumps(data, ensure_ascii=False)   # منطقه‌ها اولویت‌اند
    return s


def _restore(payload: str, source="چت مدیر"):
    """بازیابی فایل‌های محلی از پیام تلگرام (فقط اگر محلی خالی/گم باشد)"""
    data = json.loads(payload)
    restored = 0
    _state["restored_from"] = source
    for f, v in data.items():
        if v is None:
            continue
        p = Path(f"/tmp/{f}")
        need = True
        try:
            if p.exists() and json.loads(p.read_text() or "null"):
                need = False   # نسخه‌ی محلی تازه‌تر است
        except Exception:
            pass
        if need:
            p.write_text(json.dumps(v, ensure_ascii=False))
            restored += 1
    return restored


MSGID_FILE = Path("/tmp/tgstore_msgid.json")


def _save_msgid(mid):
    try:
        MSGID_FILE.write_text(json.dumps({"id": mid}))
    except OSError:
        pass
    mark_dirty()


async def init(client, admin_ids, reader=None):
    """پیدا کردن/ساخت پیام داده + بازیابی.
    reader = اکانت‌خوان (اگر وصل باشد، جستجوی تاریخچه با آن انجام می‌شود
    چون ربات‌ها اجازه‌ی گشتن در تاریخچه را ندارند)."""
    _state["client"] = client
    _state["admin"] = next(iter(sorted(admin_ids)), None) if admin_ids else None
    if not _state["admin"]:
        print("[tgstore] مدیری تعریف نشده — غیرفعال", flush=True)
        return False
    # مسیر ۰ (اولویت اول): گروه «مای ربات» (بخش ذخیره) — بکاپ اینجاست
    # 🔧 پچ: اگر اکانت‌خوان (reader) وصل نباشد، با خودِ ربات هم تلاش می‌شود —
    # ربات عضو گروه است و می‌تواند پیام بکاپ را با شناسه بخواند (بدون نیاز به یوزربات)
    g = _state.get("group")
    if g:
        for _who, _s in (("اکانت‌خوان", reader), ("ربات", client)):
            if _s is None:
                continue
            try:
                _gid = (_state.get("group_msg") or {}).get("id")
                if _gid:
                    m = await _s.get_messages(g["chat"], ids=_gid)
                    if m and m.text and m.text.startswith(MARKER) and "DATA|" in m.text:
                        n = _restore(m.text.split("DATA|", 1)[1], source="گروه ✅")
                        print(f"[tgstore] {n} فایل از گروه بازیابی شد ✓ (با {_who})", flush=True)
                        if n:
                            return True
                if _s is not client:      # گشتن در تاریخچه فقط با اکانت‌خوان ممکن است
                    async for m in _s.iter_messages(g["chat"], limit=150,
                                                    reply_to=g["topic"]):
                        if m.text and m.text.startswith(MARKER) and "DATA|" in m.text:
                            _save_group_msgid(m.id)
                            _state["group_msg"] = {"id": m.id}
                            n = _restore(m.text.split("DATA|", 1)[1], source="گروه ✅")
                            print(f"[tgstore] {n} فایل از گروه بازیابی شد ✓ (جستجو)", flush=True)
                            if n:
                                return True
                            break
            except Exception as e:
                print(f"[tgstore] جستجوی گروه ({_who}): {e}", flush=True)

    # مسیر ۱: شناسه‌ی پیام ذخیره‌شده (چت مدیر — پشتیبان)
    mid = None
    try:
        mid = json.loads(MSGID_FILE.read_text()).get("id")
    except Exception:
        pass
    searchers = []
    if reader is not None:
        searchers.append(reader)
    searchers.append(client)
    for searcher in searchers:
        # مسیر ۱: همان پیام قبلی با شناسه
        if mid:
            try:
                m = await searcher.get_messages(_state["admin"], ids=mid)
                if m and m.text and m.text.startswith(MARKER):
                    _state["msg"] = m
                    if "DATA|" in m.text:
                        n = _restore(m.text.split("DATA|", 1)[1])
                        if n:
                            print(f"[tgstore] {n} فایل از تلگرام بازیابی شد ✓", flush=True)
                    print("[tgstore] متصل (با شناسه پیام) ✅", flush=True)
                    return True
            except Exception:
                pass
        # مسیر ۲: جستجو (فقط با اکانت‌خوان واقعاً کار می‌کند)
        if searcher is not client:
            try:
                async for m in searcher.iter_messages(_state["admin"], limit=2000):
                    if m.text and m.text.startswith(MARKER):
                        _state["msg"] = m
                        _save_msgid(m.id)
                        if "DATA|" in m.text:
                            n = _restore(m.text.split("DATA|", 1)[1])
                            if n:
                                print(f"[tgstore] {n} فایل از تلگرام بازیابی شد ✓ (جستجو)", flush=True)
                        print("[tgstore] متصل (با جستجوی اکانت‌خوان) ✅", flush=True)
                        return True
            except Exception as e:
                print(f"[tgstore] جستجوی اکانت‌خوان: {e}", flush=True)
    print("[tgstore] پیام داده پیدا نشد — با /backup دستی هم می‌توان نسخه داشت", flush=True)
    return False


async def _flush():
    c, a = _state["client"], _state["admin"]
    if not c or not a:
        return
    guide = "\n".join(f"{v} \u27f5 {k}" for k, v in SECTION_INFO.items()
                      if k != "user_session.json")
    payload = (MARKER + "\n\ud83d\udccb \u0628\u062e\u0634\u200c\u0647\u0627 (\u0628\u0631\u0627\u06cc \u062d\u0630\u0641 \u062f\u0633\u062a\u06cc\u060c \u0641\u0642\u0637 \u06a9\u0644\u06cc\u062f\u0634 \u0631\u0627 \u062f\u0631 JSON \u067e\u0627\u06a9 \u06a9\u0646):\n"
               + guide + "\nDATA|" + _snapshot())
    # 🔧 اگر بخش «ذخیره»ی گروه تنظیم شده → بکاپ فقط در گروه (چت ادمین تمیز می‌ماند)
    # گروه نبود → همان روش قدیمی در چت ادمین
    g = _state.get("group")
    if g:
        try:
            if _state.get("group_msg") and _state["group_msg"].get("id"):
                await c.edit_message(g["chat"], _state["group_msg"]["id"], payload)
            else:
                m2 = await c.send_message(g["chat"], payload, reply_to=g["topic"])
                _state["group_msg"] = {"id": m2.id}
                _save_group_msgid(m2.id)
            return
        except Exception:
            try:
                m2 = await c.send_message(g["chat"], payload, reply_to=g["topic"])
                _state["group_msg"] = {"id": m2.id}
                _save_group_msgid(m2.id)
                return
            except Exception as e2:
                print(f"[tgstore] group flush: {str(e2)[:80]} — fallback به چت مدیر", flush=True)
    # بدون گروه (یا گروه ناموفق) → چت ادمین
    try:
        if _state["msg"]:
            await c.edit_message(a, _state["msg"].id, payload)
        else:
            _state["msg"] = await c.send_message(a, payload)
            _save_msgid(_state["msg"].id)
    except Exception as e:
        print(f"[tgstore] flush: {str(e)[:80]}", flush=True)
        try:
            _state["msg"] = await c.send_message(a, payload)
            _save_msgid(_state["msg"].id)
        except Exception:
            pass


async def loop():
    """هر ۲ دقیقه اگر تغییری بوده، در تلگرام ذخیره می‌کند"""
    while True:
        await asyncio.sleep(60)
        if _state["dirty"] and _state["client"]:
            _state["dirty"] = False
            await _flush()
