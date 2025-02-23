# bot.py
import os
import re
import time
import subprocess
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

from config import API_ID, API_HASH, BOT_TOKEN, ADMIN_ID, WAIT_TIME
from database import db
from utils import humanbytes, progress_bar, generate_thumbnail, parse_duration, get_storage_usage

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# יצירת תיקיות נחוצות
os.makedirs("downloads", exist_ok=True)
os.makedirs("thumbnails", exist_ok=True)

app = Client("file_converter_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# משתנים לעדכון התקדמות וביטול תהליכים
LAST_UPDATE = {}
MIN_UPDATE_INTERVAL = 5  # שניות
MIN_PERCENT_CHANGE = 10  # אחוז
CANCEL_TASKS = {}  # מילון לביטול פעולות לפי משתמש

# פונקציה לבדוק אם המשתמש רשאי לבצע פעולה
def can_user_act(user_id: int) -> (bool, int):
    premium_until = db.get_premium_until(user_id)
    now = time.time()
    if premium_until and premium_until > now:
        return True, 0
    last_action = db.get_last_action_time(user_id)
    if last_action and now - last_action < WAIT_TIME:
        remaining = int(WAIT_TIME - (now - last_action))
        return False, remaining
    return True, 0

# הודעת /start משודרגת
@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    welcome_text = (
        "👋 **ברוכים הבאים לבוט הממיר הקבצים!**\n\n"
        "שלח קובץ או וידאו, ושנה את שמו לפי הצורך. הבוט מציג התקדמות עם אחוזים, מהירות וזמן משוער.\n\n"
        "אפשרויות נוספות:\n"
        "• `/my_plan` – בדיקת תוכנית המשתמש\n"
        "• שליחת תמונה לשמירת תמונת ממוזערת\n\n"
        "בהצלחה!"
    )
    await message.reply_text(welcome_text, reply_to_message_id=message.id, parse_mode=ParseMode.MARKDOWN)

@app.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    active = db.get_active_task(user_id)
    CANCEL_TASKS[user_id] = True  # סימון ביטול תהליך
    if active:
        db.delete_active_task(user_id)
        await message.reply_text("❌ הפעולה בוטלה!", reply_to_message_id=message.id)
    else:
        await message.reply_text("⚠️ אין פעולה פעילה לביטול", reply_to_message_id=message.id)

@app.on_message(filters.command("view_thumb"))
async def view_thumb(client: Client, message: Message):
    thumb = db.get_thumbnail(message.from_user.id)
    if thumb:
        await client.send_photo(
            message.chat.id, 
            thumb, 
            caption="📷 התמונה הממוזערת שלך",
            reply_to_message_id=message.id
        )
    else:
        await message.reply_text("❌ אין תמונה ממוזערת שמורה", reply_to_message_id=message.id)

@app.on_message(filters.command("del_thumb"))
async def del_thumb(client: Client, message: Message):
    db.delete_thumbnail(message.from_user.id)
    await message.reply_text("✅ התמונה הממוזערת נמחקה", reply_to_message_id=message.id)

@app.on_message(filters.photo)
async def save_thumbnail(client: Client, message: Message):
    db.save_thumbnail(message.from_user.id, message.photo.file_id)
    await message.reply_text("✅ תמונה ממוזערת נשמרה בהצלחה", reply_to_message_id=message.id)

@app.on_message(filters.document | filters.video)
async def handle_file(client: Client, message: Message):
    user_id = message.from_user.id
    can_act, remaining = can_user_act(user_id)
    if not can_act:
        await message.reply_text(f"⚠️ המתן עוד {remaining} שניות לפני ביצוע פעולה חדשה.", reply_to_message_id=message.id)
        return

    # סימון הפעולה כפעילה
    db.set_original_message(user_id, message.id)
    db.delete_active_task(user_id)
    CANCEL_TASKS[user_id] = False  # אתחול ביטול

    # אם לא קיים שם חדש (לא בחרו לשנות), נשמור את השם המקורי של הקובץ
    if not db.get_new_name(user_id):
        if message.document and message.document.file_name:
            db.save_new_name(user_id, message.document.file_name)
        elif message.video and message.video.file_name:
            db.save_new_name(user_id, message.video.file_name)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ שנה שם", callback_data="rename_yes"),
            InlineKeyboardButton("🚫 המשך ללא שינוי", callback_data="rename_no")
        ]
    ])
    
    msg = await message.reply_text(
        "📤 האם ברצונך לשנות את שם הקובץ?\n\nבחר פורמט העלאה לאחר מכן.",
        reply_markup=keyboard,
        reply_to_message_id=message.id
    )
    db.add_active_task(user_id, msg.id)

