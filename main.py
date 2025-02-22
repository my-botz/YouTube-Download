import os
import re
import time
import asyncio
import shutil
import string
from threading import Lock
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery
)
from yt_dlp import YoutubeDL
from collections import defaultdict
import requests
import subprocess
from datetime import timedelta

# ------ הגדרות סביבה ------
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
COOKIES_FILE = "cookies.txt"

# ------ בדיקת קובץ קוקיז ------
if not os.path.exists(COOKIES_FILE):
    raise RuntimeError("קובץ cookies.txt חסר! חובה להוסיף אותו לשרת")

# ------ אתחול הבוט ------
app = Client(
    "yt_dl_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ------ ניהול משאבים ------
user_locks = defaultdict(Lock)
active_tasks = {}
progress_data = defaultdict(dict)

# ------ פונקציות עזר ------
def sanitize_filename(name):
    valid_chars = f"-_.() {string.ascii_letters}{string.digits}"
    return ''.join(c for c in name if c in valid_chars).strip()[:50]

async def progress_updater(chat_id, message_id):
    while True:
        await asyncio.sleep(3)
        data = progress_data.get((chat_id, message_id))
        if not data or data.get('done'):
            break
        
        try:
            text = (
                f"**{data['status']}**\n\n"
                f"`[{data['bar']}]` **{data['progress']}%**\n"
                f"**מהירות:** `{data['speed']}`\n"
                f"**זמן משוער:** `{data['eta']}`\n"
                f"**שלב:** `{data['phase'].capitalize()}`"
            )
            await app.edit_message_text(
                chat_id, message_id, text,
                reply_markup=data.get('markup')
            )
        except:
            pass

def format_speed(speed_str):
    try:
        speed = float(speed_str.split(' ')[0])
        units = ['B/s', 'KB/s', 'MB/s', 'GB/s']
        for unit in units:
            if speed < 1024:
                return f"{speed:.2f} {unit}"
            speed /= 1024
        return f"{speed:.2f} GB/s"
    except:
        return "0B/s"

def get_ydl_opts(user_id, media_type):
    return {
        'outtmpl': f'dl/{user_id}/%(title)s.%(ext)s',
        'cookiefile': COOKIES_FILE,
        'progress_hooks': [lambda d: handle_progress(d, user_id)],
        'noplaylist': True,
        'verbose': False,
        'postprocessors': [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}
        ] if media_type == 'audio' else []
    }

def handle_progress(d, user_id):
    try:
        if d['status'] == 'downloading':
            percent = float(d['_percent_str'].rstrip('%'))
            eta = str(timedelta(seconds=int(d['_eta_str']))) if d['_eta_str'].isdigit() else '00:00'
            bar = '●' * int(percent // 10) + '◌' * (10 - int(percent // 10))
            
            progress_data[user_id].update({
                'status': 'מוריד מהיוטיוב',
                'phase': 'download',
                'progress': percent,
                'speed': format_speed(d['_speed_str']),
                'eta': eta,
                'bar': bar
            })
    except Exception as e:
        print(f"שגיאת מעקב: {e}")

# ------ מטפל בפקודות ------
@app.on_message(filters.command(["start", "help"]))
async def start_cmd(client, message):
    start_text = (
        "🎵 **ברוכים הבאים לבוט היוטיוב!** 🎥\n\n"
        "שלחו לי קישור יוטיוב ואני:\n"
        "1. אוריד את הסרטון/השיר\n"
        "2. אמיר לכם אותו ישירות לטלגרם!\n\n"
        "⚡ תמיכה בכל הפורמטים כולל 4K"
    )
    await message.reply(start_text)

@app.on_message(filters.text & filters.private)
async def handle_message(client, message):
    user_id = message.from_user.id
    if not user_locks[user_id].acquire(blocking=False):
        await message.reply("⏳ יש להמתין לסיום הפעולה הנוכחית!")
        return
    
    try:
        url = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+)', message.text).group()
        msg = await message.reply("🔍 בודק את הקישור...")
        
        with YoutubeDL({'cookiefile': COOKIES_FILE, 'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎵 אודיו", callback_data=f"type_audio_{user_id}"),
                InlineKeyboardButton("🎥 וידאו", callback_data=f"type_video_{user_id}")
            ]
        ])
        await msg.edit("📥 בחר פורמט:", reply_markup=keyboard)
    
    except Exception as e:
        await message.reply(f"❌ שגיאה: {str(e)}")
    finally:
        user_locks[user_id].release()

@app.on_callback_query()
async def handle_callback(client, query):
    user_id = query.from_user.id
    data = query.data
    
    if not user_locks[user_id].acquire(blocking=False):
        await query.answer("⏳ יש להמתין לסיום הפעולה הנוכחית!", show_alert=True)
        return
    
    try:
        if data.startswith('type_'):
            media_type = data.split('_')[1]
            await process_media_type(query, media_type, user_id)
        
        elif data.startswith('quality_'):
            await process_quality(query, user_id)
        
        elif data == 'cancel':
            await cancel_task(user_id, query.message)
            
    except Exception as e:
        await query.message.reply(f"❌ שגיאה: {str(e)}")
    finally:
        user_locks[user_id].release()

