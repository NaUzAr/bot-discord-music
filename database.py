import aiosqlite
from datetime import datetime, timezone
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger("VoiceDB")
DB_PATH = "voice_tracker.db"


async def init_db():
    """Inisialisasi tabel voice stats secara async."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_stats (
                user_id INTEGER,
                guild_id INTEGER,
                total_seconds INTEGER DEFAULT 0,
                today_seconds INTEGER DEFAULT 0,
                last_date TEXT,
                PRIMARY KEY (user_id, guild_id)
            )
            """
        )
        await db.commit()
    logger.info("📦 SQLite database initialized (async).")


async def add_voice_time(user_id: int, guild_id: int, seconds: int):
    """Menambahkan durasi voice ke user (total & today) secara non-blocking."""
    if seconds <= 0:
        return

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT total_seconds, today_seconds, last_date FROM voice_stats WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            total_sec, today_sec, last_date = row
            if last_date != today_str:
                today_sec = 0  # Reset jika sudah ganti hari (UTC)

            new_total = total_sec + seconds
            new_today = today_sec + seconds

            await db.execute(
                """
                UPDATE voice_stats 
                SET total_seconds = ?, today_seconds = ?, last_date = ? 
                WHERE user_id = ? AND guild_id = ?
                """,
                (new_total, new_today, today_str, user_id, guild_id),
            )
        else:
            await db.execute(
                """
                INSERT INTO voice_stats (user_id, guild_id, total_seconds, today_seconds, last_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, guild_id, seconds, seconds, today_str),
            )
        await db.commit()


async def add_voice_time_batch(entries: List[tuple]):
    """Menambahkan durasi voice ke banyak user sekaligus dalam 1 transaksi (Batch Update)."""
    if not entries:
        return

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_PATH) as db:
        for user_id, guild_id, seconds in entries:
            if seconds <= 0:
                continue
            async with db.execute(
                "SELECT total_seconds, today_seconds, last_date FROM voice_stats WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                total_sec, today_sec, last_date = row
                if last_date != today_str:
                    today_sec = 0

                new_total = total_sec + seconds
                new_today = today_sec + seconds

                await db.execute(
                    """
                    UPDATE voice_stats 
                    SET total_seconds = ?, today_seconds = ?, last_date = ? 
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (new_total, new_today, today_str, user_id, guild_id),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO voice_stats (user_id, guild_id, total_seconds, today_seconds, last_date)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, guild_id, seconds, seconds, today_str),
                )
        await db.commit()


async def get_top_users(guild_id: int, limit: int = 10) -> List[tuple]:
    """Mengambil top member dengan durasi voice terbanyak di server."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT user_id, total_seconds, today_seconds 
            FROM voice_stats 
            WHERE guild_id = ? 
            ORDER BY total_seconds DESC 
            LIMIT ?
            """,
            (guild_id, limit),
        ) as cursor:
            return await cursor.fetchall()


async def get_user_stats(user_id: int, guild_id: int) -> Optional[Dict[str, Any]]:
    """Mengambil statistik waktu voice seorang member beserta peringkatnya."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT total_seconds, today_seconds, last_date FROM voice_stats WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        total_sec, today_sec, last_date = row
        if last_date != today_str:
            today_sec = 0

        # Hitung rank
        async with db.execute(
            "SELECT COUNT(*) FROM voice_stats WHERE guild_id = ? AND total_seconds > ?",
            (guild_id, total_sec),
        ) as cursor:
            count_row = await cursor.fetchone()
            rank = (count_row[0] if count_row else 0) + 1

        return {
            "total_seconds": total_sec,
            "today_seconds": today_sec,
            "rank": rank,
        }


async def reset_guild_stats(guild_id: int):
    """Mereset data voice stats untuk sebuah server."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM voice_stats WHERE guild_id = ?", (guild_id,))
        await db.commit()
