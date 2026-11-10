import asyncio
import logging
import os
from pathlib import Path

from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from telegram.constants import ParseMode

from apify_manager import ApifyManager
from config import (
    TELEGRAM_BOT_TOKEN,
    ADMIN_IDS,
    MAX_URLS_PER_REQUEST,
    MAX_FILE_SIZE_MB,
    TEMP_DIR,
)

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

apify = ApifyManager()

# Conversation states
WAITING_URLS = 1

# In-memory storage (for production use Redis or DB)
user_runs: dict[int, str] = {}



def get_attr(obj, key, default=None):
    """Safely get attribute from Run object or dict."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

# -------------------- Helpers --------------------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def ensure_temp_dir():
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)


# -------------------- Commands --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🚀 *Bulk File & Image Downloader Bot*\n\n"
        "با این ربات می‌تونی لیست URLها رو بفرستی و همه فایل‌ها و تصاویر رو به صورت یک فایل ZIP دریافت کنی.\n\n"
        "📌 *دستورات اصلی:*\n"
        "/download - شروع دانلود جدید\n"
        "/status - وضعیت آخرین درخواست\n"
        "/help - راهنمای کامل\n"
        "/cancel - لغو عملیات جاری"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *راهنمای استفاده*\n\n"
        "۱. دستور /download را بزن\n"
        "۲. لیست URLها را بفرست (هر خط یک لینک)\n"
        "۳. ربات Actor را روی Apify اجرا می‌کند\n"
        "۴. بعد از اتمام، فایل ZIP مستقیماً برایت ارسال می‌شود\n\n"
        "*مثال ورودی:*\n"
        "`https://example.com/image1.jpg`\n"
        "`https://example.com/document.pdf`\n"
        "`https://cdn.shopify.com/.../product.png`\n\n"
        f"⚠️ حداکثر {MAX_URLS_PER_REQUEST} URL در هر درخواست\n"
        f"⚠️ حداکثر حجم فایل ارسالی به تلگرام: {MAX_FILE_SIZE_MB} مگابایت"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📎 لیست URLها را بفرست (هر خط یک URL).\n\n"
        f"حداکثر {MAX_URLS_PER_REQUEST} لینک مجاز است.\n"
        "برای لغو: /cancel"
    )
    return WAITING_URLS


async def receive_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    urls = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(("http://", "https://"))
    ]

    if not urls:
        await update.message.reply_text("❌ هیچ URL معتبری پیدا نشد. دوباره امتحان کن.")
        return WAITING_URLS

    if len(urls) > MAX_URLS_PER_REQUEST:
        await update.message.reply_text(
            f"❌ حداکثر {MAX_URLS_PER_REQUEST} URL مجاز است.\n"
            f"تعداد ارسال‌شده: {len(urls)}"
        )
        return WAITING_URLS

    user_id = update.effective_user.id
    status_msg = await update.message.reply_text(
        f"⏳ در حال شروع دانلود *{len(urls)}* فایل...\nلطفاً صبر کن.",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        run = await apify.start_download(urls=urls, create_zip=True)
        run_id = get_attr(run, "id")
        if not run_id:
            raise ValueError("Could not get run id from Actor response")
        user_runs[user_id] = run_id

        await status_msg.edit_text(
            f"✅ درخواست ثبت شد!\n\n"
            f"🆔 `Run ID: {run_id}`\n"
            f"📊 تعداد URL: {len(urls)}\n\n"
            f"در حال پردازش... ممکن است چند دقیقه طول بکشد.",
            parse_mode=ParseMode.MARKDOWN,
        )

        # Start background monitoring
        asyncio.create_task(
            monitor_and_send(context, user_id, run_id, status_msg.message_id, len(urls))
        )

    except Exception as e:
        logger.exception("Failed to start Actor")
        await status_msg.edit_text(
            f"❌ خطا در شروع Actor:\n`{str(e)}`",
            parse_mode=ParseMode.MARKDOWN,
        )

    return ConversationHandler.END


async def monitor_and_send(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    run_id: str,
    message_id: int,
    url_count: int,
):
    """Wait for the Actor, then try to send the ZIP file to the user."""
    try:
        # Wait for finish
        run = await apify.wait_for_run(run_id, timeout_secs=1800)
        status = get_attr(run, "status")

        if status != "SUCCEEDED":
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=message_id,
                text=f"❌ اجرا با وضعیت `{status}` تمام شد.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        dataset_id = get_attr(run, "default_dataset_id") or get_attr(run, "defaultDatasetId")
        store_id = get_attr(run, "default_key_value_store_id") or get_attr(run, "defaultKeyValueStoreId")

        # Update status
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=(
                f"✅ پردازش تمام شد!\n\n"
                f"در حال آماده‌سازی فایل ZIP برای ارسال..."
            ),
        )

        # Prepare local path
        await ensure_temp_dir()
        local_zip = os.path.join(TEMP_DIR, f"{run_id}.zip")

        # Try to download ZIP
        zip_path = await apify.download_zip_from_store(store_id, local_zip)

        if zip_path and os.path.exists(zip_path):
            file_size_mb = os.path.getsize(zip_path) / (1024 * 1024)

            if file_size_mb > MAX_FILE_SIZE_MB:
                # File too big for Telegram
                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=message_id,
                    text=(
                        f"✅ دانلود کامل شد اما حجم فایل ({file_size_mb:.1f} MB) "
                        f"بیشتر از محدودیت تلگرام ({MAX_FILE_SIZE_MB} MB) است.\n\n"
                        f"🆔 Run ID: `{run_id}`\n"
                        f"📦 Dataset: `{dataset_id}`\n"
                        f"🗂 Store: `{store_id}`\n\n"
                        f"لطفاً از کنسول Apify فایل را دانلود کن."
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                # Send the ZIP file
                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=message_id,
                    text=f"📤 در حال ارسال فایل ZIP ({file_size_mb:.1f} MB)...",
                )

                with open(zip_path, "rb") as f:
                    await context.bot.send_document(
                        chat_id=user_id,
                        document=InputFile(f, filename=f"download_{run_id[:8]}.zip"),
                        caption=(
                            f"✅ دانلود کامل شد!\n"
                            f"تعداد URL: {url_count}\n"
                            f"حجم: {file_size_mb:.1f} MB\n"
                            f"Run ID: `{run_id}`"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )

                # Clean up status message
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                except Exception:
                    pass

        else:
            # No ZIP found – send summary instead
            summary = await apify.get_dataset_summary(dataset_id)

            reasons_text = ""
            if summary.get("reasons"):
                reasons_text = "\n\nعلت خطاها:\n" + "\n".join(summary["reasons"])

            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=message_id,
                text=(
                    f"✅ پردازش تمام شد اما فایل ZIP پیدا نشد.\n\n"
                    f"📊 خلاصه:\n"
                    f"• کل: {summary['total']}\n"
                    f"• موفق: {summary['success']}\n"
                    f"• ناموفق: {summary['failed']}"
                    f"{reasons_text}\n\n"
                    f"🆔 Run ID: `{run_id}`\n"
                    f"📦 Dataset: `{dataset_id}`\n"
                    f"🗂 Store: `{store_id}`"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )

        # Cleanup temp file
        if os.path.exists(local_zip):
            try:
                os.remove(local_zip)
            except Exception:
                pass

    except Exception as e:
        logger.exception(f"Error in monitor_and_send for run {run_id}")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ خطا در دریافت نتیجه:\n`{str(e)}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    run_id = user_runs.get(user_id)

    if not run_id:
        await update.message.reply_text("هنوز هیچ درخواستی ثبت نکرده‌ای.")
        return

    try:
        run = await apify.get_run_info(run_id)
        status = get_attr(run, "status", "UNKNOWN")
        await update.message.reply_text(
            f"آخرین درخواست شما:\n\n"
            f"🆔 `{run_id}`\n"
            f"وضعیت: *{status}*",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"خطا در دریافت وضعیت: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


# -------------------- Main --------------------

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set")
    if not os.getenv("APIFY_TOKEN"):
        raise ValueError("APIFY_TOKEN is not set")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("download", download_command)],
        states={
            WAITING_URLS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_urls)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(conv_handler)

    logger.info("Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
