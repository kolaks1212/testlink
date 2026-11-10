import os
import re
import logging
import tempfile
import requests
from urllib.parse import urlparse, unquote
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatAction

# لاگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# حداکثر سایز فایل (حدود ۴۵ مگابایت - محدودیت تلگرام برای بات‌ها معمولاً ۵۰MB است)
MAX_FILE_SIZE = 45 * 1024 * 1024

# ذخیره موقت مسیر فایل‌ها (user_id → مسیر فایل)
user_files = {}

def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except:
        return False

def get_filename_from_url(url: str, content_disposition: str = None) -> str:
    # اول از Content-Disposition سعی می‌کنیم
    if content_disposition:
        fname = re.findall(r'filename[^;=\n]*=(([\'"]).*?\2|[^;\n]*)', content_disposition)
        if fname:
            return unquote(fname[0][0].strip("'\"") )

    # از خود URL
    path = urlparse(url).path
    filename = unquote(Path(path).name)
    if filename and "." in filename:
        return filename

    return "downloaded_file"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\n"
        "لینک مستقیم فایل یا ویدیو رو برام بفرست.\n"
        "من دانلودش می‌کنم و اگر بخوای برات به تلگرام آپلود می‌کنم."
    )

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user_id = update.effective_user.id

    if not is_valid_url(url):
        await update.message.reply_text("لطفاً یک لینک معتبر (http یا https) بفرست.")
        return

    await update.message.reply_chat_action(ChatAction.TYPING)
    status_msg = await update.message.reply_text("در حال دانلود فایل... ⏳")

    try:
        # دانلود با استریم
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        with requests.get(url, stream=True, timeout=30, headers=headers) as r:
            r.raise_for_status()

            content_length = r.headers.get("content-length")
            if content_length and int(content_length) > MAX_FILE_SIZE:
                await status_msg.edit_text("حجم فایل بیشتر از حد مجاز (۴۵ مگابایت) است.")
                return

            filename = get_filename_from_url(url, r.headers.get("content-disposition"))
            # پسوند رو از content-type حدس بزنیم اگر نداشت
            if "." not in filename:
                content_type = r.headers.get("content-type", "")
                if "video" in content_type:
                    filename += ".mp4"
                elif "image" in content_type:
                    filename += ".jpg"
                else:
                    filename += ".bin"

            # ذخیره در فایل موقت
            temp_dir = tempfile.gettempdir()
            file_path = os.path.join(temp_dir, f"{user_id}_{filename}")

            downloaded = 0
            with open(file_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > MAX_FILE_SIZE:
                            os.remove(file_path)
                            await status_msg.edit_text("حجم فایل بیش از حد مجاز شد.")
                            return

        # ذخیره مسیر فایل برای کاربر
        user_files[user_id] = {
            "path": file_path,
            "filename": filename
        }

        # دکمه تأیید
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

    except requests.exceptions.RequestException as e:
        logger.error(f"Download error: {e}")
        await status_msg.edit_text("خطا در دانلود فایل. لینک رو چک کن یا دوباره امتحان کن.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await status_msg.edit_text("خطای غیرمنتظره رخ داد.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if user_id not in user_files:
        await query.edit_message_text("فایلی پیدا نشد. دوباره لینک بفرست.")
        return

    file_info = user_files[user_id]
    file_path = file_info["path"]
    filename = file_info["filename"]

    if data == "cancel":
        # پاک کردن فایل
        if os.path.exists(file_path):
            os.remove(file_path)
        del user_files[user_id]
        await query.edit_message_text("لغو شد. فایل پاک شد.")
        return

    if data == "upload":
        await query.edit_message_text("در حال آپلود فایل به تلگرام... 📤")

        try:
            # تشخیص نوع فایل
            ext = Path(filename).suffix.lower()
            is_video = ext in [".mp4", ".mkv", ".avi", ".mov", ".webm"]
            is_photo = ext in [".jpg", ".jpeg", ".png", ".webp"]

            with open(file_path, "rb") as f:
                if is_video:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        filename=filename,
                        supports_streaming=True,
                        caption=f"📁 {filename}"
                    )
                elif is_photo:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=f,
                        filename=filename,
                        caption=f"📁 {filename}"
                    )
                else:
                    await context.bot.send_document(
                        chat_id=query.message.chat_id,
                        document=f,
                        filename=filename,
                        caption=f"📁 {filename}"
                    )

            await query.edit_message_text("✅ فایل با موفقیت آپلود شد!")

        except Exception as e:
            logger.error(f"Upload error: {e}")
            await query.edit_message_text("خطا در آپلود فایل به تلگرام.")
        finally:
            # پاک کردن فایل
            if os.path.exists(file_path):
                os.remove(file_path)
            if user_id in user_files:
                del user_files[user_id]

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN environment variable is not set!")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()