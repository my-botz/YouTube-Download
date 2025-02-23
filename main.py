# main.py
import os
import re
import time
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Union
from threading import Thread
from flask import Flask

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.enums import ParseMode

# הגדרות לוג
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# טעינת משתני סביבה
API_ID = int(os.environ.get("API_ID", 12345))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8000))

# יצירת תיקיות נחוצות
Path("downloads").mkdir(exist_ok=True)
Path("thumbnails").mkdir(exist_ok=True)

class Database:
    def __init__(self):
        self.file_path = "data.json"
        self.data = self._load_data()

    def _load_data(self) -> Dict:
        if not os.path.exists(self.file_path):
            return {"users": {}}
        with open(self.file_path, "r") as f:
            return json.load(f)

    def _save(self):
        with open(self.file_path, "w") as f:
            json.dump(self.data, f, indent=2)

    def save_thumbnail(self, user_id: int, file_id: str):
        self.data["users"].setdefault(str(user_id), {})["thumbnail"] = file_id
        self._save()

    def get_thumbnail(self, user_id: int) -> Union[str, None]:
        return self.data["users"].get(str(user_id), {}).get("thumbnail")

    def delete_thumbnail(self, user_id: int):
        if str(user_id) in self.data["users"]:
            self.data["users"][str(user_id)].pop("thumbnail", None)
            self._save()

    def add_active_task(self, user_id: int, message_id: int):
        self.data["users"].setdefault(str(user_id), {})["active_task"] = message_id
        self._save()

    def delete_active_task(self, user_id: int):
        if str(user_id) in self.data["users"]:
            self.data["users"][str(user_id)].pop("active_task", None)
            self._save()

    def set_waiting_for_name(self, user_id: int, status: bool):
        self.data["users"].setdefault(str(user_id), {})["waiting_for_name"] = status
        self._save()

    def is_waiting_for_name(self, user_id: int) -> bool:
        return self.data["users"].get(str(user_id), {}).get("waiting_for_name", False)

db = Database()

app = Client(
    "file_converter_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ================= פונקציות עזר =================
def humanbytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size)
    i = 0
    while size >= 1024 and i < len(units)-1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

async def progress_bar(current: int, total: int, start_time: float) -> str:
    elapsed = time.time() - start_time
    speed = current / elapsed if elapsed > 0 else 0
    percent = current * 100 / total
    filled = int(20 * percent // 100)
    bar = '●' * filled + '◌' * (20 - filled)
    
    speed_str = f"{humanbytes(speed)}/s" if speed > 0 else "0 B/s"
    eta = (total - current) / speed if speed > 0 else 0
    
    return (
        f"[{bar}] {percent:.2f}%\n"
        f"**מהירות:** {speed_str}\n"
        f"**זמן משוער:** {eta:.1f}s"
    )

def generate_thumbnail(video_path: str, user_id: int):
    output_path = f"thumbnails/{user_id}.jpg"
    subprocess.run([
        "ffmpeg",
        "-i", video_path,
        "-ss", "00:00:01",
        "-vframes", "1",
        "-vf", "scale=320:-1",
        output_path
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path

# ================= האנדלרים =================
@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    await message.reply_text("👋 שלום! שלח לי קובץ או וידאו כדי להתחיל")

@app.on_message(filters.command("view_thumb"))
async def view_thumb(client: Client, message: Message):
    thumb = db.get_thumbnail(message.from_user.id)
    if thumb:
        await client.send_photo(message.chat.id, thumb, caption="📷 התמונה הממוזערת שלך")
    else:
        await message.reply_text("❌ אין תמונה ממוזערת שמורה")

@app.on_message(filters.command("del_thumb"))
async def del_thumb(client: Client, message: Message):
    db.delete_thumbnail(message.from_user.id)
    await message.reply_text("✅ התמונה הממוזערת נמחקה")

@app.on_message(filters.photo)
async def save_thumbnail(client: Client, message: Message):
    db.save_thumbnail(message.from_user.id, message.photo.file_id)
    await message.reply_text("✅ תמונה ממוזערת נשמרה בהצלחה")

@app.on_message(filters.document | filters.video)
async def handle_file(client: Client, message: Message):
    user_id = message.from_user.id
    if db.data["users"].get(str(user_id), {}).get("active_task"):
        return await message.reply_text("⚠️ יש לך משימה פעילה, נא להמתין לסיומה")
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ שנה שם", callback_data="rename_yes"),
            InlineKeyboardButton("🚫 המשך ללא שינוי", callback_data="rename_no")
        ]
    ])
    
    msg = await message.reply_text(
        "📁 האם ברצונך לשנות את שם הקובץ?",
        reply_markup=keyboard
    )
    db.add_active_task(user_id, msg.id)

