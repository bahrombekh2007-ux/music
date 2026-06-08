#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Music Bot - Linux Stable Version
Instagram, TikTok, Shazam, YouTube Music Search
Sync version - stable and working
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
from datetime import datetime
import threading
import psutil

# Telegram Bot
import telebot
from telebot import types
from telebot.apihelper import ApiException

# Music Recognition
from shazamio import Shazam

# Video/Audio Download
import yt_dlp

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/music_bot.log', encoding='utf-8')
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
CLEANUP_INTERVAL = 600  # 10 minutes

# ==================== YT-DLP CONFIGURATION ====================
BASE_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'socket_timeout': 30,
    'retries': 5,
    'fragment_retries': 5,
    'nocheckcertificate': True,
    'geo_bypass': True,
    'prefer_insecure': True,
}

INSTAGRAM_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'best[filesize<50M]',
    'outtmpl': str(TEMP_DIR / 'ig_%(id)s.%(ext)s'),
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Origin': 'https://www.instagram.com',
        'Referer': 'https://www.instagram.com/',
    },
}

TIKTOK_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'best[filesize<50M]',
    'outtmpl': str(TEMP_DIR / 'tt_%(id)s.%(ext)s'),
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.tiktok.com/',
    },
}

AUDIO_OPTIONS = {
    **BASE_OPTIONS,
    'format': 'bestaudio/best',
    'outtmpl': str(TEMP_DIR / 'audio_%(title)s.%(ext)s'),
    'restrictfilenames': True,
    'windowsfilenames': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '128',
    }],
}

SEARCH_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': True,
    'socket_timeout': 20,
}

# ==================== GLOBAL STATE ====================
user_sessions: Dict[int, Dict] = {}
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None, threaded=True, skip_pending=True)

# ==================== UTILITY FUNCTIONS ====================
def cleanup_old_files() -> None:
    """Eski fayllarni o'chirish"""
    try:
        current_time = time.time()
        deleted_count = 0
        
        for filepath in TEMP_DIR.iterdir():
            if filepath.is_file():
                file_age = current_time - filepath.stat().st_mtime
                if file_age > CLEANUP_INTERVAL:
                    try:
                        filepath.unlink()
                        deleted_count += 1
                    except:
                        pass
        
        if deleted_count > 0:
            logger.info(f"🧹 {deleted_count} ta eski fayl o'chirildi")
            
    except Exception as e:
        logger.error(f"Cleanup xatosi: {e}")

def safe_delete(filepath: Optional[str | Path]) -> None:
    """Faylni xavfsiz o'chirish"""
    try:
        if filepath:
            path = Path(filepath)
            if path.exists() and path.is_file():
                path.unlink()
    except Exception as e:
        logger.debug(f"Delete xatosi: {e}")

def create_hash(text: str) -> str:
    """Hash yaratish"""
    return hashlib.md5(str(text).encode('utf-8')).hexdigest()[:12]

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
        minutes = total_seconds // 60
        secs = total_seconds % 60
        return f" ({minutes}:{secs:02d})"
    except (TypeError, ValueError):
        return ""

def is_instagram_url(url: str) -> bool:
    """Instagram URL tekshirish"""
    patterns = [
        r'instagram\.com/(p|reel|reels|tv)/',
        r'instagram\.com/stories/',
    ]
    url_lower = url.lower().strip()
    return any(re.search(pattern, url_lower) for pattern in patterns)

def is_tiktok_url(url: str) -> bool:
    """TikTok URL tekshirish"""
    patterns = [
        r'tiktok\.com/',
        r'vm\.tiktok\.com/',
        r'vt\.tiktok\.com/',
    ]
    url_lower = url.lower().strip()
    return any(re.search(pattern, url_lower) for pattern in patterns)

# ==================== SHAZAM RECOGNITION ====================
async def recognize_audio_async(audio_bytes: bytes) -> Dict:
    """Shazam bilan musiqa aniqlash"""
    temp_file = None
    
    try:
        # Vaqtinchalik fayl yaratish
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix='.mp3',
            dir=TEMP_DIR
        ) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name
        
        # Shazam aniqlash
        shazam = Shazam()
        result = await shazam.recognize(temp_path)
        
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
        if temp_file:
            safe_delete(temp_path)
    
    return {'found': False}

