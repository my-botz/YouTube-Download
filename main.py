import os
import logging
import asyncio
from pathlib import Path
from typing import Dict, Optional
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

def is_busy(user_id: int) -> bool:
    return user_data.get(user_id, {}).get('busy', False)

async def cleanup_user_data(user_id: int):
    if user_id in user_data:
        del user_data[user_id]

@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    start_text = (
        "👋 שלום! אני בוט להמרת קבצים\n\n"
        "✅ אפשרויות עיקריות:\n"
        "- המרת קבצים לוידאו/מסמך\n"
        "- שינוי שם קבצים\n"
        "- ניהול תמונות ממוזערות\n\n"
        "📌 פקודות חשובות:\n"
        "/view_thumb - הצג תמונה ממוזערת\n"
        "/del_thumb - מחק תמונה ממוזערת\n\n"
        "⚠️ העלאה מרבית: 2GB"
    )
    await message.reply_text(start_text)

@app.on_message(filters.document | filters.video)
async def handle_file(client: Client, message: Message):
    user_id = message.from_user.id
    if is_busy(user_id):
        return await message.reply("⚠️ יש להשלים את הפעולה הנוכחית לפני תחילת פעולה חדשה")

    file = message.video or message.document
    user_data[user_id] = {
        "busy": True,
        "file_id": file.file_id,
        "original_name": file.file_name,
        "media_type": "video" if message.video else "document",
        "messages_to_delete": [message.id]
    }

    await message.reply_text(
        "האם ברצונך לשנות את שם הקובץ?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("שנה שם", callback_data="rename_yes"),
            InlineKeyboardButton("לא לשנות", callback_data="rename_no")],
            [InlineKeyboardButton("ביטול פעולה", callback_data="cancel")]
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
    
    if action == "yes":
        msg = await query.message.reply(
            "📝 אנא שלח את השם החדש לקובץ:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ביטול", callback_data="cancel")]])
        user_data[user_id]["messages_to_delete"].append(msg.id)
        return

    await ask_upload_type(user_id)

async def ask_upload_type(user_id: int):
    user = user_data.get(user_id)
    if not user:
        return

    file_name = user.get("new_filename", user["original_name"])
    
    try:
        msg = await app.send_message(
            chat_id=user_id,
            text=f"📁 שם קובץ: {file_name}\n\nבחר פורמט העלאה:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎥 וידאו", callback_data="upload_video"),
                [InlineKeyboardButton("📄 קובץ", callback_data="upload_file")],
                [InlineKeyboardButton("ביטול", callback_data="cancel")]
            ])
        )
        user["messages_to_delete"].append(msg.id)
    except Exception as e:
        logging.error(f"Error asking upload type: {e}")
        await cleanup_user_data(user_id)

@app.on_message(filters.private & filters.text & ~filters.command("start|view_thumb|del_thumb"))
async def handle_filename(client: Client, message: Message):
    user_id = message.from_user.id
    if not is_busy(user_id):
        return

    user_data[user_id]["new_filename"] = message.text
    user_data[user_id]["messages_to_delete"].append(message.id)

    try:
        await client.delete_messages(
            chat_id=user_id,
            message_ids=user_data[user_id]["messages_to_delete"]
        )
    except Exception as e:
        logging.error(f"Error deleting messages: {e}")

    await ask_upload_type(user_id)

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
        file_path = await client.download_media(user["file_id"])
        processed_path = await process_media(file_path, user_id, action)

        thumbnail_path = f"{THUMBNAILS_DIR}/{user_id}.jpg"
        if not os.path.exists(thumbnail_path) and action == "video":
            thumbnail_path = await generate_thumbnail(file_path)

        upload_args = {
            "chat_id": user_id,
            "file_name": user.get("new_filename", user["original_name"]),
            "thumb": thumbnail_path if action == "video" else None
        }

        if action == "video":
            await client.send_video(video=processed_path, **upload_args)
        else:
            await client.send_document(document=processed_path, **upload_args)

        await query.message.reply("✅ הקובץ הועלה בהצלחה!")
    except Exception as e:
        logging.error(f"Upload error: {e}")
        await query.message.reply("❌ שגיאה בעיבוד הקובץ")
    finally:
        await cleanup_user_data(user_id)
        for path in [file_path, processed_path]:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                logging.error(f"Error cleaning files: {e}")

async def process_media(file_path: str, user_id: int, media_type: str) -> str:
    if media_type == "video":
        output_path = f"processed_{user_id}.mp4"
        probe = ffmpeg.probe(file_path)
        duration = int(float(probe['format']['duration']))
        
        (
            ffmpeg_input(file_path)
            .output(output_path, vcodec='copy', acodec='copy')
            .run(overwrite_output=True)
        )
        return output_path
    return file_path

async def generate_thumbnail(video_path: str) -> Optional[str]:
    try:
        thumbnail_path = f"thumbnail_{os.path.basename(video_path)}.jpg"
        (
            ffmpeg_input(video_path, ss='00:00:00')
            .output(thumbnail_path, vframes=1)
            .run(overwrite_output=True)
        )
        return thumbnail_path
    except Exception as e:
        logging.error(f"Thumbnail error: {e}")
        return None

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
