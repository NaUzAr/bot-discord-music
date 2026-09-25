import os
import shutil
import glob
import asyncio
from dataclasses import dataclass
from typing import Optional, List
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

# yt-dlp configurations
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


def format_duration(seconds: Optional[int]) -> str:
    """Format detik ke format MM:SS atau HH:MM:SS."""
    if not seconds:
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
    requester: discord.Member

    @property
    def duration_str(self) -> str:
        return format_duration(self.duration)


class YTDLSource:
    """Helper untuk extract audio stream dari YouTube atau search query."""

    @classmethod
    async def extract_info(cls, query: str, download: bool = False):
        """Extract metadata asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: ytdl.extract_info(query, download=download)
        )

    @classmethod
    async def search_tracks(cls, query: str, max_results: int = 5) -> List[dict]:
        """Mencari beberapa hasil untuk menu /search."""
        search_query = f"ytsearch{max_results}:{query}"
        data = await cls.extract_info(search_query, download=False)
        if not data or "entries" not in data:
            return []
        return [entry for entry in data["entries"] if entry]

    @classmethod
    async def get_song(cls, query: str, requester: discord.Member) -> Optional[Song]:
        """Mendapatkan single song dari URL atau keyword pencarian."""
        is_url = query.startswith("http://") or query.startswith("https://")
        search_query = query if is_url else f"ytsearch1:{query}"

        data = await cls.extract_info(search_query, download=False)
        if not data:
            return None

        # Jika hasil pencarian berupa list entries
        if "entries" in data and data["entries"]:
            data = data["entries"][0]

        return Song(
            title=data.get("title", "Unknown Title"),
            url=data.get("url"),
            webpage_url=data.get("webpage_url", query),
            duration=data.get("duration"),
            thumbnail=data.get("thumbnail"),
            uploader=data.get("uploader", "Unknown Artist"),
            requester=requester,
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


class GuildMusicQueue:
    """Menangani antrean musik per Discord Server."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: List[Song] = []
        self.current: Optional[Song] = None
        self.is_looping: bool = False
        self.force_skip: bool = False
        self.volume: float = 1.0  # 100%
        self.text_channel: Optional[discord.TextChannel] = None

    def add(self, song: Song):
        self.queue.append(song)

    def next_song(self, force_skip: bool = False) -> Optional[Song]:
        # Jika force_skip aktif, lewati current song meskipun looping aktif
        if not (force_skip or self.force_skip) and self.is_looping and self.current:
            return self.current
        self.force_skip = False
        if self.queue:
            self.current = self.queue.pop(0)
            return self.current
        self.current = None
        return None

    def clear(self):
        self.queue.clear()
        self.current = None
        self.force_skip = False


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
