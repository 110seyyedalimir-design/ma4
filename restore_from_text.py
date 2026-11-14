#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🌱 بازگردانی داده‌ها از «متنِ بکاپ تلگرامی» (پیام 🗂 داده‌های ربات)

کاربرد: وقتی روی یک محیط تازه (مثل GitHub Actions) هستی و /tmp خالی است،
با این اسکریپت همان متنِ بکاپی که در گروه/چت مدیر داری را به فایل‌های
json در /tmp تبدیل می‌کنی تا ربات با همان داده‌ها بالا بیاید.

ورودی (یکی از این‌ها):
  1) متغیر محیطی TG_BACKUP_TEXT   ← در GitHub Actions همان Secret است
  2) آرگومان خط فرمان: python restore_from_text.py backup.txt

رفتار:
  • فایل‌هایی که از قبل در /tmp وجود دارند و خالی نیستند **بازنویسی نمی‌شوند**
    (یعنی نسخهٔ تازه‌تر از مخزن داده اولویت دارد). برای بازنویسی اجباری:
    SEED_FORCE=1
  • متن تلگرامی با فرارهای مارک‌داون (\\_  \\[  \\] …) تمیز می‌شود.
"""
import json
import os
import sys
from pathlib import Path

TMP = Path(os.environ.get("PERSIST_DIR", "/tmp"))

# فرارهای مارک‌داون تلگرام + کاراکترهای نامرئی
_ESCAPES = ["\\_", "\\[", "\\]", "\\(", "\\)", "\\~", "\\*", "\\`", "\\+", "\\-",
            "\\. ", "\\.", "\\!", "\\#", "\\{", "\\}", "\\|", "\\<", "\\>", "\\=",
            "\\/"]
_INVISIBLE = ["\u200f", "\u200e", "\u200b", "\ufeff"]


def clean(text: str) -> str:
    for e in _ESCAPES:
        text = text.replace(e, e[1:])
    for c in _INVISIBLE:
        text = text.replace(c, "")
    return text


def extract_json(text: str):
    if "DATA|" in text:
        text = text.split("DATA|", 1)[1]
    s = text.strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < i:
        raise ValueError("هیچ JSON ای در متن پیدا نشد (باید بعد از DATA| یک { باشد)")
    return json.loads(s[i:j + 1])


def read_source() -> str:
    src = os.environ.get("TG_BACKUP_TEXT") or ""
    if src.strip():
        return src
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
        print(f"[seed] ❌ فایل {p} پیدا نشد")
    return ""


def main() -> int:
    src = read_source()
    if not src.strip():
        print("[seed] TG_BACKUP_TEXT خالی است و فایلی هم داده نشد — چیزی برای بازگردانی نیست (رد شد)")
        return 0
    try:
        data = extract_json(clean(src))
    except Exception as e:
        print(f"[seed] ❌ خواندن بکاپ ناموفق: {e}")
        return 1

    force = (os.environ.get("SEED_FORCE", "") == "1")
    written, skipped, empty = [], [], []
    for name, val in sorted(data.items()):
        if not str(name).endswith(".json"):
            continue
        if val is None:
            empty.append(name)
            continue
        p = TMP / name
        if p.exists() and not force:
            try:
                if json.loads(p.read_text() or "null"):
                    skipped.append(name)
                    continue
            except Exception:
                pass
        try:
            p.write_text(json.dumps(val, ensure_ascii=False))
            written.append(name)
        except Exception as e:
            print(f"[seed] ⚠️ نوشتن {name} ناموفق: {e}")

    print(f"[seed] ✅ {len(written)} فایل از بکاپ تلگرامی بازگردانی شد:")
    for n in written:
        print(f"          • {n}")
    if skipped:
        print(f"[seed] ⏭  {len(skipped)} فایل از قبل موجود بود (بازنویسی نشد): {', '.join(skipped)}")
    if empty:
        print(f"[seed] ⚪ {len(empty)} کلید در بکاپ خالی (null) بود: {', '.join(empty)}")

    # گزارش کوتاه از مهم‌ترین بخش‌ها
    try:
        reg = json.loads((TMP / "bargha_regions.json").read_text()) if (TMP / "bargha_regions.json").exists() else {}
        usr = json.loads((TMP / "comp_users.json").read_text()) if (TMP / "comp_users.json").exists() else {}
        gcfg = json.loads((TMP / "group_cfg.json").read_text()) if (TMP / "group_cfg.json").exists() else {}
        gmid = json.loads((TMP / "tgstore_group_msgid.json").read_text()) if (TMP / "tgstore_group_msgid.json").exists() else {}
        print(f"[seed] 📊 منطقه‌ها: {len(reg)} کاربر | کاربران: {len(usr)} | "
              f"بخش‌های گروه: {len(gcfg)} | شناسهٔ پیام بکاپ گروه: {gmid.get('id', '—')}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
