#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Music Bot
Ketma-ketlik:
  1. Matn → YouTube qidiruv → ro'yxat → raqam bosish → MP3
  2. Audio/Voice → Shazam → MP3
  3. Instagram link → video → "Musiqani aniqlash" tugmasi → Shazam → MP3
  4. TikTok link → video → "Musiqani aniqlash" tugmasi → Shazam → MP3
"""

import sys, os, asyncio, tempfile, subprocess, hashlib, re, time, signal, logging, threading
from importlib.metadata import version
from pathlib import Path
from typing import Optional, Dict
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
BOT_TOKEN = "8524502393:AAFw9V2Pg2MqrqhdLBh1Cb1md3CE7u1A0hk"
TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)
MAX_FILE_SIZE = 50 * 1024 * 1024
CLEANUP_INTERVAL = 600
PAGE_SIZE = 10

# ==================== GLOBAL ====================
user_sessions: Dict[int, Dict] = {}

# ==================== BOT INIT ====================
def init_bot() -> telebot.TeleBot:
    try:
        telebot.TeleBot(BOT_TOKEN).remove_webhook()
        logger.info("✅ Webhook o'chirildi")
    except Exception as e:
        logger.warning(f"⚠️ Webhook xatosi: {e}")
    return telebot.TeleBot(BOT_TOKEN, parse_mode=None, threaded=False, skip_pending=True)

bot = init_bot()

# ==================== YT-DLP OPTIONS ====================
SEARCH_OPTIONS = {
    'quiet': True, 'no_warnings': True,
    'extract_flat': True, 'socket_timeout': 20,
}

AUDIO_OPTIONS = {
    'quiet': True, 'no_warnings': True,
    'format': 'bestaudio/best',
    'outtmpl': str(TEMP_DIR / 'dl_%(id)s.%(ext)s'),
    'restrictfilenames': True,
    'socket_timeout': 30, 'retries': 3,
    'nocheckcertificate': True, 'geo_bypass': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
}

IG_OPTIONS = {
    'quiet': True, 'no_warnings': True,
    'format': 'best',
    'outtmpl': str(TEMP_DIR / 'ig_%(id)s.%(ext)s'),
    'socket_timeout': 30, 'retries': 5, 'fragment_retries': 5,
    'nocheckcertificate': True, 'geo_bypass': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/91.0 Mobile Safari/537.36',
        'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.9',
        'Origin': 'https://www.instagram.com',
        'Referer': 'https://www.instagram.com/',
    },
}

TT_OPTIONS = {
    'quiet': True, 'no_warnings': True,
    'format': 'best',
    'outtmpl': str(TEMP_DIR / 'tt_%(id)s.%(ext)s'),
    'socket_timeout': 30, 'retries': 5, 'fragment_retries': 5,
    'nocheckcertificate': True, 'geo_bypass': True,
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
            logger.info(f"🧹 {count} ta fayl o'chirildi")
    except Exception as e:
        logger.error(f"Cleanup xatosi: {e}")

def create_hash(text: str) -> str:
    return hashlib.md5(str(text).encode()).hexdigest()[:12]

def clean_filename(text: str) -> str:
    if not text:
        return "audio"
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', text)
    text = re.sub(r'\s+', '_', text)
    return text[:50].strip('_') or "audio"

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

def delayed_delete(path, delay=120):
    def _del():
        time.sleep(delay)
        safe_delete(path)
    threading.Thread(target=_del, daemon=True).start()

# ==================== SHAZAM ====================
async def _shazam_recognize(path: str) -> Dict:
    try:
        result = await Shazam().recognize(path)
        if result and 'track' in result:
            t = result['track']
            return {'found': True, 'title': t.get('title', 'Noma\'lum'), 'artist': t.get('subtitle', 'Noma\'lum')}
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

# ==================== DOWNLOAD AUDIO (YouTube) ====================
def download_youtube_audio(query_or_url: str, title_hint: str = "") -> Optional[Path]:
    try:
        opts = AUDIO_OPTIONS.copy()
        name = clean_filename(title_hint or query_or_url)
        opts['outtmpl'] = str(TEMP_DIR / f"dl_{name}.%(ext)s")

        # URL yoki qidiruv
        if query_or_url.startswith('http'):
            target = query_or_url
        else:
            target = f"ytsearch1:{query_or_url}"

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([target])

        # MP3 fayl topish
        expected = TEMP_DIR / f"dl_{name}.mp3"
        if expected.exists():
            return expected

        # Fallback: eng yangi mp3
        mp3s = sorted(TEMP_DIR.glob('dl_*.mp3'), key=lambda f: f.stat().st_mtime, reverse=True)
        if mp3s and time.time() - mp3s[0].stat().st_mtime < 120:
            return mp3s[0]

    except Exception as e:
        logger.error(f"Audio yuklash xatosi: {e}")
    return None

# ==================== DOWNLOAD VIDEO ====================
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
        return files[0] if files else None
    except Exception as e:
        logger.error(f"Video yuklash xatosi: {e}")
    return None

def convert_webm_to_mp4(video_path: Path) -> Path:
    if video_path.suffix == '.webm':
        mp4 = video_path.with_suffix('.mp4')
        try:
            subprocess.run(['ffmpeg', '-i', str(video_path), '-c', 'copy', str(mp4), '-y'],
                           capture_output=True, timeout=60, check=True)
            safe_delete(video_path)
            return mp4
        except Exception:
            pass
    return video_path

# ==================== SEND AUDIO HELPER ====================
def send_audio_file(chat_id: int, audio_path: Path, title: str, artist: str = "") -> bool:
    try:
        if audio_path.stat().st_size > MAX_FILE_SIZE:
            bot.send_message(chat_id, "❌ Fayl juda katta (50MB+)")
            return False
        with open(audio_path, 'rb') as f:
            bot.send_audio(
                chat_id, f,
                title=title[:64],
                performer=artist[:64] if artist else None,
                caption=f"🎵 {title}" + (f"\n👤 {artist}" if artist else "")
            )
        return True
    except Exception as e:
        logger.error(f"send_audio xatosi: {e}")
        return False

# ==================== SEARCH RESULT UI ====================
def show_search_results(chat_id: int, page: int = 0) -> None:
    session = user_sessions.get(chat_id)
    if not session:
        bot.send_message(chat_id, "❌ Sessiya tugagan. Yangi qidiruv bering.")
        return

    songs = session['songs']
    query = session['query']
    total = len(songs)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    page_songs = songs[start:start + PAGE_SIZE]

    lines = [
        f"🔍 *{query}*",
        f"📄 Sahifa: {page+1}/{total_pages} | Jami: {total} ta",
        f"",
        f"Raqamga bosing → MP3 yuklanadi ⬇️",
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
            current_row.append(types.InlineKeyboardButton(str(global_idx), callback_data=f"dl_{h}"))
            if len(current_row) == 5:
                button_rows.append(current_row)
                current_row = []

    if current_row:
        button_rows.append(current_row)
    for row in button_rows:
        markup.add(*row)

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

# ==================== HANDLERS ====================

@bot.message_handler(commands=['start', 'help'])
def start_command(message: types.Message) -> None:
    cleanup_old_files()
    text = (
        "👋 *Salom! Men musiqa botman* 🎵\n\n"
        "📋 *Nima qila olaman:*\n\n"
        "1️⃣ Qo'shiq/ijrochi nomini yozing → ro'yxat chiqadi → raqamga bosing → *MP3 yuklanadi*\n\n"
        "2️⃣ Audio yoki voice yuboring → Shazam aniqlaydi → *MP3 yuklanadi*\n\n"
        "3️⃣ Instagram/TikTok link yuboring → video yuboriladi → '🎵 Musiqani aniqlash' tugmasini bosing → *MP3 yuklanadi*\n\n"
        "👨‍💻 Dasturchi: @Rustamov_v1"
    )
    try:
        bot.send_message(message.chat.id, text, parse_mode='Markdown')
    except Exception:
        bot.send_message(message.chat.id, text.replace('*', ''))


@bot.message_handler(content_types=['audio', 'voice'])
def handle_audio_message(message: types.Message) -> None:
    """Audio/Voice → Shazam → MP3"""
    status_msg = None
    audio_path = None
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
        bot.edit_message_text(
            f"✅ Topildi: *{title}* — {artist}\n⏳ MP3 yuklanmoqda...",
            message.chat.id, status_msg.message_id, parse_mode='Markdown'
        )

        audio_path = download_youtube_audio(f"{artist} {title}", f"{artist}_{title}")
        if audio_path and audio_path.exists():
            send_audio_file(message.chat.id, audio_path, title, artist)
            bot.delete_message(message.chat.id, status_msg.message_id)
            logger.info(f"✅ Shazam + yuklandi: {title}")
        else:
            bot.edit_message_text(
                f"✅ Topildi: *{title}* — {artist}\n\n❌ MP3 yuklab bo'lmadi.",
                message.chat.id, status_msg.message_id, parse_mode='Markdown'
            )

    except Exception as e:
        logger.error(f"Audio handler xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text("❌ Xatolik yuz berdi.", message.chat.id, status_msg.message_id)
            except Exception:
                pass
    finally:
        safe_delete(audio_path)


@bot.message_handler(func=lambda m: m.text and is_instagram_url(m.text))
def handle_instagram(message: types.Message) -> None:
    """Instagram → video yuborish + Musiqani aniqlash tugmasi"""
    status_msg = None
    video_path = None
    try:
        url = message.text.strip().split('?')[0]
        status_msg = bot.reply_to(message, "📱 Instagram yuklanmoqda...")

        video_path = download_video(url, IG_OPTIONS)
        if not video_path or not video_path.exists():
            bot.edit_message_text(
                "❌ Instagram video yuklanmadi.\n• Link noto'g'ri bo'lishi mumkin\n• Video private bo'lishi mumkin",
                message.chat.id, status_msg.message_id
            )
            return

        video_path = convert_webm_to_mp4(video_path)

        if video_path.stat().st_size > MAX_FILE_SIZE:
            bot.edit_message_text(
                f"❌ Video juda katta (50MB+). Telegram limiti.",
                message.chat.id, status_msg.message_id
            )
            return

        # Video yuborish + tugma
        h = create_hash(str(video_path))
        (TEMP_DIR / f"{h}.path").write_text(str(video_path))

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🎵 Musiqani aniqlash", callback_data=f"music_{h}"))

        with open(video_path, 'rb') as vf:
            bot.send_video(
                message.chat.id, vf,
                reply_markup=markup,
                caption="📱 Instagram",
                supports_streaming=True,
                timeout=120
            )

        bot.delete_message(message.chat.id, status_msg.message_id)
        logger.info("✅ Instagram video yuborildi")

    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        msg = "❌ Video private (shaxsiy)" if "login" in err.lower() or "Private" in err else "❌ Instagram yuklanmadi."
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
        if video_path:
            delayed_delete(video_path, 120)


@bot.message_handler(func=lambda m: m.text and is_tiktok_url(m.text))
def handle_tiktok(message: types.Message) -> None:
    """TikTok → video yuborish + Musiqani aniqlash tugmasi"""
    status_msg = None
    video_path = None
    try:
        url = message.text.strip()
        status_msg = bot.reply_to(message, "📱 TikTok yuklanmoqda...")

        video_path = download_video(url, TT_OPTIONS)
        if not video_path or not video_path.exists():
            bot.edit_message_text(
                "❌ TikTok video yuklanmadi.\n• Link noto'g'ri bo'lishi mumkin\n• Video private bo'lishi mumkin",
                message.chat.id, status_msg.message_id
            )
            return

        video_path = convert_webm_to_mp4(video_path)

        if video_path.stat().st_size > MAX_FILE_SIZE:
            bot.edit_message_text("❌ Video juda katta (50MB+).", message.chat.id, status_msg.message_id)
            return

        h = create_hash(str(video_path))
        (TEMP_DIR / f"{h}.path").write_text(str(video_path))

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🎵 Musiqani aniqlash", callback_data=f"music_{h}"))

        with open(video_path, 'rb') as vf:
            bot.send_video(
                message.chat.id, vf,
                reply_markup=markup,
                caption="📱 TikTok",
                supports_streaming=True,
                timeout=120
            )

        bot.delete_message(message.chat.id, status_msg.message_id)
        logger.info("✅ TikTok video yuborildi")

    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        msg = "❌ Video private (shaxsiy)" if "login" in err.lower() or "Private" in err else "❌ TikTok yuklanmadi."
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
        if video_path:
            delayed_delete(video_path, 120)


@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'))
def handle_search(message: types.Message) -> None:
    """Matn → YouTube qidiruv → sahifalangan ro'yxat"""
    status_msg = None
    try:
        query = message.text.strip()
        status_msg = bot.reply_to(message, f"🔍 '{query}' qidirilmoqda...")

        with yt_dlp.YoutubeDL(SEARCH_OPTIONS) as ydl:
            info = ydl.extract_info(f"ytsearch50:{query}", download=False)
            songs = info.get('entries', [])

        if not songs:
            bot.edit_message_text("❌ Hech narsa topilmadi.", message.chat.id, status_msg.message_id)
            return

        user_sessions[message.chat.id] = {
            'query': query, 'songs': songs, 'page': 0, 'timestamp': datetime.now()
        }

        show_search_results(message.chat.id, 0)
        bot.delete_message(message.chat.id, status_msg.message_id)

    except Exception as e:
        logger.error(f"Qidiruv xatosi: {e}")
        if status_msg:
            try:
                bot.edit_message_text("❌ Qidiruvda xatolik.", message.chat.id, status_msg.message_id)
            except Exception:
                pass


