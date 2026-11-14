#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🤖 ماژول هوش مصنوعی — بازسازی‌کامل v2 (ai-2.0)

مشکل ریشه‌ای قبلی: مدل llama-3.3-70b-versatile در ۱۶ آگوست ۲۰۲۶ توسط Groq خاموش شد
و چون اولین (و عملاً تنها) مدل مسیر چت بود، هوش مصنوعی کاملاً از کار افتاد.

راه‌حل این نسخه:
- کشف زنده‌ی مدل‌های موجود از GET /models خود Groq با کلید شما → هیچ‌وقت مدل مرده انتخاب نمی‌شود
- اولویت‌بندی هوشمند: مدل‌های قوی جدید اول (gpt-oss-120b، qwen3.6، kimi-k2…)
- مدل موفق به‌خاطر سپرده می‌شود → سوال بعدی سریع جواب می‌گیرد
- زنجیره‌ی کامل: همه مدل‌های زنده Groq → Gemini (برای کلید AIza) → سرویس‌های دیگر
- /aitest لیست واقعی مدل‌های زنده کلید شما را نشان می‌دهد
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "ai-2.7"

SYSTEM_FA = ("تو «آیدی» هستی؛ دستیار فارسی‌زبان یک ربات تلگرام. "
             "پاسخ‌ها را خلاصه، دقیق، مؤدب و کاربردی به فارسی بده.")

GROQ_BASE = "https://api.groq.com/openai/v1"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# اولویت مدل‌های Groq — قوی‌ترین‌ها اول (اگر در /models زنده باشند)
_GROQ_PRIORITY = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.6-27b",
    "moonshotai/kimi-k2-instruct-0905",
    "moonshotai/kimi-k2-instruct",
    "openai/gpt-oss-20b",
    "groq/compound-mini",
    "groq/compound",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "gemma2-9b-it",
]
# مدل‌هایی که برای چت مناسب نیستند
_EXCLUDE = ("whisper", "tts", "guard", "safeguard", "embed", "rerank", "distil")

# حافظه: مدل‌های زنده + آخرین مدل موفق
_cache = {"ts": 0.0, "models": []}
LAST_OK = ""      # آخرین مدلی که جواب داد
LAST_ERR = ""     # آخرین خطا (برای /debug)


def _clean_key():
    key = (os.environ.get("AI_API_KEY") or "").strip().strip('"').strip("'")
    return key.removeprefix("Bearer ").strip()


def _post_json(url, payload, headers=None, timeout=45):
    h = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _get_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


# ------------------------------------------------ کشف زنده‌ی مدل‌های Groq

def groq_models(key, force=False):
    """لیست مدل‌های چتِ زنده از خود Groq — ۳۰ دقیقه کش"""
    now = time.time()
    if not force and _cache["models"] and now - _cache["ts"] < 1800:
        return _cache["models"]
    try:
        d = _get_json(f"{GROQ_BASE}/models",
                      headers={"Authorization": f"Bearer {key}"}, timeout=15)
        ids = []
        for m in (d.get("data") or []):
            mid = m.get("id") or ""
            if mid and not any(x in mid.lower() for x in _EXCLUDE):
                ids.append(mid)
        # مرتب‌سازی بر اساس اولویت؛ مدل‌های ناشناخته‌ی جدید بعد از شناخته‌ها
        known = [m for m in _GROQ_PRIORITY if m in ids]
        unknown = sorted(m for m in ids if m not in _GROQ_PRIORITY)
        ordered = known + unknown
        if ordered:
            _cache.update(ts=now, models=ordered)
            return ordered
    except Exception:
        pass
    # اگر /models در دسترس نبود → لیست ایستا (باز به‌ترتیب اولویت)
    return [m for m in _GROQ_PRIORITY]


def _strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    return text.replace("<think>", "").replace("</think>", "").strip()


def _try_groq(key, model, messages, max_tokens=1200):
    out = _post_json(
        f"{GROQ_BASE}/chat/completions",
        {"model": model, "messages": messages,
         "max_tokens": max_tokens, "temperature": 0.7},
        headers={"Authorization": f"Bearer {key}"})
    d = json.loads(out)
    if d.get("error"):
        raise RuntimeError(str(d["error"])[:90])
    msg = ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    msg = _strip_think(msg or "")
    if not msg:
        raise RuntimeError("پاسخ خالی")
    return msg


