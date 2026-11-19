#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
سوپروایزر پرتابل — جایگزین app.py برای هر پلتفرمی غیر از Hugging Face
(Northflank / Render / Back4App / Oracle VPS / Docker / GitHub Actions)

چرا این فایل؟
  app.py فقط برای اسپیس HF نوشته شده (Gradio + تابع الکی ZeroGPU + پروکسی 7861).
  بیرون از HF هیچ‌کدام لازم نیست: dor_bot.py خودش سرور HTTP دارد و روی
  0.0.0.0:PORT مسیرهای /dns-query/<token> و /ios/… و /health را سرو می‌کند.
  bot.py هم هیچ پورتی نمی‌خواهد (Telethon = فقط ارتباط خروجی).

متغیرهای محیطی:
  PORT              پورت عمومی سرویس (پلتفرم معمولاً خودش تزریق می‌کند) — پیش‌فرض 7860
  ENABLE_BOT1       اجرای ربات آچارفرانسه (bot.py)        — پیش‌فرض 1
  ENABLE_BOT2       اجرای ربات دوربرگردون (dor_bot.py)    — پیش‌فرض 1
  BOT2_PORT         پورت داخلی dor_bot (اگر با PORT فرق دارد)
  PUBLIC_URL        آدرس عمومی HTTPS سرویس — برای لینک‌های آیفون لازم است
                    (روی HF این از SPACE_HOST ساخته می‌شد؛ اینجا باید دستی بدهید)
  HF_TOKEN, DATA_REPO  برای ذخیره‌سازی دائمی داده‌ها (همان مکانیزم قبلی)

