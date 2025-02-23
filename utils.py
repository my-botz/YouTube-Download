# utils.py
import subprocess
import time
from pathlib import Path

def humanbytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size)
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

async def progress_bar(current: int, total: int, start_time: float) -> str:
    elapsed = time.time() - start_time
    speed = current / elapsed if elapsed > 0 else 0
    percent = current * 100 / total
    filled = int(20 * percent // 100)
    bar = '●' * filled + '◌' * (20 - filled)
    
    speed_str = f"{humanbytes(speed)}/s" if speed > 0 else "0 B/s"
    eta = (total - current) / speed if speed > 0 else 0
    
    return (
        f"[{bar}] {percent:.2f}%\n"
        f"**מהירות:** {speed_str}\n"
        f"**זמן משוער:** {eta:.1f}s"
    )

def generate_thumbnail(video_path: str, user_id: int) -> str:
    output_path = f"thumbnails/{user_id}.jpg"
    subprocess.run([
        "ffmpeg",
        "-i", video_path,
        "-ss", "00:00:01",
        "-vframes", "1",
        "-vf", "scale=320:-1",
        output_path
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path

def parse_duration(duration_str: str) -> int:
    # תומך בפורמטים כמו 20d, 5h, 30m
    unit = duration_str[-1]
    try:
        value = int(duration_str[:-1])
    except ValueError:
        return 0
    if unit == "d":
        return value * 86400
    elif unit == "h":
        return value * 3600
    elif unit == "m":
        return value * 60
    else:
        return 0

def get_storage_usage(path: str) -> int:
    total = 0
    for p in Path(path).rglob('*'):
        if p.is_file():
            total += p.stat().st_size
    return total
