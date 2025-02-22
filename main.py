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
import mimetypes

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
        await asyncio.sleep(3)
        data = progress_data.get((chat_id, message_id))
        if not data or data.get('completed'):
            break
        
        progress = data.get('progress', 0)
        speed = data.get('speed', 'N/A')
        eta = data.get('eta', 'N/A')
        status = data.get('status', 'Processing...')
        phase = data.get('phase', 'download')
        
        # יצירת סרגל התקדמות עם סמלים ייחודיים
        filled = int(progress // 10)
        progress_bar = '●' * filled + '◌' * (10 - filled)
        
        text = (
            f"**{status}**\n\n"
            f"`[{progress_bar}]` **{progress:.1f}%**\n"
            f"**מהירות:** `{speed}`\n"
            f"**זמן משוער:** `{eta}`\n"
            f"**שלב:** `{phase.capitalize()}`"
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
    units = ['B/s', 'KB/s', 'MB/s', 'GB/s']
    unit_index = 0
    while speed >= 1024 and unit_index < 3:
        speed /= 1024
        unit_index += 1
    return f"{speed:.2f} {units[unit_index]}"

def download_thumbnail(url, video_id):
    try:
        path = f"{video_id}.jpg"
        with open(path, 'wb') as f:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            f.write(response.content)
        return path
    except:
        return download_thumbnail(f"https://img.youtube.com/vi/{video_id}/default.jpg", video_id)

def convert_to_mp3(input_file, output_file, thumbnail, metadata):
    subprocess.run([
        'ffmpeg',
        '-i', input_file,
        '-i', thumbnail,
        '-map', '0:0',
        '-map', '1:0',
        '-id3v2_version', '3',
        '-metadata', f"title={metadata['title']}",
        '-metadata', f"artist={metadata['artist']}",
        '-metadata', 'comment=Uploaded by YouTube Bot',
        '-codec:a', 'libmp3lame',
        '-q:a', '0',
        output_file
    ], capture_output=True)

def get_ydl_opts(format_id, media_type):
    opts = {
        'format': format_id,
        'progress_hooks': [],
        'postprocessors': [],
        'outtmpl': '%(id)s.%(ext)s',
        'noplaylist': True,
        'verbose': False
    }
    
    if os.path.exists('cookies.txt'):
        opts['cookiefile'] = 'cookies.txt'
        opts['mark_watched'] = False
    
    if media_type == 'audio':
        opts['postprocessors'].append({
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        })
    
    return opts

@app.on_message(filters.command(["start", "help"]))
async def start_command(client: Client, message: Message):
    user = message.from_user
    start_text = (
        f"👋 שלום {user.mention}!\n"
        "אני בוט להורדת מדיה מיוטיוב עם תמיכה בכל האיכויות!\n\n"
        "📚 **איך להשתמש:**\n"
        "1. שלח לי קישור יוטיוב\n"
        "2. אבחר את הפורמט הרצוי\n"
        "3. בחר איכות\n"
        "4. המתן לסיום העיבוד\n\n"
        "🎧 עבור אודיו - אספק קובץ MP3 באיכות גבוהה\n"
        "🎥 עבור וידאו - אספק קובץ MP4 עד 4K\n\n"
        "🕒 זמן עיבוד ממוצע: 1-5 דקות תלוי בגודל"
    )
    await message.reply(start_text)

@app.on_message(filters.text & filters.private)
async def handle_message(client: Client, message: Message):
    url_match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+)', message.text)
    if not url_match:
        return
    
    checking_msg = await message.reply("🔍 בודק את הקישור...")
    
    try:
        url = url_match.group(1)
        user_id = message.from_user.id
        user_data[user_id]['url'] = url
        
        ydl_opts = {'quiet': True, 'extract_flat': True}
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
        
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        
        await checking_msg.delete()
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎵 מוזיקה (MP3)", callback_data="audio"),
            InlineKeyboardButton("🎥 וידאו (MP4)", callback_data="video")]
        ])
        
        msg = await message.reply(
            "📂 **בחר פורמט להורדה:**",
            reply_markup=keyboard
        )
        user_data[user_id]['format_message'] = msg.id
    
    except Exception as e:
        await checking_msg.edit(f"❌ שגיאה: {str(e)}")

