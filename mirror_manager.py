import io
import re
import asyncio
import logging
from typing import Optional, Dict, Any, List, Set, Callable, Union

import aiohttp
import discord

import database

logger = logging.getLogger("MirrorManager")

WEBHOOK_NAME = "RRAWRRRR Mirror"
WEBHOOK_ID_RE = re.compile(r"/webhooks/(\d+)/")
FORBIDDEN_WEBHOOK_WORDS = ("discord", "clyde")
MAX_MIRROR_ATTACHMENT_BYTES = 8 * 1024 * 1024


def _sanitize_webhook_username(name: str) -> str:
    """Bersihkan nama tampilan agar memenuhi aturan Webhook Discord (1-80 char, bebas kata terlarang)."""
    clean = (name or "Member").strip()
    for word in FORBIDDEN_WEBHOOK_WORDS:
        clean = re.sub(
            word,
            lambda m: m.group(0)[0] + "\u200b" + m.group(0)[1:],
            clean,
            flags=re.IGNORECASE,
        )
    if clean.lower() in ("everyone", "here"):
        clean = clean[0] + "\u200b" + clean[1:]
    if not clean:
        clean = "Member"
    return clean[:80]


def _extract_webhook_id(webhook_url: str) -> Optional[int]:
    """Ekstrak ID numerik Webhook dari URL Discord Webhook."""
    match = WEBHOOK_ID_RE.search(webhook_url or "")
    return int(match.group(1)) if match else None


def _split_chunks(text: str, limit: int = 1990) -> List[str]:
    """Memecah teks panjang (>2000 karakter / Nitro) agar aman dikirim lewat Webhook."""
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


