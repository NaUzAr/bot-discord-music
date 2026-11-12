import os
import json
import aiosqlite
from datetime import datetime, timezone
import sys
import logging
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("VoiceDB")
DB_PATH = os.getenv("DB_PATH", "voice_tracker.db")

# Fallback path jika berjalan di luar container Docker (misal local Windows / server tanpa /app)
if DB_PATH.startswith("/app") and (not os.path.exists("/app") or sys.platform == "win32"):
    local_data_db = os.path.join(os.path.dirname(__file__), "data", "voice_tracker.db")
    local_root_db = os.path.join(os.path.dirname(__file__), "voice_tracker.db")
    if os.path.exists(local_data_db):
        DB_PATH = local_data_db
    elif os.path.exists(local_root_db):
        DB_PATH = local_root_db
    else:
        DB_PATH = local_data_db

db_dir = os.path.dirname(DB_PATH)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)


async def init_db():
    """Inisialisasi tabel voice stats dan playlists secara async."""
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
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                tracks TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, guild_id, name)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_247 (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS message_log_settings (
                guild_id INTEGER PRIMARY KEY,
                log_channel_id INTEGER NOT NULL,
                log_edits INTEGER DEFAULT 1,
                ignore_bots INTEGER DEFAULT 1,
                tag_on_delete INTEGER DEFAULT 1
            )
            """
        )
        # Migrasi aman untuk DB yang sudah terlanjur dibuat tanpa kolom tag_on_delete
        async with db.execute("PRAGMA table_info(message_log_settings)") as cursor:
            cols = {row[1] for row in await cursor.fetchall()}
        if "tag_on_delete" not in cols:
            await db.execute(
                "ALTER TABLE message_log_settings ADD COLUMN tag_on_delete INTEGER DEFAULT 1"
            )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS message_cache (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                author_name TEXT NOT NULL,
                author_display_name TEXT NOT NULL,
                author_avatar TEXT,
                author_is_bot INTEGER DEFAULT 0,
                content TEXT,
                attachments_json TEXT,
                stickers_json TEXT,
                created_at REAL NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_message_cache_created ON message_cache(created_at)"
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


# ─── Playlist CRUD ───────────────────────────────────────────────────
async def save_playlist(
    user_id: int, guild_id: int, name: str, tracks: List[Dict[str, Any]]
) -> bool:
    """Menyimpan playlist (INSERT OR REPLACE jika nama sudah ada)."""
    created_at = datetime.now(timezone.utc).isoformat()
    tracks_json = json.dumps(tracks, ensure_ascii=False)

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO playlists (user_id, guild_id, name, tracks, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, guild_id, name, tracks_json, created_at),
            )
            await db.commit()
        return True
    except Exception as e:
        logger.error(f"Gagal menyimpan playlist '{name}': {e}")
        return False


async def load_playlist(
    user_id: int, guild_id: int, name: str
) -> Optional[List[Dict[str, Any]]]:
    """Memuat data lagu dari playlist berdasarkan nama.
    Mencari milik user terlebih dahulu, jika tidak ada mencoba mencari playlist dengan nama sama di guild.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT tracks FROM playlists WHERE user_id = ? AND guild_id = ? AND name = ?",
                (user_id, guild_id, name),
            ) as cursor:
                row = await cursor.fetchone()

            # Fallback jika playlist tersimpan di server oleh member lain
            if not row:
                async with db.execute(
                    "SELECT tracks FROM playlists WHERE guild_id = ? AND LOWER(name) = LOWER(?) ORDER BY id DESC LIMIT 1",
                    (guild_id, name),
                ) as cursor:
                    row = await cursor.fetchone()

        if row:
            return json.loads(row[0])
        return None
    except Exception as e:
        logger.error(f"Gagal memuat playlist '{name}': {e}")
        return None


