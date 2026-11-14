# 🤖 کیت GitHub Actions — اجرای ۲۴/۷ رایگان، بدون کارت، بدون سقف مصرف

پکیج **v26.5.0** آمادهٔ push شدن به یک ریپوی **public** روی GitHub.

> **چرا GitHub Actions؟** روی ریپوی عمومی، دقیقه‌های رانر **unmetered و بدون سقف** هستند (نه اعتبار دلاری، نه ساعت ماهانه)، و رانر `ubuntu-latest` **۴ vCPU / ۱۶GB RAM / ۱۴GB SSD** با ffmpeg نصب‌شده است — یعنی عملاً قوی‌تر از هر پلن رایگان دیگری برای فشرده‌سازی و دانلود. تنها محدودیت سخت: هر job حداکثر ۶ ساعت، که با زنجیرهٔ cron دور زده می‌شود.

---

## 📂 محتویات کیت

| فایل | توضیح |
|---|---|
| `bot.py`, `dor_bot.py`, `tools.py`, … | همان کدهای خودت، **بدون تغییر** |
| `run_all.py` | سوپروایزر پرتابل به‌جای `app.py` (بدون Gradio/ZeroGPU) — هر دو ربات را با ری‌استارت خودکار اجرا می‌کند |
| `persist.py` | **پچ‌شده**: ۱) بیرون از HF با `DATA_REPO` کار می‌کند (قبلاً به `SPACE_ID` وابسته بود و کلاً خاموش می‌شد) ۲) فاصلهٔ ذخیره با `PERSIST_SECONDS` تنظیم می‌شود ۳) **ذخیرهٔ پایانی با SIGTERM/atexit** تا در ری‌استارت ۶ ساعته داده نپرد |
| `.github/workflows/bot-24x7.yml` | اجرای اصلی: job با `timeout-minutes: 355` + cron هر ۵ دقیقه + `concurrency group` |
| `.github/workflows/bot-watchdog.yml` | نگهبان ساعتی: اگر اجرای فعال/در صف نبود، خودش workflow را dispatch می‌کند |
| `requirements-gha.txt` | همان وابستگی‌ها بدون `static-ffmpeg` (روی رانر، ffmpeg هست) |
| `.env.example` | نمونهٔ متغیرها برای تست لوکال (کامیت نکن) |
| `Dockerfile` | برای مهاجرت بعدی به Oracle/Northflank (روی GHA لازم نیست) |
| `.gitignore` | `cookies.txt`, `.env`, `*.session`, `__pycache__` را بیرون نگه می‌دارد |

---

## 🚀 قدم ۱ — ریپوی عمومی بساز

1. در GitHub → **New repository** → اسم مثلاً `my-tg-bots` → **Public** → بدون README.
2. ⚠️ اگر ریپو **private** باشد، فقط ۲۰۰۰ دقیقه در ماه داری (≈۳۳ ساعت) → برای ۲۴/۷ کافی نیست.

## 🚀 قدم ۲ — فایل‌ها را آپلود کن

**روش ساده (وب):** در ریپو → `uploading an existing file` → همهٔ محتویات کیت را drag کن → Commit.
مطمئن شو که workflow دقیقاً در مسیر `.github/workflows/bot-24x7.yml` قرار بگیرد (نه در یک پوشهٔ دیگر).

**روش git:**
```bash
cd gha-kit
git init -b main
git add . && git commit -m "bot v26.5.0 + GitHub Actions 24/7"
git remote add origin https://github.com/<username>/my-tg-bots.git
git push -u origin main
```

> 🔒 `cookies.txt` (کوکی یوتیوب) را عمداً در کیت نگذاشتم و در `.gitignore` هم هست — در ریپوی عمومی لو می‌رفت. اگر لازم داری، به‌جای فایل، محتوایش را در Secret بگذار یا از `yt_cookie.json` در `DATA_REPO` استفاده کن.

## 🚀 قدم ۳ — Secrets و Variables

مسیر: ریپو → **Settings** → **Secrets and variables** → **Actions**

**تب Secrets** (مقادیر حساس):

| نام Secret | از کجا |
|---|---|
| `BOT_TOKEN` | @BotFather (ربات آچارفرانسه) |
| `ADMIN_IDS` | آیدی عددی مدیران، با کاما |
| `TG_API_ID` / `TG_API_HASH` | my.telegram.org → API development tools |
| `HF_TOKEN` | huggingface.co/settings/tokens — نوع **Write** (برای ساخت/کامیت مخزن داده) |
| `AI_API_KEY` | اختیاری |
| `DOR_BOT_TOKEN`, `DOR_ADMIN_ID`, `LICENSE_PRIVATE_KEY` | فقط اگر `ENABLE_BOT2=1` |
| `CF_TUNNEL_TOKEN` | اختیاری — فقط برای تانل Cloudflare (بخش آیفون) |

**تب Variables** (تنظیمات غیرحساس):

| نام Variable | مقدار |
|---|---|
| `DATA_REPO` | `<hf-username>/bot-data` — مخزن خصوصی dataset؛ در اولین اجرا خودکار ساخته می‌شود |
| `DOR_DATA_REPO` | `<hf-username>/dorbargardoon-data` |
| `ENABLE_BOT1` | `1` |
| `ENABLE_BOT2` | `0` (پیش‌فرض) — چون Actions پورت ورودی ندارد |
| `PUBLIC_URL` | فقط با تانل Cloudflare لازم است |

> 💡 هر دو ربات متغیر `DATA_REPO` را می‌خوانند؛ `run_all.py` برای ربات دوم از `DOR_DATA_REPO` استفاده می‌کند تا داده‌های دو ربات در یک مخزن قاطی نشوند (این تداخل در نسخهٔ اصلی وجود داشت).

