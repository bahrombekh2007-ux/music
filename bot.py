#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Music Search Bot
- Qo'shiq nomi / ijrochi → YouTube qidiruv natijalari
- Audio/Voice → Shazam aniqlash
- Instagram / TikTok link → video yuklab, musiqasini Shazam bilan aniqlash
- YUKLAB BERMAYDI — faqat musiqa ma'lumotini topadi
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
import threading
from importlib.metadata import version
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime

import telebot
from telebot import types
from telebot.apihelper import ApiException

from shazamio import Shazam
import yt_dlp

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ==================== CONFIG ====================
BOT_TOKEN = "8524502393:AAFvZmPd2VtjSPrw6TLQdJ9iojHYMlPsj_E"
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024   # 50 MB
CLEANUP_INTERVAL = 600              # 10 daqiqa
PAGE_SIZE = 10                      # Sahifadagi natijalar soni

# ==================== GLOBAL STATE ====================
user_sessions: Dict[int, Dict] = {}

# ==================== BOT INIT ====================
def init_bot() -> telebot.TeleBot:
    try:
        telebot.TeleBot(BOT_TOKEN).remove_webhook()
        logger.info("✅ Webhook o'chirildi")
    except Exception as e:
        logger.warning(f"⚠️ Webhook xatosi: {e}")

    return telebot.TeleBot(
        BOT_TOKEN,
        parse_mode=None,
        threaded=False,
        skip_pending=True
    )

bot = init_bot()

# ==================== YT-DLP OPTIONS ====================
SEARCH_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': True,
    'socket_timeout': 20,
}

IG_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'format': 'best[filesize<50M]/best',
    'outtmpl': str(TEMP_DIR / 'ig_%(id)s.%(ext)s'),
    'socket_timeout': 30,
    'retries': 5,
    'fragment_retries': 5,
    'nocheckcertificate': True,
    'geo_bypass': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/91.0 Mobile Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Origin': 'https://www.instagram.com',
        'Referer': 'https://www.instagram.com/',
    },
}

TT_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'format': 'best[filesize<50M]/best',
    'outtmpl': str(TEMP_DIR / 'tt_%(id)s.%(ext)s'),
    'socket_timeout': 30,
    'retries': 5,
    'fragment_retries': 5,
    'nocheckcertificate': True,
    'geo_bypass': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/91.0 Mobile Safari/537.36',
        'Referer': 'https://www.tiktok.com/',
    },
}

# ==================== HELPERS ====================
def safe_delete(path) -> None:
    try:
        if path:
            p = Path(path)
            if p.exists() and p.is_file():
                p.unlink()
    except Exception:
        pass

def cleanup_old_files() -> None:
    try:
        now = time.time()
        count = 0
        for f in TEMP_DIR.iterdir():
            if f.is_file() and now - f.stat().st_mtime > CLEANUP_INTERVAL:
                f.unlink()
                count += 1
        if count:
            logger.info(f"🧹 {count} ta eski fayl o'chirildi")
    except Exception as e:
        logger.error(f"Cleanup xatosi: {e}")

def create_hash(text: str) -> str:
    return hashlib.md5(str(text).encode()).hexdigest()[:12]

def format_duration(seconds) -> str:
    try:
        s = int(float(seconds))
        return f" ({s//60}:{s%60:02d})"
    except Exception:
        return ""

def is_instagram_url(url: str) -> bool:
    return bool(re.search(r'instagram\.com/(p|reel|reels|tv|stories)/', url.lower()))

def is_tiktok_url(url: str) -> bool:
    return bool(re.search(r'(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)', url.lower()))

# ==================== SHAZAM ====================
async def _shazam_recognize(path: str) -> Dict:
    try:
        result = await Shazam().recognize(path)
        if result and 'track' in result:
            t = result['track']
            return {
                'found': True,
                'title': t.get('title', 'Noma\'lum'),
                'artist': t.get('subtitle', 'Noma\'lum'),
            }
    except Exception as e:
        logger.error(f"Shazam xatosi: {e}")
    return {'found': False}

def recognize_audio(audio_bytes: bytes) -> Dict:
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3', dir=TEMP_DIR) as f:
            f.write(audio_bytes)
            tmp = f.name
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_shazam_recognize(tmp))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"recognize_audio xatosi: {e}")
        return {'found': False}
    finally:
        safe_delete(tmp)

