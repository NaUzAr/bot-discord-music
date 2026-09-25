import asyncio
import os
import time
import logging
from typing import Optional, Dict
import discord
from music_player import FFMPEG_EXECUTABLE

logger = logging.getLogger("Pomodoro")
BELL_SOUND_FILE = "pomodoro_bell.wav"


class PomodoroSession:
    """Mengelola satu sesi Pomodoro per guild dengan anti-drift timer dan audio ducking."""

    def __init__(
        self,
        guild_id: int,
        text_channel: discord.TextChannel,
        get_voice_client_func,
        work_min: int = 25,
        break_min: int = 5,
        total_cycles: int = 4,
    ):
        self.guild_id = guild_id
        self.text_channel = text_channel
        self.get_voice_client = get_voice_client_func
        self.work_min = work_min
        self.break_min = break_min
        self.total_cycles = total_cycles
        self.current_cycle = 1
        self.is_work_time = True
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.target_end_ts: float = 0.0

    @property
    def remaining_seconds(self) -> int:
        """Menghitung sisa detik secara akurat berdasarkan target timestamp (anti-drift)."""
        if not self.is_running or self.target_end_ts <= 0:
            return 0
        return max(0, int(self.target_end_ts - time.time()))

    async def play_chime(self):
        """
        Memutar suara bel notifikasi ke voice channel.
        Jika musik sedang bermain, bel di-inject sebagai overlay (audio ducking).
        Jika bot idle, bel diputar langsung via vc.play().
        """
        vc = self.get_voice_client(self.guild_id)
        if not vc or not vc.is_connected():
            return

        if not os.path.exists(BELL_SOUND_FILE):
            logger.warning(f"File bel {BELL_SOUND_FILE} tidak ditemukan!")
            return

        try:
            # 1. Jika musik sedang aktif dan source mendukung overlay ducking
            if vc.is_playing() and hasattr(vc.source, "inject_overlay"):
                bell_source = discord.FFmpegPCMAudio(BELL_SOUND_FILE, executable=FFMPEG_EXECUTABLE)
                vc.source.inject_overlay(bell_source)
                logger.info(f"🔔 Pomodoro chime di-mix (ducking) di atas musik untuk guild {self.guild_id}")
            # 2. Jika tidak ada audio yang diputar (idle voice)
            elif not vc.is_playing() and not vc.is_paused():
                bell_source = discord.FFmpegPCMAudio(BELL_SOUND_FILE, executable=FFMPEG_EXECUTABLE)
                vc.play(bell_source)
                logger.info(f"🔔 Pomodoro chime diputar standalone untuk guild {self.guild_id}")
        except Exception as e:
            logger.warning(f"Tidak dapat memutar chime pomodoro: {e}")

    async def run(self):
        """Loop utama pomodoro dengan anti-drift timer."""
        self.is_running = True

        while self.is_running and self.current_cycle <= self.total_cycles:
            # ─── Sesi Kerja / Fokus ──────────────────────────────────
            self.is_work_time = True
            self.target_end_ts = time.time() + (self.work_min * 60)

            embed_work = discord.Embed(
                title=f"🍅 Pomodoro Dimulai — Siklus {self.current_cycle}/{self.total_cycles}",
                description=(
                    f"Waktu fokus selama **{self.work_min} menit**.\n"
                    f"Selamat belajar / sprint tugas! 🚀"
                ),
                color=discord.Color.red(),
            )
            embed_work.set_footer(text="Gunakan /pomodoro stop untuk menghentikan • /pomodoro status untuk sisa waktu")
            await self.text_channel.send(embed=embed_work)
            await self.play_chime()

            # Countdown anti-drift
            while self.is_running and time.time() < self.target_end_ts:
                await asyncio.sleep(1)

            if not self.is_running:
                return

            # ─── Sesi Istirahat ──────────────────────────────────────
            self.is_work_time = False
            self.target_end_ts = time.time() + (self.break_min * 60)

            embed_break = discord.Embed(
                title=f"🔔 Sesi Fokus Selesai! — Waktunya Istirahat",
                description=(
                    f"Kerja bagus! Istirahat santai selama **{self.break_min} menit** "
                    f"sebelum lanjut ke siklus berikutnya. ☕"
                ),
                color=discord.Color.green(),
            )
            await self.text_channel.send(embed=embed_break)
            await self.play_chime()

            # Countdown istirahat anti-drift
            while self.is_running and time.time() < self.target_end_ts:
                await asyncio.sleep(1)

            if not self.is_running:
                return

            self.current_cycle += 1

        # ─── Selesai Seluruh Siklus ──────────────────────────────────
        if self.is_running:
            embed_done = discord.Embed(
                title="🎉 Sesi Pomodoro Selesai!",
                description=(
                    f"Kamu telah menyelesaikan total **{self.total_cycles} siklus** fokus. "
                    f"Kerja luar biasa hari ini! 🌟"
                ),
                color=discord.Color.gold(),
            )
            await self.text_channel.send(embed=embed_done)
            await self.play_chime()

        self.is_running = False

    def stop(self):
        """Hentikan sesi pomodoro."""
        self.is_running = False
        if self.task and not self.task.done():
            self.task.cancel()


class PomodoroManager:
    """Manager untuk semua sesi pomodoro di berbagai guild."""

    def __init__(self, get_voice_client_func):
        self.get_voice_client = get_voice_client_func
        self.sessions: Dict[int, PomodoroSession] = {}

    def start_session(
        self,
        guild_id: int,
        text_channel: discord.TextChannel,
        work_min: int,
        break_min: int,
        cycles: int,
    ) -> PomodoroSession:
        # Hentikan sesi lama jika ada
        if guild_id in self.sessions:
            self.sessions[guild_id].stop()

        session = PomodoroSession(
            guild_id=guild_id,
            text_channel=text_channel,
            get_voice_client_func=self.get_voice_client,
            work_min=work_min,
            break_min=break_min,
            total_cycles=cycles,
        )
        self.sessions[guild_id] = session
        session.task = asyncio.create_task(session.run())
        return session

    def stop_session(self, guild_id: int) -> bool:
        session = self.sessions.get(guild_id)
        if session and session.is_running:
            session.stop()
            del self.sessions[guild_id]
            return True
        return False

    def get_session(self, guild_id: int) -> Optional[PomodoroSession]:
        return self.sessions.get(guild_id)