نکته: اگر ENABLE_BOT2=0 باشد ولی پلتفرم پورت بخواهد (مثل Render)، این فایل
خودش یک سرور /health روی PORT بالا می‌آورد تا سرویس «وب» شناخته شود.
"""
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = (os.environ.get("PORT") or "7860").strip()
BOT2_PORT = (os.environ.get("BOT2_PORT") or PORT).strip()


def _flag(name: str, default: bool = True) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on", "enabled")


ENABLE_BOT1 = _flag("ENABLE_BOT1", True)
ENABLE_BOT2 = _flag("ENABLE_BOT2", True)

_stop = threading.Event()
_procs = []


def log(msg: str):
    print(f"[supervisor] {msg}", flush=True)


def ensure_ffmpeg():
    """ffmpeg سیستم (apt) ترجیح دارد؛ اگر نبود، نسخهٔ همراه static-ffmpeg"""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        log("ffmpeg سیستم پیدا شد ✓")
        return
    try:
        from static_ffmpeg import add_paths
        log("در حال آماده‌سازی ffmpeg همراه…")
        add_paths()
        log("ffmpeg همراه آماده شد ✓")
    except Exception as e:
        log(f"⚠️ ffmpeg آماده نشد: {e} (فشرده‌سازی/صوت کار نخواهد کرد)")


def _pipe(proc, label):
    try:
        for line in proc.stdout:
            print(f"[{label}] {line.rstrip()}", flush=True)
    except Exception:
        pass


def run_script(script: str, label: str, extra_env=None):
    """اجرای یک اسکریپت با ری‌استارت خودکار تا زمانی که سوپروایزر زنده است"""
    env = dict(os.environ)
    env.update(extra_env or {})
    while not _stop.is_set():
        started = time.time()
        try:
            p = subprocess.Popen(
                [sys.executable, "-u", script],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except Exception as e:
            log(f"{label}: خطا در اجرا → {e}")
            _stop.wait(5)
            continue
        _procs.append(p)
        threading.Thread(target=_pipe, args=(p, label), daemon=True).start()
        code = p.wait()
        up = int(time.time() - started)
        log(f"{label}: خارج شد (کد {code}) بعد از {up} ثانیه")
        if _stop.is_set():
            break
        # 🛑 مرز پایان اجرا (۶ ساعته): ربات خودش تمیز خارج شد → ری‌استارت نکن
        try:
            if code == 0 and os.path.exists("/tmp/.run_deadline"):
                log("⏳ نشانگر پایان اجرا — سوپروایزر هم تمام می‌کند تا مرحلهٔ ذخیرهٔ کش اجرا شود")
                _stop.set()
                break
        except Exception:
            pass
        log(f"{label}: ری‌استارت در ۵ ثانیه")
        _stop.wait(5)


class _Health(BaseHTTPRequestHandler):
    server_version = "bot-supervisor"

    def log_message(self, *a):
        pass

    def _ok(self, body=b"ok"):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        self._ok()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def serve_health(port: str):
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", int(port)), _Health)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log(f"سرور /health روی پورت {port} بالا آمد (برای زنده نگه‌داشتن سرویس)")
    except Exception as e:
        log(f"⚠️ سرور health بالا نیامد: {e}")


def _shutdown(signum, _frame):
    log(f"سیگنال {signum} — خاموش کردن ربات‌ها…")
    _stop.set()
    for p in list(_procs):
        try:
            if p.poll() is None:
                p.terminate()
        except Exception:
            pass
    time.sleep(3)
    for p in list(_procs):
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
    sys.exit(0)


def seed_from_backup():
    """اگر TG_BACKUP_TEXT پر بود، داده‌های بکاپ تلگرامی را به /tmp برگردان
    (فقط فایل‌های خالی پر می‌شوند، پس تکرارش بی‌خطر است)"""
    if not (os.environ.get("TG_BACKUP_TEXT") or "").strip():
        return
    if not os.path.exists("restore_from_text.py"):
        log("TG_BACKUP_TEXT هست ولی restore_from_text.py پیدا نشد — رد شد")
        return
    try:
        subprocess.run([sys.executable, "-u", "restore_from_text.py"], timeout=120)
    except Exception as e:
        log(f"⚠️ بازگردانی از بکاپ تلگرامی ناموفق: {e}")


def preflight():
    """🔎 چک سریع پیش از شروع — به‌جای اینکه ربات ۶ ساعت در حلقهٔ کرش بماند و
    هیچ علامتی نبینی، اگر Secret مهمی خالی یا بدشکل باشد فوراً با پیام خوانا خارج می‌شود.
    ⚠️ مقدار Secretها هرگز چاپ نمی‌شود (فقط طول و شکلشان)."""
    problems, warnings = [], []

    def _val(k):
        return (os.environ.get(k) or "").strip()

    def _bad_masked(k, v):
        """مقداری که از جای مخفی کپی شده (مثل *** در Secrets اسپیس HF)"""
        if set(v) <= {"*"} or v in ("...", "—", "-"):
            problems.append(
                f"{k} = «{v}» است، یعنی مقدار واقعی نیست! این را از جایی کپی کرده‌ای که مقدار را "
                f"مخفی نشان می‌داد (مثلاً Secrets اسپیس HF که *** نمایش می‌دهد و کپی کردنش فایده ندارد). "
                f"مقدار واقعی را از مبدأ اصلی بگیر: TG_API_ID و TG_API_HASH از my.telegram.org → "
                f"API development tools، و BOT_TOKEN از @BotFather.")

    if ENABLE_BOT1:
        for k, fa in (("BOT_TOKEN", "توکن ربات آچارفرانسه (از @BotFather)"),
                      ("TG_API_ID", "api_id عددی از my.telegram.org"),
                      ("TG_API_HASH", "api_hash از my.telegram.org")):
            if not _val(k):
                problems.append(f"{k} خالی است — {fa}")

        api_id, api_hash, bot_tok = _val("TG_API_ID"), _val("TG_API_HASH"), _val("BOT_TOKEN")

        if api_id:
            _bad_masked("TG_API_ID", api_id)
            if not api_id.isdigit():
                problems.append(
                    f"TG_API_ID باید **فقط رقم** باشد (مثل 12345678) — ولی مقدار فعلی {len(api_id)} کاراکتر "
                    f"است و رقم نیست. احتمالاً api_hash را جای api_id گذاشته‌ای، یا موقع کپی فاصله/کوتیشن/"
                    f"خط جدید چسبیده. در my.telegram.org → API development tools عدد api_id را جدا کپی کن.")

        if api_hash:
            _bad_masked("TG_API_HASH", api_hash)
            if len(api_hash) != 32 or any(c not in "0123456789abcdefABCDEF" for c in api_hash):
                problems.append(
                    f"TG_API_HASH باید **دقیقاً ۳۲ کاراکتر هگز** (0-9 و a-f) باشد — مقدار فعلی "
                    f"{len(api_hash)} کاراکتر است. شاید api_id را جای آن گذاشته‌ای یا ناقص کپی شده.")

        if bot_tok:
            _bad_masked("BOT_TOKEN", bot_tok)
            if ":" not in bot_tok:
                problems.append(
                    f"BOT_TOKEN باید شکل «عدد:رشته» داشته باشد (مثل 123456789:AAE...) — مقدار فعلی "
                    f"{len(bot_tok)} کاراکتر است و «:» ندارد.")

    if ENABLE_BOT2:
        if not (os.environ.get("DOR_BOT_TOKEN") or "").strip():
            problems.append("DOR_BOT_TOKEN خالی است (ENABLE_BOT2=1 گذاشته‌ای) — "
                            "یا اگر ربات دوم را نمی‌خواهی، ENABLE_BOT2=0 بگذار")
        if not ((os.environ.get("PUBLIC_URL") or "").strip()
                or (os.environ.get("IOS_PUBLIC_URL") or "").strip()):
            warnings.append("PUBLIC_URL خالی است → اشتراک آیفون/DoH صادر نمی‌شود")

    if not ((os.environ.get("GH_PAT") or "").strip() or (os.environ.get("HF_TOKEN") or "").strip()):
        warnings.append("⚠️ نه GH_PAT داری نه HF_TOKEN → داده‌ها هیچ‌جا ذخیره نمی‌شوند "
                        "و با هر ری‌استارت (هر ~۶ ساعت) کاربران/منطقه‌ها می‌پرند!")

    for w in warnings:
        log(w)

    if problems:
        log("═" * 62)
        log("❌ اجرا متوقف شد — این Secretها/Variables خالی یا اشتباه‌اند:")
        for p in problems:
            log(f"     • {p}")
        log("👈 مسیر ساخت: ریپو → Settings → Secrets and variables → Actions")
        log("   (نام‌ها باید دقیقاً همین باشند؛ بعد از ساخت، Run workflow بزن)")
        log("═" * 62)
        sys.exit(2)


def main():
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if not ENABLE_BOT1 and not ENABLE_BOT2:
        log("هر دو ربات غیرفعال‌اند! ENABLE_BOT1 یا ENABLE_BOT2 را 1 بگذارید.")
        sys.exit(1)

    preflight()
    seed_from_backup()
    ensure_ffmpeg()
    log(f"شروع… bot1={ENABLE_BOT1} bot2={ENABLE_BOT2} PORT={PORT}")

    if ENABLE_BOT2:
        dor_env = {"PORT": BOT2_PORT}
        # هر دو ربات متغیر DATA_REPO را می‌خوانند؛ اگر یکی باشد داده‌ها قاطی می‌شوند!
        # DOR_DATA_REPO را جدا بدهید (مثلاً yourname/dorbargardoon-data)
        dor_repo = (os.environ.get("DOR_DATA_REPO") or "").strip()
        if dor_repo:
            dor_env["DATA_REPO"] = dor_repo
        elif (os.environ.get("DATA_REPO") or "").strip():
            log("⚠️ DOR_DATA_REPO تنظیم نشده — دوربرگردون از همان DATA_REPO ربات اول استفاده می‌کند "
                "(بهتر است دو مخزن جدا بدهید: bot-data و dorbargardoon-data)")
        threading.Thread(target=run_script, args=("dor_bot.py", "dor_bot", dor_env),
                         daemon=True).start()
    else:
        # پلتفرم‌های «وب‌سرویس» یک پورت شنونده می‌خواهند
        serve_health(PORT)

    if ENABLE_BOT1:
        threading.Thread(target=run_script, args=("bot.py", "bot", {}), daemon=True).start()

    while not _stop.is_set():
        _stop.wait(30)


if __name__ == "__main__":
    main()
