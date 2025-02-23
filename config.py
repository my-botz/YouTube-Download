# config.py
import os

API_ID = int(os.environ.get("API_ID", 12345))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8000))
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))  # עדכן למזהה המנהל
WAIT_TIME = 300
