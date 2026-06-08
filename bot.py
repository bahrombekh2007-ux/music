#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Music Bot - Linux Optimized (Python 3.11+)
Instagram, TikTok, Shazam, YouTube Music Search
Fast & Concurrent Version
"""

import sys
import os
import asyncio
import tempfile
import subprocess
import hashlib
import re
import time
import signal
import logging
from importlib.metadata import version
from pathlib import Path
from typing import Optional, Dict, List, Any
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import aiofiles
import aiofiles.os

# Python 3.11+ imports
from datetime import datetime
import psutil

# Telegram Bot
import telebot
from telebot import types
from telebot.apihelper import ApiException
from telebot.async_telebot import AsyncTeleBot

# Music Recognition
from shazamio import Shazam

# Video/Audio Download
import yt_dlp

# Async HTTP
import aiohttp
import httpx

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# UTF-8 encoding
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ==================== CONFIG ====================
BOT_TOKEN = "8524502393:AAFvZmPd2VtjSPrw6TLQdJ9iojHYMlPsj_E"
TEMP_DIR = Path("/tmp/telegram_music_bot")
TEMP_DIR.mkdir(exist_ok=True, parents=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB Telegram limit
CLEANUP_INTERVAL = 300  # 5 minutes (Linuxda tezroq)
MAX_WORKERS = psutil.cpu_count() * 2  # CPU yadrolar soniga qarab

# ==================== GLOBAL STATE ====================
user_sessions: Dict[int, Dict] = {}
thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
process_pool = ProcessPoolExecutor(max_workers=2)
http_session: Optional[aiohttp.ClientSession] = None

# ==================== ASYNC BOT INITIALIZATION ====================
async def init_bot() -> AsyncTeleBot:
    """Async bot yaratish va sozlash"""
    global http_session
    
    # Oldingi webhook o'chirish
    try:
        temp_bot = telebot.TeleBot(BOT_TOKEN)
        temp_bot.remove_webhook()
        logger.info("✅ Webhook o'chirildi")
    except Exception as e:
        logger.warning(f"⚠️ Webhook o'chirish xatosi: {e}")
    
    # HTTP session yaratish
    http_session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            limit=100,
            ttl_dns_cache=300,
            force_close=False
        ),
        timeout=aiohttp.ClientTimeout(total=60)
    )
    
    # Async bot yaratish
    bot = AsyncTeleBot(
        BOT_TOKEN,
        parse_mode=None,
        skip_pending=True
    )
    
    return bot

# ==================== YT-DLP CONFIGURATION ====================
BASE_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'socket_timeout': 20,
    'retries': 3,
    'fragment_retries': 3,
    'nocheckcertificate': True,
    'geo_bypass': True,
    'prefer_insecure': True,
    'concurrent_fragment_downloads': 10,  # Parallel yuklash
    'buffersize': 1024 * 1024,  # 1MB buffer
}

INSTAGRAM_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'best[filesize<50M]',  # 50MB dan kichik
    'outtmpl': str(TEMP_DIR / 'ig_%(id)s.%(ext)s'),
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
    },
}

TIKTOK_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'best[filesize<50M]',
    'outtmpl': str(TEMP_DIR / 'tt_%(id)s.%(ext)s'),
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
    },
}

AUDIO_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'bestaudio/best',
    'outtmpl': str(TEMP_DIR / 'audio_%(title)s.%(ext)s'),
    'restrictfilenames': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '128',
    }],
    'concurrent_fragment_downloads': 20,
}

SEARCH_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': True,
    'socket_timeout': 15,
    'concurrent_fragment_downloads': 5,
}

# ==================== ASYNC UTILITY FUNCTIONS ====================
async def cleanup_old_files() -> None:
    """Async eski fayllarni o'chirish"""
    try:
        current_time = time.time()
        deleted_count = 0
        
        async for filepath in aiofiles.os.scandir(TEMP_DIR):
            if filepath.is_file():
                stat = await aiofiles.os.stat(filepath.path)
                if current_time - stat.st_mtime > CLEANUP_INTERVAL:
                    await aiofiles.os.remove(filepath.path)
                    deleted_count += 1
        
        if deleted_count > 0:
            logger.info(f"🧹 {deleted_count} ta eski fayl o'chirildi")
            
    except Exception as e:
        logger.error(f"Cleanup xatosi: {e}")

