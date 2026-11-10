import os
import re
import logging
import tempfile
from urllib.parse import urlparse, unquote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================== تنظیمات ====================
BOT_TOKEN = "8758704422:AAGTexKCiwc1ASoolDYb39NFBTEgWMbo9ZM"
MAX_FILE_SIZE = 45 * 1024 * 1024  # ۴۵ مگابایت

user_files = {}      # user_id -> {path, filename, method}
pending_urls = {}    # user_id -> url

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================== ایمپورت پکیج‌های دانلود (اختیاری) ====================
METHODS = {}

# --- روش ۱: curl_cffi ---
try:
    from curl_cffi import requests as cffi_requests
    METHODS["curl_cffi"] = {
        "name": "1️⃣ curl_cffi (TLS Impersonation)",
        "available": True,
        "error": None,
        "install": "",
    }
    logger.info("✅ curl_cffi loaded successfully")
except ImportError as e:
    cffi_requests = None
    METHODS["curl_cffi"] = {
        "name": "1️⃣ curl_cffi",
        "available": False,
        "error": str(e),
        "install": "pip install curl-cffi",
    }
    logger.warning("❌ curl_cffi not available: %s", e)

# --- روش ۲: ai-cloudscraper (hybrid) ---
try:
    import cloudscraper as ai_cloudscraper
    METHODS["ai_cloudscraper"] = {
        "name": "2️⃣ ai-cloudscraper (Hybrid + Playwright)",
        "available": True,
        "error": None,
        "install": "",
    }
    logger.info("✅ ai-cloudscraper loaded successfully")
except ImportError as e:
    ai_cloudscraper = None
    METHODS["ai_cloudscraper"] = {
        "name": "2️⃣ ai-cloudscraper (Hybrid)",
        "available": False,
        "error": str(e),
        "install": "pip install ai-cloudscraper && playwright install chromium",
    }
    logger.warning("❌ ai-cloudscraper not available: %s", e)

# --- روش ۳: cloudscraper-turnstile ---
try:
    import cloudscraper_turnstile
    METHODS["turnstile"] = {
        "name": "3️⃣ cloudscraper-turnstile",
        "available": True,
        "error": None,
        "install": "",
    }
    logger.info("✅ cloudscraper-turnstile loaded successfully")
except ImportError as e:
    cloudscraper_turnstile = None
    METHODS["turnstile"] = {
        "name": "3️⃣ cloudscraper-turnstile",
        "available": False,
        "error": str(e),
        "install": "pip install cloudscraper-turnstile + تنظیم PEAK_API_KEY",
    }
    logger.warning("❌ cloudscraper-turnstile not available: %s", e)


# ==================== توابع کمکی ====================
def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


def get_filename_from_url(url: str, content_disposition: str | None = None) -> str:
    if content_disposition:
        fname = re.findall(
            r'filename[^;=\n]*=(([\'"]).*?\2|[^;\n]*)', content_disposition
        )
        if fname:
            filename = fname[0][0].strip("'\"")
            return unquote(filename)

    path = urlparse(url).path
    filename = os.path.basename(path)
    if filename:
        return unquote(filename)
    return "downloaded_file"


# ==================== هندلرها ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    available = [m["name"] for m in METHODS.values() if m["available"]]
    await update.message.reply_text(
        "سلام 👋\n\n"
        "لینک فایل رو بفرست تا برات دانلود کنم.\n"
        "بعد از ارسال لینک، می‌تونی روش دانلود رو انتخاب کنی.\n\n"
        f"حداکثر حجم: ۴۵ مگابایت\n\n"
        f"روش‌های فعلاً فعال: {len(available)} از {len(METHODS)}\n"
        "برای دیدن وضعیت کامل: /methods"
    )


