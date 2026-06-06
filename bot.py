import os
import re
import asyncio
import tempfile
import requests
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import yt_dlp
from shazamio import Shazam

# ─────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8524502393:AAFvZmPd2VtjSPrw6TLQdJ9iojHYMlPsj_E")
# ─────────────────────────────────────────────


def is_instagram_url(url: str) -> bool:
    return bool(re.search(r"instagram\.com/(p|reel|tv|stories)/", url))


async def download_instagram_video(url: str) -> tuple[str | None, str | None]:
    """Download video, return (file_path, error_message)."""
    tmp_dir = tempfile.mkdtemp()
    output_template = os.path.join(tmp_dir, "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": output_template,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "cookiefile": "cookies.txt" if Path("cookies.txt").exists() else None,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # yt-dlp may change extension
            for ext in ["mp4", "mkv", "webm", "mov"]:
                candidate = Path(filename).with_suffix(f".{ext}")
                if candidate.exists():
                    return str(candidate), None
            # fallback: find any file in tmp_dir
            files = list(Path(tmp_dir).iterdir())
            if files:
                return str(files[0]), None
            return None, "Video fayl topilmadi."
    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        if "Private" in err or "Login" in err:
            return None, "❌ Bu yopiq (private) akkaunt yoki login talab etiladi."
        return None, f"❌ Yuklab bo'lmadi: {err[:200]}"
    except Exception as e:
        return None, f"❌ Xato: {str(e)[:200]}"


async def identify_music(video_path: str) -> dict | None:
    """Use Shazam to identify music in the video."""
    shazam = Shazam()
    try:
        result = await shazam.recognize(video_path)
        if result and "track" in result:
            track = result["track"]
            return {
                "title": track.get("title", "Noma'lum"),
                "artist": track.get("subtitle", "Noma'lum"),
                "genre": track.get("genres", {}).get("primary", ""),
                "shazam_url": track.get("url", ""),
            }
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────────────────────
#  HANDLERS
# ──────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 Salom! Men <b>Instagram Video Bot</b>man.\n\n"
        "📥 <b>Nima qila olaman:</b>\n"
        "• Instagram post / reel / TV videolarini yuklab beraman\n"
        "• Videodagi musiqani Shazam orqali aniqlayman 🎵\n\n"
        "📌 <b>Ishlatish:</b> Menga Instagram havolasini yuboring.\n\n"
        "⚠️ <i>Faqat ochiq (public) akkauntlar ishlaydi.</i>"
    )
    await update.message.reply_html(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "ℹ️ <b>Yordam</b>\n\n"
        "1️⃣ Instagram postining havolasini nusxalab yuboring.\n"
        "   Masalan: <code>https://www.instagram.com/reel/ABC123/</code>\n\n"
        "2️⃣ Men videoni yuklab, sizga yuboraman.\n\n"
        "3️⃣ Keyin videodagi musiqani ham aniqlayman.\n\n"
        "🔒 Yopiq akkauntlar ishlamaydi."
    )
    await update.message.reply_html(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()

    if not is_instagram_url(text):
        await update.message.reply_text(
            "❓ Instagram havolasi ko'rinmadi.\n"
            "Iltimos, to'g'ri havola yuboring.\n"
            "Masalan: https://www.instagram.com/reel/ABC123/"
        )
        return

    status_msg = await update.message.reply_text("⏳ Video yuklanmoqda, kuting...")

    file_path, error = await download_instagram_video(text)

    if error:
        await status_msg.edit_text(error)
        return

    # Send video
    await status_msg.edit_text("📤 Video yuborilmoqda...")
    try:
        with open(file_path, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption="✅ Video tayyor!",
                supports_streaming=True,
            )
    except Exception as e:
        await status_msg.edit_text(f"❌ Video yuborishda xato: {str(e)[:200]}")
        return

    # Identify music
    await status_msg.edit_text("🎵 Musiqa aniqlanmoqda...")
    music = await identify_music(file_path)

    if music:
        music_text = (
            f"🎵 <b>Musiqa topildi!</b>\n\n"
            f"🎤 <b>Ijrochi:</b> {music['artist']}\n"
            f"🎼 <b>Qo'shiq:</b> {music['title']}\n"
        )
        if music["genre"]:
            music_text += f"🎸 <b>Janr:</b> {music['genre']}\n"
        if music["shazam_url"]:
            keyboard = [[InlineKeyboardButton("🔗 Shazamda ko'rish", url=music["shazam_url"])]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await status_msg.edit_text(music_text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await status_msg.edit_text(music_text, parse_mode="HTML")
    else:
        await status_msg.edit_text("🎵 Musiqa aniqlanmadi (instrumental yoki shovqin bo'lishi mumkin).")

    # Cleanup
    try:
        Path(file_path).unlink(missing_ok=True)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
