import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Set
from collections import defaultdict

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from apify_client import ApifyClientAsync

# ==================== تنظیمات ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
ACTOR_ID = "lofomachines/telegram-keyword-search-scraper"

# حالت‌های مکالمه
(
    SELECT_MODE,
    KEYWORD_INPUT,
    CHANNEL_INPUT,
    MAX_RESULTS,
    AFTER_DATE,
    COUNTRY,
    LANGUAGE,
    CONFIRM,
) = range(8)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==================== توابع کمکی ====================
def deduplicate_items(items: List[Dict]) -> List[Dict]:
    """حذف کانال و پست تکراری"""
    seen_channels: Set[str] = set()
    seen_posts: Set[str] = set()
    unique_items = []

    for item in items:
        # استخراج شناسه کانال
        channel = (
            item.get("channelUsername")
            or item.get("username")
            or item.get("channelName")
            or item.get("channel")
            or ""
        ).lower().lstrip("@")

        # استخراج شناسه پست
        post_id = str(
            item.get("id")
            or item.get("messageId")
            or item.get("postId")
            or item.get("url")
            or ""
        )

        # اگر پست تکراری بود رد کن
        if post_id and post_id in seen_posts:
            continue

        # اگر کانال تکراری بود، فقط یک پست از آن نگه دار (اولین)
        if channel and channel in seen_channels:
            # می‌توانی اینجا تصمیم بگیری چند پست از هر کانال نگه داری
            # فعلاً فقط اولین پست هر کانال را نگه می‌داریم
            continue

        if channel:
            seen_channels.add(channel)
        if post_id:
            seen_posts.add(post_id)

        unique_items.append(item)

    return unique_items


def get_channel_stats(items: List[Dict]) -> Dict:
    """آمار کانال‌ها"""
    channels = defaultdict(int)
    for item in items:
        ch = (
            item.get("channelUsername")
            or item.get("username")
            or item.get("channelName")
            or "unknown"
        )
        channels[ch] += 1
    return dict(channels)


async def run_apify_actor(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """اجرای Actor و دریافت نتایج"""
    client = ApifyClientAsync(APIFY_TOKEN)

    run = await client.actor(ACTOR_ID).call(run_input=input_data)

    dataset_items = []
    async for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        dataset_items.append(item)
        if len(dataset_items) >= 500:  # سقف امن
            break

    # حذف تکراری‌ها
    unique_items = deduplicate_items(dataset_items)

    return {
        "run_id": run["id"],
        "dataset_id": run["defaultDatasetId"],
        "status": run["status"],
        "items": unique_items,
        "raw_count": len(dataset_items),
        "unique_count": len(unique_items),
        "console_url": f"https://console.apify.com/actors/runs/{run['id']}",
    }


def format_summary(items: List[Dict], mode: str) -> str:
    """ساخت خلاصه خوانا"""
    if not items:
        return "هیچ نتیجه‌ای پیدا نشد."

    channel_stats = get_channel_stats(items)
    unique_channels = len(channel_stats)

    lines = [
        f"📊 **نتایج نهایی**",
        f"• تعداد پست یکتا: `{len(items)}`",
        f"• تعداد کانال یکتا: `{unique_channels}`",
        "",
        "🔝 **نمونه نتایج:**",
        ""
    ]

    for i, item in enumerate(items[:10], 1):
        text = (
            item.get("text")
            or item.get("message")
            or item.get("postText")
            or item.get("content")
            or "بدون متن"
        )
        text = text.replace("\n", " ").strip()[:160]

        channel = (
            item.get("channelUsername")
            or item.get("username")
            or item.get("channelName")
            or "نامشخص"
        )
        channel = channel.lstrip("@")

        date = (
            item.get("date")
            or item.get("publishedAt")
            or item.get("timestamp")
            or item.get("createdAt")
            or ""
        )
        if isinstance(date, str) and "T" in date:
            date = date.split("T")[0]

        views = item.get("views") or item.get("viewCount") or item.get("viewsCount") or "?"

        lines.append(
            f"**{i}.** 📢 `@{channel}`\n"
            f"📅 {date} | 👁 {views}\n"
            f"{text}...\n"
        )

    if len(items) > 10:
        lines.append(f"\n... و {len(items) - 10} مورد دیگر در فایل")

    return "\n".join(lines)


# ==================== هندلرها ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    keyboard = [
        [InlineKeyboardButton("🔍 جستجوی کلیدواژه", callback_data="mode_keyword")],
        [InlineKeyboardButton("📢 اسکراپ کانال مشخص", callback_data="mode_channel")],
        [InlineKeyboardButton("ℹ️ راهنما", callback_data="help")],
    ]

    text = (
        "سلام! 👋\n\n"
        "ربات پیشرفته اسکراپ تلگرام با Apify\n\n"
        "قابلیت‌ها:\n"
        "• جستجوی کلیدواژه در کانال‌های عمومی\n"
        "• اسکراپ تاریخچه کانال‌های مشخص\n"
        "• حذف خودکار کانال و پست تکراری\n"
        "• خروجی تمیز و آماری\n\n"
        "یکی از گزینه‌ها را انتخاب کن:"
    )

    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return SELECT_MODE


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    help_text = (
        "📖 **راهنمای استفاده**\n\n"
        "**۱. جستجوی کلیدواژه**\n"
        "کلمات کلیدی را وارد کن تا کانال‌ها و پست‌های مرتبط پیدا شود.\n"
        "مثال: `هوش مصنوعی` یا `bitcoin, crypto`\n\n"
        "**۲. اسکراپ کانال**\n"
        "یوزرنیم کانال‌ها را بده تا پست‌های اخیرشان استخراج شود.\n"
        "مثال: `cointelegraph` یا `@durov`\n\n"
        "**نکات:**\n"
        "• کانال‌های تکراری به صورت خودکار حذف می‌شوند\n"
        "• برای تست اول تعداد کم انتخاب کن\n"
        "• می‌توانی هر زمان `/start` بزنی"
    )

    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_start")]]
    await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return SELECT_MODE


