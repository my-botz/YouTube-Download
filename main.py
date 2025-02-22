import os
import re
import time
import asyncio
from threading import Thread
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
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# מאגר נתונים זמני
user_data = defaultdict(dict)
progress_data = defaultdict(dict)

async def edit_progress_message(chat_id, message_id):
    while True:
        await asyncio.sleep(5)
        data = progress_data.get((chat_id, message_id))
        if not data or data.get('completed'):
            break
        
        progress = data.get('progress', 0)
        speed = data.get('speed', 'N/A')
        eta = data.get('eta', 'N/A')
        status = data.get('status', 'Processing...')
        
        # יצירת סרגל התקדמות מעוצב
        filled_blocks = int(progress // 10)
        empty_blocks = 10 - filled_blocks
        progress_bar = '●' * filled_blocks + '◌' * empty_blocks
        
        text = (
            f"**{status}**\n\n"
            f"`[{progress_bar}]` **{progress:.1f}%**\n"
            f"**מהירות:** `{speed}`\n"
            f"**זמן משוער:** `{eta}`"
        )
        
        try:
            await app.edit_message_text(
                chat_id,
                message_id,
                text,
                reply_markup=data.get('reply_markup')
            )
        except:
            pass

def format_speed(speed):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if speed < 1024:
            return f"{speed:.2f} {unit}/s"
        speed /= 1024
    return f"{speed:.2f} GB/s"

def download_thumbnail(url, video_id):
    path = f"{video_id}.jpg"
    with open(path, 'wb') as f:
        f.write(requests.get(url).content)
    return path

def convert_to_mp3(input_file, output_file, thumbnail):
    subprocess.run([
        'ffmpeg',
        '-i', input_file,
        '-i', thumbnail,
        '-map', '0:0',
        '-map', '1:0',
        '-id3v2_version', '3',
        '-metadata:s:v', 'title="Album cover"',
        '-metadata:s:v', 'comment="Cover (front)"',
        '-codec:a', 'copy',
        '-codec:v', 'copy',
        output_file
    ], capture_output=True)

def get_ydl_opts(format_id, media_type):
    opts = {
        'format': format_id,
        'progress_hooks': [],
        'postprocessors': [],
        'outtmpl': '%(id)s.%(ext)s',
    }
    
    if os.path.exists('cookies.txt'):
        opts['cookiefile'] = 'cookies.txt'
    
    if media_type == 'audio':
        opts['postprocessors'].append({
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        })
    
    return opts

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    user = message.from_user
    start_text = (
        f"היי {user.first_name} 👋\n"
        "אני בוט להורדת מדיה מיוטיוב!\n\n"
        "**איך להשתמש:**\n"
        "1. שלח לי קישור ליוטיוב\n"
        "2. בחר פורמט (אודיו/וידאו)\n"
        "3. בחר איכות\n"
        "4. המתן עד לסיום העיבוד\n\n"
        "הבוט תומך בכל האיכויות כולל 4K!"
    )
    await message.reply(start_text)

@app.on_message(filters.text & filters.private)
async def handle_message(client: Client, message: Message):
    url = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+)', message.text)
    if not url:
        return
    
    url = url.group(1)
    user_id = message.from_user.id
    user_data[user_id]['url'] = url
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 מוזיקה (MP3)", callback_data="audio"),
        InlineKeyboardButton("🎥 וידאו (MP4)", callback_data="video")]
    ])
    
    msg = await message.reply(
        "**בחר פורמט להורדה:**",
        reply_markup=keyboard
    )
    user_data[user_id]['format_message'] = msg.id

