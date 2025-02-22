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

# הגדרות סביבה
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# אתחול הבוט
app = Client(
    "yt_dl_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ניהול משאבים
user_locks = defaultdict(Lock)
active_tasks = {}
progress_data = defaultdict(dict)

# פונקציות עזר
def sanitize_filename(name):
    valid_chars = f"-_.() {string.ascii_letters}{string.digits}"
    return ''.join(c for c in name if c in valid_chars).strip()[:50]

async def edit_progress(chat_id, message_id):
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

def format_speed(speed):
    try:
        units = ['B/s', 'KB/s', 'MB/s', 'GB/s']
        speed = float(speed)
        for i, unit in enumerate(units):
            if speed < 1024 or i == len(units)-1:
                return f"{speed:.2f} {unit}"
            speed /= 1024
    except:
        return "0B/s"

def get_ydl_opts(user_id, media_type):
    opts = {
        'outtmpl': f'dl/{user_id}/%(title)s.%(ext)s',
        'progress_hooks': [lambda d: progress_hook(d, user_id)],
        'noplaylist': True,
        'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
        'verbose': False
    }
    
    if media_type == 'audio':
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320'
        }]
    return opts

def progress_hook(d, user_id):
    try:
        data = progress_data[user_id]
        if d['status'] == 'downloading':
            percent = float(d['_percent_str'].strip('%'))
            eta = str(timedelta(seconds=int(d['_eta_str']))) if d['_eta_str'].isdigit() else '00:00'
            bar = '●' * int(percent // 10) + '◌' * (10 - int(percent // 10))
            
            data.update({
                'status': 'מוריד מהיוטיוב',
                'phase': 'download',
                'progress': percent,
                'speed': format_speed(d['_speed_str'].split(' ')[0]),
                'eta': eta,
                'bar': bar
            })
    except Exception as e:
        print(f"Progress error: {e}")

# מטפל בפקודות
@app.on_message(filters.command(["start", "help"]))
async def start(client, message):
    text = (
        "👋 שלום! אני בוט להורדת מדיה מיוטיוב\n"
        "📤 שלחו לי קישור ואבחר לכם אפשרויות הורדה\n"
        "⚡ תמיכה באודיו (MP3) ווידאו עד 4K"
    )
    await message.reply(text)

@app.on_message(filters.text & filters.private)
async def handle_url(client, message):
    user_id = message.from_user.id
    
    if not user_locks[user_id].acquire(blocking=False):
        await message.reply("⏳ יש להמתין לסיום הפעולה הנוכחית!")
        return
    
    try:
        url = re.findall(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+)', message.text)[0]
        msg = await message.reply("🔍 בודק קישור...")
        
        with YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎵 אודיו", callback_data=f"type_audio_{user_id}"),
                InlineKeyboardButton("🎥 וידאו", callback_data=f"type_video_{user_id}")
            ]
        ])
        await msg.edit("📥 בחרו פורמט:", reply_markup=keyboard)
    
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
        url = re.findall(r'(https?://\S+)', query.message.reply_to_message.text)[0]
        msg = await query.message.edit("🔍 מאתר איכויות...")
        
        with YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        
        formats = []
        if media_type == 'audio':
            formats = [f for f in info['formats'] if f.get('acodec') != 'none']
            formats = sorted(formats, key=lambda x: x.get('abr', 0), reverse=True)
        else:
            formats = [f for f in info['formats'] if f.get('vcodec') != 'none']
            formats = sorted(formats, key=lambda x: x.get('height', 0), reverse=True)
        
        buttons = []
        for fmt in formats[:5]:
            quality = f"{fmt['abr']}kbps" if media_type == 'audio' else f"{fmt['height']}p"
            buttons.append([
                InlineKeyboardButton(
                    f"🎚 {quality}",
                    callback_data=f"quality_{fmt['format_id']}_{media_type}_{user_id}"
                )
            ])
        
        buttons.append([InlineKeyboardButton("🚫 ביטול", callback_data="cancel")])
        await msg.edit(
            "📊 בחרו איכות:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    
    except Exception as e:
        await query.message.reply(f"❌ שגיאה: {str(e)}")

async def process_quality(query, user_id):
    try:
        _, format_id, media_type, uid = query.data.split('_')
        url = re.findall(r'(https?://\S+)', query.message.reply_to_message.text)[0]
        
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
        
        task = asyncio.create_task(download_and_send(
            url, format_id, media_type, user_id, msg
        ))
        active_tasks[user_id] = task
        
        await asyncio.gather(task, edit_progress(msg.chat.id, msg.id))
    
    except Exception as e:
        await query.message.reply(f"❌ שגיאה: {str(e)}")

async def download_and_send(url, format_id, media_type, user_id, msg):
    try:
        # הורדה
        opts = get_ydl_opts(user_id, media_type)
        opts['format'] = format_id
        
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
        
        # המרה והעלאה
        sanitized = sanitize_filename(info['title'])
        output_path = f"dl/{user_id}/{sanitized}.{media_type}"
        os.rename(file_path, output_path)
        
        # העלאה
        await upload_file(output_path, media_type, info, user_id, msg)
        
    except asyncio.CancelledError:
        await msg.edit("❌ הפעולה בוטלה!")
        raise
    except Exception as e:
        await msg.edit(f"❌ שגיאה: {str(e)}")
    finally:
        progress_data[user_id]['done'] = True
        shutil.rmtree(f"dl/{user_id}", ignore_errors=True)
        active_tasks.pop(user_id, None)

async def upload_file(path, media_type, info, user_id, msg):
    try:
        caption = f"🎬 {info['title']}\n📤 הועלה ע\"י @{(await app.get_me()).username}"
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
                progress=lambda c, t: upload_progress(c, t, user_id)
            )
        else:
            await app.send_video(
                msg.chat.id, path,
                caption=caption,
                thumb=thumb,
                progress=lambda c, t: upload_progress(c, t, user_id)
            )
        
        await msg.delete()
    except Exception as e:
        await msg.edit(f"❌ שגיאה בהעלאה: {str(e)}")

def upload_progress(current, total, user_id):
    try:
        progress = (current / total) * 100
        elapsed = time.time() - progress_data[user_id].get('start', time.time())
        speed = current / elapsed if elapsed > 0 else 0
        
        progress_data[user_id].update({
            'progress': round(progress, 1),
            'speed': format_speed(speed),
            'eta': str(timedelta(seconds=int((total - current)/speed))) if speed > 0 else '00:00',
            'bar': '●' * int(progress//10) + '◌' * (10 - int(progress//10))
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
            f.write(requests.get(url).content)
        return path
    except:
        return None

if __name__ == "__main__":
    os.makedirs("dl", exist_ok=True)
    print("🚀 הבוט פועל וממתין לבקשות!")
    app.run()
