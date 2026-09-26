import os
import time
import shutil
import glob
import asyncio
from dataclasses import dataclass
from typing import Optional, List, Any, Set
import logging
import discord
from discord import ui
import yt_dlp

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
YTDL_OPTIONS = {
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
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
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
    before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
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


def format_duration(seconds: Optional[int]) -> str:
    """Format detik ke format MM:SS atau HH:MM:SS."""
    if not seconds or seconds < 0:
        return "Live / Unknown"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


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
    async def extract_info(cls, query: str, download: bool = False):
        """Extract metadata asynchronously."""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                None, lambda: ytdl.extract_info(query, download=download)
            )
        except Exception as e:
            logger.error(f"yt-dlp extract_info error untuk query '{query}': {e}")
            return None

    @classmethod
    async def search_tracks(cls, query: str, max_results: int = 5) -> List[dict]:
        """Mencari beberapa hasil untuk menu /search."""
        search_query = f"ytsearch{max_results}:{query}"
        data = await cls.extract_info(search_query, download=False)
        if not data or "entries" not in data:
            return []
        return [entry for entry in data["entries"] if entry]

    @classmethod
    async def get_song(
        cls,
        query: str,
        requester: Any,
        is_podcast: bool = False,
        ai_context: Optional[dict] = None,
    ) -> Optional[Song]:
        """Mendapatkan single song dari URL atau keyword pencarian."""
        is_url = query.startswith("http://") or query.startswith("https://")
        search_query = query if is_url else f"ytsearch1:{query}"

        try:
            data = await cls.extract_info(search_query, download=False)
            if not data:
                return None

            # Jika hasil pencarian berupa list entries
            if "entries" in data and data["entries"]:
                data = data["entries"][0]

            if not data or not data.get("url"):
                return None

            return Song(
                title=data.get("title", "Unknown Title"),
                url=data.get("url"),
                webpage_url=data.get("webpage_url", query),
                duration=data.get("duration"),
                thumbnail=data.get("thumbnail"),
                uploader=data.get("uploader", "Unknown Artist"),
                requester=requester,
                is_podcast=is_podcast,
                ai_context=ai_context,
            )
        except Exception as e:
            logger.error(f"Error parsing data lagu: {e}")
            return None


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
            if len(self.recent_history) > 6:
                self.recent_history.pop(0)
            if self.current.webpage_url:
                self.played_history.add(self.current.webpage_url)
                # Cap played_history agar tidak memakan memori di mode 24/7
                if len(self.played_history) > 200:
                    # Hapus setengah entry terlama (set tidak ordered, tapi efek rotasinya tetap terasa)
                    evict_count = len(self.played_history) - 100
                    it = iter(self.played_history)
                    for _ in range(evict_count):
                        self.played_history.discard(next(it))

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


class SearchSelectView(ui.View):
    """Dropdown View untuk memilih hasil /search."""

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
                )
            )

        self.select_menu = ui.Select(
            placeholder="🎵 Pilih lagu yang ingin kamu putar...",
            options=options,
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

        self.btn_cancel = ui.Button(label="Batal", emoji="❌", style=discord.ButtonStyle.secondary, row=1)
        self.btn_cancel.callback = self.cancel_callback
        self.add_item(self.btn_cancel)

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

        await interaction.response.edit_message(
            content="❌ Pencarian lagu dibatalkan.",
            embed=None,
            view=self,
        )

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "❌ Hanya yang menjalankan perintah yang bisa memilih!",
                ephemeral=True,
            )
            return

        selected_idx = int(self.select_menu.values[0])
        chosen_track = self.tracks[selected_idx]

        song = Song(
            title=chosen_track.get("title", "Unknown Title"),
            url=chosen_track.get("url"),
            webpage_url=chosen_track.get("webpage_url", ""),
            duration=chosen_track.get("duration"),
            thumbnail=chosen_track.get("thumbnail"),
            uploader=chosen_track.get("uploader", "Unknown Artist"),
            requester=self.requester,
        )

        # Matikan view setelah memilih
        self.stop()
        for item in self.children:
            item.disabled = True

        await self.on_select_callback(interaction, song, self)


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

