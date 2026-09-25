"""
🎵 Discord Music & 24/7 Voice Companion Bot
- Full Music Player ala Rythm (Play, Search Dropdown, Queue, Skip, Pause, Resume, Volume, Loop)
- 24/7 Keep-Online Voice Keeper with Auto-Reconnect
- Pomodoro Voice Study Companion with Bell Chimes
- Voice Activity Tracker & Voice Leaderboard (SQLite)
"""

import os
import asyncio
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
    FFMPEG_OPTIONS,
    FFMPEG_EXECUTABLE,
    InterruptableVolumeTransformer,
    format_duration,
)
from pomodoro import PomodoroManager

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

    def get_guild_voice_client(self, guild_id: int) -> Optional[discord.VoiceClient]:
        """Ambil voice client aktif untuk guild."""
        return self.voice_clients_dict.get(guild_id)

    def get_queue(self, guild_id: int) -> GuildMusicQueue:
        """Ambil atau inisialisasi queue musik untuk guild."""
        if guild_id not in self.music_queues:
            self.music_queues[guild_id] = GuildMusicQueue(guild_id)
        return self.music_queues[guild_id]

    async def setup_hook(self):
        """Inisialisasi database dan sinkronisasi slash commands."""
        await database.init_db()
        await self.tree.sync()
        logger.info("✅ Slash commands berhasil di-sync!")

    async def on_ready(self):
        logger.info(f"✅ Bot logged in as {self.user}")
        logger.info(f"🌐 Terhubung ke {len(self.guilds)} server")

        # Mulai background tasks
        if not self.reconnect_loop.is_running():
            self.reconnect_loop.start()
        if not self.flush_voice_activity_loop.is_running():
            self.flush_voice_activity_loop.start()

        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="🎵 /play | 🍅 /pomodoro",
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


# ─── Music Playback Engine ───────────────────────────────────────────
def play_next_song(guild_id: int):
    """Callback pemutar lagu berikutnya dari antrean."""
    vc = bot.get_guild_voice_client(guild_id)
    queue = bot.get_queue(guild_id)

    if not vc or not vc.is_connected():
        return

    song = queue.next_song()
    if not song:
        # Antrean habis
        if queue.text_channel:
            embed = discord.Embed(
                description="⏹️ Antrean lagu sudah selesai. Bot tetap stay di voice channel!",
                color=discord.Color.blue(),
            )
            asyncio.run_coroutine_threadsafe(
                queue.text_channel.send(embed=embed), bot.loop
            )
        return

    try:
        audio_source = discord.FFmpegPCMAudio(
            song.url, executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTIONS
        )
        volume_transformer = InterruptableVolumeTransformer(
            audio_source, volume=queue.volume
        )

        def after_callback(error):
            if error:
                logger.error(f"Error saat playback: {error}")
            play_next_song(guild_id)

        vc.play(volume_transformer, after=after_callback)

        # Kirim embed Now Playing
        if queue.text_channel:
            embed = discord.Embed(
                title="🎶 Sedang Memutar",
                description=f"[{song.title}]({song.webpage_url})",
                color=discord.Color.purple(),
            )
            embed.add_field(name="Durasi", value=song.duration_str, inline=True)
            embed.add_field(name="Artis / Channel", value=song.uploader, inline=True)
            embed.add_field(name="Diminta oleh", value=song.requester.mention, inline=True)
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            embed.set_footer(text=f"Volume: {int(queue.volume * 100)}% | Loop: {'On' if queue.is_looping else 'Off'}")

            asyncio.run_coroutine_threadsafe(
                queue.text_channel.send(embed=embed), bot.loop
            )
    except Exception as e:
        logger.error(f"Gagal memutar lagu: {e}")
        play_next_song(guild_id)


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
            await interaction.followup.send(f"❌ Tidak dapat menemukan atau memutar lagu untuk: `{query}`")
            return

        queue.add(song)

        if not vc.is_playing() and not vc.is_paused():
            play_next_song(guild_id)
            await interaction.followup.send(f"▶️ Memutar: **{song.title}**")
        else:
            embed = discord.Embed(
                title="📥 Ditambahkan ke Antrean",
                description=f"[{song.title}]({song.webpage_url})",
                color=discord.Color.green(),
            )
            embed.add_field(name="Durasi", value=song.duration_str, inline=True)
            embed.add_field(name="Posisi Antrean", value=f"#{len(queue.queue)}", inline=True)
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
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
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Musik dijeda.")
    else:
        await interaction.response.send_message("❌ Tidak ada musik yang sedang diputar!", ephemeral=True)


