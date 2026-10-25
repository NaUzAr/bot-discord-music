import os
import re
import json
import urllib.request
import time
import shutil
import glob
import asyncio
import subprocess
import shlex
from dataclasses import dataclass
from typing import Optional, List, Any, Set
import logging
from dotenv import load_dotenv
import discord
from discord import ui
from discord.opus import Encoder as OpusEncoder
import yt_dlp

load_dotenv()
logger = logging.getLogger("MusicPlayer")


def get_ffmpeg_executable() -> str:
    """Mencari path binary ffmpeg di sistem secara otomatis."""
    which_path = shutil.which("ffmpeg")
    if which_path:
        return which_path

    # Cari di winget packages jika PATH belum refresh
    local_app_data = os.getenv("LOCALAPPDATA", "")
    if local_app_data:
        winget_pattern = os.path.join(
            local_app_data,
            "Microsoft",
            "WinGet",
            "Packages",
            "*FFmpeg*",
            "**",
            "ffmpeg.exe",
        )
        matches = glob.glob(winget_pattern, recursive=True)
        if matches:
            return matches[0]

    return "ffmpeg"


FFMPEG_EXECUTABLE = get_ffmpeg_executable()

# yt-dlp configurations — Kualitas Tertinggi (Opus 48kHz)
# Setup cookies jika tersedia (untuk mengatasi blokir bot YouTube di hosting seperti Render)
COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")
DATA_COOKIE_FILE = os.path.join(os.path.dirname(__file__), "data", "cookies.txt")
if not os.path.exists(COOKIE_FILE) and os.path.exists(DATA_COOKIE_FILE):
    COOKIE_FILE = DATA_COOKIE_FILE

_cookies_b64 = os.getenv("YOUTUBE_COOKIES_BASE64")
if _cookies_b64:
    try:
        import base64
        _decoded = base64.b64decode(_cookies_b64.strip())
        _current_size = os.path.getsize(COOKIE_FILE) if os.path.exists(COOKIE_FILE) else 0
        if not os.path.exists(COOKIE_FILE) or _current_size != len(_decoded):
            with open(COOKIE_FILE, "wb") as f:
                f.write(_decoded)
            logger.info("[yt-dlp] Berhasil membuat/memperbarui cookies.txt dari YOUTUBE_COOKIES_BASE64")
    except Exception as e:
        logger.warning(f"[yt-dlp] Gagal decode YOUTUBE_COOKIES_BASE64: {e}")

YTDL_BASE_OPTIONS = {
    "format": "bestaudio[acodec=opus]/bestaudio[ext=webm]/bestaudio/best",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "js_runtimes": {"node": {}, "deno": {}, "bun": {}},
    "remote_components": ["ejs:github"],
}

YTDL_OPTIONS = dict(YTDL_BASE_OPTIONS)

# Opsi fallback YouTube tanpa cookies menggunakan mobile/embedded client
YTDL_OPTIONS_NOCOOKIE = {
    **YTDL_BASE_OPTIONS,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web_embedded", "mweb"],
            "player_skip": ["webpage", "configs"],
        }
    },
}

# Opsi khusus fallback SoundCloud
YTDL_OPTIONS_SC = {
    **YTDL_BASE_OPTIONS,
    "default_search": "scsearch",
}

if os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 0:
    YTDL_OPTIONS["cookiefile"] = COOKIE_FILE
    logger.info(f"[yt-dlp] Menggunakan file cookies: {COOKIE_FILE}")
else:
    YTDL_OPTIONS["extractor_args"] = {
        "youtube": {
            "player_client": ["android", "web_embedded", "mweb"],
            "player_skip": ["webpage", "configs"],
        }
    }

FFMPEG_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
FFMPEG_BEFORE_OPTIONS = f'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -headers "User-Agent: {FFMPEG_USER_AGENT}\r\n"'

FFMPEG_OPTIONS = {
    "before_options": FFMPEG_BEFORE_OPTIONS,
    "options": "-vn -af aresample=48000",
}

# ─── Audio Filter DSP Presets ─────────────────────────────────────────
AUDIO_FILTERS = {
    "reset": {
        "name": "Normal (Tanpa Filter)",
        "emoji": "🔄",
        "desc": "Kualitas suara asli tanpa modifikasi",
        "af": "",
    },
    "bassboost": {
        "name": "Bass Boost",
        "emoji": "🔊",
        "desc": "Bass menggelegar dan tebal (Phonk, EDM, Hip-Hop)",
        "af": "bass=g=10:f=110:w=0.6",
    },
    "nightcore": {
        "name": "Nightcore",
        "emoji": "⚡",
        "desc": "Tempo lebih cepat 25% dan nada/pitch vokal lebih tinggi",
        "af": "asetrate=48000*1.25,aresample=48000",
    },
    "slowed": {
        "name": "Slowed + Reverb",
        "emoji": "🌌",
        "desc": "Tempo lambat santai dengan reverb luas (Vaporwave / Chill)",
        "af": "asetrate=48000*0.85,aresample=48000,aecho=0.8:0.88:60:0.4",
    },
    "8d": {
        "name": "8D Audio (360°)",
        "emoji": "🎧",
        "desc": "Efek audio memutar 360° kiri-kanan (wajib earphone)",
        "af": "apulsator=hz=0.125",
    },
    "lofi": {
        "name": "Lo-Fi Vintage",
        "emoji": "📻",
        "desc": "Suara hangat retro seperti kaset pita zaman dulu",
        "af": "lowpass=f=3000,highpass=f=200",
    },
    "karaoke": {
        "name": "Karaoke / Vocal Cut",
        "emoji": "🎤",
        "desc": "Meredam vokal tengah agar instrumen lebih dominan",
        "af": "stereotools=mlev=0.015625",
    },
    "treble": {
        "name": "Treble Boost",
        "emoji": "✨",
        "desc": "Menjernihkan suara instrumen tinggi dan petikan",
        "af": "treble=g=8:f=4000:w=0.5",
    },
}


