"""
🤖 Discord Voice Keep-Online Bot
Bot yang tetap stay di voice channel dan bisa dikontrol via slash commands.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN tidak ditemukan di file .env!")
    print("   Buka file .env dan paste token bot kamu.")
    exit(1)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("VoiceKeeper")


class VoiceKeeperBot(discord.Client):
    """Discord bot yang stay di voice channel."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.guilds = True

        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._voice_channels: dict[int, discord.VoiceClient] = {}  # guild_id -> VoiceClient
        self._target_channels: dict[int, int] = {}  # guild_id -> channel_id (for auto-reconnect)
        self._connected_since: dict[int, datetime] = {}  # guild_id -> timestamp

    async def setup_hook(self):
        """Sync slash commands saat bot startup."""
        await self.tree.sync()
        logger.info("✅ Slash commands synced!")

    async def on_ready(self):
        """Event handler ketika bot berhasil login."""
        logger.info(f"✅ Bot logged in as {self.user}")
        logger.info(f"📡 Bot ready — gunakan /join untuk mulai")
        logger.info(f"🌐 Connected to {len(self.guilds)} server(s)")

        # Start auto-reconnect loop
        if not self.reconnect_loop.is_running():
            self.reconnect_loop.start()

        # Set activity status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="🎙️ Voice Online",
            )
        )

    async def on_disconnect(self):
        """Event handler ketika bot kehilangan koneksi WebSocket."""
        logger.warning("⚠️ Bot disconnected dari Discord gateway! Mencoba reconnect...")

    async def on_resumed(self):
        """Event handler ketika bot berhasil resume session."""
        logger.info("🔄 Bot berhasil resume session ke Discord gateway.")

    async def on_error(self, event_method, *args, **kwargs):
        """Global error handler agar bot tidak crash."""
        logger.exception(f"❌ Unhandled exception di event '{event_method}':")

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """Deteksi jika bot di-disconnect paksa dari voice channel."""
        if member.id != self.user.id:
            return

        # Bot was disconnected
        if before.channel is not None and after.channel is None:
            guild_id = before.channel.guild.id
            if guild_id in self._target_channels:
                logger.warning(
                    f"⚠️ Bot disconnected dari {before.channel.name} "
                    f"di server {before.channel.guild.name}. Auto-reconnect..."
                )
                # Clean up stale voice client
                if guild_id in self._voice_channels:
                    del self._voice_channels[guild_id]
                if guild_id in self._connected_since:
                    del self._connected_since[guild_id]

    @tasks.loop(seconds=30)
    async def reconnect_loop(self):
        """Auto-reconnect ke voice channel jika terputus."""
        for guild_id, channel_id in list(self._target_channels.items()):
            guild = self.get_guild(guild_id)
            if not guild:
                continue

            voice_client = self._voice_channels.get(guild_id)

            # Skip jika sudah connected
            if voice_client and voice_client.is_connected():
                continue

            # Coba reconnect
            channel = guild.get_channel(channel_id)
            if not channel:
                logger.error(f"❌ Channel {channel_id} tidak ditemukan, skip reconnect")
                continue

            try:
                vc = await channel.connect(self_deaf=True)
                self._voice_channels[guild_id] = vc
                self._connected_since[guild_id] = datetime.now(timezone.utc)
                logger.info(f"🔄 Auto-reconnected ke {channel.name} di {guild.name}")
            except Exception as e:
                logger.error(f"❌ Gagal reconnect ke {channel.name}: {e}")

    @reconnect_loop.error
    async def reconnect_loop_error(self, error):
        """Handler error untuk reconnect loop agar loop tidak berhenti."""
        logger.exception(f"❌ Error di reconnect_loop, loop akan restart: {error}")
        await asyncio.sleep(10)
        if not self.reconnect_loop.is_running():
            self.reconnect_loop.start()

    @reconnect_loop.before_loop
    async def before_reconnect_loop(self):
        await self.wait_until_ready()


# ─── Inisialisasi Bot ────────────────────────────────────────────────
bot = VoiceKeeperBot()


# ─── Slash Commands ──────────────────────────────────────────────────
@bot.tree.command(name="join", description="🎙️ Bot join ke voice channel kamu")
@app_commands.describe(channel="Voice channel tujuan (opsional, default: channel kamu saat ini)")
async def cmd_join(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel = None,
):
    """Join ke voice channel."""
    # Tentukan target channel
    if channel is None:
        if interaction.user.voice and interaction.user.voice.channel:
            channel = interaction.user.voice.channel
        else:
            await interaction.response.send_message(
                "❌ Kamu harus berada di voice channel, atau sebutkan channel tujuan!",
                ephemeral=True,
            )
            return

    guild_id = interaction.guild.id

    # Cek apakah sudah di voice channel yang sama
    existing_vc = bot._voice_channels.get(guild_id)
    if existing_vc and existing_vc.is_connected():
        if existing_vc.channel.id == channel.id:
            await interaction.response.send_message(
                f"ℹ️ Bot sudah berada di **{channel.name}**!",
                ephemeral=True,
            )
            return
        # Pindah ke channel baru
        await existing_vc.move_to(channel)
        bot._target_channels[guild_id] = channel.id
        bot._connected_since[guild_id] = datetime.now(timezone.utc)
        await interaction.response.send_message(
            f"🔀 Bot pindah ke **{channel.name}**!",
        )
        return

    # Join voice channel baru
    try:
        vc = await channel.connect(self_deaf=True)
        bot._voice_channels[guild_id] = vc
        bot._target_channels[guild_id] = channel.id
        bot._connected_since[guild_id] = datetime.now(timezone.utc)

        embed = discord.Embed(
            title="🎙️ Voice Online!",
            description=f"Bot joined **{channel.name}** dan akan tetap online.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Auto-Reconnect", value="✅ Aktif", inline=True)
        embed.set_footer(text="Gunakan /leave untuk disconnect")

        await interaction.response.send_message(embed=embed)
        logger.info(f"🎙️ Joined {channel.name} di {interaction.guild.name}")

    except discord.errors.Forbidden:
        await interaction.response.send_message(
            "❌ Bot tidak punya permission untuk join channel ini!",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Error: {e}",
            ephemeral=True,
        )


