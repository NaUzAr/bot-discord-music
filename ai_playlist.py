import os
import json
import re
import asyncio
import logging
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List

logger = logging.getLogger("AIPlaylist")

# Model Gemini yang cepat, stabil, dan mendukung structured JSON
CANDIDATE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
]


async def generate_ai_playlist(user_prompt: str, track_count: int = 5) -> Optional[Dict[str, Any]]:
    """
    Menggunakan Google Gemini AI untuk menginterpretasikan prompt/mood user
    dan menghasilkan playlist terkurasi berisi lagu-lagu nyata.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY tidak ditemukan di .env")
        return None

    track_count = max(3, min(10, track_count))

    system_instruction = (
        "Kamu adalah seorang AI Music Curator & DJ profesional kelas dunia. "
        "Tugasmu adalah menganalisis mood, skenario, bahasa, genre, atau suasana yang diinginkan pengguna, "
        "lalu meracik sebuah playlist lagu NYATA dan TERKENAL yang paling pas.\n"
        "PENTING:\n"
        "1. Lagu-lagu yang kamu sarankan HARUS lagu nyata yang bisa ditemukan di YouTube atau Spotify (sertakan nama artis yang tepat).\n"
        "2. Jika pengguna meminta nuansa lokal (Indonesia, Jawa, Sunda, K-Pop, J-Pop, dll), sesuaikan penyanyi/artisnya dengan sangat tepat.\n"
        "3. Berikan output HANYA berupa JSON murni dengan format persis seperti ini (tanpa teks penjelasan lain di luar JSON):\n"
        "{\n"
        '  "title": "Nama Playlist Kreatif & Menarik",\n'
        '  "vibe": "1-2 kalimat ringkas menggambarkan suasana musik ini",\n'
        '  "tracks": [\n'
        '    {"title": "Judul Lagu", "artist": "Nama Artis/Penyanyi"}\n'
        "  ]\n"
        "}"
    )

    full_prompt = (
        f"{system_instruction}\n\n"
        f"Permintaan Pengguna: \"{user_prompt}\"\n"
        f"Jumlah lagu yang diminta: {track_count} lagu."
    )

    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1000,
            "responseMimeType": "application/json",
        },
    }

    def _sync_request():
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

        # Coba model kandidat secara berurutan jika terjadi 503 atau 404
        for model in CANDIDATE_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=12) as response:
                    raw_text = response.read().decode("utf-8")
                    logger.info(f"✅ AI Playlist berhasil dihasilkan oleh model: {model}")
                    return raw_text
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                logger.warning(f"Model {model} HTTP {e.code}: {err_body[:100]}... Mencoba model berikutnya.")
                continue
            except Exception as e:
                logger.warning(f"Model {model} error: {e}... Mencoba model berikutnya.")
                continue

        return None

    raw_response = await asyncio.to_thread(_sync_request)
    if not raw_response:
        logger.error("Semua kandidat model Gemini gagal merespons.")
        return None

    try:
        data = json.loads(raw_response)
        candidates = data.get("candidates", [])
        if not candidates:
            return None

        text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        # Bersihkan format markdown jika ada
        text_content = re.sub(r"^```(?:json)?\s*", "", text_content.strip())
        text_content = re.sub(r"\s*```$", "", text_content.strip())

        parsed_json = json.loads(text_content)
        return parsed_json
    except Exception as e:
        logger.error(f"Gagal mem-parsing respons JSON dari Gemini: {e}")
        return None


async def recommend_next_song(recent_songs: List[Any]) -> Optional[Dict[str, str]]:
    """
    Menganalisis 2-5 lagu terakhir yang baru saja diputar di voice room,
    memahami tema, mood, dan genre-nya, lalu merekomendasikan 1 lagu berikutnya yang paling pas.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not recent_songs:
        return None

    recent_list_text = ""
    for i, s in enumerate(recent_songs[-5:], start=1):
        artist = getattr(s, "uploader", "") or "Unknown"
        title = getattr(s, "title", "") or "Unknown"
        recent_list_text += f'{i}. "{title}" oleh {artist}\n'

    prompt = (
        "Kamu adalah seorang AI DJ dan Kurator Musik berpengalaman tinggi. "
        "Di sebuah room voice Discord, pengguna baru saja mendengarkan beberapa lagu berikut secara berturut-turut:\n\n"
        f"{recent_list_text}\n"
        "TUGAS KAMU:\n"
        "1. Analisis genre, tema lirik, nuansa mood (apakah ceria, galau, fokus, santai, dsb), dan tempo dari lagu-lagu di atas.\n"
        "2. Rekomendasikan TEPAT 1 lagu NYATA dan TERKENAL berikutnya yang PALING COCOK untuk menyambung alur dan suasana musik ini.\n"
        "3. Lagu yang kamu rekomendasikan TIDAK BOLEH sama dengan lagu-lagu yang sudah ada di daftar di atas.\n"
        "4. Berikan output HANYA berupa JSON murni dengan format:\n"
        "{\n"
        '  "title": "Judul Lagu Rekomendasi",\n'
        '  "artist": "Nama Artis/Penyanyi",\n'
        '  "theme": "Label Genre/Tema (misal: Pop Galau Indonesia / Lofi Study / EDM Workout)",\n'
        '  "reason": "1 kalimat ringkas kenapa lagu ini cocok melanjutkan suasana lagu sebelumnya"\n'
        "}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": 400,
            "responseMimeType": "application/json",
        },
    }

    def _sync_request():
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

        for model in CANDIDATE_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    return response.read().decode("utf-8")
            except Exception:
                continue
        return None

    raw_response = await asyncio.to_thread(_sync_request)
    if not raw_response:
        return None

    try:
        data = json.loads(raw_response)
        candidates = data.get("candidates", [])
        if not candidates:
            return None

        text_content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        text_content = re.sub(r"^```(?:json)?\s*", "", text_content.strip())
        text_content = re.sub(r"\s*```$", "", text_content.strip())

        parsed_json = json.loads(text_content)
        if parsed_json.get("title") and parsed_json.get("artist"):
            return parsed_json
        return None
    except Exception as e:
        logger.error(f"Gagal parse rekomendasi lagu dari AI: {e}")
        return None
