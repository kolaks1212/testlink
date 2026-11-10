import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
ACTOR_ID = "Z2samcXlPvSQy1oTq"  # Bulk Image & File Downloader

# Admin user IDs (comma separated in env)
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# Limits
MAX_URLS_PER_REQUEST = 300
MAX_FILE_SIZE_MB = 49  # Telegram bot limit is 50MB
TEMP_DIR = "/tmp/bot_downloads"