@bot.tree.command(name="leave", description="👋 Bot leave dari voice channel")
async def cmd_leave(interaction: discord.Interaction):
    """Leave voice channel."""
    guild_id = interaction.guild.id
    vc = bot._voice_channels.get(guild_id)

    if not vc or not vc.is_connected():
        await interaction.response.send_message(
            "ℹ️ Bot tidak sedang di voice channel manapun.",
            ephemeral=True,
        )
        return

    channel_name = vc.channel.name

    # Hapus dari tracking supaya tidak auto-reconnect
    bot._target_channels.pop(guild_id, None)
    bot._connected_since.pop(guild_id, None)
    bot._voice_channels.pop(guild_id, None)

    await vc.disconnect()

    embed = discord.Embed(
        title="👋 Disconnected",
        description=f"Bot sudah leave dari **{channel_name}**.",
        color=discord.Color.red(),
    )
    embed.add_field(name="Auto-Reconnect", value="❌ Nonaktif", inline=True)

    await interaction.response.send_message(embed=embed)
    logger.info(f"👋 Left {channel_name} di {interaction.guild.name}")


@bot.tree.command(name="move", description="🔀 Pindahkan bot ke voice channel lain")
@app_commands.describe(channel="Voice channel tujuan")
async def cmd_move(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
):
    """Pindahkan bot ke voice channel lain."""
    guild_id = interaction.guild.id
    vc = bot._voice_channels.get(guild_id)

    if not vc or not vc.is_connected():
        await interaction.response.send_message(
            "❌ Bot belum di voice channel. Gunakan `/join` dulu!",
            ephemeral=True,
        )
        return

    if vc.channel.id == channel.id:
        await interaction.response.send_message(
            f"ℹ️ Bot sudah berada di **{channel.name}**!",
            ephemeral=True,
        )
        return

    old_channel = vc.channel.name
    await vc.move_to(channel)
    bot._target_channels[guild_id] = channel.id
    bot._connected_since[guild_id] = datetime.now(timezone.utc)

    embed = discord.Embed(
        title="🔀 Pindah Channel",
        description=f"Bot pindah dari **{old_channel}** ke **{channel.name}**.",
        color=discord.Color.blue(),
    )
    await interaction.response.send_message(embed=embed)
    logger.info(f"🔀 Moved from {old_channel} to {channel.name}")


@bot.tree.command(name="status", description="📊 Cek status koneksi voice bot")
async def cmd_status(interaction: discord.Interaction):
    """Cek status bot di voice channel."""
    guild_id = interaction.guild.id
    vc = bot._voice_channels.get(guild_id)

    if not vc or not vc.is_connected():
        embed = discord.Embed(
            title="📊 Status Bot",
            description="Bot **tidak** sedang di voice channel.",
            color=discord.Color.greyple(),
        )
        embed.add_field(name="Status", value="🔴 Offline", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    connected_since = bot._connected_since.get(guild_id)
    if connected_since:
        uptime = datetime.now(timezone.utc) - connected_since
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{hours}h {minutes}m {seconds}s"
    else:
        uptime_str = "N/A"

    embed = discord.Embed(
        title="📊 Status Bot",
        color=discord.Color.green(),
    )
    embed.add_field(name="Status", value="🟢 Online", inline=True)
    embed.add_field(name="Channel", value=vc.channel.mention, inline=True)
    embed.add_field(name="Uptime", value=uptime_str, inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="Auto-Reconnect", value="✅ Aktif", inline=True)
    embed.add_field(
        name="Members di Channel",
        value=str(len(vc.channel.members)),
        inline=True,
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ping", description="🏓 Cek latency bot")
async def cmd_ping(interaction: discord.Interaction):
    """Cek latency bot."""
    latency = round(bot.latency * 1000)

    if latency < 100:
        status = "🟢 Excellent"
    elif latency < 200:
        status = "🟡 Good"
    else:
        status = "🔴 High"

    embed = discord.Embed(
        title="🏓 Pong!",
        color=discord.Color.green() if latency < 200 else discord.Color.red(),
    )
    embed.add_field(name="Latency", value=f"**{latency}ms**", inline=True)
    embed.add_field(name="Status", value=status, inline=True)

    await interaction.response.send_message(embed=embed)


# ─── Run Bot ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    MAX_RETRIES = 5
    retry_count = 0

    while retry_count < MAX_RETRIES:
        try:
            logger.info(f"🚀 Starting bot... (attempt {retry_count + 1}/{MAX_RETRIES})")
            bot.run(TOKEN, log_handler=None, reconnect=True)
        except Exception as e:
            retry_count += 1
            logger.error(f"💥 Bot crashed: {e}")
            if retry_count < MAX_RETRIES:
                wait_time = min(30, 5 * retry_count)  # backoff: 5s, 10s, 15s, 20s, 25s
                logger.info(f"🔄 Restarting in {wait_time}s... ({retry_count}/{MAX_RETRIES})")
                import time
                time.sleep(wait_time)
                # Re-create bot instance for clean state
                bot = VoiceKeeperBot()
            else:
                logger.critical("❌ Max retries reached. Bot shutting down.")
                break
        else:
            # bot.run() returned normally (e.g., via /leave or Ctrl+C)
            logger.info("👋 Bot stopped gracefully.")
            break
