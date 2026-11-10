import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
async def run_apify_actor(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """اجرای Actor و دریافت نتایج"""
    client = ApifyClientAsync(APIFY_TOKEN)
    
    run = await client.actor(ACTOR_ID).call(run_input=input_data)
    
    # دریافت آیتم‌های دیتاست
    dataset_items = []
    async for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        dataset_items.append(item)
        if len(dataset_items) >= 300:  # محدودیت برای تلگرام
            break
    
    return {
        "run_id": run["id"],
        "dataset_id": run["defaultDatasetId"],
        "status": run["status"],
        "items": dataset_items,
        "item_count": len(dataset_items),
        "console_url": f"https://console.apify.com/actors/runs/{run['id']}"
    }


def format_summary(items: list, mode: str) -> str:
    """ساخت خلاصه متنی از نتایج"""
    if not items:
        return "هیچ نتیجه‌ای پیدا نشد."
    
    lines = [f"📊 تعداد نتایج: {len(items)}\n"]
    
    for i, item in enumerate(items[:8], 1):  # فقط ۸ تای اول
        text = item.get("text") or item.get("message") or item.get("postText") or "بدون متن"
        text = text[:180].replace("\n", " ")
        channel = item.get("channelUsername") or item.get("channelName") or item.get("username") or "نامشخص"
        date = item.get("date") or item.get("publishedAt") or item.get("timestamp") or ""
        views = item.get("views") or item.get("viewCount") or "?"
        
        lines.append(
            f"{i}. 📢 @{channel}\n"
            f"   📅 {date} | 👁 {views}\n"
            f"   {text}...\n"
        )
    
    if len(items) > 8:
        lines.append(f"\n... و {len(items) - 8} مورد دیگر در فایل JSON")
    
    return "\n".join(lines)


# ==================== هندلرها ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("🔍 جستجوی کلیدواژه", callback_data="mode_keyword")],
        [InlineKeyboardButton("📢 اسکراپ کانال", callback_data="mode_channel")],
    ]
    
    await update.message.reply_text(
        "سلام! 👋\n\n"
        "ربات اسکراپر پیشرفته تلگرام با استفاده از Apify\n\n"
        "یکی از حالت‌ها را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_MODE


async def mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    mode = query.data.replace("mode_", "")
    context.user_data["mode"] = mode
    
    if mode == "keyword":
        await query.edit_message_text(
            "🔍 **حالت جستجوی کلیدواژه**\n\n"
            "کلمات کلیدی را وارد کن (هر کلمه در یک خط یا با کاما جدا کن):\n\n"
            "مثال:\n"
            "`bitcoin`\n"
            "`هوش مصنوعی`\n"
            "`remote jobs`"
        )
        return KEYWORD_INPUT
    else:
        await query.edit_message_text(
            "📢 **حالت اسکراپ کانال**\n\n"
            "یوزرنیم کانال‌ها را وارد کن (با یا بدون @):\n\n"
            "مثال:\n"
            "`cointelegraph`\n"
            "`durov`\n"
            "`telegram`"
        )
        return CHANNEL_INPUT


async def receive_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    keywords = [k.strip().lstrip("@") for k in text.replace(",", "\n").split("\n") if k.strip()]
    
    if not keywords:
        await update.message.reply_text("حداقل یک کلیدواژه وارد کن.")
        return KEYWORD_INPUT
    
    context.user_data["keywords"] = keywords
    await update.message.reply_text(
        f"✅ کلیدواژه‌ها: {', '.join(keywords)}\n\n"
        "حداکثر تعداد نتایج برای هر کلیدواژه را وارد کن (۱۰ تا ۱۰۰):\n"
        "پیش‌فرض: `50`"
    )
    return MAX_RESULTS


async def receive_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    channels = [c.strip().lstrip("@") for c in text.replace(",", "\n").split("\n") if c.strip()]
    
    if not channels:
        await update.message.reply_text("حداقل یک کانال وارد کن.")
        return CHANNEL_INPUT
    
    context.user_data["channels"] = channels
    await update.message.reply_text(
        f"✅ کانال‌ها: {', '.join('@' + c for c in channels)}\n\n"
        "حداکثر تعداد پیام برای هر کانال را وارد کن (۱۰ تا ۵۰۰۰):\n"
        "پیش‌فرض: `100`"
    )
    return MAX_RESULTS


async def receive_max_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    try:
        max_val = int(text) if text else (50 if context.user_data["mode"] == "keyword" else 100)
    except ValueError:
        max_val = 50 if context.user_data["mode"] == "keyword" else 100
    
    if context.user_data["mode"] == "keyword":
        context.user_data["maxResultsPerKeyword"] = max(10, min(max_val, 100))
    else:
        context.user_data["channelMaxMessages"] = max(10, min(max_val, 5000))
    
    await update.message.reply_text(
        "تاریخ شروع (اختیاری):\n"
        "مثال‌ها:\n"
        "`7 days`\n"
        "`30 days`\n"
        "`2025-01-01`\n\n"
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
    
    # کشور و زبان فقط برای حالت keyword معنی‌دار است
    if context.user_data["mode"] == "keyword":
        keyboard = [
            ["us", "de", "fr", "tr"],
            ["ru", "ir", "ae", "skip"]
        ]
        await update.message.reply_text(
            "کد کشور برای جستجو (اختیاری):\n"
            "مثال: `us`, `de`, `tr`, `ir`\n"
            "یا `skip`",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        )
        return COUNTRY
    else:
        return await show_confirm(update, context)


async def receive_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text not in ["skip", "رد", "-"]:
        context.user_data["countryCode"] = text
    
    await update.message.reply_text(
        "کد زبان (اختیاری):\n"
        "مثال: `en`, `fa`, `de`, `tr`, `ar`\n"
        "یا `skip`",
        reply_markup=ReplyKeyboardRemove()
    )
    return LANGUAGE


async def receive_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text not in ["skip", "رد", "-"]:
        context.user_data["languageCode"] = text
    
    return await show_confirm(update, context)


async def show_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data
    mode = data.get("mode")
    
    summary = f"📋 **خلاصه تنظیمات**\n\n"
    summary += f"حالت: `{'جستجوی کلیدواژه' if mode == 'keyword' else 'اسکراپ کانال'}`\n"
    
    if mode == "keyword":
        summary += f"کلیدواژه‌ها: `{', '.join(data.get('keywords', []))}`\n"
        summary += f"حداکثر نتایج: `{data.get('maxResultsPerKeyword', 50)}`\n"
        if data.get("afterDate"):
            summary += f"از تاریخ: `{data['afterDate']}`\n"
        if data.get("countryCode"):
            summary += f"کشور: `{data['countryCode']}`\n"
        if data.get("languageCode"):
            summary += f"زبان: `{data['languageCode']}`\n"
    else:
        summary += f"کانال‌ها: `{', '.join('@' + c for c in data.get('channels', []))}`\n"
        summary += f"حداکثر پیام: `{data.get('channelMaxMessages', 100)}`\n"
        if data.get("channelAfterDate"):
            summary += f"از تاریخ: `{data['channelAfterDate']}`\n"
    
    keyboard = [
        [InlineKeyboardButton("✅ شروع اسکراپ", callback_data="confirm_run")],
        [InlineKeyboardButton("❌ لغو", callback_data="cancel")]
    ]
    
    await update.message.reply_text(summary, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return CONFIRM


async def confirm_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("عملیات لغو شد.")
        return ConversationHandler.END
    
    await query.edit_message_text("⏳ در حال اجرای Actor روی Apify...\nاین کار ممکن است چند دقیقه طول بکشد.")
    
    # ساخت ورودی Actor
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
        
        # ارسال خلاصه
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"✅ **اسکراپ تمام شد!**\n\n{summary}",
            parse_mode="Markdown"
        )
        
        # ارسال فایل JSON
        if result["items"]:
            filename = f"telegram_scrape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(result["items"], f, ensure_ascii=False, indent=2)
            
            with open(filename, "rb") as f:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    caption=f"📁 فایل کامل نتایج ({result['item_count']} مورد)\n🔗 [مشاهده در Apify]({result['console_url']})",
                    parse_mode="Markdown"
                )
            os.remove(filename)
        else:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="هیچ داده‌ای برنگشت. ممکن است کلیدواژه یا کانال نتیجه‌ای نداشته باشد."
            )
            
    except Exception as e:
        logger.error(f"Error running actor: {e}")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ خطا در اجرای Actor:\n`{str(e)}`",
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
            SELECT_MODE: [CallbackQueryHandler(mode_selected, pattern="^mode_")],
            KEYWORD_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_keywords)],
            CHANNEL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channels)],
            MAX_RESULTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_max_results)],
            AFTER_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_after_date)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_country)],
            LANGUAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_language)],
            CONFIRM: [CallbackQueryHandler(confirm_run, pattern="^(confirm_run|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    
    print("ربات در حال اجراست...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()