def extract_audio_snippet(video_path, duration=10) -> Optional[Path]:
    """Videodan qisqa audio ajratish (Shazam uchun)"""
    try:
        out = Path(video_path).parent / f"{Path(video_path).stem}_snip.mp3"
        cmd = ['ffmpeg', '-i', str(video_path), '-t', str(duration),
               '-vn', '-acodec', 'mp3', '-ar', '44100', '-ab', '128k', '-y', str(out)]
        subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        if out.exists() and out.stat().st_size > 0:
            return out
    except Exception as e:
        logger.error(f"Audio snippet xatosi: {e}")
    return None

# ==================== DOWNLOAD VIDEO (for Shazam only) ====================
def download_video(url: str, opts: dict) -> Optional[Path]:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            vid_id = info.get('id', '')

        prefix = 'ig_' if 'instagram' in url else 'tt_'
        files = list(TEMP_DIR.glob(f"{prefix}{vid_id}*"))
        if not files:
            files = sorted(
                list(TEMP_DIR.glob(f'{prefix}*.mp4')) + list(TEMP_DIR.glob(f'{prefix}*.webm')),
                key=lambda f: f.stat().st_mtime, reverse=True
            )
        if files:
            return files[0]
    except Exception as e:
        logger.error(f"Video yuklash xatosi: {e}")
    return None

# ==================== SEARCH RESULT UI ====================
def show_search_results(chat_id: int, page: int = 0) -> None:
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "❌ Sessiya muddati tugagan. Yangi qidiruv bering.")
        return

    songs = session['songs']
    query = session['query']
    total = len(songs)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    page_songs = songs[start:end]

    lines = [
        f"🔍 *{query}*",
        f"📄 Sahifa: {page+1}/{total_pages} | Jami: {total} ta",
        ""
    ]

    markup = types.InlineKeyboardMarkup(row_width=5)
    current_row = []
    button_rows = []

    for i, song in enumerate(page_songs, start=1):
        if not song:
            continue
        global_idx = start + i
        title = (song.get('title') or 'Noma\'lum')[:45]
        dur = format_duration(song.get('duration'))
        lines.append(f"{global_idx}. {title}{dur}")

        url = song.get('url') or song.get('webpage_url')
        if url:
            h = create_hash(f"{url}_{global_idx}")
            (TEMP_DIR / f"song_{h}.txt").write_text(f"{url}|{title}|{global_idx}")
            current_row.append(types.InlineKeyboardButton(str(global_idx), callback_data=f"info_{h}"))
            if len(current_row) == 5:
                button_rows.append(current_row)
                current_row = []

    if current_row:
        button_rows.append(current_row)
    for row in button_rows:
        markup.add(*row)

    # Navigatsiya
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅️ Oldingi", callback_data=f"page_{page-1}"))
    nav.append(types.InlineKeyboardButton("❌", callback_data="close_page"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton("Keyingi ➡️", callback_data=f"page_{page+1}"))
    if nav:
        markup.row(*nav)

    markup.row(
        types.InlineKeyboardButton("🔄 Yangi qidiruv", callback_data="nav_new"),
        types.InlineKeyboardButton("🏠 Bosh menyu", callback_data="nav_home")
    )

    session['page'] = page
    text = "\n".join(lines)
    try:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
    except Exception:
        bot.send_message(chat_id, text.replace('*', ''), reply_markup=markup)

# ==================== SONG INFO (no download) ====================
def get_youtube_info(url: str) -> Optional[Dict]:
    """YouTube videosidan ma'lumot olish (yuklamasdan)"""
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'socket_timeout': 20}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception as e:
        logger.error(f"YouTube info xatosi: {e}")
    return None

# ==================== HANDLERS ====================

@bot.message_handler(commands=['start', 'help'])
def start_command(message: types.Message) -> None:
    cleanup_old_files()
    text = (
        "👋 *Salom! Men musiqa topuvchi botman* 🎵\n\n"
        "📝 *Nima qila olaman:*\n"
        "• Qo\'shiq yoki ijrochi nomini yozsangiz — YouTube\'dan topib beraman\n"
        "• Audio/Voice yubotsangiz — Shazam bilan aniqlayman\n"
        "• Instagram/TikTok link yubotsangiz — videodagi musiqani topaman\n\n"
        "⚠️ *Eslatma:* Men faqat *musiqa ma\'lumotini topaman*, yuklab bermayman.\n\n"
        "👨‍💻 Dasturchi: @Rustamov_v1"
    )
    try:
        bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception:
        bot.send_message(message.chat.id, text.replace('*', ''))


