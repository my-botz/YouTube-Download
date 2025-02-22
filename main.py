import os
import re
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

# ================= CONFIG ================= #
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"])
COOKIES_FILE = "cookies.txt"
PORT = int(os.environ.get("PORT", 8080))

# ================= SETUP ================= #
app = Client("yt_bot", API_ID, API_HASH, bot_token=BOT_TOKEN)
user_data = defaultdict(dict)

# ================= UTILS ================= #
def escape_markdown(text):
    return re.sub(r"([_*\[\]()~`>\#\+\-=\|{}\.!])", r"\\\1", text) if text else ""

async def download_thumbnail(url, filename):
    try:
        response = await asyncio.to_thread(requests.get, url, timeout=10)
        if response.status_code == 200:
            with open(filename, 'wb') as f:
                f.write(response.content)
            return filename
    except Exception as e:
        print(f"Thumbnail error: {e}")
    return None

async def cleanup_files(*files):
    for file in files:
        if file and os.path.exists(file):
            try: os.remove(file)
            except: pass

# ================= PROGRESS HANDLERS ================= #
async def progress_updater(chat_id, message_id, total, downloaded, start_time):
    while True:
        await asyncio.sleep(5)
        try:
            elapsed = time.time() - start_time
            speed = (downloaded / elapsed) if elapsed > 0 else 0
            percent = (downloaded / total) * 100 if total > 0 else 0
            
            progress_bar = "[" + "●" * int(percent // 10) + "◌" * (10 - int(percent // 10)) + "]"
            text = (
                f"**{'⬇️ מוריד' if total > downloaded else '⬆️ מעלה'}**\n\n"
                f"{progress_bar} **{percent:.1f}%**\n"
                f"**מהירות:** {speed/1024/1024:.2f}MB/s\n"
                f"**זמן משוער:** {timedelta(seconds=int((total - downloaded)/speed)) if speed > 0 else 'N/A'}"
            )
            await app.edit_message_text(chat_id, message_id, text)
        except:
            break

# ================= HANDLERS ================= #
@app.on_message(filters.command(["start", "help"]))
async def start(_, msg):
    start_text = """
    🎉 **ברוכים הבאים לבוט היוטיוב!** 🚀
    📤 שלחו לי קישור יוטיוב ואני:
    1. אוריד את המדיה באיכות הגבוהה ביותר
    2. אמיר לכם אותה ישירות לטלגרם!
    """
    await msg.reply(start_text)

@app.on_message(filters.text & filters.private)
async def handle_url(_, msg):
    url_match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+)', msg.text)
    if not url_match:
        return await msg.reply("❌ **קישור לא תקין** - שלח קישור יוטיוב תקני")
    
    user_id = msg.from_user.id
    user_data[user_id] = {'url': url_match.group()}
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 אודיו MP3", callback_data="audio"),
         InlineKeyboardButton("🎥 וידאו MP4", callback_data="video")]
    ])
    
    await msg.reply("📥 **בחר פורמט הורדה:**", reply_markup=keyboard)

@app.on_callback_query()
async def handle_query(_, query):
    user_id = query.from_user.id
    data = query.data
    
    if data == 'cancel':
        await query.message.edit("❌ **הפעולה בוטלה!**")
        return
    
    if data in ['audio', 'video']:
        url = user_data.get(user_id, {}).get('url')
        if not url:
            return await query.answer("❌ שגיאה - נסה שוב מההתחלה")
        
        try:
            ydl_opts = {'quiet': True, 'cookiefile': COOKIES_FILE}
            info = await asyncio.to_thread(YoutubeDL(ydl_opts).extract_info, url, download=False)
            
            formats = sorted(
                info['formats'],
                key=lambda x: x.get('abr' if data == 'audio' else 'height', 0),
                reverse=True
            )[:5]
            
            buttons = []
            for fmt in formats:
                quality = f"{fmt['abr']}kbps" if data == 'audio' else f"{fmt['height']}p"
                buttons.append([InlineKeyboardButton(
                    f"🎚 {quality}", 
                    callback_data=f"dl_{data}_{fmt['format_id']}"
                )])
            
            buttons.append([InlineKeyboardButton("🚫 ביטול", callback_data="cancel")])
            safe_title = escape_markdown(info.get('title', 'ללא כותרת'))
            await query.message.edit(
                f"**{safe_title}**\n\n📊 **בחר איכות:**",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        
        except Exception as e:
            await query.message.edit(f"❌ **שגיאה**: {escape_markdown(str(e))}")

    elif data.startswith('dl_'):
        media_type, format_id = data.split('_')[1:]
        url = user_data[user_id]['url']
        msg = await query.message.edit("⏳ **מתחיל בעיבוד...**")
        
        try:
            # התחלת מעקב התקדמות
            progress_task = None
            start_time = time.time()
            
            # הגדרות הורדה
            ydl_opts = {
                'format': f'{format_id}+bestaudio' if media_type == 'video' else format_id,
                'cookiefile': COOKIES_FILE,
                'outtmpl': '%(title)s.%(ext)s',
                'postprocessors': [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}
                ] if media_type == 'audio' else [],
                'noplaylist': True,
                'writethumbnail': True
            }
            
            # הורדה
            with YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=True)
                file_path = ydl.prepare_filename(info)
                
                # מעקב התקדמות
                total_size = info.get('filesize', 0)
                progress_task = asyncio.create_task(
                    progress_updater(msg.chat.id, msg.id, total_size, 0, start_time)
                )
                
                # המרה לאודיו
                if media_type == 'audio':
                    mp3_path = f"{os.path.splitext(file_path)[0]}.mp3"
                    await asyncio.to_thread(
                        subprocess.run,
                        ['ffmpeg', '-i', file_path, '-b:a', '320k', mp3_path],
                        check=True
                    )
                    file_path = mp3_path
            
            # הכנת קבצים
            thumb_path = f"{os.path.splitext(file_path)[0]}.jpg"
            if not os.path.exists(thumb_path) and info.get('thumbnail'):
                await download_thumbnail(info['thumbnail'], thumb_path)
            
            # עדכון הודעה
            await msg.edit("📤 **מעלה לטלגרם...**")
            
            # שליחת המדיה
            caption = f"🎬 **{escape_markdown(info['title'])}**\n⬆️ הועלה ע\"י @{(await app.get_me()).username}"
            if media_type == 'audio':
                await app.send_audio(
                    msg.chat.id,
                    file_path,
                    caption=caption,
                    thumb=thumb_path,
                    duration=info.get('duration'),
                    performer=info.get('uploader', 'Unknown')
                )
            else:
                await app.send_video(
                    msg.chat.id,
                    file_path,
                    caption=caption,
                    thumb=thumb_path,
                    duration=info.get('duration'),
                    width=info.get('width'),
                    height=info.get('height'),
                    supports_streaming=True
                )
            
            await msg.delete()
        
        except Exception as e:
            await msg.edit(f"❌ **שגיאה**: {escape_markdown(str(e))}")
        finally:
            if progress_task:
                progress_task.cancel()
            await cleanup_files(file_path, thumb_path)

# ================= HEALTH CHECK ================= #
from flask import Flask
server = Flask(__name__)

@server.route('/')
def health_check():
    return "Bot is running", 200

if __name__ == "__main__":
    import threading
    threading.Thread(target=server.run, kwargs={'host': '0.0.0.0', 'port': PORT}).start()
    print("🤖 הבוט פועל בהצלחה!")
    app.run()