def recognize_audio(audio_bytes: bytes) -> Dict:
    """Sync wrapper for Shazam"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(recognize_audio_async(audio_bytes))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"Async loop xatosi: {e}")
        return {'found': False}

# ==================== DOWNLOAD FUNCTIONS ====================
def download_youtube_audio(query: str, filename_hint: str = "") -> Optional[Path]:
    """YouTube'dan audio yuklash"""
    try:
        clean_name = clean_filename(filename_hint or query)
        options = AUDIO_OPTIONS.copy()
        options['outtmpl'] = str(TEMP_DIR / f"audio_{clean_name}.%(ext)s")
        
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([f"ytsearch1:{query}"])
        
        # Yuklanganini tekshirish
        output_path = TEMP_DIR / f"audio_{clean_name}.mp3"
        if output_path.exists():
            return output_path
        
        # Fallback: eng yangi mp3 topish
        mp3_files = sorted(
            TEMP_DIR.glob('audio_*.mp3'),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        
        if mp3_files and (time.time() - mp3_files[0].stat().st_mtime) < 120:
            return mp3_files[0]
        
    except Exception as e:
        logger.error(f"Audio yuklash xatosi: {e}")
    
    return None

def extract_audio_from_video(video_path: str | Path, duration: int = 10) -> Optional[Path]:
    """Videodan audio ajratish (FFmpeg)"""
    try:
        video_path = Path(video_path)
        audio_path = video_path.parent / f"{video_path.stem}_audio.mp3"
        
        command = [
            'ffmpeg',
            '-i', str(video_path),
            '-t', str(duration),
            '-vn',
            '-acodec', 'mp3',
            '-ar', '44100',
            '-ab', '128k',
            '-y',
            '-loglevel', 'error',
            str(audio_path)
        ]
        
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=60,
            check=False
        )
        
        if audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path
        
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg timeout")
    except Exception as e:
        logger.error(f"Audio extraction xatosi: {e}")
    
    return None

def delayed_delete(filepath: Path, delay: int = 60) -> None:
    """Kechiktirilgan o'chirish"""
    def _delete():
        time.sleep(delay)
        safe_delete(filepath)
    
    thread = threading.Thread(target=_delete, daemon=True)
    thread.start()

# ==================== MESSAGE HANDLERS ====================
@bot.message_handler(commands=['start', 'help'])
def start_command(message: types.Message) -> None:
    """Start komandasi"""
    cleanup_old_files()
    
    welcome_text = (
        "👋 *Salom! Musiqa topuvchi botman* 🎵\n\n"
        "📱 *Instagram/TikTok* linki yuboring\n"
        "🎤 *Qo'shiq* yoki *ijrochi* nomini yozing\n"
        "🎵 *Audio* fayl yuboring (aniqlash uchun)\n\n"
        "👨‍💻 Dasturchi: @Rustamov_v1"
    )
    
    try:
        bot.send_message(
            message.chat.id,
            welcome_text,
            parse_mode='Markdown'
        )
    except:
        bot.send_message(message.chat.id, welcome_text.replace('*', ''))