@bot.tree.command(name="resume", description="▶️ Lanjutkan lagu yang dijeda")
async def cmd_resume(interaction: discord.Interaction):
    vc = bot.get_guild_voice_client(interaction.guild.id)
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Musik dilanjutkan.")
    else:
        await interaction.response.send_message("❌ Musik tidak sedang dijeda!", ephemeral=True)


@bot.tree.command(name="stop", description="⏹️ Hentikan musik dan kosongkan antrean")
async def cmd_stop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = bot.get_guild_voice_client(guild_id)
    queue = bot.get_queue(guild_id)

    queue.clear()
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()

    await interaction.response.send_message("⏹️ Musik dihentikan dan antrean dikosongkan.")


@bot.tree.command(name="queue", description="📜 Lihat daftar antrean lagu")
async def cmd_queue(interaction: discord.Interaction):
    queue = bot.get_queue(interaction.guild.id)

    if not queue.current and not queue.queue:
        await interaction.response.send_message("📭 Antrean musik sedang kosong!", ephemeral=True)
        return

    embed = discord.Embed(
        title="📜 Antrean Musik",
        color=discord.Color.blue(),
    )

    if queue.current:
        embed.add_field(
            name="▶️ Sedang Diputar",
            value=f"[{queue.current.title}]({queue.current.webpage_url}) | `{queue.current.duration_str}`",
            inline=False,
        )

    if queue.queue:
        queue_text = ""
        for i, song in enumerate(queue.queue[:10], start=1):
            queue_text += f"`{i}.` [{song.title}]({song.webpage_url}) (`{song.duration_str}`) - {song.requester.mention}\n"
        if len(queue.queue) > 10:
            queue_text += f"\n*...dan {len(queue.queue) - 10} lagu lainnya.*"
        embed.add_field(name="Daftar Berikutnya", value=queue_text, inline=False)
    else:
        embed.add_field(name="Daftar Berikutnya", value="*Tidak ada lagu berikutnya di antrean.*", inline=False)

    embed.set_footer(text=f"Total: {len(queue.queue) + (1 if queue.current else 0)} lagu | Loop: {'On' if queue.is_looping else 'Off'}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="nowplaying", description="🎧 Info lagu yang sedang berputar")
async def cmd_nowplaying(interaction: discord.Interaction):
    queue = bot.get_queue(interaction.guild.id)
    if not queue.current:
        await interaction.response.send_message("❌ Tidak ada lagu yang sedang diputar!", ephemeral=True)
        return

    song = queue.current
    embed = discord.Embed(
        title="🎧 Now Playing",
        description=f"[{song.title}]({song.webpage_url})",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Artis", value=song.uploader, inline=True)
    embed.add_field(name="Durasi", value=song.duration_str, inline=True)
    embed.add_field(name="Diminta oleh", value=song.requester.mention, inline=True)
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)
    embed.set_footer(text=f"Volume: {int(queue.volume * 100)}% | Loop: {'On' if queue.is_looping else 'Off'}")
    await interaction.response.send_message(embed=embed)


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


# ─── Slash Commands: Pomodoro Voice Companion ────────────────────────
@bot.tree.command(name="pomodoro", description="🍅 Kelola sesi Pomodoro Study Timer")
@app_commands.describe(
    action="Pilihan aksi (start, stop, status)",
    work="Durasi fokus dalam menit (default: 25)",
    break_time="Durasi istirahat dalam menit (default: 5)",
    cycles="Jumlah siklus fokus & istirahat (default: 4)",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Start (Mulai sesi pomodoro)", value="start"),
        app_commands.Choice(name="Stop (Hentikan sesi pomodoro)", value="stop"),
        app_commands.Choice(name="Status (Cek sisa waktu)", value="status"),
    ]
)
async def cmd_pomodoro(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    work: Optional[int] = 25,
    break_time: Optional[int] = 5,
    cycles: Optional[int] = 4,
):
    guild_id = interaction.guild.id

    if action.value == "start":
        # Pastikan bot join voice jika belum
        await ensure_voice_connection(interaction)

        session = bot.pomodoro.start_session(
            guild_id=guild_id,
            text_channel=interaction.channel,
            work_min=work,
            break_min=break_time,
            cycles=cycles,
        )
        embed = discord.Embed(
            title="🍅 Sesi Pomodoro Dibuat!",
            description=(
                f"• **Waktu Fokus:** {work} menit\n"
                f"• **Waktu Istirahat:** {break_time} menit\n"
                f"• **Total Siklus:** {cycles}x\n\n"
                f"🔔 Bot akan membunyikan bel di voice channel saat pergantian sesi!"
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed)

    elif action.value == "stop":
        stopped = bot.pomodoro.stop_session(guild_id)
        if stopped:
            await interaction.response.send_message("🛑 Sesi Pomodoro berhasil dihentikan.")
        else:
            await interaction.response.send_message("ℹ️ Tidak ada sesi Pomodoro yang aktif.", ephemeral=True)

    elif action.value == "status":
        session = bot.pomodoro.get_session(guild_id)
        if not session or not session.is_running:
            await interaction.response.send_message("ℹ️ Tidak ada sesi Pomodoro yang aktif.", ephemeral=True)
            return

        m, s = divmod(session.remaining_seconds, 60)
        phase_str = "⚡ Sesi Fokus" if session.is_work_time else "☕ Sesi Istirahat"
        embed = discord.Embed(
            title="🍅 Status Pomodoro",
            color=discord.Color.red() if session.is_work_time else discord.Color.green(),
        )
        embed.add_field(name="Fase Saat Ini", value=phase_str, inline=True)
        embed.add_field(name="Siklus", value=f"{session.current_cycle}/{session.total_cycles}", inline=True)
        embed.add_field(name="Sisa Waktu", value=f"**{m:02d}:{s:02d}**", inline=True)
        await interaction.response.send_message(embed=embed)


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
        title="🏆 Papan Peringkat Voice Channel",
        description=f"Top {len(rows)} member paling aktif di server **{interaction.guild.name}**:",
        color=discord.Color.gold(),
    )

    medals = ["🥇", "🥈", "🥉"]
    for i, (user_id, total_sec, today_sec) in enumerate(rows):
        member = interaction.guild.get_member(user_id)
        name = member.display_name if member else f"User ID: {user_id}"
        badge = medals[i] if i < 3 else f"`#{i+1}`"

        total_fmt = format_duration(total_sec)
        today_fmt = format_duration(today_sec)

        embed.add_field(
            name=f"{badge} {name}",
            value=f"⏱️ Total: **{total_fmt}** | Hari ini: **{today_fmt}**",
            inline=False,
        )

    embed.set_footer(text="Data dihitung secara otomatis saat kamu berada di voice channel")
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
        title=f"⏱️ Statistik Voice — {target.display_name}",
        color=discord.Color.blue(),
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="🏆 Peringkat Server", value=f"**#{stats['rank']}**", inline=True)
    embed.add_field(name="⏳ Total Waktu", value=f"**{format_duration(stats['total_seconds'])}**", inline=True)
    embed.add_field(name="📅 Hari Ini", value=f"**{format_duration(stats['today_seconds'])}**", inline=True)

    await interaction.response.send_message(embed=embed)