@bot.message_handler(content_types=['audio', 'voice'])
def handle_audio_message(message: types.Message) -> None:
    """Audio/Voice → Shazam aniqlash"""
    status_msg = None
    try:
        status_msg = bot.reply_to(message, "🎵 Musiqa aniqlanmoqda...")

        file_id = message.audio.file_id if message.audio else message.voice.file_id
        file_info = bot.get_file(file_id)
        audio_data = bot.download_file(file_info.file_path)

        result = recognize_audio(audio_data)

        if not result['found']:
            bot.edit_message_text(
                "❌ Musiqa tanilmadi.\n\nBoshqa audio yuboring yoki qo'shiq nomini yozing.",
                message.chat.id, status_msg.message_id
            )
            return

        title = result['title']
        artist = result['artist']

        response = (
            f"✅ *Musiqa topildi!*\n\n"
            f"🎵 *Qo\'shiq:* {title}\n"
            f"👤 *Ijrochi:* {artist}\n\n"
            f"🔍 Qidirish uchun: `{artist} {title}`"
        )
        try:
            bot.edit_message_text(response, message.chat.id, status_msg.message_id, parse_mode='Markdown')
        except Exception:
            bot.edit_message_text(response.replace('*', '').replace('`', ''), message.chat.id, status_msg.message_id)

        logger.info(f"✅ Shazam topdi: {title} - {artist}")

    except ApiException as e:
        logger.error(f"Telegram API xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text("❌ Xatolik yuz berdi.", message.chat.id, status_msg.message_id)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Audio handler xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text("❌ Xatolik yuz berdi.", message.chat.id, status_msg.message_id)
            except Exception:
                pass


@bot.message_handler(func=lambda m: m.text and is_instagram_url(m.text))
def handle_instagram(message: types.Message) -> None:
    """Instagram → video yuklab → Shazam → ma'lumot"""
    status_msg = None
    video_path = None
    audio_path = None
    try:
        url = message.text.strip().split('?')[0]
        status_msg = bot.reply_to(message, "📱 Instagram videosi tahlil qilinmoqda...")

        video_path = download_video(url, IG_OPTIONS)
        if not video_path or not video_path.exists():
            bot.edit_message_text(
                "❌ Instagram video yuklanmadi.\n\n• Link noto'g'ri bo'lishi mumkin\n• Video private bo'lishi mumkin",
                message.chat.id, status_msg.message_id
            )
            return

        # Webm → mp4
        if video_path.suffix == '.webm':
            mp4 = video_path.with_suffix('.mp4')
            try:
                subprocess.run(['ffmpeg', '-i', str(video_path), '-c', 'copy', str(mp4), '-y'],
                               capture_output=True, timeout=60, check=True)
                safe_delete(video_path)
                video_path = mp4
            except Exception:
                pass

        bot.edit_message_text("🎵 Musiqa aniqlanmoqda...", message.chat.id, status_msg.message_id)

        audio_path = extract_audio_snippet(video_path)
        if not audio_path:
            bot.edit_message_text("❌ Videodan audio ajratib bo'lmadi.", message.chat.id, status_msg.message_id)
            return

        with open(audio_path, 'rb') as f:
            audio_data = f.read()
        result = recognize_audio(audio_data)

        if not result['found']:
            bot.edit_message_text(
                "❌ Bu videodagi musiqa tanilmadi.\n\nQo'shiq nomini bilsangiz, yozing.",
                message.chat.id, status_msg.message_id
            )
            return

        title = result['title']
        artist = result['artist']
        response = (
            f"✅ *Instagram videosidagi musiqa:*\n\n"
            f"🎵 *Qo\'shiq:* {title}\n"
            f"👤 *Ijrochi:* {artist}\n\n"
            f"🔍 Qidirish uchun: `{artist} {title}`"
        )
        try:
            bot.edit_message_text(response, message.chat.id, status_msg.message_id, parse_mode='Markdown')
        except Exception:
            bot.edit_message_text(response.replace('*', '').replace('`', ''), message.chat.id, status_msg.message_id)

        logger.info(f"✅ Instagram musiqa: {title} - {artist}")

    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        msg = "❌ Video private (shaxsiy)" if "login" in err.lower() or "Private" in err else "❌ Instagram video yuklanmadi."
        if status_msg:
            try:
                bot.edit_message_text(msg, message.chat.id, status_msg.message_id)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Instagram xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text("❌ Xatolik yuz berdi.", message.chat.id, status_msg.message_id)
            except Exception:
                pass
    finally:
        safe_delete(audio_path)
        if video_path:
            threading.Thread(target=lambda: (time.sleep(60), safe_delete(video_path)), daemon=True).start()


@bot.message_handler(func=lambda m: m.text and is_tiktok_url(m.text))
def handle_tiktok(message: types.Message) -> None:
    """TikTok → video yuklab → Shazam → ma'lumot"""
    status_msg = None
    video_path = None
    audio_path = None
    try:
        url = message.text.strip()
        status_msg = bot.reply_to(message, "📱 TikTok videosi tahlil qilinmoqda...")

        video_path = download_video(url, TT_OPTIONS)
        if not video_path or not video_path.exists():
            bot.edit_message_text(
                "❌ TikTok video yuklanmadi.\n\n• Link noto'g'ri bo'lishi mumkin\n• Video private bo'lishi mumkin",
                message.chat.id, status_msg.message_id
            )
            return

        if video_path.suffix == '.webm':
            mp4 = video_path.with_suffix('.mp4')
            try:
                subprocess.run(['ffmpeg', '-i', str(video_path), '-c', 'copy', str(mp4), '-y'],
                               capture_output=True, timeout=60, check=True)
                safe_delete(video_path)
                video_path = mp4
            except Exception:
                pass

        bot.edit_message_text("🎵 Musiqa aniqlanmoqda...", message.chat.id, status_msg.message_id)

        audio_path = extract_audio_snippet(video_path)
        if not audio_path:
            bot.edit_message_text("❌ Videodan audio ajratib bo'lmadi.", message.chat.id, status_msg.message_id)
            return

        with open(audio_path, 'rb') as f:
            audio_data = f.read()
        result = recognize_audio(audio_data)

        if not result['found']:
            bot.edit_message_text(
                "❌ Bu videodagi musiqa tanilmadi.\n\nQo'shiq nomini bilsangiz, yozing.",
                message.chat.id, status_msg.message_id
            )
            return

        title = result['title']
        artist = result['artist']
        response = (
            f"✅ *TikTok videosidagi musiqa:*\n\n"
            f"🎵 *Qo\'shiq:* {title}\n"
            f"👤 *Ijrochi:* {artist}\n\n"
            f"🔍 Qidirish uchun: `{artist} {title}`"
        )
        try:
            bot.edit_message_text(response, message.chat.id, status_msg.message_id, parse_mode='Markdown')
        except Exception:
            bot.edit_message_text(response.replace('*', '').replace('`', ''), message.chat.id, status_msg.message_id)

        logger.info(f"✅ TikTok musiqa: {title} - {artist}")

    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        msg = "❌ Video private (shaxsiy)" if "login" in err.lower() or "Private" in err else "❌ TikTok video yuklanmadi."
        if status_msg:
            try:
                bot.edit_message_text(msg, message.chat.id, status_msg.message_id)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"TikTok xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text("❌ Xatolik yuz berdi.", message.chat.id, status_msg.message_id)
            except Exception:
                pass
    finally:
        safe_delete(audio_path)
        if video_path:
            threading.Thread(target=lambda: (time.sleep(60), safe_delete(video_path)), daemon=True).start()


@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'))
def handle_search(message: types.Message) -> None:
    """Matn → YouTube qidiruv"""
    status_msg = None
    try:
        query = message.text.strip()
        status_msg = bot.reply_to(message, f"🔍 '{query}' qidirilmoqda...")

        with yt_dlp.YoutubeDL(SEARCH_OPTIONS) as ydl:
            info = ydl.extract_info(f"ytsearch50:{query}", download=False)
            songs = info.get('entries', [])

        if not songs:
            bot.edit_message_text(
                "❌ Hech narsa topilmadi.\n\nBoshqa nom bilan qidiring.",
                message.chat.id, status_msg.message_id
            )
            return

        user_sessions[message.chat.id] = {
            'query': query,
            'songs': songs,
            'page': 0,
            'timestamp': datetime.now()
        }

        show_search_results(message.chat.id, 0)
        bot.delete_message(message.chat.id, status_msg.message_id)

    except Exception as e:
        logger.error(f"Qidiruv xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text("❌ Qidiruvda xatolik yuz berdi.", message.chat.id, status_msg.message_id)
            except Exception:
                pass


# ==================== CALLBACK HANDLERS ====================

@bot.callback_query_handler(func=lambda c: c.data.startswith('info_'))
def handle_song_info(call: types.CallbackQuery) -> None:
    """Qo'shiq haqida ma'lumot ko'rsatish (yuklamasdan)"""
    try:
        h = call.data.split('_')[1]
        data_file = TEMP_DIR / f"song_{h}.txt"
        if not data_file.exists():
            bot.answer_callback_query(call.id, "❌ Ma'lumot topilmadi (vaqt o'tgan)", show_alert=True)
            return

        parts = data_file.read_text().strip().split('|', 2)
        url = parts[0]
        title = parts[1] if len(parts) > 1 else 'Noma\'lum'
        num = parts[2] if len(parts) > 2 else ''

        bot.answer_callback_query(call.id, "⏳ Ma'lumot olinmoqda...")

        info = get_youtube_info(url)
        if info:
            t = info.get('title', title)
            artist = info.get('uploader', info.get('channel', 'Noma\'lum'))
            dur = format_duration(info.get('duration'))
            view_count = info.get('view_count', 0)
            views = f"{view_count:,}" if view_count else "—"
            yt_url = info.get('webpage_url', url)

            text = (
                f"🎵 *{t}*\n"
                f"👤 *Ijrochi:* {artist}\n"
                f"⏱ *Davomiyligi:* {dur.strip()}\n"
                f"👁 *Ko\'rishlar:* {views}\n\n"
                f"🔗 [YouTube\'da ochish]({yt_url})"
            )
        else:
            text = f"🎵 *{title}*\n\n🔗 [YouTube\'da ochish]({url})"

        try:
            bot.send_message(call.message.chat.id, text, parse_mode='Markdown', disable_web_page_preview=False)
        except Exception:
            bot.send_message(call.message.chat.id, text.replace('*', '').replace('[', '').replace(']', ''))

    except Exception as e:
        logger.error(f"Song info xatosi: {e}")
        bot.answer_callback_query(call.id, "❌ Xatolik yuz berdi", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data.startswith('page_'))
def handle_page(call: types.CallbackQuery) -> None:
    try:
        page = int(call.data.split('_')[1])
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_search_results(call.message.chat.id, page)
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Page xatosi: {e}")
        bot.answer_callback_query(call.id, "❌ Xatolik", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data == "close_page")
def handle_close(call: types.CallbackQuery) -> None:
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "✅ Yopildi")
        user_sessions.pop(call.message.chat.id, None)
    except Exception as e:
        logger.error(f"Close xatosi: {e}")


@bot.callback_query_handler(func=lambda c: c.data.startswith('nav_'))
def handle_nav(call: types.CallbackQuery) -> None:
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    if call.data == 'nav_home':
        start_command(call.message)
    elif call.data == 'nav_new':
        bot.send_message(call.message.chat.id, "🔍 Qo'shiq yoki ijrochi nomini yozing:")
    bot.answer_callback_query(call.id)


# ==================== SHUTDOWN ====================
def shutdown_handler(signum, frame) -> None:
    logger.info("🛑 Bot to'xtatilmoqda...")
    cleanup_old_files()
    try:
        bot.stop_polling()
    except Exception:
        pass
    logger.info("✅ Bot to'xtatildi")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)