async def process_media_type(query, media_type, user_id):
    try:
        url = re.search(r'(https?://\S+)', query.message.reply_to_message.text).group()
        msg = await query.message.edit("🔍 מאתר איכויות זמינות...")
        
        with YoutubeDL({'cookiefile': COOKIES_FILE, 'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        formats = []
        if media_type == 'audio':
            formats = sorted(
                [f for f in info['formats'] if f.get('acodec') != 'none'],
                key=lambda x: x.get('abr', 0),
                reverse=True
            )
        else:
            formats = sorted(
                [f for f in info['formats'] if f.get('vcodec') != 'none'],
                key=lambda x: x.get('height', 0),
                reverse=True
            )
        
        buttons = []
        for fmt in formats[:5]:
            quality = f"{fmt['abr']}kbps" if media_type == 'audio' else f"{fmt['height']}p"
            buttons.append([InlineKeyboardButton(
                f"🎚 {quality}",
                callback_data=f"quality_{fmt['format_id']}_{media_type}_{user_id}"
            )])
        
        buttons.append([InlineKeyboardButton("🚫 ביטול", callback_data="cancel")])
        await msg.edit(
            "📊 בחר איכות:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    
    except Exception as e:
        await query.message.edit(f"❌ שגיאה: {str(e)}")

async def process_quality(query, user_id):
    try:
        _, format_id, media_type, _ = query.data.split('_')
        url = re.search(r'(https?://\S+)', query.message.reply_to_message.text).group()
        
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 ביטול", callback_data="cancel")]])
        msg = await query.message.edit("⏳ מתחיל בעיבוד...", reply_markup=markup)
        
        progress_data[user_id] = {
            'status': 'מתחיל...',
            'progress': 0,
            'speed': '0B/s',
            'eta': '00:00',
            'phase': 'init',
            'bar': '◌'*10,
            'done': False,
            'markup': markup
        }
        
        task = asyncio.create_task(download_and_upload(
            url, format_id, media_type, user_id, msg
        ))
        active_tasks[user_id] = task
        
        asyncio.create_task(progress_updater(msg.chat.id, msg.id))
    
    except Exception as e:
        await query.message.edit(f"❌ שגיאה: {str(e)}")

async def download_and_upload(url, format_id, media_type, user_id, msg):
    try:
        # שלב 1: הורדה
        opts = get_ydl_opts(user_id, media_type)
        opts['format'] = format_id
        
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
        
        # שלב 2: עיבוד קובץ
        sanitized = sanitize_filename(info['title'])
        output_path = f"dl/{user_id}/{sanitized}.{media_type}"
        os.rename(file_path, output_path)
        
        # שלב 3: העלאה
        await upload_media(output_path, media_type, info, user_id, msg)
        
    except asyncio.CancelledError:
        await msg.edit("❌ הפעולה בוטלה!")
        raise
    except Exception as e:
        await msg.edit(f"❌ שגיאה קריטית: {str(e)}")
    finally:
        progress_data[user_id]['done'] = True
        shutil.rmtree(f"dl/{user_id}", ignore_errors=True)
        active_tasks.pop(user_id, None)

async def upload_media(path, media_type, info, user_id, msg):
    try:
        caption = f"🎬 **{info['title']}**\n📤 הועלה ע\"י @{(await app.get_me()).username}"
        thumb = download_thumbnail(info['thumbnail'], user_id)
        
        progress_data[user_id].update({
            'status': 'מעלה לטלגרם',
            'phase': 'upload'
        })
        
        if media_type == 'audio':
            await app.send_audio(
                msg.chat.id, path,
                caption=caption,
                thumb=thumb,
                progress=lambda c, t: handle_upload_progress(c, t, user_id)
            )
        else:
            await app.send_video(
                msg.chat.id, path,
                caption=caption,
                thumb=thumb,
                progress=lambda c, t: handle_upload_progress(c, t, user_id)
            )
        
        await msg.delete()
    except Exception as e:
        await msg.edit(f"❌ שגיאה בהעלאה: {str(e)}")

def handle_upload_progress(current, total, user_id):
    try:
        progress = (current / total) * 100
        elapsed = time.time() - progress_data[user_id].get('start_time', time.time())
        speed = current / elapsed if elapsed > 0 else 0
        eta_seconds = int((total - current) / speed) if speed > 0 else 0
        
        progress_data[user_id].update({
            'progress': round(progress, 1),
            'speed': format_speed(speed),
            'eta': str(timedelta(seconds=eta_seconds)),
            'bar': '●' * int(progress // 10) + '◌' * (10 - int(progress // 10))
        })
    except:
        pass

async def cancel_task(user_id, msg):
    task = active_tasks.get(user_id)
    if task:
        task.cancel()
        await msg.edit("❌ הפעולה בוטלה!")
    else:
        await msg.edit("⚠️ אין פעולה פעילה לביטול!")

def download_thumbnail(url, user_id):
    try:
        path = f"dl/{user_id}/thumb.jpg"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(requests.get(url, timeout=10).content)
        return path
    except:
        return None

if __name__ == "__main__":
    os.makedirs("dl", exist_ok=True)
    print("🚀 הבוט פועל עם cookies.txt!")
    app.run()
