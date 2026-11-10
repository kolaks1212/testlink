# Telegram Bulk File & Image Downloader Bot

ربات تلگرامی پیشرفته برای استفاده از Actor  
**Bulk Image & File Downloader** (`Z2samcXlPvSQy1oTq`) روی پلتفرم Apify.

## قابلیت‌ها

- دریافت لیست URL از کاربر
- اجرای Actor روی Apify
- مانیتورینگ وضعیت به صورت زنده
- **ارسال مستقیم فایل ZIP به تلگرام** (تا ۴۹ مگابایت)
- نمایش خلاصه در صورت بزرگ بودن فایل
- دستورات `/start`, `/download`, `/status`, `/help`, `/cancel`

## نصب و اجرا روی Railway

### ۱. ساخت ربات تلگرام
1. به [@BotFather](https://t.me/BotFather) پیام بده
2. دستور `/newbot` را بزن و توکن را کپی کن

### ۲. گرفتن توکن Apify
1. وارد [console.apify.com](https://console.apify.com) شو
2. از Settings → Integrations توکن API را کپی کن

### ۳. دیپلوی روی Railway
1. این پروژه را روی GitHub آپلود کن
2. در [Railway.app](https://railway.app) پروژه جدید بساز → Deploy from GitHub
3. متغیرهای محیطی زیر را اضافه کن:

| متغیر | توضیح |
|-------|------|
| `TELEGRAM_BOT_TOKEN` | توکن ربات تلگرام |
| `APIFY_TOKEN` | توکن API آپیفای |
| `ADMIN_IDS` | (اختیاری) آیدی عددی ادمین‌ها با کاما |

4. Deploy کن.

## متغیرهای محیطی

```env
TELEGRAM_BOT_TOKEN=...
APIFY_TOKEN=...
ADMIN_IDS=123456789
```

## محدودیت‌ها

- حداکثر ۳۰۰ URL در هر درخواست
- حداکثر حجم فایل ارسالی به تلگرام: ۴۹ مگابایت
- اگر فایل بزرگ‌تر باشد، لینک‌های Dataset و Key-Value Store نمایش داده می‌شود

## ساختار فایل‌ها

```
telegram-bulk-downloader/
├── bot.py              # کد اصلی ربات
├── apify_manager.py    # ارتباط با Apify API
├── config.py           # تنظیمات
├── requirements.txt
├── Dockerfile
├── railway.toml
├── .env.example
└── README.md
```

## توسعه بیشتر (پیشنهادی)

- پشتیبانی از آپلود فایل CSV
- صف‌بندی درخواست‌ها با Redis
- دیتابیس برای تاریخچه کاربران
- انتخاب گزینه‌های Actor (imagesOnly و ...)
- پرداخت درون‌رباتی

---

ساخته شده برای Actor: `chris_elder/bulk-file-downloader`
