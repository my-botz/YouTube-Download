import os
import re
from pathlib import Path
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from pyrogram.errors import MessageNotModified
from yt_dlp import YoutubeDL

load_dotenv()

app = Client(
    "yt_dl_bot",
    api_id=os.getenv("API_ID"),
    api_hash=os.getenv("API_HASH"),
    bot_token=os.getenv("BOT_TOKEN")
)

STORAGE_CHANNEL = os.getenv("STORAGE_CHANNEL")
COOKIES_FILE = 'cookies.txt'
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
video_info_cache = {}

def get_video_id(url: str) -> str:
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'^([0-9A-Za-z_-]{11})$'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

async def send_progress(current, total, message: Message, start_time):
    percent = current * 100 / total
    await message.edit_text(f"📤 מעלה קובץ... {round(percent, 1)}%")

@app.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    start_text = """
ברוכים הבאים לבוט ההורדות מיוטיוב! 🎉

**שימוש:**
1. שלח לי קישור יוטיוב
2. בחר פורמט (אודיו/וידאו)
3. קבל את הקובץ ישירות לצ'אט!

מגבלה: עד 2GB לקובץ
    """
    await message.reply_text(start_text)

@app.on_message(filters.regex(r"youtu\.?be"))
async def handle_youtube_link(client: Client, message: Message):
    url = message.text
    video_id = get_video_id(url)
    
    if not video_id:
        return await message.reply_text("❌ קישור לא תקין")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 אודיו MP3", callback_data=f"audio_{video_id}"),
         InlineKeyboardButton("🎬 וידאו MP4", callback_data=f"video_{video_id}")]
    ])
    
    await message.reply_text("בחר פורמט להורדה:", reply_markup=keyboard)

async def download_media(video_id: str, media_type: str, quality: str = 'best'):
    ydl_opts = {
        'format': 'bestaudio/best' if media_type == 'audio' else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': f'downloads/%(id)s.%(ext)s',
        'cookiefile': COOKIES_FILE,
        'noplaylist': True,
        'quiet': True,
    }
    
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_id, download=False)
        file_path = ydl.prepare_filename(info)
        
        if media_type == 'audio':
            ydl_opts.update({
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            })
        
        ydl.download([video_id])
    
    return Path(file_path)

@app.on_callback_query(filters.regex(r"^(audio|video)_"))
async def handle_callback(client: Client, query: CallbackQuery):
    data = query.data
    media_type, video_id = data.split('_', 1)
    
    progress_msg = await query.message.reply_text("⏳ מתחיל בהורדה...")
    
    try:
        file_path = await download_media(video_id, media_type)
        if not file_path.exists():
            return await progress_msg.edit_text("❌ שגיאה בהורדה")
        
        if file_path.stat().st_size > MAX_FILE_SIZE:
            file_path.unlink()
            return await progress_msg.edit_text("❌ הקובץ גדול מ-2GB")
        
        await progress_msg.edit_text("📤 מעלה לשרת...")
        
        if media_type == 'audio':
            await client.send_audio(
                chat_id=query.message.chat.id,
                audio=str(file_path),
                progress=send_progress,
                progress_args=(progress_msg,)
            )
        else:
            await client.send_video(
                chat_id=query.message.chat.id,
                video=str(file_path),
                progress=send_progress,
                progress_args=(progress_msg,)
            )
        
        await progress_msg.delete()
        file_path.unlink()
        
    except Exception as e:
        await progress_msg.edit_text(f"❌ שגיאה: {str(e)}")
        if file_path.exists():
            file_path.unlink()

if __name__ == "__main__":
    Path("downloads").mkdir(exist_ok=True)
    app.run()
