import os
import re
import logging
import tempfile
import cloudscraper  # جایگزین requests
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

user_files = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== توابع کمکی ====================
def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False

def get_filename_from_url(url: str, content_disposition: str | None = None) -> str:
    if content_disposition:
        fname = re.findall(r'filename[^;=\n]*=(([\'"]).*?\2|[^;\n]*)', content_disposition)
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
    await update.message.reply_text(
        "سلام 👋\n\n"
        "لینک فایل رو بفرست تا برات دانلود کنم.\n"
        "حداکثر حجم مجاز: ۴۵ مگابایت"
    )

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user_id = update.effective_user.id

    if not is_valid_url(url):
        await update.message.reply_text("لطفاً یک لینک معتبر (http یا https) بفرست.")
        return

    await update.message.reply_chat_action(ChatAction.TYPING)
    status_msg = await update.message.reply_text("در حال دانلود فایل... ⏳")

    # ایجاد یک نمونه از CloudScraper با تنظیمات بهینه و لاگ فعال
    # debug=True باعث می‌شود cloudscraper جزئیات مربوط به حل چالش کلادفلر را چاپ کند.
    scraper = cloudscraper.create_scraper(
        debug=True,  # فعال کردن لاگ دقیق برای عیب‌یابی
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        },
        interpreter='js2py',  # استفاده از مفسر جاوااسکریپت داخلی
        delay=5  # تاخیر برای حل چالش‌های پیچیده‌تر
    )

    try:
        # استفاده از scraper به جای requests
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": url,
        }

        logger.info("Starting download for URL: %s (User ID: %s)", url, user_id)

        with scraper.get(url, stream=True, timeout=40, headers=headers, allow_redirects=True) as r:

            logger.info("Response received. Status Code: %s", r.status_code)

            if r.status_code >= 400:
                # لاگ کردن تمام جزئیات خطا
                error_details = (
                    f"Status Code: {r.status_code}\n"
                    f"Reason: {r.reason}\n"
                    f"Final URL: {r.url}\n"
                    f"Headers: {dict(r.headers)}\n"
                    f"Response Body (first 500 chars): {r.text[:500] if r.text else 'No body'}"
                )
                logger.error("Download failed with detailed info:\n%s", error_details)

                await status_msg.edit_text(
                    f"❌ خطا در دانلود فایل\n\n"
                    f"کد خطا: `{r.status_code}`\n"
                    f"دلیل: {r.reason}\n\n"
                    f"جزئیات کامل در لاگ ثبت شد.",
                    parse_mode="Markdown"
                )
                return

            r.raise_for_status()

            content_length = r.headers.get("content-length")
            if content_length and int(content_length) > MAX_FILE_SIZE:
                logger.warning("File too large (Content-Length): %s bytes", content_length)
                await status_msg.edit_text("حجم فایل بیشتر از حد مجاز (۴۵ مگابایت) است.")
                return

            filename = get_filename_from_url(url, r.headers.get("content-disposition"))
            logger.info("Determined filename: %s", filename)

            if "." not in filename:
                content_type = r.headers.get("content-type", "")
                if "video" in content_type:
                    filename += ".mp4"
                elif "image" in content_type:
                    filename += ".jpg"
                else:
                    filename += ".bin"
                logger.info("Filename extended based on Content-Type '%s': %s", content_type, filename)

            temp_dir = tempfile.gettempdir()
            file_path = os.path.join(temp_dir, f"{user_id}_{filename}")

            downloaded = 0
            logger.info("Starting file write to: %s", file_path)
            with open(file_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > MAX_FILE_SIZE:
                            os.remove(file_path)
                            logger.warning("File too large during download. Aborted at %s bytes.", downloaded)
                            await status_msg.edit_text("حجم فایل بیش از حد مجاز شد.")
                            return

        logger.info("Download completed successfully. Size: %s bytes", downloaded)

        user_files[user_id] = {
            "path": file_path,
            "filename": filename
        }

        keyboard = [
            [
                InlineKeyboardButton("✅ آپلود کن", callback_data="upload"),
                InlineKeyboardButton("❌ لغو", callback_data="cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        await status_msg.edit_text(
            f"✅ فایل دانلود شد!\n\n"
            f"نام: `{filename}`\n"
            f"حجم: {size_mb:.2f} مگابایت\n\n"
            f"می‌خوای برات آپلود کنم؟",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    except Exception as e:  # گرفتن تمام خطاها برای لاگ دقیق‌تر
        logger.exception("Unexpected error during download for URL: %s", url)
        await status_msg.edit_text(
            f"❌ خطا در دانلود فایل\n\n"
            f"`{type(e).__name__}`: {str(e)[:150]}",
            parse_mode="Markdown"
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "cancel":
        if user_id in user_files:
            try:
                os.remove(user_files[user_id]["path"])
            except Exception:
                pass
            del user_files[user_id]

        await query.edit_message_text("❌ عملیات لغو شد.")
        return

    if data == "upload":
        if user_id not in user_files:
            await query.edit_message_text("فایلی برای آپلود پیدا نشد. دوباره لینک بفرست.")
            return

        file_info = user_files[user_id]
        file_path = file_info["path"]
        filename = file_info["filename"]

        try:
            await query.edit_message_text("در حال آپلود فایل... ⏳")

            with open(file_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    filename=filename,
                    caption=f"✅ فایل آپلود شد\n\nنام: `{filename}`",
                    parse_mode="Markdown"
                )

            await query.edit_message_text("✅ فایل با موفقیت آپلود شد.")

        except Exception as e:
            logger.exception("Error while uploading file:")
            await query.edit_message_text(f"❌ خطا در آپلود فایل:\n`{str(e)[:150]}`", parse_mode="Markdown")

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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("ربات شروع به کار کرد...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
