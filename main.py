import os
import re
import logging
import tempfile
from urllib.parse import urlparse, unquote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ==================== تنظیمات ====================
BOT_TOKEN = "8758704422:AAGTexKCiwc1ASoolDYb39NFBTEgWMbo9ZM"
MAX_FILE_SIZE = 45 * 1024 * 1024  # ۴۵ مگابایت

user_files = {}
pending_urls = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================== ایمپورت cf-killer ====================
try:
    from cf_killer import CFPageFetcher
    CF_KILLER_AVAILABLE = True
    logger.info("✅ cf-killer با موفقیت بارگذاری شد")
except ImportError as e:
    CF_KILLER_AVAILABLE = False
    logger.warning("❌ cf-killer در دسترس نیست: %s", e)


# ==================== توابع کمکی ====================
def is_valid_url(url: str) -> bool:
    try:
        r = urlparse(url)
        return all([r.scheme in ("http", "https"), r.netloc])
    except Exception:
        return False


def get_filename_from_url(url: str, cd: str | None = None) -> str:
    if cd:
        m = re.findall(r'filename[^;=\n]*=(([\'"]).*?\2|[^;\n]*)', cd)
        if m:
            return unquote(m[0][0].strip("'\""))
    fn = os.path.basename(urlparse(url).path)
    return unquote(fn) if fn else "downloaded_file"


# ==================== هندلرها ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\n\n"
        "لینک فایل رو بفرست تا برات دانلود کنم.\n"
        "از موتور cf-killer برای عبور از Cloudflare استفاده می‌شود.\n\n"
        f"حداکثر حجم: ۴۵ مگابایت"
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    uid = update.effective_user.id

    if not is_valid_url(url):
        await update.message.reply_text("لینک معتبر (http/https) بفرست.")
        return

    if not CF_KILLER_AVAILABLE:
        await update.message.reply_text(
            "❌ cf-killer نصب نیست.\n"
            "با دستور /methods وضعیت را بررسی کن."
        )
        return

    # پاک‌سازی فایل قبلی
    if uid in user_files:
        try:
            os.remove(user_files[uid]["path"])
        except Exception:
            pass
        del user_files[uid]

    pending_urls[uid] = url
    logger.info("لینک از کاربر %s: %s", uid, url)

    keyboard = [[
        InlineKeyboardButton("🚀 دانلود با cf-killer", callback_data="dl_cf_killer"),
        InlineKeyboardButton("❌ لغو", callback_data="cancel"),
    ]]
    await update.message.reply_text(
        f"🔗 لینک دریافت شد.\n\nآماده دانلود با cf-killer هستی؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def do_download(url: str, uid: int, edit_func):
    """دانلود با cf-killer"""
    if not CF_KILLER_AVAILABLE:
        await edit_func("❌ cf-killer در دسترس نیست.")
        return

    logger.info("=" * 60)
    logger.info("DOWNLOAD (cf-killer) | User: %s | URL: %s", uid, url)
    logger.info("=" * 60)

    await edit_func("⏳ در حال دانلود با cf-killer (عبور از Cloudflare)...")

    temp_dir = tempfile.gettempdir()
    filename = get_filename_from_url(url)
    if "." not in filename:
        filename += ".bin"
    file_path = os.path.join(temp_dir, f"{uid}_cfkiller_{filename}")

    try:
        # cf-killer: استفاده از CFPageFetcher برای دانلود فایل
        async with CFPageFetcher(headless=True) as fetcher:
            logger.info("[cf-killer] شروع download_file()...")
            await fetcher.download_file(url, file_path)

        # بررسی فایل دانلودشده
        if not os.path.exists(file_path):
            raise FileNotFoundError("فایل پس از دانلود پیدا نشد.")

        file_size = os.path.getsize(file_path)
        logger.info("[cf-killer] موفق: %s بایت (%.2f مگابایت)",
                     file_size, file_size / 1024 / 1024)

        if file_size > MAX_FILE_SIZE:
            os.remove(file_path)
            await edit_func("حجم فایل از حد مجاز (۴۵ مگابایت) بیشتر است.")
            return

        user_files[uid] = {
            "path": file_path,
            "filename": filename,
            "method": "cf-killer",
        }

        keyboard = [[
            InlineKeyboardButton("✅ آپلود در تلگرام", callback_data="upload"),
            InlineKeyboardButton("❌ لغو", callback_data="cancel"),
        ]]
        size_mb = file_size / (1024 * 1024)
        await edit_func(
            f"✅ دانلود موفق!\n\n"
            f"نام: <code>{filename}</code>\n"
            f"حجم: {size_mb:.2f} MB\n"
            f"روش: cf-killer\n\nآپلود شود؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception("[cf-killer] خطا در دانلود")
        await edit_func(
            f"❌ خطا در دانلود (cf-killer)\n\n"
            f"<code>{type(e).__name__}</code>: {str(e)[:200]}\n\n"
            f"جزئیات در لاگ ثبت شد.",
            parse_mode="HTML",
        )
        # پاک‌سازی فایل ناقص
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    if data == "cancel":
        if uid in user_files:
            try:
                os.remove(user_files[uid]["path"])
            except Exception:
                pass
            del user_files[uid]
        pending_urls.pop(uid, None)
        await q.edit_message_text("❌ لغو شد.")
        return

    if data == "dl_cf_killer":
        url = pending_urls.pop(uid, None)
        if not url:
            await q.edit_message_text("لینک منقضی شده. دوباره بفرست.")
            return

        async def edit_func(text, **kw):
            return await q.edit_message_text(text, **kw)

        await do_download(url, uid, edit_func)
        return

    if data == "upload":
        if uid not in user_files:
            await q.edit_message_text("فایلی پیدا نشد.")
            return
        fi = user_files[uid]
        try:
            await q.edit_message_text("در حال آپلود... ⏳")
            with open(fi["path"], "rb") as f:
                await context.bot.send_document(
                    chat_id=q.message.chat_id,
                    document=f,
                    filename=fi["filename"],
                    caption=f"✅ آپلود شد\nنام: {fi['filename']}\nروش: {fi.get('method','?')}",
                )
            await q.edit_message_text("✅ آپلود موفق.")
        except Exception as e:
            logger.exception("خطا در آپلود:")
            await q.edit_message_text(
                f"❌ خطا در آپلود:\n<code>{str(e)[:200]}</code>",
                parse_mode="HTML",
            )
        finally:
            try:
                os.remove(fi["path"])
            except Exception:
                pass
            user_files.pop(uid, None)


# ==================== اجرا ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("ربات شروع شد. cf-killer در دسترس: %s", CF_KILLER_AVAILABLE)
    print("ربات شروع شد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
