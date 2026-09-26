"""
🎵 Discord Music & 24/7 Voice Companion Bot
- Full Music Player ala Rythm (Play, Search Dropdown, Queue, Skip, Pause, Resume, Volume, Loop)
- 24/7 Keep-Online Voice Keeper with Auto-Reconnect
- Pomodoro Voice Study Companion with Bell Chimes
- Voice Activity Tracker & Voice Leaderboard (SQLite)
"""

import os
import re
import random
import asyncio
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Dict

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import database
from music_player import (
    Song,
    YTDLSource,
    GuildMusicQueue,
    SearchSelectView,
    GenreSelectView,
    FilterSelectView,
    GENRE_PLAYLISTS,
    AUDIO_FILTERS,
    get_ffmpeg_options,
    FFMPEG_OPTIONS,
    FFMPEG_EXECUTABLE,
    InterruptableVolumeTransformer,
    format_duration,
)
from pomodoro import PomodoroManager
from ai_playlist import generate_ai_playlist, recommend_next_song
from lyrics_manager import get_lyrics, chunk_lyrics, LyricsPaginationView, clean_song_title
from sleep_timer import SleepTimerManager, SleepTimerSession, SleepTimerSelectView

import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─── Environment & Logging ───────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_MUSIC_TOKEN") or os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("❌ ERROR: DISCORD_MUSIC_TOKEN atau DISCORD_TOKEN tidak ditemukan di .env!")
    print("   Buka file .env dan tambahkan token bot kamu.")
    exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("RythmVoiceCompanion")

# ─── 🎨 Design System: Branding & Color Palette ─────────────────────
class Theme:
    """Palet warna konsisten untuk semua embed bot."""
    PLAYING    = 0x9B59B6  # Ungu elegan — sedang memutar
    QUEUED     = 0x2ECC71  # Hijau segar — ditambahkan ke antrean
    INFO       = 0x3498DB  # Biru lembut — informasi umum
    AI         = 0xF39C12  # Emas warm — AI / smart features
    RADIO      = 0xE91E63  # Pink vibrant — radio / genre
    ERROR      = 0xE74C3C  # Merah — error
    SUCCESS    = 0x2ECC71  # Hijau — sukses
    POMODORO_W = 0xE74C3C  # Merah — fokus kerja
    POMODORO_B = 0x27AE60  # Hijau — istirahat
    IDLE       = 0x95A5A6  # Abu — idle
    LEADERBOARD = 0xF1C40F # Kuning — leaderboard
    BRAND_NAME = "🎵 Rythm Voice Companion"
    SEPARATOR  = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    SEPARATOR_THIN = "─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─"


def styled_embed(
    title: str,
    description: str = "",
    color: int = Theme.INFO,
    *,
    footer_text: str = "",
    footer_icon: str = None,
    thumbnail: str = None,
) -> discord.Embed:
    """Membuat embed dengan styling konsisten."""
    embed = discord.Embed(title=title, description=description, color=color)
    if footer_text:
        embed.set_footer(text=f"{footer_text}  •  {Theme.BRAND_NAME}", icon_url=footer_icon)
    else:
        embed.set_footer(text=Theme.BRAND_NAME)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def make_progress_bar(current_label: str = "▶️", length: int = 16) -> str:
    """Membuat visual bar dekoratif untuk Now Playing."""
    filled = random.randint(3, length - 3)
    bar = "▬" * filled + "🔘" + "▬" * (length - filled - 1)
    return f"`{bar}`"


def add_safe_fields(
    embed: discord.Embed,
    name: str,
    lines: List[str],
    max_chars: int = 950,
    inline: bool = False,
):
    """
    Menambahkan list of strings (lines) ke embed fields secara aman,
    memecahnya ke beberapa field jika total panjang melebihi max_chars (Discord limit 1024).
    Mencegah crash HTTPException 400 (50035): In embeds.0.fields.X.value: Must be 1024 or fewer in length.
    """
    if not lines:
        return

    chunks = []
    current_chunk = []
    current_len = 0

    for line in lines:
        safe_line = line if len(line) <= max_chars else line[: max_chars - 3] + "..."
        line_len = len(safe_line) + 1  # include newline
        if current_len + line_len > max_chars and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [safe_line]
            current_len = line_len
        else:
            current_chunk.append(safe_line)
            current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    for idx, chunk in enumerate(chunks):
        field_name = name if idx == 0 else f"{name} (Lanjutan)"
        embed.add_field(name=field_name, value=chunk, inline=inline)



