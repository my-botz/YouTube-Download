import os
import logging
import time
import asyncio
from pathlib import Path
from typing import Dict, Optional
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import BadRequest
from ffmpeg import input as ffmpeg_input
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='▸ %(asctime)s ▸ %(levelname)s ▸ %(message)s',
    datefmt='%H:%M:%S'
)

app = Client(
    "file_converter_bot",
    api_id=os.getenv("API_ID"),
    api_hash=os.getenv("API_HASH"),
    bot_token=os.getenv("BOT_TOKEN")
)

THUMBNAILS_DIR = "thumbnails"
Path(THUMBNAILS_DIR).mkdir(exist_ok=True)
user_data: Dict[int, dict] = {}

VIDEO_EXTS = {'.mp4', '.avi', '.mkv', '.mov', '.webm'}
AUDIO_EXTS = {'.mp3', '.wav', '.ogg', '.flac'}

def humanbytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024:
            break
        size /= 1024
    return f"{size:.2f} {unit}"

def human_time(seconds: int) -> str:
    periods = [('שעה', 3600), ('דקה', 60), ('שניות', 1)]
    result = []
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result.append(f"{int(period_value)} {period_name}")
    return ' '.join(result) if result else '0 שניות'

def progress_bar(percentage: float) -> str:
    filled = '●'
    empty = '○'
    total_bars = 12
    filled_bars = round(percentage / 100 * total_bars)
    return f"[{filled * filled_bars}{empty * (total_bars - filled_bars)}] {percentage:.2f}%"

def process_filename(new_name: str, original_name: str, media_type: str) -> str:
    original_ext = os.path.splitext(original_name)[1].lower()
    name_part, ext_part = os.path.splitext(new_name)
    ext_part = ext_part.lower()
    
    if media_type == 'video':
        if ext_part in AUDIO_EXTS:
            return f"{name_part}.mp4"
        if not ext_part or ext_part not in VIDEO_EXTS:
            return f"{name_part}{original_ext if original_ext in VIDEO_EXTS else '.mp4'}"
    else:
        if not ext_part:
            return f"{new_name}{original_ext}"
    return new_name

@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    start_text = """
    🌟 **ברוך הבא לבוט ההמרות!** 🌟

    כאן תוכל:
    ▸ להמיר קבצים בין פורמטים
    ▸ לשנות שמות קבצים
    ▸ לנהל תמונות ממוזערות

    📜 **פקודות זמינות:**
    /start - תפריט ראשי
    /view_thumb - הצג תמונה ממוזערת
    /del_thumb - מחק תמונה ממוזערת
    /cancel - ביטול פעולה נוכחית

    ⚡ **גודל מקסימלי:** 2GB
    """
    await message.reply_text(
        start_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("התחל המרה 🚀", callback_data="start_conversion")]
        ])
    )

@app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in user_data:
        await cleanup_user_data(user_id)
        await message.reply("✅ כל הפעולות בוטלו בהצלחה!")
    else:
        await message.reply("ℹ️ אין פעולות פעילות לביטול")

@app.on_message(filters.document | filters.video)
async def handle_file(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_data.get(user_id, {}).get('busy'):
        return await message.reply("⚠️ יש להשלים את הפעולה הנוכחית לפני התחלת פעולה חדשה")
    
    file = message.video or message.document
    user_data[user_id] = {
        'busy': True,
        'file_id': file.file_id,
        'original_name': file.file_name,
        'media_type': 'video' if message.video else 'document',
        'start_time': time.time(),
        'messages_to_delete': [message.id]
    }
    
    await message.reply_text(
        "📝 **בחירת שם קובץ**",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("שנה שם ✏️", callback_data="rename_yes"),
                InlineKeyboardButton("המשך ללא שינוי ✅", callback_data="rename_no")
            ],
            [InlineKeyboardButton("ביטול פעולה ❌", callback_data="cancel")]
        ])
    )

@app.on_callback_query(filters.regex(r"^rename_(yes|no|cancel)$"))
async def handle_rename(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    action = query.data.split("_")[1]
    
    await query.answer()
    await query.message.delete()
    
    if action == "cancel":
        await cleanup_user_data(user_id)
        return await query.message.reply("✅ הפעולה בוטלה בהצלחה")
    
    if action == "no":
        user_data[user_id]["new_filename"] = process_filename(
            user_data[user_id]['original_name'],
            user_data[user_id]['original_name'],
            user_data[user_id]['media_type']
        )
        await ask_upload_type(user_id)
    else:
        msg = await query.message.reply(
            "✍️ **שלח את השם החדש לקובץ:**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול ❌", callback_data="cancel")]])
        )
        user_data[user_id]["messages_to_delete"].append(msg.id)

