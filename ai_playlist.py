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


def _clean_track_title(raw_title: str) -> str:
    """Membersihkan judul YouTube dari tag video umum agar AI fokus ke esensi lagu."""
    cleaned = re.sub(
        r"[\(\[\{].*?(?:official|video|audio|lyric|lirik|clip|mv|visualizer|remaster|hd|4k|hq).*?[\)\]\}]",
        "",
        raw_title,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[\(\[\{].*?[\)\]\}]", "", cleaned)  # Bersihkan kurung sisa jika ada
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|")
    return cleaned or raw_title


async def recommend_next_song(recent_songs: List[Any]) -> Optional[Dict[str, Any]]:
    """
    Menganalisis riwayat lagu terakhir dengan standar Music Director profesional.
    Menghasilkan rekomendasi utama berkelas dan 2 lagu cadangan (alternatives).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not recent_songs:
        return None

    recent_list_text = ""
    for i, s in enumerate(recent_songs[-5:], start=1):
        raw_title = getattr(s, "title", "") or "Unknown"
        clean_title = _clean_track_title(raw_title)
        uploader = getattr(s, "uploader", "") or "Unknown"
        recent_list_text += f'{i}. "{clean_title}" (Judul asli: {raw_title}, Channel: {uploader})\n'

    prompt = (
        "Kamu adalah seorang AI Music Director & DJ Kurator Kelas Dunia dengan selera musik (taste) yang sangat tinggi.\n"
        "Di sebuah room voice Discord, pengguna baru saja mendengarkan lagu-lagu berikut:\n\n"
        f"{recent_list_text}\n"
        "PETUNJUK ANALISIS & KURASI (HARUS DIPATUHI):\n"
        "1. IDENTIFIKASI ARTIS ASLI:\n"
        "   Data di atas diambil dari YouTube. Nama channel/uploader sering kali adalah nama label rekaman (contoh: 'HITS Records', 'Musica Studios', 'Vevo', 'Aquarius') "
        "   atau nama agregator. Kenali SIAPA PENYANYI SEBENARNYA dari judul video tersebut.\n\n"
        "2. KONSISTENSI SELERA & SUB-KULTUR MUSIK (TASTE CONSISTENCY):\n"
        "   - Pahami 'circle' atau 'sub-kultur' musik yang sedang diputar. Jangan cuma merekomendasikan lagu pop mainstream yang pasaran atau overplayed.\n"
        "   - Contoh penyesuaian circle:\n"
        "     * Jika user mendengarkan Indie Folk / Poetic Pop Indonesia (Tulus, Sal Priadi, Bernadya, Nadin Amizah, Kunto Aji, Pamungkas, Hindia, Feby Putri, Juicy Luicy, Coldiac) "
        "       -> Rekomendasikan lagu dari circle yang sefrekuensi (berestetika puitis, instrumen organik/akustik, vokal intim). JANGAN lompat ke pop drama komersil yang terlalu beda estetika.\n"
        "     * Jika user mendengarkan Pop Ballad Vokal Kuat (Mahalini, Tiara Andini, Lyodra, Keisya, Judika) -> Rekomendasikan lagu vokal ballad emosional selevel.\n"
        "     * Jika user mendengarkan J-Pop / City Pop / Anime -> Jaga nuansa City Pop / J-Pop Jepang tetap terjaga.\n"
        "     * Jika user mendengarkan Lofi / R&B Chill / Jazz -> Pertahankan groove santai dan instrumen hangatnya.\n"
        "     * Jika user mendengarkan Rock / Pop-Punk / Metal -> Pertahankan energi distorsi dan ketukannya.\n\n"
        "3. KONSISTENSI BAHASA & KULTUR (LANGUAGE CONTINUITY):\n"
        "   - Jika lagu-lagu sebelumnya lagu Indonesia, prioritaskan 100% lagu berikutnya tetap lagu Indonesia.\n"
        "   - Jika lagu Barat/English, rekomendasikan lagu Barat. Jangan mencampur bahasa tanpa alasan musikal yang kuat.\n\n"
        "4. KONSISTENSI TEMPO & MOOD (ENERGY MATCHING):\n"
        "   - Jaga agar transisi lagu tidak mengagetkan pendengar. Jangan melompat drastis dari lagu akustik syahdu lambat ke lagu party bertempo cepat.\n\n"
        "5. PILIHAN UTAMA & 2 ALTERNATIF CADANGAN:\n"
        "   - Berikan 1 rekomendasi terbaik (title, artist, theme, reason).\n"
        "   - Berikan 2 alternatif lagu lain yang sama-sama cocok jika lagu utama sudah pernah diputar.\n"
        "   - Semua lagu HARUS LAGU NYATA dan TERKENAL yang pasti ada di YouTube.\n\n"
        "FORMAT OUTPUT (HANYA JSON MURNI TANPA MARKDOWN ATAU PENJELASAN LAIN):\n"
        "{\n"
        '  "title": "Judul Lagu Rekomendasi Utama",\n'
        '  "artist": "Nama Penyanyi/Artis Asli",\n'
        '  "theme": "Label Subgenre & Nuansa (misal: Indonesian Poetic Indie-Pop / Warm Acoustic Ballad)",\n'
        '  "reason": "1 kalimat ringkas menjelaskan kenapa lagu ini sempurna menyambung estetika lagu sebelumnya",\n'
        '  "alternatives": [\n'
        '    {"title": "Judul Lagu Alternatif 1", "artist": "Artis Alternatif 1"},\n'
        '    {"title": "Judul Lagu Alternatif 2", "artist": "Artis Alternatif 2"}\n'
        '  ]\n'
        "}"
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": 600,
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
