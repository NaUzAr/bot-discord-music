"""
podcast_manager.py
Modul untuk menjelajah, mencari, dan memutar episode podcast trending/populer di Discord.
Mendukung pencarian topik bebas serta kurasi episode podcast trending minggu ini.
"""

import asyncio
import logging
import re
from typing import Dict, Any, List, Optional, Callable
import discord
import yt_dlp

logger = logging.getLogger("PodcastManager")

# Kategori podcast yang dikurasi dengan pencarian trending live minggu ini
PODCAST_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "trending": {
        "name": "Trending Minggu Ini",
        "emoji": "🔥",
        "desc": "Podcast obrolan & talkshow paling ramai dibicarakan minggu ini",
        "query": "podcast indonesia terbaru trending minggu ini",
    },
    "komedi": {
        "name": "Komedi & Talkshow Santai",
        "emoji": "😂",
        "desc": "PWK (Praz Teguh), Agak Laen, Vindes, Podkesmas, Curhat Bang",
        "query": "podcast talkshow komedi indonesia terbaru",
    },
    "horor": {
        "name": "Kisah Horor & Misteri",
        "emoji": "👻",
        "desc": "Lentera Malam, RJL 5, Do You See What I See, Nadia Omara",
        "query": "podcast horor indonesia terbaru minggu ini",
    },
    "bisnis": {
        "name": "Bisnis, Karir & Edukasi",
        "emoji": "🧠",
        "desc": "Endgame Gita Wirjawan, Raymond Chin, Thirty Days of Lunch",
        "query": "podcast bisnis edukasi self improvement indonesia terbaru",
    },
    "global": {
        "name": "Global & Science (English)",
        "emoji": "🌍",
        "desc": "The Joe Rogan Experience, Huberman Lab, The Diary Of A CEO",
        "query": "popular podcast episodes this week full",
    },
}


def format_duration_podcast(seconds: Optional[int]) -> str:
    """Format durasi detik ke bentuk '1j 24m' atau '48m 10s'."""
    if not seconds or seconds <= 0:
        return "Live / Unknown"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}j {m}m" if s == 0 else f"{h}j {m}m {s}s"
    return f"{m}m {s}s"


async def search_podcast_episodes(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """
    Mencari episode podcast di YouTube secara cepat menggunakan extract_flat.
    Memfilter hasil agar memprioritaskan episode penuh (durasi > 8 menit).
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    loop = asyncio.get_event_loop()

    def _sync_search():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Ambil hingga 8 kandidat lalu filter durasi
            search_query = f"ytsearch8:{query}"
            return ydl.extract_info(search_query, download=False)

    try:
        data = await loop.run_in_executor(None, _sync_search)
        if not data or "entries" not in data:
            return []

        entries = data.get("entries", [])
        episodes = []

        for entry in entries:
            if not entry:
                continue

            duration = entry.get("duration") or 0
            # Filter agar durasi minimal 8 menit (480s) untuk menghindari cuplikan pendek / shorts
            if duration > 0 and duration < 480 and len(entries) > max_results:
                continue

            title = entry.get("title", "Episode Podcast")
            uploader = entry.get("uploader", "Podcast Channel")
            url = entry.get("url")
            if not url or not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={entry.get('id')}"

            thumbnails = entry.get("thumbnails", [])
            thumb_url = thumbnails[-1].get("url") if thumbnails else None

            episodes.append({
                "title": title,
                "uploader": uploader,
                "duration": duration,
                "duration_str": format_duration_podcast(duration),
                "url": url,
                "thumbnail": thumb_url,
            })

            if len(episodes) >= max_results:
                break

        # Jika filter durasi terlalu ketat dan hasil kosong, gunakan entries awal
        if not episodes and entries:
            for entry in entries[:max_results]:
                dur = entry.get("duration") or 0
                episodes.append({
                    "title": entry.get("title", "Episode Podcast"),
                    "uploader": entry.get("uploader", "Podcast Channel"),
                    "duration": dur,
                    "duration_str": format_duration_podcast(dur),
                    "url": entry.get("url") if entry.get("url", "").startswith("http") else f"https://www.youtube.com/watch?v={entry.get('id')}",
                    "thumbnail": entry.get("thumbnails", [{}])[-1].get("url") if entry.get("thumbnails") else None,
                })

        return episodes
    except Exception as e:
        logger.error(f"Gagal mencari episode podcast untuk '{query}': {e}")
        return []


class PodcastSelectMenu(discord.ui.Select):
    """Dropdown menu berisi 5 episode podcast yang ditemukan."""

    def __init__(self, episodes: List[Dict[str, Any]], on_selected_callback: Callable):
        self.episodes = episodes
        self.on_selected_callback = on_selected_callback

        options = []
        for i, ep in enumerate(episodes[:5]):
            title_truncated = ep["title"][:95] + ("..." if len(ep["title"]) > 95 else "")
            uploader_truncated = ep["uploader"][:35]
            desc = f"👤 {uploader_truncated} · ⏱️ {ep['duration_str']}"
            options.append(
                discord.SelectOption(
                    label=title_truncated,
                    description=desc[:100],
                    emoji="🎙️",
                    value=str(i),
                )
            )

        super().__init__(
            placeholder="🎙️ Pilih episode podcast untuk diputar...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        selected_ep = self.episodes[idx]
        await self.on_selected_callback(interaction, selected_ep, self.view)


class PodcastSelectView(discord.ui.View):
    """View berisi dropdown pilihan episode podcast dan tombol batal."""

    def __init__(self, episodes: List[Dict[str, Any]], user: discord.User, on_selected_callback: Callable):
        super().__init__(timeout=90.0)
        self.user = user
        self.add_item(PodcastSelectMenu(episodes, on_selected_callback))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ Hanya yang menjalankan perintah podcast yang dapat memilih episode ini.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Batal", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def btn_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="🛑 Pemilihan podcast dibatalkan.", embed=None, view=self)


class PodcastCategorySelectMenu(discord.ui.Select):
    """Dropdown untuk memilih kategori trending podcast."""

    def __init__(self, on_category_picked: Callable):
        self.on_category_picked = on_category_picked
        options = [
            discord.SelectOption(
                label=data["name"],
                description=data["desc"][:100],
                emoji=data["emoji"],
                value=cat_key,
            )
            for cat_key, data in PODCAST_CATEGORIES.items()
        ]
        super().__init__(
            placeholder="📂 Pilih kategori podcast trending minggu ini...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        cat_key = self.values[0]
        await self.on_category_picked(interaction, cat_key, self.view)


class PodcastCategoryView(discord.ui.View):
    """View awal untuk memilih kategori trending podcast."""

    def __init__(self, user: discord.User, on_category_picked: Callable):
        super().__init__(timeout=90.0)
        self.user = user
        self.add_item(PodcastCategorySelectMenu(on_category_picked))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ Hanya pengguna yang memanggil menu yang dapat memilih kategori.",
                ephemeral=True,
            )
            return False
        return True
