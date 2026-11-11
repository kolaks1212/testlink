import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

# dz_omar/universal-downloader — supports Residential Proxy
ACTOR_ID = "vcSGsZFGVpJJvAn0n"

# Admin user IDs (comma separated in env)
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# Limits
MAX_URLS_PER_REQUEST = 100
MAX_FILE_SIZE_MB = 49  # Telegram bot limit is 50MB
TEMP_DIR = "/tmp/bot_downloads"

# Proxy: use residential to bypass 403 / geo blocks
USE_RESIDENTIAL_PROXY = os.getenv("USE_RESIDENTIAL_PROXY", "true").lower() in ("1", "true", "yes")
PROXY_COUNTRY = os.getenv("PROXY_COUNTRY", "")  # e.g. "US", "DE" or empty for auto