class RythmVoiceBot(discord.Client):
    """Bot musik serbaguna dengan 24/7 voice stay, Pomodoro, dan Voice Tracker."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.guilds = True
        intents.members = True

        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

        # Voice & Keep-alive tracking
        self.voice_clients_dict: Dict[int, discord.VoiceClient] = {}
        self.target_channels: Dict[int, int] = {}  # guild_id -> channel_id
        self.connected_since: Dict[int, datetime] = {}

        # Music queues
        self.music_queues: Dict[int, GuildMusicQueue] = {}

        # Pomodoro Manager
        self.pomodoro = PomodoroManager(self.get_guild_voice_client)

        # Sleep Timer Manager
        self.sleep_timer = SleepTimerManager()

    def get_guild_voice_client(self, guild_id: int) -> Optional[discord.VoiceClient]:
        """Ambil voice client aktif untuk guild."""
        return self.voice_clients_dict.get(guild_id)

    def get_queue(self, guild_id: int) -> GuildMusicQueue:
        """Ambil atau inisialisasi queue musik untuk guild."""
        if guild_id not in self.music_queues:
            self.music_queues[guild_id] = GuildMusicQueue(guild_id)
        return self.music_queues[guild_id]

    async def setup_hook(self):
        """Inisialisasi database, persistent views, dan sinkronisasi slash commands."""
        await database.init_db()
        self.add_view(MusicControlView(bot_instance=self))
        await self.tree.sync()
        logger.info("✅ Slash commands & Persistent views berhasil di-sync!")

    async def on_ready(self):
        logger.info(f"✅ Bot logged in as {self.user}")
        logger.info(f"🌐 Terhubung ke {len(self.guilds)} server")

        # Instant guild-level sync agar commands langsung muncul detik itu juga di Discord tanpa menunggu cache 1 jam
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info(f"⚡ Instant guild commands synced: {len(synced)} commands untuk '{guild.name}' ({guild.id})")
            except Exception as e:
                logger.warning(f"⚠️ Gagal instant sync ke guild {guild.name}: {e}")

        # Mulai background tasks
        if not self.reconnect_loop.is_running():
            self.reconnect_loop.start()
        if not self.flush_voice_activity_loop.is_running():
            self.flush_voice_activity_loop.start()

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="🎵 /play | 🌙 /sleep | 🍅 /pomodoro",
            )
        )

    # ─── Event Voice State Update (Tracker & Reconnect) ───────────────
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        # 1. Deteksi bot disconnect tidak sengaja / kicked
        if member.id == self.user.id:
            if before.channel is not None and after.channel is None:
                guild_id = before.channel.guild.id
                logger.warning(
                    f"⚠️ Bot terputus dari voice channel '{before.channel.name}' di '{before.channel.guild.name}'."
                )
                self.voice_clients_dict.pop(guild_id, None)
                self.connected_since.pop(guild_id, None)

                # Reset antrean & hentikan sesi pomodoro agar tidak menjadi zombie
                queue = self.music_queues.get(guild_id)
                if queue:
                    queue.clear()

                self.pomodoro.stop_session(guild_id)
                asyncio.create_task(update_voice_channel_status(before.channel.id, None))
                asyncio.create_task(update_bot_presence(None))
            return

    # ─── Event Message (Prefix Command Fallback: !sleep, !sync, !np) ──
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        content = message.content.strip()
        lower_content = content.lower()

        # Command !sync: sinkronkan slash command ke server saat ini secara instan
        if lower_content == "!sync":
            try:
                self.tree.copy_global_to(guild=message.guild)
                synced = await self.tree.sync(guild=message.guild)
                await message.reply(
                    f"⚡ **Berhasil menyinkronkan {len(synced)} slash commands ke server ini secara instan!**\n"
                    f"Sekarang autocomplete `/sleep`, `/play`, dll. sudah langsung aktif."
                )
            except Exception as e:
                await message.reply(f"❌ Gagal sinkronisasi: `{e}`")
            return

        # Command !sleep atau !sleeptimer
        if lower_content.startswith("!sleep") or lower_content.startswith("!sleeptimer"):
            parts = content.split()
            subcmd = parts[1].lower() if len(parts) > 1 else ""

            if subcmd in ["cancel", "stop", "batal", "-cancel"]:
                cancelled = self.sleep_timer.cancel_timer(message.guild.id)
                if cancelled:
                    await message.reply(f"🛑 Sleep timer ({cancelled.minutes}m) berhasil dibatalkan!")
                else:
                    await message.reply("ℹ️ Tidak ada sleep timer yang sedang aktif.")
                return

            if subcmd in ["status", "cek", "-status"]:
                session = self.sleep_timer.get_session(message.guild.id)
                if session and session.is_active:
                    await message.reply(f"📊 Sleep timer aktif: sisa `{session.remaining_str}` (Tindakan: `{session.action}`).")
                else:
                    await message.reply("ℹ️ Tidak ada sleep timer yang sedang aktif.")
                return

            if subcmd.isdigit():
                minutes = int(subcmd)
                session = self.sleep_timer.start_timer(
                    guild_id=message.guild.id,
                    minutes=minutes,
                    action="kick_user",
                    text_channel=message.channel,
                    started_by=message.author,
                    on_trigger_callback=handle_sleep_timer_trigger,
                )
                embed = styled_embed(
                    title="🌙 Sleep Timer Berhasil Dipasang!",
                    description=(
                        f"⏱️ **Durasi:** `{minutes} menit`\n"
                        f"🎯 **Tindakan:** `Stop musik & disconnect saya dari voice`\n"
                        f"⌛ **Selesai dalam:** `{session.remaining_str}`\n\n"
                        f"💡 *Ketik `!sleep-cancel` jika ingin membatalkan kapan saja.*"
                    ),
                    color=Theme.SUCCESS,
                )
                await message.reply(embed=embed)
                return

            # Tampilkan menu dropdown interaktif
            current_session = self.sleep_timer.get_session(message.guild.id)
            desc_lines = [
                "### 🌙 Pengatur Waktu Tidur (Sleep Timer)",
                Theme.SEPARATOR,
                "Atur timer otomatis agar musik berhenti dan/atau akunmu disconnect dari voice saat tidur lelap.\n",
            ]
            if current_session and current_session.is_active:
                desc_lines.append(
                    f"🟢 **Timer Sedang Aktif!**\n"
                    f"Sisa waktu: **`{current_session.remaining_str}`**\n"
                    f"Tindakan: `{current_session.action}`\n\n"
                    "Pilih waktu baru di bawah atau tekan tombol merah untuk membatalkan."
                )
            else:
                desc_lines.append(
                    "Pilih durasi waktu dan tindakan yang kamu inginkan dari menu di bawah:"
                )

            embed = discord.Embed(
                title="",
                description="\n".join(desc_lines),
                color=0x34495E,
            )

            async def on_set(select_inter: discord.Interaction, minutes: int, action: str, view_inst):
                session = self.sleep_timer.start_timer(
                    guild_id=message.guild.id,
                    minutes=minutes,
                    action=action,
                    text_channel=message.channel,
                    started_by=select_inter.user,
                    on_trigger_callback=handle_sleep_timer_trigger,
                )
                action_desc = "Stop musik & disconnect saya dari voice" if action == "kick_user" else ("Bot keluar voice" if action == "leave" else "Hanya stop musik")
                res_embed = styled_embed(
                    title="🌙 Sleep Timer Berhasil Dipasang!",
                    description=(
                        f"⏱️ **Durasi:** `{minutes} menit`\n"
                        f"🎯 **Tindakan:** `{action_desc}`\n"
                        f"⌛ **Selesai dalam:** `{session.remaining_str}`\n\n"
                        f"Selamat beristirahat dan tidur nyenyak! 💤✨"
                    ),
                    color=Theme.SUCCESS,
                )
                await select_inter.response.edit_message(embed=res_embed, view=view_inst)

            async def on_cancel(cancel_inter: discord.Interaction, view_inst):
                self.sleep_timer.cancel_timer(message.guild.id)
                res_embed = styled_embed(
                    title="🛑 Sleep Timer Dibatalkan",
                    description="Timer tidur telah dimatikan. Musik akan terus berputar secara normal.",
                    color=Theme.INFO,
                )
                await cancel_inter.response.edit_message(embed=res_embed, view=view_inst)

            view = SleepTimerSelectView(message.author, current_session, on_set, on_cancel)
            await message.reply(embed=embed, view=view)
            return

        # Command !nowplaying atau !np untuk memunculkan player terbaru dengan tombol Sleep Timer
        if lower_content in ["!np", "!nowplaying", "!player"]:
            queue = self.get_queue(message.guild.id)
            if not queue.current:
                await message.reply("❌ Tidak ada lagu yang sedang diputar saat ini!")
                return
            embed = build_now_playing_embed(
                song=queue.current,
                queue=queue,
                voice_client=self.get_guild_voice_client(message.guild.id),
                connected_since=self.connected_since.get(message.guild.id),
            )
            view = MusicControlView(guild_id=message.guild.id, bot_instance=self)
            await message.reply(embed=embed, view=view)
            return

        # Command !leave atau !dc untuk mengeluarkan bot dari voice channel
        if lower_content in ["!leave", "!dc", "!disconnect", "!keluar"]:
            vc = self.get_guild_voice_client(message.guild.id)
            if not vc or not vc.is_connected():
                await message.reply("ℹ️ Bot tidak sedang berada di voice channel.")
                return
            self.target_channels.pop(message.guild.id, None)
            self.connected_since.pop(message.guild.id, None)
            self.voice_clients_dict.pop(message.guild.id, None)
            self.pomodoro.stop_session(message.guild.id)
            self.get_queue(message.guild.id).clear()
            if vc.channel:
                await update_voice_channel_status(vc.channel.id, None)
            await update_bot_presence(None)
            await vc.disconnect()
            await message.reply("👋 Bot telah keluar dari voice channel.")
            return

        # Command !stop
        if lower_content in ["!stop", "!hentikan"]:
            vc = self.get_guild_voice_client(message.guild.id)
            queue = self.get_queue(message.guild.id)
            queue.manual_stopped = True
            queue.clear()
            if vc and (vc.is_playing() or vc.is_paused()):
                vc.stop()
            if vc and vc.channel:
                await update_voice_channel_status(vc.channel.id, None)
            await update_bot_presence(None)
            await message.reply("⏹️ Musik dihentikan. (Ketik `/play` untuk memutar kembali, atau `!leave` agar bot keluar dari voice)")
            return



    # ─── Background Tasks ─────────────────────────────────────────────
    @tasks.loop(seconds=30)
    async def reconnect_loop(self):
        """Memastikan bot tetap stay di voice channel 24/7."""
        for guild_id, channel_id in list(self.target_channels.items()):
            guild = self.get_guild(guild_id)
            if not guild:
                continue

            vc = self.voice_clients_dict.get(guild_id)
            if vc and vc.is_connected():
                continue

            channel = guild.get_channel(channel_id)
            if not channel:
                continue

            try:
                new_vc = await channel.connect(self_deaf=False)
                self.voice_clients_dict[guild_id] = new_vc
                self.connected_since[guild_id] = datetime.now(timezone.utc)
                logger.info(f"🔄 Auto-reconnected ke {channel.name} di {guild.name}")
                queue = self.get_queue(guild_id)
                if queue.autoplay and not queue.manual_stopped and not new_vc.is_playing() and not queue.queue:
                    asyncio.create_task(handle_autoplay(guild_id))
            except Exception as e:
                logger.error(f"❌ Gagal auto-reconnect ke {channel.name}: {e}")

    @tasks.loop(seconds=60)
    async def flush_voice_activity_loop(self):
        """
        Simpan akumulasi waktu voice setiap 60 detik secara berkala.
        Anti-AFK & Deaf Filter:
        1. User tidak sedang self_deaf atau server deaf.
        2. Channel tidak kosong (ada minimal 2 member manusia, ATAU 1 manusia bersama bot).
        """
        entries_to_add = []
        for guild in self.guilds:
            for channel in guild.voice_channels:
                # Filter anggota manusia yang aktif (tidak deaf)
                active_humans = [
                    m for m in channel.members
                    if not m.bot and m.voice and not m.voice.self_deaf and not m.voice.deaf
                ]

                if not active_humans:
                    continue

                # Cek apakah sendirian di room kosong tanpa bot (mencegah farming AFK sendirian)
                bot_in_channel = any(m.id == self.user.id for m in channel.members)
                if len(active_humans) < 2 and not bot_in_channel:
                    continue

                for member in active_humans:
                    entries_to_add.append((member.id, guild.id, 60))

        if entries_to_add:
            try:
                await database.add_voice_time_batch(entries_to_add)
            except Exception as e:
                logger.error(f"Gagal mencatat voice activity batch: {e}")

    @reconnect_loop.before_loop
    @flush_voice_activity_loop.before_loop
    async def before_loops(self):
        await self.wait_until_ready()


# ─── Inisialisasi Bot Instance ────────────────────────────────────────
bot = RythmVoiceBot()


async def update_voice_channel_status(channel_id: Optional[int], status: Optional[str]):
    """Update status teks di Voice Channel Discord (Voice Channel Status seperti di bawah nama room)."""
    if not channel_id:
        return
    try:
        from discord.http import Route
        route = Route("PUT", "/channels/{channel_id}/voice-status", channel_id=channel_id)
        truncated_status = status[:500] if status else ""
        await bot.http.request(route, json={"status": truncated_status})
    except Exception as e:
        logger.debug(f"Tidak dapat mengubah voice channel status ({channel_id}): {e}")


async def update_bot_presence(title: Optional[str] = None):
    """Update status kehadiran/aktivitas bot di Discord."""
    try:
        if title:
            activity = discord.Activity(
                type=discord.ActivityType.listening,
                name=title[:128],
            )
        else:
            activity = discord.Activity(
                type=discord.ActivityType.listening,
                name="🎵 /play | 🍅 /pomodoro",
            )
        await bot.change_presence(activity=activity)
    except Exception as e:
        logger.debug(f"Gagal update bot presence: {e}")


# ─── Music Controller UI: Modern 3-Row Interactive Buttons ───────────
class MusicControlView(discord.ui.View):
    """
    Tombol interaktif pemutar musik modern (3-Row Layout).
    Persistent View: Berfungsi permanen bahkan setelah bot restart.
    Row 0: [ ⏸️ Jeda ]  [ ⏭️ Lewati ]  [ ⏹️ Stop ]  [ 🔀 Acak ]  [ 📜 Antrean ]
    Row 1: [ 🔁 Loop: OFF ]  [ 📻 Auto: ON ]  [ ✨ AI Next ]  [ 🎧 Radio ]  [ 📖 Lirik ]
    Row 2: [ 🎛️ Filter Audio ]  [ 🌙 Sleep Timer ]
    """

    def __init__(self, guild_id: Optional[int] = None, bot_instance = None):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.bot = bot_instance or bot
        if self.guild_id and self.bot:
            self._sync_states(self.guild_id)

    def _sync_states(self, guild_id: Optional[int] = None):
        """Update label, emoji, dan warna tombol sesuai status pemutar saat ini."""
        gid = guild_id or self.guild_id
        if not gid or not self.bot:
            return
        vc = self.bot.get_guild_voice_client(gid)
        queue = self.bot.get_queue(gid)

        # 1. Pause / Resume Button
        if vc and vc.is_paused():
            self.btn_pause_resume.emoji = "▶️"
            self.btn_pause_resume.label = "Lanjut"
            self.btn_pause_resume.style = discord.ButtonStyle.success
        else:
            self.btn_pause_resume.emoji = "⏸️"
            self.btn_pause_resume.label = "Jeda"
            self.btn_pause_resume.style = discord.ButtonStyle.primary

        # 2. Skip Button
        self.btn_skip.emoji = "⏭️"
        self.btn_skip.label = "Lewati"
        self.btn_skip.style = discord.ButtonStyle.secondary

        # 3. Stop Button
        self.btn_stop.emoji = "⏹️"
        self.btn_stop.label = "Stop"
        self.btn_stop.style = discord.ButtonStyle.danger

        # 4. Shuffle Button
        self.btn_shuffle.emoji = "🔀"
        self.btn_shuffle.label = "Acak"
        self.btn_shuffle.style = discord.ButtonStyle.secondary

        # 5. Queue Button
        q_count = len(queue.queue) if queue else 0
        self.btn_queue.emoji = "📜"
        self.btn_queue.label = f"Antrean ({q_count})" if q_count > 0 else "Antrean"
        self.btn_queue.style = discord.ButtonStyle.secondary

        # 6. Loop Button
        is_loop = queue.is_looping if queue else False
        self.btn_loop.emoji = "🔁"
        self.btn_loop.label = "Loop: ON" if is_loop else "Loop: OFF"
        self.btn_loop.style = discord.ButtonStyle.success if is_loop else discord.ButtonStyle.secondary

        # 7. AutoPlay Button
        is_autoplay = queue.autoplay if queue else False
        self.btn_autoplay.emoji = "📻"
        self.btn_autoplay.label = "Auto: ON" if is_autoplay else "Auto: OFF"
        self.btn_autoplay.style = discord.ButtonStyle.success if is_autoplay else discord.ButtonStyle.secondary

        # 8. AI Next Button
        self.btn_ai_next.emoji = "✨"
        self.btn_ai_next.label = "AI Next"
        self.btn_ai_next.style = discord.ButtonStyle.primary

        # 9. Radio Button
        self.btn_radio.emoji = "🎧"
        self.btn_radio.label = "Radio"
        self.btn_radio.style = discord.ButtonStyle.secondary

        # 10. Lyrics Button
        self.btn_lyrics.emoji = "📖"
        self.btn_lyrics.label = "Lirik"
        self.btn_lyrics.style = discord.ButtonStyle.secondary

        # 11. Audio Filter Button
        flt_key = queue.audio_filter if queue else None
        if flt_key and flt_key in AUDIO_FILTERS:
            self.btn_filter.label = f"Filter: {AUDIO_FILTERS[flt_key]['name'][:12]}"
            self.btn_filter.style = discord.ButtonStyle.success
        else:
            self.btn_filter.label = "Filter Audio"
            self.btn_filter.style = discord.ButtonStyle.secondary

        # 12. Sleep Timer Button
        timer_session = self.bot.sleep_timer.get_session(gid) if hasattr(self.bot, 'sleep_timer') else None
        if timer_session and timer_session.is_active:
            self.btn_sleep.label = f"Sleep: {timer_session.remaining_str[:10]}"
            self.btn_sleep.style = discord.ButtonStyle.success
        else:
            self.btn_sleep.label = "Sleep Timer"
            self.btn_sleep.style = discord.ButtonStyle.secondary

    # ─── Row 0: Playback Core Controls ───────────────────────────────
    @discord.ui.button(label="Jeda", emoji="⏸️", style=discord.ButtonStyle.primary, row=0, custom_id="mctrl_pause_resume")
    async def btn_pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        if not guild_id:
            await interaction.response.send_message("❌ Server tidak dikenali!", ephemeral=True)
            return

        vc = self.bot.get_guild_voice_client(guild_id)
        if not vc:
            await interaction.response.send_message("❌ Bot tidak sedang di voice channel!", ephemeral=True)
            return

        queue = self.bot.get_queue(guild_id)
        if vc.is_playing():
            vc.pause()
            if queue and not queue.pause_start_time:
                queue.pause_start_time = time.time()
            self._sync_states(guild_id)
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                if not interaction.response.is_done():
                    await interaction.response.send_message("⏸️ Musik dijeda.", ephemeral=True)
                else:
                    await interaction.followup.send("⏸️ Musik dijeda.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            if queue and queue.pause_start_time > 0:
                queue.paused_duration += time.time() - queue.pause_start_time
                queue.pause_start_time = 0.0
            self._sync_states(guild_id)
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                if not interaction.response.is_done():
                    await interaction.response.send_message("▶️ Musik dilanjutkan.", ephemeral=True)
                else:
                    await interaction.followup.send("▶️ Musik dilanjutkan.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Tidak ada musik yang sedang diputar!", ephemeral=True)

    @discord.ui.button(label="Lewati", emoji="⏭️", style=discord.ButtonStyle.secondary, row=0, custom_id="mctrl_skip")
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        vc = self.bot.get_guild_voice_client(guild_id)
        queue = self.bot.get_queue(guild_id)
        if not vc or not (vc.is_playing() or vc.is_paused()):
            await interaction.response.send_message("❌ Tidak ada lagu untuk dilewati!", ephemeral=True)
            return

        queue.force_skip = True
        vc.stop()
        self._sync_states(guild_id)
        if not interaction.response.is_done():
            await interaction.response.send_message("⏭️ Lagu dilewati!", ephemeral=True)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, row=0, custom_id="mctrl_stop")
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        vc = self.bot.get_guild_voice_client(guild_id)
        queue = self.bot.get_queue(guild_id)
        queue.manual_stopped = True
        queue.clear()
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

        if vc and vc.channel:
            await update_voice_channel_status(vc.channel.id, None)
        await update_bot_presence(None)

        for child in self.children:
            child.disabled = True

        try:
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("⏹️ Pemutaran dihentikan.", ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message("⏹️ Pemutaran dihentikan.", ephemeral=True)
            else:
                await interaction.followup.send("⏹️ Pemutaran dihentikan.", ephemeral=True)

    @discord.ui.button(label="Acak", emoji="🔀", style=discord.ButtonStyle.secondary, row=0, custom_id="mctrl_shuffle")
    async def btn_shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        queue = self.bot.get_queue(guild_id)
        if len(queue.queue) < 2:
            await interaction.response.send_message("ℹ️ Antrean butuh minimal 2 lagu untuk diacak.", ephemeral=True)
            return

        random.shuffle(queue.queue)
        self._sync_states(guild_id)
        try:
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"🔀 Berhasil mengacak **{len(queue.queue)} lagu** di antrean!", ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"🔀 Berhasil mengacak **{len(queue.queue)} lagu** di antrean!", ephemeral=True)
            else:
                await interaction.followup.send(f"🔀 Berhasil mengacak **{len(queue.queue)} lagu** di antrean!", ephemeral=True)

    @discord.ui.button(label="Antrean", emoji="📜", style=discord.ButtonStyle.secondary, row=0, custom_id="mctrl_queue")
    async def btn_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        queue = self.bot.get_queue(guild_id)
        embed = discord.Embed(
            title="📜 Antrean Musik (Quick View)",
            color=Theme.INFO,
        )
        if queue.current:
            embed.add_field(
                name="▶️ Sedang Diputar",
                value=f"**[{queue.current.title[:45]}]({queue.current.webpage_url})**\n⏱️ `{queue.current.duration_str}` — {queue.current.uploader[:30]}",
                inline=False,
            )

        if queue.queue:
            q_lines = []
            for i, s in enumerate(queue.queue[:8], start=1):
                clean_title = s.title[:35] + ("…" if len(s.title) > 35 else "")
                q_lines.append(f"`{i}.` [{clean_title}]({s.webpage_url}) · `{s.duration_str}` — {s.requester.mention}")
            if len(queue.queue) > 8:
                q_lines.append(f"*...dan {len(queue.queue) - 8} lagu lainnya.*")
            add_safe_fields(embed, name="📋 Berikutnya", lines=q_lines, max_chars=950, inline=False)
        else:
            embed.add_field(name="📋 Berikutnya", value="*Antrean kosong — AutoPlay aktif jika dinyalakan.*", inline=False)

        total_songs = len(queue.queue) + (1 if queue.current else 0)
        embed.set_footer(text=f"Total: {total_songs} lagu  •  Gunakan /queue untuk kontrol penuh  •  {Theme.BRAND_NAME}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── Row 1: Smart Modes & AI Tools ──────────────────────────────
    @discord.ui.button(label="Loop: OFF", emoji="🔁", style=discord.ButtonStyle.secondary, row=1, custom_id="mctrl_loop")
    async def btn_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        queue = self.bot.get_queue(guild_id)
        queue.is_looping = not queue.is_looping
        self._sync_states(guild_id)
        status_text = "diaktifkan 🔁" if queue.is_looping else "dimatikan ➡️"
        try:
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"Loop musik **{status_text}**", ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Loop musik **{status_text}**", ephemeral=True)
            else:
                await interaction.followup.send(f"Loop musik **{status_text}**", ephemeral=True)

    @discord.ui.button(label="Auto: OFF", emoji="📻", style=discord.ButtonStyle.secondary, row=1, custom_id="mctrl_autoplay")
    async def btn_autoplay(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        queue = self.bot.get_queue(guild_id)
        queue.autoplay = not queue.autoplay
        self._sync_states(guild_id)
        status_text = "diaktifkan 📻 (Putar otomatis 24/7)" if queue.autoplay else "dimatikan ⏹️"
        try:
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"AutoPlay **{status_text}**", ephemeral=True)
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"AutoPlay **{status_text}**", ephemeral=True)
            else:
                await interaction.followup.send(f"AutoPlay **{status_text}**", ephemeral=True)

    @discord.ui.button(label="AI Next", emoji="✨", style=discord.ButtonStyle.primary, row=1, custom_id="mctrl_ai_next")
    async def btn_ai_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild_id = self.guild_id or interaction.guild_id
        vc = self.bot.get_guild_voice_client(guild_id)
        if not vc or not vc.is_connected():
            await interaction.followup.send("❌ Bot tidak sedang di voice channel!", ephemeral=True)
            return

        queue = self.bot.get_queue(guild_id)
        recent_songs = list(queue.recent_history) + ([queue.current] if queue.current else [])
        if not recent_songs:
            await interaction.followup.send("ℹ️ Putar minimal satu lagu dulu agar AI dapat membaca referensi vibe!", ephemeral=True)
            return

        try:
            ai_data = await recommend_next_song(recent_songs)
            if not ai_data or not ai_data.get("title"):
                await interaction.followup.send("⚠️ AI gagal meracik rekomendasi saat ini. Coba lagi nanti!", ephemeral=True)
                return

            search_query = f"{ai_data.get('artist', '')} {ai_data.get('title', '')}".strip()
            song = await YTDLSource.get_song(search_query, requester=interaction.user)

            if not song:
                await interaction.followup.send(f"⚠️ AI menyarankan **{search_query}**, namun audio tidak ditemukan di YouTube.", ephemeral=True)
                return

            queue.add(song)
            self._sync_states(guild_id)
            try:
                if interaction.message:
                    await interaction.message.edit(view=self)
            except Exception:
                pass

            theme_label = ai_data.get("theme", "Rekomendasi AI")
            reason = ai_data.get("reason", "Melanjutkan vibe lagu sebelumnya")
            embed = styled_embed(
                title="✨ AI Rekomendasi Lagu Ditambahkan!",
                description=(
                    f"🎶 **[{song.title}]({song.webpage_url})**\n"
                    f"👤 `{song.uploader}` · ⏱️ `{song.duration_str}`\n\n"
                    f"🏷️ **Vibe:** `{theme_label}`\n"
                    f"💡 *\"{reason}\"*\n\n"
                    f"📥 Lagu dimasukkan ke antrean #{len(queue.queue)}!"
                ),
                color=Theme.AI,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

            if not vc.is_playing() and not vc.is_paused():
                play_next_song(guild_id)
        except Exception as e:
            logger.error(f"Error pada btn_ai_next: {e}", exc_info=True)
            await interaction.followup.send(f"❌ Terjadi kesalahan saat memproses AI Next: `{e}`", ephemeral=True)

    @discord.ui.button(label="Radio", emoji="🎧", style=discord.ButtonStyle.secondary, row=1, custom_id="mctrl_radio")
    async def btn_radio(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        async def on_genre_selected(inter: discord.Interaction, chosen_key: str, v: GenreSelectView):
            await inter.response.defer()
            await apply_genre_playlist(
                guild_id=guild_id,
                channel=inter.channel,
                user=inter.user,
                genre_key=chosen_key,
                interaction=inter,
            )

        view = GenreSelectView(interaction.user, on_genre_selected)
        embed = styled_embed(
            title="📻 Radio Otomatis 24/7",
            description="Pilih genre di bawah untuk memutar musik sesuai tema non-stop:",
            color=Theme.RADIO,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Lirik", emoji="📖", style=discord.ButtonStyle.secondary, row=1, custom_id="mctrl_lyrics")
    async def btn_lyrics(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        queue = self.bot.get_queue(guild_id)
        if not queue.current:
            await interaction.response.send_message("❌ Tidak ada lagu yang sedang diputar untuk dicari liriknya!", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        song = queue.current
        lyrics_data = await get_lyrics(song.title, song.uploader)

        if not lyrics_data or not lyrics_data.get("lyrics"):
            await interaction.followup.send(
                f"❌ Maaf, lirik untuk lagu **{song.title}** tidak ditemukan.",
                ephemeral=True,
            )
            return

        pages = chunk_lyrics(lyrics_data["lyrics"], max_chars=1800)
        view = LyricsPaginationView(
            pages=pages,
            title=lyrics_data["title"],
            artist=lyrics_data["artist"],
            source=lyrics_data["source"],
            requester=interaction.user,
            thumbnail=song.thumbnail,
        )
        await interaction.followup.send(embed=view.build_embed(), view=view, ephemeral=True)

    # ─── Row 2: Audio DSP Filters ────────────────────────────────────
    @discord.ui.button(label="Filter Audio", emoji="🎛️", style=discord.ButtonStyle.secondary, row=2, custom_id="mctrl_filter")
    async def btn_filter(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        queue = self.bot.get_queue(guild_id)
        current_flt = queue.audio_filter or "reset"
        current_name = AUDIO_FILTERS[current_flt]["name"]
        current_emoji = AUDIO_FILTERS[current_flt]["emoji"]

        embed = discord.Embed(
            title="",
            description=(
                f"### 🎛️ Audio DSP Filters\n"
                f"{Theme.SEPARATOR}\n"
                f"Ubah kualitas dan karakter suara secara langsung (real-time).\n"
                f"Lagu akan langsung berganti efek tanpa harus mengulang dari awal!\n\n"
                f"**Filter Aktif:** {current_emoji} **{current_name}**\n\n"
                "Pilih efek audio dari menu dropdown di bawah:"
            ),
            color=Theme.PLAYING,
        )

        async def on_filter_selected(select_inter: discord.Interaction, chosen_key: str, view_instance):
            res = apply_audio_filter(guild_id, chosen_key)
            self._sync_states(guild_id)
            try:
                if interaction.message:
                    await interaction.message.edit(view=self)
            except Exception:
                pass

            status_desc = (
                f"Efek berhasil diubah menjadi: {res['emoji']} **{res['name']}**\n\n"
                f"📝 *{res['desc']}*"
            )
            if res["reloaded"]:
                status_desc += f"\n\n⚡ *Lagu saat ini langsung di-reload pada detik `{format_duration(int(res['elapsed']))}`!*"

            res_embed = styled_embed(
                title=f"{res['emoji']} Audio Filter Diperbarui",
                description=status_desc,
                color=Theme.SUCCESS if chosen_key != "reset" else Theme.INFO,
            )
            await select_inter.response.edit_message(embed=res_embed, view=view_instance)

        view = FilterSelectView(interaction.user, queue.audio_filter, on_filter_selected)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Sleep Timer", emoji="🌙", style=discord.ButtonStyle.secondary, row=2, custom_id="mctrl_sleep")
    async def btn_sleep(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        current_session = self.bot.sleep_timer.get_session(guild_id)

        desc_lines = [
            "### 🌙 Pengatur Waktu Tidur (Sleep Timer)",
            Theme.SEPARATOR,
            "Atur timer otomatis agar musik berhenti dan/atau akunmu disconnect dari voice saat tidur lelap.\n",
        ]
        if current_session and current_session.is_active:
            desc_lines.append(
                f"🟢 **Timer Sedang Aktif!**\n"
                f"Sisa waktu: **`{current_session.remaining_str}`**\n"
                f"Tindakan: `{current_session.action}`\n\n"
                "Pilih waktu baru di bawah atau tekan tombol merah untuk membatalkan."
            )
        else:
            desc_lines.append(
                "Pilih durasi waktu dan tindakan yang kamu inginkan dari menu di bawah:"
            )

        embed = discord.Embed(
            title="",
            description="\n".join(desc_lines),
            color=0x34495E,
        )

        async def on_set(select_inter: discord.Interaction, minutes: int, action: str, view_inst):
            session = self.bot.sleep_timer.start_timer(
                guild_id=guild_id,
                minutes=minutes,
                action=action,
                text_channel=interaction.channel,
                started_by=interaction.user,
                on_trigger_callback=handle_sleep_timer_trigger,
            )
            self._sync_states(guild_id)
            try:
                if interaction.message:
                    await interaction.message.edit(view=self)
            except Exception:
                pass

            action_desc = "Stop musik & disconnect saya dari voice" if action == "kick_user" else ("Bot keluar voice" if action == "leave" else "Hanya stop musik")
            res_embed = styled_embed(
                title="🌙 Sleep Timer Berhasil Dipasang!",
                description=(
                    f"⏱️ **Durasi:** `{minutes} menit`\n"
                    f"🎯 **Tindakan:** `{action_desc}`\n"
                    f"⌛ **Selesai dalam:** `{session.remaining_str}`\n\n"
                    f"Selamat beristirahat dan tidur nyenyak! 💤✨"
                ),
                color=Theme.SUCCESS,
            )
            await select_inter.response.edit_message(embed=res_embed, view=view_inst)

        async def on_cancel(cancel_inter: discord.Interaction, view_inst):
            self.bot.sleep_timer.cancel_timer(guild_id)
            self._sync_states(guild_id)
            try:
                if interaction.message:
                    await interaction.message.edit(view=self)
            except Exception:
                pass
            res_embed = styled_embed(
                title="🛑 Sleep Timer Dibatalkan",
                description="Timer tidur telah dimatikan. Musik akan terus berputar secara normal.",
                color=Theme.INFO,
            )
            await cancel_inter.response.edit_message(embed=res_embed, view=view_inst)

        view = SleepTimerSelectView(interaction.user, current_session, on_set, on_cancel)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        """Global error handler untuk tombol agar tidak pernah hang / timed out."""
        logger.error(f"Error pada tombol kontrol musik ({getattr(item, 'custom_id', item)}): {error}", exc_info=True)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Terjadi kesalahan pada tombol: `{error}`", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Terjadi kesalahan pada tombol: `{error}`", ephemeral=True)
        except Exception:
            pass



# ─── Music Playback Engine ───────────────────────────────────────────
def play_next_song(guild_id: int):
    """Callback pemutar lagu berikutnya dari antrean."""
    vc = bot.get_guild_voice_client(guild_id)
    queue = bot.get_queue(guild_id)

    if not vc or not vc.is_connected():
        return

    song = queue.next_song()
    if not song:
        # Antrean habis - jika AutoPlay aktif, putar rekomendasi berikutnya
        if queue.autoplay and not queue.manual_stopped:
            asyncio.run_coroutine_threadsafe(handle_autoplay(guild_id), bot.loop)
            return

        if vc.channel:
            asyncio.run_coroutine_threadsafe(
                update_voice_channel_status(vc.channel.id, None), bot.loop
            )
        asyncio.run_coroutine_threadsafe(update_bot_presence(None), bot.loop)

        if queue.text_channel:
            embed = styled_embed(
                title="📭 Antrean Selesai",
                description=(
                    f"{Theme.SEPARATOR_THIN}\n"
                    "Semua lagu dalam antrean telah selesai diputar.\n"
                    "Bot tetap **standby 24/7** di voice channel!\n\n"
                    "💡 *Gunakan `/play`, `/radio`, atau `/aiplaylist` untuk memutar musik lagi.*"
                ),
                color=Theme.IDLE,
            )
            asyncio.run_coroutine_threadsafe(
                queue.text_channel.send(embed=embed), bot.loop
            )
        return

    try:
        ffmpeg_opts = get_ffmpeg_options(queue.audio_filter)
        audio_source = discord.FFmpegPCMAudio(
            song.url, executable=FFMPEG_EXECUTABLE, **ffmpeg_opts
        )
        volume_transformer = InterruptableVolumeTransformer(
            audio_source, volume=queue.volume
        )

        def after_callback(error):
            if queue.is_reloading:
                return
            if error:
                logger.error(f"Error saat playback: {error}")
            play_next_song(guild_id)

        vc.play(volume_transformer, after=after_callback)
        queue.song_start_time = time.time()
        queue.paused_duration = 0.0
        queue.pause_start_time = 0.0

        # Update status teks di bawah nama Voice Channel & bot presence
        if vc.channel:
            status_text = f"{song.title} - {song.uploader}" if song.uploader and song.uploader != "Unknown Artist" else song.title
            asyncio.run_coroutine_threadsafe(
                update_voice_channel_status(vc.channel.id, status_text), bot.loop
            )
        asyncio.run_coroutine_threadsafe(
            update_bot_presence(song.title), bot.loop
        )

        # Kirim embed Now Playing — Desain Premium
        if queue.text_channel:
            artist_display = song.uploader if song.uploader and song.uploader != "Unknown Artist" else "Unknown Artist"
            progress = make_progress_bar()

            embed = discord.Embed(
                title="",
                description=(
                    f"### 🎶 Sedang Memutar\n"
                    f"**[{song.title}]({song.webpage_url})**\n"
                    f"oleh **{artist_display}**\n\n"
                    f"{progress}\n"
                    f"`⏱️ {song.duration_str}`"
                ),
                color=Theme.PLAYING,
            )
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)

            # Status bar kompak
            loop_icon = "🔁" if queue.is_looping else "➡️"
            autoplay_icon = "📻" if queue.autoplay else "⏹️"
            vol_pct = int(queue.volume * 100)
            status_parts = [f"🔊 `{vol_pct}%`", f"{loop_icon} Loop", f"{autoplay_icon} AutoPlay"]

            genre_data = GENRE_PLAYLISTS.get(queue.autoplay_genre) if queue.autoplay_genre else None
            if genre_data:
                status_parts.append(f"{genre_data['emoji']} {genre_data['name']}")

            if queue.audio_filter and queue.audio_filter in AUDIO_FILTERS:
                flt_data = AUDIO_FILTERS[queue.audio_filter]
                status_parts.append(f"{flt_data['emoji']} {flt_data['name']}")

            embed.add_field(name="", value=" **·** ".join(status_parts), inline=False)
            embed.add_field(name="Diminta oleh", value=song.requester.mention, inline=True)
            if queue.queue:
                embed.add_field(name="Antrean", value=f"`{len(queue.queue)} lagu`", inline=True)

            footer_parts = [f"Volume: {vol_pct}%"]
            if genre_data:
                footer_parts.append(f"📻 {genre_data['name']}")
            if queue.audio_filter and queue.audio_filter in AUDIO_FILTERS:
                footer_parts.append(f"🎛️ {AUDIO_FILTERS[queue.audio_filter]['name']}")
            embed.set_footer(text=f"{'  ·  '.join(footer_parts)}  •  {Theme.BRAND_NAME}")

            view = MusicControlView(guild_id, bot)
            asyncio.run_coroutine_threadsafe(
                queue.text_channel.send(embed=embed, view=view), bot.loop
            )
    except Exception as e:
        logger.error(f"Gagal memutar lagu: {e}")
        play_next_song(guild_id)


def apply_audio_filter(guild_id: int, filter_key: str) -> dict:
    """
    Mengaplikasikan audio filter (DSP) secara real-time.
    Jika sedang ada lagu berputar, lagu akan di-hot-reload pada detik yang sama
    menggunakan opsi FFmpeg baru (-ss {elapsed} dan -af "{filter}").
    """
    queue = bot.get_queue(guild_id)
    vc = bot.get_guild_voice_client(guild_id)

    key = filter_key.lower().strip() if filter_key else "reset"
    if key not in AUDIO_FILTERS or key == "reset":
        queue.audio_filter = None
        filter_info = AUDIO_FILTERS["reset"]
    else:
        queue.audio_filter = key
        filter_info = AUDIO_FILTERS[key]

    reloaded = False
    elapsed = 0.0

    if vc and (vc.is_playing() or vc.is_paused()) and queue.current:
        elapsed = queue.get_elapsed_seconds()
        was_paused = vc.is_paused()

        # Tandai agar after_callback tidak memanggil play_next_song saat vc.stop()
        queue.is_reloading = True
        try:
            vc.stop()
        except Exception as e:
            logger.warning(f"Error saat menghentikan vc untuk reload filter: {e}")

        ffmpeg_opts = get_ffmpeg_options(queue.audio_filter, start_time=elapsed)
        try:
            new_source = discord.FFmpegPCMAudio(
                queue.current.url,
                executable=FFMPEG_EXECUTABLE,
                **ffmpeg_opts,
            )
            volume_transformer = InterruptableVolumeTransformer(
                new_source, volume=queue.volume
            )

            def after_callback(error):
                if queue.is_reloading:
                    return
                if error:
                    logger.error(f"Error saat playback setelah reload filter: {error}")
                play_next_song(guild_id)

            vc.play(volume_transformer, after=after_callback)

            # Perbarui timing tracking agar get_elapsed_seconds() tetap akurat
            now = time.time()
            queue.song_start_time = now - elapsed
            queue.paused_duration = 0.0
            if was_paused:
                vc.pause()
                queue.pause_start_time = now
            else:
                queue.pause_start_time = 0.0

            reloaded = True
        except Exception as e:
            logger.error(f"Gagal hot-reload audio filter: {e}", exc_info=True)
        finally:
            queue.is_reloading = False

    return {
        "key": queue.audio_filter or "reset",
        "name": filter_info["name"],
        "emoji": filter_info["emoji"],
        "desc": filter_info["desc"],
        "reloaded": reloaded,
        "elapsed": elapsed,
        "current_song": queue.current.title if queue.current else None,
    }


async def handle_autoplay(guild_id: int):
    """Mencari dan memutar lagu rekomendasi/genre secara otomatis (AutoPlay 24/7)."""
    vc = bot.get_guild_voice_client(guild_id)
    queue = bot.get_queue(guild_id)

    if not vc or not vc.is_connected() or not queue.autoplay or queue.manual_stopped:
        return
    if vc.is_playing() or vc.is_paused() or queue.queue or queue.current:
        return

    genre_data = GENRE_PLAYLISTS.get(queue.autoplay_genre) if queue.autoplay_genre else None
    
    # ─── 1. AI Context AutoPlay (Analisis Beberapa Lagu Terakhir) ───
    if not genre_data:
        history_to_analyze = queue.recent_history[-5:] if queue.recent_history else ([queue.last_played] if queue.last_played else [])
        if history_to_analyze:
            try:
                ai_rec = await recommend_next_song(history_to_analyze)
                if ai_rec and ai_rec.get("title") and ai_rec.get("artist"):
                    search_str = f"{ai_rec.get('artist')} {ai_rec.get('title')}".strip()
                    logger.info(f"🤖 AI AutoPlay merekomendasikan: '{search_str}' (Tema: {ai_rec.get('theme')})")
                    ai_song = await YTDLSource.get_song(search_str, requester=bot.user)
                    if ai_song and ai_song.webpage_url not in queue.played_history:
                        queue.add(ai_song)
                        if not vc.is_playing() and not vc.is_paused():
                            play_next_song(guild_id)
                            if queue.text_channel:
                                theme_text = ai_rec.get("theme", "Lagu Terkait")
                                reason_text = ai_rec.get("reason", "Melanjutkan vibe lagu sebelumnya.")
                                embed = discord.Embed(
                                    title="",
                                    description=(
                                        f"### 🤖 AI Context AutoPlay\n"
                                        f"{Theme.SEPARATOR_THIN}\n"
                                        f"Menganalisis tema lagu-lagu sebelumnya...\n\n"
                                        f"▶️ **[{ai_song.title}]({ai_song.webpage_url})**\n"
                                        f"👤 **{ai_song.uploader}** · ⏱️ `{ai_song.duration_str}`\n\n"
                                        f"🏷️ **Tema:** `{theme_text}`\n"
                                        f"💡 **Alasan:** *\"{reason_text}\"*"
                                    ),
                                    color=Theme.AI,
                                )
                                if ai_song.thumbnail:
                                    embed.set_thumbnail(url=ai_song.thumbnail)
                                embed.set_footer(text=f"AI Smart AutoPlay  •  {Theme.BRAND_NAME}")
                                await queue.text_channel.send(embed=embed)
                        return
            except Exception as e:
                logger.warning(f"AI AutoPlay context error (fallback to search): {e}")

    # ─── 2. Fallback / Genre Radio AutoPlay ───
    if genre_data:
        query = random.choice(genre_data["queries"])
        logger.info(f"📻 AutoPlay Radio [{genre_data['name']}] mencari lagu: '{query}'")
    elif queue.last_played and queue.last_played.title:
        title = queue.last_played.title
        clean_title = re.sub(r"[\[\(].*?[\]\)]", "", title).strip()
        uploader = queue.last_played.uploader or ""
        if uploader and uploader.lower() not in ["unknown artist", "youtube"]:
            query = f"{uploader} {clean_title} songs"
        else:
            query = f"{clean_title} songs"
        logger.info(f"📻 AutoPlay rekomendasi mencari lagu: '{query}'")
    else:
        query = "lofi chill beats mix"
        logger.info(f"📻 AutoPlay default mencari lagu: '{query}'")

    try:
        tracks = await YTDLSource.search_tracks(query, max_results=5)
        selected_track = None
        for t in tracks:
            w_url = t.get("webpage_url") or t.get("url")
            if w_url and w_url not in queue.played_history:
                selected_track = t
                break

        if not selected_track and tracks:
            selected_track = tracks[0]

        if not selected_track:
            tracks = await YTDLSource.search_tracks("lofi hip hop radio beats to relax", max_results=3)
            if tracks:
                selected_track = tracks[0]

        if selected_track:
            target_url = selected_track.get("webpage_url") or selected_track.get("url")
            song = await YTDLSource.get_song(target_url, requester=bot.user)
            if song:
                queue.add(song)
                if not vc.is_playing() and not vc.is_paused():
                    play_next_song(guild_id)
                    if queue.text_channel:
                        if genre_data:
                            genre_tag = f"{genre_data['emoji']} {genre_data['name']}"
                            embed = styled_embed(
                                title=f"📻 Radio: {genre_tag}",
                                description=(
                                    f"Memutar lagu berikutnya secara otomatis:\n\n"
                                    f"▶️ **[{song.title}]({song.webpage_url})**\n"
                                    f"⏱️ `{song.duration_str}`"
                                ),
                                color=Theme.RADIO,
                                footer_text=f"Radio {genre_data['name']}  ·  /radio untuk ganti genre",
                            )
                        else:
                            embed = styled_embed(
                                title="📻 AutoPlay",
                                description=(
                                    f"Memutar rekomendasi berikutnya:\n\n"
                                    f"▶️ **[{song.title}]({song.webpage_url})**\n"
                                    f"⏱️ `{song.duration_str}`"
                                ),
                                color=Theme.PLAYING,
                                footer_text="/radio untuk genre  ·  /autoplay on/off",
                            )
                        if song.thumbnail:
                            embed.set_thumbnail(url=song.thumbnail)
                        await queue.text_channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Error pada handle_autoplay: {e}", exc_info=True)


async def apply_genre_playlist(
    guild_id: int,
    channel: discord.TextChannel,
    user: discord.Member,
    genre_key: str,
    interaction: Optional[discord.Interaction] = None,
):
    """Menerapkan genre radio & mengisi lagu awal dari genre tersebut."""
    queue = bot.get_queue(guild_id)
    vc = bot.get_guild_voice_client(guild_id)

    if genre_key == "off":
        queue.autoplay_genre = None
        embed = styled_embed(
            title="⏹️ Radio Dinonaktifkan",
            description="Mode Radio Genre dimatikan.\nAutoPlay kembali ke **rekomendasi AI** berdasarkan lagu terakhir.",
            color=Theme.IDLE,
            footer_text="/radio untuk memilih genre lagi",
        )
        if interaction:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed)
            else:
                await interaction.response.send_message(embed=embed)
        return

    if genre_key not in GENRE_PLAYLISTS:
        msg = f"❌ Genre `{genre_key}` tidak ditemukan!"
        if interaction:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        return

    genre = GENRE_PLAYLISTS[genre_key]
    queue.autoplay_genre = genre_key
    queue.autoplay = True
    queue.manual_stopped = False
    queue.text_channel = channel

    # Ambil lagu untuk genre ini
    query = random.choice(genre["queries"])
    tracks = await YTDLSource.search_tracks(query, max_results=5)

    loaded_songs = []
    if tracks:
        for t in tracks:
            w_url = t.get("webpage_url") or t.get("url")
            if w_url:
                s = await YTDLSource.get_song(w_url, requester=user)
                if s:
                    queue.add(s)
                    loaded_songs.append(s)

    # Putar sekarang jika bot belum play musik
    if vc and not vc.is_playing() and not vc.is_paused():
        play_next_song(guild_id)

    embed = discord.Embed(
        title="",
        description=(
            f"### 📻 Radio Aktif: {genre['emoji']} {genre['name']}\n"
            f"{Theme.SEPARATOR_THIN}\n"
            f"*{genre['desc']}*\n\n"
            f"✅ Menambahkan **{len(loaded_songs)} lagu** ke antrean!\n"
            f"🎶 Bot akan terus memutar lagu **{genre['name']}** non-stop 24/7."
        ),
        color=Theme.RADIO,
    )
    if loaded_songs:
        song_list_text = "\n".join(
            [f"`{i+1}.` [{s.title[:45]}]({s.webpage_url}) · `{s.duration_str}`" for i, s in enumerate(loaded_songs[:5])]
        )
        embed.add_field(name="🎶 Playlist Awal", value=song_list_text, inline=False)

    embed.set_footer(text=f"Radio {genre['name']}  ·  /radio untuk ganti genre  •  {Theme.BRAND_NAME}")

    if interaction:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)
    else:
        await channel.send(embed=embed)


async def ensure_voice_connection(interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
    """Helper untuk memastikan bot dan user berada di voice channel."""
    if not interaction.user.voice or not interaction.user.voice.channel:
        msg = "❌ Kamu harus masuk ke voice channel terlebih dahulu!"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return None

    user_channel = interaction.user.voice.channel
    guild_id = interaction.guild.id
    vc = bot.get_guild_voice_client(guild_id)

    if not vc or not vc.is_connected():
        try:
            vc = await user_channel.connect(self_deaf=False, timeout=20.0)
            bot.voice_clients_dict[guild_id] = vc
            bot.target_channels[guild_id] = user_channel.id
            bot.connected_since[guild_id] = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Gagal bergabung ke voice channel: {e}", exc_info=True)
            msg = f"❌ Gagal bergabung ke voice channel: `{e}`"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return None
    elif vc.channel.id != user_channel.id:
        try:
            await vc.move_to(user_channel)
            bot.target_channels[guild_id] = user_channel.id
        except Exception as e:
            logger.error(f"Gagal berpindah ke channel {user_channel.name}: {e}")

    # Otomatis optimasi Bitrate Voice Channel ke kualitas tertinggi server jika punya izin
    try:
        max_bitrate = interaction.guild.bitrate_limit
        if user_channel.bitrate < max_bitrate and user_channel.permissions_for(interaction.guild.me).manage_channels:
            await user_channel.edit(bitrate=max_bitrate)
            logger.info(f"🔊 Bitrate {user_channel.name} ditingkatkan otomatis ke {max_bitrate // 1000} kbps")
    except Exception as e:
        logger.debug(f"Tidak dapat mengubah bitrate otomatis: {e}")

    return vc


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Global handler agar slash command tidak hang di 'thinking...' jika ada error tak terduga."""
    logger.error(f"Unhandled slash command error: {error}", exc_info=error)
    msg = f"❌ Terjadi kesalahan saat menjalankan perintah: `{error}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ─── Slash Commands: Music ───────────────────────────────────────────
