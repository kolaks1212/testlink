import os
import tempfile
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    user_id = update.effective_user.id

    if not is_valid_url(url):
        await update.message.reply_text("لطفاً یک لینک معتبر (http یا https) بفرست.")
        return

    await update.message.reply_chat_action(ChatAction.TYPING)
    status_msg = await update.message.reply_text("در حال دانلود فایل... ⏳")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "keep-alive",
            "Referer": url,  # بعضی سایت‌ها اینو چک می‌کنن
        }

        with requests.get(url, stream=True, timeout=40, headers=headers, allow_redirects=True) as r:

            # ---------- لاگ دقیق خطا ----------
            if r.status_code >= 400:
                error_details = (
                    f"Status Code: {r.status_code}\n"
                    f"Reason: {r.reason}\n"
                    f"URL: {r.url}\n"
                    f"Headers: {dict(r.headers)}\n"
                    f"Response Body (first 500 chars): {r.text[:500] if r.text else 'No body'}"
                )
                logger.error("Download failed with detailed info:\n%s", error_details)
                
                await status_msg.edit_text(
                    f"❌ خطا در دانلود فایل\n\n"
                    f"کد خطا: `{r.status_code}`\n"
                    f"دلیل: {r.reason}\n\n"
                    f"جزئیات کامل در لاگ ثبت شد."
                )
                return

            r.raise_for_status()

            content_length = r.headers.get("content-length")
            if content_length and int(content_length) > MAX_FILE_SIZE:
                await status_msg.edit_text("حجم فایل بیشتر از حد مجاز (۴۵ مگابایت) است.")
                return

            filename = get_filename_from_url(url, r.headers.get("content-disposition"))
            
            if "." not in filename:
                content_type = r.headers.get("content-type", "")
                if "video" in content_type:
                    filename += ".mp4"
                elif "image" in content_type:
                    filename += ".jpg"
                else:
                    filename += ".bin"

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

    except requests.exceptions.RequestException as e:
        # لاگ دقیق‌تر برای خطاهای شبکه
        logger.error("RequestException occurred:")
        logger.error("Error type: %s", type(e).__name__)
        logger.error("Error message: %s", str(e))
        
        if hasattr(e, 'response') and e.response is not None:
            logger.error("Status Code: %s", e.response.status_code)
            logger.error("Response Headers: %s", dict(e.response.headers))
            logger.error("Response Text: %s", e.response.text[:1000])
        
        await status_msg.edit_text(
            f"❌ خطا در دانلود فایل\n\n"
            f"`{type(e).__name__}`: {str(e)[:150]}"
        )
    except Exception as e:
        logger.exception("Unexpected error during download:")  # این کامل stacktrace رو می‌نویسه
        await status_msg.edit_text("خطای غیرمنتظره رخ داد. جزئیات در لاگ ثبت شد.")