## 🚀 قدم ۴ — اجرا

تب **Actions** → `bot-24x7` → **Run workflow** → شاخهٔ `main`.

در اولین اجرا در لاگ‌ها دنبال این خط‌ها بگرد:
```
[supervisor] شروع… bot1=True bot2=False PORT=7860
[persist] N فایل از مخزن برگشت          ← ذخیره‌سازی دائمی فعال است
🎬 ffmpeg: ✅ آماده                       ← فشرده‌سازی کار می‌کند
ffmpeg version … / telethon 1.x / yt-dlp 2026.x
```
و در HF باید یک dataset خصوصی به نام `bot-data` با commit «backup: bot data 💾» ساخته شود.

## ⛔ قدم ۵ — اسپیس HF را خاموش کن (خیلی مهم)

قبل از روشن کردن GHA، اسپیس فعلی را **Pause** کن (Settings → Pause this Space). وگرنه دو نسخهٔ هم‌زمان از یک ربات:
- در Bot API → خطای `409 Conflict` و از دست رفتن updateها
- در Telethon → تداخل روی یک session و ریسک `AUTH_KEY_DUPLICATE` / جریمهٔ تلگرام
- و پیام‌های تکراری برای کاربرها

---

## ⚠️ محدودیت‌های صادقانهٔ این مسیر

| محدودیت | اثرش روی ربات تو |
|---|---|
| سقف ۶ ساعت برای هر job | هر ~۵h۴۰m یک ری‌استارت چند ثانیه‌ای؛ پیام‌های آن لحظه ممکن است چند ثانیه دیرتر جواب بگیرند |
| **بدون پورت ورودی** | `/dns-query/<token>` و `/ios/…` دوربرگردون کار نمی‌کند → اشتراک آیفون صادر نمی‌شود (مگر با تانل Cloudflare، پایین‌تر) |
| دیسک ۱۴GB و `/tmp` موقت | فایل‌های بزرگ فشرده‌سازی باید زیر ~۱۰GB باشند؛ داده‌های دائمی فقط از راه `DATA_REPO` زنده می‌مانند |
| IP رانر = Azure و هر بار متفاوت | یوتیوب/اینستاگرام سخت‌گیرتر می‌شوند (مسیر Piped/LBRY و `yt_cookie` کمک می‌کند)؛ برای یوزربات تلگرام هم کمی ریسک |
| کد public می‌شود | Secrets لو نمی‌روند، ولی کد دیده و کپی می‌شود |
| ToS گیت‌هاب | Actions برای «هاست سرویس دائمی» طراحی نشده و fair-use throttle دارد → همیشه یک پلن B (اسپیس HF یا Oracle) نگه دار |

## 🍏 اگر بخش آیفون/DoH را هم لازم داری

دو راه داری:
1. **تانل Cloudflare روی همان رانر** (رایگان، ولی یک دامنه روی Cloudflare لازم داری):
   یک Named Tunnel بساز، توکنش را در Secret `CF_TUNNEL_TOKEN` بگذار، بلوک کامنت‌شدهٔ داخل `bot-24x7.yml` را فعال کن، `ENABLE_BOT2=1` و `PUBLIC_URL=https://bot.yourdomain.com` را در Variables ست کن. آدرس ثابت می‌ماند، پس پروفایل آیفون و DoH کار می‌کنند.
2. **دوربرگردون را جای دیگری بگذار** (اسپیس HF فعلی یا Oracle/Northflank) و آچارفرانسه را روی GitHub Actions — یعنی هر ربات جایی که برایش مناسب‌تر است.

## 🛠 عیب‌یابی سریع

| علامت | علت | راه‌حل |
|---|---|---|
| `409 Conflict` | نسخهٔ دوم ربات روشن است | اسپیس HF را Pause کن |
| `AUTH_KEY_DUPLICATE` / `SESSION_REVOKED` | دو پروسه با یک session | فقط یک نسخه روشن؛ `bot_session.json` در `DATA_REPO` باشد |
| `[persist] ❌ نه SPACE_ID هست نه DATA_REPO` | Variable ست نشده | `DATA_REPO` را در تب Variables بساز |
| `[persist] ❌ توکن HF نیست` | `HF_TOKEN` نیست یا Read-only است | توکن **Write** بساز |
| `ffmpeg: MISSING` | تصویر رانر عوض شده | خط `static-ffmpeg>=2.0` را به `requirements-gha.txt` برگردان |
| workflow زمان‌بندی‌شده اجرا نمی‌شود | cron فقط روی **شاخهٔ پیش‌فرض**؛ تأخیر تا ۱۵ دقیقه در بار بالا | `bot-watchdog.yml` را فعال نگه دار؛ یک بار دستی Run کن |
| `No module named 'gradio'` | `app.py` اجرا شده | روی GHA همیشه `run_all.py` را اجرا کن (workflow همین کار را می‌کند) |
| ربات بعد از چند ساعت خاموش شد | job به سقف ۶ ساعت خورد | طبیعی است؛ اجرای بعدی از صف جایگزین می‌شود (لاگ `پایان اجرا` را ببین) |

## ⏹ خاموش کردن کامل

تب **Actions** → `bot-24x7` → منوی `…` → **Disable workflow** (و همین کار برای `bot-watchdog`). تا وقتی غیرفعال است، cron اجرا نمی‌شود.

## 🔜 مهاجرت بعدی

اگر بعداً Oracle Cloud Always Free (۱۲GB رم + ۱۰TB ترافیک، با کارت) یا هر VPS دیگری گرفتی، همین پوشه با `Dockerfile` آماده است:
```bash
docker build -t mybots . && docker run -d --restart unless-stopped -p 7860:7860 --env-file .env mybots
```