@bot.tree.command(name="play", description="🎵 Putar lagu dari YouTube (judul atau link)")
@app_commands.describe(query="Judul lagu, nama artis, atau URL YouTube")
async def cmd_play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    try:
        vc = await ensure_voice_connection(interaction)
        if not vc:
            return

        guild_id = interaction.guild.id
        queue = bot.get_queue(guild_id)
        queue.text_channel = interaction.channel

        song = await YTDLSource.get_song(query, interaction.user)
        if not song:
            embed = styled_embed(
                title="❌ Lagu Tidak Ditemukan",
                description=f"Tidak dapat menemukan lagu untuk:\n> `{query}`\n\n💡 *Coba gunakan judul yang lebih spesifik atau URL langsung.*",
                color=Theme.ERROR,
            )
            await interaction.followup.send(embed=embed)
            return

        queue.add(song)

        if not vc.is_playing() and not vc.is_paused():
            play_next_song(guild_id)
            embed = styled_embed(
                title="▶️ Memutar Sekarang",
                description=(
                    f"**[{song.title}]({song.webpage_url})**\n"
                    f"👤 {song.uploader} · ⏱️ `{song.duration_str}`"
                ),
                color=Theme.PLAYING,
                thumbnail=song.thumbnail,
            )
            await interaction.followup.send(embed=embed)
        else:
            embed = discord.Embed(
                title="",
                description=(
                    f"### 📥 Ditambahkan ke Antrean\n"
                    f"**[{song.title}]({song.webpage_url})**\n"
                    f"👤 {song.uploader} · ⏱️ `{song.duration_str}`\n\n"
                    f"📍 Posisi: **#{len(queue.queue)}** dalam antrean"
                ),
                color=Theme.QUEUED,
            )
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            embed.set_footer(text=Theme.BRAND_NAME)
            await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.error(f"Error pada cmd_play: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Terjadi kesalahan saat memproses lagu: `{e}`")


