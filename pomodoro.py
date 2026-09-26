"""
🍅 Pomodoro Voice Study Companion
- Teknik Pomodoro standar: Fokus → Short Break → Fokus → ... → Long Break
- Anti-drift timer (berbasis timestamp, bukan sleep counter)
- Audio ducking chime saat pergantian fase
- Progress bar visual per siklus
- Live-updating countdown embed (update tiap 30 detik)
- Pause / Resume support
- Statistik per user
"""

import asyncio
import os
import time
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, field

import discord
from music_player import FFMPEG_EXECUTABLE

logger = logging.getLogger("Pomodoro")

BELL_SOUND_FILE = "pomodoro_bell.wav"

# ─── Konstanta Visual ────────────────────────────────────────────────
PHASE_WORK = "work"
PHASE_SHORT_BREAK = "short_break"
PHASE_LONG_BREAK = "long_break"

PHASE_DISPLAY = {
    PHASE_WORK:        {"emoji": "🔥", "label": "Fokus",           "color": 0xE74C3C},
    PHASE_SHORT_BREAK: {"emoji": "☕", "label": "Istirahat Pendek", "color": 0x27AE60},
    PHASE_LONG_BREAK:  {"emoji": "🌴", "label": "Istirahat Panjang","color": 0x3498DB},
}

BRAND = "🍅 Pomodoro Companion"


def make_cycle_bar(current: int, total: int, is_work: bool) -> str:
    """Buat progress bar siklus: 🟥🟥🟥⬜⬜"""
    filled = "🟥" if is_work else "🟩"
    empty = "⬜"
    bar = ""
    for i in range(1, total + 1):
        if i < current:
            bar += "✅"  # Siklus selesai
        elif i == current:
            bar += filled  # Siklus aktif
        else:
            bar += empty  # Belum dimulai
    return bar


def make_timer_bar(remaining: int, total: int, length: int = 12) -> str:
    """Buat progress bar waktu: ▓▓▓▓▓▓░░░░"""
    if total <= 0:
        return "`" + "░" * length + "`"
    elapsed = total - remaining
    filled = int((elapsed / total) * length)
    filled = min(filled, length)
    bar = "▓" * filled + "░" * (length - filled)
    return f"`{bar}`"


def fmt_time(seconds: int) -> str:
    """Format detik ke MM:SS."""
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


@dataclass
class PomodoroStats:
    """Statistik sesi Pomodoro per user."""
    total_focus_seconds: int = 0
    completed_cycles: int = 0
    sessions_started: int = 0


class PomodoroControlView(discord.ui.View):
    """Tombol interaktif sesi Pomodoro (Pause/Resume, Skip Phase, Stop)."""

    def __init__(self, session: "PomodoroSession"):
        super().__init__(timeout=None)
        self.session = session
        self._sync_state()

    def _sync_state(self):
        if self.session.is_paused:
            self.btn_pause.label = "Lanjut"
            self.btn_pause.emoji = "▶️"
            self.btn_pause.style = discord.ButtonStyle.success
        else:
            self.btn_pause.label = "Jeda"
            self.btn_pause.emoji = "⏸️"
            self.btn_pause.style = discord.ButtonStyle.primary

    @discord.ui.button(label="Jeda", emoji="⏸️", style=discord.ButtonStyle.primary, custom_id="pomo_btn_pause")
    async def btn_pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.session.is_paused:
            self.session.resume()
            self._sync_state()
            embed = self.session._build_phase_embed()
            try:
                await interaction.response.edit_message(embed=embed, view=self)
            except Exception:
                await interaction.response.send_message("▶️ Pomodoro dilanjutkan.", ephemeral=True)
        else:
            self.session.pause()
            self._sync_state()
            embed = self.session._build_phase_embed()
            try:
                await interaction.response.edit_message(embed=embed, view=self)
            except Exception:
                await interaction.response.send_message("⏸️ Pomodoro dijeda.", ephemeral=True)

    @discord.ui.button(label="Lewati Fase", emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="pomo_btn_skip")
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.session.target_end_ts = 0
        await interaction.response.send_message("⏭️ Fase Pomodoro saat ini dilewati!", ephemeral=True)

    @discord.ui.button(label="Selesai", emoji="🛑", style=discord.ButtonStyle.danger, custom_id="pomo_btn_stop")
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        self.session.stop()
        try:
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("🛑 Sesi Pomodoro telah dihentikan.", ephemeral=True)
        except Exception:
            await interaction.response.send_message("🛑 Sesi Pomodoro telah dihentikan.", ephemeral=True)


