import os
import re
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup,
    InlineKeyboardButton, CallbackQuery
)
from yt_dlp import YoutubeDL
from collections import defaultdict
import requests
import subprocess

# ========== הגדרות סביבה ========== #
API_ID = int(os.environ.get("API_ID", 12345))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token")
COOKIES_FILE = "cookies.txt"

# ========== בדיקות התקנה ========== #
if not os.path.exists(COOKIES_FILE):
    raise SystemExit("❌ **שגיאה קריטית**: קובץ cookies.txt חסר!")

# ========== אתחול הבוט ========== #
app = Client("yt_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ========== מאגר נתונים ========== #
user_data = defaultdict(dict)
progress_data = defaultdict(dict)

# ========== פונקציות עזר ========== #
async def edit_progress(chat_id, msg_id, text):
    try:
        await app.edit_message_text(chat_id, msg_id, text)
    except:
        pass

def get_formats(info, media_type):
    if media_type == 'audio':
        return sorted(
            [f for f in info['formats'] if f.get('acodec') != 'none'],
            key=lambda x: x.get('abr', 0),
            reverse=True
        )[:5]
    return sorted(
        [f for f in info['formats'] if f.get('vcodec') != 'none'],
        key=lambda x: x.get('height', 0),
        reverse=True
    )[:5]

def create_progress_bar(percent):
    filled = '●' * int(percent // 10)
    empty = '◌' * (10 - len(filled))
    return f"[{filled}{empty}]"

# ========== מטפל בהודעות ========== #
@app.on_message(filters.command(["start", "help"]))
async def start(_, message):
    start_msg = """
    🎉 **ברוך הבא לבוט היוטיוב!** 🚀
    📤 שלח לי קישור יוטיוב ואני:
    1. אוריד את המדיה באיכות הגבוהה ביותר
    2. אמיר לך אותה ישירות לטלגרם!
    """
    await message.reply(start_msg)

@app.on_message(filters.text & filters.private)
async def handle_message(_, message):
    user_id = message.from_user.id
    url_match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+)', message.text)
    
    if not url_match:
        return await message.reply("❌ **קישור לא תקין** - שלח קישור יוטיוב תקני")
    
    url = url_match.group()
    user_data[user_id]['url'] = url
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 אודיו MP3", callback_data="audio"),
         InlineKeyboardButton("🎥 וידאו MP4", callback_data="video")]
    ])
    
    msg = await message.reply("📥 **בחר פורמט הורדה:**", reply_markup=keyboard)
    user_data[user_id]['msg_id'] = msg.id

# ========== מטפל בבחירות ========== #
@app.on_callback_query()
async def handle_callback(_, query):
    user_id = query.from_user.id
    data = query.data
    
    if data in ['audio', 'video']:
        url = user_data[user_id].get('url')
        if not url:
            return await query.answer("❌ שגיאה - נסה שוב מההתחלה")
        
        try:
            with YoutubeDL({'cookiefile': COOKIES_FILE, 'quiet': True}) as ydl:
                info = ydl.extract_info(url, download=False)
            
            formats = get_formats(info, data)
            buttons = []
            for fmt in formats:
                quality = f"{fmt['abr']}kbps" if data == 'audio' else f"{fmt['height']}p"
                buttons.append([InlineKeyboardButton(
                    f"🎚 {quality}", 
                    callback_data=f"quality_{fmt['format_id']}_{data}"
                )])
            
            buttons.append([InlineKeyboardButton("🚫 ביטול", callback_data="cancel")])
            await query.message.edit(
                "📊 **בחר איכות:**",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        
        except Exception as e:
            await query.message.edit(f"❌ **שגיאה**: {str(e)}")
    
    elif data.startswith('quality_'):
        _, format_id, media_type = data.split('_')
        url = user_data[user_id]['url']
        msg = await query.message.edit("⏳ **מתחיל בהורדה...**")
        
        try:
            opts = {
                'format': format_id,
                'cookiefile': COOKIES_FILE,
                'outtmpl': '%(title)s.%(ext)s',
                'progress_hooks': [lambda d: download_progress(d, msg.chat.id, msg.id)],
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                }] if media_type == 'audio' else []
            }
            
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)
            
            # המרה לאודיו
            if media_type == 'audio':
                new_path = f"{info['title']}.mp3"
                subprocess.run(['ffmpeg', '-i', file_path, '-b:a', '320k', new_path], check=True)
                file_path = new_path
            
            # העלאה
            await msg.edit("📤 **מעלה לטלגרם...**")
            if media_type == 'audio':
                await app.send_audio(
                    msg.chat.id,
                    file_path,
                    title=info['title'],
                    performer=info.get('uploader', 'Unknown Artist')
                )
            else:
                await app.send_video(
                    msg.chat.id,
                    file_path,
                    caption=f"🎬 **{info['title']}**\n⬆️ הועלה ע\"י @{(await app.get_me()).username}"
                )
            
            await msg.delete()
        
        except Exception as e:
            await msg.edit(f"❌ **שגיאה קריטית**: {str(e)}")
        finally:
            # ניקוי קבצים
            for f in [file_path, f"{info['title']}.mp3"]:
                try: os.remove(f)
                except: pass
    
    elif data == 'cancel':
        await query.message.edit("❌ **הפעולה בוטלה!**")

def download_progress(d, chat_id, msg_id):
    if d['status'] == 'downloading':
        percent = float(d['_percent_str'].strip('%'))
        speed = d['_speed_str']
        eta = d['_eta_str']
        
        progress_text = (
            f"⬇️ **מוריד מהיוטיוב**\n\n"
            f"{create_progress_bar(percent)} {percent:.1f}%\n"
            f"**מהירות**: `{speed}`\n"
            f"**זמן משוער**: `{eta}`"
        )
        
        asyncio.run(edit_progress(chat_id, msg_id, progress_text))

# ========== הפעלת הבוט ========== #
if __name__ == "__main__":
    print("🤖 הבוט פועל בהצלחה!")
    app.run()