@bot.tree.command(name="search", description="🔍 Cari 5 pilihan lagu dan pilih via menu dropdown")
@app_commands.describe(query="Judul lagu atau artis yang dicari")
async def cmd_search(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    try:
        vc = await ensure_voice_connection(interaction)
        if not vc:
            return

        tracks = await YTDLSource.search_tracks(query, max_results=5)
        if not tracks:
            await interaction.followup.send(f"❌ Tidak ada hasil untuk: `{query}`")
            return

        guild_id = interaction.guild.id
        queue = bot.get_queue(guild_id)
        queue.text_channel = interaction.channel

        async def on_song_selected(inter: discord.Interaction, song: Song, view: SearchSelectView):
            queue.add(song)
            if not vc.is_playing() and not vc.is_paused():
                play_next_song(guild_id)
                await inter.response.edit_message(
                    content=f"▶️ Memutar: **{song.title}** ({song.duration_str})",
                    view=view,
                )
            else:
                await inter.response.edit_message(
                    content=f"📥 Ditambahkan ke antrean: **{song.title}** (#{len(queue.queue)})",
                    view=view,
                )

        view = SearchSelectView(tracks, interaction.user, on_song_selected)
        embed = discord.Embed(
            title="🔍 Hasil Pencarian Lagu",
            description=f"Ditemukan 5 lagu untuk `{query}`. Pilih lagu dari menu dropdown di bawah:",
            color=discord.Color.blurple(),
        )
        await interaction.followup.send(embed=embed, view=view)
    except Exception as e:
        logger.error(f"Error pada cmd_search: {e}", exc_info=True)
        await interaction.followup.send(f"❌ Terjadi kesalahan saat mencari lagu: `{e}`")


@bot.tree.command(name="skip", description="⏭️ Lewati lagu yang sedang diputar")
async def cmd_skip(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = bot.get_guild_voice_client(guild_id)
    queue = bot.get_queue(guild_id)

    if not vc or not (vc.is_playing() or vc.is_paused()):
        await interaction.response.send_message("❌ Tidak ada lagu yang sedang diputar!", ephemeral=True)
        return

    # Force skip bypassing loop
    queue.force_skip = True
    vc.stop()
    await interaction.response.send_message("⏭️ Lagu dilewati!")


@bot.tree.command(name="pause", description="⏸️ Jeda lagu yang sedang diputar")
async def cmd_pause(interaction: discord.Interaction):
    vc = bot.get_guild_voice_client(interaction.guild.id)
    queue = bot.get_queue(interaction.guild.id)
    if vc and vc.is_playing():
        vc.pause()
        if queue and not queue.pause_start_time:
            queue.pause_start_time = time.time()
        await interaction.response.send_message("⏸️ Musik dijeda.")
    else:
        await interaction.response.send_message("❌ Tidak ada musik yang sedang diputar!", ephemeral=True)


@bot.tree.command(name="resume", description="▶️ Lanjutkan lagu yang dijeda")
async def cmd_resume(interaction: discord.Interaction):
    vc = bot.get_guild_voice_client(interaction.guild.id)
    queue = bot.get_queue(interaction.guild.id)
    if vc and vc.is_paused():
        vc.resume()
        if queue and queue.pause_start_time > 0:
            queue.paused_duration += time.time() - queue.pause_start_time
            queue.pause_start_time = 0.0
        await interaction.response.send_message("▶️ Musik dilanjutkan.")
    else:
        await interaction.response.send_message("❌ Musik tidak sedang dijeda!", ephemeral=True)


@bot.tree.command(name="stop", description="⏹️ Hentikan musik dan kosongkan antrean")
async def cmd_stop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = bot.get_guild_voice_client(guild_id)
    queue = bot.get_queue(guild_id)

    queue.manual_stopped = True
    queue.clear()
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()

    if vc and vc.channel:
        await update_voice_channel_status(vc.channel.id, None)
    await update_bot_presence(None)

    await interaction.response.send_message("⏹️ Musik dihentikan. (Ketik `/play` atau `/autoplay` untuk memutar kembali)")


@bot.tree.command(name="queue", description="📜 Lihat daftar antrean lagu")
async def cmd_queue(interaction: discord.Interaction):
    queue = bot.get_queue(interaction.guild.id)

    if not queue.current and not queue.queue:
        await interaction.response.send_message("📭 Antrean musik sedang kosong!", ephemeral=True)
        return

    # Hitung total durasi
    total_dur = sum(s.duration or 0 for s in queue.queue) + (queue.current.duration or 0 if queue.current else 0)
    total_dur_str = format_duration(total_dur) if total_dur > 0 else "N/A"

    desc_parts = []
    if queue.autoplay_genre and queue.autoplay_genre in GENRE_PLAYLISTS:
        g = GENRE_PLAYLISTS[queue.autoplay_genre]
        desc_parts.append(f"📻 **Radio:** {g['emoji']} **{g['name']}**")
    desc_parts.append(Theme.SEPARATOR_THIN)

    embed = discord.Embed(
        title="📜 Antrean Musik",
        description="\n".join(desc_parts),
        color=Theme.INFO,
    )

    if queue.current:
        embed.add_field(
            name="▶️ Sedang Diputar",
            value=(
                f"**[{queue.current.title}]({queue.current.webpage_url})**\n"
                f"👤 {queue.current.uploader} · ⏱️ `{queue.current.duration_str}`"
            ),
            inline=False,
        )

    if queue.queue:
        queue_lines = []
        for i, song in enumerate(queue.queue[:10], start=1):
            clean_title = song.title[:35] + ("…" if len(song.title) > 35 else "")
            queue_lines.append(f"`{i}.` [{clean_title}]({song.webpage_url}) · `{song.duration_str}` — {song.requester.mention}")
        if len(queue.queue) > 10:
            queue_lines.append(f"*...dan {len(queue.queue) - 10} lagu lainnya.*")
        add_safe_fields(embed, name="📋 Berikutnya", lines=queue_lines, max_chars=950, inline=False)
    else:
        embed.add_field(name="📋 Berikutnya", value="*Antrean kosong — AutoPlay akan memilih lagu otomatis.*", inline=False)

    # Status bar bawah
    total_songs = len(queue.queue) + (1 if queue.current else 0)
    loop_str = "🔁 On" if queue.is_looping else "➡️ Off"
    auto_str = "📻 On" if queue.autoplay else "⏹️ Off"
    status_line = f"`{total_songs} lagu` · ⏱️ `{total_dur_str}` · Loop: {loop_str} · AutoPlay: {auto_str}"
    embed.add_field(name="", value=status_line, inline=False)

    embed.set_footer(text=Theme.BRAND_NAME)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="nowplaying", description="🎧 Info lagu yang sedang berputar")
async def cmd_nowplaying(interaction: discord.Interaction):
    queue = bot.get_queue(interaction.guild.id)
    if not queue.current:
        await interaction.response.send_message("❌ Tidak ada lagu yang sedang diputar!", ephemeral=True)
        return

    song = queue.current
    artist_display = song.uploader if song.uploader and song.uploader != "Unknown Artist" else "Unknown Artist"
    progress = make_progress_bar()

    embed = discord.Embed(
        title="",
        description=(
            f"### 🎧 Now Playing\n"
            f"{Theme.SEPARATOR_THIN}\n\n"
            f"**[{song.title}]({song.webpage_url})**\n"
            f"oleh **{artist_display}**\n\n"
            f"{progress}\n"
            f"`⏱️ {song.duration_str}`"
        ),
        color=Theme.PLAYING,
    )
    if song.thumbnail:
        embed.set_image(url=song.thumbnail)

    # Status bar
    vol_pct = int(queue.volume * 100)
    loop_icon = "🔁" if queue.is_looping else "➡️"
    autoplay_icon = "📻" if queue.autoplay else "⏹️"
    status_parts = [f"🔊 `{vol_pct}%`", f"{loop_icon} Loop", f"{autoplay_icon} AutoPlay"]

    genre_data = GENRE_PLAYLISTS.get(queue.autoplay_genre) if queue.autoplay_genre else None
    if genre_data:
        status_parts.append(f"{genre_data['emoji']} {genre_data['name']}")

    embed.add_field(name="", value=" **·** ".join(status_parts), inline=False)
    embed.add_field(name="Diminta oleh", value=song.requester.mention, inline=True)
    if queue.queue:
        embed.add_field(name="Antrean", value=f"`{len(queue.queue)} lagu`", inline=True)

    embed.set_footer(text=f"Volume: {vol_pct}%  •  {Theme.BRAND_NAME}")
    view = MusicControlView(interaction.guild.id, bot)
    await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="lyrics", description="📜 Cari lirik lagu yang sedang diputar atau cari judul tertentu")
