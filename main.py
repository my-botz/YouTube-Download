import os
import logging
import time
import math
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

# הגדרות לוג
logging.basicConfig(
    level=logging.INFO,
    format='▸ %(asctime)s ▸ %(levelname)s ▸ %(message)s',
    datefmt='%H:%M:%S'
)

# הגדרות אפליקציה
app = Client(
    "file_converter_bot",
    api_id=os.getenv("API_ID"),
    api_hash=os.getenv("API_HASH"),
    bot_token=os.getenv("BOT_TOKEN")
)

# הגדרות כללים
THUMBNAILS_DIR = "thumbnails"
Path(THUMBNAILS_DIR).mkdir(exist_ok=True)
user_data: Dict[int, dict] = {}

# שרת HTTP לבריאות
async def health_check(request):
    return web.Response(text="OK")

@ app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    welcome_text = """
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
        welcome_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("התחל המרה 🚀", callback_data="start_conversion")]
        ])
    )

@ app.on_callback_query(filters.regex("^start_conversion$"))
async def start_conversion(client: Client, query: CallbackQuery):
    await query.answer()
    await query.message.delete()
    await query.message.reply("📤 אנא שלח קובץ להמרה:")

@ app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in user_data:
        await cleanup_user_data(user_id)
        await message.reply("✅ כל הפעולות בוטלו בהצלחה!")
    else:
        await message.reply("ℹ️ אין פעולות פעילות לביטול")

@ app.on_message(filters.document | filters.video)
async def handle_file(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_data.get(user_id, {}).get('busy'):
        return await message.reply("""
        ⚠️ **פעולה קיימת בתהליך!**
        יש להשלים את הפעולה הנוכחית או להשתמש ב/cancel
        """)
    
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

@ app.on_callback_query(filters.regex(r"^rename_(yes|no|cancel)$"))
async def handle_rename(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    action = query.data.split("_")[1]
    
    await query.answer()
    await query.message.delete()
    
    if action == "cancel":
        await cleanup_user_data(user_id)
        return await query.message.reply("✅ הפעולה בוטלה בהצלחה")
    
    if action == "no":
        user_data[user_id]["new_filename"] = user_data[user_id]["original_name"]
        await ask_upload_type(user_id)
    else:
        msg = await query.message.reply(
            "✍️ **שלח את השם החדש לקובץ:**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול ❌", callback_data="cancel")]])
        )
        user_data[user_id]["messages_to_delete"].append(msg.id)

@ app.on_message(filters.private & filters.text & ~filters.command(["start","view_thumb","del_thumb","cancel"]))
async def handle_filename(client: Client, message: Message):
    user_id = message.from_user.id
    if not user_data.get(user_id, {}).get('busy'):
        return
    
    user_data[user_id]["new_filename"] = message.text
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
            progress=create_progress_callback(progress_msg, "הורדה")
        )
        
        user["file_path"] = file_path
        await progress_msg.delete()
        
        await app.send_message(
            user_id,
            f"""
            📁 **פרטי קובץ:**
            ▸ שם: `{user.get('new_filename', user['original_name'])}`
            ▸ גודל: {humanbytes(os.path.getsize(file_path))}
            
            📤 **בחר פורמט העלאה:**
            """,
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

@ app.on_callback_query(filters.regex(r"^upload_(video|file|cancel)$"))
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
        
        file_name = user.get("new_filename", user["original_name"])
        
        if action == "video":
            output_path = await process_video(user["file_path"], user_id)
            await app.send_video(
                user_id,
                output_path,
                file_name=file_name,
                progress=create_progress_callback(progress_msg, "העלאה")
            )
        else:
            await app.send_document(
                user_id,
                user["file_path"],
                file_name=file_name,
                progress=create_progress_callback(progress_msg, "העלאה")
            )
        
        await progress_msg.delete()
        await app.send_message(user_id, "✅ **הקובץ הועלה בהצלחה!**")
        
    except Exception as e:
        logging.error(f"שגיאת העלאה: {e}")
        await progress_msg.edit("❌ **שגיאה בהעלאת הקובץ**")
    finally:
        await cleanup_user_data(user_id)

# פונקציות עזר
def create_progress_callback(message: Message, operation: str):
    async def wrapper(current, total):
        try:
            await update_progress(
                current=current,
                total=total,
                message=message,
                operation=operation,
                file_name="קובץ"
            )
        except Exception as e:
            logging.error(f"שגיאת עדכון התקדמות: {e}")
    return wrapper

async def update_progress(current: int, total: int, message: Message, operation: str, file_name: str):
    percent = current * 100 / total
    bar = f"[{'●' * int(percent//10)}{'○' * (10 - int(percent//10))}]"
    speed = humanbytes(current / (time.time() - user_data[message.from_user.id]['start_time']))
    
    text = f"""
    🚀 **{operation} מתבצעת**
    
    ▸ {bar} {percent:.1f}%
    ▸ 📁 שם: `{file_name}`
    ▸ ⚡ מהירות: {speed}/s
    ▸ 🕒 זמן משוער: {estimate_time(current, total)}
    """
    
    try:
        await message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול פעולה ❌", callback_data="cancel")]])
    except BadRequest:
        pass

def estimate_time(current, total):
    elapsed = time.time() - user_data['start_time']
    remaining = (total - current) * elapsed / current if current else 0
    return f"{int(remaining//60)}:{int(remaining%60):02d} דקות"

def humanbytes(size: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024:
            break
        size /= 1024
    return f"{size:.2f} {unit}"

# ניהול קבצים
async def process_video(input_path: str, user_id: int) -> str:
    output_path = f"processed_{user_id}.mp4"
    try:
        (
            ffmpeg_input(input_path)
            .output(output_path, vcodec='copy', acodec='copy')
            .run(overwrite_output=True)
        return output_path
    except Exception as e:
        logging.error(f"שגיאת עיבוד וידאו: {e}")
        raise e

async def cleanup_user_data(user_id: int):
    if user_id in user_data:
        for path in [user_data[user_id].get('file_path'), user_data[user_id].get('processed_path')]:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except:
                pass
        del user_data[user_id]

# הפעלת שרת בריאות
async def run_server():
    server = web.Server(health_check)
    runner = web.ServerRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8000)
    await site.start()
    logging.info("שרת בריאות פועל בפורט 8000")

if __name__ == "__main__":
    app.start()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_server())
    app.run()
