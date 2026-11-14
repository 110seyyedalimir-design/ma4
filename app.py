#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
اجرای «دو ربات تلگرام» روی یک Hugging Face Space (رایگان / ZeroGPU)

🤖 ربات ۱: آچارفرانسه (@testarenakhodeshbot) → bot.py      (همون ربات اصلی)
🔁 ربات ۲: دوربرگردون (@dorbargardoon_bot)   → dor_bot.py  (پورت داخلی 7861)

قوانین اسپیس رایگان HF:
- سخت‌افزار مجبوری ZeroGPU است
- ZeroGPU موقع روشن شدن دنبال تابعی با برچسب @spaces.GPU «داخل رابط Gradio» می‌گردد
  و اگر پیدا نکند برنامه را می‌بندد!
پس: یک Gradio کوچک + یک تابع GPU نمایشی که به دکمه وصل است (هرگز لازم نیست)
+ هر دو ربات به‌صورت زیرپروسه با ری‌استارت خودکار.

مسیرهای آیفون ربات دوربرگردون (فقط پورت 7860 عمومی است):
  /dns-query/<توکن>   ← DNS رمزنگاری‌شدهٔ شخصی (DoH)
  /ios/...            ← صفحهٔ نصب، فایل پروفایل، پروفایل sing-box
این مسیرها از داخل Gradio به سرور داخلی ربات دوم (127.0.0.1:7861) فرستاده می‌شوند.

هر دو ربات تلگرام‌شان را با Telethon (MTProto مستقیم) وصل می‌کنند؛
دیتای هرکدام در مخزن جداگانهٔ خصوصی ذخیره می‌شود:
  ربات ۱ → <owner>/bot-data          ربات ۲ → <owner>/dorbargardoon-data