async def safe_delete(filepath: Optional[str | Path]) -> None:
    """Async faylni xavfsiz o'chirish"""
    try:
        if filepath:
            path = Path(filepath)
            if path.exists():
                await aiofiles.os.remove(path)
    except Exception:
        pass

def create_hash(text: str) -> str:
    """Tez hash yaratish"""
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()[:16]

def clean_filename(text: str) -> str:
    """Fayl nomini tozalash"""
    if not text:
        return "audio"
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', text)
    text = re.sub(r'\s+', '_', text)
    return text[:50].strip('_') or "audio"

def format_duration(seconds: Optional[int | float]) -> str:
    """Vaqtni formatlash"""
    try:
        total_seconds = int(float(seconds))
        return f" ({total_seconds//60}:{total_seconds%60:02d})"
    except (TypeError, ValueError):
        return ""

def is_instagram_url(url: str) -> bool:
    """Instagram URL tekshirish"""
    return bool(re.search(r'instagram\.com/(p|reel|reels|tv|stories)/', url.lower()))

def is_tiktok_url(url: str) -> bool:
    """TikTok URL tekshirish"""
    return bool(re.search(r'(tiktok|vm\.tiktok|vt\.tiktok)\.com/', url.lower()))

# ==================== ASYNC SHAZAM RECOGNITION ====================
async def recognize_audio_async(audio_bytes: bytes) -> Dict:
    """Shazam bilan musiqa aniqlash (optimallashtirilgan)"""
    temp_file = None
    
    try:
        # RAM disk ga yozish (Linuxda tezroq)
        temp_path = TEMP_DIR / f"shazam_{hashlib.md5(audio_bytes[:100]).hexdigest()}.mp3"
        
        async with aiofiles.open(temp_path, 'wb') as f:
            await f.write(audio_bytes)
        
        # Shazam aniqlash
        shazam = Shazam()
        result = await shazam.recognize(str(temp_path))
        
        if result and 'track' in result:
            track = result['track']
            return {
                'found': True,
                'title': track.get('title', 'Unknown'),
                'artist': track.get('subtitle', 'Unknown'),
            }
    
    except Exception as e:
        logger.error(f"Shazam xatosi: {e}")
    
    finally:
        await safe_delete(temp_path)
    
    return {'found': False}

