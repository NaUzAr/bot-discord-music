"""
🌙 Sleep Timer Manager
- Pengatur waktu tidur otomatis untuk voice room Discord
- Mendukung dua opsi tindakan saat timer habis:
  1. 'stop'  : Menghentikan musik dan antrean, bot tetap standby 24/7 di voice channel.
  2. 'leave' : Menghentikan musik dan bot disconnect keluar dari voice channel.
- Anti-drift countdown berbasis timestamp
- Embed notifikasi yang menenangkan saat waktu tidur tiba
"""

import time
import asyncio
import logging
from typing import Optional, Dict, Callable

import discord

logger = logging.getLogger("SleepTimer")


class SleepTimerSession:
    """Sesi Sleep Timer per guild."""

    def __init__(
        self,
        guild_id: int,
        minutes: int,
        action: str,
        text_channel: discord.TextChannel,
        started_by: discord.Member,
        on_trigger_callback: Callable,
    ):
        self.guild_id = guild_id
        self.minutes = minutes
        self.action = action  # "stop" atau "leave"
        self.text_channel = text_channel
        self.started_by = started_by
        self.on_trigger = on_trigger_callback

        self.started_ts = time.time()
        self.duration_seconds = minutes * 60
        self.target_end_ts = self.started_ts + self.duration_seconds

        self.is_active = False
        self.task: Optional[asyncio.Task] = None

    @property
    def remaining_seconds(self) -> int:
        if not self.is_active:
            return 0
        return max(0, int(self.target_end_ts - time.time()))

    @property
    def remaining_str(self) -> str:
        sec = self.remaining_seconds
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h} jam {m} menit"
        elif m > 0:
            return f"{m} menit {s} detik"
        else:
            return f"{s} detik"

    async def _run(self):
        """Loop internal menunggu sampai waktu habis."""
        self.is_active = True
        try:
            while self.is_active and self.remaining_seconds > 0:
                # Sleep in increments agar responsif terhadap cancel
                await asyncio.sleep(min(5, self.remaining_seconds))

            if self.is_active:
                logger.info(f"🌙 Sleep timer triggered for guild {self.guild_id} (action={self.action})")
                await self.on_trigger(self)
        except asyncio.CancelledError:
            pass
        finally:
            self.is_active = False

    def start(self):
        """Mulai countdown sleep timer."""
        self.task = asyncio.create_task(self._run())

    def cancel(self):
        """Batalkan sleep timer."""
        self.is_active = False
        if self.task and not self.task.done():
            self.task.cancel()


class SleepTimerManager:
    """Mengelola sesi sleep timer di berbagai guild."""

    def __init__(self):
        self.sessions: Dict[int, SleepTimerSession] = {}

    def get_session(self, guild_id: int) -> Optional[SleepTimerSession]:
        session = self.sessions.get(guild_id)
        if session and session.is_active:
            return session
        return None

    def start_timer(
        self,
        guild_id: int,
        minutes: int,
        action: str,
        text_channel: discord.TextChannel,
        started_by: discord.Member,
        on_trigger_callback: Callable,
    ) -> SleepTimerSession:
        """Membuat dan memulai sleep timer baru (menggantikan yang lama jika ada)."""
        existing = self.sessions.get(guild_id)
        if existing and existing.is_active:
            existing.cancel()

        session = SleepTimerSession(
            guild_id=guild_id,
            minutes=minutes,
            action=action,
            text_channel=text_channel,
            started_by=started_by,
            on_trigger_callback=on_trigger_callback,
        )
        self.sessions[guild_id] = session
        session.start()
        return session

    def cancel_timer(self, guild_id: int) -> Optional[SleepTimerSession]:
        """Membatalkan timer jika ada."""
        session = self.sessions.pop(guild_id, None)
        if session and session.is_active:
            session.cancel()
            return session
        return None


class SleepTimerSelectView(discord.ui.View):
    """View interaktif dropdown untuk mengatur durasi dan mode Sleep Timer."""

    def __init__(
        self,
        requester: discord.Member,
        current_session: Optional[SleepTimerSession],
        on_set_callback: Callable,
        on_cancel_callback: Callable,
    ):
        super().__init__(timeout=60.0)
        self.requester = requester
        self.on_set_callback = on_set_callback
        self.on_cancel_callback = on_cancel_callback

        options = [
            discord.SelectOption(
                label="15 Menit",
                value="15:kick_user",
                emoji="💤",
                description="Matikan musik & disconnect saya dari voice (15 mnt)",
            ),
            discord.SelectOption(
                label="30 Menit (Rekomendasi)",
                value="30:kick_user",
                emoji="💤",
                description="Matikan musik & disconnect saya dari voice (30 mnt)",
                default=True,
            ),
            discord.SelectOption(
                label="45 Menit",
                value="45:kick_user",
                emoji="💤",
                description="Matikan musik & disconnect saya dari voice (45 mnt)",
            ),
            discord.SelectOption(
                label="60 Menit (1 Jam)",
                value="60:kick_user",
                emoji="💤",
                description="Matikan musik & disconnect saya dari voice (60 mnt)",
            ),
            discord.SelectOption(
                label="90 Menit (1.5 Jam)",
                value="90:kick_user",
                emoji="💤",
                description="Matikan musik & disconnect saya dari voice (90 mnt)",
            ),
            discord.SelectOption(
                label="30 Menit (Hanya Stop Musik)",
                value="30:stop",
                emoji="⏹️",
                description="Musik berhenti, bot & kamu tetap standby di voice",
            ),
            discord.SelectOption(
                label="60 Menit (Hanya Stop Musik)",
                value="60:stop",
                emoji="⏹️",
                description="Musik berhenti, bot & kamu tetap standby di voice",
            ),
            discord.SelectOption(
                label="30 Menit (Bot Keluar Voice)",
                value="30:leave",
                emoji="👋",
                description="Musik berhenti & bot pamit keluar dari voice channel",
            ),
            discord.SelectOption(
                label="60 Menit (Bot Keluar Voice)",
                value="60:leave",
                emoji="👋",
                description="Musik berhenti & bot pamit keluar dari voice channel",
            ),
        ]

        self.select_menu = discord.ui.Select(
            placeholder="🌙 Pilih durasi & tindakan sleep timer...",
            options=options,
            row=0,
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)

        if current_session and current_session.is_active:
            self.btn_cancel = discord.ui.Button(
                label=f"Batalkan Timer ({current_session.remaining_str})",
                emoji="🛑",
                style=discord.ButtonStyle.danger,
                row=1,
            )
            self.btn_cancel.callback = self.cancel_callback
            self.add_item(self.btn_cancel)

        self.btn_close = discord.ui.Button(
            label="Tutup",
            emoji="❌",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        self.btn_close.callback = self.close_callback
        self.add_item(self.btn_close)

    async def select_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "❌ Hanya yang membuka menu yang bisa mengaturnya!", ephemeral=True
            )
            return

        val = self.select_menu.values[0]
        minutes_str, action = val.split(":")
        minutes = int(minutes_str)
        self.stop()
        for item in self.children:
            item.disabled = True
        await self.on_set_callback(interaction, minutes, action, self)

    async def cancel_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "❌ Hanya yang membuka menu yang bisa membatalkannya!", ephemeral=True
            )
            return
        self.stop()
        for item in self.children:
            item.disabled = True
        await self.on_cancel_callback(interaction, self)

    async def close_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "❌ Hanya yang membuka menu yang bisa menutupnya!", ephemeral=True
            )
            return
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Menu Sleep Timer ditutup.", embed=None, view=self
        )
