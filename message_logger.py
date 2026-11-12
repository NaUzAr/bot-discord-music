import io
import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import aiohttp
import discord

import database

logger = logging.getLogger("MessageLogger")

MAX_RAM_CACHE = 5000
MAX_DB_CACHE = 5000
PRUNE_EVERY_N_WRITES = 200
MAX_ATTACHMENT_DOWNLOAD_BYTES = 8 * 1024 * 1024  # 8 MB batas aman upload Discord gratis
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _format_bytes(size: int) -> str:
    """Format ukuran file agar mudah dibaca (B, KB, MB)."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def _truncate_text(text: str, max_len: int = 1000) -> str:
    """Memotong teks agar aman masuk ke embed Discord tanpa error 400."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 18] + "\n... *(terpotong)*"


class MessageLogger:
    """Manajer pencatatan log pesan dihapus & diedit dengan cache RAM + SQLite persisten."""

    def __init__(self, brand_name: str = "by pallpalee"):
        self.brand_name = brand_name
        self.configs: Dict[int, Dict[str, Any]] = {}
        self.ram_cache: OrderedDict[int, Dict[str, Any]] = OrderedDict()
        self._write_count = 0
        self._audit_counts: Dict[int, int] = {}
        self._http_session: Optional[aiohttp.ClientSession] = None

    async def load_configs(self):
        """Memuat konfigurasi channel log semua server dari database SQLite."""
        self.configs = await database.get_all_message_log_configs()
        if self.configs:
            logger.info(f"📝 Memuat {len(self.configs)} konfigurasi log hapus pesan dari SQLite.")

    def get_config(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Ambil konfigurasi log aktif untuk guild."""
        return self.configs.get(guild_id)

    async def set_config(
        self,
        guild_id: int,
        log_channel_id: int,
        log_edits: bool = True,
        ignore_bots: bool = True,
    ):
        """Simpan dan aktifkan konfigurasi log hapus pesan untuk guild."""
        await database.set_message_log_config(
            guild_id=guild_id,
            log_channel_id=log_channel_id,
            log_edits=log_edits,
            ignore_bots=ignore_bots,
        )
        self.configs[guild_id] = {
            "guild_id": guild_id,
            "log_channel_id": log_channel_id,
            "log_edits": log_edits,
            "ignore_bots": ignore_bots,
        }

    async def disable_config(self, guild_id: int) -> bool:
        """Nonaktifkan log hapus pesan untuk guild."""
        self.configs.pop(guild_id, None)
        return await database.disable_message_log(guild_id)

    async def close(self):
        """Tutup sesi HTTP internal saat bot shutdown."""
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    def _serialize_message(self, message: discord.Message) -> Dict[str, Any]:
        """Ekstrak data penting dari objek discord.Message untuk cache."""
        attachments = [
            {
                "filename": att.filename,
                "size": att.size,
                "url": att.url,
                "proxy_url": att.proxy_url,
                "content_type": att.content_type or "",
            }
            for att in message.attachments
        ]
        stickers = [st.name for st in message.stickers]
        avatar_url = str(message.author.display_avatar.url) if message.author.display_avatar else ""

        return {
            "message_id": message.id,
            "guild_id": message.guild.id,
            "channel_id": message.channel.id,
            "author_id": message.author.id,
            "author_name": str(message.author),
            "author_display_name": message.author.display_name,
            "author_avatar": avatar_url,
            "author_is_bot": bool(message.author.bot),
            "content": message.content or "",
            "attachments": attachments,
            "stickers": stickers,
            "created_at": message.created_at.timestamp() if message.created_at else datetime.now(timezone.utc).timestamp(),
        }

    def record_message(self, message: discord.Message):
        """Catat pesan masuk ke RAM cache dan database SQLite (jika server mengaktifkan log)."""
        if not message.guild:
            return

        cfg = self.configs.get(message.guild.id)
        if cfg and message.channel.id == cfg["log_channel_id"]:
            return

        # Skip pesan kosong tanpa teks, lampiran, maupun stiker (misal system join notif)
        if not message.content and not message.attachments and not message.stickers:
            return

        msg_data = self._serialize_message(message)

        # Simpan ke RAM cache dengan batas LRU
        self.ram_cache[message.id] = msg_data
        self.ram_cache.move_to_end(message.id)
        while len(self.ram_cache) > MAX_RAM_CACHE:
            self.ram_cache.popitem(last=False)

        # Persist ke SQLite hanya untuk server yang mengaktifkan log (dan sesuai filter ignore_bots)
        if cfg:
            if cfg["ignore_bots"] and msg_data["author_is_bot"]:
                return
            asyncio.create_task(self._persist_to_db(msg_data))

    async def _persist_to_db(self, msg_data: Dict[str, Any]):
        """Simpan pesan ke SQLite dan pangkas baris lama secara berkala."""
        await database.save_cached_message(msg_data)
        self._write_count += 1
        if self._write_count >= PRUNE_EVERY_N_WRITES:
            self._write_count = 0
            await database.prune_message_cache(MAX_DB_CACHE)

    async def _lookup_message(
        self,
        cached_obj: Optional[discord.Message],
        message_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Cari data pesan secara berurutan: RAM cache -> cache discord.py -> SQLite DB."""
        if message_id in self.ram_cache:
            return self.ram_cache[message_id]
        if cached_obj is not None and cached_obj.guild:
            return self._serialize_message(cached_obj)
        return await database.get_cached_message(message_id)

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=3.5)
            self._http_session = aiohttp.ClientSession(timeout=timeout)
        return self._http_session

    async def _try_recover_images(
        self, attachments: List[Dict[str, Any]]
    ) -> Tuple[List[discord.File], Optional[str]]:
        """
        Mencoba mengunduh gambar yang baru dihapus dari proxy Discord CDN
        agar arsip gambar tetap tampil utuh di channel log.
        """
        recovered_files: List[discord.File] = []
        primary_image_name: Optional[str] = None

        image_atts = [
            att for att in attachments
            if (
                att.get("content_type", "").startswith("image/")
                or att.get("filename", "").lower().endswith(IMAGE_EXTENSIONS)
            )
            and att.get("size", 0) <= MAX_ATTACHMENT_DOWNLOAD_BYTES
        ]

        if not image_atts:
            return recovered_files, primary_image_name

        session = await self._get_http_session()
        for idx, att in enumerate(image_atts[:3]):
            urls_to_try = [u for u in (att.get("proxy_url"), att.get("url")) if u]
            for url in urls_to_try:
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if data and len(data) <= MAX_ATTACHMENT_DOWNLOAD_BYTES:
                                safe_name = f"deleted_{idx}_{att['filename']}"
                                recovered_files.append(discord.File(io.BytesIO(data), filename=safe_name))
                                if primary_image_name is None:
                                    primary_image_name = safe_name
                                break
                except Exception:
                    continue

        return recovered_files, primary_image_name

    async def _find_single_deleter(
        self,
        guild: discord.Guild,
        author_id: Optional[int],
        channel_id: int,
    ) -> Optional[discord.abc.User]:
        """Cek Audit Log Discord untuk mengetahui moderator yang menghapus pesan user lain."""
        if not guild.me or not guild.me.guild_permissions.view_audit_log:
            return None

        # Beri jeda singkat agar Discord selesai mencatat audit log
        await asyncio.sleep(0.55)
        now = datetime.now(timezone.utc)

        try:
            async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.message_delete):
                age = (now - entry.created_at).total_seconds()
                if age > 15:
                    continue

                extra_ch = getattr(entry.extra, "channel", None)
                if extra_ch and extra_ch.id != channel_id:
                    continue
                if author_id and entry.target and entry.target.id != author_id:
                    continue

                current_count = getattr(entry.extra, "count", 1) or 1
                prev_count = self._audit_counts.get(entry.id)

                # Jika entri lama sudah pernah dipakai dan count tidak bertambah, abaikan agar tidak salah tuduh
                if prev_count is not None and current_count <= prev_count and age > 2.5:
                    continue

                self._audit_counts[entry.id] = current_count
                if len(self._audit_counts) > 200:
                    self._audit_counts.clear()
                return entry.user
        except (discord.Forbidden, discord.HTTPException):
            return None
        return None

    async def _find_bulk_deleter(
        self,
        guild: discord.Guild,
        channel_id: int,
    ) -> Optional[discord.abc.User]:
        """Cek Audit Log Discord untuk pelaku bulk message delete (purge)."""
        if not guild.me or not guild.me.guild_permissions.view_audit_log:
            return None

        await asyncio.sleep(0.8)
        now = datetime.now(timezone.utc)
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.message_bulk_delete):
                if (now - entry.created_at).total_seconds() <= 15:
                    if entry.target and entry.target.id == channel_id:
                        return entry.user
        except (discord.Forbidden, discord.HTTPException):
            return None
        return None

    def _resolve_log_channel(
        self,
        bot: discord.Client,
        guild_id: Optional[int],
        deleted_channel_id: int,
    ) -> Optional[discord.TextChannel]:
        """Validasi konfigurasi dan kembalikan channel tujuan log jika aktif dan valid."""
        if not guild_id:
            return None
        cfg = self.configs.get(guild_id)
        if not cfg:
            return None
        if deleted_channel_id == cfg["log_channel_id"]:
            return None

        guild = bot.get_guild(guild_id)
        if not guild:
            return None
        log_channel = guild.get_channel(cfg["log_channel_id"])
        if not isinstance(log_channel, (discord.TextChannel, discord.Thread)):
            return None
        return log_channel

    async def handle_raw_message_delete(
        self,
        bot: discord.Client,
        payload: discord.RawMessageDeleteEvent,
    ):
        """Tangani event penghapusan satu pesan dan kirim detailnya ke channel log."""
        log_channel = self._resolve_log_channel(bot, payload.guild_id, payload.channel_id)
        if not log_channel:
            return

        cfg = self.configs[payload.guild_id]
        msg_data = await self._lookup_message(payload.cached_message, payload.message_id)

        # Bersihkan dari cache agar tidak menumpuk
        self.ram_cache.pop(payload.message_id, None)
        asyncio.create_task(database.delete_cached_messages([payload.message_id]))

        if msg_data and cfg["ignore_bots"] and msg_data["author_is_bot"]:
            return

        guild = log_channel.guild
        now_ts = int(datetime.now(timezone.utc).timestamp())

        if msg_data:
            deleter = await self._find_single_deleter(
                guild, msg_data["author_id"], payload.channel_id
            )
            sent_ts = int(msg_data["created_at"])
            deleter_str = (
                f"{deleter.mention} (`{deleter}`)"
                if deleter
                else "Pengirim sendiri / Tidak tercatat di Audit Log"
            )

            embed = discord.Embed(
                title="🗑️ Pesan Dihapus",
                color=0xE74C3C,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_author(
                name=f"{msg_data['author_display_name']} (@{msg_data['author_name']})",
                icon_url=msg_data.get("author_avatar") or None,
            )
            if msg_data.get("author_avatar"):
                embed.set_thumbnail(url=msg_data["author_avatar"])

            meta_lines = [
                f"**Pengirim:** <@{msg_data['author_id']}> (`{msg_data['author_id']}`)",
                f"**Channel:** <#{payload.channel_id}>",
                f"**Dihapus oleh:** {deleter_str}",
                f"**Dikirim:** <t:{sent_ts}:F> (<t:{sent_ts}:R>)",
            ]
            embed.description = "\n".join(meta_lines)

            extra_files: List[discord.File] = []
            content = msg_data.get("content") or ""
            if content.strip():
                if len(content) <= 1020:
                    embed.add_field(name="💬 Isi Pesan", value=content, inline=False)
                else:
                    embed.add_field(
                        name="💬 Isi Pesan",
                        value=_truncate_text(content, 1000),
                        inline=False,
                    )
                    extra_files.append(
                        discord.File(
                            io.BytesIO(content.encode("utf-8")),
                            filename=f"deleted_msg_{payload.message_id}.txt",
                        )
                    )
            else:
                embed.add_field(name="💬 Isi Pesan", value="*— (Tidak ada teks, hanya lampiran/stiker)*", inline=False)

            attachments = msg_data.get("attachments") or []
            if attachments:
                att_lines = [
                    f"• [`{att['filename']}`]({att['url']}) ({_format_bytes(att['size'])})"
                    for att in attachments
                ]
                embed.add_field(
                    name=f"📎 Lampiran ({len(attachments)})",
                    value=_truncate_text("\n".join(att_lines), 1000),
                    inline=False,
                )
                recovered_files, primary_img = await self._try_recover_images(attachments)
                if primary_img:
                    embed.set_image(url=f"attachment://{primary_img}")
                extra_files.extend(recovered_files)

            stickers = msg_data.get("stickers") or []
            if stickers:
                embed.add_field(
                    name="🏷️ Stiker",
                    value=", ".join(f"`{s}`" for s in stickers),
                    inline=False,
                )

            embed.set_footer(text=f"ID Pesan: {payload.message_id}  •  {self.brand_name}")
            try:
                await log_channel.send(embed=embed, files=extra_files)
            except discord.HTTPException as e:
                logger.warning(f"Gagal mengirim log hapus pesan ke #{log_channel.name}: {e}")
            return

        # Fallback jika pesan dikirim sebelum fitur aktif / sudah keluar dari cache
        deleter = await self._find_single_deleter(guild, None, payload.channel_id)
        deleter_line = f"\n**Dihapus oleh (Audit Log):** {deleter.mention} (`{deleter}`)" if deleter else ""

        embed = discord.Embed(
            title="🗑️ Pesan Dihapus (Tidak Tercache)",
            description=(
                f"**Channel:** <#{payload.channel_id}>\n"
                f"**Waktu Hapus:** <t:{now_ts}:F> (<t:{now_ts}:R>)"
                f"{deleter_line}\n\n"
                f"*Isi pesan tidak tersedia karena pesan dikirim sebelum sistem log aktif atau sudah terlalu lama.*"
            ),
            color=0x95A5A6,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"ID Pesan: {payload.message_id}  •  {self.brand_name}")
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException as e:
            logger.warning(f"Gagal mengirim log uncached delete ke #{log_channel.name}: {e}")

    async def handle_raw_bulk_message_delete(
        self,
        bot: discord.Client,
        payload: discord.RawBulkMessageDeleteEvent,
    ):
        """Tangani event hapus pesan massal (purge/clear chat) dan lampirkan transkripnya."""
        log_channel = self._resolve_log_channel(bot, payload.guild_id, payload.channel_id)
        if not log_channel:
            return

        cfg = self.configs[payload.guild_id]
        msg_ids = list(payload.message_ids)
        cached_map: Dict[int, Dict[str, Any]] = {}

        # 1. Cek payload.cached_messages & RAM cache
        discord_cached_by_id = {m.id: m for m in payload.cached_messages}
        missing_ids: List[int] = []
        for mid in msg_ids:
            if mid in self.ram_cache:
                cached_map[mid] = self.ram_cache.pop(mid)
            elif mid in discord_cached_by_id:
                cached_map[mid] = self._serialize_message(discord_cached_by_id[mid])
            else:
                missing_ids.append(mid)

        # 2. Fallback cek SQLite untuk sisa pesan yang belum ketemu
        if missing_ids:
            db_found = await database.get_cached_messages_bulk(missing_ids)
            cached_map.update(db_found)

        asyncio.create_task(database.delete_cached_messages(msg_ids))

        # Urutkan pesan dari terlama ke terbaru berdasarkan Snowflake ID
        ordered_msgs = [
            cached_map[mid]
            for mid in sorted(cached_map.keys())
            if not (cfg["ignore_bots"] and cached_map[mid]["author_is_bot"])
        ]

        deleter = await self._find_bulk_deleter(log_channel.guild, payload.channel_id)
        deleter_str = f"{deleter.mention} (`{deleter}`)" if deleter else "Tidak tercatat di Audit Log"

        embed = discord.Embed(
            title="🧹 Hapus Pesan Massal (Bulk Delete)",
            description=(
                f"**Channel:** <#{payload.channel_id}>\n"
                f"**Total Pesan Dihapus:** `{len(msg_ids)} pesan` (`{len(ordered_msgs)}` tersimpan di cache)\n"
                f"**Pelaku (Audit Log):** {deleter_str}"
            ),
            color=0xE67E22,
            timestamp=datetime.now(timezone.utc),
        )

        files: List[discord.File] = []
        if ordered_msgs:
            preview_lines = []
            transcript_lines = [
                f"=== TRANSKRIP HAPUS MASSAL (#{payload.channel_id}) ===",
                f"Total dihapus: {len(msg_ids)} | Tercache: {len(ordered_msgs)}",
                f"Waktu log: {datetime.now(timezone.utc).isoformat()}",
                "=" * 55,
                "",
            ]
            for m in ordered_msgs:
                ts_str = datetime.fromtimestamp(m["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                att_info = f" [Lampiran: {', '.join(a['filename'] for a in m['attachments'])}]" if m["attachments"] else ""
                text_body = m["content"] or "(tanpa teks)"
                transcript_lines.append(f"[{ts_str}] {m['author_display_name']} (@{m['author_name']} | {m['author_id']}): {text_body}{att_info}")
                if len(preview_lines) < 5:
                    short_body = text_body.replace("\n", " ")
                    if len(short_body) > 90:
                        short_body = short_body[:87] + "..."
                    preview_lines.append(f"• **{m['author_display_name']}**: {short_body}{att_info}")

            embed.add_field(
                name="📜 Cuplikan Pesan Terakhir",
                value=_truncate_text("\n".join(preview_lines), 1000),
                inline=False,
            )
            transcript_bytes = "\n".join(transcript_lines).encode("utf-8")
            files.append(
                discord.File(
                    io.BytesIO(transcript_bytes),
                    filename=f"bulk_delete_{payload.channel_id}.txt",
                )
            )

        embed.set_footer(text=f"Channel ID: {payload.channel_id}  •  {self.brand_name}")
        try:
            await log_channel.send(embed=embed, files=files)
        except discord.HTTPException as e:
            logger.warning(f"Gagal mengirim log bulk delete ke #{log_channel.name}: {e}")

    async def handle_raw_message_edit(
        self,
        bot: discord.Client,
        payload: discord.RawMessageUpdateEvent,
    ):
        """Tangani event edit pesan dan kirim perbandingan Sebelum/Sesudah ke channel log."""
        if "content" not in payload.data:
            return  # Abaikan update otomatis Discord (seperti render embed link)

        log_channel = self._resolve_log_channel(bot, payload.guild_id, payload.channel_id)
        if not log_channel:
            return

        cfg = self.configs[payload.guild_id]
        new_content = payload.data.get("content") or ""
        old_data = await self._lookup_message(payload.cached_message, payload.message_id)

        if not old_data:
            return
        if cfg["ignore_bots"] and old_data["author_is_bot"]:
            return

        old_content = old_data.get("content") or ""
        if old_content == new_content:
            return

        # Perbarui isi cache dengan versi terbaru agar log hapus pesan tetap akurat meski log edit mati
        old_data["content"] = new_content
        self.ram_cache[payload.message_id] = old_data
        self.ram_cache.move_to_end(payload.message_id)
        asyncio.create_task(database.update_cached_message(payload.message_id, new_content))

        if not cfg.get("log_edits", True):
            return

        jump_url = f"https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id}"
        embed = discord.Embed(
            title="✏️ Pesan Diedit",
            description=(
                f"**Pengirim:** <@{old_data['author_id']}> (`{old_data['author_id']}`)\n"
                f"**Channel:** <#{payload.channel_id}> · [Lompat ke Pesan]({jump_url})"
            ),
            color=0xF39C12,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_author(
            name=f"{old_data['author_display_name']} (@{old_data['author_name']})",
            icon_url=old_data.get("author_avatar") or None,
        )
        embed.add_field(
            name="📜 Sebelum",
            value=_truncate_text(old_content, 1000) if old_content.strip() else "*— (Kosong)*",
            inline=False,
        )
        embed.add_field(
            name="📝 Sesudah",
            value=_truncate_text(new_content, 1000) if new_content.strip() else "*— (Kosong)*",
            inline=False,
        )
        embed.set_footer(text=f"ID Pesan: {payload.message_id}  •  {self.brand_name}")
        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException as e:
            logger.warning(f"Gagal mengirim log edit pesan ke #{log_channel.name}: {e}")
