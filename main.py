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

# הגדרות סביבה
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
SESSION_STRING = os.environ.get("SESSION_STRING")

# אתחול הקליינט
app = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# מאגר נתונים זמני
user_data = defaultdict(dict)
progress_data = defaultdict(dict)

# פונקציות עזר
async def edit_progress_message(chat_id, message_id):
    while True:
        await asyncio.sleep(5)
        data = progress_data.get((chat_id, message_id))
        if not data or data.get('completed'):
            break
        
        progress = data.get('progress', 0)
        speed = data.get('speed', 'N/A')
        status = data.get('status', 'Processing...')
        
        bar = "[" + "█" * int(progress / 10) + " " * (10 - int(progress / 10)) + "]"
        text = (
            f"**{status}**\n\n"
            f"{bar} {progress}%\n"
            f"**Speed:** {speed}/s\n"
            f"**ETA:** {data.get('eta', 'N/A')}"
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

# מטפל בהודעות
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

# מטפל בבחירת פורמט
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
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        
        formats = []
        if data == 'audio':
            audio_formats = [f for f in info['formats'] if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
            formats = sorted(
                {f['format_note']: f for f in audio_formats}.values(),
                key=lambda x: x.get('abr', 0),
                reverse=True
            )
        else:
            video_formats = [f for f in info['formats'] if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
            formats = sorted(
                {f['format_note']: f for f in video_formats}.values(),
                key=lambda x: x.get('height', 0),
                reverse=True
            )
        
        buttons = []
        for fmt in formats[:5]:
            quality = fmt['format_note'] or f"{fmt.get('height', '?')}p"
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
            'status': 'Downloading...',
            'speed': '0B',
            'eta': 'N/A',
            'completed': False
        }
        
        # התחל עדכון התקדמות
        Thread(target=lambda: asyncio.run(edit_progress_message(chat_id, message_id)).start()
        
        # הורדה ועיבוד
        try:
            video_id = re.search(r'v=([\w-]+)', url).group(1)
            thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
            thumbnail_path = download_thumbnail(thumbnail_url, video_id)
            
            ydl_opts = {
                'format': format_id,
                'outtmpl': f'{video_id}.%(ext)s',
                'progress_hooks': [lambda d: update_progress(d, chat_id, message_id)],
                'postprocessors': []
            }
            
            if media_type == 'audio':
                ydl_opts['postprocessors'].append({
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                })
            
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)
                
                if media_type == 'audio':
                    new_path = f"{video_id}.mp3"
                    convert_to_mp3(file_path, new_path, thumbnail_path)
                    file_path = new_path
            
            # עדכן הודעה לפני העלאה
            progress_data[(chat_id, message_id)]['status'] = 'Uploading...'
            
            # העלה לטלגרם
            if media_type == 'audio':
                await app.send_audio(
                    chat_id,
                    file_path,
                    thumb=thumbnail_path,
                    progress=upload_progress,
                    progress_args=(chat_id, message_id)
                )
            else:
                await app.send_video(
                    chat_id,
                    file_path,
                    thumb=thumbnail_path,
                    progress=upload_progress,
                    progress_args=(chat_id, message_id)
                )
            
            # סיום
            progress_data[(chat_id, message_id)]['completed'] = True
            await query.message.delete()
            
        except Exception as e:
            await query.message.edit(f"שגיאה: {str(e)}")
        
        # נקה קבצים
        for f in [file_path, thumbnail_path]:
            try: os.remove(f)
            except: pass

def update_progress(d, chat_id, message_id):
    if d['status'] == 'downloading':
        progress = d.get('_percent_str', '0%').strip('%')
        progress_data[(chat_id, message_id)].update({
            'progress': float(progress),
            'speed': d.get('_speed_str', 'N/A'),
            'eta': d.get('_eta_str', 'N/A')
        })

def upload_progress(current, total, chat_id, message_id):
    progress = (current / total) * 100
    speed = current / (time.time() - progress_data[(chat_id, message_id)].get('start_time', time.time()))
    progress_data[(chat_id, message_id)].update({
        'progress': round(progress, 1),
        'speed': f"{speed / 1024 / 1024:.2f}MB",
        'status': 'Uploading...'
    })

if __name__ == "__main__":
    print("הבוט מתחיל...")
    app.run()
