#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔐 قفل نشست‌ها (sessionlock)
─────────────────────────────
نشست تلگرام (StringSession) در حکم «کارت ورود بدون رمز» به حساب است:
هر کس آن رشته را داشته باشد می‌تواند به‌جای صاحب حساب وارد شود.
این ماژول نشست‌ها را با یک کلیدِ Secret رمزنگاری می‌کند تا اگر فایل/بکاپ
جایی لو رفت، بدون کلید هیچ ارزشی نداشته باشد.

کلید: متغیر محیطی  SESSION_KEY  (هر عبارت طولانی و تصادفی، حداقل ۱۶ کاراکتر)
ساخت کلید:  python -c "import secrets;print(secrets.token_urlsafe(32))"

نحوهٔ کار: PBKDF2-HMAC-SHA256 (۲۰۰هزار دور + salt تصادفی) → کلید Fernet
خروجی:  «sl1:<salt_b64>:<token>»
اگر کلید تنظیم نشده باشد، seal() مقدار None برمی‌گرداند و برنامه
می‌تواند به حالت قبلی (بدون رمز) برگردد و به مدیر هشدار بدهد.
"""

import base64
import os
import secrets

_PREFIX = "sl1"
_HAS_CRYPTO = True
try:                                    # cryptography در requirements هست
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except Exception:                       # pragma: no cover
    _HAS_CRYPTO = False
    InvalidToken = Exception


def key_present() -> bool:
    return bool((os.environ.get("SESSION_KEY") or "").strip()) and _HAS_CRYPTO


def crypto_available() -> bool:
    return _HAS_CRYPTO


def _fernet(salt: bytes):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=200_000)
    key = base64.urlsafe_b64encode(kdf.derive(
        (os.environ.get("SESSION_KEY") or "").encode("utf-8")))
    return Fernet(key)


def seal(plain: str):
    """رمزنگاری متن نشست. اگر کلید نباشد → None."""
    if not plain or not key_present():
        return None
    salt = secrets.token_bytes(16)
    tok = _fernet(salt).encrypt(plain.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}:{base64.urlsafe_b64encode(salt).decode('ascii')}:{tok}"


def open_(blob: str):
    """رمزگشایی. خروجی: (متن یا None، پیام خطا یا '')"""
    blob = (blob or "").strip()
    if not blob:
        return None, "خالی"
    if not blob.startswith(_PREFIX + ":"):
        return None, "قفل‌شده نیست"
    if not key_present():
        return None, "SESSION_KEY تنظیم نشده یا cryptography نصب نیست"
    try:
        _, salt_b64, tok = blob.split(":", 2)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        return _fernet(salt).decrypt(tok.encode("ascii")).decode("utf-8"), ""
    except InvalidToken:
        return None, "کلید اشتباه است (SESSION_KEY عوض شده؟)"
    except Exception as ex:
        return None, f"{type(ex).__name__}: {str(ex)[:80]}"


def is_sealed(blob: str) -> bool:
    return str(blob or "").strip().startswith(_PREFIX + ":")