"""

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request

import gradio as gr

# GPU فقط روی اسپیس‌های ZeroGPU هست — روی CPU اختیاری است
try:
    import spaces
    @spaces.GPU
    def gpu_placeholder(x=""):
        return x
except Exception:
    def gpu_placeholder(x=""):
        return x

BOT1_USERNAME = "@testarenakhodeshbot"
BOT2_USERNAME = "@dorbargardoon_bot"
BOT2_PORT = "7861"            # پورت داخلی ربات دوربرگردون (7860 مال Gradio است)

BOTS = {
    "bot.py":     {"proc": None, "started": None, "title": "🔧 آچارفرانسه", "user": BOT1_USERNAME},
    "dor_bot.py": {"proc": None, "started": None, "title": "🔁 دوربرگردون",  "user": BOT2_USERNAME},
}


def log(line: str):
    try:
        print(line, flush=True)
    except Exception:
        pass


def ensure_ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        log("[ffmpeg] از قبل نصب است ✓")
        return
    try:
        from static_ffmpeg import add_paths
        log("[ffmpeg] در حال آماده‌سازی نسخه همراه…")
        add_paths()
        log("[ffmpeg] آماده شد ✓")
    except Exception as e:
        log(f"[ffmpeg] آماده‌سازی ناموفق: {e}")


def run_bot(script: str, st: dict, port: str = None):
    """اجرای یک ربات با ری‌استارت خودکار — تا ابد"""
    env = dict(os.environ)
    if port:
        env["PORT"] = port              # پورت داخلی سرور HTTP ربات (فقط دوربرگردون)
    while True:
        st["started"] = time.time()
        try:
            p = subprocess.Popen(
                ["python", "-u", script],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            st["proc"] = p

            def reader():
                try:
                    for line in p.stdout:
                        log(f"[{script}] {line.rstrip()}")
                except Exception:
                    pass

            threading.Thread(target=reader, daemon=True).start()
            code = p.wait()
            log(f"[supervisor:{script}] ربات قطع شد (کد {code}) — ۵ ثانیه بعد دوباره…")
        except Exception as e:
            log(f"[supervisor:{script}] خطا در اجرای ربات: {e}")
        time.sleep(5)


def _alive(st: dict) -> bool:
    return st["proc"] is not None and st["proc"].poll() is None


def _uptime(st: dict) -> str:
    up = (time.time() - st["started"]) if st["started"] else 0
    h, m = int(up // 3600), int((up % 3600) // 60)
    return f"{h} ساعت و {m} دقیقه"


def status_bot1():
    """وضعیت ربات آچارفرانسه"""
    st = BOTS["bot.py"]
    if _alive(st):
        return f"### ✅ روشنه و در حال کاره\nآپ‌تایم این اجرا: {_uptime(st)}"
    return "### ⛔ در حال راه‌اندازی مجدد… (چند ثانیه دیگر)"


def status_bot2():
    """وضعیت ربات دوربرگردون — از سرور داخلی خودش (7861)"""
    st = BOTS["dor_bot.py"]
    head = "✅ روشنه" if _alive(st) else "⛔ در حال راه‌اندازی مجدد…"
    lines = [f"### {head}"]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{BOT2_PORT}/status.json", timeout=5) as r:
            d = json.loads(r.read().decode("utf-8"))
        tg = str(d.get("telegram", ""))
        if tg and not tg.startswith("✅"):
            lines.append(f"⚠️ {tg}")
        elif tg:
            lines.append(f"🛰 تلگرام وصل است — {d.get('transport', '')}")
        ios = d.get("ios") or {}
        if ios:
            lines.append(f"🍏 اشتراک آیفون فعال: {ios.get('active', 0)} | DNS امروز: {ios.get('dns_today', 0)}")
        stg = d.get("storage", "")
        if stg:
            lines.append(f"💾 {stg}")
    except Exception:
        if _alive(st):
            lines.append("⏳ در حال وصل شدن به تلگرام…")
    return "\n".join(lines)


with gr.Blocks(title="ربات‌های تلگرام") as demo:
    gr.Markdown("# 🤖 دو ربات، یک اسپیس")
    gr.Markdown("📦 **پکیج v26.5.0** — 🔧 آچارفرانسه v25.49.0 (اتاق تگ) + 🔁 دوربرگردون")
    with gr.Row():
        with gr.Column():
            gr.Markdown(f"## 🔧 آچارفرانسه\n{BOT1_USERNAME}")
            gr.Markdown(status_bot1, every=30)
        with gr.Column():
            gr.Markdown(f"## 🔁 دوربرگردون\n{BOT2_USERNAME}")
            gr.Markdown(status_bot2, every=30)

    # دکمه و تابع GPU — فقط برای عبور از چک ZeroGPU
    _btn = gr.Button("بررسی سیستم", visible=False)
    _out = gr.Textbox(visible=False)
    _btn.click(gpu_placeholder, inputs=_out, outputs=_out)


# ------------------------------------------------------------ iPhone gateway
# فقط پورت Gradio (7860) عمومی است؛ مسیرهای آیفون به سرور داخلی ربات دوربرگردون (7861) فرستاده می‌شوند
_HOP = {"content-length", "transfer-encoding", "connection", "content-encoding", "server", "date", "keep-alive"}


def add_ios_routes(app):
    import httpx
    from starlette.responses import Response
    from starlette.routing import Route

    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{BOT2_PORT}", timeout=httpx.Timeout(20.0, connect=3.0),
                               limits=httpx.Limits(max_connections=200, max_keepalive_connections=50))

    async def proxy(request):
        body = await request.body() if request.method == "POST" else b""
        if len(body) > 8192:
            return Response("too large", status_code=413, media_type="text/plain")
        fwd = {k: v for k, v in request.headers.items()
               if k.lower() in ("content-type", "accept", "user-agent", "accept-language")}
        xff = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")
        fwd["X-Forwarded-For"] = xff
        url = request.url.path + (("?" + request.url.query) if request.url.query else "")
        try:
            r = await client.request(request.method, url, content=body, headers=fwd)
        except Exception:
            return Response("starting, try again", status_code=503, media_type="text/plain",
                            headers={"Retry-After": "5", "Cache-Control": "no-store"})
        hdrs = {k: v for k, v in r.headers.items() if k.lower() not in _HOP}
        return Response(r.content, status_code=r.status_code, headers=hdrs)

    routes = [Route("/dns-query/{token}", proxy, methods=["GET", "POST", "HEAD"]),
              Route("/ios/{rest:path}", proxy, methods=["GET", "HEAD"])]
    for r in reversed(routes):
        app.router.routes.insert(0, r)      # قبل از مسیرهای Gradio
    log("[app] 🍏 مسیرهای آیفون دوربرگردون فعال شد: /dns-query/<token> و /ios/…")


# ------------------------------------------------------------ شروع
ensure_ffmpeg()
threading.Thread(target=run_bot, args=("bot.py", BOTS["bot.py"]), daemon=True).start()
threading.Thread(target=run_bot, args=("dor_bot.py", BOTS["dor_bot.py"], BOT2_PORT), daemon=True).start()

demo.launch(ssr_mode=False, prevent_thread_lock=True)
log("[app] ✅ همه سرویس‌ها بالا آمدند — هر دو ربات در حال اجرا هستند")
try:
    add_ios_routes(demo.app)
except Exception as e:
    log(f"[app] ⚠️ مسیرهای آیفون اضافه نشد: {e}")

# حلقه اصلی: هرگز تمام نشود
while True:
    time.sleep(60)
