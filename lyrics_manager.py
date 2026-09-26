"""
📜 Lyrics Manager
- Pencarian lirik otomatis via LRCLIB API (gratis & cepat)
- Fallback ke Google Gemini AI jika lirik tidak ditemukan di database publik
- Sanitasi judul lagu (menghapus tag noise YouTube: Official Video, MV, dsb.)
- Paginasi embed interaktif dengan tombol [ ◀️ Sebelumnya ] [ Selanjutnya ▶️ ] [ 🗑️ Tutup ]
"""

import os
import re
import json
import asyncio
import logging
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any, List

import discord

logger = logging.getLogger("LyricsManager")

CANDIDATE_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
]


def clean_song_title(title: str) -> str:
    """Membersihkan judul lagu dari noise YouTube metadata."""
    patterns = [
        r"\s*[\(\[](?:official|music|video|audio|lyric|lyrics|visualizer|hd|4k|mv|remastered|live|clip|full).*?[\)\]]",
        r"\s*\|\s*.*$",
        r"\s*-\s*(?:official|music|video|audio|lyric|lyrics).*$",
    ]
    cleaned = title
    for p in patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def chunk_lyrics(text: str, max_chars: int = 1800) -> List[str]:
    """Memecah lirik lagu menjadi halaman-halaman yang pas untuk Discord Embed."""
    if len(text) <= max_chars:
        return [text]

    lines = text.split("\n")
    pages = []
    current_page = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_chars and current_page:
            pages.append("\n".join(current_page).strip())
            current_page = [line]
            current_len = line_len
        else:
            current_page.append(line)
            current_len += line_len

    if current_page:
        pages.append("\n".join(current_page).strip())

    return pages if pages else [text]


async def fetch_lyrics_lrclib(query: str, artist: str = "") -> Optional[Dict[str, Any]]:
    """Mencari lirik lagu menggunakan LRCLIB API gratis."""
    search_q = f"{artist} {query}".strip() if artist else query
    encoded_query = urllib.parse.quote(search_q)
    url = f"https://lrclib.net/api/search?q={encoded_query}"

    def _request():
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "RythmVoiceBot/2.0 (Discord Music Companion)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=6) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            logger.debug(f"LRCLIB request error: {e}")
            return None

    data = await asyncio.to_thread(_request)
    if not data or not isinstance(data, list):
        return None

    for item in data:
        plain = item.get("plainLyrics")
        synced = item.get("syncedLyrics")
        lyrics = plain or (re.sub(r"\[\d+:\d+\.\d+\]\s*", "", synced) if synced else None)
        if lyrics and len(lyrics.strip()) > 30:
            return {
                "title": item.get("trackName") or query,
                "artist": item.get("artistName") or artist or "Unknown Artist",
                "lyrics": lyrics.strip(),
                "source": "LRCLIB",
            }
    return None


async def fetch_lyrics_gemini(query: str, artist: str = "") -> Optional[Dict[str, Any]]:
    """Fallback: Mencari lirik lengkap lagu menggunakan Google Gemini AI."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    prompt = (
        f"Berikan lirik lengkap dari lagu: \"{query}\" (Penyanyi/Artis: {artist or 'sesuai lagu aslinya'}).\n"
        "KETENTUAN PENTING:\n"
        "1. Berikan HANYA teks lirik murni dari awal sampai akhir lagu.\n"
        "2. JANGAN sertakan kata pembuka, pengantar, atau penutup (seperti 'Berikut adalah lirik...').\n"
        "3. Berikan baris per bait secara rapi."
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2048,
        },
    }
    data = json.dumps(payload).encode("utf-8")

    def _sync_gemini():
        for model in CANDIDATE_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    res = json.loads(response.read().decode("utf-8"))
                    text = res.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if text and len(text.strip()) > 50:
                        return text.strip()
            except Exception:
                continue
        return None

    lyrics = await asyncio.to_thread(_sync_gemini)
    if lyrics:
        return {
            "title": clean_song_title(query),
            "artist": artist or "Artis Asli",
            "lyrics": lyrics,
            "source": "Google Gemini AI",
        }
    return None


async def get_lyrics(query: str, artist: str = "") -> Optional[Dict[str, Any]]:
    """Mengambil lirik dengan strategi: LRCLIB API -> Gemini AI Fallback."""
    cleaned = clean_song_title(query)

    # 1. Coba LRCLIB
    result = await fetch_lyrics_lrclib(cleaned, artist)
    if result:
        return result

    # 2. Coba LRCLIB dengan query asli jika berbeda
    if cleaned != query:
        result = await fetch_lyrics_lrclib(query, artist)
        if result:
            return result

    # 3. Fallback Gemini AI
    return await fetch_lyrics_gemini(cleaned, artist)


# ─── Paginasi View Interaktif untuk Lirik ─────────────────────────────
class LyricsPaginationView(discord.ui.View):
    """View tombol interaktif untuk menjelajahi lirik bertingkat halaman."""

    def __init__(
        self,
        pages: List[str],
        title: str,
        artist: str,
        source: str,
        requester: discord.Member,
        thumbnail: Optional[str] = None,
    ):
        super().__init__(timeout=120.0)
        self.pages = pages
        self.title = title
        self.artist = artist
        self.source = source
        self.requester = requester
        self.thumbnail = thumbnail
        self.current_page = 0
        self._update_buttons()

    def _update_buttons(self):
        total = len(self.pages)
        self.btn_prev.disabled = self.current_page == 0
        self.btn_next.disabled = self.current_page >= total - 1
        self.btn_page_num.label = f"Halaman {self.current_page + 1}/{total}"

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="",
            description=(
                f"### 📜 Lirik: {self.title}\n"
                f"👤 **{self.artist}**\n"
                f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n\n"
                f"{self.pages[self.current_page]}"
            ),
            color=0x9B59B6,  # Soft Violet
        )
        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)
        embed.set_footer(
            text=f"Sumber: {self.source}  •  Diminta oleh {self.requester.display_name}  •  🎵 Rythm Voice Companion",
            icon_url=self.requester.display_avatar.url,
        )
        return embed

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary, custom_id="lyrics_prev")
    async def btn_prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="Halaman 1/1", style=discord.ButtonStyle.primary, disabled=True, custom_id="lyrics_indicator")
    async def btn_page_num(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary, custom_id="lyrics_next")
    async def btn_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(emoji="🗑️", label="Tutup", style=discord.ButtonStyle.danger, custom_id="lyrics_close")
    async def btn_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message("❌ Hanya yang meminta lirik yang bisa menutup tampilan ini!", ephemeral=True)
            return

        self.stop()
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.edit_message(content="*Tampilan lirik ditutup.*", embed=None, view=None)
