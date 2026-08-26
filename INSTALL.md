# نصب ربات دانلودر روی هر سرور

این نسخه‌ی تمیز ربات است (بدون توکن/کوکی/دیتابیس شخصی). روی هر سرور لینوکسی
(Debian/Ubuntu) با یک دستور بالا می‌آید.

---

## پیش‌نیاز
- یک سرور لینوکس (Debian/Ubuntu ۶۴بیتی) با دسترسی root یا sudo
- توکن ربات از [@BotFather](https://t.me/BotFather) (دستور `/newbot`)
- آیدی عددی خودت از [@userinfobot](https://t.me/userinfobot)

---

## نصب سریع (۴ قدم)

```bash
# 1) اکسترکت
tar xzf mydlplus-bot-portable_*.tar.gz
cd mydlplus-bot

# 2) نصب همه‌ی وابستگی‌ها (ffmpeg، venv پایتون، Node، سرور PO-token، WARP)
#    مسیرها را می‌توانی با متغیر عوض کنی؛ پیش‌فرض زیر /opt نصب می‌کند.
bash bootstrap.sh

# 3) فایل تنظیمات را بساز و توکن را بگذار
cp .env.example .env
nano .env            # BOT_TOKEN و ADMIN_IDS را پر کن

# 4) اجرا
./run.sh start
./run.sh status      # باید supervisor/bot/pot را «running» نشان بدهد
```

تمام. حالا در تلگرام به ربات یک لینک بفرست.

---

## اگر IP سرور توسط یوتیوب/اینستاگرام مسدود است

خیلی از سرورهای ابری IPشان روی یوتیوب/اینستاگرام بلاک است. `bootstrap.sh` پروکسی
**WARP userspace** را هم نصب می‌کند (بدون نیاز به root یا tun). فعالش کن:

```bash
bash /data/warp/warp.sh start     # یا هر مسیری که WARP_DIR گذاشتی
bash /data/warp/warp.sh status    # باید 'egress: UP' بدهد
```

بعد در `.env` این دو خط را از حالت کامنت دربیاور:

```
PROXY_YOUTUBE=socks5://127.0.0.1:25366
PROXY_INSTAGRAM=socks5://127.0.0.1:25366
```

و `./run.sh restart`.

> اگر IP سرورت آزاد است (مثلاً خارج) اصلاً به WARP نیازی نداری — این دو خط را
> کامنت بگذار تا مستقیم برود.

---

## کوکی‌ها (اختیاری، برای محتوای لاگین‌خواه)

- استوری/پست خصوصی اینستاگرام و بعضی ویدیوهای سن‌دار یوتیوب کوکی لازم دارند.
- در تلگرام به‌عنوان ادمین `/cookies` بزن، بعد یک فایل `cookies.txt` (خروجی افزونه‌ی
  cookie مرورگر، فرمت Netscape) آپلود کن. خودِ ربات نصبش می‌کند.

## دانلود استوری تلگرام (اختیاری)

توکن ربات اجازه‌ی استوری ندارد؛ نیاز به یک اکانت کاربری است. در پیوی `/tglogin`
بزن و مرحله‌به‌مرحله شماره + کد + (در صورت وجود) رمز دومرحله‌ای را بده. توصیه:
یک اکانت فرعی، نه اکانت اصلی‌ات.

---

## کنترل سرویس

```bash
./run.sh start      # اجرا (detached، با سوپروایزر خودکار ری‌استارت)
./run.sh stop       # توقف
./run.sh restart
./run.sh status     # وضعیت + آخرین لاگ‌ها
./run.sh logs       # دنبال‌کردن زنده‌ی لاگ
```

## زنده‌ماندن بعد از ری‌استارت سرور

سوپروایزر داخلی `run.sh` اگر ربات بمیرد آن را بلند می‌کند، ولی خودش با ریبوت سرور
اجرا نمی‌شود. برای اجرای خودکار بعد از بوت، `watchdog.sh` را روی cron بگذار:

```bash
# هر دقیقه چک می‌کند و اگر ربات پایین بود بالا می‌آورد، + یک‌بار موقع بوت
( crontab -l 2>/dev/null; echo "* * * * * cd $(pwd) && bash watchdog.sh >/dev/null 2>&1"; \
  echo "@reboot cd $(pwd) && bash bootstrap.sh && ./run.sh start >/dev/null 2>&1" ) | crontab -
```

> اگر سرورت systemd دارد (اکثر VPSها)، می‌توانی به‌جای cron یک سرویس systemd هم
> بسازی — ولی cron ساده‌تر و قابل‌حمل‌تر است.

---

## عیب‌یابی

- **`! .env is missing`** → `cp .env.example .env` و توکن را بگذار.
- **ربات بالا می‌آید ولی یوتیوب/اینستا خطا می‌دهد** → IP بلاک است؛ WARP را روشن کن
  (بخش بالا).
- **`pot server ... NOT responding`** → Node یا سرور PO-token ساخته نشده؛
  `bash bootstrap.sh` را دوباره اجرا کن.
- **فایل‌های بزرگ‌تر از ۵۰MB نمی‌روند** → عادی است اگر MTProto خطا بدهد؛ پیش‌فرض از
  api_id عمومی تلگرام‌دسکتاپ استفاده می‌کند تا سقف ۲GB برود. لاگ را ببین.
- **پورت PO-token اشغال است** → `POT_PORT=4417 ./run.sh restart`.

---

## مسیرهای قابل‌تنظیم (متغیر محیطی، اختیاری)

`bootstrap.sh` و `run.sh` این پیش‌فرض‌ها را دارند؛ با export قبل از اجرا عوض کن:

| متغیر | پیش‌فرض | توضیح |
|-------|---------|-------|
| `VENV` | `/opt/botvenv` | venv پایتون |
| `NODE` | `/opt/node22/bin/node` | باینری Node |
| `POT_DIR` | `/opt/bgutil-pot/server` | سرور PO-token |
| `POT_PORT` | `4416` | پورت PO-token |
| `WARP_DIR` | `/data/warp` | پوشه‌ی WARP |

مثال روی سروری که `/data` ندارد:

```bash
export WARP_DIR="$HOME/warp" VENV="$HOME/botvenv"
bash bootstrap.sh
./run.sh start
```