async def mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "help":
        return await help_handler(update, context)
    if query.data == "back_to_start":
        return await start(update, context)

    mode = query.data.replace("mode_", "")
    context.user_data["mode"] = mode

    if mode == "keyword":
        await query.edit_message_text(
            "🔍 **حالت جستجوی کلیدواژه**\n\n"
            "کلمات کلیدی را وارد کن:\n"
            "• هر کلمه در یک خط\n"
            "• یا با کاما جدا کن\n\n"
            "مثال:\n"
            "`bitcoin`\n"
            "`هوش مصنوعی`\n"
            "`remote job, freelance`"
        )
        return KEYWORD_INPUT
    else:
        await query.edit_message_text(
            "📢 **حالت اسکراپ کانال**\n\n"
            "یوزرنیم کانال‌ها را وارد کن (با یا بدون @):\n\n"
            "مثال:\n"
            "`cointelegraph`\n"
            "`durov`\n"
            "`telegram, bbcpersian`"
        )
        return CHANNEL_INPUT


async def receive_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    keywords = [k.strip().lstrip("@") for k in text.replace(",", "\n").split("\n") if k.strip()]

    if not keywords:
        await update.message.reply_text("حداقل یک کلیدواژه وارد کن.")
        return KEYWORD_INPUT

    # حذف کلیدواژه تکراری
    keywords = list(dict.fromkeys(keywords))
    context.user_data["keywords"] = keywords

    keyboard = [
        [
            InlineKeyboardButton("۲۰", callback_data="max_20"),
            InlineKeyboardButton("۵۰", callback_data="max_50"),
            InlineKeyboardButton("۱۰۰", callback_data="max_100"),
        ],
        [InlineKeyboardButton("سفارشی", callback_data="max_custom")],
    ]

    await update.message.reply_text(
        f"✅ کلیدواژه‌ها: `{', '.join(keywords)}`\n\n"
        "حداکثر تعداد نتایج برای هر کلیدواژه را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return MAX_RESULTS


async def receive_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    channels = [c.strip().lstrip("@").lower() for c in text.replace(",", "\n").split("\n") if c.strip()]

    if not channels:
        await update.message.reply_text("حداقل یک کانال وارد کن.")
        return CHANNEL_INPUT

    # حذف کانال تکراری
    channels = list(dict.fromkeys(channels))
    context.user_data["channels"] = channels

    keyboard = [
        [
            InlineKeyboardButton("۵۰", callback_data="max_50"),
            InlineKeyboardButton("۱۰۰", callback_data="max_100"),
            InlineKeyboardButton("۲۰۰", callback_data="max_200"),
        ],
        [InlineKeyboardButton("سفارشی", callback_data="max_custom")],
    ]

    await update.message.reply_text(
        f"✅ کانال‌ها: `{', '.join('@' + c for c in channels)}`\n\n"
        "حداکثر تعداد پیام برای هر کانال را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return MAX_RESULTS


async def max_results_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "max_custom":
        await query.edit_message_text("عدد دلخواه را وارد کن (بین ۱۰ تا ۵۰۰):")
        context.user_data["waiting_custom_max"] = True
        return MAX_RESULTS

    max_val = int(query.data.replace("max_", ""))

    if context.user_data["mode"] == "keyword":
        context.user_data["maxResultsPerKeyword"] = max_val
    else:
        context.user_data["channelMaxMessages"] = max_val

    await query.edit_message_text(
        "تاریخ شروع (اختیاری):\n\n"
        "مثال‌ها:\n"
        "• `7 days`\n"
        "• `30 days`\n"
        "• `2025-06-01`\n\n"
        "اگر نمی‌خوای، `skip` بفرست."
    )
    return AFTER_DATE


async def receive_max_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # فقط وقتی کاربر عدد سفارشی می‌فرستد
    if not context.user_data.get("waiting_custom_max"):
        return MAX_RESULTS

    text = update.message.text.strip()
    try:
        max_val = int(text)
        max_val = max(10, min(max_val, 500))
    except ValueError:
        await update.message.reply_text("یک عدد معتبر وارد کن (۱۰ تا ۵۰۰).")
        return MAX_RESULTS

    context.user_data.pop("waiting_custom_max", None)

    if context.user_data["mode"] == "keyword":
        context.user_data["maxResultsPerKeyword"] = max_val
    else:
        context.user_data["channelMaxMessages"] = max_val

    await update.message.reply_text(
        "تاریخ شروع (اختیاری):\n\n"
        "مثال‌ها:\n"
        "• `7 days`\n"
        "• `30 days`\n"
        "• `2025-06-01`\n\n"
        "اگر نمی‌خوای، `skip` بفرست."
    )
    return AFTER_DATE


async def receive_after_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()

    if text not in ["skip", "رد", "-", ""]:
        if context.user_data["mode"] == "keyword":
            context.user_data["afterDate"] = text
        else:
            context.user_data["channelAfterDate"] = text

    if context.user_data["mode"] == "keyword":
        keyboard = [
            [
                InlineKeyboardButton("🇺🇸 US", callback_data="country_us"),
                InlineKeyboardButton("🇩🇪 DE", callback_data="country_de"),
                InlineKeyboardButton("🇹🇷 TR", callback_data="country_tr"),
            ],
            [
                InlineKeyboardButton("🇷🇺 RU", callback_data="country_ru"),
                InlineKeyboardButton("🇮🇷 IR", callback_data="country_ir"),
                InlineKeyboardButton("رد کردن", callback_data="country_skip"),
            ],
        ]
        await update.message.reply_text(
            "کشور ترجیحی برای جستجو (اختیاری):",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return COUNTRY
    else:
        return await show_confirm(update, context)


async def country_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data != "country_skip":
        context.user_data["countryCode"] = query.data.replace("country_", "")

    keyboard = [
        [
            InlineKeyboardButton("🇬🇧 EN", callback_data="lang_en"),
            InlineKeyboardButton("🇮🇷 FA", callback_data="lang_fa"),
            InlineKeyboardButton("🇩🇪 DE", callback_data="lang_de"),
        ],
        [
            InlineKeyboardButton("🇹🇷 TR", callback_data="lang_tr"),
            InlineKeyboardButton("🇷🇺 RU", callback_data="lang_ru"),
            InlineKeyboardButton("رد کردن", callback_data="lang_skip"),
        ],
    ]
    await query.edit_message_text(
        "زبان ترجیحی (اختیاری):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return LANGUAGE


async def language_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data != "lang_skip":
        context.user_data["languageCode"] = query.data.replace("lang_", "")

    return await show_confirm(update, context)


async def show_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    mode = data.get("mode")

    summary = "📋 **خلاصه تنظیمات**\n\n"
    summary += f"حالت: `{'جستجوی کلیدواژه' if mode == 'keyword' else 'اسکراپ کانال'}`\n"

    if mode == "keyword":
        summary += f"کلیدواژه‌ها: `{', '.join(data.get('keywords', []))}`\n"
        summary += f"حداکثر نتایج: `{data.get('maxResultsPerKeyword', 50)}`\n"
        if data.get("afterDate"):
            summary += f"از تاریخ: `{data['afterDate']}`\n"
        if data.get("countryCode"):
            summary += f"کشور: `{data['countryCode'].upper()}`\n"
        if data.get("languageCode"):
            summary += f"زبان: `{data['languageCode']}`\n"
    else:
        summary += f"کانال‌ها: `{', '.join('@' + c for c in data.get('channels', []))}`\n"
        summary += f"حداکثر پیام: `{data.get('channelMaxMessages', 100)}`\n"
        if data.get("channelAfterDate"):
            summary += f"از تاریخ: `{data['channelAfterDate']}`\n"

    keyboard = [
        [InlineKeyboardButton("✅ شروع اسکراپ", callback_data="confirm_run")],
        [InlineKeyboardButton("❌ لغو و بازگشت", callback_data="cancel")],
    ]

    if update.callback_query:
        await update.callback_query.edit_message_text(
            summary, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            summary, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )

    return CONFIRM


async def confirm_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("عملیات لغو شد.\nبرای شروع دوباره /start را بزن.")
        return ConversationHandler.END

    await query.edit_message_text(
        "⏳ در حال اجرای Actor روی Apify...\n\n"
        "این کار بسته به تعداد نتایج ممکن است ۱ تا ۳ دقیقه طول بکشد.\n"
        "لطفاً صبر کن..."
    )

    data = context.user_data
    run_input = {"mode": data["mode"]}

    if data["mode"] == "keyword":
        run_input["keywords"] = data.get("keywords", [])
        run_input["maxResultsPerKeyword"] = data.get("maxResultsPerKeyword", 50)
        if data.get("afterDate"):
            run_input["afterDate"] = data["afterDate"]
        if data.get("countryCode"):
            run_input["countryCode"] = data["countryCode"]
        if data.get("languageCode"):
            run_input["languageCode"] = data["languageCode"]
    else:
        run_input["channels"] = data.get("channels", [])
        run_input["channelMaxMessages"] = data.get("channelMaxMessages", 100)
        if data.get("channelAfterDate"):
            run_input["channelAfterDate"] = data["channelAfterDate"]

    try:
        result = await run_apify_actor(run_input)

        summary = format_summary(result["items"], data["mode"])

        # آمار اضافی
        extra = (
            f"\n\n📈 **آمار پردازش**\n"
            f"• نتایج خام: `{result['raw_count']}`\n"
            f"• بعد از حذف تکراری: `{result['unique_count']}`"
        )

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ **اسکراپ تمام شد!**\n\n{summary}{extra}",
            parse_mode="Markdown"
        )

        # ارسال فایل اگر نتایج وجود داشت
        if result["items"]:
            filename = f"telegram_scrape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(result["items"], f, ensure_ascii=False, indent=2)

            with open(filename, "rb") as f:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    caption=(
                        f"📁 فایل کامل نتایج\n"
                        f"تعداد: {result['unique_count']} مورد یکتا\n"
                        f"🔗 [مشاهده در Apify]({result['console_url']})"
                    ),
                    parse_mode="Markdown"
                )
            os.remove(filename)

        # دکمه شروع مجدد
        keyboard = [[InlineKeyboardButton("🔄 جستجوی جدید", callback_data="back_to_start")]]
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="می‌خوای جستجوی جدیدی انجام بدی؟",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error running actor: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ خطا در اجرای Actor:\n`{str(e)[:300]}`",
            parse_mode="Markdown"
        )

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ==================== main ====================
def main():
    if not TELEGRAM_TOKEN or not APIFY_TOKEN:
        raise ValueError("TELEGRAM_TOKEN و APIFY_TOKEN باید تنظیم شوند")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_MODE: [
                CallbackQueryHandler(mode_selected, pattern="^(mode_|help|back_to_start)"),
            ],
            KEYWORD_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_keywords)],
            CHANNEL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channels)],
            MAX_RESULTS: [
                CallbackQueryHandler(max_results_handler, pattern="^max_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_max_results),
            ],
            AFTER_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_after_date)],
            COUNTRY: [CallbackQueryHandler(country_handler, pattern="^country_")],
            LANGUAGE: [CallbackQueryHandler(language_handler, pattern="^lang_")],
            CONFIRM: [CallbackQueryHandler(confirm_run, pattern="^(confirm_run|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))

    print("ربات پیشرفته در حال اجراست...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