@app.on_callback_query(filters.regex(r"^rename_(yes|no)"))
async def rename_choice(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    action = query.data.split("_")[1]
    
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"שגיאה במחיקת הודעה: {e}")
    
    db.delete_active_task(user_id)
    
    if action == "yes":
        db.set_waiting_for_name(user_id, True)
        sent_msg = await query.message.reply("✍️ שלח את השם החדש עבור הקובץ:")
        db.add_active_task(user_id, sent_msg.id)
    else:
        await ask_upload_type(client, query.message, user_id)

async def ask_upload_type(client: Client, message: Message, user_id: int):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 וידאו", callback_data="upload_video"),
            InlineKeyboardButton("📁 קובץ", callback_data="upload_file")
        ]
    ])
    
    msg = await message.reply(
        "📤 בחר פורמט העלאה:",
        reply_markup=keyboard
    )
    db.add_active_task(user_id, msg.id)

@app.on_message(filters.text & ~filters.regex(r'^/') & filters.private)
async def handle_new_name(client: Client, message: Message):
    user_id = message.from_user.id
    
    if db.is_waiting_for_name(user_id):
        try:
            await message.delete()
        except:
            pass
        
        new_name = message.text
        user_data = db.data["users"].setdefault(str(user_id), {})
        user_data["new_name"] = new_name
        user_data.pop("waiting_for_name", None)
        db._save()
        
        await ask_upload_type(client, message, user_id)
        db.set_waiting_for_name(user_id, False)

@app.on_callback_query(filters.regex(r"^upload_(video|file)"))
async def upload_file(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    upload_type = query.data.split("_")[1]
    
    try:
        original_msg = query.message.reply_to_message
        if not original_msg:
            return await query.answer("❌ שגיאה: הודעה לא נמצאה", show_alert=True)
        
        file = original_msg.video or original_msg.document
        if not file:
            return await query.answer("❌ קובץ לא נתמך", show_alert=True)
        
        start_time = time.time()
        progress_msg = await query.message.reply_text("⬇️ מתחיל בהורדה...")
        
        # הורדת הקובץ
        download_path = await client.download_media(
            file.file_id,
            file_name=f"downloads/{file.file_id}",
            progress=progress_callback,
            progress_args=(start_time, progress_msg, "download")
        )
        
        # עיבוד קובץ
        new_name = db.data["users"].get(str(user_id), {}).get("new_name")
        output_path = None
        
        if upload_type == "video":
            # יצירת תמונה ממוזערת
            thumb_path = db.get_thumbnail(user_id) or generate_thumbnail(download_path, user_id)
            
            # המרת פורמט
            output_path = f"converted_{file.file_id}.mp4"
            subprocess.run([
                "ffmpeg",
                "-i", download_path,
                "-c", "copy",
                output_path
            ], check=True)
            
            # העלאת וידאו
            await client.send_video(
                chat_id=user_id,
                video=output_path,
                thumb=thumb_path,
                caption=f"📁 שם קובץ: `{new_name}`" if new_name else None,
                progress=progress_callback,
                progress_args=(start_time, progress_msg, "upload")
            )
        else:
            # העלאת קובץ
            await client.send_document(
                chat_id=user_id,
                document=download_path,
                file_name=new_name if new_name else None,
                progress=progress_callback,
                progress_args=(start_time, progress_msg, "upload")
            )
        
        # ניקוי
        os.remove(download_path)
        if output_path and os.path.exists(output_path):
            os.remove(output_path)
            
        await progress_msg.delete()
        
    except Exception as e:
        logger.error(f"שגיאה בהעלאה: {e}")
        await query.message.reply_text("❌ אירעה שגיאה בעיבוד הקובץ")
    finally:
        db.delete_active_task(user_id)
        db.data["users"].get(str(user_id), {}).pop("new_name", None)
        db._save()

async def progress_callback(current: int, total: int, start_time: float, message: Message, action: str):
    try:
        progress = await progress_bar(current, total, start_time)
        text = (
            f"**{'⬇️ מוריד' if action == 'download' else '⬆️ מעלה'} את הקובץ**\n\n"
            f"**גודל קובץ:** `{humanbytes(total)}`\n"
            f"{progress}"
        )
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ בטל", callback_data="cancel")]])
        await message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"שגיאה בעדכון התקדמות: {e}")

@app.on_callback_query(filters.regex("^cancel"))
async def cancel_process(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    db.delete_active_task(user_id)
    await query.message.edit_text("❌ הפעולה בוטלה!")

# ================= שרת Flask לבריאות =================
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "OK"

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    # הפעלת שרת Flask ב-thread נפרד
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # הפעלת הבוט
    app.run()