def get_ffmpeg_options(filter_key: Optional[str] = None, start_time: Optional[float] = None) -> dict:
    """Menghasilkan dictionary opsi FFmpeg lengkap dengan filter audio & seek position."""
    before_options = FFMPEG_BEFORE_OPTIONS
    if start_time and start_time > 0:
        before_options += f" -ss {int(start_time)}"

    af_parts = []
    if filter_key and filter_key in AUDIO_FILTERS and AUDIO_FILTERS[filter_key]["af"]:
        af_parts.append(AUDIO_FILTERS[filter_key]["af"])
    else:
        af_parts.append("aresample=48000")

    af_combined = ",".join(af_parts)
    options = f'-vn -af "{af_combined}"'

    return {
        "before_options": before_options,
        "options": options,
    }

# ─── Preset Genre & Mood untuk Auto-Playlist 24/7 ─────────────────────
GENRE_PLAYLISTS = {
    "lofi": {
        "name": "Lofi & Chill Study",
        "emoji": "☕",
        "desc": "Lofi hip hop beats santai untuk belajar, kerja, & rileks",
        "queries": [
            "lofi hip hop radio beats to relax study to",
            "chill lofi beats to sleep relax",
            "aesthetic lofi mix peaceful music",
            "japanese lofi hip hop mix chill",
        ],
    },
    "indonesia": {
        "name": "Pop Hits Indonesia",
        "emoji": "🇮🇩",
        "desc": "Lagu pop Indonesia terbaru, viral, dan hits terpopuler",
        "queries": [
            "lagu pop indonesia terbaru hits viral",
            "top hits lagu indonesia terbaik playlist",
            "lagu hits indonesia terpopuler saat ini",
            "lagu indie indonesia santai populer",
        ],
    },
    "pop": {
        "name": "Global Pop & Billboard",
        "emoji": "🌎",
        "desc": "Top hits Billboard & lagu barat populer terfavorit",
        "queries": [
            "top billboard hot 100 pop songs hits",
            "popular english songs global pop playlist",
            "latest top pop hits songs viral",
        ],
    },
    "edm": {
        "name": "EDM & Gaming",
        "emoji": "⚡",
        "desc": "Lagu EDM, Synthwave, Phonk, dan NCS penambah semangat",
        "queries": [
            "ncs best gaming edm songs mix",
            "phonk drift gaming mix songs",
            "popular dance edm festival hits mix",
            "synthwave cyberpunk electronic music",
        ],
    },
    "jazz": {
        "name": "Jazz & Cafe Vibes",
        "emoji": "🎷",
        "desc": "Jazz akustik santai & Bossa Nova suasana kedai kopi",
        "queries": [
            "relaxing jazz coffee shop bossa nova music",
            "cafe jazz music sweet background",
            "smooth night jazz instrumental chill",
        ],
    },
    "galau": {
        "name": "Galau & Akustik Sendu",
        "emoji": "🌧️",
        "desc": "Lagu sendu, akustik petikan gitar, dan nostalgia galau",
        "queries": [
            "lagu galau indonesia akustik santai sedih",
            "akustik lagu galau indonesia terbaik",
            "lagu sedih menyentuh hati playlist indonesia",
        ],
    },
    "rock": {
        "name": "Rock & Alternative",
        "emoji": "🎸",
        "desc": "Classic rock, alternative, & modern rock anthems",
        "queries": [
            "classic rock greatest hits playlist",
            "best modern alternative rock songs",
            "legendary rock anthems mix classic",
        ],
    },
    "anime": {
        "name": "Anime & J-Pop",
        "emoji": "🌸",
        "desc": "Lagu tema anime opening/ending, J-Pop, dan City Pop",
        "queries": [
            "popular anime opening songs full mix",
            "aesthetic japanese city pop mix 80s 90s",
            "best j-pop songs playlist hits",
        ],
    },
    "piano": {
        "name": "Piano & Focus Instrumental",
        "emoji": "🎹",
        "desc": "Melodi piano tenang tanpa vokal untuk fokus maksimal",
        "queries": [
            "peaceful piano instrumental study focus",
            "calm piano relaxing background music",
            "beautiful emotional piano solo peaceful",
        ],
    },
    "ambient": {
        "name": "Sleep & Deep Ambient",
        "emoji": "💤",
        "desc": "Suara hujan & ambient tenang untuk tidur lelap / meditasi",
        "queries": [
            "relaxing rain sounds deep sleep ambient",
            "deep focus ambient atmosphere study",
            "calm nature ambient relaxation soundscape",
        ],
    },
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
ytdl_nocookie = yt_dlp.YoutubeDL(YTDL_OPTIONS_NOCOOKIE)
ytdl_sc = yt_dlp.YoutubeDL(YTDL_OPTIONS_SC)


def clean_search_query(raw_title: str, author: str = "") -> str:
    """Membersihkan judul lagu dari kata/tag sampah (Official Video, MV, dsb) agar pencarian fallback akurat."""
    if not raw_title:
        return ""
    junk_patterns = [
        r"\((?:official|music|video|audio|lyric|lyrics|hd|4k|remastered|clip|visualizer|mv|feat|ft)[^)]*\)",
        r"\[(?:official|music|video|audio|lyric|lyrics|hd|4k|remastered|clip|visualizer|mv|feat|ft)[^\]]*\]",
        r"\|.*$",
        r"#\S+",
    ]
    cleaned = raw_title
    for p in junk_patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:|")

    # Hanya tambahkan author jika judul BELUM ada pemisah " - " (Artis - Judul)
    # dan author bukan nama channel / label rekaman umum
    if author and " - " not in cleaned:
        author_clean = re.sub(r" - Topic$", "", author, flags=re.IGNORECASE).strip()
        is_label = bool(re.search(r"\b(records|record|vevo|entertainment|music|official|channel|productions|media|tv)\b", author_clean, re.IGNORECASE))
        if not is_label and author_clean and author_clean.lower() not in cleaned.lower():
            cleaned = f"{author_clean} - {cleaned}"

    return cleaned or raw_title


def format_duration(seconds: Optional[Any]) -> str:
    """Format detik ke format MM:SS atau HH:MM:SS (aman untuk tipe int, float, maupun string)."""
    if seconds is None:
        return "Live / Unknown"
    try:
        sec_int = int(float(seconds))
        if sec_int < 0:
            return "Live / Unknown"
        m, s = divmod(sec_int, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
    except (ValueError, TypeError):
        return "Live / Unknown"


@dataclass
class Song:
    title: str
    url: str  # direct audio stream URL
    webpage_url: str  # YouTube page URL
    duration: Optional[int]
    thumbnail: Optional[str]
    uploader: str
    requester: Any
    is_podcast: bool = False
    ai_context: Optional[dict] = None

    @property
    def duration_str(self) -> str:
        return format_duration(self.duration)


class YTDLSource:
    """Helper untuk extract audio stream dari YouTube atau search query."""

    @classmethod
    async def extract_info(cls, query: str, download: bool = False, custom_ytdl: Optional[Any] = None):
        """Extract metadata asynchronously."""
        loop = asyncio.get_event_loop()
        instance = custom_ytdl or ytdl
        try:
            return await loop.run_in_executor(
                None, lambda: instance.extract_info(query, download=download)
            )
        except Exception as e:
            logger.error(f"yt-dlp extract_info error untuk query '{query}': {e}")
            return None

    @classmethod
    async def search_tracks(cls, query: str, max_results: int = 5) -> List[dict]:
        """Mencari beberapa hasil untuk menu /search (dengan fallback otomatis ke SoundCloud)."""
        search_query = f"ytsearch{max_results}:{query}"
        data = await cls.extract_info(search_query, download=False, custom_ytdl=ytdl)
        entries = []
        if data and "entries" in data:
            entries = [entry for entry in data["entries"] if entry]

        # Retry YouTube tanpa cookies jika gagal dan ada cookiefile
        if not entries and "cookiefile" in YTDL_OPTIONS:
            logger.info(f"[Search Retry] YouTube dengan cookies kosong, mencoba tanpa cookies: {query}")
            data_nc = await cls.extract_info(search_query, download=False, custom_ytdl=ytdl_nocookie)
            if data_nc and "entries" in data_nc:
                entries = [entry for entry in data_nc["entries"] if entry]

        # Fallback ke SoundCloud jika YouTube diblokir atau tidak ada hasil
        if not entries:
            cleaned_query = clean_search_query(query)
            logger.info(f"[Search Fallback] YouTube gagal, mencari di SoundCloud: {cleaned_query}")
            sc_targets = [f"scsearch{max_results * 2}:{cleaned_query}"]
            if cleaned_query != query:
                sc_targets.append(f"scsearch{max_results * 2}:{query}")

            for sc_query in sc_targets:
                try:
                    sc_data = await cls.extract_info(sc_query, download=False, custom_ytdl=ytdl_sc)
                    if sc_data and "entries" in sc_data:
                        # Filter out klip preview 30 detik (SoundCloud preview)
                        for entry in sc_data["entries"]:
                            if not entry:
                                continue
                            dur = entry.get("duration")
                            url = entry.get("url", "")
                            if (dur is not None and dur <= 45) or ("/preview/" in url):
                                continue
                            if entry not in entries:
                                entries.append(entry)
                            if len(entries) >= max_results:
                                break
                    if len(entries) >= max_results:
                        break
                except Exception as sc_err:
                    logger.error(f"[Search Fallback] Error SoundCloud query '{sc_query}': {sc_err}")

            if not entries and sc_data and "entries" in sc_data:
                entries = [entry for entry in sc_data["entries"] if entry][:max_results]

        return entries

    @classmethod
    async def get_song(
        cls,
        query: str,
        requester: Any,
        is_podcast: bool = False,
        ai_context: Optional[dict] = None,
    ) -> Optional[Song]:
        """Mendapatkan single song dari URL atau keyword pencarian.

        Mendukung retry multi-client YouTube dan fallback otomatis ke SoundCloud jika YouTube diblokir di cloud/VPS.
        """
        is_url = query.startswith("http://") or query.startswith("https://")
        search_query = query if is_url else f"ytsearch1:{query}"

        fallback_title = None
        fallback_thumbnail = None
        fallback_uploader = None

        # 1. Coba ambil dari YouTube via primary ytdl
        data = None
        try:
            data = await cls.extract_info(search_query, download=False, custom_ytdl=ytdl)
            if data and "entries" in data and data["entries"]:
                data = data["entries"][0]
        except Exception as e:
            logger.warning(f"Ekstraksi awal gagal: {e}")
            data = None

        # 2. Jika gagal dan cookies sedang dipakai, coba lagi tanpa cookies via mobile/embedded client
        if (not data or not data.get("url")) and "cookiefile" in YTDL_OPTIONS:
            try:
                logger.info(f"[Retry YouTube] Mencoba ekstraksi tanpa cookies (mobile client): {query}")
                data = await cls.extract_info(search_query, download=False, custom_ytdl=ytdl_nocookie)
                if data and "entries" in data and data["entries"]:
                    data = data["entries"][0]
            except Exception as e:
                logger.warning(f"Ekstraksi retry nocookie gagal: {e}")
                data = None

        # 3. Fallback otomatis ke SoundCloud jika YouTube diblokir bot / IP VPS
        if not data or not data.get("url"):
            logger.info(f"[Fallback] Mengambil alternatif audio via SoundCloud untuk: {query}")
            sc_targets = []
            fallback_thumbnail = None
            fallback_uploader = None
            fallback_title = None

            # Jika query berupa URL YouTube, ambil judul & metadata via public oEmbed API (bebas blokir)
            if "youtube.com" in query or "youtu.be" in query:
                try:
                    oembed_url = f"https://www.youtube.com/oembed?url={query}&format=json"
                    req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
                    loop = asyncio.get_event_loop()

                    def _fetch_oembed():
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            return json.loads(resp.read().decode())

                    meta = await loop.run_in_executor(None, _fetch_oembed)
                    if meta and meta.get("title"):
                        raw_title = meta.get("title", "")
                        author = meta.get("author_name", "")
                        fallback_title = raw_title
                        fallback_thumbnail = meta.get("thumbnail_url")
                        fallback_uploader = author

                        # 1. Judul bersih murni (misal "COLORCODE - Sway")
                        clean_base = clean_search_query(raw_title, "")
                        if clean_base:
                            sc_targets.append(clean_base)
                            # Variasi tanpa tanda hubung (-) misal "COLORCODE Sway" (Sangat akurat di SoundCloud)
                            no_dash = clean_base.replace(" - ", " ").replace("-", " ")
                            no_dash = re.sub(r"\s+", " ", no_dash).strip()
                            if no_dash and no_dash != clean_base and no_dash not in sc_targets:
                                sc_targets.append(no_dash)

                        # 2. Judul dengan author jika relevan
                        cleaned_with_author = clean_search_query(raw_title, author)
                        if cleaned_with_author and cleaned_with_author not in sc_targets:
                            sc_targets.append(cleaned_with_author)

                        # 3. Raw title asli sebagai opsi cadangan terakhir
                        if raw_title and raw_title not in sc_targets:
                            sc_targets.append(raw_title)

                        logger.info(f"[Fallback] oEmbed resolve: '{raw_title}' -> Targets: {sc_targets}")
                except Exception as oe_err:
                    logger.debug(f"Gagal oEmbed fallback: {oe_err}")
            else:
                clean_base = clean_search_query(query, "")
                if clean_base:
                    sc_targets.append(clean_base)
                    no_dash = clean_base.replace(" - ", " ").replace("-", " ")
                    no_dash = re.sub(r"\s+", " ", no_dash).strip()
                    if no_dash and no_dash != clean_base and no_dash not in sc_targets:
                        sc_targets.append(no_dash)
                if query not in sc_targets:
                    sc_targets.append(query)

            # Coba cari di SoundCloud dengan target yang telah disanitasi
            for target in sc_targets:
                sc_search = (
                    target
                    if (target.startswith("http://") or target.startswith("https://"))
                    else f"scsearch5:{target}"
                )
                try:
                    sc_data = await cls.extract_info(sc_search, download=False, custom_ytdl=ytdl_sc)
                    if sc_data and "entries" in sc_data and sc_data["entries"]:
                        valid_entries = [e for e in sc_data["entries"] if e and e.get("url")]
                        full_entry = None
                        for sc_entry in valid_entries:
                            dur = sc_entry.get("duration")
                            url = sc_entry.get("url", "")
                            # Abaikan klip preview berdurasi <= 45 detik atau bertanda /preview/
                            if (dur is not None and dur <= 45) or ("/preview/" in url):
                                continue
                            full_entry = sc_entry
                            break

                        # Jika ada versi full, pilih versi full. Jika semua preview, fallback ke entri valid pertama
                        data = full_entry or (valid_entries[0] if valid_entries else None)
                        if data and data.get("url"):
                            chosen_dur = data.get("duration")
                            logger.info(f"[Fallback] Berhasil menemukan di SoundCloud ({int(chosen_dur) if chosen_dur else '?'}s): '{data.get('title')}'")
                            break
                except Exception as sc_err:
                    logger.error(f"[Fallback] SoundCloud extract error untuk '{target}': {sc_err}")

        if not data or not data.get("url"):
            return None

        raw_dur = data.get("duration")
        dur_int = None
        if raw_dur is not None:
            try:
                dur_int = int(float(raw_dur))
            except (ValueError, TypeError):
                dur_int = None

        # Prioritaskan judul, thumbnail, dan uploader asli dari YouTube jika query adalah link YouTube
        final_title = (fallback_title if (is_url and fallback_title) else None) or data.get("title", "Unknown Title")
        final_thumbnail = (fallback_thumbnail if (is_url and fallback_thumbnail) else None) or data.get("thumbnail")
        final_uploader = (fallback_uploader if (is_url and fallback_uploader) else None) or data.get("uploader") or "Unknown Artist"

        return Song(
            title=final_title,
            url=data.get("url"),
            webpage_url=query if is_url else data.get("webpage_url", query),
            duration=dur_int,
            thumbnail=final_thumbnail,
            uploader=final_uploader,
            requester=requester,
            is_podcast=is_podcast,
            ai_context=ai_context,
        )


class InterruptableVolumeTransformer(discord.PCMVolumeTransformer):
    """
    Volume Transformer dengan fitur audio ducking & chime injection.
    Ketika chime/bel aktif, volume musik diturunkan (ducked) dan suara bel di-mix di atasnya.
    """

    def __init__(self, original: discord.AudioSource, volume: float = 1.0):
        super().__init__(original, volume)
        self.overlay_source: Optional[discord.AudioSource] = None

    def inject_overlay(self, overlay: discord.AudioSource):
        """Memasukkan audio overlay (misal bel pomodoro)."""
        if self.overlay_source:
            try:
                self.overlay_source.cleanup()
            except Exception:
                pass
        self.overlay_source = overlay

    def read(self) -> bytes:
        data = self.original.read()
        if not data:
            return b""

        import audioop

        # Terapkan volume dasar musik
        music_chunk = audioop.mul(data, 2, self._volume)

        if self.overlay_source:
            overlay_chunk = self.overlay_source.read()
            if overlay_chunk:
                # Duck musik hingga 25% agar bel notifikasi terdengar jelas
                ducked_music = audioop.mul(music_chunk, 2, 0.25)
                min_len = min(len(ducked_music), len(overlay_chunk))
                mixed = audioop.add(ducked_music[:min_len], overlay_chunk[:min_len], 2)
                if len(ducked_music) > min_len:
                    mixed += ducked_music[min_len:]
                return mixed
            else:
                # Overlay selesai
                try:
                    self.overlay_source.cleanup()
                except Exception:
                    pass
                self.overlay_source = None

        return music_chunk

    def cleanup(self) -> None:
        if self.overlay_source:
            try:
                self.overlay_source.cleanup()
            except Exception:
                pass
            self.overlay_source = None
        super().cleanup()


class PipedFFmpegAudio(discord.AudioSource):
    """
    AudioSource yang mem-pipe bitstream audio langsung dari subprocess yt-dlp ke FFmpeg stdin (pipe:0).
    Ini sepenuhnya menghilangkan error 403 Forbidden dari YouTube googlevideo CDN
    karena yt-dlp menangani semua cookie, session tokens, PO tokens, dan challenge secara lokal.
    """

    def __init__(
        self,
        source: str,
        *,
        cookie_file: Optional[str] = None,
        ffmpeg_executable: str = "ffmpeg",
        before_options: Optional[str] = None,
        options: Optional[str] = None,
    ):
        self.source = source
        self.ffmpeg_proc: Optional[subprocess.Popen] = None
        self.ytdlp_proc: Optional[subprocess.Popen] = None

        ytdlp_cmd = [
            "yt-dlp",
            "-f", "bestaudio[acodec=opus]/bestaudio[ext=webm]/bestaudio/best",
            "--no-warnings",
            "--quiet",
            "--no-playlist",
            "-o", "-",
        ]
        if cookie_file and os.path.exists(cookie_file) and os.path.getsize(cookie_file) > 0:
            ytdlp_cmd.extend(["--cookies", cookie_file])

        ytdlp_cmd.append(source)

        # 1. Jalankan proses yt-dlp
        self.ytdlp_proc = subprocess.Popen(
            ytdlp_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        # 2. Susun parameter FFmpeg
        args = [ffmpeg_executable]
        if before_options:
            args.extend(shlex.split(before_options))
        args.extend(["-i", "pipe:0"])
        args.extend([
            "-f", "s16le",
            "-ar", "48000",
            "-ac", "2",
            "-loglevel", "warning",
        ])
        if options:
            args.extend(shlex.split(options))
        args.append("pipe:1")

        # 3. Jalankan FFmpeg dengan input dari stdout yt-dlp
        self.ffmpeg_proc = subprocess.Popen(
            args,
            stdin=self.ytdlp_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        # Tutup file handle stdout yt-dlp di parent agar child FFmpeg menjadi satu-satunya pemilik read pipe
        if self.ytdlp_proc.stdout:
            self.ytdlp_proc.stdout.close()

    def read(self) -> bytes:
        if not self.ffmpeg_proc or not self.ffmpeg_proc.stdout:
            return b""
        try:
            ret = self.ffmpeg_proc.stdout.read(OpusEncoder.FRAME_SIZE)
            if len(ret) != OpusEncoder.FRAME_SIZE:
                return b""
            return ret
        except Exception:
            return b""

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        if self.ffmpeg_proc:
            try:
                self.ffmpeg_proc.kill()
            except Exception:
                pass
            self.ffmpeg_proc = None
        if self.ytdlp_proc:
            try:
                self.ytdlp_proc.kill()
            except Exception:
                pass
            self.ytdlp_proc = None

    def __del__(self):
        self.cleanup()


def is_youtube_source(song: Song) -> bool:
    """Mengecek apakah audio berasal dari YouTube yang membutuhkan piping yt-dlp."""
    target = f"{song.webpage_url or ''} {song.url or ''}".lower()
    return any(k in target for k in ["youtube.com", "youtu.be", "googlevideo.com"])


def create_audio_source(
    song: Song,
    filter_key: Optional[str] = None,
    start_time: Optional[float] = None,
) -> discord.AudioSource:
    """Membuat AudioSource yang aman dan optimal (piped yt-dlp untuk YouTube, direct untuk SoundCloud)."""
    ffmpeg_opts = get_ffmpeg_options(filter_key, start_time=start_time)

    if is_youtube_source(song):
        target_url = (
            song.webpage_url
            if (
                song.webpage_url
                and ("youtube.com" in song.webpage_url or "youtu.be" in song.webpage_url)
            )
            else (song.url or song.webpage_url)
        )
        before_opts = f"-ss {int(start_time)}" if (start_time and start_time > 0) else ""
        cookie_to_use = (
            COOKIE_FILE
            if (os.path.exists(COOKIE_FILE) and os.path.getsize(COOKIE_FILE) > 0)
            else None
        )
        return PipedFFmpegAudio(
            target_url,
            cookie_file=cookie_to_use,
            ffmpeg_executable=FFMPEG_EXECUTABLE,
            before_options=before_opts,
            options=ffmpeg_opts.get("options", ""),
        )
    else:
        return discord.FFmpegPCMAudio(
            song.url,
            executable=FFMPEG_EXECUTABLE,
            **ffmpeg_opts,
        )


class GuildMusicQueue:
    """Menangani antrean musik per Discord Server."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: List[Song] = []
        self.current: Optional[Song] = None
        self.is_looping: bool = False
        self.force_skip: bool = False
        self.autoplay: bool = True  # Mode AutoPlay 24/7 (default: aktif)
        self.autoplay_genre: Optional[str] = None  # Key dari GENRE_PLAYLISTS jika radio genre aktif
        self.manual_stopped: bool = False
        self.is_loading_playlist: bool = False
        self.last_played: Optional[Song] = None
        self.recent_history: List[Song] = []  # Menyimpan hingga 6 lagu terakhir untuk analisis tema AI AutoPlay
        self.played_history: Set[str] = set()
        self.volume: float = 1.0  # 100%
        self.text_channel: Optional[discord.TextChannel] = None
        # Audio filter state & timing tracking
        self.audio_filter: Optional[str] = None
        self.song_start_time: float = 0.0
        self.pause_start_time: float = 0.0
        self.paused_duration: float = 0.0
        self.is_reloading: bool = False

    def get_elapsed_seconds(self) -> float:
        """Menghitung detik pemutaran lagu saat ini."""
        if not self.song_start_time:
            return 0.0
        if self.pause_start_time > 0:
            current_paused = time.time() - self.pause_start_time
            return max(0.0, time.time() - self.song_start_time - self.paused_duration - current_paused)
        return max(0.0, time.time() - self.song_start_time - self.paused_duration)

    def add(self, song: Song):
        self.queue.append(song)
        self.manual_stopped = False

    def next_song(self, force_skip: bool = False) -> Optional[Song]:
        # Jika force_skip aktif, lewati current song meskipun looping aktif
        if not (force_skip or self.force_skip) and self.is_looping and self.current:
            return self.current
        self.force_skip = False

        if self.current:
            self.last_played = self.current
            self.recent_history.append(self.current)
            if len(self.recent_history) > 20:
                self.recent_history.pop(0)
            if self.current.webpage_url:
                self.played_history.add(self.current.webpage_url)
                # Cap played_history agar tidak memakan memori di mode 24/7
                if len(self.played_history) > 200:
                    # Hapus setengah entry terlama (set tidak ordered, tapi efek rotasinya tetap terasa)
                    evict_count = len(self.played_history) - 100
                    evict_items = list(self.played_history)[:evict_count]
                    for item in evict_items:
                        self.played_history.discard(item)

        if self.queue:
            self.current = self.queue.pop(0)
            return self.current
        self.current = None
        return None

    def clear(self):
        self.queue.clear()
        self.current = None
        self.force_skip = False
        self.song_start_time = 0.0
        self.pause_start_time = 0.0
        self.paused_duration = 0.0
        self.is_reloading = False
        self.is_loading_playlist = False


class SearchSelectView(ui.View):
    """View interaktif (Tombol Angka 1-5 & Dropdown Menu) untuk memilih hasil /search."""

    def __init__(
        self,
        tracks: List[dict],
        requester: discord.Member,
        on_select_callback,
    ):
        super().__init__(timeout=60.0)
        self.tracks = tracks
        self.requester = requester
        self.on_select_callback = on_select_callback
        self.message: Optional[discord.Message] = None

        number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

        # 1. Baris Tombol Angka (Row 0)
        for i in range(min(5, len(tracks))):
            btn = ui.Button(
                emoji=number_emojis[i],
                style=discord.ButtonStyle.primary,
                row=0,
                custom_id=f"search_btn_{i}",
            )
            btn.callback = self._create_button_callback(i)
            self.add_item(btn)

        # 2. Menu Dropdown Pilihan Lagu (Row 1)
        options = []
        for i, track in enumerate(tracks[:5]):
            title = track.get("title", f"Track {i+1}")[:95]
            uploader = track.get("uploader", "Unknown")[:40]
            dur = format_duration(track.get("duration"))
            desc = f"{uploader} • {dur}"[:100]

            options.append(
                discord.SelectOption(
                    label=f"{i+1}. {title}",
                    description=desc,
                    value=str(i),
                    emoji=number_emojis[i],
                )
            )

        self.select_menu = ui.Select(
            placeholder="🎵 Atau pilih dari daftar dropdown...",
            options=options,
            row=1,
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

        # 3. Tombol Batal (Row 2)
        self.btn_cancel = ui.Button(
            label="Batal",
            emoji="❌",
            style=discord.ButtonStyle.secondary,
            row=2,
            custom_id="search_btn_cancel",
        )
        self.btn_cancel.callback = self.cancel_callback
        self.add_item(self.btn_cancel)

    def _create_button_callback(self, index: int):
        async def button_callback(interaction: discord.Interaction):
            await self.select_track_by_index(interaction, index)
        return button_callback

    async def select_callback(self, interaction: discord.Interaction):
        selected_idx = int(self.select_menu.values[0])
        await self.select_track_by_index(interaction, selected_idx)

    async def select_track_by_index(self, interaction: discord.Interaction, selected_idx: int):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "❌ Hanya yang menjalankan perintah yang bisa memilih lagu!",
                ephemeral=True,
            )
            return

        user = interaction.user
        if not user.voice or not user.voice.channel:
            await interaction.response.send_message(
                "❌ Kamu harus berada di voice channel untuk memilih lagu!",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if guild and guild.voice_client and guild.voice_client.channel:
            if user.voice.channel.id != guild.voice_client.channel.id:
                await interaction.response.send_message(
                    f"❌ Kamu harus berada di voice channel yang sama dengan bot (**🔊 {guild.voice_client.channel.name}**) untuk memilih lagu!",
                    ephemeral=True,
                )
                return

        if selected_idx < 0 or selected_idx >= len(self.tracks):
            return

        # Defer agar interaksi tidak timeout jika proses ekstraksi stream memakan waktu
        await interaction.response.defer()

        # Matikan view setelah memilih
        self.stop()
        for item in self.children:
            item.disabled = True

        chosen_track = self.tracks[selected_idx]
        raw_stream_url = chosen_track.get("url")
        webpage_url = chosen_track.get("webpage_url") or raw_stream_url or ""

        # Cek apakah URL sudah berupa direct stream yang playable
        is_direct_audio = (
            raw_stream_url
            and (raw_stream_url.startswith("http://") or raw_stream_url.startswith("https://"))
            and ("youtube.com" not in raw_stream_url and "youtu.be" not in raw_stream_url)
            and (".googlevideo.com" not in raw_stream_url)
        )

        if not is_direct_audio:
            target_q = webpage_url or chosen_track.get("title", "")
            resolved = await YTDLSource.get_song(target_q, requester=self.requester)
            if resolved:
                song = resolved
            else:
                song = Song(
                    title=chosen_track.get("title", "Unknown Title"),
                    url=raw_stream_url or webpage_url,
                    webpage_url=webpage_url,
                    duration=chosen_track.get("duration"),
                    thumbnail=chosen_track.get("thumbnail"),
                    uploader=chosen_track.get("uploader", "Unknown Artist"),
                    requester=self.requester,
                )
        else:
            song = Song(
                title=chosen_track.get("title", "Unknown Title"),
                url=raw_stream_url,
                webpage_url=webpage_url,
                duration=chosen_track.get("duration"),
                thumbnail=chosen_track.get("thumbnail"),
                uploader=chosen_track.get("uploader", "Unknown Artist"),
                requester=self.requester,
            )

        await self.on_select_callback(interaction, song, self)

    async def cancel_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "❌ Hanya yang menjalankan perintah yang bisa membatalkan!",
                ephemeral=True,
            )
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        embed = discord.Embed(
            title="❌ Pencarian Dibatalkan",
            description="Pencarian lagu telah dibatalkan.",
            color=discord.Color.dark_gray(),
        )
        if interaction.response.is_done():
            await interaction.edit_original_response(content=None, embed=embed, view=None)
        else:
            await interaction.response.edit_message(content=None, embed=embed, view=None)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class GenreSelectView(ui.View):
    """Dropdown View interaktif untuk memilih Genre / Mood Auto-Playlist."""

    def __init__(self, requester: discord.Member, on_select_callback):
        super().__init__(timeout=60.0)
        self.requester = requester
        self.on_select_callback = on_select_callback

        options = []
        for key, g in GENRE_PLAYLISTS.items():
            options.append(
                discord.SelectOption(
                    label=g["name"][:100],
                    value=key,
                    emoji=g["emoji"],
                    description=g["desc"][:100],
                )
            )

        # Opsi untuk matikan mode genre
        options.append(
            discord.SelectOption(
                label="Nonaktifkan Radio Genre (AutoPlay Bebas)",
                value="off",
                emoji="⏹️",
                description="Kembali ke rekomendasi berdasarkan lagu terakhir",
            )
        )

        self.select_menu = ui.Select(
            placeholder="📻 Pilih Genre Lagu / Mood Radio...",
            options=options,
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

        self.btn_close = ui.Button(label="Tutup", emoji="❌", style=discord.ButtonStyle.secondary, row=1)
        self.btn_close.callback = self.close_callback
        self.add_item(self.btn_close)

    async def close_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "❌ Hanya yang menjalankan perintah yang bisa menutup menu!",
                ephemeral=True,
            )
            return

        self.stop()
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="Menu radio ditutup.",
            embed=None,
            view=self,
        )

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "❌ Hanya yang menjalankan perintah yang bisa memilih genre!",
                ephemeral=True,
            )
            return

        genre_key = self.select_menu.values[0]
        self.stop()
        for item in self.children:
            item.disabled = True

        await self.on_select_callback(interaction, genre_key, self)


class FilterSelectView(ui.View):
    """Dropdown View interaktif untuk memilih Audio Filter."""

    def __init__(self, requester: discord.Member, current_filter: Optional[str], on_select_callback):
        super().__init__(timeout=60.0)
        self.requester = requester
        self.on_select_callback = on_select_callback

        options = []
        for key, f in AUDIO_FILTERS.items():
            is_default = (key == current_filter) or (key == "reset" and not current_filter)
            options.append(
                discord.SelectOption(
                    label=f["name"][:100],
                    value=key,
                    emoji=f["emoji"],
                    description=f["desc"][:100],
                    default=is_default,
                )
            )

        self.select_menu = ui.Select(
            placeholder="🎛️ Pilih Efek Audio Filter...",
            options=options,
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

        self.btn_close = ui.Button(label="Tutup", emoji="❌", style=discord.ButtonStyle.secondary, row=1)
        self.btn_close.callback = self.close_callback
        self.add_item(self.btn_close)

    async def close_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message("❌ Hanya yang membuka menu yang bisa menutupnya!", ephemeral=True)
            return
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Menu filter ditutup.", embed=None, view=self)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message("❌ Hanya yang membuka menu yang bisa memilih filter!", ephemeral=True)
            return

        filter_key = self.select_menu.values[0]
        self.stop()
        for item in self.children:
            item.disabled = True
        await self.on_select_callback(interaction, filter_key, self)