@app_commands.describe(judul="Judul lagu yang ingin dicari (kosongkan untuk lirik lagu yang sedang diputar)")
async def cmd_lyrics(interaction: discord.Interaction, judul: Optional[str] = None):
    await interaction.response.defer()
    guild_id = interaction.guild.id
    queue = bot.get_queue(guild_id)

    if judul:
        search_title = judul
        search_artist = ""
        thumb = None
    else:
        if not queue.current:
            await interaction.followup.send("❌ Tidak ada lagu yang sedang diputar! Masukkan judul lagu yang dicari: `/lyrics judul:...`")
            return
        search_title = queue.current.title
        search_artist = queue.current.uploader
        thumb = queue.current.thumbnail

    lyrics_data = await get_lyrics(search_title, search_artist)

    if not lyrics_data or not lyrics_data.get("lyrics"):
        embed = styled_embed(
            title="❌ Lirik Tidak Ditemukan",
            description=(
                f"Tidak dapat menemukan lirik untuk lagu **{search_title}**.\n\n"
                "💡 *Tips: Coba cari dengan format nama artis dan judul lagu, misal: `/lyrics judul: Tulus Hati-Hati di Jalan`*"
            ),
            color=Theme.ERROR,
        )
        await interaction.followup.send(embed=embed)
        return

    pages = chunk_lyrics(lyrics_data["lyrics"], max_chars=1800)
    view = LyricsPaginationView(
        pages=pages,
        title=lyrics_data["title"],
        artist=lyrics_data["artist"],
        source=lyrics_data["source"],
        requester=interaction.user,
        thumbnail=thumb,
    )
    await interaction.followup.send(embed=view.build_embed(), view=view)


@bot.tree.command(name="volume", description="🔊 Atur volume musik (1 - 100%)")
@app_commands.describe(persen="Persentase volume (1 - 100)")
async def cmd_volume(interaction: discord.Interaction, persen: int):
    persen = max(1, min(100, persen))
    queue = bot.get_queue(interaction.guild.id)
    queue.volume = persen / 100.0

    vc = bot.get_guild_voice_client(interaction.guild.id)
    if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = queue.volume

    await interaction.response.send_message(f"🔊 Volume diatur ke **{persen}%**")


@bot.tree.command(name="loop", description="🔁 Putar ulang lagu yang sedang diputar terus-menerus")
async def cmd_loop(interaction: discord.Interaction):
    queue = bot.get_queue(interaction.guild.id)
    queue.is_looping = not queue.is_looping
    status_str = "diaktifkan 🔁" if queue.is_looping else "dinonaktifkan ➡️"
    await interaction.response.send_message(f"Loop musik berhasil **{status_str}**")