class PomodoroSession:
    """Sesi Pomodoro per guild dengan anti-drift timer, pause/resume, dan live embed."""

    def __init__(
        self,
        guild_id: int,
        text_channel: discord.TextChannel,
        get_voice_client_func,
        started_by: discord.Member,
        work_min: int = 25,
        short_break_min: int = 5,
        long_break_min: int = 15,
        total_cycles: int = 4,
    ):
        self.guild_id = guild_id
        self.text_channel = text_channel
        self.get_voice_client = get_voice_client_func
        self.started_by = started_by

        self.work_min = max(1, min(120, work_min))
        self.short_break_min = max(1, min(30, short_break_min))
        self.long_break_min = max(1, min(60, long_break_min))
        self.total_cycles = max(1, min(12, total_cycles))

        self.current_cycle = 1
        self.phase = PHASE_WORK
        self.is_running = False
        self.is_paused = False
        self.task: Optional[asyncio.Task] = None

        # Anti-drift timestamps
        self.target_end_ts: float = 0.0
        self.phase_duration_sec: int = 0  # Durasi fase saat ini (untuk progress bar)
        self.pause_remaining: int = 0  # Detik tersisa saat di-pause

        # Live embed & UI View
        self.live_message: Optional[discord.Message] = None
        self.view = PomodoroControlView(self)

        # Stats
        self.focus_seconds_accumulated: int = 0
        self.completed_cycles_count: int = 0

    @property
    def remaining_seconds(self) -> int:
        if self.is_paused:
            return self.pause_remaining
        if not self.is_running or self.target_end_ts <= 0:
            return 0
        return max(0, int(self.target_end_ts - time.time()))

    def pause(self) -> bool:
        """Jeda sesi Pomodoro."""
        if not self.is_running or self.is_paused:
            return False
        self.is_paused = True
        self.pause_remaining = self.remaining_seconds
        return True

    def resume(self) -> bool:
        """Lanjutkan sesi Pomodoro."""
        if not self.is_running or not self.is_paused:
            return False
        self.is_paused = False
        self.target_end_ts = time.time() + self.pause_remaining
        return True

    def _build_phase_embed(self, *, countdown_update: bool = False) -> discord.Embed:
        """Membuat embed untuk fase saat ini."""
        info = PHASE_DISPLAY[self.phase]
        remaining = self.remaining_seconds
        time_str = fmt_time(remaining)
        timer_bar = make_timer_bar(remaining, self.phase_duration_sec)
        cycle_bar = make_cycle_bar(self.current_cycle, self.total_cycles, self.phase == PHASE_WORK)

        pause_indicator = "  ⏸️ **DIJEDA**\n" if self.is_paused else ""

        # Motivational text per phase
        if self.phase == PHASE_WORK:
            subtitle = "Saatnya fokus! Singkirkan distraksi dan selesaikan tugasmu. 💪"
        elif self.phase == PHASE_SHORT_BREAK:
            subtitle = "Istirahat sejenak! Minum air, regangkan badan. ☕"
        else:
            subtitle = "Kamu sudah bekerja keras! Nikmati istirahat panjang. 🌴"

        embed = discord.Embed(
            title="",
            description=(
                f"### 🍅 Pomodoro — {info['emoji']} {info['label']}\n"
                f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n"
                f"{pause_indicator}"
                f"*{subtitle}*\n\n"
                f"⏱️ **Sisa Waktu:** `{time_str}`\n"
                f"{timer_bar}\n\n"
                f"📊 **Siklus:** {cycle_bar}  `{self.current_cycle}/{self.total_cycles}`"
            ),
            color=info["color"],
        )

        # Info tambahan di footer
        parts = [
            f"⏱️ {self.work_min}m fokus",
            f"☕ {self.short_break_min}m istirahat",
            f"🌴 {self.long_break_min}m panjang",
        ]
        embed.set_footer(text=f"{'  ·  '.join(parts)}  •  {BRAND}")
        return embed

    async def _send_or_update_embed(self, embed: discord.Embed, *, new_message: bool = False):
        """Kirim embed baru atau update yang sudah ada."""
        try:
            self.view._sync_state()
            if new_message or not self.live_message:
                self.live_message = await self.text_channel.send(embed=embed, view=self.view)
            else:
                await self.live_message.edit(embed=embed, view=self.view)
        except discord.NotFound:
            # Pesan dihapus, buat baru
            self.live_message = await self.text_channel.send(embed=embed, view=self.view)
        except Exception as e:
            logger.warning(f"Gagal update embed Pomodoro: {e}")

    async def play_chime(self):
        """Memutar bel notifikasi (overlay ducking jika musik aktif)."""
        vc = self.get_voice_client(self.guild_id)
        if not vc or not vc.is_connected():
            return

        if not os.path.exists(BELL_SOUND_FILE):
            logger.warning(f"File bel {BELL_SOUND_FILE} tidak ditemukan!")
            return

        try:
            if vc.is_playing() and hasattr(vc.source, "inject_overlay"):
                bell = discord.FFmpegPCMAudio(BELL_SOUND_FILE, executable=FFMPEG_EXECUTABLE)
                vc.source.inject_overlay(bell)
                logger.info(f"🔔 Chime di-mix (ducking) untuk guild {self.guild_id}")
            elif not vc.is_playing() and not vc.is_paused():
                bell = discord.FFmpegPCMAudio(BELL_SOUND_FILE, executable=FFMPEG_EXECUTABLE)
                vc.play(bell)
                logger.info(f"🔔 Chime standalone untuk guild {self.guild_id}")
        except Exception as e:
            logger.warning(f"Gagal putar chime: {e}")

    async def _run_phase(self, phase: str, duration_min: int):
        """Jalankan satu fase (fokus/istirahat) dengan live countdown."""
        self.phase = phase
        self.phase_duration_sec = duration_min * 60
        self.target_end_ts = time.time() + self.phase_duration_sec

        # Kirim embed baru untuk fase ini
        embed = self._build_phase_embed()
        await self._send_or_update_embed(embed, new_message=True)
        await self.play_chime()

        # Update interval: pertama setelah 30 detik, lalu tiap 30 detik
        last_update = time.time()
        UPDATE_INTERVAL = 30  # detik

        while self.is_running and self.remaining_seconds > 0:
            await asyncio.sleep(1)

            if self.is_paused:
                continue

            # Update embed tiap N detik atau saat < 10 detik tersisa
            now = time.time()
            remaining = self.remaining_seconds
            if (now - last_update >= UPDATE_INTERVAL) or (remaining <= 10 and remaining > 0):
                try:
                    embed = self._build_phase_embed(countdown_update=True)
                    await self._send_or_update_embed(embed)
                    last_update = now
                except Exception:
                    pass

        # Final update: waktu habis
        if self.is_running:
            self.target_end_ts = 0
            if self.phase == PHASE_WORK:
                self.focus_seconds_accumulated += self.phase_duration_sec

    async def run(self):
        """Main loop Pomodoro: Work → Break → Work → ... → Long Break → Done."""
        self.is_running = True

        while self.is_running and self.current_cycle <= self.total_cycles:
            # ─── Fase Fokus ───
            await self._run_phase(PHASE_WORK, self.work_min)
            if not self.is_running:
                return

            self.completed_cycles_count += 1

            # ─── Fase Istirahat ───
            is_last_cycle = self.current_cycle >= self.total_cycles
            if is_last_cycle:
                # Siklus terakhir → Long Break
                await self._run_phase(PHASE_LONG_BREAK, self.long_break_min)
            else:
                # Siklus biasa → Short Break
                await self._run_phase(PHASE_SHORT_BREAK, self.short_break_min)

            if not self.is_running:
                return

            self.current_cycle += 1

        # ─── Semua Siklus Selesai ─────────────────────────────────────
        if self.is_running:
            total_focus = self.focus_seconds_accumulated
            focus_h, focus_rem = divmod(total_focus, 3600)
            focus_m, _ = divmod(focus_rem, 60)
            if focus_h > 0:
                focus_str = f"{focus_h} jam {focus_m} menit"
            else:
                focus_str = f"{focus_m} menit"

            embed = discord.Embed(
                title="",
                description=(
                    f"### 🎉 Pomodoro Selesai!\n"
                    f"─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n\n"
                    f"Semua **{self.total_cycles} siklus** telah selesai!\n\n"
                    f"📊 **Ringkasan:**\n"
                    f"⏱️ Total fokus: **{focus_str}**\n"
                    f"✅ Siklus selesai: **{self.completed_cycles_count}**\n\n"
                    f"Kerja luar biasa, {self.started_by.mention}! 🌟"
                ),
                color=0xF1C40F,
            )
            embed.set_footer(text=BRAND)
            await self.text_channel.send(embed=embed)
            await self.play_chime()

        self.is_running = False

    def stop(self):
        """Hentikan sesi Pomodoro."""
        self.is_running = False
        if hasattr(self, "view") and self.view:
            for child in self.view.children:
                child.disabled = True
            if self.live_message:
                try:
                    asyncio.create_task(self.live_message.edit(view=self.view))
                except Exception:
                    pass
        if self.task and not self.task.done():
            self.task.cancel()

    def get_summary(self) -> dict:
        """Ringkasan sesi untuk stop/status."""
        return {
            "cycle": self.current_cycle,
            "total_cycles": self.total_cycles,
            "phase": self.phase,
            "remaining": self.remaining_seconds,
            "is_paused": self.is_paused,
            "completed": self.completed_cycles_count,
            "focus_seconds": self.focus_seconds_accumulated,
        }


