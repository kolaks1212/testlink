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
MAX_FILE_SIZE = 45 * 1024 * 1024

user_files = {}
pending_urls = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================== ایمپورت روش‌ها ====================
METHODS = {}

# --- روش ۱: curl_cffi ---
try:
    from curl_cffi import requests as cffi_requests
    METHODS["curl_cffi"] = {
        "name": "1️⃣ curl_cffi (TLS فقط)",
        "available": True, "error": None,
        "install": "",
    }
    logger.info("✅ curl_cffi loaded")
except ImportError as e:
    cffi_requests = None
    METHODS["curl_cffi"] = {
        "name": "1️⃣ curl_cffi",
        "available": False, "error": str(e),
        "install": "pip install curl-cffi",
    }
    logger.warning("❌ curl_cffi: %s", e)

# --- روش ۲: ai-cloudscraper (اگر نصب باشد) ---
try:
    import cloudscraper as ai_cloudscraper
    METHODS["ai_cloudscraper"] = {
        "name": "2️⃣ ai-cloudscraper (Hybrid)",
        "available": True, "error": None,
        "install": "",
    }
    logger.info("✅ ai-cloudscraper loaded")
except ImportError as e:
    ai_cloudscraper = None
    METHODS["ai_cloudscraper"] = {
        "name": "2️⃣ ai-cloudscraper",
        "available": False, "error": str(e),
        "install": "pip install ai-cloudscraper",
    }
    logger.warning("❌ ai-cloudscraper: %s", e)

# --- روش ۳: cloudscraper25 ---
try:
    import cloudscraper25
    METHODS["cloudscraper25"] = {
        "name": "3️⃣ cloudscraper25 (Enhanced)",
        "available": True, "error": None,
        "install": "",
    }
    logger.info("✅ cloudscraper25 loaded")
except ImportError as e:
    cloudscraper25 = None
    METHODS["cloudscraper25"] = {
        "name": "3️⃣ cloudscraper25",
        "available": False, "error": str(e),
        "install": "pip install cloudscraper25",
    }
    logger.warning("❌ cloudscraper25: %s", e)

# --- روش ۴: unblock_requests ---
try:
    from unblock_requests import CloudflareSession
    METHODS["unblock_requests"] = {
        "name": "4️⃣ unblock_requests (Fallback)",
        "available": True, "error": None,
        "install": "",
    }
    logger.info("✅ unblock_requests loaded")
except ImportError as e:
    CloudflareSession = None
    METHODS["unblock_requests"] = {
        "name": "4️⃣ unblock_requests",
        "available": False, "error": str(e),
        "install": "pip install unblock-requests",
    }
    logger.warning("❌ unblock_requests: %s", e)

# --- روش ۵: nodriver ---
try:
    import nodriver as nd
    METHODS["nodriver"] = {
        "name": "5️⃣ nodriver (مرورگر واقعی)",
        "available": True, "error": None,
        "install": "",
    }
    logger.info("✅ nodriver loaded")
except ImportError as e:
    nd = None
    METHODS["nodriver"] = {
        "name": "5️⃣ nodriver",
        "available": False, "error": str(e),
        "install": "pip install nodriver + Chromium",
    }
    logger.warning("❌ nodriver: %s", e)

# --- روش ۶: cloudflare-skip ---
try:
    from cloudflare_skip import get as cf_skip_get
    METHODS["cloudflare_skip"] = {
        "name": "6️⃣ cloudflare-skip (Hybrid + کش)",
        "available": True, "error": None,
        "install": "",
    }
    logger.info("✅ cloudflare-skip loaded")
except ImportError as e:
    cf_skip_get = None
    METHODS["cloudflare_skip"] = {
        "name": "6️⃣ cloudflare-skip",
        "available": False, "error": str(e),
        "install": "pip install cloudflare-skip nodriver + Chromium",
    }
    logger.warning("❌ cloudflare-skip: %s", e)


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
    avail = sum(1 for m in METHODS.values() if m["available"])
    await update.message.reply_text(
        f"سلام 👋\n\n"
        f"لینک فایل رو بفرست تا دانلود کنم.\n"
        f"بعد از لینک، از بین {avail} روش فعال انتخاب کن.\n\n"
        f"برای وضعیت کامل: /methods"
    )


