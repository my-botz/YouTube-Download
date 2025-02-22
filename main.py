import os
import logging
import time
import asyncio
from pathlib import Path
from typing import Dict, Optional, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import BadRequest
from ffmpeg import input as ffmpeg_input, probe
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

# ========= הגדרות לוג מתקדמות =========
logging.basicConfig(
    level=logging.INFO,
    format='✨ [%(asctime)s] ▸ %(levelname)s ▸ %(message)s ✨',
    datefmt='%d/%m/%Y %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot_activity.log')
    ]
)

# ========= הגדרות אפליקציה =========
app = Client(
    "ULTIMATE_CONVERTER_BOT",
    api_id=os.getenv("API_ID"),
    api_hash=os.getenv("API_HASH"),
    bot_token=os.getenv("BOT_TOKEN")
)

# ========= קבועים גלובליים =========
THUMBNAILS_DIR = Path("thumbnails")
THUMBNAILS_DIR.mkdir(exist_ok=True)
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

user_data: Dict[int, dict] = {}

SUPPORTED_VIDEO = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
SUPPORTED_AUDIO = {'.mp3', '.wav', '.ogg', '.flac'}

# ========= פונקציות עזר מעוצבות =========
def humanbytes(size: int) -> str:
    """ממיר בייטים לפורמט קריא עם אימוג'ים"""
    UNITS = ["B", "KB", "MB", "GB", "TB"]
    for unit in UNITS:
        if size < 1024:
            return f"📦 {size:.2f} {unit}"
        size /= 1024
    return f"📦 {size:.2f} TB"

def progress_bar(percentage: float) -> str:
    """פס התקדמות אנימציוני"""
    filled = '🟪'
    empty = '⬜'
    bars = 10
    filled_bars = int(percentage // (100/bars))
    return f"{filled * filled_bars}{empty * (bars - filled_bars)} {percentage:.1f}%"

async def generate_thumbnail(video_path: Path) -> Optional[Path]:
    """מייצר תמונה ממוזערת אוטומטית מהוידאו"""
    try:
        output_path = TEMP_DIR / f"thumb_{time.time()}.jpg"
        (
            ffmpeg_input(str(video_path), ss='00:00:01')
            .output(str(output_path), vframes=1)
            .run(quiet=True, overwrite_output=True)
        )
        return output_path
    except Exception as e:
        logging.error(f"שגיאת יצירת תמונה: {e}")
        return None

# ========= מערכת ניהול קבצים =========
class FileManager:
    @staticmethod
    async def cleanup(user_id: int):
        """מנקה קבצים זמניים"""
        if user_id in user_data:
            for path in user_data[user_id].get('temp_files', []):
                try:
                    Path(path).unlink()
                except:
                    pass
            del user_data[user_id]

    @staticmethod
    def add_temp_file(user_id: int, path: str):
        """מוסיף קובץ זמני למעקב"""
        if user_id not in user_data:
            user_data[user_id] = {'temp_files': []}
        user_data[user_id]['temp_files'].append(path)

# ========= הודעות מעוצבות =========
class FancyMessages:
    @staticmethod
    async def send_progress(
        message: Message,
        operation: str,
        current: int,
        total: int,
        start_time: float
    ) -> None:
        """שולח הודעת התקדמות מעוצבת"""
        elapsed = time.time() - start_time
        speed = current / elapsed if elapsed > 0 else 0
        eta = (total - current) / speed if speed > 0 else 0
        
        text = (
            f"🚀 **{operation} מתבצעת**\n\n"
            f"{progress_bar(current*100/total)}\n\n"
            f"⚡ **מהירות:** {humanbytes(speed)}/s\n"
            f"⏳ **זמן משוער:** {time.strftime('%M:%S', time.gmtime(eta))}\n"
            f"📊 **גודל כולל:** {humanbytes(total)}"
        )
        
        try:
            await message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚫 ביטול פעולה", callback_data="cancel_operation")]
                ])
            )
        except BadRequest:
            pass

# ========= Handlers מעוצבים =========
@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    """הודעת פתיחה אפית"""
    await message.reply_text(
        "🎬 **ברוך הבא לבוט ההמרות המתקדם!**\n\n"
        "העלה כל קובץ ואני אמיר אותו בצורה המושלמת!\n"
        "מתאים לווידאו, אודיו ומסמכים עם עיצוב מדהים!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("התחל המרה 🚀", callback_data="start_conversion")],
            [InlineKeyboardButton("⚙️ הגדרות", callback_data="settings")]
        ])
    )

@app.on_message(filters.document | filters.video | filters.audio)
async def handle_file(client: Client, message: Message):
    """מטפל בקבצים עם סגנון"""
    user_id = message.from_user.id
    file = message.video or message.document or message.audio
    
    # איפוס נתונים קודמים
    await FileManager.cleanup(user_id)
    
    user_data[user_id] = {
        'file_id': file.file_id,
        'original_name': file.file_name,
        'media_type': 'video' if message.video else 'audio' if message.audio else 'document',
        'start_time': time.time(),
        'thumb': None
    }
    
    await message.reply_text(
        "📁 **קובץ התקבל!**\n"
        "מה תרצה לעשות?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎞️ המר לפורמט אחר", callback_data="convert")],
            [InlineKeyboardButton("🖼️ הוסף תמונה ממוזערת", callback_data="add_thumb")],
            [InlineKeyboardButton("❌ ביטול", callback_data="cancel")]
        ])
    )

@app.on_callback_query(filters.regex("convert"))
async def convert_file(client: Client, query: CallbackQuery):
    """המרת קובץ עם אנימציות"""
    user_id = query.from_user.id
    await query.answer()
    
    progress_msg = await query.message.reply("⚡ **מתחיל בעיבוד...**")
    file_path = await client.download_media(
        user_data[user_id]['file_id'],
        progress=lambda c, t: FancyMessages.send_progress(progress_msg, "הורדה", c, t, user_data[user_id]['start_time'])
    )
    
    # המרה בפועל
    converted_path = await process_media(Path(file_path))
    
    # שליחה בחזרה עם עיצוב
    await client.send_document(
        chat_id=user_id,
        document=str(converted_path),
        thumb=str(user_data[user_id].get('thumb', '')),
        caption="✅ **הקובץ המומר מוכן!**"
    )
    
    # ניקוי
    await FileManager.cleanup(user_id)

async def process_media(file_path: Path) -> Path:
    """מעבד את הקובץ עם ffmpeg"""
    output_path = TEMP_DIR / f"converted_{time.time()}{file_path.suffix}"
    (
        ffmpeg_input(str(file_path))
        .output(str(output_path), vcodec='copy', acodec='copy')
        .run(quiet=True, overwrite_output=True)
    )
    return output_path

# ========= הרצת השרת =========
async def run_web_server():
    app_web = web.Application()
    app_web.router.add_get('/health', lambda r: web.Response(text="🟢 מערכת פעילה!"))
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8000)
    await site.start()

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_web_server())
    app.run()