async def methods_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📋 <b>وضعیت روش‌های دانلود:</b>\n"]
    for key, info in METHODS.items():
        status = "✅" if info["available"] else "❌"
        lines.append(f"{status} {info['name']}")
        if not info["available"]:
            lines.append(f"    <i>نصب: {info['install']}</i>")
    lines.append("\n⚠️ نکته: پکیج‌های <code>ai-cloudscraper</code> و <code>cloudscraper-turnstile</code>")
    lines.append("همزمان قابل نصب نیستند. برای تست هر روش، requirements.txt را تغییر بده.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user_id = update.effective_user.id

    if not is_valid_url(url):
        await update.message.reply_text(
            "لطفاً یک لینک معتبر (http یا https) بفرست."
        )
        return

    # پاک‌سازی فایل موقت قبلی این کاربر
    if user_id in user_files:
        try:
            os.remove(user_files[user_id]["path"])
        except Exception:
            pass
        del user_files[user_id]

    pending_urls[user_id] = url
    logger.info("URL received from user %s: %s", user_id, url)

    # ساخت کیبورد انتخاب روش
    keyboard = []
    for key, info in METHODS.items():
        if info["available"]:
            keyboard.append([
                InlineKeyboardButton(
                    f"✅ {info['name']}",
                    callback_data=f"dl_{key}",
                )
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(
                    f"⛔ {info['name']} (نصب نیست)",
                    callback_data=f"info_{key}",
                )
            ])
    keyboard.append([InlineKeyboardButton("❌ لغو", callback_data="cancel")])

    short_url = url if len(url) <= 80 else url[:77] + "..."
    await update.message.reply_text(
        f"🔗 لینک دریافت شد:\n<code>{short_url}</code>\n\n"
        f"روش دانلود را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def do_download(url: str, user_id: int, edit_func, method_key: str):
    """دانلود با روش انتخاب‌شده. edit_func یک تابع async برای ویرایش پیام است."""
    info = METHODS.get(method_key)
    if not info or not info["available"]:
        await edit_func(f"❌ روش <code>{method_key}</code> در دسترس نیست.")
        return

    method_name = info["name"]

    logger.info("=" * 60)
    logger.info("DOWNLOAD START")
    logger.info("  User ID  : %s", user_id)
    logger.info("  Method   : %s (%s)", method_key, method_name)
    logger.info("  URL      : %s", url)
    logger.info("=" * 60)

    await edit_func(
        f"⏳ در حال دانلود...\n\n"
        f"روش: {method_name}",
    )

    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": url,
    }

    response = None
    try:
        # ============ ارسال درخواست بر اساس روش ============
        if method_key == "curl_cffi":
            logger.info("[curl_cffi] Sending GET with impersonate='chrome'")
            response = cffi_requests.get(
                url,
                impersonate="chrome",
                stream=True,
                timeout=40,
                headers=headers,
                allow_redirects=True,
            )

        elif method_key == "ai_cloudscraper":
            logger.info("[ai-cloudscraper] Creating hybrid scraper (Playwright fallback)")
            scraper = ai_cloudscraper.create_scraper(
                debug=True,
                interpreter="hybrid",
                browser={
                    "browser": "chrome",
                    "platform": "windows",
                    "desktop": True,
                },
            )
            logger.info("[ai-cloudscraper] Sending GET")
            response = scraper.get(
                url,
                stream=True,
                timeout=40,
                headers=headers,
                allow_redirects=True,
            )

        elif method_key == "turnstile":
            logger.info("[turnstile] Creating cloudscraper_turnstile scraper")
            scraper = cloudscraper_turnstile.create_scraper()
            logger.info("[turnstile] Sending GET")
            response = scraper.get(
                url,
                stream=True,
                timeout=40,
                headers=headers,
                allow_redirects=True,
            )
        else:
            raise RuntimeError(f"Unknown method: {method_key}")

        # ============ تحلیل پاسخ ============
        status_code = response.status_code
        reason = getattr(response, "reason", "N/A")
        final_url = getattr(response, "url", url)
        resp_headers = dict(response.headers)
        cf_mitigated = resp_headers.get("Cf-Mitigated", "").lower()

        logger.info("[%s] Status    : %s %s", method_key, status_code, reason)
        logger.info("[%s] Final URL : %s", method_key, final_url)
        logger.info("[%s] Cf-Mitigated: %s", method_key, cf_mitigated or "none")
        logger.info("[%s] Response Headers: %s", method_key, resp_headers)

        # ============ خطا ============
        if status_code >= 400:
            body_snippet = ""
            try:
                body_snippet = response.text[:500]
            except Exception as e:
                logger.warning("[%s] Could not read body: %s", method_key, e)

            logger.error(
                "[%s] DOWNLOAD FAILED\n"
                "  Status        : %s\n"
                "  Reason        : %s\n"
                "  Final URL     : %s\n"
                "  Cf-Mitigated  : %s\n"
                "  Body snippet  : %s",
                method_key,
                status_code,
                reason,
                final_url,
                cf_mitigated or "none",
                body_snippet[:300],
            )

            hint = ""
            if cf_mitigated == "challenge":
                hint = (
                    "\n\n💡 سرور صفحه‌ی چالش Cloudflare فرستاده "
                    "(Cf-Mitigated: challenge). این روش نتوانست آن را حل کند."
                )

            await edit_func(
                f"❌ خطا در دانلود\n\n"
                f"روش: {method_name}\n"
                f"کد: <code>{status_code}</code>\n"
                f"دلیل: {reason}\n"
                f"Cf-Mitigated: <code>{cf_mitigated or 'none'}</code>"
                f"{hint}\n\n"
                f"جزئیات کامل در لاگ ثبت شد.",
                parse_mode="HTML",
            )
            return

        # ============ بررسی حجم (Content-Length) ============
        content_length = resp_headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > MAX_FILE_SIZE:
                    logger.warning(
                        "[%s] File too large: %.2f MB", method_key, size / 1024 / 1024
                    )
                    await edit_func(
                        f"حجم فایل ({size / 1024 / 1024:.1f} MB) بیش از حد مجاز است."
                    )
                    return
            except ValueError:
                pass

        # ============ تعیین نام فایل ============
        filename = get_filename_from_url(url, resp_headers.get("content-disposition"))
        if "." not in filename:
            ct = resp_headers.get("content-type", "")
            if "video" in ct:
                filename += ".mp4"
            elif "image" in ct:
                filename += ".jpg"
            else:
                filename += ".bin"
        logger.info("[%s] Filename: %s", method_key, filename)

        # ============ ذخیره‌سازی ============
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, f"{user_id}_{filename}")
        logger.info("[%s] Saving to: %s", method_key, file_path)

        downloaded = 0
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > MAX_FILE_SIZE:
                        os.remove(file_path)
                        logger.warning(
                            "[%s] Exceeded size during download at %s bytes",
                            method_key,
                            downloaded,
                        )
                        await edit_func("حجم فایل بیش از حد مجاز شد.")
                        return

        logger.info(
            "[%s] Download complete: %s bytes (%.2f MB)",
            method_key,
            downloaded,
            downloaded / 1024 / 1024,
        )

        user_files[user_id] = {
            "path": file_path,
            "filename": filename,
            "method": method_name,
        }

        keyboard = [[
            InlineKeyboardButton("✅ آپلود کن", callback_data="upload"),
            InlineKeyboardButton("❌ لغو", callback_data="cancel"),
        ]]

        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        await edit_func(
            f"✅ دانلود موفق!\n\n"
            f"روش: {method_name}\n"
            f"نام: <code>{filename}</code>\n"
            f"حجم: {size_mb:.2f} مگابایت\n\n"
            f"می‌خوای برات آپلود کنم؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception(
            "[%s] EXCEPTION during download for URL: %s", method_key, url
        )
        await edit_func(
            f"❌ خطا (روش: {method_name})\n\n"
            f"<code>{type(e).__name__}</code>: {str(e)[:200]}\n\n"
            f"جزئیات در لاگ ثبت شد.",
            parse_mode="HTML",
        )

    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    # --- اطلاعات روش غیرفعال ---
    if data.startswith("info_"):
        key = data[len("info_"):]
        info = METHODS.get(key)
        if info:
            await query.answer(
                f"این روش نصب نیست.\n\nنصب:\n{info['install']}",
                show_alert=True,
            )
        return

    # --- لغو ---
    if data == "cancel":
        if user_id in user_files:
            try:
                os.remove(user_files[user_id]["path"])
            except Exception:
                pass
            del user_files[user_id]
        if user_id in pending_urls:
            del pending_urls[user_id]
        await query.edit_message_text("❌ عملیات لغو شد.")
        return

    # --- انتخاب روش دانلود ---
    if data.startswith("dl_"):
        method_key = data[len("dl_"):]
        url = pending_urls.pop(user_id, None)
        if not url:
            await query.edit_message_text(
                "لینک منقضی شده یا پیدا نشد. دوباره لینک بفرست."
            )
            return

        async def edit_func(text, **kwargs):
            return await query.edit_message_text(text, **kwargs)

        await do_download(url, user_id, edit_func, method_key)
        return

    # --- آپلود ---
    if data == "upload":
        if user_id not in user_files:
            await query.edit_message_text("فایلی برای آپلود پیدا نشد. دوباره لینک بفرست.")
            return

        file_info = user_files[user_id]
        file_path = file_info["path"]
        filename = file_info["filename"]
        method_name = file_info.get("method", "?")

        try:
            await query.edit_message_text("در حال آپلود فایل... ⏳")
            logger.info("Uploading file for user %s: %s (%s)", user_id, filename, method_name)

            with open(file_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    filename=filename,
                    caption=(
                        f"✅ فایل آپلود شد\n\n"
                        f"نام: {filename}\n"
                        f"روش دانلود: {method_name}"
                    ),
                )

            await query.edit_message_text("✅ فایل با موفقیت آپلود شد.")
            logger.info("Upload successful for user %s", user_id)

        except Exception as e:
            logger.exception("Error while uploading file:")
            await query.edit_message_text(
                f"❌ خطا در آپلود فایل:\n<code>{str(e)[:200]}</code>",
                parse_mode="HTML",
            )

        finally:
            try:
                os.remove(file_path)
            except Exception:
                pass
            if user_id in user_files:
                del user_files[user_id]


# ==================== اجرای ربات ====================
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("methods", methods_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started. Available methods: %s",
                [k for k, v in METHODS.items() if v["available"]])
    print("ربات شروع به کار کرد...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