@app.on_message(filters.private & filters.text & ~filters.command(["start","view_thumb","del_thumb","cancel"]))
async def handle_filename(client: Client, message: Message):
    user_id = message.from_user.id
    if not user_data.get(user_id, {}).get('busy'):
        return
    
    processed_name = process_filename(
        message.text,
        user_data[user_id]['original_name'],
        user_data[user_id]['media_type']
    )
    
    user_data[user_id]["new_filename"] = processed_name
    user_data[user_id]["messages_to_delete"].append(message.id)
    
    try:
        await client.delete_messages(user_id, user_data[user_id]["messages_to_delete"])
    except Exception as e:
        logging.error(f"שגיאת מחיקת הודעות: {e}")
    
    await ask_upload_type(user_id)

async def ask_upload_type(user_id: int):
    user = user_data.get(user_id)
    if not user:
        return
    
    try:
        progress_msg = await app.send_message(
            user_id,
            "⚡ **מוריד את הקובץ...**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול הורדה ❌", callback_data="cancel")]])
        )
        
        file_path = await app.download_media(
            user["file_id"],
            progress=lambda current, total: update_progress(current, total, progress_msg, "הורדה")
        )
        
        user["file_path"] = file_path
        await progress_msg.delete()
        
        await app.send_message(
            user_id,
            f"📁 **פרטי קובץ:**\n"
            f"▸ שם: `{user.get('new_filename', user['original_name']}`\n"
            f"▸ גודל: {humanbytes(os.path.getsize(file_path))}\n\n"
            "📤 **בחר פורמט העלאה:**",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("וידאו 🎥", callback_data="upload_video"),
                    InlineKeyboardButton("קובץ 📄", callback_data="upload_file")
                ],
                [InlineKeyboardButton("ביטול הכל ❌", callback_data="cancel")]
            ])
        )
        
    except Exception as e:
        logging.error(f"שגיאת הורדה: {e}")
        await cleanup_user_data(user_id)
        await progress_msg.edit("❌ **שגיאה בהורדת הקובץ**")

async def update_progress(current: int, total: int, message: Message, operation: str):
    percent = current * 100 / total
    bar = progress_bar(percent)
    speed = humanbytes(current / (time.time() - user_data[message.from_user.id]['start_time']))
    eta_seconds = (total - current) / (current / (time.time() - user_data[message.from_user.id]['start_time'])) if current > 0 else 0
    eta = human_time(int(eta_seconds)) if current > 0 else '0 שניות'
    
    text = (
        f"🚀 **{operation} מתבצעת**\n\n"
        f"{bar}\n"
        f"▸ 📁 שם: `{user_data[message.from_user.id].get('new_filename', 'קובץ')}`\n"
        f"▸ ⚡ מהירות: {speed}/s\n"
        f"▸ 🕒 זמן משוער: {eta}"
    )
    
    try:
        await message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול פעולה ❌", callback_data="cancel")]])
        )
    except BadRequest:
        pass

@app.on_callback_query(filters.regex(r"^upload_(video|file|cancel)$"))
async def handle_upload(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    action = query.data.split("_")[1]
    
    await query.answer()
    await query.message.delete()
    
    if action == "cancel":
        await cleanup_user_data(user_id)
        return await query.message.reply("✅ הפעולה בוטלה בהצלחה")
    
    user = user_data.get(user_id)
    if not user:
        return
    
    try:
        progress_msg = await app.send_message(
            user_id,
            "⚡ **מתחיל בעיבוד...**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול העלאה ❌", callback_data="cancel")]])
        )
        
        file_name = user.get("new_filename", user["original_name"])
        output_path = await process_media(user["file_path"], user_id, action)
        
        if action == "video":
            await app.send_video(
                user_id,
                output_path,
                file_name=file_name,
                progress=lambda current, total: update_progress(current, total, progress_msg, "העלאה")
            )
        else:
            await app.send_document(
                user_id,
                output_path,
                file_name=file_name,
                progress=lambda current, total: update_progress(current, total, progress_msg, "העלאה")
            )
        
        await progress_msg.delete()
        await app.send_message(user_id, "✅ **הקובץ הועלה בהצלחה!**")
        
    except Exception as e:
        logging.error(f"שגיאת העלאה: {e}")
        await progress_msg.edit("❌ **שגיאה בהעלאת הקובץ**")
    finally:
        await cleanup_user_data(user_id)

async def process_media(file_path: str, user_id: int, media_type: str) -> str:
    if media_type == "video":
        output_path = f"processed_{user_id}.mp4"
        (
            ffmpeg_input(file_path)
            .output(output_path, vcodec='copy', acodec='copy')
            .run(overwrite_output=True)
        )
        return output_path
    return file_path

async def cleanup_user_data(user_id: int):
    if user_id in user_data:
        for path in [user_data[user_id].get('file_path'), user_data[user_id].get('processed_path')]:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except:
                pass
        del user_data[user_id]

async def health_check(request):
    return web.Response(text="OK")

async def run_server():
    app_web = web.Application()
    app_web.router.add_get('/health', health_check)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8000)
    await site.start()
    logging.info("שרת בריאות פועל בפורט 8000")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(run_server())
    app.run()