def start_periodic_cleanup() -> None:
    def loop():
        while True:
            time.sleep(CLEANUP_INTERVAL)
            cleanup_old_files()
    threading.Thread(target=loop, daemon=True).start()
    logger.info("🧹 Davriy tozalash yoqildi")


# ==================== MAIN ====================
def main() -> None:
    logger.info("=" * 55)
    logger.info("🎵 TELEGRAM MUSIC SEARCH BOT")
    logger.info("=" * 55)
    logger.info(f"🐍 Python: {sys.version.split()[0]}")
    try:
        logger.info(f"📦 pyTelegramBotAPI: {version('pyTelegramBotAPI')}")
    except Exception:
        pass
    logger.info(f"📁 Temp: {TEMP_DIR.absolute()}")
    logger.info("⚡ Faqat musiqa topadi — yuklamaydi")
    logger.info("=" * 55)

    cleanup_old_files()
    start_periodic_cleanup()

    logger.info("🔄 Polling boshlandi...")
    try:
        bot.infinity_polling(
            skip_pending=True,
            timeout=30,
            long_polling_timeout=30,
            none_stop=True
        )
    except KeyboardInterrupt:
        shutdown_handler(None, None)
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        logger.info("🔄 3 soniyadan keyin qayta uriniladi...")
        time.sleep(3)
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30, none_stop=True)


if __name__ == '__main__':
    main()