async def get_playlists(user_id: int, guild_id: int) -> List[Dict[str, Any]]:
    """Mengambil daftar semua playlist milik user atau yang tersedia di guild tertentu."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT name, tracks, created_at FROM playlists 
                WHERE user_id = ? AND guild_id = ? 
                UNION
                SELECT name, tracks, created_at FROM playlists 
                WHERE guild_id = ?
                ORDER BY created_at DESC
                """,
                (user_id, guild_id, guild_id),
            ) as cursor:
                rows = await cursor.fetchall()

        results = []
        seen_names = set()
        for name, tracks_json, created_at in rows:
            if name in seen_names:
                continue
            seen_names.add(name)
            tracks = json.loads(tracks_json)
            results.append(
                {
                    "name": name,
                    "track_count": len(tracks),
                    "created_at": created_at,
                }
            )
        return results
    except Exception as e:
        logger.error(f"Gagal mengambil daftar playlist: {e}")
        return []


async def delete_playlist(user_id: int, guild_id: int, name: str) -> bool:
    """Menghapus playlist berdasarkan nama."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "DELETE FROM playlists WHERE user_id = ? AND guild_id = ? AND name = ?",
                (user_id, guild_id, name),
            )
            await db.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Gagal menghapus playlist '{name}': {e}")
        return False


# ─── 24/7 Voice Channel Persistent Settings ──────────────────────────
async def save_247_channel(guild_id: int, channel_id: int):
    """Menyimpan voice channel target untuk 24/7 stay di SQLite."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO voice_247 (guild_id, channel_id) VALUES (?, ?)",
                (guild_id, channel_id),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Gagal menyimpan 24/7 voice channel: {e}")


async def remove_247_channel(guild_id: int):
    """Menghapus setting 24/7 stay untuk guild ini."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM voice_247 WHERE guild_id = ?", (guild_id,))
            await db.commit()
    except Exception as e:
        logger.error(f"Gagal menghapus 24/7 voice channel: {e}")


async def get_all_247_channels() -> Dict[int, int]:
    """Mengambil semua voice channel target 24/7 yang tersimpan."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT guild_id, channel_id FROM voice_247") as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}
    except Exception as e:
        logger.error(f"Gagal memuat 24/7 voice channels: {e}")
        return {}


# ─── Message Delete & Edit Log Settings + Persistent Cache ───────────
async def set_message_log_config(
    guild_id: int,
    log_channel_id: int,
    log_edits: bool = True,
    ignore_bots: bool = True,
    tag_on_delete: bool = True,
):
    """Menyimpan konfigurasi channel log hapus/edit pesan untuk server."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO message_log_settings
                (guild_id, log_channel_id, log_edits, ignore_bots, tag_on_delete)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    log_channel_id,
                    1 if log_edits else 0,
                    1 if ignore_bots else 0,
                    1 if tag_on_delete else 0,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Gagal menyimpan message log config: {e}")


async def disable_message_log(guild_id: int) -> bool:
    """Menonaktifkan log hapus/edit pesan untuk server ini."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "DELETE FROM message_log_settings WHERE guild_id = ?", (guild_id,)
            )
            await db.commit()
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Gagal menonaktifkan message log: {e}")
        return False