# ─── Slash Commands: Channel Management & 24/7 Keep-Online ───────────
@bot.tree.command(name="join", description="🎙️ Bot join ke voice channel kamu dan standby 24/7")
async def cmd_join(interaction: discord.Interaction):
    vc = await ensure_voice_connection(interaction)
    if vc:
        embed = discord.Embed(
            title="🎙️ Voice Online 24/7",
            description=f"Bot telah terhubung ke **{vc.channel.name}**.\nAuto-reconnect aktif menjaga bot tetap online!",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Gunakan /leave untuk mengeluarkan bot")
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

    await vc.disconnect()
    await interaction.response.send_message("👋 Bot telah keluar dari voice channel.")


@bot.tree.command(name="status", description="📊 Cek status bot, uptime, latency, dan queue")
async def cmd_status(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = bot.get_guild_voice_client(guild_id)
    queue = bot.get_queue(guild_id)

    embed = discord.Embed(title="📊 Status Bot", color=discord.Color.green() if vc else discord.Color.greyple())
    embed.add_field(name="Ping", value=f"{round(bot.latency * 1000)}ms", inline=True)

    if vc and vc.is_connected():
        since = bot.connected_since.get(guild_id)
        if since:
            uptime = datetime.now(timezone.utc) - since
            h, rem = divmod(int(uptime.total_seconds()), 3600)
            m, s = divmod(rem, 60)
            uptime_str = f"{h}h {m}m {s}s"
        else:
            uptime_str = "Baru saja"

        embed.add_field(name="Voice Status", value="🟢 Online (24/7)", inline=True)
        embed.add_field(name="Channel", value=vc.channel.mention, inline=True)
        embed.add_field(name="Voice Uptime", value=uptime_str, inline=True)
        embed.add_field(name="Musik", value="▶️ Memutar" if vc.is_playing() else "⏸️ Idle", inline=True)
        embed.add_field(name="Antrean", value=f"{len(queue.queue)} lagu", inline=True)
    else:
        embed.add_field(name="Voice Status", value="🔴 Offline", inline=True)

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