def _gemini_rest(key, model, messages, max_tokens=1200):
    url = f"{GEMINI_BASE}/models/{model}:generateContent?key={key}"
    contents = [{"role": "user" if m["role"] != "assistant" else "model",
                 "parts": [{"text": m["content"]}]} for m in messages]
    out = _post_json(url, {"contents": contents,
                           "generationConfig": {"maxOutputTokens": max_tokens}})
    d = json.loads(out)
    if d.get("error"):
        raise RuntimeError(str(d["error"].get("message", ""))[:90])
    parts = d.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    txt = _strip_think("".join(p.get("text", "") for p in parts))
    if not txt:
        raise RuntimeError("پاسخ خالی gemini")
    return txt


def _try_openai_compatible(url, key, model, messages, max_tokens=1200):
    out = _post_json(url.rstrip("/") + "/chat/completions",
                     {"model": model, "messages": messages,
                      "max_tokens": max_tokens, "temperature": 0.7},
                     headers={"Authorization": f"Bearer {key}"})
    d = json.loads(out)
    if d.get("error"):
        raise RuntimeError(str(d["error"])[:90])
    msg = _strip_think(((d.get("choices") or [{}])[0].get("message") or {}).get("content", ""))
    if not msg:
        raise RuntimeError("پاسخ خالی")
    return msg


# ------------------------------------------------ مسیر اصلی چت

def _chat_groq(key, messages):
    """همه‌ی مدل‌های زنده Groq را به‌ترتیب اولویت امتحان می‌کند"""
    global LAST_OK, LAST_ERR
    models = list(groq_models(key))
    if LAST_OK and LAST_OK in models:
        models.remove(LAST_OK)
        models.insert(0, LAST_OK)      # مدل موفق قبلی → اول
    last = None
    for m in models:
        try:
            ans = _try_groq(key, m, messages)
            LAST_OK = m
            return ans
        except Exception as e:
            last = e
            s = str(e).lower()
            if "invalid api key" in s or "unauthorized" in s or "401" in s:
                break   # کلید غلط است؛ بقیه‌ی مدل‌ها هم رد می‌شوند
    LAST_ERR = str(last or "")
    raise last or RuntimeError("هیچ مدل Groq در دسترس نبود")


def _chat_gemini(key, messages):
    last = None
    for m in ("gemini-2.0-flash", "gemini-flash-latest", "gemini-2.5-flash",
              "gemini-2.0-flash-lite", "gemini-1.5-flash"):
        try:
            return _gemini_rest(key, m, messages)
        except Exception as e:
            last = e
    raise last


def _anon(messages):
    """پاسخ رایگان بدون کلید — best effort"""
    try:
        url = ("https://text.pollinations.ai/"
               + urllib.parse.quote(messages[-1]["content"][:2000]))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read().decode("utf-8", "ignore").strip()
        if t and not t.startswith("{") and len(t) > 1 and "402" not in t[:30]:
            return t[:4000]
    except Exception:
        pass
    return None


def ask(prompt, history=None):
    messages = [{"role": "system", "content": SYSTEM_FA}]
    for h in (history or [])[-6:]:
        messages.append({"role": h.get("r", "user"),
                         "content": (h.get("t") or "")[:1500]})
    messages.append({"role": "user", "content": (prompt or "").strip()[:4000]})

    key = _clean_key()
    key_err = None
    if key:
        try:
            if key.startswith("gsk_"):
                return _chat_groq(key, messages)
            if key.startswith("AIza"):
                return _chat_gemini(key, messages)
            if key.startswith("sk-or-"):
                return _try_openai_compatible(
                    "https://openrouter.ai/api/v1", key,
                    "meta-llama/llama-3.3-70b-instruct:free", messages)
            if key.startswith("sk-"):
                return _try_openai_compatible(
                    "https://api.deepseek.com", key, "deepseek-chat", messages)
        except Exception as e:
            key_err = e
    r = _anon(messages)
    if r:
        return r
    if key_err is not None:
        raise RuntimeError(
            "❌ سرور هوش مصنوعی جواب نداد.\n"
            f"آخرین خطا: {str(key_err)[:120]}\n"
            "مدیر: /aitest بزن تا لیست مدل‌های زنده کلیدت را ببینی.")
    raise RuntimeError("سرویس هوش مصنوعی موقتاً قطع است — چند لحظه بعد دوباره.")