async def methods_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📋 <b>وضعیت روش‌های دانلود:</b>\n"]
    for info in METHODS.values():
        icon = "✅" if info["available"] else "❌"
        lines.append(f"{icon} {info['name']}")
        if not info["available"]:
            lines.append(f"    <i>{info['install']}</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    uid = update.effective_user.id

    if not is_valid_url(url):
        await update.message.reply_text("لینک معتبر (http/https) بفرست.")
        return

    if uid in user_files:
        try:
            os.remove(user_files[uid]["path"])
        except Exception:
            pass
        del user_files[uid]

    pending_urls[uid] = url
    logger.info("URL from user %s: %s", uid, url)

    keyboard = []
    for key, info in METHODS.items():
        if info["available"]:
            keyboard.append([InlineKeyboardButton(
                f"✅ {info['name']}", callback_data=f"dl_{key}"
            )])
        else:
            keyboard.append([InlineKeyboardButton(
                f"⛔ {info['name']}", callback_data=f"info_{key}"
            )])
    keyboard.append([InlineKeyboardButton("❌ لغو", callback_data="cancel")])

    await update.message.reply_text(
        f"🔗 لینک دریافت شد.\n\nروش دانلود را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def do_download(url: str, uid: int, edit_func, method_key: str):
    info = METHODS.get(method_key)
    if not info or not info["available"]:
        await edit_func(f"❌ روش <code>{method_key}</code> فعال نیست.")
        return

    mname = info["name"]
    logger.info("=" * 60)
    logger.info("DOWNLOAD | User: %s | Method: %s (%s)", uid, method_key, mname)
    logger.info("URL: %s", url)
    logger.info("=" * 60)

    await edit_func(f"⏳ در حال دانلود...\n\nروش: {mname}")

    headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": url,
    }

    response = None
    try:
        # ==================== ارسال درخواست بر اساس روش ====================
        if method_key == "curl_cffi":
            logger.info("[curl_cffi] GET with impersonate='chrome'")
            response = cffi_requests.get(
                url, impersonate="chrome", stream=True,
                timeout=40, headers=headers, allow_redirects=True,
            )

        elif method_key == "ai_cloudscraper":
            logger.info("[ai-cloudscraper] create_scraper(interpreter='hybrid')")
            scraper = ai_cloudscraper.create_scraper(
                debug=True, interpreter="hybrid",
                browser={"browser": "chrome", "platform": "windows", "desktop": True},
            )
            response = scraper.get(url, stream=True, timeout=40, headers=headers, allow_redirects=True)

        elif method_key == "cloudscraper25":
            logger.info("[cloudscraper25] create_scraper() with stealth")
            scraper = cloudscraper25.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True},
            )
            response = scraper.get(url, stream=True, timeout=40, headers=headers, allow_redirects=True)

        elif method_key == "unblock_requests":
            logger.info("[unblock_requests] CloudflareSession(mode='curl_cffi')")
            session = CloudflareSession()
            response = session.get(url, stream=True, timeout=40, headers=headers, allow_redirects=True)

        elif method_key == "nodriver":
            logger.info("[nodriver] Starting real Chrome browser...")
            driver = await nd.start()
            try:
                tab = await driver.get(url)
                # ذخیره‌ی محتوای صفحه به عنوان فایل
                content = await tab.get_content()
                temp_dir = tempfile.gettempdir()
                filename = get_filename_from_url(url) or "nodriver_download.bin"
                file_path = os.path.join(temp_dir, f"{uid}_nodriver_{filename}")
                with open(file_path, "wb") as f:
                    f.write(content.encode("utf-8"))
                user_files[uid] = {"path": file_path, "filename": filename, "method": mname}
                size_kb = os.path.getsize(file_path) / 1024
                logger.info("[nodriver] Content saved: %s bytes", os.path.getsize(file_path))
                keyboard = [[InlineKeyboardButton("✅ آپلود", callback_data="upload"),
                             InlineKeyboardButton("❌ لغو", callback_data="cancel")]]
                await edit_func(
                    f"✅ محتوا دریافت شد (روش nodriver)\n"
                    f"نام: <code>{filename}</code>\n"
                    f"حجم: {size_kb:.1f} KB\n\n"
                    f"آپلود شود؟",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
                return
            finally:
                try:
                    driver.stop()
                except Exception:
                    pass

        elif method_key == "cloudflare_skip":
            logger.info("[cloudflare-skip] Using curl_cffi + nodriver hybrid with cache")
            response = cf_skip_get(url, stream=True, timeout=40, headers=headers)

        else:
            raise RuntimeError(f"Unknown method: {method_key}")

        # ==================== تحلیل پاسخ ====================
        status = response.status_code
        reason = getattr(response, "reason", "N/A")
        final_url = getattr(response, "url", url)
        resp_headers = dict(response.headers)
        cf_mitigated = resp_headers.get("Cf-Mitigated", "").lower()

        logger.info("[%s] Status: %s %s", method_key, status, reason)
        logger.info("[%s] Final URL: %s", method_key, final_url)
        logger.info("[%s] Cf-Mitigated: %s", method_key, cf_mitigated or "none")
        logger.info("[%s] Headers: %s", method_key, resp_headers)

        if status >= 400:
            body_snippet = ""
            try:
                body_snippet = response.text[:500]
            except Exception:
                pass
            logger.error(
                "[%s] FAILED | Status=%s | Reason=%s | Cf-Mitigated=%s | Body=%s",
                method_key, status, reason, cf_mitigated or "none", body_snippet[:300],
            )
            hint = ""
            if cf_mitigated == "challenge":
                hint = "\n\n💡 هنوز صفحه‌ی چالش کلادفلر برگشته."
            await edit_func(
                f"❌ خطا (روش: {mname})\n"
                f"کد: <code>{status}</code>\n"
                f"دلیل: {reason}\n"
                f"Cf-Mitigated: <code>{cf_mitigated or 'none'}</code>"
                f"{hint}\n\nجزئیات در لاگ.",
                parse_mode="HTML",
            )
            return

        # ==================== حجم و نام فایل ====================
        cl = resp_headers.get("content-length")
        if cl:
            try:
                if int(cl) > MAX_FILE_SIZE:
                    logger.warning("[%s] Too large (CL): %s", method_key, cl)
                    await edit_func("حجم فایل بیش از ۴۵ مگابایت است.")
                    return
            except ValueError:
                pass

        filename = get_filename_from_url(url, resp_headers.get("content-disposition"))
        if "." not in filename:
            ct = resp_headers.get("content-type", "")
            filename += ".mp4" if "video" in ct else ".jpg" if "image" in ct else ".bin"
        logger.info("[%s] Filename: %s", method_key, filename)

        # ==================== ذخیره‌سازی ====================
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, f"{uid}_{method_key}_{filename}")
        downloaded = 0

        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > MAX_FILE_SIZE:
                        os.remove(file_path)
                        logger.warning("[%s] Size exceeded at %s bytes", method_key, downloaded)
                        await edit_func("حجم فایل از حد مجاز بیشتر شد.")
                        return

        logger.info("[%s] SUCCESS: %s bytes (%.2f MB)", method_key, downloaded, downloaded / 1024 / 1024)

        user_files[uid] = {"path": file_path, "filename": filename, "method": mname}
        keyboard = [[InlineKeyboardButton("✅ آپلود", callback_data="upload"),
                     InlineKeyboardButton("❌ لغو", callback_data="cancel")]]
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        await edit_func(
            f"✅ دانلود موفق!\n\n"
            f"روش: {mname}\n"
            f"نام: <code>{filename}</code>\n"
            f"حجم: {size_mb:.2f} MB\n\nآپلود شود؟",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.exception("[%s] EXCEPTION | URL=%s", method_key, url)
        await edit_func(
            f"❌ خطا (روش: {mname})\n\n"
            f"<code>{type(e).__name__}</code>: {str(e)[:200]}",
            parse_mode="HTML",
        )
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    if data.startswith("info_"):
        key = data[5:]
        info = METHODS.get(key)
        if info:
            await q.answer(f"نصب:\n{info['install']}", show_alert=True)
        return

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

    if data.startswith("dl_"):
        method_key = data[3:]
        url = pending_urls.pop(uid, None)
        if not url:
            await q.edit_message_text("لینک منقضی شده. دوباره بفرست.")
            return

        async def edit_func(text, **kw):
            return await q.edit_message_text(text, **kw)

        await do_download(url, uid, edit_func, method_key)
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
            logger.exception("Upload error:")
            await q.edit_message_text(f"❌ خطا در آپلود:\n<code>{str(e)[:200]}</code>", parse_mode="HTML")
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
    app.add_handler(CommandHandler("methods", methods_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(button_handler))

    avail = [k for k, v in METHODS.items() if v["available"]]
    logger.info("Bot started. Available methods: %s", avail)
    print(f"ربات شروع شد. روش‌های فعال: {len(avail)}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