async def get_all_message_log_configs() -> Dict[int, Dict[str, Any]]:
    """Mengambil seluruh konfigurasi log pesan semua server."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT guild_id, log_channel_id, log_edits, ignore_bots, COALESCE(tag_on_delete, 1) FROM message_log_settings"
            ) as cursor:
                rows = await cursor.fetchall()
                return {
                    row[0]: {
                        "guild_id": row[0],
                        "log_channel_id": row[1],
                        "log_edits": bool(row[2]),
                        "ignore_bots": bool(row[3]),
                        "tag_on_delete": bool(row[4]),
                    }
                    for row in rows
                }
    except Exception as e:
        logger.error(f"Gagal memuat message log configs: {e}")
        return {}


def _row_to_cached_msg(row: tuple) -> Dict[str, Any]:
    """Konversi baris SQLite message_cache menjadi dict terstruktur."""
    return {
        "message_id": row[0],
        "guild_id": row[1],
        "channel_id": row[2],
        "author_id": row[3],
        "author_name": row[4],
        "author_display_name": row[5],
        "author_avatar": row[6],
        "author_is_bot": bool(row[7]),
        "content": row[8] or "",
        "attachments": json.loads(row[9]) if row[9] else [],
        "stickers": json.loads(row[10]) if row[10] else [],
        "created_at": row[11],
    }


async def save_cached_message(msg_data: Dict[str, Any]):
    """Menyimpan metadata & isi pesan ke cache persisten SQLite."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO message_cache
                (message_id, guild_id, channel_id, author_id, author_name,
                 author_display_name, author_avatar, author_is_bot, content,
                 attachments_json, stickers_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg_data["message_id"],
                    msg_data["guild_id"],
                    msg_data["channel_id"],
                    msg_data["author_id"],
                    msg_data["author_name"],
                    msg_data["author_display_name"],
                    msg_data.get("author_avatar"),
                    1 if msg_data.get("author_is_bot") else 0,
                    msg_data.get("content", ""),
                    json.dumps(msg_data.get("attachments", [])),
                    json.dumps(msg_data.get("stickers", [])),
                    msg_data["created_at"],
                ),
            )
            await db.commit()
    except Exception as e:
        logger.debug(f"Gagal menyimpan cached message {msg_data.get('message_id')}: {e}")


async def update_cached_message(message_id: int, new_content: str):
    """Memperbarui teks pesan di cache SQLite saat pesan diedit."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE message_cache SET content = ? WHERE message_id = ?",
                (new_content, message_id),
            )
            await db.commit()
    except Exception as e:
        logger.debug(f"Gagal memperbarui cached message {message_id}: {e}")


async def get_cached_message(message_id: int) -> Optional[Dict[str, Any]]:
    """Mengambil pesan dari cache SQLite berdasarkan ID pesan."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT message_id, guild_id, channel_id, author_id, author_name,
                       author_display_name, author_avatar, author_is_bot, content,
                       attachments_json, stickers_json, created_at
                FROM message_cache WHERE message_id = ?
                """,
                (message_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return _row_to_cached_msg(row) if row else None
    except Exception as e:
        logger.debug(f"Gagal membaca cached message {message_id}: {e}")
        return None


async def get_cached_messages_bulk(message_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Mengambil banyak pesan sekaligus dari cache SQLite untuk log bulk delete."""
    if not message_ids:
        return {}
    try:
        placeholders = ",".join("?" for _ in message_ids)
        query = (
            "SELECT message_id, guild_id, channel_id, author_id, author_name, "
            "author_display_name, author_avatar, author_is_bot, content, "
            "attachments_json, stickers_json, created_at "
            f"FROM message_cache WHERE message_id IN ({placeholders})"
        )
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(query, tuple(message_ids)) as cursor:
                rows = await cursor.fetchall()
                return {row[0]: _row_to_cached_msg(row) for row in rows}
    except Exception as e:
        logger.debug(f"Gagal membaca bulk cached messages: {e}")
        return {}


async def delete_cached_messages(message_ids: List[int]):
    """Menghapus pesan dari cache SQLite setelah berhasil di-log."""
    if not message_ids:
        return
    try:
        placeholders = ",".join("?" for _ in message_ids)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                f"DELETE FROM message_cache WHERE message_id IN ({placeholders})",
                tuple(message_ids),
            )
            await db.commit()
    except Exception as e:
        logger.debug(f"Gagal menghapus cached messages: {e}")


async def prune_message_cache(max_rows: int = 5000):
    """Memangkas cache pesan lama agar ukuran database SQLite tetap ringan."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                DELETE FROM message_cache
                WHERE message_id NOT IN (
                    SELECT message_id FROM message_cache
                    ORDER BY created_at DESC LIMIT ?
                )
                """,
                (max_rows,),
            )
            await db.commit()
    except Exception as e:
        logger.debug(f"Gagal memangkas message cache: {e}")


