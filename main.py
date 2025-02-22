import os
import logging
import time
import math
from pathlib import Path
from typing import Dict, Optional, Tuple
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import BadRequest
from ffmpeg import input as ffmpeg_input
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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

# Helper functions
def humanbytes(size: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size)
    i = 0
    while size >= 1024 and i < len(units)-1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

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

async def update_progress(
    current: int,
    total: int,
    message: Message,
    start_time: float,
    operation: str,
    file_name: str
):
    now = time.time()
    diff = now - start_time
    
    if diff < 2 and current != total:
        return
    
    percentage = current * 100 / total
    speed = current / diff
    eta = (total - current) / speed if speed > 0 else 0
    
    progress = progress_bar(percentage)
    speed_text = f"{humanbytes(speed)}/שניה"
    eta_text = human_time(int(eta))
    size_text = humanbytes(total)
    
    text = (
        f"**📤 {operation} את הקובץ**\n\n"
        f"**שם קובץ:** `{file_name}`\n"
        f"**גודל קובץ:** `{size_text}`\n\n"
        f"{progress}\n\n"
        f"**מהירות:** {speed_text}\n"
        f"**זמן משוער:** {eta_text}"
    )
    
    try:
        await message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול פעולה ❌", callback_data="cancel_operation")]])
        )
    except BadRequest:
        pass

# Main handlers
@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    start_text = (
        "👋 **ברוך הבא לבוט המרת הקבצים!**\n\n"
        "📁 **אפשרויות עיקריות:**\n"
        "• המרת קבצים בין פורמטים\n"
        "• שינוי שם קבצים\n"
        "• ניהול תמונות ממוזערות\n\n"
        "⚡ **פקודות חשובות:**\n"
        "/view_thumb - הצג תמונה ממוזערת\n"
        "/del_thumb - מחק תמונה ממוזערת\n\n"
        "📦 **גודל מקסימלי:** 2GB"
    )
    await message.reply_text(start_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("התחל המרה 🚀", callback_data="start_conversion")]]))

@app.on_callback_query(filters.regex("^start_conversion$"))
async def start_conversion(client: Client, query: CallbackQuery):
    await query.answer()
    await query.message.delete()
    await query.message.reply("📤 אנא שלח קובץ להמרה:")

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
        "📝 האם לשנות את שם הקובץ?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("שנה שם ✏️", callback_data="rename_yes"),
             InlineKeyboardButton("המשך ללא שינוי ✅", callback_data="rename_no")],
            [InlineKeyboardButton("ביטול ❌", callback_data="cancel")]
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
        return await query.message.reply("❌ הפעולה בוטלה")
    
    if action == "no":
        user_data[user_id]["new_filename"] = user_data[user_id]["original_name"]
        await ask_upload_type(user_id)
    else:
        msg = await query.message.reply(
            "✍️ אנא שלח את השם החדש לקובץ:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול ❌", callback_data="cancel")]])
        )
        user_data[user_id]["messages_to_delete"].append(msg.id)

async def ask_upload_type(user_id: int):
    user = user_data.get(user_id)
    if not user:
        return
    
    progress_msg = await app.send_message(
        user_id,
        "⚡ מכין להעלאה...",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול פעולה ❌", callback_data="cancel_operation")]])
    )
    
    try:
        file_path = await app.download_media(
            user["file_id"],
            progress=lambda current, total: update_progress(
                current, total, progress_msg,
                user["start_time"], "מוריד", user["original_name"]
            )
        )
        
        user["file_path"] = file_path
        await progress_msg.delete()
        
        await app.send_message(
            user_id,
            f"📁 שם קובץ: `{user.get('new_filename', user['original_name'])}`\n"
            "📤 בחר פורמט העלאה:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("וידאו 🎥", callback_data="upload_video"),
                 InlineKeyboardButton("קובץ 📄", callback_data="upload_file")],
                [InlineKeyboardButton("ביטול ❌", callback_data="cancel")]
            ])
        )
        
    except Exception as e:
        logging.error(f"Download error: {e}")
        await cleanup_user_data(user_id)
        await progress_msg.edit("❌ שגיאה בהורדת הקובץ")