# ==================== AUDIO/VOICE HANDLER ====================
@bot.message_handler(content_types=['audio', 'voice'])
def handle_audio_message(message: types.Message) -> None:
    """Audio/Voice aniqlash va yuklash"""
    status_msg = None
    audio_file_path = None
    
    try:
        status_msg = bot.reply_to(message, "🎵 Musiqa aniqlanmoqda...")
        
        # File ID olish
        file_id = message.audio.file_id if message.audio else message.voice.file_id
        
        # File yuklab olish
        file_info = bot.get_file(file_id)
        audio_data = bot.download_file(file_info.file_path)
        
        # Shazam aniqlash
        logger.info("Shazam aniqlash boshlandi...")
        result = recognize_audio(audio_data)
        
        if not result['found']:
            bot.edit_message_text(
                "❌ Musiqa tanilmadi\n\nBoshqa audio yuboring yoki qo'shiq nomini yozing",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        title = result['title']
        artist = result['artist']
        
        bot.edit_message_text(
            f"✅ Topildi: {title} - {artist}\n⏳ Yuklanmoqda...",
            message.chat.id,
            status_msg.message_id
        )
        
        # Audio yuklash
        query = f"{artist} {title}"
        audio_file_path = download_youtube_audio(query, f"{artist}_{title}")
        
        if audio_file_path and audio_file_path.exists():
            with open(audio_file_path, 'rb') as audio_file:
                bot.send_audio(
                    message.chat.id,
                    audio_file,
                    title=title[:64],
                    performer=artist[:64],
                    caption=f"🎵 {title}\n👤 {artist}"
                )
            
            bot.delete_message(message.chat.id, status_msg.message_id)
            logger.info(f"✅ Audio yuborildi: {title}")
        else:
            bot.edit_message_text(
                f"✅ Topildi:\n🎵 {title}\n👤 {artist}\n\n❌ Yuklanmadi, qayta urinib ko'ring",
                message.chat.id,
                status_msg.message_id
            )
    
    except ApiException as e:
        logger.error(f"Telegram API xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text(
                    "❌ Xatolik yuz berdi",
                    message.chat.id,
                    status_msg.message_id
                )
            except:
                pass
    
    except Exception as e:
        logger.error(f"Audio handler xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text(
                    "❌ Xatolik yuz berdi",
                    message.chat.id,
                    status_msg.message_id
                )
            except:
                pass
    
    finally:
        safe_delete(audio_file_path)

# ==================== INSTAGRAM HANDLER ====================
@bot.message_handler(func=lambda m: m.text and is_instagram_url(m.text))
def handle_instagram(message: types.Message) -> None:
    """Instagram video yuklash"""
    status_msg = None
    video_path = None
    
    try:
        url = message.text.strip().split('?')[0]
        status_msg = bot.reply_to(message, "⏳ Instagram yuklanmoqda...")
        
        logger.info(f"Instagram URL: {url}")
        
        # yt-dlp bilan yuklash
        with yt_dlp.YoutubeDL(INSTAGRAM_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', 'video')
        
        # Video topish
        video_files = list(TEMP_DIR.glob(f"ig_{video_id}*"))
        if not video_files:
            video_files = sorted(
                list(TEMP_DIR.glob('ig_*.mp4')) + list(TEMP_DIR.glob('ig_*.webm')),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
        
        if not video_files:
            bot.edit_message_text(
                "❌ Video yuklanmadi\n\n"
                "Sabablar:\n"
                "• Link noto'g'ri\n"
                "• Video private\n"
                "• Instagram blok qilgan",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        video_path = video_files[0]
        
        # Agar .webm bo'lsa, .mp4 ga o'zgartirish
        if video_path.suffix == '.webm':
            mp4_path = video_path.with_suffix('.mp4')
            try:
                subprocess.run(
                    ['ffmpeg', '-i', str(video_path), '-c', 'copy', str(mp4_path), '-y', '-loglevel', 'error'],
                    capture_output=True,
                    timeout=60,
                    check=True
                )
                safe_delete(video_path)
                video_path = mp4_path
            except:
                pass  # Agar ffmpeg ishlamasa, webm yuboramiz
        
        # Hajmni tekshirish
        file_size = video_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            size_mb = file_size / (1024 * 1024)
            bot.edit_message_text(
                f"❌ Video juda katta ({size_mb:.1f} MB)\n"
                f"Telegram limit: 50 MB",
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
        with open(video_path, 'rb') as video_file:
            bot.send_video(
                message.chat.id,
                video_file,
                reply_markup=markup,
                caption="📱 Instagram",
                supports_streaming=True,
                timeout=120
            )
        
        # Session saqlash
        (TEMP_DIR / f"{btn_hash}.path").write_text(str(video_path))
        
        bot.delete_message(message.chat.id, status_msg.message_id)
        logger.info("✅ Instagram video yuborildi")
    
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.error(f"yt-dlp xatosi: {error_msg}")
        
        if status_msg:
            if "Private" in error_msg or "login" in error_msg.lower():
                msg = "❌ Bu video private (shaxsiy)"
            elif "unavailable" in error_msg.lower():
                msg = "❌ Video mavjud emas"
            else:
                msg = "❌ Instagram video yuklanmadi\n\nQayta urinib ko'ring"
            
            try:
                bot.edit_message_text(msg, message.chat.id, status_msg.message_id)
            except:
                pass
    
    except Exception as e:
        logger.error(f"Instagram xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text(
                    "❌ Video yuklanmadi",
                    message.chat.id,
                    status_msg.message_id
                )
            except:
                pass
    
    finally:
        # Kechiktirilgan o'chirish
        if video_path:
            delayed_delete(video_path, 60)

# ==================== TIKTOK HANDLER ====================
@bot.message_handler(func=lambda m: m.text and is_tiktok_url(m.text))
def handle_tiktok(message: types.Message) -> None:
    """TikTok video yuklash"""
    status_msg = None
    video_path = None
    
    try:
        url = message.text.strip()
        status_msg = bot.reply_to(message, "📱 TikTok yuklanmoqda...")
        
        logger.info(f"TikTok URL: {url}")
        
        # yt-dlp bilan yuklash
        with yt_dlp.YoutubeDL(TIKTOK_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', 'video')
        
        # Video topish
        video_files = list(TEMP_DIR.glob(f"tt_{video_id}*"))
        if not video_files:
            video_files = sorted(
                list(TEMP_DIR.glob('tt_*.mp4')) + list(TEMP_DIR.glob('tt_*.webm')),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
        
        if not video_files:
            bot.edit_message_text(
                "❌ TikTok video yuklanmadi\n\n"
                "Sabablar:\n"
                "• Link noto'g'ri\n"
                "• Video private\n"
                "• TikTok blok qilgan",
                message.chat.id,
                status_msg.message_id
            )
            return
        
        video_path = video_files[0]
        
        # Agar .webm bo'lsa, .mp4 ga o'zgartirish
        if video_path.suffix == '.webm':
            mp4_path = video_path.with_suffix('.mp4')
            try:
                subprocess.run(
                    ['ffmpeg', '-i', str(video_path), '-c', 'copy', str(mp4_path), '-y', '-loglevel', 'error'],
                    capture_output=True,
                    timeout=60,
                    check=True
                )
                safe_delete(video_path)
                video_path = mp4_path
            except:
                pass
        
        # Hajmni tekshirish
        file_size = video_path.stat().st_size
        if file_size > MAX_FILE_SIZE:
            size_mb = file_size / (1024 * 1024)
            bot.edit_message_text(
                f"❌ Video juda katta ({size_mb:.1f} MB)\n"
                f"Telegram limit: 50 MB",
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
        with open(video_path, 'rb') as video_file:
            bot.send_video(
                message.chat.id,
                video_file,
                reply_markup=markup,
                caption="📱 TikTok",
                supports_streaming=True,
                timeout=120
            )
        
        # Session saqlash
        (TEMP_DIR / f"{btn_hash}.path").write_text(str(video_path))
        
        bot.delete_message(message.chat.id, status_msg.message_id)
        logger.info("✅ TikTok video yuborildi")
    
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.error(f"yt-dlp xatosi: {error_msg}")
        
        if status_msg:
            if "Private" in error_msg or "login" in error_msg.lower():
                msg = "❌ Bu video private (shaxsiy)"
            elif "unavailable" in error_msg.lower():
                msg = "❌ Video mavjud emas"
            else:
                msg = "❌ TikTok video yuklanmadi\n\nQayta urinib ko'ring"
            
            try:
                bot.edit_message_text(msg, message.chat.id, status_msg.message_id)
            except:
                pass
    
    except Exception as e:
        logger.error(f"TikTok xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text(
                    "❌ TikTok yuklanmadi",
                    message.chat.id,
                    status_msg.message_id
                )
            except:
                pass
    
    finally:
        if video_path:
            delayed_delete(video_path, 60)

# ==================== VIDEO MUSIC RECOGNITION ====================
@bot.callback_query_handler(func=lambda c: c.data.startswith('music_'))
def handle_video_music_recognition(call: types.CallbackQuery) -> None:
    """Videodan musiqa aniqlash"""
    audio_path = None
    video_path = None
    audio_file_path = None
    
    try:
        btn_hash = call.data.split('_')[1]
        bot.answer_callback_query(call.id, "🎵 Musiqa aniqlanmoqda...")
        
        # Video path olish
        path_file = TEMP_DIR / f"{btn_hash}.path"
        if not path_file.exists():
            bot.send_message(call.message.chat.id, "❌ Video topilmadi (vaqt o'tgan)")
            return
        
        video_path = path_file.read_text().strip()
        if not Path(video_path).exists():
            bot.send_message(call.message.chat.id, "❌ Video fayl o'chirilgan")
            return
        
        # Audio ajratish
        logger.info("Audio ajratilmoqda...")
        audio_path = extract_audio_from_video(video_path, 10)
        
        if not audio_path or not audio_path.exists():
            bot.send_message(call.message.chat.id, "❌ Audio ajratilmadi")
            return
        
        # Shazam aniqlash
        with open(audio_path, 'rb') as f:
            audio_data = f.read()
        
        result = recognize_audio(audio_data)
        
        if not result['found']:
            bot.send_message(call.message.chat.id, "❌ Musiqa tanilmadi")
            return
        
        title = result['title']
        artist = result['artist']
        
        bot.send_message(
            call.message.chat.id,
            f"✅ Topildi: {title} - {artist}\n⏳ Yuklanmoqda..."
        )
        
        # Audio yuklash
        query = f"{artist} {title}"
        audio_file_path = download_youtube_audio(query, f"{artist}_{title}")
        
        if audio_file_path and audio_file_path.exists():
            with open(audio_file_path, 'rb') as audio_file:
                bot.send_audio(
                    call.message.chat.id,
                    audio_file,
                    title=title[:64],
                    performer=artist[:64],
                    caption=f"🎵 {title}\n👤 {artist}"
                )
            logger.info(f"✅ Audio yuborildi: {title}")
        else:
            bot.send_message(
                call.message.chat.id,
                f"✅ Topildi:\n🎵 {title}\n👤 {artist}\n\n❌ Yuklanmadi"
            )
    
    except Exception as e:
        logger.error(f"Video music recognition xatosi: {e}")
        bot.send_message(call.message.chat.id, "❌ Xatolik yuz berdi")
    
    finally:
        safe_delete(audio_path)
        safe_delete(audio_file_path)
        if video_path:
            safe_delete(video_path)

# ==================== SEARCH HANDLER ====================
@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'))
def handle_search(message: types.Message) -> None:
    """Qidiruv handler"""
    status_msg = None
    
    try:
        query = message.text.strip()
        status_msg = bot.reply_to(message, f"🔍 '{query}' qidirilmoqda...")
        
        logger.info(f"Qidiruv: {query}")
        
        # YouTube qidiruv - 30 ta
        with yt_dlp.YoutubeDL(SEARCH_OPTIONS) as ydl:
            info = ydl.extract_info(f"ytsearch30:{query}", download=False)
            songs = info.get('entries', [])
        
        if not songs:
            bot.edit_message_text(
                "❌ Hech narsa topilmadi\n\nBoshqa nom bilan qidiring",
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
        show_search_results(message.chat.id, 0)
        bot.delete_message(message.chat.id, status_msg.message_id)
    
    except Exception as e:
        logger.error(f"Qidiruv xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text(
                    "❌ Qidiruvda xatolik",
                    message.chat.id,
                    status_msg.message_id
                )
            except:
                pass

def show_search_results(chat_id: int, page: int = 0) -> None:
    """Qidiruv natijalarini ko'rsatish"""
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "❌ Sessiya muddati tugagan\n\nYangi qidiruv bering")
        return
    
    query = session['query']
    songs = session['songs']
    total_songs = len(songs)
    page_size = 10
    total_pages = (total_songs + page_size - 1) // page_size
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_songs)
    page_songs = songs[start_idx:end_idx]
    
    text_lines = [
        f"🔍 *{query}*",
        f"📄 Sahifa: {page + 1}/{total_pages} | Jami: {total_songs} ta",
        ""
    ]
    
    markup = types.InlineKeyboardMarkup(row_width=5)
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
            (TEMP_DIR / f"song_{h}.txt").write_text(f"{url}|{title}|{global_idx}")
            
            btn = types.InlineKeyboardButton(str(global_idx), callback_data=f"dl_{h}")
            current_row.append(btn)
            
            if len(current_row) == 5:
                button_rows.append(current_row)
                current_row = []
    
    if current_row:
        button_rows.append(current_row)
    
    for row in button_rows:
        markup.add(*row)
    
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
        bot.send_message(
            chat_id,
            "\n".join(text_lines),
            reply_markup=markup,
            parse_mode='Markdown'
        )
    except:
        bot.send_message(
            chat_id,
            "\n".join(text_lines).replace('*', ''),
            reply_markup=markup
        )

# ==================== CALLBACK HANDLERS ====================
@bot.callback_query_handler(func=lambda c: c.data.startswith('page_'))
def handle_page_navigation(call: types.CallbackQuery) -> None:
    """Sahifa navigatsiyasi"""
    try:
        page = int(call.data.split('_')[1])
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_search_results(call.message.chat.id, page)
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Page navigation xatosi: {e}")
        bot.answer_callback_query(call.id, "❌ Xatolik", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data == "close_page")
def handle_close_page(call: types.CallbackQuery) -> None:
    """Sahifani yopish"""
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "✅ Sahifa yopildi")
        if call.message.chat.id in user_sessions:
            del user_sessions[call.message.chat.id]
    except Exception as e:
        logger.error(f"Close page xatosi: {e}")

@bot.callback_query_handler(func=lambda c: c.data.startswith('dl_'))
def handle_song_download(call: types.CallbackQuery) -> None:
    """Qo'shiq yuklash"""
    audio_file_path = None
    
    try:
        btn_hash = call.data.split('_')[1]
        
        data_file = TEMP_DIR / f"song_{btn_hash}.txt"
        if not data_file.exists():
            bot.answer_callback_query(call.id, "❌ Vaqt o'tgan", show_alert=True)
            return
        
        data = data_file.read_text().strip()
        parts = data.split('|', 2)
        
        if len(parts) == 3:
            url, title, song_num = parts
        elif len(parts) == 2:
            url, title = parts
        else:
            url = parts[0]
            title = 'Audio'
        
        bot.answer_callback_query(call.id, "⏳ Yuklanmoqda...")
        logger.info(f"Yuklash: {title}")
        
        audio_file_path = download_youtube_audio(url, title)
        
        if audio_file_path and audio_file_path.exists():
            with open(audio_file_path, 'rb') as audio_file:
                bot.send_audio(
                    call.message.chat.id,
                    audio_file,
                    title=title[:64],
                    caption=f"✅ {title}"
                )
            logger.info(f"✅ Yuklandi: {title}")
        else:
            bot.send_message(
                call.message.chat.id,
                "❌ Yuklashda xatolik\n\nQayta urinib ko'ring"
            )
        
        safe_delete(data_file)
    
    except Exception as e:
        logger.error(f"Download xatosi: {e}")
        bot.answer_callback_query(call.id, "❌ Xatolik yuz berdi", show_alert=True)
    
    finally:
        safe_delete(audio_file_path)

@bot.callback_query_handler(func=lambda c: c.data.startswith('nav_'))
def handle_navigation(call: types.CallbackQuery) -> None:
    """Navigation handler"""
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass
    
    if call.data == 'nav_home':
        start_command(call.message)
    elif call.data == 'nav_new':
        bot.send_message(
            call.message.chat.id,
            "🔍 Yangi qidiruv uchun qo'shiq yoki ijrochi nomini yozing:"
        )

# ==================== SHUTDOWN HANDLER ====================
def shutdown_handler(signum, frame) -> None:
    """Graceful shutdown"""
    logger.info("\n🛑 Bot to'xtatilmoqda...")
    
    try:
        cleanup_old_files()
        bot.stop_polling()
    except Exception as e:
        logger.error(f"Shutdown xatosi: {e}")
    
    logger.info("✅ Bot to'xtatildi")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

# ==================== PERIODIC CLEANUP ====================
def start_periodic_cleanup() -> None:
    """Davriy tozalash"""
    def cleanup_loop():
        while True:
            try:
                time.sleep(CLEANUP_INTERVAL)
                cleanup_old_files()
            except Exception as e:
                logger.error(f"Cleanup loop xatosi: {e}")
    
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    logger.info("🧹 Davriy tozalash yoqildi")

# ==================== MAIN ====================
def main() -> None:
    """Bot ishga tushirish"""
    logger.info("=" * 60)
    logger.info("🎵 TELEGRAM MUSIC BOT - LINUX STABLE")
    logger.info("=" * 60)
    logger.info(f"🐍 Python: {sys.version.split()[0]}")
    logger.info(f"📦 pyTelegramBotAPI: {version('pyTelegramBotAPI')}")
    logger.info(f"📁 Temp katalog: {TEMP_DIR.absolute()}")
    logger.info("=" * 60)
    logger.info("✅ Bot ishga tushdi!")
    logger.info("⚡ Tez va xavfsiz")
    logger.info("📱 Instagram, TikTok, Shazam, YouTube")
    logger.info("=" * 60)
    
    # Boshlang'ich tozalash
    cleanup_old_files()
    
    # Davriy tozalash
    start_periodic_cleanup()
    
    try:
        # Bot polling
        logger.info("🔄 Polling boshlandi...")
        bot.infinity_polling(
            skip_pending=True,
            timeout=30,
            long_polling_timeout=30,
            none_stop=True
        )
    
    except KeyboardInterrupt:
        logger.info("\n⌨️ Keyboard interrupt")
        shutdown_handler(None, None)
    
    except Exception as e:
        logger.error(f"❌ Fatal xatolik: {e}")
        logger.info("🔄 3 soniyadan keyin qayta ishga tushiriladi...")
        time.sleep(3)
        
        try:
            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30,
                none_stop=True
            )
        except Exception as e2:
            logger.error(f"❌ Qayta urinish muvaffaqiyatsiz: {e2}")
            sys.exit(1)

if __name__ == '__main__':
    main()