@app.on_callback_query()
async def handle_callback(client: Client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data
    
    if data in ['audio', 'video']:
        url = user_data[user_id].get('url')
        if not url:
            await query.answer("❌ שגיאה, נסה שוב!")
            return
        
        checking_msg = await query.message.edit("🔍 מאתר איכויות זמינות...")
        
        try:
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
                    key=lambda x: (x.get('height', 0), x.get('tbr', 0)),
                    reverse=True
                )
            
            unique_formats = {}
            for fmt in formats:
                quality = None
                if data == 'audio':
                    quality = f"{fmt.get('abr', 0)}kbps"
                else:
                    quality = f"{fmt.get('height', '?')}p"
                
                if quality and quality not in unique_formats:
                    unique_formats[quality] = fmt
            
            buttons = []
            for quality, fmt in list(unique_formats.items())[:8]:
                buttons.append([InlineKeyboardButton(
                    f"🎚 {quality}",
                    callback_data=f"quality_{fmt['format_id']}_{data}"
                )])
            
            await checking_msg.edit(
                "📊 **בחר איכות:**",
                reply_markup=InlineKeyboardMarkup(buttons)
            
        except Exception as e:
            await checking_msg.edit(f"❌ שגיאה: {str(e)}")
    
    elif data.startswith('quality_'):
        _, format_id, media_type = data.split('_')
        url = user_data[user_id].get('url')
        
        progress_msg = await query.message.edit("⏳ מתחיל בעיבוד...")
        chat_id = progress_msg.chat.id
        message_id = progress_msg.id
        
        progress_data[(chat_id, message_id)] = {
            'progress': 0,
            'status': 'מתחיל הורדה',
            'speed': '0B/s',
            'eta': '00:00',
            'phase': 'download',
            'completed': False,
            'start_time': time.time()
        }
        
        Thread(target=lambda: asyncio.run(edit_progress_message(chat_id, message_id))).start()
        
        try:
            video_id = re.search(r'v=([\w-]+)', url).group(1)
            thumbnail_path = download_thumbnail(f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg", video_id)
            
            ydl_opts = get_ydl_opts(format_id, media_type)
            ydl_opts['progress_hooks'] = [lambda d: update_progress(d, chat_id, message_id)]
            
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)
                
                if media_type == 'audio':
                    new_path = f"{video_id}.mp3"
                    metadata = {
                        'title': info.get('title', 'Unknown'),
                        'artist': info.get('uploader', 'Unknown')
                    }
                    convert_to_mp3(file_path, new_path, thumbnail_path, metadata)
                    file_path = new_path
            
            progress_data[(chat_id, message_id)].update({
                'status': 'מתחיל העלאה',
                'phase': 'upload',
                'progress': 0
            })
            
            duration = str(timedelta(seconds=info.get('duration', 0)))
            caption = (
                f"🎬 **{info['title']}**\n"
                f"⏱ **משך:** `{duration}`\n"
                f"📤 **הועלה ע\"י:** @{(await app.get_me()).username}"
            )
            
            mime_type = mimetypes.guess_type(file_path)[0]
            if media_type == 'audio':
                await app.send_audio(
                    chat_id,
                    file_path,
                    thumb=thumbnail_path,
                    caption=caption,
                    duration=info.get('duration'),
                    performer=info.get('uploader'),
                    title=info.get('title'),
                    mime_type=mime_type,
                    progress=lambda c, t: upload_progress(c, t, chat_id, message_id)
            else:
                await app.send_video(
                    chat_id,
                    file_path,
                    thumb=thumbnail_path,
                    caption=caption,
                    duration=info.get('duration'),
                    width=info.get('width'),
                    height=info.get('height'),
                    mime_type=mime_type,
                    progress=lambda c, t: upload_progress(c, t, chat_id, message_id)
            
            progress_data[(chat_id, message_id)]['completed'] = True
            await query.message.delete()
            
        except Exception as e:
            await query.message.edit(f"❌ שגיאה קריטית: {str(e)}")
        
        for f in [file_path, thumbnail_path]:
            try: os.remove(f)
            except: pass

def update_progress(d, chat_id, message_id):
    if d['status'] == 'downloading':
        progress = float(d.get('_percent_str', '0%').strip('%'))
        speed = d.get('_speed_str', '0B').split(' ')[0]
        eta = d.get('_eta_str', '0')
        
        progress_data[(chat_id, message_id)].update({
            'progress': progress,
            'speed': format_speed(float(speed)),
            'eta': str(timedelta(seconds=int(eta))) if eta.isdigit() else '00:00',
            'status': 'מוריד מהיוטיוב',
            'phase': 'download'
        })

def upload_progress(current, total, chat_id, message_id):
    progress = (current / total) * 100
    elapsed = time.time() - progress_data[(chat_id, message_id)]['start_time']
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    
    progress_data[(chat_id, message_id)].update({
        'progress': progress,
        'speed': format_speed(speed),
        'eta': str(timedelta(seconds=int(eta))).split('.')[0],
        'status': 'מעלה לטלגרם',
        'phase': 'upload'
    })

if __name__ == "__main__":
    print("הבוט מתחיל...")
    app.run()