@app.on_callback_query(filters.regex(r"^upload_(video|file|cancel)$"))
async def handle_upload(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    action = query.data.split("_")[1]
    
    await query.answer()
    await query.message.delete()
    
    if action == "cancel":
        await cleanup_user_data(user_id)
        return await query.message.reply("❌ הפעולה בוטלה")
    
    user = user_data.get(user_id)
    if not user:
        return
    
    try:
        file_name = user.get("new_filename", user["original_name"])
        progress_msg = await app.send_message(
            user_id,
            f"⚡ מתחיל {action} העלאה...",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול פעולה ❌", callback_data="cancel_operation")]])
        )
        
        if action == "video":
            output_path = await process_video(user["file_path"], user_id)
            await app.send_video(
                user_id,
                output_path,
                file_name=file_name,
                progress=lambda current, total: update_progress(
                    current, total, progress_msg,
                    time.time(), "מעלה", file_name
                )
            )
        else:
            await app.send_document(
                user_id,
                user["file_path"],
                file_name=file_name,
                progress=lambda current, total: update_progress(
                    current, total, progress_msg,
                    time.time(), "מעלה", file_name
                )
            )
        
        await progress_msg.delete()
        await app.send_message(user_id, "✅ הקובץ הועלה בהצלחה!")
        
    except Exception as e:
        logging.error(f"Upload error: {e}")
        await progress_msg.edit("❌ שגיאה בהעלאת הקובץ")
    finally:
        await cleanup_user_data(user_id)
        for path in [user.get("file_path"), user.get("processed_path")]:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except:
                pass

async def process_video(input_path: str, user_id: int) -> str:
    output_path = f"processed_{user_id}.mp4"
    
    try:
        probe = ffmpeg.probe(input_path)
        duration = int(float(probe['format']['duration']))
        
        (
            ffmpeg_input(input_path)
            .output(output_path, vcodec='copy', acodec='copy')
            .run(overwrite_output=True)
        )
        
        return output_path
    except Exception as e:
        logging.error(f"Video processing error: {e}")
        raise e

@app.on_callback_query(filters.regex("^cancel_operation$"))
async def cancel_operation(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    await query.answer("🚫 מבטל פעולה...")
    await cleanup_user_data(user_id)
    await query.message.edit("❌ הפעולה בוטלה בהצלחה")

async def cleanup_user_data(user_id: int):
    if user_id in user_data:
        for path in [user_data[user_id].get("file_path"), 
                   user_data[user_id].get("processed_path")]:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except:
                pass
        del user_data[user_id]

# Thumbnail handlers
@app.on_message(filters.command("view_thumb"))
async def view_thumbnail(client: Client, message: Message):
    user_id = message.from_user.id
    thumbnail_path = f"{THUMBNAILS_DIR}/{user_id}.jpg"
    
    if os.path.exists(thumbnail_path):
        await message.reply_photo(
            thumbnail_path,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("מחק תמונה", callback_data="delete_thumb")]
            ])
        )
    else:
        await message.reply_text("לא נמצאה תמונה ממוזערת")

@app.on_message(filters.command("del_thumb"))
async def delete_thumbnail_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    thumbnail_path = f"{THUMBNAILS_DIR}/{user_id}.jpg"
    
    try:
        os.remove(thumbnail_path)
        await message.reply_text("✅ תמונה ממוזערת נמחקה")
    except FileNotFoundError:
        await message.reply_text("❌ לא נמצאה תמונה למחיקה")
    except Exception as e:
        await message.reply_text(f"❌ שגיאה במחיקה: {str(e)}")

@app.on_callback_query(filters.regex("delete_thumb"))
async def delete_thumbnail(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    thumbnail_path = f"{THUMBNAILS_DIR}/{user_id}.jpg"
    
    try:
        os.remove(thumbnail_path)
        await query.answer("✅ תמונה נמחקה")
        await query.message.edit_text("תמונה ממוזערת נמחקה")
    except Exception as e:
        await query.answer(f"❌ שגיאה: {str(e)}")

@app.on_message(filters.photo & filters.private)
async def save_thumbnail(client: Client, message: Message):
    user_id = message.from_user.id
    thumbnail_path = f"{THUMBNAILS_DIR}/{user_id}.jpg"
    
    try:
        await client.download_media(message.photo.file_id, file_name=thumbnail_path)
        await message.reply_text("✅ תמונה ממוזערת נשמרה!")
    except Exception as e:
        await message.reply_text(f"❌ שגיאה בשמירת תמונה: {str(e)}")

if __name__ == "__main__":
    app.run()