# ==================== CALLBACK: VIDEO MUSIC ====================
@bot.callback_query_handler(func=lambda c: c.data.startswith('music_'))
def handle_video_music(call: types.CallbackQuery) -> None:
    """Video → audio ajratish → Shazam → MP3"""
    audio_snippet = None
    mp3_path = None
    try:
        h = call.data.split('_')[1]
        bot.answer_callback_query(call.id, "🎵 Musiqa aniqlanmoqda...")

        path_file = TEMP_DIR / f"{h}.path"
        if not path_file.exists():
            bot.send_message(call.message.chat.id, "❌ Video topilmadi (vaqt o'tgan). Linkni qayta yuboring.")
            return

        video_path = path_file.read_text().strip()
        if not Path(video_path).exists():
            bot.send_message(call.message.chat.id, "❌ Video fayl o'chirilgan. Linkni qayta yuboring.")
            return

        status = bot.send_message(call.message.chat.id, "🎵 Musiqa aniqlanmoqda...")

        audio_snippet = extract_audio_snippet(video_path)
        if not audio_snippet:
            bot.edit_message_text("❌ Videodan audio ajratib bo'lmadi.", call.message.chat.id, status.message_id)
            return

        with open(audio_snippet, 'rb') as f:
            audio_data = f.read()
        result = recognize_audio(audio_data)

        if not result['found']:
            bot.edit_message_text(
                "❌ Musiqa tanilmadi.\n\nQo'shiq nomini bilsangiz, yozing.",
                call.message.chat.id, status.message_id
            )
            return

        title = result['title']
        artist = result['artist']
        bot.edit_message_text(
            f"✅ Topildi: *{title}* — {artist}\n⏳ MP3 yuklanmoqda...",
            call.message.chat.id, status.message_id, parse_mode='Markdown'
        )

        mp3_path = download_youtube_audio(f"{artist} {title}", f"{artist}_{title}")
        if mp3_path and mp3_path.exists():
            send_audio_file(call.message.chat.id, mp3_path, title, artist)
            bot.delete_message(call.message.chat.id, status.message_id)
            logger.info(f"✅ Video musiqa yuklandi: {title}")
        else:
            bot.edit_message_text(
                f"✅ Topildi: *{title}* — {artist}\n\n❌ MP3 yuklab bo'lmadi.",
                call.message.chat.id, status.message_id, parse_mode='Markdown'
            )

    except Exception as e:
        logger.error(f"Video music xatosi: {e}")
        bot.send_message(call.message.chat.id, "❌ Xatolik yuz berdi.")
    finally:
        safe_delete(audio_snippet)
        safe_delete(mp3_path)