def ask_raw(system, user_text, history=None, max_tokens=600):
    """چت با پرامپت سیستمی سفارشی + حافظه — برای دستیار اکانت‌خوان (/m) و گروه AI"""
    messages = [{"role": "system", "content": system[:3500]}]
    for h in (history or [])[-12:]:
        messages.append({"role": h.get("r", "user"),
                         "content": (h.get("t") or "")[:1200]})
    messages.append({"role": "user", "content": (user_text or "").strip()[:3500]})
    key = _clean_key()
    if key:
        if key.startswith("gsk_"):
            return _chat_groq(key, messages)
        if key.startswith("AIza"):
            return _chat_gemini(key, messages)
    r = _anon(messages)
    if r:
        return r
    raise RuntimeError("هوش مصنوعی در دسترس نیست — /aitest را بزن.")


# ------------------------------------------------ تشخیص

def diagnose():
    """گزارش زنده برای /aitest"""
    global LAST_OK, LAST_ERR
    key = _clean_key()
    lines = ["🧪 تشخیص هوش مصنوعی", ""]
    if not key:
        lines += ["❌ AI_API_KEY در Secrets نیست.",
                  "کلید رایگان: console.groq.com (gsk_) یا aistudio.google.com/apikey (AIza)"]
        return "\n".join(lines)
    kind = ("Groq" if key.startswith("gsk_") else
            "Gemini" if key.startswith("AIza") else
            "OpenRouter" if key.startswith("sk-or-") else
            "DeepSeek" if key.startswith("sk-") else "ناشناخته")
    lines.append(f"🔑 کلید: {key[:7]}…{key[-3:]} ({len(key)} حرف) — {kind}")
    lines.append("")

    msgs = [{"role": "user", "content": "فقط یک کلمه بگو: سالم"}]
    if key.startswith("gsk_"):
        models = groq_models(key, force=True)
        lines.append(f"📋 مدل‌های زنده‌ی کلید شما ({len(models)}):")
        for m in models[:8]:
            mark = "⭐" if m == LAST_OK else "•"
            lines.append(f"  {mark} {m}")
        if len(models) > 8:
            lines.append(f"  … و {len(models) - 8} مدل دیگر")
        lines.append("")
        # تست زنده سه مدل اول
        winner = None
        for m in models[:3]:
            try:
                _try_groq(key, m, msgs, 120)
                lines.append(f"✅ {m} → کار می‌کند!")
                winner = m
                break
            except Exception as e:
                lines.append(f"❌ {m} → {str(e)[:60]}")
        lines.append("")
        if winner:
            LAST_OK = winner
            lines.append(f"🎯 نتیجه: {winner} فعال — AI آماده است ✅")
        else:
            lines.append("❌ هیچ مدل Groq جواب نداد.")
        lines.append("")
        lines.append("💡 پیشنهادها:")
        lines.append("• کلید Groq را در console.groq.com تازه کن (API Keys → Create)")
        lines.append("• یا کلید رایگان Gemini: aistudio.google.com/apikey")
        lines.append("  (در Secrets: AI_API_KEY = AIza...)")
        lines.append("• ویس به متن: از HF_TOKEN هم پشتیبانی می‌شود (مستقل از Groq)")
    elif key.startswith("AIza"):
        for m in ("gemini-2.0-flash", "gemini-flash-latest", "gemini-1.5-flash"):
            try:
                _gemini_rest(key, m, msgs, 120)
                lines.append(f"✅ {m} → کار می‌کند!")
                break
            except Exception as e:
                lines.append(f"❌ {m} → {str(e)[:60]}")
    else:
        try:
            ask("فقط بگو: سالم")
            lines.append("✅ مسیر اصلی کار می‌کند!")
        except Exception as e:
            lines.append(f"❌ {str(e)[:120]}")
    return "\n".join(lines)


def warm_cache():
    """گرم‌کردن کش مدل‌ها موقع استارت — تا /debug از اول تعداد را نشان دهد"""
    key = _clean_key()
    if key.startswith("gsk_"):
        try:
            groq_models(key, force=True)
        except Exception:
            pass
    elif key.startswith("AIza"):
        pass


def has_provider() -> bool:
    return bool(_clean_key())


def status_line() -> str:
    """یک خط برای /debug"""
    key = _clean_key()
    if not key:
        return "❌ کلید AI در Secrets نیست"
    if LAST_OK:
        return f"✅ فعال ({LAST_OK})"
    n = len(_cache["models"]) or "؟"
    return f"⏳ آماده ({n} مدل Groq کش‌شده)"


# ------------------------------------------------ ویس → متن (Groq Whisper)