class ChannelMirrorManager:
    """Manajer mirroring pesan real-time antar channel menggunakan Discord Webhook."""

    def __init__(self, ref_lookup: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None):
        self.mirrors_by_id: Dict[int, Dict[str, Any]] = {}
        self.mirrors_by_source: Dict[int, List[Dict[str, Any]]] = {}
        self._own_webhook_ids: Set[int] = set()
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._ref_lookup = ref_lookup

    async def load_mirrors(self):
        """Memuat semua konfigurasi mirror dari SQLite ke index memori O(1)."""
        rows = await database.get_all_channel_mirrors()
        self._rebuild_indexes(rows)
        if rows:
            active_count = sum(1 for r in rows if r["enabled"])
            logger.info(f"🪞 Memuat {len(rows)} aturan mirror channel ({active_count} aktif).")

    def _rebuild_indexes(self, rows: List[Dict[str, Any]]):
        self.mirrors_by_id.clear()
        self.mirrors_by_source.clear()
        self._own_webhook_ids.clear()

        for row in rows:
            self.mirrors_by_id[row["id"]] = row
            wh_id = _extract_webhook_id(row["webhook_url"])
            if wh_id:
                self._own_webhook_ids.add(wh_id)
            if row["enabled"]:
                self.mirrors_by_source.setdefault(row["source_channel_id"], []).append(row)

    async def _refresh_from_db(self):
        rows = await database.get_all_channel_mirrors()
        self._rebuild_indexes(rows)

    async def close(self):
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._http_session

    def has_reverse_mirror(self, source_channel_id: int, target_channel_id: int) -> bool:
        """Cegah loop dua arah (A -> B dan B -> A sekaligus)."""
        reverse_list = self.mirrors_by_source.get(target_channel_id, [])
        return any(m["target_channel_id"] == source_channel_id for m in reverse_list)

    async def get_or_create_webhook(self, target_channel: Union[discord.TextChannel, discord.VoiceChannel]) -> str:
        """Mengambil webhook mirror yang sudah ada di channel tujuan atau membuat baru."""
        existing = await target_channel.webhooks()
        for wh in existing:
            if wh.url and wh.name == WEBHOOK_NAME:
                return wh.url

        new_wh = await target_channel.create_webhook(
            name=WEBHOOK_NAME,
            reason="Channel Mirroring Proxy Webhook",
        )
        return new_wh.url

    async def add_mirror(
        self,
        source_channel: Union[discord.TextChannel, discord.VoiceChannel],
        target_channel: Union[discord.TextChannel, discord.VoiceChannel],
        ignore_bots: bool = True,
        show_source: bool = False,
        created_by: Optional[int] = None,
    ) -> Optional[int]:
        """Membuat webhook di tujuan dan menyimpan konfigurasi mirror ke SQLite."""
        webhook_url = await self.get_or_create_webhook(target_channel)
        mirror_id = await database.add_channel_mirror(
            source_guild_id=source_channel.guild.id,
            source_channel_id=source_channel.id,
            target_guild_id=target_channel.guild.id,
            target_channel_id=target_channel.id,
            webhook_url=webhook_url,
            ignore_bots=ignore_bots,
            show_source=show_source,
            created_by=created_by,
        )
        await self._refresh_from_db()
        return mirror_id

    async def remove_mirror(self, mirror_id: int) -> bool:
        """Menghapus konfigurasi mirror berdasarkan ID."""
        removed = await database.remove_channel_mirror(mirror_id)
        if removed:
            await self._refresh_from_db()
        return removed

    async def toggle_mirror(self, mirror_id: int) -> Optional[bool]:
        """Menjeda atau mengaktifkan kembali aturan mirror. Mengembalikan status baru atau None."""
        cfg = self.mirrors_by_id.get(mirror_id)
        if not cfg:
            return None
        new_state = not cfg["enabled"]
        ok = await database.set_channel_mirror_enabled(mirror_id, new_state)
        if ok:
            await self._refresh_from_db()
            return new_state
        return None

    def get_guild_mirrors(self, guild_id: int) -> List[Dict[str, Any]]:
        """Mengambil daftar mirror yang terkait dengan server ini (sebagai sumber atau tujuan)."""
        return [
            m for m in self.mirrors_by_id.values()
            if m["source_guild_id"] == guild_id or m["target_guild_id"] == guild_id
        ]

    def _build_reply_quote(self, message: discord.Message) -> str:
        """Susun kutipan singkat jika pesan merupakan balasan (reply) dari pesan lain."""
        if not message.reference or not message.reference.message_id:
            return ""

        ref_author = None
        ref_text = ""

        resolved = message.reference.resolved
        if isinstance(resolved, discord.Message):
            ref_author = resolved.author.display_name
            ref_text = resolved.content or ("(lampiran/embed)" if (resolved.attachments or resolved.embeds) else "")
        elif self._ref_lookup:
            cached = self._ref_lookup(message.reference.message_id)
            if cached:
                ref_author = cached.get("author_display_name")
                ref_text = cached.get("content") or ("(lampiran)" if cached.get("attachments") else "")

        if not ref_author:
            return ""

        snippet = ref_text.replace("\n", " ").strip()
        if len(snippet) > 75:
            snippet = snippet[:72] + "..."
        if snippet:
            return f"> ↩️ *Membalas **{ref_author}**: {snippet}*\n"
        return f"> ↩️ *Membalas **{ref_author}***\n"

    async def _collect_attachments(
        self,
        attachments: List[discord.Attachment],
        max_bytes: int,
    ) -> tuple[List[bytes], List[str], List[str]]:
        """Mengunduh lampiran agar aman dikirim ulang sebagai file permanen lewat webhook."""
        raw_blobs: List[bytes] = []
        filenames: List[str] = []
        fallback_links: List[str] = []

        for att in attachments:
            if att.size > max_bytes:
                fallback_links.append(f"📎 [{att.filename}]({att.url}) *(melebihi batas upload)*")
                continue
            try:
                data = await att.read()
                if len(data) <= max_bytes:
                    raw_blobs.append(data)
                    filenames.append(att.filename)
                else:
                    fallback_links.append(f"📎 [{att.filename}]({att.url})")
            except Exception:
                fallback_links.append(f"📎 [{att.filename}]({att.url})")

        return raw_blobs, filenames, fallback_links

    async def handle_message(self, bot: discord.Client, message: discord.Message):
        """Teruskan pesan dari channel sumber ke seluruh target mirror via Webhook."""
        if not message.guild:
            return

        # Anti-loop mutlak: abaikan pesan yang berasal dari webhook milik mirror sendiri
        if message.webhook_id and message.webhook_id in self._own_webhook_ids:
            return

        mirrors = self.mirrors_by_source.get(message.channel.id)
        if not mirrors:
            return

        # Saring mirror yang aktif & sesuai filter ignore_bots
        active_targets = [
            m for m in mirrors
            if not (m["ignore_bots"] and (message.author.bot or message.webhook_id is not None))
        ]
        if not active_targets:
            return

        # Skip pesan kosong tanpa isi apa pun
        if not message.content and not message.attachments and not message.embeds and not message.stickers:
            return

        reply_prefix = self._build_reply_quote(message)
        raw_blobs, filenames, fallback_links = await self._collect_attachments(
            message.attachments, MAX_MIRROR_ATTACHMENT_BYTES
        )

        sticker_notes = []
        for st in message.stickers:
            sticker_notes.append(f"🏷️ *[Stiker: {st.name}]* ({st.url})")

        body_parts = []
        if reply_prefix:
            body_parts.append(reply_prefix.rstrip("\n"))
        if message.content:
            body_parts.append(message.content)
        if sticker_notes:
            body_parts.append("\n".join(sticker_notes))
        if fallback_links:
            body_parts.append("\n".join(fallback_links))

        full_text = "\n".join(body_parts).strip()
        text_chunks = _split_chunks(full_text) if full_text else [""]
        embeds = message.embeds[:10] if message.embeds else []

        base_name = message.author.display_name
        avatar_url = str(message.author.display_avatar.url) if message.author.display_avatar else None
        source_ch_name = getattr(message.channel, "name", str(message.channel.id))

        for mirror_cfg in active_targets:
            asyncio.create_task(
                self._dispatch_to_target(
                    bot=bot,
                    mirror_cfg=mirror_cfg,
                    base_name=base_name,
                    source_ch_name=source_ch_name,
                    avatar_url=avatar_url,
                    text_chunks=text_chunks,
                    raw_blobs=raw_blobs,
                    filenames=filenames,
                    embeds=embeds,
                )
            )

    async def _send_via_webhook(
        self,
        webhook_url: str,
        username: str,
        avatar_url: Optional[str],
        text_chunks: List[str],
        raw_blobs: List[bytes],
        filenames: List[str],
        embeds: List[discord.Embed],
    ):
        """Kirim payload teks, file, dan embed melalui Discord Webhook."""
        session = await self._get_http_session()
        webhook = discord.Webhook.from_url(webhook_url, session=session)

        for idx, chunk in enumerate(text_chunks):
            is_first = idx == 0
            files = (
                [discord.File(io.BytesIO(blob), filename=fname) for blob, fname in zip(raw_blobs, filenames)]
                if is_first and raw_blobs
                else discord.utils.MISSING
            )
            chunk_embeds = embeds if (is_first and embeds) else discord.utils.MISSING
            if not chunk and files is discord.utils.MISSING and chunk_embeds is discord.utils.MISSING:
                continue
            await webhook.send(
                content=chunk or None,
                username=username,
                avatar_url=avatar_url or discord.utils.MISSING,
                files=files,
                embeds=chunk_embeds,
                allowed_mentions=discord.AllowedMentions.none(),
                wait=False,
            )

    async def _dispatch_to_target(
        self,
        bot: discord.Client,
        mirror_cfg: Dict[str, Any],
        base_name: str,
        source_ch_name: str,
        avatar_url: Optional[str],
        text_chunks: List[str],
        raw_blobs: List[bytes],
        filenames: List[str],
        embeds: List[discord.Embed],
    ):
        """Kirim ke 1 target mirror dengan pemulihan otomatis jika webhook sempat dihapus."""
        raw_username = (
            f"{base_name} (#{source_ch_name})"
            if mirror_cfg.get("show_source")
            else base_name
        )
        username = _sanitize_webhook_username(raw_username)

        try:
            await self._send_via_webhook(
                webhook_url=mirror_cfg["webhook_url"],
                username=username,
                avatar_url=avatar_url,
                text_chunks=text_chunks,
                raw_blobs=raw_blobs,
                filenames=filenames,
                embeds=embeds,
            )
        except discord.NotFound:
            # Webhook mungkin terhapus manual -> coba buat ulang otomatis
            target_ch = bot.get_channel(mirror_cfg["target_channel_id"])
            if not isinstance(target_ch, (discord.TextChannel, discord.VoiceChannel)):
                logger.warning(f"Target channel mirror #{mirror_cfg['id']} tidak ditemukan; menonaktifkan mirror.")
                await database.set_channel_mirror_enabled(mirror_cfg["id"], False)
                await self._refresh_from_db()
                return
            try:
                new_url = await self.get_or_create_webhook(target_ch)
                await database.update_channel_mirror_webhook(mirror_cfg["id"], new_url)
                await self._refresh_from_db()
                await self._send_via_webhook(
                    webhook_url=new_url,
                    username=username,
                    avatar_url=avatar_url,
                    text_chunks=text_chunks,
                    raw_blobs=raw_blobs,
                    filenames=filenames,
                    embeds=embeds,
                )
            except Exception as heal_err:
                logger.warning(f"Gagal memulihkan webhook mirror #{mirror_cfg['id']}: {heal_err}")
        except Exception as e:
            logger.warning(f"Gagal mengirim mirror #{mirror_cfg['id']}: {e}")