class PomodoroManager:
    """Manager untuk semua sesi Pomodoro di berbagai guild."""

    def __init__(self, get_voice_client_func):
        self.get_voice_client = get_voice_client_func
        self.sessions: Dict[int, PomodoroSession] = {}
        self.user_stats: Dict[int, PomodoroStats] = {}  # user_id → stats

    def start_session(
        self,
        guild_id: int,
        text_channel: discord.TextChannel,
        started_by: discord.Member,
        work_min: int = 25,
        short_break_min: int = 5,
        long_break_min: int = 15,
        cycles: int = 4,
    ) -> PomodoroSession:
        # Hentikan sesi lama jika ada
        if guild_id in self.sessions:
            old = self.sessions[guild_id]
            if old.is_running:
                self._record_stats(old)
            old.stop()

        session = PomodoroSession(
            guild_id=guild_id,
            text_channel=text_channel,
            get_voice_client_func=self.get_voice_client,
            started_by=started_by,
            work_min=work_min,
            short_break_min=short_break_min,
            long_break_min=long_break_min,
            total_cycles=cycles,
        )
        self.sessions[guild_id] = session

        # Track stats
        uid = started_by.id
        if uid not in self.user_stats:
            self.user_stats[uid] = PomodoroStats()
        self.user_stats[uid].sessions_started += 1

        session.task = asyncio.create_task(self._run_and_cleanup(guild_id, session))
        return session

    async def _run_and_cleanup(self, guild_id: int, session: PomodoroSession):
        """Jalankan sesi dan catat stats setelah selesai."""
        try:
            await session.run()
        except asyncio.CancelledError:
            pass
        finally:
            self._record_stats(session)

    def _record_stats(self, session: PomodoroSession):
        """Catat statistik setelah sesi berakhir."""
        uid = session.started_by.id
        if uid not in self.user_stats:
            self.user_stats[uid] = PomodoroStats()
        stats = self.user_stats[uid]
        stats.total_focus_seconds += session.focus_seconds_accumulated
        stats.completed_cycles += session.completed_cycles_count

    def stop_session(self, guild_id: int) -> Optional[dict]:
        """Hentikan sesi dan return summary, atau None jika tidak ada sesi."""
        session = self.sessions.get(guild_id)
        if not session or not session.is_running:
            return None

        summary = session.get_summary()
        self._record_stats(session)
        session.stop()
        del self.sessions[guild_id]
        return summary

    def pause_session(self, guild_id: int) -> bool:
        session = self.sessions.get(guild_id)
        if session and session.is_running:
            return session.pause()
        return False

    def resume_session(self, guild_id: int) -> bool:
        session = self.sessions.get(guild_id)
        if session and session.is_running:
            return session.resume()
        return False

    def get_session(self, guild_id: int) -> Optional[PomodoroSession]:
        return self.sessions.get(guild_id)

    def get_user_stats(self, user_id: int) -> PomodoroStats:
        return self.user_stats.get(user_id, PomodoroStats())