# پرامپتِ راهنما: به ویسپر می‌گوید انتظار فارسی محاوره‌ای دارد — کلمات خودمونی بهتر درمی‌آیند
_STT_PROMPT = ("مکالمه‌ی فارسی محاوره‌ای. "
               "کلمات غیررسمی و خودمونی (مثل: چطوری، نمیدونم، بریم، فک کنم) "
               "همین‌طور نگارش محاوره‌ای نوشته می‌شود.")


# زنجیره‌ی مدل‌های STT — اولی که کلید قبول کند (403 = مدل برای کلید آزاد نیست)
STT_MODELS = ("whisper-large-v3", "whisper-large-v3-turbo", "distil-whisper-large-v3-en")


def transcribe(path, lang=None, model=None, prompt=_STT_PROMPT):
    """ویس/صدا → متن فارسی با Whisper Groq
    - زنجیره‌ی مدل: large-v3 → turbo → distil (اولی که جواب دهد)
    - lang=None → تشخیص خودکار زبان
    - prompt → بایاس به فارسی محاوره‌ای
    خروجی: متن | "" اگر خالی بود | None اگر کلید نیست"""
    last_err = None
    for m_ in (model,) if model else STT_MODELS:
        try:
            return _transcribe_one(path, lang, m_, prompt)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (403, 404, 400):     # مدل آزاد نیست → مدل بعدی
                continue
            raise                              # خطای دیگر (شبکه/کلید) → همان
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return None


def _transcribe_one(path, lang=None, model="whisper-large-v3-turbo", prompt=_STT_PROMPT):
    """یک بار تلاش با یک مدل مشخص"""
    key = _clean_key()
    if not key.startswith("gsk_"):
        return None
    import os as _os
    import uuid as _uuid
    boundary = "----stt" + _uuid.uuid4().hex
    fn = _os.path.basename(path)
    with open(path, "rb") as f:
        audio = f.read(24_000_000)          # سقف ۲۴ مگابایت (خود Groq)
    parts = []
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                  f"filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
                 + audio + b"\r\n")
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n"
                  + (model or "whisper-large-v3") + "\r\n").encode())
    if lang:
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n"
                      f"{lang}\r\n").encode())
    if prompt:
        parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"prompt\"\r\n\r\n"
                      f"{prompt[:800]}\r\n").encode())
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"temperature\"\r\n\r\n0\r\n").encode())
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        f"{GROQ_BASE}/audio/transcriptions", data=body, method="POST", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    return (d.get("text") or "").strip()


def has_provider() -> bool:
    return bool(_clean_key())


def status_line() -> str:
    """یک خط برای /debug"""
    key = _clean_key()
    if not key:
        return "❌ کلید AI در Secrets نیست"
    if LAST_OK:
        return f"✅ فعال ({LAST_OK})"
    n = len(_cache["models"]) or "؟"
    return f"⏳ آماده ({n} مدل Groq کش‌شده)"


# ------------------------------------------------ ویس → متن (Groq Whisper)

def transcribe(path, lang="fa"):
    """تبدیل فایل ویس به متن با whisper روی Groq — None یعنی ممکن نیست"""
    key = _clean_key()
    if not key.startswith("gsk_"):
        return None
    import base64
    import os
    import uuid
    boundary = "----" + uuid.uuid4().hex
    fn = os.path.basename(path)
    with open(path, "rb") as f:
        audio = f.read(24_000_000)          # سقف ۲۴ مگابایت
    body = b""
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
    body += audio + b"\r\n"
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n"
             f"whisper-large-v3-turbo\r\n").encode()
    if lang:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n"
                 f"{lang}\r\n").encode()
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{GROQ_BASE}/audio/transcriptions", data=body, method="POST", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    return (d.get("text") or "").strip() or None


def _hf_whisper(path):
    """فالبک STT: HF Inference API با HF_TOKEN (توکنی که کاربر از قبل دارد)
    مدل‌ها به‌ترتیب: whisper-large-v3-turbo → whisper-large-v3"""
    hf_key = (os.environ.get("HF_TOKEN")
              or os.environ.get("HUGGING_FACE_HUB_TOKEN") or "").strip()
    if not hf_key:
        return None
    for model in ("openai/whisper-large-v3-turbo", "openai/whisper-large-v3"):
        try:
            with open(path, "rb") as f:
                audio = f.read(24_000_000)
            req = urllib.request.Request(
                f"https://api-inference.huggingface.co/models/{model}",
                data=audio, method="POST",
                headers={"Authorization": f"Bearer {hf_key}",
                         "Content-Type": "audio/mpeg"})
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode("utf-8", "ignore"))
            txt = (d.get("text") or "").strip()
            if txt:
                return txt
        except Exception:
            continue
    return None