# ==================== CALLBACK: DOWNLOAD FROM SEARCH ====================
@bot.callback_query_handler(func=lambda c: c.data.startswith('dl_'))
def handle_song_download(call: types.CallbackQuery) -> None:
    """Qidiruv ro'yxatidan MP3 yuklash"""
    mp3_path = None
    try:
        h = call.data.split('_')[1]
        data_file = TEMP_DIR / f"song_{h}.txt"
        if not data_file.exists():
            bot.answer_callback_query(call.id, "❌ Vaqt o'tgan. Qayta qidiring.", show_alert=True)
            return

        parts = data_file.read_text().strip().split('|', 2)
        url = parts[0]
        title = parts[1] if len(parts) > 1 else 'Audio'

        bot.answer_callback_query(call.id, "⏳ MP3 yuklanmoqda...")
        status = bot.send_message(call.message.chat.id, f"⏳ Yuklanmoqda: *{title}*...", parse_mode='Markdown')

        mp3_path = download_youtube_audio(url, title)
        if mp3_path and mp3_path.exists():
            send_audio_file(call.message.chat.id, mp3_path, title)
            bot.delete_message(call.message.chat.id, status.message_id)
            logger.info(f"✅ Yuklandi: {title}")
        else:
            bot.edit_message_text("❌ Yuklab bo'lmadi. Qayta urinib ko'ring.", call.message.chat.id, status.message_id)

        safe_delete(data_file)

    except Exception as e:
        logger.error(f"Download xatosi: {e}")
        bot.answer_callback_query(call.id, "❌ Xatolik yuz berdi", show_alert=True)
    finally:
        safe_delete(mp3_path)


# ==================== CALLBACK: NAVIGATION ====================
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
    except Exception:
        pass

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
    logger.info("🎵 TELEGRAM MUSIC BOT")
    logger.info("=" * 55)
    logger.info(f"🐍 Python: {sys.version.split()[0]}")
    logger.info(f"📁 Temp: {TEMP_DIR.absolute()}")
    logger.info("=" * 55)
    logger.info("Ketma-ketlik:")
    logger.info("  Matn → qidiruv ro'yxati → raqam → MP3")
    logger.info("  Audio/Voice → Shazam → MP3")
    logger.info("  IG/TT link → video → Musiqani aniqlash → MP3")
    logger.info("=" * 55)

    cleanup_old_files()
    start_periodic_cleanup()

    logger.info("🔄 Polling boshlandi...")
    try:
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30, none_stop=True)
    except KeyboardInterrupt:
        shutdown_handler(None, None)
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        time.sleep(3)
        bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30, none_stop=True)

if __name__ == '__main__':
    main()