# ==================== ASYNC DOWNLOAD FUNCTIONS ====================
async def download_youtube_audio_async(query: str, filename_hint: str = "") -> Optional[Path]:
    """Async YouTube'dan audio yuklash"""
    try:
        clean_name = clean_filename(filename_hint or query)
        output_template = str(TEMP_DIR / f"audio_{clean_name}.%(ext)s")
        
        options = {**AUDIO_OPTIONS, 'outtmpl': output_template}
        
        # Thread poolda yuklash (I/O intensive)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            thread_pool,
            lambda: _sync_download(options, f"ytsearch1:{query}")
        )
        
        # Yuklangan faylni topish
        mp3_files = sorted(
            TEMP_DIR.glob('audio_*.mp3'),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        
        if mp3_files and (time.time() - mp3_files[0].stat().st_mtime) < 30:
            return mp3_files[0]
        
    except Exception as e:
        logger.error(f"Audio yuklash xatosi: {e}")
    
    return None

def _sync_download(options: dict, url: str) -> None:
    """Sync yuklash (thread poolda ishlatish uchun)"""
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

async def extract_audio_from_video_async(video_path: str | Path, duration: int = 10) -> Optional[Path]:
    """Async videodan audio ajratish (FFmpeg)"""
    try:
        video_path = Path(video_path)
        audio_path = video_path.parent / f"{video_path.stem}_audio.mp3"
        
        # FFmpeg optimallashtirilgan parametrlar
        command = [
            'ffmpeg',
            '-i', str(video_path),
            '-t', str(duration),
            '-vn',
            '-acodec', 'libmp3lame',
            '-ar', '44100',
            '-ab', '128k',
            '-threads', str(psutil.cpu_count()),  # Barcha CPU yadrolar
            '-y',
            '-loglevel', 'error',
            str(audio_path)
        ]
        
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        await asyncio.wait_for(process.communicate(), timeout=30)
        
        if audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path
        
    except asyncio.TimeoutError:
        logger.error("FFmpeg timeout")
    except Exception as e:
        logger.error(f"Audio extraction xatosi: {e}")
    
    return None

# ==================== ASYNC MESSAGE HANDLERS ====================
async def start_command(message: types.Message) -> None:
    """Start komandasi"""
    await cleanup_old_files()
    
    welcome_text = (
        "👋 *Salom! Musiqa topuvchi botman* 🎵\n\n"
        "📱 *Instagram/TikTok* linki yuboring\n"
        "🎤 *Qo'shiq* yoki *ijrochi* nomini yozing\n"
        "🎵 *Audio* fayl yuboring (aniqlash uchun)\n\n"
        "👨‍💻 Dasturchi: @Rustamov_v1"
    )
    
    try:
        await bot.send_message(
            message.chat.id,
            welcome_text,
            parse_mode='Markdown'
        )
    except:
        await bot.send_message(message.chat.id, welcome_text.replace('*', ''))

# ==================== ASYNC AUDIO/VOICE HANDLER ====================
async def handle_audio_message(message: types.Message) -> None:
    """Audio/Voice aniqlash va yuklash"""
    status_msg = None
    audio_file_path = None
    
    try:
        status_msg = await bot.reply_to(message, "🎵 Musiqa aniqlanmoqda...")
        
        # File ID olish
        file_id = message.audio.file_id if message.audio else message.voice.file_id
        
        # Async file yuklash
        file_info = await bot.get_file(file_id)
        audio_data = await bot.download_file(file_info.file_path)
        
        # Shazam aniqlash (parallel ish)
        logger.info("Shazam aniqlash boshlandi...")
        result = await recognize_audio_async(audio_data)
        
        if not result['found']:
            await bot.edit_message_text(
                "❌ Musiqa tanilmadi\n\nBoshqa audio yuboring yoki qo'shiq nomini yozing",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        title = result['title']
        artist = result['artist']
        
        await bot.edit_message_text(
            f"✅ Topildi: {title} - {artist}\n⏳ Yuklanmoqda...",
            message.chat.id,
            status_msg.message_id
        )
        
        # Audio yuklash (parallel)
        query = f"{artist} {title}"
        audio_file_path = await download_youtube_audio_async(query, f"{artist}_{title}")
        
        if audio_file_path and audio_file_path.exists():
            async with aiofiles.open(audio_file_path, 'rb') as audio_file:
                audio_bytes = await audio_file.read()
                
            await bot.send_audio(
                message.chat.id,
                audio_bytes,
                title=title[:64],
                performer=artist[:64],
                caption=f"🎵 {title}\n👤 {artist}"
            )
            
            await bot.delete_message(message.chat.id, status_msg.message_id)
            logger.info(f"✅ Audio yuborildi: {title}")
        else:
            await bot.edit_message_text(
                f"✅ Topildi:\n🎵 {title}\n👤 {artist}\n\n❌ Yuklanmadi, qayta urinib ko'ring",
                message.chat.id,
                status_msg.message_id
            )
    
    except Exception as e:
        logger.error(f"Audio handler xatosi: {e}")
        if status_msg:
            try:
                await bot.edit_message_text(
                    "❌ Xatolik yuz berdi",
                    message.chat.id,
                    status_msg.message_id
                )
            except:
                pass
    
    finally:
        await safe_delete(audio_file_path)

# ==================== INSTAGRAM HANDLER ====================
async def handle_instagram(message: types.Message) -> None:
    """Instagram video yuklash (optimallashtirilgan)"""
    status_msg = None
    video_path = None
    
    try:
        url = message.text.strip().split('?')[0]
        status_msg = await bot.reply_to(message, "⏳")
        
        logger.info(f"Instagram URL: {url}")
        
        # yt-dlp optimallashtirilgan sozlamalar
        ydl_opts = {**INSTAGRAM_OPTIONS}
        
        # Thread poolda yuklash
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(
            thread_pool,
            lambda: _sync_download_with_info(ydl_opts, url)
        )
        
        if not info:
            await bot.edit_message_text(
                "❌ Video yuklanmadi\n\nSabablar:\n• Link noto'g'ri\n• Video private",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        video_id = info.get('id', 'video')
        
        # Video topish
        video_files = list(TEMP_DIR.glob(f"ig_{video_id}*"))
        if not video_files:
            video_files = sorted(
                TEMP_DIR.glob('ig_*.mp4'),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
        
        if not video_files:
            await bot.edit_message_text(
                "❌ Video yuklanmadi",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        video_path = video_files[0]
        
        # Hajmni tekshirish
        file_size = video_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            size_mb = file_size / (1024 * 1024)
            await bot.edit_message_text(
                f"❌ Video juda katta ({size_mb:.1f} MB)\nTelegram limit: 50 MB",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        # Inline tugma
        btn_hash = create_hash(str(video_path))
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "🎵 Musiqani aniqlash",
                callback_data=f"music_{btn_hash}"
            )
        )
        
        # Async video yuborish
        async with aiofiles.open(video_path, 'rb') as video_file:
            video_bytes = await video_file.read()
        
        await bot.send_video(
            message.chat.id,
            video_bytes,
            reply_markup=markup,
            caption="📱 Instagram",
            supports_streaming=True
        )
        
        # Session saqlash
        async with aiofiles.open(TEMP_DIR / f"{btn_hash}.path", 'w') as f:
            await f.write(str(video_path))
        
        await bot.delete_message(message.chat.id, status_msg.message_id)
        logger.info("✅ Instagram video yuborildi")
    
    except Exception as e:
        logger.error(f"Instagram xatosi: {e}")
        if status_msg:
            await bot.edit_message_text(
                "❌ Video yuklanmadi",
                message.chat.id,
                status_msg.message_id
            )
    
    finally:
        # Async delayed delete
        if video_path:
            asyncio.create_task(delayed_delete_async(video_path, 60))

def _sync_download_with_info(options: dict, url: str) -> Optional[Dict]:
    """Sync yuklash info bilan"""
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=True)
    except:
        return None

async def delayed_delete_async(filepath: Path, delay: int) -> None:
    """Async kechiktirilgan o'chirish"""
    await asyncio.sleep(delay)
    await safe_delete(filepath)

# ==================== TIKTOK HANDLER ====================
async def handle_tiktok(message: types.Message) -> None:
    """TikTok video yuklash"""
    status_msg = None
    video_path = None
    
    try:
        url = message.text.strip()
        status_msg = await bot.reply_to(message, "📱 TikTok yuklanmoqda...")
        
        # Thread poolda yuklash
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(
            thread_pool,
            lambda: _sync_download_with_info(TIKTOK_OPTIONS, url)
        )
        
        if not info:
            await bot.edit_message_text(
                "❌ TikTok video yuklanmadi",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        video_id = info.get('id', 'video')
        video_files = sorted(
            TEMP_DIR.glob(f'tt_{video_id}*') or TEMP_DIR.glob('tt_*.mp4'),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        
        if not video_files:
            await bot.edit_message_text(
                "❌ Video yuklanmadi",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        video_path = video_files[0]
        
        # Hajm tekshirish
        file_size = video_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            size_mb = file_size / (1024 * 1024)
            await bot.edit_message_text(
                f"❌ Video juda katta ({size_mb:.1f} MB)",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        # Inline tugma
        btn_hash = create_hash(str(video_path))
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton(
                "🎵 Musiqani aniqlash",
                callback_data=f"music_{btn_hash}"
            )
        )
        
        # Video yuborish
        async with aiofiles.open(video_path, 'rb') as f:
            video_bytes = await f.read()
        
        await bot.send_video(
            message.chat.id,
            video_bytes,
            reply_markup=markup,
            caption="📱 TikTok",
            supports_streaming=True
        )
        
        async with aiofiles.open(TEMP_DIR / f"{btn_hash}.path", 'w') as f:
            await f.write(str(video_path))
        
        await bot.delete_message(message.chat.id, status_msg.message_id)
    
    except Exception as e:
        logger.error(f"TikTok xatosi: {e}")
        if status_msg:
            await bot.edit_message_text(
                "❌ TikTok yuklanmadi",
                message.chat.id,
                status_msg.message_id
            )
    
    finally:
        if video_path:
            asyncio.create_task(delayed_delete_async(video_path, 60))

# ==================== VIDEO MUSIC RECOGNITION ====================
async def handle_video_music_recognition(call: types.CallbackQuery) -> None:
    """Videodan musiqa aniqlash"""
    audio_path = None
    video_path = None
    audio_file_path = None
    
    try:
        btn_hash = call.data.split('_')[1]
        await bot.answer_callback_query(call.id, "🎵 Musiqa aniqlanmoqda...")
        
        # Video path olish
        path_file = TEMP_DIR / f"{btn_hash}.path"
        if not path_file.exists():
            await bot.send_message(call.message.chat.id, "❌ Video topilmadi")
            return
        
        async with aiofiles.open(path_file, 'r') as f:
            video_path = await f.read()
        
        if not Path(video_path).exists():
            await bot.send_message(call.message.chat.id, "❌ Video fayl o'chirilgan")
            return
        
        # Audio ajratish
        audio_path = await extract_audio_from_video_async(video_path, 10)
        
        if not audio_path:
            await bot.send_message(call.message.chat.id, "❌ Audio ajratilmadi")
            return
        
        # Shazam aniqlash
        async with aiofiles.open(audio_path, 'rb') as f:
            audio_data = await f.read()
        
        result = await recognize_audio_async(audio_data)
        
        if not result['found']:
            await bot.send_message(call.message.chat.id, "❌ Musiqa tanilmadi")
            return
        
        title = result['title']
        artist = result['artist']
        
        await bot.send_message(
            call.message.chat.id,
            f"✅ Topildi: {title} - {artist}\n⏳ Yuklanmoqda..."
        )
        
        # Audio yuklash
        query = f"{artist} {title}"
        audio_file_path = await download_youtube_audio_async(query, f"{artist}_{title}")
        
        if audio_file_path and audio_file_path.exists():
            async with aiofiles.open(audio_file_path, 'rb') as f:
                audio_bytes = await f.read()
            
            await bot.send_audio(
                call.message.chat.id,
                audio_bytes,
                title=title[:64],
                performer=artist[:64],
                caption=f"🎵 {title}\n👤 {artist}"
            )
        else:
            await bot.send_message(
                call.message.chat.id,
                f"✅ Topildi:\n🎵 {title}\n👤 {artist}\n\n❌ Yuklanmadi"
            )
    
    except Exception as e:
        logger.error(f"Video music recognition xatosi: {e}")
        await bot.send_message(call.message.chat.id, "❌ Xatolik yuz berdi")
    
    finally:
        await safe_delete(audio_path)
        await safe_delete(audio_file_path)
        await safe_delete(video_path)

# ==================== SEARCH HANDLER ====================
async def handle_search(message: types.Message) -> None:
    """Qidiruv handler (optimallashtirilgan)"""
    status_msg = None
    
    try:
        query = message.text.strip()
        status_msg = await bot.reply_to(message, f"🔍 '{query}' qidirilmoqda...")
        
        # Thread poolda qidirish
        loop = asyncio.get_event_loop()
        songs = await loop.run_in_executor(
            thread_pool,
            lambda: _sync_search(query)
        )
        
        if not songs:
            await bot.edit_message_text(
                "❌ Hech narsa topilmadi",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        # Session saqlash
        user_sessions[message.chat.id] = {
            'query': query,
            'songs': songs,
            'page': 0,
            'timestamp': datetime.now()
        }
        
        # Birinchi sahifani ko'rsatish
        await show_search_results(message.chat.id, 0)
        await bot.delete_message(message.chat.id, status_msg.message_id)
    
    except Exception as e:
        logger.error(f"Qidiruv xatosi: {e}")
        if status_msg:
            await bot.edit_message_text(
                "❌ Qidiruvda xatolik",
                message.chat.id,
                status_msg.message_id
            )

def _sync_search(query: str) -> List[Dict]:
    """Sync qidirish"""
    try:
        with yt_dlp.YoutubeDL(SEARCH_OPTIONS) as ydl:
            info = ydl.extract_info(f"ytsearch30:{query}", download=False)
            return info.get('entries', [])
    except:
        return []

async def show_search_results(chat_id: int, page: int = 0) -> None:
    """Qidiruv natijalarini ko'rsatish"""
    session = user_sessions.get(chat_id)
    if not session:
        await bot.send_message(chat_id, "❌ Sessiya muddati tugagan")
        return
    
    songs = session['songs']
    total_songs = len(songs)
    page_size = 10
    total_pages = (total_songs + page_size - 1) // page_size
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_songs)
    page_songs = songs[start_idx:end_idx]
    
    # Matnni yaratish
    text_lines = [
        f"🔍 *{session['query']}*",
        f"📄 Sahifa: {page + 1}/{total_pages} | Jami: {total_songs} ta",
        ""
    ]
    
    markup = types.InlineKeyboardMarkup(row_width=5)
    
    # Tugmalar yaratish
    button_rows = []
    current_row = []
    
    for idx, song in enumerate(page_songs, start=1):
        global_idx = start_idx + idx
        title = song.get('title', 'Nomaʼlum')[:45]
        duration = format_duration(song.get('duration'))
        
        text_lines.append(f"{global_idx}. {title}{duration}")
        
        url = song.get('url') or song.get('webpage_url')
        if url:
            h = create_hash(f"{url}_{global_idx}")
            
            # Async faylga yozish
            async with aiofiles.open(TEMP_DIR / f"song_{h}.txt", 'w') as f:
                await f.write(f"{url}|{title}|{global_idx}")
            
            btn = types.InlineKeyboardButton(str(global_idx), callback_data=f"dl_{h}")
            current_row.append(btn)
            
            if len(current_row) == 5:
                button_rows.append(current_row)
                current_row = []
    
    if current_row:
        button_rows.append(current_row)
    
    for row in button_rows:
        markup.add(*row)
    
    # Navigation tugmalari
    nav_buttons = []
    if page > 0:
        nav_buttons.append(types.InlineKeyboardButton("⬅️ Oldingi", callback_data=f"page_{page-1}"))
    nav_buttons.append(types.InlineKeyboardButton("❌", callback_data="close_page"))
    if page < total_pages - 1:
        nav_buttons.append(types.InlineKeyboardButton("Keyingi ➡️", callback_data=f"page_{page+1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)
    
    markup.row(
        types.InlineKeyboardButton("🔄 Yangi qidiruv", callback_data="nav_new"),
        types.InlineKeyboardButton("🏠 Bosh menyu", callback_data="nav_home")
    )
    
    user_sessions[chat_id]['page'] = page
    
    try:
        await bot.send_message(
            chat_id,
            "\n".join(text_lines),
            reply_markup=markup,
            parse_mode='Markdown'
        )
    except:
        await bot.send_message(
            chat_id,
            "\n".join(text_lines).replace('*', ''),
            reply_markup=markup
        )

# ==================== CALLBACK HANDLERS ====================
async def handle_page_navigation(call: types.CallbackQuery) -> None:
    """Sahifa navigatsiyasi"""
    try:
        page = int(call.data.split('_')[1])
        await bot.delete_message(call.message.chat.id, call.message.message_id)
        await show_search_results(call.message.chat.id, page)
        await bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Page navigation xatosi: {e}")

async def handle_close_page(call: types.CallbackQuery) -> None:
    """Sahifani yopish"""
    try:
        await bot.delete_message(call.message.chat.id, call.message.message_id)
        await bot.answer_callback_query(call.id, "✅ Sahifa yopildi")
        if call.message.chat.id in user_sessions:
            del user_sessions[call.message.chat.id]
    except:
        pass

async def handle_song_download(call: types.CallbackQuery) -> None:
    """Qo'shiq yuklash"""
    audio_file_path = None
    
    try:
        btn_hash = call.data.split('_')[1]
        data_file = TEMP_DIR / f"song_{btn_hash}.txt"
        
        if not data_file.exists():
            await bot.answer_callback_query(call.id, "❌ Vaqt o'tgan", show_alert=True)
            return
        
        async with aiofiles.open(data_file, 'r') as f:
            data = await f.read()
        
        parts = data.strip().split('|', 2)
        url = parts[0]
        title = parts[1] if len(parts) > 1 else 'Audio'
        
        await bot.answer_callback_query(call.id, "⏳ Yuklanmoqda...")
        
        audio_file_path = await download_youtube_audio_async(url, title)
        
        if audio_file_path and audio_file_path.exists():
            async with aiofiles.open(audio_file_path, 'rb') as f:
                audio_bytes = await f.read()
            
            await bot.send_audio(
                call.message.chat.id,
                audio_bytes,
                title=title[:64],
                caption=f"✅ {title}"
            )
        else:
            await bot.send_message(
                call.message.chat.id,
                "❌ Yuklashda xatolik"
            )
        
        await safe_delete(data_file)
    
    except Exception as e:
        logger.error(f"Download xatosi: {e}")
        await bot.answer_callback_query(call.id, "❌ Xatolik", show_alert=True)
    
    finally:
        await safe_delete(audio_file_path)

async def handle_navigation(call: types.CallbackQuery) -> None:
    """Navigation handler"""
    try:
        await bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    
    if call.data == 'nav_home':
        await start_command(call.message)
    elif call.data == 'nav_new':
        await bot.send_message(
            call.message.chat.id,
            "🔍 Yangi qidiruv uchun qo'shiq yoki ijrochi nomini yozing:"
        )

# ==================== REGISTER HANDLERS ====================
def register_handlers() -> None:
    """Barcha handlerlarni ro'yxatdan o'tkazish"""
    
    @bot.message_handler(commands=['start', 'help'])
    async def start_cmd(message):
        await start_command(message)
    
    @bot.message_handler(content_types=['audio', 'voice'])
    async def audio_cmd(message):
        await handle_audio_message(message)
    
    @bot.message_handler(func=lambda m: m.text and is_instagram_url(m.text))
    async def ig_cmd(message):
        await handle_instagram(message)
    
    @bot.message_handler(func=lambda m: m.text and is_tiktok_url(m.text))
    async def tt_cmd(message):
        await handle_tiktok(message)
    
    @bot.callback_query_handler(func=lambda c: c.data.startswith('music_'))
    async def music_cb(call):
        await handle_video_music_recognition(call)
    
    @bot.callback_query_handler(func=lambda c: c.data.startswith('page_'))
    async def page_cb(call):
        await handle_page_navigation(call)
    
    @bot.callback_query_handler(func=lambda c: c.data == "close_page")
    async def close_cb(call):
        await handle_close_page(call)
    
    @bot.callback_query_handler(func=lambda c: c.data.startswith('dl_'))
    async def dl_cb(call):
        await handle_song_download(call)
    
    @bot.callback_query_handler(func=lambda c: c.data.startswith('nav_'))
    async def nav_cb(call):
        await handle_navigation(call)
    
    @bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'))
    async def search_cmd(message):
        await handle_search(message)

# ==================== PERIODIC CLEANUP ====================
async def periodic_cleanup() -> None:
    """Async davriy tozalash"""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            await cleanup_old_files()
        except Exception as e:
            logger.error(f"Cleanup loop xatosi: {e}")

# ==================== MAIN ====================
async def main() -> None:
    """Async bot ishga tushirish"""
    global bot
    
    logger.info("=" * 60)
    logger.info("🎵 TELEGRAM MUSIC BOT - LINUX OPTIMIZED")
    logger.info("=" * 60)
    logger.info(f"🐍 Python: {sys.version.split()[0]}")
    logger.info(f"💻 CPU Yadrolar: {psutil.cpu_count()}")
    logger.info(f"🧵 Max Workers: {MAX_WORKERS}")
    logger.info(f"📁 Temp: {TEMP_DIR}")
    logger.info("=" * 60)
    
    # Bot init
    bot = await init_bot()
    register_handlers()
    
    # Boshlang'ich tozalash
    await cleanup_old_files()
    
    # Davriy tozalash
    cleanup_task = asyncio.create_task(periodic_cleanup())
    
    try:
        logger.info("✅ Bot ishga tushdi!")
        await bot.infinity_polling(
            skip_pending=True,
            timeout=20,
            long_polling_timeout=20
        )
    
    except KeyboardInterrupt:
        logger.info("\n⌨️ Keyboard interrupt")
    
    except Exception as e:
        logger.error(f"❌ Fatal xatolik: {e}")
    
    finally:
        cleanup_task.cancel()
        await cleanup_old_files()
        if http_session:
            await http_session.close()
        thread_pool.shutdown(wait=False)
        process_pool.shutdown(wait=False)
        logger.info("✅ Bot to'xtatildi")

if __name__ == '__main__':
    # Linux signal handlers
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
