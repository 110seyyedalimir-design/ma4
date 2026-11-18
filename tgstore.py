#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
حافظه‌ی پشتیبان تلگرامی — داده‌های حیاتی (منطقه‌ها + کاربران) به‌صورت خودکار
در گروه/چت مدیر ذخیره می‌شود؛ حتی اگر محیط اجرا از نو ساخته شود، داده‌ها از
تلگرام برمی‌گردند. (پشتیبانِ persist.py)

حالت‌ها (با متغیر محیطی):
  TGSTORE_MODE=new  (پیش‌فرض) → هر بار **پیام تازه** می‌فرستد؛ ویرایش نمی‌کند
  TGSTORE_MODE=edit           → رفتار قدیمی: همان پیام قبلی را ویرایش می‌کند
  TGSTORE_NEW_EVERY_MIN=15    → حداقل فاصلهٔ دو پیام تازه (دقیقه) — ضد اسپم
  TGSTORE_DELETE_OLD=1        → پیام قبلی را بعد از فرستادن پیام تازه پاک می‌کند
"""

import asyncio
import json
import os
import time
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


SECRET_FILES = {"user_session.json", "sd_sessions.json"}   # نشست‌های حساس


def _only_sealed(name: str, payload):
    """در بکاپِ تلگرامی، از نشست‌ها فقط نسخهٔ رمزنگاری‌شده می‌رود.
    (متن خامِ نشست = ورود کامل به حساب؛ هرگز در پیام تلگرام نمی‌گذاریم)"""
    if payload is None:
        return None
    def _sealed(v):
        return isinstance(v, str) and v.strip().startswith("sl1:")
    if name == "user_session.json":
        if isinstance(payload, dict) and _sealed(payload.get("enc")):
            return {"enc": payload["enc"]}
        return {"_note": "🔒 نشست رمزنگاری نشده — برای امنیت در بکاپ تلگرام نیامد "
                         "(SESSION_KEY بساز تا اینجا هم بیاید)"}
    if name == "sd_sessions.json":
        if isinstance(payload, dict):
            keep = {k: v for k, v in payload.items() if _sealed(v)}
            if len(keep) != len(payload):
                keep["_note"] = f"🔒 {len(payload) - len(keep)} نشستِ بدون قفل حذف شد"
            return keep
    return payload


def _snapshot() -> str:
    data = {}
    for f in FILES:
        p = Path(f"/tmp/{f}")
        try:
            data[f] = json.loads(p.read_text()) if p.exists() else None
        except Exception:
            data[f] = None
        if f in SECRET_FILES:
            data[f] = _only_sealed(f, data[f])
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


# ── حالت بکاپ تلگرامی ────────────────────────────────────────────────────
MODE_NEW = (os.environ.get("TGSTORE_MODE", "new").strip().lower() != "edit")
NEW_EVERY_S = max(60.0, float(os.environ.get("TGSTORE_NEW_EVERY_MIN", "15") or 15) * 60.0)
DELETE_OLD = (os.environ.get("TGSTORE_DELETE_OLD", "").strip().lower() in ("1", "true", "yes", "on"))
_last_new = {"ts": 0.0}


def _due_new() -> bool:
    """الان اجازهٔ فرستادن پیام تازه هست؟ (تا گروه پر از پیام نشود)"""
    now = time.time()
    if now - _last_new["ts"] >= NEW_EVERY_S:
        _last_new["ts"] = now
        return True
    return False


async def _flush() -> bool:
    """True = بکاپ واقعاً نوشته شد | False = چیزی نفرستاد (مثلاً مهلت پیام تازه نرسیده)"""
    c, a = _state["client"], _state["admin"]
    if not c or not a:
        return False
    guide = "\n".join(f"{v} \u27f5 {k}" for k, v in SECTION_INFO.items()
                      if k != "user_session.json")
    payload = (MARKER + "\n\ud83d\udccb \u0628\u062e\u0634\u200c\u0647\u0627 (\u0628\u0631\u0627\u06cc \u062d\u0630\u0641 \u062f\u0633\u062a\u06cc\u060c \u0641\u0642\u0637 \u06a9\u0644\u06cc\u062f\u0634 \u0631\u0627 \u062f\u0631 JSON \u067e\u0627\u06a9 \u06a9\u0646):\n"
               + guide + "\nDATA|" + _snapshot())
    # 🔧 اگر بخش «ذخیره»ی گروه تنظیم شده → بکاپ فقط در گروه (چت ادمین تمیز می‌ماند)
    g = _state.get("group")
    if g:
        old_id = (_state.get("group_msg") or {}).get("id")
        if MODE_NEW:
            if not _due_new():
                return False            # ویرایش هم نمی‌کنیم — تا نوبت پیام تازه صبر می‌کنیم
            try:
                m2 = await c.send_message(g["chat"], payload, reply_to=g["topic"])
                if DELETE_OLD and old_id and old_id != m2.id:
                    try:
                        await c.delete_messages(g["chat"], [old_id])
                    except Exception:
                        pass
                _state["group_msg"] = {"id": m2.id}
                _save_group_msgid(m2.id)
                print(f"[tgstore] 💾 بکاپ تازه در گروه فرستاده شد (پیام {m2.id})", flush=True)
                return True
            except Exception as e:
                print(f"[tgstore] ارسال پیام تازه به گروه ناموفق: {str(e)[:80]} — fallback به چت مدیر", flush=True)
        else:
            try:
                if old_id:
                    await c.edit_message(g["chat"], old_id, payload)
                else:
                    m2 = await c.send_message(g["chat"], payload, reply_to=g["topic"])
                    _state["group_msg"] = {"id": m2.id}
                    _save_group_msgid(m2.id)
                return True
            except Exception:
                try:
                    m2 = await c.send_message(g["chat"], payload, reply_to=g["topic"])
                    _state["group_msg"] = {"id": m2.id}
                    _save_group_msgid(m2.id)
                    return True
                except Exception as e2:
                    print(f"[tgstore] group flush: {str(e2)[:80]} — fallback به چت مدیر", flush=True)
    # بدون گروه (یا گروه ناموفق) → چت ادمین
    try:
        if MODE_NEW:
            if not _due_new():
                return False
            _state["msg"] = await c.send_message(a, payload)
            _save_msgid(_state["msg"].id)
            print(f"[tgstore] 💾 بکاپ تازه در چت مدیر فرستاده شد", flush=True)
            return True
        if _state["msg"]:
            await c.edit_message(a, _state["msg"].id, payload)
        else:
            _state["msg"] = await c.send_message(a, payload)
            _save_msgid(_state["msg"].id)
        return True
    except Exception as e:
        print(f"[tgstore] flush: {str(e)[:80]}", flush=True)
        try:
            _state["msg"] = await c.send_message(a, payload)
            _save_msgid(_state["msg"].id)
            return True
        except Exception:
            return False


async def loop():
    """هر ۶۰ ثانیه اگر تغییری بوده، بکاپ را در تلگرام به‌روز می‌کند"""
    while True:
        await asyncio.sleep(60)
        if _state["dirty"] and _state["client"]:
            # 🔧 dirty فقط وقتی صفر می‌شود که بکاپ واقعاً نوشته شده باشد
            if await _flush():
                _state["dirty"] = False
