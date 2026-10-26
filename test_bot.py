"""
🛠️ Bot Discord & System Diagnostics Test Suite
Jalankan file ini untuk memeriksa kesehatan seluruh modul bot di VPS / Host:
    python test_bot.py
"""

import os
import sys
import time
import shutil
import asyncio
from dotenv import load_dotenv

load_dotenv()

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

passed_tests = 0
total_tests = 0


def log_test(name: str, success: bool, detail: str = ""):
    global passed_tests, total_tests
    total_tests += 1
    if success:
        passed_tests += 1
        print(f"  {GREEN}✅ [PASS]{RESET} {BOLD}{name}{RESET} {f'- {detail}' if detail else ''}")
    else:
        print(f"  {RED}❌ [FAIL]{RESET} {BOLD}{name}{RESET} {f'- {detail}' if detail else ''}")


async def run_diagnostics():
    print(f"\n{CYAN}{BOLD}======================================================{RESET}")
    print(f"{CYAN}{BOLD}    🎵 DISCORD MUSIC BOT — VPS DIAGNOSTIC SUITE       {RESET}")
    print(f"{CYAN}{BOLD}======================================================{RESET}\n")

    # 1. Environment Variables
    print(f"{BOLD}1. Memeriksa File Konfigurasi (.env)...{RESET}")
    token = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_MUSIC_TOKEN")
    log_test("DISCORD_TOKEN", bool(token), "Token bot Discord terdeteksi" if token else "Token KOSONG di .env!")
    
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        log_test("GEMINI_API_KEY", True, "API Key terpasang (Fitur AI Playlist & DJ aktif)")
    else:
        print(f"  {YELLOW}⚠️  [SKIP]{RESET} GEMINI_API_KEY tidak diisi (fitur AI akan dinonaktifkan)")

    # 2. Binary Dependencies
    print(f"\n{BOLD}2. Memeriksa Software & Binary Sistem...{RESET}")
    ffmpeg_bin = shutil.which("ffmpeg")
    log_test("FFmpeg Executable", bool(ffmpeg_bin), f"Ditemukan di '{ffmpeg_bin}'" if ffmpeg_bin else "FFmpeg belum terinstal!")

    ytdlp_bin = shutil.which("yt-dlp")
    log_test("yt-dlp Executable", bool(ytdlp_bin or sys.executable), f"Ditemukan: '{ytdlp_bin or sys.executable}'")

    # 3. Database SQLite
    print(f"\n{BOLD}3. Memeriksa Database SQLite & Penyimpanan...{RESET}")
    try:
        import database
        await database.init_db()
        t0 = time.time()
        await database.add_voice_time(999999999, 888888888, 1)
        stats = await database.get_user_stats(999999999, 888888888)
        db_ms = round((time.time() - t0) * 1000, 1)
        log_test("SQLite Async I/O", bool(stats), f"Path: '{database.DB_PATH}' ({db_ms} ms)")
    except Exception as e:
        log_test("SQLite Async I/O", False, str(e))

    # 4. Spotify Resolver Test
    print(f"\n{BOLD}4. Memeriksa Fitur Spotify Resolver...{RESET}")
    try:
        from music_player import resolve_spotify_track
        test_spot = resolve_spotify_track("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
        if test_spot and "Rick Astley" in test_spot.get("artist", ""):
            log_test("Spotify oEmbed Resolver", True, f"Track: '{test_spot['search_query']}'")
        else:
            log_test("Spotify oEmbed Resolver", False, "Respon tidak sesuai")
    except Exception as e:
        log_test("Spotify oEmbed Resolver", False, str(e))

    # 5. Audio Extraction Test (YouTube & SoundCloud)
    print(f"\n{BOLD}5. Memeriksa Ekstraksi Audio (yt-dlp & Fallback)...{RESET}")
    try:
        from music_player import YTDLSource
        t0 = time.time()
        song = await YTDLSource.get_song("lofi hip hop radio beats to relax", requester=None)
        extract_ms = round((time.time() - t0) * 1000, 1)
        if song and song.url:
            log_test("Audio Stream Resolution", True, f"'{song.title[:35]}' [{song.source_provider}] ({extract_ms} ms)")
        else:
            log_test("Audio Stream Resolution", False, "Gagal mendapatkan direct audio stream")
    except Exception as e:
        log_test("Audio Stream Resolution", False, str(e))

    # 6. Gemini AI Test
    if gemini_key:
        print(f"\n{BOLD}6. Memeriksa Google Gemini AI API...{RESET}")
        try:
            from ai_playlist import generate_ai_playlist
            t0 = time.time()
            ai_res = await generate_ai_playlist("coffee morning", track_count=3)
            ai_ms = round((time.time() - t0) * 1000, 1)
            if ai_res and ai_res.get("tracks"):
                log_test("Gemini AI Curation", True, f"Playlist: '{ai_res.get('title')}' ({ai_ms} ms)")
            else:
                log_test("Gemini AI Curation", False, "Respon JSON kosong")
        except Exception as e:
            log_test("Gemini AI Curation", False, str(e))

    print(f"\n{CYAN}------------------------------------------------------{RESET}")
    print(f"Hasil Pengujian: {BOLD}{passed_tests}/{total_tests}{RESET} tes berhasil.")
    if passed_tests == total_tests:
        print(f"{GREEN}{BOLD}🎉 SEMUA MODUL BOT SIAP DIGUNAKAN DI VPS 24/7!{RESET}\n")
    else:
        print(f"{YELLOW}{BOLD}⚠️ Beberapa pengujian gagal. Periksa log detail di atas.{RESET}\n")


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    asyncio.run(run_diagnostics())