@app.on_callback_query(filters.regex(r"^rename_(yes|no)"))
async def rename_choice(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    original_msg_id = db.get_original_message(user_id)
    action = query.data.split("_")[1]
    
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"שגיאה במחיקת הודעה: {e}")
    
    db.delete_active_task(user_id)
    
    if action == "yes":
        db.set_waiting_for_name(user_id, True)
        sent_msg = await client.send_message(
            chat_id=user_id,
            text="✍️ שלח את השם החדש עבור הקובץ:",
            reply_to_message_id=original_msg_id
        )
        db.add_active_task(user_id, sent_msg.id)
    else:
        await ask_upload_type(client, original_msg_id, user_id)

async def ask_upload_type(client: Client, original_msg_id: int, user_id: int):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎥 וידאו", callback_data="upload_video"),
            InlineKeyboardButton("📁 קובץ", callback_data="upload_file")
        ]
    ])
    
    msg = await client.send_message(
        chat_id=user_id,
        text="📤 בחר פורמט העלאה:",
        reply_markup=keyboard,
        reply_to_message_id=original_msg_id
    )
    db.add_active_task(user_id, msg.id)

@app.on_message(filters.text & ~filters.regex(r'^/') & filters.private)
async def handle_new_name(client: Client, message: Message):
    user_id = message.from_user.id
    original_msg_id = db.get_original_message(user_id)
    
    if db.is_waiting_for_name(user_id):
        try:
            await message.delete()
        except Exception:
            pass
        
        new_name = message.text.strip()
        db.save_new_name(user_id, new_name)
        db.set_waiting_for_name(user_id, False)
        
        await ask_upload_type(client, original_msg_id, user_id)

async def progress_callback(current: int, total: int, start_time: float, message: Message, action: str):
    try:
        user_id = message.chat.id
        # בדיקת ביטול – אם המשתמש ביקש ביטול, נזרוק חריגה
        if CANCEL_TASKS.get(user_id, False):
            raise Exception("Cancelled by user")

        now = time.time()
        should_update = False
        if user_id not in LAST_UPDATE:
            should_update = True
        else:
            time_diff = now - LAST_UPDATE[user_id]["time"]
            percent_diff = ((current/total)*100) - LAST_UPDATE[user_id]["percent"]
            if time_diff >= MIN_UPDATE_INTERVAL or percent_diff >= MIN_PERCENT_CHANGE:
                should_update = True
        
        if should_update:
            progress = await progress_bar(current, total, start_time)
            text = (
                f"**{'⬇️ מוריד' if action == 'download' else '⬆️ מעלה'} את הקובץ**\n\n"
                f"**גודל קובץ:** `{humanbytes(total)}`\n"
                f"{progress}"
            )
            
            await message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ בטל", callback_data="cancel")]]),
                parse_mode=ParseMode.MARKDOWN
            )
            LAST_UPDATE[user_id] = {
                "time": now,
                "percent": (current/total)*100
            }
    except Exception as e:
        logger.error(f"שגיאה בעדכון התקדמות: {e}")
        raise e  # כדי לאפס את התהליך במקרה של ביטול