@bot.tree.command(name="filter", description="🎛️ Pasang efek audio DSP (Bassboost, Nightcore, Slowed, 8D, Lofi, dll.)")
@app_commands.describe(jenis="Pilih efek audio filter yang ingin dipasang atau reset")
@app_commands.choices(
    jenis=[
        app_commands.Choice(name="⏹️ Reset / Normal (Tanpa Filter)", value="reset"),
        app_commands.Choice(name="🔊 Bass Boost (Dentuman Rendah Kuat)", value="bassboost"),
        app_commands.Choice(name="⚡ Nightcore (Speed Up + High Pitch)", value="nightcore"),
        app_commands.Choice(name="🌧️ Slowed + Reverb (Vibe Sendu & Santai)", value="slowed"),
        app_commands.Choice(name="🎧 8D Audio (Surround Berputar Kiri-Kanan)", value="8d"),
        app_commands.Choice(name="☕ Lo-Fi Warm (Warm Vintage Low-Pass)", value="lofi"),
        app_commands.Choice(name="🎤 Karaoke / Vocal Cut (Peredam Vokal)", value="karaoke"),
        app_commands.Choice(name="✨ Treble Boost (Instrumen Tinggi Lebih Jernih)", value="treble"),
    ]
)
async def cmd_filter(interaction: discord.Interaction, jenis: Optional[app_commands.Choice[str]] = None):
    guild_id = interaction.guild.id
    queue = bot.get_queue(guild_id)

    if jenis is None:
        current_flt = queue.audio_filter or "reset"
        current_name = AUDIO_FILTERS[current_flt]["name"]
        current_emoji = AUDIO_FILTERS[current_flt]["emoji"]

        embed = discord.Embed(
            title="",
            description=(
                f"### 🎛️ Audio DSP Filters\n"
                f"{Theme.SEPARATOR}\n"
                f"Pilih efek audio untuk mengubah karakter dan vibe suara musik secara langsung.\n"
                f"Efek langsung diterapkan pada lagu yang sedang berputar tanpa memuat ulang dari awal!\n\n"
                f"**Filter Aktif Saat Ini:** {current_emoji} **{current_name}**\n\n"
                "Pilih efek dari menu dropdown di bawah:"
            ),
            color=Theme.PLAYING,
        )

        async def on_filter_selected(select_inter: discord.Interaction, chosen_key: str, view_instance):
            res = apply_audio_filter(guild_id, chosen_key)
            status_desc = (
                f"Efek berhasil diubah menjadi: {res['emoji']} **{res['name']}**\n\n"
                f"📝 *{res['desc']}*"
            )
            if res["reloaded"]:
                status_desc += f"\n\n⚡ *Lagu saat ini langsung di-reload pada detik `{format_duration(int(res['elapsed']))}`!*"

            res_embed = styled_embed(
                title=f"{res['emoji']} Audio Filter Diperbarui",
                description=status_desc,
                color=Theme.SUCCESS if chosen_key != "reset" else Theme.INFO,
            )
            await select_inter.response.edit_message(embed=res_embed, view=view_instance)

        view = FilterSelectView(interaction.user, queue.audio_filter, on_filter_selected)
        await interaction.response.send_message(embed=embed, view=view)
        return

    res = apply_audio_filter(guild_id, jenis.value)
    status_desc = (
        f"Efek berhasil diubah menjadi: {res['emoji']} **{res['name']}**\n\n"
        f"📝 *{res['desc']}*"
    )
    if res["reloaded"]:
        status_desc += f"\n\n⚡ *Lagu saat ini langsung di-reload pada detik `{format_duration(int(res['elapsed']))}`!*"

    embed = styled_embed(
        title=f"{res['emoji']} Audio Filter Diperbarui",
        description=status_desc,
        color=Theme.SUCCESS if jenis.value != "reset" else Theme.INFO,
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="autoplay", description="📻 Aktifkan atau nonaktifkan AutoPlay musik otomatis 24/7")
@app_commands.describe(
    mode="Pilih ON untuk menyalakan atau OFF untuk mematikan",
    genre="Opsional: Tentukan genre lagu tertentu untuk AutoPlay 24/7",
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="ON (AutoPlay aktif)", value="on"),
        app_commands.Choice(name="OFF (AutoPlay mati)", value="off"),
    ],
    genre=[
        app_commands.Choice(name="☕ Lofi & Chill Study", value="lofi"),
        app_commands.Choice(name="🇮🇩 Pop Hits Indonesia", value="indonesia"),
        app_commands.Choice(name="🌎 Global Pop & Billboard", value="pop"),
        app_commands.Choice(name="⚡ EDM & Gaming", value="edm"),
        app_commands.Choice(name="🎷 Jazz & Cafe Vibes", value="jazz"),
        app_commands.Choice(name="🌧️ Galau & Akustik Sendu", value="galau"),
        app_commands.Choice(name="🎸 Rock & Alternative", value="rock"),
        app_commands.Choice(name="🌸 Anime & J-Pop", value="anime"),
        app_commands.Choice(name="🎹 Piano & Focus Instrumental", value="piano"),
        app_commands.Choice(name="💤 Sleep & Deep Ambient", value="ambient"),
        app_commands.Choice(name="⏹️ Hapus Filter Genre (AutoPlay Bebas)", value="off"),
    ],
)
async def cmd_autoplay(
    interaction: discord.Interaction,
    mode: Optional[app_commands.Choice[str]] = None,
    genre: Optional[app_commands.Choice[str]] = None,
):
    guild_id = interaction.guild.id
    queue = bot.get_queue(guild_id)

    if mode:
        queue.autoplay = (mode.value == "on")
    elif genre is None:
        queue.autoplay = not queue.autoplay

    if genre:
        queue.autoplay_genre = None if genre.value == "off" else genre.value

    queue.manual_stopped = False
    genre_data = GENRE_PLAYLISTS.get(queue.autoplay_genre) if queue.autoplay_genre else None
    genre_info = f" (Genre: {genre_data['emoji']} **{genre_data['name']}**)" if genre_data else ""

    status_str = f"diaktifkan 📻{genre_info}" if queue.autoplay else "dinonaktifkan ⏹️"

    vc = bot.get_guild_voice_client(guild_id)
    if queue.autoplay and vc and vc.is_connected() and not vc.is_playing() and not vc.is_paused():
        queue.text_channel = interaction.channel
        asyncio.create_task(handle_autoplay(guild_id))

    await interaction.response.send_message(f"AutoPlay berhasil **{status_str}**")


async def execute_radio(interaction: discord.Interaction, genre_val: Optional[str] = None):
    await interaction.response.defer()
    vc = await ensure_voice_connection(interaction)
    if not vc:
        return

    guild_id = interaction.guild.id

    if genre_val:
        await apply_genre_playlist(
            guild_id=guild_id,
            channel=interaction.channel,
            user=interaction.user,
            genre_key=genre_val,
            interaction=interaction,
        )
    else:
        async def on_genre_selected(inter: discord.Interaction, chosen_key: str, view: GenreSelectView):
            await inter.response.defer()
            await apply_genre_playlist(
                guild_id=guild_id,
                channel=inter.channel,
                user=inter.user,
                genre_key=chosen_key,
                interaction=inter,
            )

        view = GenreSelectView(interaction.user, on_genre_selected)
        embed = discord.Embed(
            title="",
            description=(
                f"### 📻 Pilih Stasiun Radio\n"
                f"{Theme.SEPARATOR_THIN}\n"
                "Pilih genre atau mood musik dari menu di bawah.\n"
                "Bot akan menyusun playlist dan memutarnya **non-stop 24/7**!"
            ),
            color=Theme.RADIO,
        )
        embed.set_footer(text=Theme.BRAND_NAME)
        await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="radio", description="📻 Auto-Playlist 24/7 berdasarkan genre musik atau mood")
@app_commands.describe(genre="Pilih genre lagu langsung, atau kosongkan untuk membuka menu dropdown")
@app_commands.choices(
    genre=[
        app_commands.Choice(name="☕ Lofi & Chill Study", value="lofi"),
        app_commands.Choice(name="🇮🇩 Pop Hits Indonesia", value="indonesia"),
        app_commands.Choice(name="🌎 Global Pop & Billboard", value="pop"),
        app_commands.Choice(name="⚡ EDM & Gaming", value="edm"),
        app_commands.Choice(name="🎷 Jazz & Cafe Vibes", value="jazz"),
        app_commands.Choice(name="🌧️ Galau & Akustik Sendu", value="galau"),
        app_commands.Choice(name="🎸 Rock & Alternative", value="rock"),
        app_commands.Choice(name="🌸 Anime & J-Pop", value="anime"),
        app_commands.Choice(name="🎹 Piano & Focus Instrumental", value="piano"),
        app_commands.Choice(name="💤 Sleep & Deep Ambient", value="ambient"),
        app_commands.Choice(name="⏹️ Nonaktifkan Radio Genre (Off)", value="off"),
    ]
)
async def cmd_radio(
    interaction: discord.Interaction,
    genre: Optional[app_commands.Choice[str]] = None,
):
    await execute_radio(interaction, genre.value if genre else None)


@bot.tree.command(name="genre", description="📻 Buka menu pilihan genre lagu (Auto-Playlist)")
async def cmd_genre(interaction: discord.Interaction):
    await execute_radio(interaction, None)


async def execute_ai_playlist(
    interaction: discord.Interaction,
    prompt: str,
    jumlah: int = 5,
):
    await interaction.response.defer()
    vc = await ensure_voice_connection(interaction)
    if not vc:
        return

    guild_id = interaction.guild.id
    queue = bot.get_queue(guild_id)
    queue.text_channel = interaction.channel

    jumlah = max(3, min(10, jumlah or 5))

    status_embed = discord.Embed(
        title="",
        description=(
            f"### 🤖 AI DJ sedang meracik...\n"
            f"{Theme.SEPARATOR_THIN}\n"
            f"Menganalisis vibe: *\"{prompt}\"*\n"
            f"Meracik **{jumlah} lagu** terbaik untukmu..."
        ),
        color=Theme.AI,
    )
    status_embed.set_footer(text=f"Powered by Google Gemini  •  {Theme.BRAND_NAME}")
    msg = await interaction.followup.send(embed=status_embed)

    ai_data = await generate_ai_playlist(prompt, track_count=jumlah)
    if not ai_data or not ai_data.get("tracks"):
        err_embed = styled_embed(
            title="❌ Gagal Meracik Playlist",
            description="AI sedang mengalami kendala. Silakan coba sesaat lagi dengan deskripsi lain.",
            color=Theme.ERROR,
        )
        await msg.edit(embed=err_embed)
        return

    playlist_title = ai_data.get("title", "AI Curated Playlist")
    vibe_desc = ai_data.get("vibe", "")
    tracks = ai_data.get("tracks", [])

    loaded_songs = []
    for item in tracks:
        song_title = item.get("title", "")
        artist = item.get("artist", "")
        search_query = f"{artist} {song_title}".strip()
        if not search_query:
            continue

        try:
            song = await YTDLSource.get_song(search_query, requester=interaction.user)
            if song:
                queue.add(song)
                loaded_songs.append(song)
        except Exception as e:
            logger.warning(f"Gagal memuat lagu AI '{search_query}': {e}")

    if not loaded_songs:
        err_embed = styled_embed(
            title="❌ Lagu Tidak Ditemukan",
            description="AI telah merekomendasikan lagu, namun bot gagal menemukan stream audio di YouTube.",
            color=Theme.ERROR,
        )
        await msg.edit(embed=err_embed)
        return

    if vc and not vc.is_playing() and not vc.is_paused():
        play_next_song(guild_id)

    res_embed = discord.Embed(
        title="",
        description=(
            f"### ✨ {playlist_title}\n"
            f"{Theme.SEPARATOR_THIN}\n"
            f"*{vibe_desc}*\n\n"
            f"✅ Berhasil menambahkan **{len(loaded_songs)} lagu** ke antrean!"
        ),
        color=Theme.AI,
    )
    safe_prompt = prompt[:250] + "..." if len(prompt) > 250 else prompt
    res_embed.add_field(
        name="🎯 Prompt",
        value=f"> *\"{safe_prompt}\"*",
        inline=False,
    )

    track_lines = []
    for i, s in enumerate(loaded_songs, start=1):
        clean_title = s.title[:38] + ("…" if len(s.title) > 38 else "")
        clean_uploader = s.uploader[:20] + ("…" if len(s.uploader) > 20 else "")
        track_lines.append(f"`{i}.` [{clean_title}]({s.webpage_url}) · `{s.duration_str}` — {clean_uploader}")
    add_safe_fields(res_embed, name="🎶 Daftar Lagu", lines=track_lines, max_chars=950, inline=False)
    res_embed.set_footer(
        text=f"Powered by Google Gemini  •  Diminta oleh {interaction.user.display_name}  •  {Theme.BRAND_NAME}",
        icon_url=interaction.user.display_avatar.url,
    )

    view = MusicControlView(guild_id, bot)
    await msg.edit(embed=res_embed, view=view)


@bot.tree.command(name="aiplaylist", description="🤖 Minta AI meracik playlist custom berdasarkan mood, vibe, atau situasi kamu")
@app_commands.describe(
    prompt="Ceritakan mood, situasi, atau suasana yang kamu inginkan (misal: lagu galau akustik indo)",
    jumlah="Jumlah lagu yang ingin diracik oleh AI (3 - 10 lagu, default: 5)",
)
async def cmd_aiplaylist(
    interaction: discord.Interaction,
    prompt: str,
    jumlah: Optional[int] = 5,
):
    await execute_ai_playlist(interaction, prompt=prompt, jumlah=jumlah or 5)


@bot.tree.command(name="aidj", description="🤖 Minta AI DJ meracik playlist custom instan dari vibe kamu")
@app_commands.describe(vibe="Deskripsikan suasana/vibe lagu yang kamu inginkan")
async def cmd_aidj(interaction: discord.Interaction, vibe: str):
    await execute_ai_playlist(interaction, prompt=vibe, jumlah=5)