@app.on_callback_query()
async def handle_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data
    
    if data in ['audio', 'video']:
        url = user_data[user_id].get('url')
        if not url:
            await query.answer("שגיאה, נסה שוב!")
            return
        
        ydl_opts = {'quiet': True, 'extract_flat': True}
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        
        formats = []
        if data == 'audio':
            audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none']
            formats = sorted(
                audio_formats,
                key=lambda x: x.get('abr', 0) or x.get('tbr', 0),
                reverse=True
            )
        else:
            video_formats = [f for f in info['formats'] if f.get('vcodec') != 'none']
            formats = sorted(
                video_formats,
                key=lambda x: x.get('height', 0) or x.get('width', 0),
                reverse=True
            )
        
        # סינון פורמטים כפולים
        unique_formats = {}
        for fmt in formats:
            key = fmt.get('format_note') or fmt.get('height') or fmt.get('abr')
            if key and key not in unique_formats:
                unique_formats[key] = fmt
        
        buttons = []
        for fmt in list(unique_formats.values())[:8]:
            quality = fmt.get('format_note') or f"{fmt.get('height', '?')}p" or f"{fmt.get('abr', 0)}kbps"
            buttons.append([InlineKeyboardButton(
                f"🎚 {quality}",
                callback_data=f"quality_{fmt['format_id']}_{data}"
            )])
        
        await query.message.edit(
            "**בחר איכות:**",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    
    elif data.startswith('quality_'):
        _, format_id, media_type = data.split('_')
        url = user_data[user_id].get('url')
        
        progress_msg = await query.message.edit("**מתחיל בהורדה...**")
        chat_id = progress_msg.chat.id
        message_id = progress_msg.id
        
        progress_data[(chat_id, message_id)] = {
            'progress': 0,
            'status': 'מוריד...',
            'speed': '0B/s',
            'eta': '00:00',
            'completed': False,
            'start_time': time.time()
        }
        
        Thread(target=lambda: asyncio.run(edit_progress_message(chat_id, message_id))).start()
        
        try:
            video_id = re.search(r'v=([\w-]+)', url).group(1)
            thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
            thumbnail_path = download_thumbnail(thumbnail_url, video_id)
            
            ydl_opts = get_ydl_opts(format_id, media_type)
            ydl_opts['progress_hooks'] = [lambda d: update_progress(d, chat_id, message_id)]
            
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)
                
                if media_type == 'audio':
                    new_path = f"{video_id}.mp3"
                    convert_to_mp3(file_path, new_path, thumbnail_path)
                    file_path = new_path
            
            progress_data[(chat_id, message_id)]['status'] = 'מעלה...'
            
            # יצירת כיתוב עם פרטים
            duration = str(timedelta(seconds=info.get('duration', 0)))
            caption = (
                f"🎵 **{info['title']}**\n"
                f"⏳ **משך:** `{duration}`\n"
                f"📤 **הועלה ע\"י:** @{(await app.get_me()).username}"
            )
            
            if media_type == 'audio':
                await app.send_audio(
                    chat_id,
                    file_path,
                    thumb=thumbnail_path,
                    caption=caption,
                    duration=info.get('duration'),
                    performer=info.get('uploader'),
                    title=info.get('title'),
                    progress=lambda c, t: upload_progress(c, t, chat_id, message_id)
                )
            else:
                await app.send_video(
                    chat_id,
                    file_path,
                    thumb=thumbnail_path,
                    caption=caption,
                    duration=info.get('duration'),
                    width=info.get('width'),
                    height=info.get('height'),
                    progress=lambda c, t: upload_progress(c, t, chat_id, message_id)
                )
            
            progress_data[(chat_id, message_id)]['completed'] = True
            await query.message.delete()
            
        except Exception as e:
            await query.message.edit(f"שגיאה: {str(e)}")
        
        for f in [file_path, thumbnail_path]:
            try: os.remove(f)
            except: pass

def update_progress(d, chat_id, message_id):
    if d['status'] == 'downloading':
        progress = float(d.get('_percent_str', '0%').strip('%'))
        elapsed = time.time() - progress_data[(chat_id, message_id)]['start_time']
        speed = d.get('_speed_str', '0B').split(' ')[0]
        eta = d.get('_eta_str', '0')
        
        progress_data[(chat_id, message_id)].update({
            'progress': progress,
            'speed': speed + "/s",
            'eta': eta if eta.isdigit() else '00:00'
        })

def upload_progress(current, total, chat_id, message_id):
    progress = (current / total) * 100
    elapsed = time.time() - progress_data[(chat_id, message_id)]['start_time']
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    
    progress_data[(chat_id, message_id)].update({
        'progress': progress,
        'speed': format_speed(speed),
        'eta': str(timedelta(seconds=int(eta))).split('.')[0]
    })

if __name__ == "__main__":
    print("הבוט מתחיל...")
    app.run()