@app.on_callback_query(filters.regex(r"^upload_(video|file)"))
async def upload_file(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    original_msg_id = db.get_original_message(user_id)
    upload_type = query.data.split("_")[1]
    
    # נערוך את הודעת הבחירה
    progress_msg = query.message
    try:
        await progress_msg.edit_text("⬇️ מתחיל בהורדה...", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"שגיאה בעדכון הודעת התקדמות: {e}")
    
    try:
        original_msg = await client.get_messages(chat_id=user_id, message_ids=original_msg_id)
        file = original_msg.video or original_msg.document
        if not file:
            return await query.answer("❌ קובץ לא נתמך", show_alert=True)
        
        start_time = time.time()
        # הורדת הקובץ
        download_path = await client.download_media(
            file.file_id,
            file_name=f"downloads/{file.file_id}",
            progress=progress_callback,
            progress_args=(start_time, progress_msg, "download")
        )
        
        # בדיקה במידה והמשתמש ביטל במהלך ההורדה
        if CANCEL_TASKS.get(user_id, False):
            await progress_msg.edit_text("❌ הפעולה בוטלה!")
            os.remove(download_path)
            return
        
        new_name = db.get_new_name(user_id)
        output_path = None
        
        if upload_type == "video":
            thumb_path = db.get_thumbnail(user_id) or generate_thumbnail(download_path, user_id)
            output_path = f"converted_{file.file_id}.mp4"
            try:
                # המרת וידאו באמצעות re-encoding (ניתן להתאים את הפרמטרים)
                subprocess.run([
                    "ffmpeg",
                    "-i", download_path,
                    "-c:v", "libx264",
                    "-crf", "23",
                    "-preset", "veryfast",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    output_path
                ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                logger.error(f"שגיאה בהמרת וידאו: {e}")
                await client.send_message(chat_id=user_id, text="❌ אירעה שגיאה בהמרת הווידאו", reply_to_message_id=original_msg_id)
                return
            
            await client.send_video(
                chat_id=user_id,
                video=output_path,
                thumb=thumb_path,
                caption=f"📁 שם קובץ: `{new_name}`" if new_name else None,
                progress=progress_callback,
                progress_args=(start_time, progress_msg, "upload"),
                reply_to_message_id=original_msg_id
            )
        else:
            await client.send_document(
                chat_id=user_id,
                document=download_path,
                file_name=new_name if new_name else None,
                progress=progress_callback,
                progress_args=(start_time, progress_msg, "upload"),
                reply_to_message_id=original_msg_id
            )
        
        # ניקוי קבצים
        os.remove(download_path)
        if output_path and os.path.exists(output_path):
            os.remove(output_path)
        
        # עדכון זמן הפעולה וספירת פעולות (למשתמש לא פרימיום)
        db.set_last_action_time(user_id, time.time())
        db.add_action_count(user_id)
        await progress_msg.delete()
        
    except Exception as e:
        logger.error(f"שגיאה בהעלאה: {e}")
        try:
            await query.message.edit_text("❌ אירעה שגיאה בעיבוד הקובץ", reply_to_message_id=original_msg_id)
        except Exception:
            pass
    finally:
        db.delete_active_task(user_id)
        db.delete_new_name(user_id)
        if user_id in LAST_UPDATE:
            del LAST_UPDATE[user_id]
        if user_id in CANCEL_TASKS:
            CANCEL_TASKS.pop(user_id)

@app.on_callback_query(filters.regex("^cancel"))
async def cancel_process(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    db.delete_active_task(user_id)
    CANCEL_TASKS[user_id] = True  # סימון ביטול
    try:
        await query.answer("הפעולה בוטלה", show_alert=True)
        await query.message.edit_text("❌ הפעולה בוטלה!")
    except Exception as e:
        logger.error(f"שגיאה בטיפול בביטול: {e}")

# פקודות תכנית משתמש ופרימיום
@app.on_message(filters.command("my_plan"))
async def my_plan(client: Client, message: Message):
    user_id = message.from_user.id
    premium_until = db.get_premium_until(user_id)
    now = time.time()
    if premium_until and premium_until > now:
        remaining = int(premium_until - now)
        plan_info = f"✅ יש לך פרימיום למשך עוד {remaining // 3600} שעות ו-{(remaining % 3600) // 60} דקות."
    else:
        last_action = db.get_last_action_time(user_id)
        wait = max(0, int(WAIT_TIME - (now - last_action))) if last_action else 0
        plan_info = f"🆓 חינמי. זמינות פעולה: {'מיידית' if wait==0 else f'עוד {wait} שניות'}."
    
    plans = ("תוכניות זמינות:\n"
             "1. חינמי - פעולה אחת כל 5 דקות\n"
             "2. פרימיום - ללא הגבלות (ניתן לשדרוג ע\"י מנהל)")
    await message.reply_text(f"📊 התוכנית שלך:\n{plan_info}\n\n{plans}", reply_to_message_id=message.id)

# פקודות מנהל
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

@app.on_message(filters.command("add"))
async def add_premium(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ אין לך הרשאה לביצוע פעולה זו", reply_to_message_id=message.id)
    try:
        parts = message.text.split()
        target_id = int(parts[1])
        duration_str = parts[2]
        duration = parse_duration(duration_str)
        if duration <= 0:
            return await message.reply_text("❌ פורמט זמן לא תקין", reply_to_message_id=message.id)
        new_premium = time.time() + duration
        db.set_premium_until(target_id, new_premium)
        await message.reply_text(f"✅ הוספתי פרימיום למשתמש {target_id} לתקופה של {duration_str}", reply_to_message_id=message.id)
        try:
            await client.send_message(chat_id=target_id, text=f"🎉 קיבלת פרימיום לבוט למשך {duration_str}!")
        except Exception as e:
            logger.error(f"לא ניתן לשלוח הודעה למשתמש {target_id}: {e}")
    except Exception as e:
        logger.error(e)
        await message.reply_text("❌ שימוש: /add <user_id> <duration>\nלדוגמא: /add 123456 20d", reply_to_message_id=message.id)

@app.on_message(filters.command("stop"))
async def stop_premium(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ אין לך הרשאה לביצוע פעולה זו", reply_to_message_id=message.id)
    try:
        parts = message.text.split()
        target_id = int(parts[1])
        db.remove_premium(target_id)
        await message.reply_text(f"✅ הופסק הפרימיום למשתמש {target_id}", reply_to_message_id=message.id)
        try:
            await client.send_message(chat_id=target_id, text="⚠️ הפרימיום שלך בוטל על ידי המנהל.")
        except Exception as e:
            logger.error(f"לא ניתן לשלוח הודעה למשתמש {target_id}: {e}")
    except Exception as e:
        logger.error(e)
        await message.reply_text("❌ שימוש: /stop <user_id>", reply_to_message_id=message.id)

@app.on_message(filters.command("premiums"))
async def list_premiums(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ אין לך הרשאה לביצוע פעולה זו", reply_to_message_id=message.id)
    users = db.get_all_users()
    now = time.time()
    premium_users = [uid for uid, data in users.items() if data.get("premium_until", 0) > now]
    text = "משתמשי פרימיום:\n" + "\n".join(premium_users) if premium_users else "אין משתמשי פרימיום"
    await message.reply_text(text, reply_to_message_id=message.id)

@app.on_message(filters.command("stats"))
async def stats(client: Client, message: Message):
    if not is_admin(message.from_user.id):
        return await message.reply_text("❌ אין לך הרשאה לביצוע פעולה זו", reply_to_message_id=message.id)
    users = db.get_all_users()
    total_users = len(users)
    now = time.time()
    premium_users = [uid for uid, data in users.items() if data.get("premium_until", 0) > now]
    total_premium = len(premium_users)
    total_actions = sum(data.get("actions_count", 0) for data in users.values())
    downloads_usage = humanbytes(get_storage_usage("downloads"))
    thumbs_usage = humanbytes(get_storage_usage("thumbnails"))
    text = (
        f"📊 סטטיסטיקות:\n"
        f"משתמשים: {total_users}\n"
        f"משתמשי פרימיום: {total_premium}\n"
        f"פעולות: {total_actions}\n"
        f"שטח הורדות: {downloads_usage}\n"
        f"שטח תמונות: {thumbs_usage}\n"
        f"מצב השרת: תקין"
    )
    await message.reply_text(text, reply_to_message_id=message.id)