# ─── Slash Commands: Pomodoro Voice Companion ────────────────────────
@bot.tree.command(name="pomo-start", description="🍅 Mulai sesi Pomodoro (Fokus → Istirahat → Fokus...)")
@app_commands.describe(
    fokus="Durasi fokus dalam menit (default: 25, max: 120)",
    istirahat="Durasi istirahat pendek dalam menit (default: 5, max: 30)",
    istirahat_panjang="Durasi istirahat panjang setelah semua siklus (default: 15, max: 60)",
    siklus="Jumlah siklus fokus & istirahat (default: 4, max: 12)",
)
async def cmd_pomo_start(
    interaction: discord.Interaction,
    fokus: Optional[int] = 25,
    istirahat: Optional[int] = 5,
    istirahat_panjang: Optional[int] = 15,
    siklus: Optional[int] = 4,
):
    await ensure_voice_connection(interaction)

    guild_id = interaction.guild.id

    # Cek jika sudah ada sesi aktif
    existing = bot.pomodoro.get_session(guild_id)
    if existing and existing.is_running:
        embed = styled_embed(
            title="⚠️ Sesi Sudah Aktif",
            description=(
                "Sudah ada sesi Pomodoro yang berjalan!\n\n"
                "💡 Gunakan `/pomo-stop` untuk menghentikan, atau `/pomo-status` untuk melihat progress."
            ),
            color=Theme.AI,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    fokus = max(1, min(120, fokus or 25))
    istirahat = max(1, min(30, istirahat or 5))
    istirahat_panjang = max(1, min(60, istirahat_panjang or 15))
    siklus = max(1, min(12, siklus or 4))

    session = bot.pomodoro.start_session(
        guild_id=guild_id,
        text_channel=interaction.channel,
        started_by=interaction.user,
        work_min=fokus,
        short_break_min=istirahat,
        long_break_min=istirahat_panjang,
        cycles=siklus,
    )

    total_work = fokus * siklus
    total_break = istirahat * (siklus - 1) + istirahat_panjang
    total_all = total_work + total_break

    embed = discord.Embed(
        title="",
        description=(
            f"### 🍅 Pomodoro Dimulai!\n"
            f"{Theme.SEPARATOR_THIN}\n\n"
            f"🔥 **Fokus:** `{fokus} menit` × {siklus} siklus\n"
            f"☕ **Istirahat Pendek:** `{istirahat} menit`\n"
            f"🌴 **Istirahat Panjang:** `{istirahat_panjang} menit` *(setelah siklus terakhir)*\n\n"
            f"⏱️ **Total estimasi:** `{total_all} menit` ({total_work}m fokus + {total_break}m istirahat)\n\n"
            f"🔔 Bot akan memutar bel notifikasi di voice channel setiap pergantian fase.\n"
            f"📊 Progress siklus akan otomatis di-update di chat."
        ),
        color=Theme.POMODORO_W,
    )
    embed.set_footer(text=f"/pomo-stop untuk berhenti  ·  /pomo-pause untuk jeda  •  🍅 Pomodoro Companion")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pomo-stop", description="🛑 Hentikan sesi Pomodoro yang sedang berjalan")
async def cmd_pomo_stop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    summary = bot.pomodoro.stop_session(guild_id)

    if not summary:
        await interaction.response.send_message("ℹ️ Tidak ada sesi Pomodoro yang aktif.", ephemeral=True)
        return

    focus_min = summary["focus_seconds"] // 60
    completed = summary["completed"]

    embed = discord.Embed(
        title="",
        description=(
            f"### 🛑 Pomodoro Dihentikan\n"
            f"{Theme.SEPARATOR_THIN}\n\n"
            f"Sesi Pomodoro telah dihentikan.\n\n"
            f"📊 **Ringkasan:**\n"
            f"✅ Siklus fokus selesai: **{completed}**\n"
            f"⏱️ Total fokus: **{focus_min} menit**"
        ),
        color=Theme.IDLE,
    )
    embed.set_footer(text="🍅 Pomodoro Companion")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pomo-pause", description="⏸️ Jeda sesi Pomodoro sementara")
async def cmd_pomo_pause(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    success = bot.pomodoro.pause_session(guild_id)

    if not success:
        session = bot.pomodoro.get_session(guild_id)
        if session and session.is_paused:
            await interaction.response.send_message("ℹ️ Sesi sudah dijeda. Gunakan `/pomo-resume` untuk melanjutkan.", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Tidak ada sesi Pomodoro yang aktif.", ephemeral=True)
        return

    session = bot.pomodoro.get_session(guild_id)
    remaining = session.remaining_seconds if session else 0
    m, s = divmod(remaining, 60)

    embed = discord.Embed(
        title="",
        description=(
            f"### ⏸️ Pomodoro Dijeda\n"
            f"{Theme.SEPARATOR_THIN}\n\n"
            f"Timer dihentikan sementara.\n"
            f"Sisa waktu: **`{m:02d}:{s:02d}`**\n\n"
            f"💡 Gunakan `/pomo-resume` untuk melanjutkan."
        ),
        color=Theme.AI,
    )
    embed.set_footer(text="🍅 Pomodoro Companion")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pomo-resume", description="▶️ Lanjutkan sesi Pomodoro yang dijeda")
async def cmd_pomo_resume(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    success = bot.pomodoro.resume_session(guild_id)

    if not success:
        session = bot.pomodoro.get_session(guild_id)
        if session and not session.is_paused:
            await interaction.response.send_message("ℹ️ Sesi tidak sedang dijeda.", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ Tidak ada sesi Pomodoro yang aktif.", ephemeral=True)
        return

    session = bot.pomodoro.get_session(guild_id)
    remaining = session.remaining_seconds if session else 0
    m, s = divmod(remaining, 60)

    embed = discord.Embed(
        title="",
        description=(
            f"### ▶️ Pomodoro Dilanjutkan\n"
            f"{Theme.SEPARATOR_THIN}\n\n"
            f"Timer berjalan kembali!\n"
            f"Sisa waktu: **`{m:02d}:{s:02d}`**"
        ),
        color=Theme.SUCCESS,
    )
    embed.set_footer(text="🍅 Pomodoro Companion")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pomo-status", description="📊 Cek progress dan sisa waktu Pomodoro")
async def cmd_pomo_status(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    session = bot.pomodoro.get_session(guild_id)

    if not session or not session.is_running:
        await interaction.response.send_message("ℹ️ Tidak ada sesi Pomodoro yang aktif.", ephemeral=True)
        return

    embed = session._build_phase_embed()
    await interaction.response.send_message(embed=embed)


# ─── Slash Commands: Sleep Timer (Pengatur Waktu Tidur) ───────────────
async def handle_sleep_timer_trigger(session: SleepTimerSession):
    """Callback saat waktu sleep timer habis."""
    guild_id = session.guild_id
    vc = bot.get_guild_voice_client(guild_id)
    queue = bot.get_queue(guild_id)

    # 1. Hentikan musik & bersihkan antrean
    queue.manual_stopped = True
    queue.clear()
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()

    if vc and vc.channel:
        await update_voice_channel_status(vc.channel.id, None)
    await update_bot_presence(None)

    # 2. Tindakan: kick_user, leave, atau stop (tetap standby)
    user_kicked = False
    if session.action == "kick_user":
        try:
            guild = bot.get_guild(guild_id)
            member = None
            if guild:
                member = guild.get_member(session.started_by.id)
                if not member:
                    try:
                        member = await guild.fetch_member(session.started_by.id)
                    except Exception:
                        pass
            if not member and isinstance(session.started_by, discord.Member):
                member = session.started_by

            if member and member.voice and member.voice.channel:
                await member.move_to(None)
                user_kicked = True
        except discord.Forbidden:
            logger.warning(f"Bot tidak memiliki izin Move Members untuk mendisconnect {session.started_by.display_name}")
        except Exception as e:
            logger.warning(f"Gagal mendisconnect user dari voice: {e}")

        kick_status = (
            f"✅ **{session.started_by.display_name}** telah dikeluarkan dari voice channel agar baterai hemat & tidur tenang."
            if user_kicked
            else f"ℹ️ **{session.started_by.display_name}** sudah tidak berada di voice channel."
        )
        embed = discord.Embed(
            title="",
            description=(
                "### 🌙 Waktu Tidur Tiba (Sleep Timer Selesai)\n"
                f"{Theme.SEPARATOR_THIN}\n\n"
                f"Waktu tidur **{session.minutes} menit** telah tercapai.\n\n"
                "Musik telah dihentikan secara otomatis.\n"
                f"{kick_status}\n\n"
                "Bot tetap standby 24/7 di voice channel. Selamat tidur nyenyak! 💤✨"
            ),
            color=0x2C3E50,
        )
    elif session.action == "leave":
        # Hapus dari target_channels agar 24/7 reconnect loop tidak menyambungkan bot lagi
        bot.target_channels.pop(guild_id, None)
        bot.connected_since.pop(guild_id, None)
        bot.voice_clients_dict.pop(guild_id, None)
        if vc and vc.is_connected():
            await vc.disconnect()
        embed = discord.Embed(
            title="",
            description=(
                "### 🌙 Waktu Tidur Tiba (Sleep Timer Selesai)\n"
                f"{Theme.SEPARATOR_THIN}\n\n"
                f"Waktu tidur **{session.minutes} menit** telah tercapai.\n\n"
                "Musik dihentikan dan bot telah keluar dari voice channel.\n"
                "Selamat beristirahat dan tidur nyenyak! 💤✨"
            ),
            color=0x2C3E50,
        )
    else:
        embed = discord.Embed(
            title="",
            description=(
                "### 🌙 Waktu Tidur Tiba (Sleep Timer Selesai)\n"
                f"{Theme.SEPARATOR_THIN}\n\n"
                f"Waktu tidur **{session.minutes} menit** telah tercapai.\n\n"
                "Musik telah dihentikan secara otomatis.\n"
                "Bot tetap standby 24/7 di voice channel. Selamat tidur! 💤✨"
            ),
            color=0x2C3E50,
        )

    embed.set_footer(text=f"Sleep Timer  •  Diminta oleh {session.started_by.display_name}  •  {Theme.BRAND_NAME}")

    try:
        await session.text_channel.send(embed=embed)
    except Exception as e:
        logger.warning(f"Gagal mengirim notifikasi sleep timer: {e}")


async def _execute_sleeptimer(
    interaction: discord.Interaction,
    menit: Optional[int] = None,
    tindakan: Optional[app_commands.Choice[str]] = None,
):
    guild_id = interaction.guild.id
    if menit is None:
        current_session = bot.sleep_timer.get_session(guild_id)

        desc_lines = [
            "### 🌙 Pengatur Waktu Tidur (Sleep Timer)",
            Theme.SEPARATOR,
            "Atur timer otomatis agar musik berhenti dan/atau akunmu disconnect dari voice saat tidur lelap.\n",
        ]
        if current_session and current_session.is_active:
            desc_lines.append(
                f"🟢 **Timer Sedang Aktif!**\n"
                f"Sisa waktu: **`{current_session.remaining_str}`**\n"
                f"Tindakan: `{current_session.action}`\n\n"
                "Pilih waktu baru di bawah atau tekan tombol merah untuk membatalkan."
            )
        else:
            desc_lines.append(
                "Pilih durasi waktu dan tindakan yang kamu inginkan dari menu di bawah:"
            )

        embed = discord.Embed(
            title="",
            description="\n".join(desc_lines),
            color=0x34495E,
        )

        async def on_set(select_inter: discord.Interaction, minutes: int, action: str, view_inst):
            session = bot.sleep_timer.start_timer(
                guild_id=guild_id,
                minutes=minutes,
                action=action,
                text_channel=interaction.channel,
                started_by=interaction.user,
                on_trigger_callback=handle_sleep_timer_trigger,
            )
            action_desc = "Stop musik & disconnect saya dari voice" if action == "kick_user" else ("Bot keluar voice" if action == "leave" else "Hanya stop musik")
            res_embed = styled_embed(
                title="🌙 Sleep Timer Berhasil Dipasang!",
                description=(
                    f"⏱️ **Durasi:** `{minutes} menit`\n"
                    f"🎯 **Tindakan:** `{action_desc}`\n"
                    f"⌛ **Selesai dalam:** `{session.remaining_str}`\n\n"
                    f"Selamat beristirahat dan tidur nyenyak! 💤✨"
                ),
                color=Theme.SUCCESS,
            )
            await select_inter.response.edit_message(embed=res_embed, view=view_inst)

        async def on_cancel(cancel_inter: discord.Interaction, view_inst):
            bot.sleep_timer.cancel_timer(guild_id)
            res_embed = styled_embed(
                title="🛑 Sleep Timer Dibatalkan",
                description="Timer tidur telah dimatikan. Musik akan terus berputar secara normal.",
                color=Theme.INFO,
            )
            await cancel_inter.response.edit_message(embed=res_embed, view=view_inst)

        view = SleepTimerSelectView(interaction.user, current_session, on_set, on_cancel)
        await interaction.response.send_message(embed=embed, view=view)
        return

    action_val = tindakan.value if tindakan else "kick_user"

    if menit < 1 or menit > 360:
        await interaction.response.send_message("❌ Durasi harus antara 1 sampai 360 menit (maksimal 6 jam)!", ephemeral=True)
        return

    session = bot.sleep_timer.start_timer(
        guild_id=guild_id,
        minutes=menit,
        action=action_val,
        text_channel=interaction.channel,
        started_by=interaction.user,
        on_trigger_callback=handle_sleep_timer_trigger,
    )

    if action_val == "kick_user":
        action_text = "Hentikan musik & Disconnect saya dari Voice"
    elif action_val == "leave":
        action_text = "Hentikan musik & Keluar Voice"
    else:
        action_text = "Hentikan musik (Standby 24/7)"

    embed = discord.Embed(
        title="",
        description=(
            "### 🌙 Sleep Timer Diaktifkan\n"
            f"{Theme.SEPARATOR_THIN}\n\n"
            f"⏱️ **Durasi:** `{menit} menit` (berakhir dalam `{session.remaining_str}`)\n"
            f"🎯 **Tindakan:** `{action_text}`\n\n"
            f"💡 *Gunakan `/sleep-cancel` jika ingin membatalkan timer kapan saja.*"
        ),
        color=0x34495E,
    )
    embed.set_footer(text=f"Diminta oleh {interaction.user.display_name}  •  {Theme.BRAND_NAME}")
    await interaction.response.send_message(embed=embed)


async def _execute_sleeptimer_cancel(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    cancelled = bot.sleep_timer.cancel_timer(guild_id)

    if not cancelled:
        await interaction.response.send_message("ℹ️ Tidak ada Sleep Timer yang sedang aktif.", ephemeral=True)
        return

    embed = styled_embed(
        title="🛑 Sleep Timer Dibatalkan",
        description=f"Sleep timer ({cancelled.minutes} menit) telah dibatalkan. Musik akan tetap berputar normal.",
        color=Theme.IDLE,
    )
    await interaction.response.send_message(embed=embed)


async def _execute_sleeptimer_status(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    session = bot.sleep_timer.get_session(guild_id)

    if not session:
        await interaction.response.send_message("ℹ️ Tidak ada Sleep Timer yang sedang aktif saat ini.", ephemeral=True)
        return

    if session.action == "kick_user":
        action_text = f"Hentikan musik & Disconnect {session.started_by.display_name} dari Voice"
    elif session.action == "leave":
        action_text = "Hentikan musik & Keluar Voice Channel"
    else:
        action_text = "Hentikan musik (Standby 24/7)"
    embed = discord.Embed(
        title="",
        description=(
            "### 🌙 Status Sleep Timer\n"
            f"{Theme.SEPARATOR_THIN}\n\n"
            f"⏱️ **Sisa Waktu:** `{session.remaining_str}`\n"
            f"⏳ **Total Durasi:** `{session.minutes} menit`\n"
            f"🎯 **Tindakan:** `{action_text}`\n"
            f"👤 **Dipasang oleh:** {session.started_by.mention}"
        ),
        color=0x34495E,
    )
    embed.set_footer(text=Theme.BRAND_NAME)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="sleeptimer", description="🌙 Pasang timer tidur untuk mematikan musik secara otomatis")
@app_commands.describe(
    menit="Durasi waktu dalam menit (kosongkan untuk membuka menu pilihan durasi)",
    tindakan="Tindakan saat waktu habis: stop musik, disconnect saya dari voice, atau bot keluar voice",
)
@app_commands.choices(
    tindakan=[
        app_commands.Choice(name="💤 Hentikan musik & disconnect saya dari voice", value="kick_user"),
        app_commands.Choice(name="⏹️ Hentikan musik saja (bot tetap standby 24/7)", value="stop"),
        app_commands.Choice(name="👋 Hentikan musik & bot keluar dari voice", value="leave"),
    ]
)
async def cmd_sleeptimer(
    interaction: discord.Interaction,
    menit: Optional[int] = None,
    tindakan: Optional[app_commands.Choice[str]] = None,
):
    await _execute_sleeptimer(interaction, menit, tindakan)


@bot.tree.command(name="sleep", description="🌙 Pasang timer tidur otomatis (stop musik / disconnect kamu saat tidur)")
@app_commands.describe(
    menit="Durasi waktu dalam menit (kosongkan untuk membuka menu pilihan durasi)",
    tindakan="Tindakan saat waktu habis: stop musik, disconnect saya dari voice, atau bot keluar voice",
)
@app_commands.choices(
    tindakan=[
        app_commands.Choice(name="💤 Hentikan musik & disconnect saya dari voice", value="kick_user"),
        app_commands.Choice(name="⏹️ Hentikan musik saja (bot tetap standby 24/7)", value="stop"),
        app_commands.Choice(name="👋 Hentikan musik & bot keluar dari voice", value="leave"),
    ]
)
async def cmd_sleep(
    interaction: discord.Interaction,
    menit: Optional[int] = None,
    tindakan: Optional[app_commands.Choice[str]] = None,
):
    await _execute_sleeptimer(interaction, menit, tindakan)


@bot.tree.command(name="sleep-cancel", description="🛑 Batalkan sleep timer yang sedang berjalan")
async def cmd_sleep_cancel(interaction: discord.Interaction):
    await _execute_sleeptimer_cancel(interaction)


@bot.tree.command(name="sleep-status", description="📊 Cek sisa waktu sleep timer aktif")
async def cmd_sleep_status(interaction: discord.Interaction):
    await _execute_sleeptimer_status(interaction)


@bot.tree.command(name="sleeptimer-cancel", description="🛑 Batalkan sleep timer yang sedang berjalan")
async def cmd_sleeptimer_cancel(interaction: discord.Interaction):
    await _execute_sleeptimer_cancel(interaction)


@bot.tree.command(name="sleeptimer-status", description="📊 Cek sisa waktu sleep timer aktif")
async def cmd_sleeptimer_status(interaction: discord.Interaction):
    await _execute_sleeptimer_status(interaction)


@bot.tree.command(name="sync", description="⚡ Sinkronisasi ulang semua slash commands ke server ini secara instan")
async def cmd_sync(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ Hanya dapat digunakan di dalam server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        bot.tree.copy_global_to(guild=interaction.guild)
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.followup.send(
            f"⚡ **Berhasil menyinkronkan {len(synced)} slash commands ke server ini!**\n"
            f"Ketik `/sleep` atau `/play` sekarang — autocomplete sudah langsung aktif.",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Gagal sinkronisasi: `{e}`", ephemeral=True)


# ─── Slash Commands: Voice Activity Tracker & Leaderboard ────────────
@bot.tree.command(name="voicetop", description="🏆 Papan peringkat member teraktif di voice channel")
@app_commands.describe(limit="Jumlah member yang ditampilkan (default: 10)")
async def cmd_voicetop(interaction: discord.Interaction, limit: Optional[int] = 10):
    limit = max(1, min(25, limit))
    rows = await database.get_top_users(interaction.guild.id, limit)

    if not rows:
        await interaction.response.send_message("📊 Belum ada data aktivitas voice yang tercatat.", ephemeral=True)
        return

    embed = discord.Embed(
        title="",
        description=(
            f"### 🏆 Leaderboard Voice Channel\n"
            f"{Theme.SEPARATOR_THIN}\n"
            f"Top **{len(rows)}** member di server **{interaction.guild.name}**:\n"
        ),
        color=Theme.LEADERBOARD,
    )

    medals = ["🥇", "🥈", "🥉"]
    lb_lines = []
    for i, (user_id, total_sec, today_sec) in enumerate(rows):
        member = interaction.guild.get_member(user_id)
        name = member.display_name if member else f"User {user_id}"
        badge = medals[i] if i < 3 else f"`#{i+1}`"

        total_fmt = format_duration(total_sec)
        today_fmt = format_duration(today_sec)

        lb_lines.append(f"{badge} **{name}**\n  ⏱️ Total: `{total_fmt}` · Hari ini: `{today_fmt}`")

    add_safe_fields(embed, name="🏆 Peringkat", lines=lb_lines, max_chars=950, inline=False)
    embed.set_footer(text=f"Data dihitung otomatis dari voice activity  •  {Theme.BRAND_NAME}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="voicetime", description="⏱️ Cek durasi waktu kamu atau member lain di voice channel")
@app_commands.describe(member="Member yang ingin dicek (default: kamu sendiri)")
async def cmd_voicetime(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target = member or interaction.user
    stats = await database.get_user_stats(target.id, interaction.guild.id)

    if not stats:
        await interaction.response.send_message(
            f"ℹ️ Belum ada catatan aktivitas voice untuk **{target.display_name}**.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="",
        description=(
            f"### ⏱️ Statistik Voice — {target.display_name}\n"
            f"{Theme.SEPARATOR_THIN}"
        ),
        color=Theme.INFO,
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="🏆 Peringkat", value=f"**#{stats['rank']}**", inline=True)
    embed.add_field(name="⏳ Total", value=f"**{format_duration(stats['total_seconds'])}**", inline=True)
    embed.add_field(name="📅 Hari Ini", value=f"**{format_duration(stats['today_seconds'])}**", inline=True)
    embed.set_footer(text=Theme.BRAND_NAME)

    await interaction.response.send_message(embed=embed)


# ─── Slash Commands: Channel Management & 24/7 Keep-Online ───────────
@bot.tree.command(name="join", description="🎙️ Bot join ke voice channel kamu dan standby 24/7 (AutoPlay)")
async def cmd_join(interaction: discord.Interaction):
    vc = await ensure_voice_connection(interaction)
    if vc:
        guild_id = interaction.guild.id
        queue = bot.get_queue(guild_id)
        queue.text_channel = interaction.channel
        queue.manual_stopped = False

        started_autoplay = False
        if queue.autoplay and not vc.is_playing() and not vc.is_paused() and not queue.queue:
            asyncio.create_task(handle_autoplay(guild_id))
            started_autoplay = True

        embed = discord.Embed(
            title="",
            description=(
                f"### 🎙️ Voice Online 24/7\n"
                f"{Theme.SEPARATOR_THIN}\n"
                f"Terhubung ke **{vc.channel.name}**\n"
                f"Auto-reconnect aktif — bot tetap online!"
            ),
            color=Theme.SUCCESS,
        )
        if started_autoplay:
            embed.add_field(
                name="📻 AutoPlay",
                value="Sedang memuat musik rekomendasi otomatis...",
                inline=False,
            )
        embed.set_footer(text=f"/autoplay on/off · /leave untuk keluar  •  {Theme.BRAND_NAME}")
        await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leave", description="👋 Bot keluar dari voice channel")
async def cmd_leave(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = bot.get_guild_voice_client(guild_id)

    if not vc or not vc.is_connected():
        await interaction.response.send_message("ℹ️ Bot tidak sedang berada di voice channel.", ephemeral=True)
        return

    # Hapus dari auto-reconnect tracking
    bot.target_channels.pop(guild_id, None)
    bot.connected_since.pop(guild_id, None)
    bot.voice_clients_dict.pop(guild_id, None)

    # Stop pomodoro & musik jika ada
    bot.pomodoro.stop_session(guild_id)
    bot.get_queue(guild_id).clear()

    if vc and vc.channel:
        await update_voice_channel_status(vc.channel.id, None)
    await update_bot_presence(None)

    await vc.disconnect()
    await interaction.response.send_message("👋 Bot telah keluar dari voice channel.")


@bot.tree.command(name="status", description="📊 Cek status bot, uptime, latency, dan queue")
async def cmd_status(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = bot.get_guild_voice_client(guild_id)
    queue = bot.get_queue(guild_id)

    is_online = vc and vc.is_connected()
    embed = discord.Embed(
        title="",
        description=(
            f"### 📊 Status Bot\n"
            f"{Theme.SEPARATOR_THIN}"
        ),
        color=Theme.SUCCESS if is_online else Theme.IDLE,
    )
    embed.add_field(name="🏎️ Ping", value=f"`{round(bot.latency * 1000)}ms`", inline=True)

    if is_online:
        since = bot.connected_since.get(guild_id)
        if since:
            uptime = datetime.now(timezone.utc) - since
            h, rem = divmod(int(uptime.total_seconds()), 3600)
            m, s = divmod(rem, 60)
            uptime_str = f"`{h}h {m}m {s}s`"
        else:
            uptime_str = "`Baru saja`"

        embed.add_field(name="🟢 Voice", value="Online 24/7", inline=True)
        embed.add_field(name="📡 Channel", value=vc.channel.mention, inline=True)
        embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=True)
        music_status = "▶️ Memutar" if vc.is_playing() else ("⏸️ Dijeda" if vc.is_paused() else "⏹️ Idle")
        embed.add_field(name="🎵 Musik", value=music_status, inline=True)
        embed.add_field(name="📜 Antrean", value=f"`{len(queue.queue)} lagu`", inline=True)
    else:
        embed.add_field(name="🔴 Voice", value="Offline", inline=True)

    embed.set_footer(text=Theme.BRAND_NAME)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="voicestatus", description="📝 Atur teks status yang tampil di bawah nama Voice Channel")
@app_commands.describe(teks="Teks status voice channel (kosongkan untuk membersihkan/reset)")
async def cmd_voicestatus(interaction: discord.Interaction, teks: Optional[str] = None):
    vc = bot.get_guild_voice_client(interaction.guild.id)
    channel = None
    if vc and vc.channel:
        channel = vc.channel
    elif interaction.user.voice and interaction.user.voice.channel:
        channel = interaction.user.voice.channel
    else:
        await interaction.response.send_message("❌ Kamu atau bot harus berada di voice channel terlebih dahulu!", ephemeral=True)
        return

    await update_voice_channel_status(channel.id, teks)
    if teks:
        await interaction.response.send_message(f"✅ Status Voice Channel **{channel.name}** berhasil diatur ke:\n> 💬 **{teks}**")
    else:
        await interaction.response.send_message(f"🧹 Status Voice Channel **{channel.name}** telah dibersihkan.")


@bot.tree.command(name="help", description="📖 Panduan lengkap perintah dan fitur bot")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="",
        description=(
            f"### 📖 Panduan Bot Musik & Companion\n"
            f"{Theme.SEPARATOR}\n"
            f"Daftar lengkap fitur & perintah yang tersedia:"
        ),
        color=Theme.PLAYING,
    )

    embed.add_field(
        name="🎵 Pemutar Musik & AI DJ",
        value=(
            "`/play` — Putar dari judul/URL\n"
            "`/search` — Cari & pilih dari dropdown\n"
            "`/lyrics` — Cari lirik lagu (Auto / Judul)\n"
            "`/filter` — Efek audio DSP (Bass, Nightcore, Slowed, 8D)\n"
            "`/aiplaylist` — AI racik playlist dari mood\n"
            "`/aidj` — Shortcut AI DJ cepat\n"
            "`/radio` — Auto-Playlist 24/7 per genre\n"
            "`/genre` — Menu interaktif genre\n"
            "`/skip` · `/pause` · `/resume` · `/stop`\n"
            "`/queue` · `/nowplaying` · `/volume` · `/loop`\n"
            "`/autoplay` — AutoPlay non-stop 24/7"
        ),
        inline=True,
    )

    embed.add_field(
        name="🌐 Voice 24/7 & Utilitas",
        value=(
            "`/join` — Bot join & standby 24/7\n"
            "`/leave` — Keluar dari voice\n"
            "`/voicestatus` — Atur status voice channel\n"
            "`/status` — Cek ping & uptime\n"
            "\n"
            "🌙 **Sleep Timer**\n"
            "`/sleep` · `/sleeptimer` — Pasang timer tidur\n"
            "`/sleep-cancel` · `/sleep-status`\n"
            "\n"
            "🍅 **Pomodoro**\n"
            "`/pomo-start` — Mulai fokus timer\n"
            "`/pomo-stop` · `/pomo-pause` · `/pomo-resume`\n"
            "`/pomo-status` — Cek progress\n"
            "\n"
            "🏆 **Leaderboard**\n"
            "`/voicetop` — Ranking member teraktif\n"
            "`/voicetime` — Cek waktu voice kamu"
        ),
        inline=True,
    )

    embed.add_field(
        name="🏛️ Tombol Interaktif",
        value=(
            "**Now Playing (3 Baris):**\n"
            "• `Row 0:` `⏸️ Jeda` · `⏭️ Lewati` · `⏹️ Stop` · `🔀 Acak` · `📜 Antrean`\n"
            "• `Row 1:` `🔁 Loop` · `📻 AutoPlay` · `✨ AI Next` · `🎧 Radio` · `📖 Lirik`\n"
            "• `Row 2:` `🎛️ Filter Audio` · `🌙 Sleep Timer`\n\n"
            "**Pomodoro Live Timer:**\n"
            "• `⏸️ Jeda / Lanjut` · `⏭️ Lewati Fase` · `🛑 Selesai`"
        ),
        inline=False,
    )

    embed.set_footer(
        text=f"Ketik / untuk melihat semua perintah  •  {Theme.BRAND_NAME}",
        icon_url=bot.user.display_avatar.url if bot.user else None,
    )

    await interaction.response.send_message(embed=embed)


# ─── Main Execution with Auto Restart ────────────────────────────────
if __name__ == "__main__":
    MAX_RETRIES = 5
    retry_count = 0

    while retry_count < MAX_RETRIES:
        try:
            logger.info(f"🚀 Menjalankan Bot Musik & Voice Companion... (Percobaan {retry_count + 1}/{MAX_RETRIES})")
            bot.run(TOKEN, log_handler=None, reconnect=True)
        except Exception as e:
            retry_count += 1
            logger.error(f"💥 Bot error: {e}")
            if retry_count < MAX_RETRIES:
                wait_time = min(30, 5 * retry_count)
                logger.info(f"🔄 Restarting dalam {wait_time} detik...")
                import time
                time.sleep(wait_time)
                bot = RythmVoiceBot()
            else:
                logger.critical("❌ Batas percobaan restart tercapai.")
                break
        else:
            logger.info("👋 Bot berhenti normal.")
            break
