import os
import re
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from yt_dlp import YoutubeDL
import requests
from pathlib import Path

# הגדרות סביבה - חובה!
API_ID = os.getenv("API_ID") 
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
STORAGE_CHANNEL = int(os.getenv("STORAGE_CHANNEL"))  # חובה מספרי

app = Client(
    "yt_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

video_info_cache = {}
TEMP_DIR = Path("downloads")
TEMP_DIR.mkdir(exist_ok=True)

# פונקציות עזר
def escape_markdown(text: str) -> str:
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def get_thumbnail(url: str, video_id: str) -> str:
    thumb_path = TEMP_DIR / f"{video_id}.jpg"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            with open(thumb_path, 'wb') as f:
                f.write(response.content)
            return str(thumb_path)
    except Exception as e:
        print(f"Thumbnail error: {e}")
    return None

class DownloadProgress:
    def __init__(self, chat_id: int, msg_id: int):
        self.chat_id = chat_id
        self.msg_id = msg_id
        self.last_percent = 0

    async def update(self, d: dict):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
            downloaded = d.get('downloaded_bytes', 0)
            percent = int((downloaded / total) * 100)
            
            if percent - self.last_percent >= 5:
                try:
                    await app.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.msg_id,
                        text=f"**⏳ מוריד...**\nהתקדמות: `{percent}%`",
                        parse_mode=enums.ParseMode.MARKDOWN
                    )
                    self.last_percent = percent
                except Exception as e:
                    print(f"Progress error: {e}")

# הורדה עם yt-dlp
async def ytdl_download(url: str, opts: dict) -> Path:
    loop = asyncio.get_event_loop()
    with YoutubeDL(opts) as ydl:
        info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
        file_path = Path(ydl.prepare_filename(info))
        
        # המרה לאודיו אם צריך
        if opts.get('postprocessors'):
            for pp in opts['postprocessors']:
                if pp.get('key') == 'FFmpegExtractAudio':
                    file_path = file_path.with_suffix('.mp3')
                    break
        return file_path

@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    start_text = """
*ברוכים הבאים!* 🎬
שלחו לי קישור יוטיוב ואני אוריד עבורכם את הסרטון/אודיו!
    """
    await message.reply(start_text, parse_mode=enums.ParseMode.MARKDOWN)

@app.on_message(filters.regex(r"(youtube\.com|youtu\.be)"))
async def handle_youtube(client: Client, message: Message):
    url = message.text
    video_id = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url).group(1)
    
    # שמירת מידע במטמון
    video_info_cache[message.chat.id] = {'url': url, 'id': video_id}
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 אודיו", callback_data=f"audio_{video_id}"),
         InlineKeyboardButton("🎬 וידאו", callback_data=f"video_{video_id}")]
    ])
    
    await message.reply("**בחר פורמט:**", reply_markup=markup, parse_mode=enums.ParseMode.MARKDOWN)

@app.on_callback_query(filters.regex(r"^(audio|video)_"))
async def handle_format(client: Client, query: CallbackQuery):
    data = query.data.split('_')
    media_type, video_id = data[0], data[1]
    chat_id = query.message.chat.id
    
    # שליחת הודעת התקדמות
    progress_msg = await query.message.reply("**⏳ מתחיל בהורדה...**", parse_mode=enums.ParseMode.MARKDOWN)
    
    try:
        # הגדרות הורדה
        progress_cb = DownloadProgress(chat_id, progress_msg.id)
        ydl_opts = {
            'format': 'bestaudio/best' if media_type == 'audio' else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
            'outtmpl': str(TEMP_DIR / f'%(id)s.%(ext)s'),
            'progress_hooks': [lambda d: asyncio.create_task(progress_cb.update(d))],
            'writethumbnail': True,
            'postprocessors': [
                {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
                {'key': 'FFmpegMetadata'}
            ]
        }
        
        if media_type == 'audio':
            ydl_opts['postprocessors'].append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192'
            })
        
        # הורדה
        file_path = await ytdl_download(video_id, ydl_opts)
        
        # בדיקת גודל קובץ
        if file_path.stat().st_size > 2 * 1024 * 1024 * 1024:
            await progress_msg.edit_text("**❌ הקובץ גדול מ-2GB!**")
            file_path.unlink()
            return
        
        # מציאת תמונה ממוזערת
        thumb_path = TEMP_DIR / f"{video_id}.jpg"
        if not thumb_path.exists():
            thumb_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
            await get_thumbnail(thumb_url, video_id)
        
        # העלאה לטלגרם
        await progress_msg.edit_text("**📤 מעלה...**")
        caption = f"**הורד באמצעות @{app.me.username}**"
        
        if media_type == 'audio':
            msg = await client.send_audio(
                chat_id=chat_id,
                audio=str(file_path),
                caption=caption,
                thumb=str(thumb_path) if thumb_path.exists() else None,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        else:
            msg = await client.send_video(
                chat_id=chat_id,
                video=str(file_path),
                caption=caption,
                thumb=str(thumb_path) if thumb_path.exists() else None,
                parse_mode=enums.ParseMode.MARKDOWN
            )
        
        # ניקוי קבצים
        file_path.unlink()
        if thumb_path.exists():
            thumb_path.unlink()
        
        await progress_msg.delete()
        
    except Exception as e:
        await progress_msg.edit_text(f"**❌ שגיאה:** `{str(e)}`")
        if 'file_path' in locals():
            file_path.unlink(missing_ok=True)

if __name__ == "__main__":
    app.run()
