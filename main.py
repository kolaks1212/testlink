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
    """Wait for the Actor, then send file links / small files to the user."""
    try:
        run = await apify.wait_for_run(run_id, timeout_secs=1800)
        status = get_attr(run, "status")

        if status != "SUCCEEDED":
            status_msg_text = get_attr(run, "statusMessage") or get_attr(run, "status_message") or ""
            extra = f"\n{status_msg_text}" if status_msg_text else ""
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=message_id,
                text=f"❌ اجرا با وضعیت `{status}` تمام شد.{extra}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        dataset_id = get_attr(run, "default_dataset_id") or get_attr(run, "defaultDatasetId")
        store_id = get_attr(run, "default_key_value_store_id") or get_attr(run, "defaultKeyValueStoreId")

        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text="✅ پردازش تمام شد!\n\nدر حال آماده‌سازی نتیجه...",
        )

        summary = await apify.get_dataset_summary(dataset_id) if dataset_id else {
            "total": 0, "success": 0, "failed": 0, "reasons": [], "file_links": []
        }
        file_links = summary.get("file_links") or []

        # Fallback: this Actor mainly stores files in Key-Value Store
        if store_id and not file_links:
            store_files = await apify.list_store_files(store_id)
            if store_files:
                file_links = store_files
                summary["success"] = len(store_files)
                summary["total"] = max(summary.get("total") or 0, len(store_files))
                logger.info(f"Found {len(store_files)} files in KV store")

        # 1) Try ZIP if exists
        await ensure_temp_dir()
        local_zip = os.path.join(TEMP_DIR, f"{run_id}.zip")
        zip_path = await apify.download_zip_from_store(store_id, local_zip) if store_id else None

        if zip_path and os.path.exists(zip_path):
            file_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            if file_size_mb <= MAX_FILE_SIZE_MB:
                with open(zip_path, "rb") as f:
                    await context.bot.send_document(
                        chat_id=user_id,
                        document=InputFile(f, filename=f"download_{run_id[:8]}.zip"),
                        caption=(
                            f"✅ دانلود کامل شد!\n"
                            f"موفق: {summary['success']} / {summary['total']}\n"
                            f"حجم: {file_size_mb:.1f} MB\n"
                            f"Run ID: `{run_id}`"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                except Exception:
                    pass
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
                return
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"⚠️ ZIP خیلی بزرگ است ({file_size_mb:.1f} MB).\n"
                        f"لینک‌های فایل‌ها را جداگانه می‌فرستم."
                    ),
                )

        # 2) Send summary + links
        reasons_text = ""
        if summary.get("reasons"):
            reasons_text = "\n\nعلت خطاها:\n" + "\n".join(summary["reasons"])

        links_text = ""
        if file_links:
            lines = []
            for i, fl in enumerate(file_links[:15], 1):
                name = str(fl.get("name") or "file")[:40]
                url = fl.get("url") or ""
                lines.append(f"{i}. [{name}]({url})")
            links_text = "\n\n📥 لینک دانلود:\n" + "\n".join(lines)
            if len(file_links) > 15:
                links_text += f"\n... و {len(file_links) - 15} فایل دیگر"

        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=(
                f"✅ پردازش تمام شد.\n\n"
                f"📊 خلاصه:\n"
                f"• کل: {summary['total']}\n"
                f"• موفق: {summary['success']}\n"
                f"• ناموفق: {summary['failed']}"
                f"{reasons_text}"
                f"{links_text}\n\n"
                f"🆔 Run ID: `{run_id}`"
            ),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )

        # 3) Try to send small successful files as Telegram documents (max 3)
        sent = 0
        for fl in file_links[:3]:
            url = fl.get("url")
            if not url:
                continue
            name = str(fl.get("name") or "file")
            # sanitize filename
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:80]
            local_path = os.path.join(TEMP_DIR, f"{run_id}_{safe_name}")
            path = await apify.download_file_from_url(url, local_path)
            if not path or not os.path.exists(path):
                continue
            size_mb = os.path.getsize(path) / (1024 * 1024)
            if size_mb > MAX_FILE_SIZE_MB:
                try:
                    os.remove(path)
                except Exception:
                    pass
                continue
            try:
                with open(path, "rb") as f:
                    await context.bot.send_document(
                        chat_id=user_id,
                        document=InputFile(f, filename=safe_name),
                        caption=f"📎 {safe_name} ({size_mb:.1f} MB)",
                    )
                sent += 1
            except Exception as e:
                logger.warning(f"Failed to send file {safe_name}: {e}")
            finally:
                try:
                    os.remove(path)
                except Exception:
                    pass

        if sent:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📤 {sent} فایل کوچک هم مستقیماً ارسال شد.",
            )

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
