"""
🌐 Web Dashboard & Healthcheck Endpoint
- Menggunakan aiohttp.web (ringan & terintegrasi langsung dengan event loop asyncio bot)
- Menyediakan endpoint healthcheck /health untuk cloud deployment (Docker, VPS, Railway, Render)
- Menyediakan endpoint /api/stats untuk polling data real-time
- Menyediakan UI Dashboard Web modern bertema dark-glassmorphism dengan live auto-update dan audio visualizer
"""

import os
import time
import html
import logging
from aiohttp import web
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

logger = logging.getLogger("WebDashboard")

START_TIME = time.time()


def create_dashboard_app(bot) -> web.Application:
    app = web.Application()

    def get_stats_data():
        uptime_sec = int(time.time() - START_TIME)
        hours = uptime_sec // 3600
        minutes = (uptime_sec % 3600) // 60
        seconds = uptime_sec % 60
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        ping = round(bot.latency * 1000, 1) if (bot.latency is not None and bot.latency > 0) else 0
        guilds_count = len(bot.guilds)
        voice_count = sum(1 for vc in bot.voice_clients_dict.values() if vc and vc.is_connected())

        playing_tracks = []
        for gid, q in bot.music_queues.items():
            if q.current:
                guild = bot.get_guild(gid)
                gname = guild.name if guild else f"Server #{gid}"
                elapsed = int(q.get_elapsed_seconds())
                elapsed_min, elapsed_sec = divmod(elapsed, 60)
                dur_sec = q.current.duration or 0
                pct = min(100, int((elapsed / dur_sec) * 100)) if dur_sec > 0 else 0

                playing_tracks.append({
                    "guild_id": gid,
                    "guild_name": gname,
                    "title": q.current.title,
                    "uploader": q.current.uploader or "Unknown Artist",
                    "thumbnail": q.current.thumbnail or "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=300",
                    "elapsed_str": f"{elapsed_min:02d}:{elapsed_sec:02d}",
                    "duration_str": q.current.duration_str or "Live",
                    "progress_pct": pct,
                    "queue_count": len(q.queue),
                    "autoplay": bool(q.autoplay),
                })

        return {
            "status": "healthy",
            "bot_user": str(bot.user) if bot.user else "Rythm Companion",
            "uptime_seconds": uptime_sec,
            "uptime_str": uptime_str,
            "latency_ms": ping,
            "guild_count": guilds_count,
            "voice_connections": voice_count,
            "playing_servers": len(playing_tracks),
            "target_channels": {str(k): v for k, v in bot.target_channels.items()},
            "reconnect_attempts": {str(k): v for k, v in bot.reconnect_attempts.items()},
            "tracks": playing_tracks,
        }

    async def handle_health(request):
        data = get_stats_data()
        return web.json_response({
            "status": data["status"],
            "bot_user": data["bot_user"],
            "uptime_seconds": data["uptime_seconds"],
            "latency_ms": data["latency_ms"],
            "guild_count": data["guild_count"],
            "voice_connections": data["voice_connections"],
            "playing_servers": data["playing_servers"],
        })

    async def handle_api_stats(request):
        data = get_stats_data()
        return web.json_response(data)

    async def handle_dashboard(request):
        data = get_stats_data()
        uptime_str = data["uptime_str"]
        ping = data["latency_ms"]
        guilds_count = data["guild_count"]
        voice_count = data["voice_connections"]
        bot_user = html.escape(data["bot_user"])

        playing_cards = []
        for track in data["tracks"]:
            gname = html.escape(track["guild_name"])
            title = html.escape(track["title"][:45] + ("…" if len(track["title"]) > 45 else ""))
            uploader = html.escape(track["uploader"])
            thumb = html.escape(track["thumbnail"])
            elapsed_str = track["elapsed_str"]
            dur_str = track["duration_str"]
            pct = track["progress_pct"]
            q_info = f"<span class='badge-queue'>+ {track['queue_count']} di antrean</span>" if track["queue_count"] > 0 else ""
            ap_info = "<span class='badge-ap'>📻 AutoPlay 24/7</span>" if track["autoplay"] else ""

            playing_cards.append(f"""
            <div class="card" id="card-{track['guild_id']}">
                <div class="thumb-container">
                    <img src="{thumb}" alt="Thumbnail" class="card-thumb" onerror="this.src='https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=300'">
                    <div class="equalizer">
                        <span class="bar bar-1"></span>
                        <span class="bar bar-2"></span>
                        <span class="bar bar-3"></span>
                        <span class="bar bar-4"></span>
                    </div>
                </div>
                <div class="card-info">
                    <div class="badge-row">
                        <span class="badge-guild">📍 {gname}</span>
                        {ap_info}
                        {q_info}
                    </div>
                    <div class="track-title" title="{html.escape(track['title'])}">{title}</div>
                    <div class="track-artist">👤 {uploader}</div>
                    <div class="progress-bar-bg">
                        <div class="progress-bar-fill" style="width: {pct}%"></div>
                    </div>
                    <div class="track-time"><span>{elapsed_str}</span><span>{dur_str}</span></div>
                </div>
            </div>
            """)

        if not playing_cards:
            playing_html = """<div class="empty-state">💤 Saat ini tidak ada lagu yang sedang diputar.<br><span style="font-size:0.85rem; opacity:0.7">Gunakan <code>/play</code> atau <code>/radio</code> di Discord untuk memulai musik 24/7!</span></div>"""
        else:
            playing_html = "".join(playing_cards)

        html_content = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎵 {bot_user} — Live 24/7 Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0b0f19;
            --surface-color: rgba(22, 27, 46, 0.7);
            --surface-border: rgba(255, 255, 255, 0.08);
            --accent-purple: #7950f2;
            --accent-cyan: #22d3ee;
            --accent-pink: #f43f5e;
            --accent-green: #10b981;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: radial-gradient(circle at 15% 15%, rgba(121, 80, 242, 0.15) 0%, transparent 40%),
                        radial-gradient(circle at 85% 85%, rgba(34, 211, 238, 0.12) 0%, transparent 40%),
                        var(--bg-color);
            color: var(--text-primary);
            font-family: 'Outfit', sans-serif;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 30px 20px;
        }}
        .container {{ width: 100%; max-width: 1000px; }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 25px;
            border-bottom: 1px solid var(--surface-border);
            margin-bottom: 30px;
        }}
        .logo-group {{ display: flex; align-items: center; gap: 15px; }}
        .bot-avatar {{
            width: 52px; height: 52px; border-radius: 16px;
            background: linear-gradient(135deg, var(--accent-purple), var(--accent-cyan));
            display: flex; align-items: center; justify-content: center;
            font-size: 26px; box-shadow: 0 8px 24px rgba(121, 80, 242, 0.35);
        }}
        .brand-title {{ font-size: 1.5rem; font-weight: 700; letter-spacing: -0.5px; }}
        .brand-subtitle {{ font-size: 0.85rem; color: var(--text-secondary); }}
        .status-pill {{
            display: inline-flex; align-items: center; gap: 8px;
            background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.3);
            color: var(--accent-green); padding: 8px 16px; border-radius: 999px;
            font-size: 0.85rem; font-weight: 600; transition: all 0.3s ease;
        }}
        .dot {{ width: 8px; height: 8px; background: var(--accent-green); border-radius: 50%; box-shadow: 0 0 10px var(--accent-green); animation: pulse 2s infinite; }}
        @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}

        .stats-grid {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px; margin-bottom: 35px;
        }}
        .stat-card {{
            background: var(--surface-color); backdrop-filter: blur(12px);
            border: 1px solid var(--surface-border); border-radius: 18px;
            padding: 20px; transition: transform 0.2s ease, border-color 0.2s ease;
        }}
        .stat-card:hover {{ transform: translateY(-3px); border-color: rgba(255, 255, 255, 0.18); }}
        .stat-label {{ font-size: 0.8rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
        .stat-value {{ font-size: 1.6rem; font-weight: 800; font-family: 'JetBrains Mono', monospace; }}

        .section-header {{
            font-size: 1.25rem; font-weight: 700; margin-bottom: 20px;
            display: flex; align-items: center; justify-content: space-between;
        }}
        .live-indicator {{
            font-size: 0.75rem; color: var(--accent-cyan);
            font-family: 'JetBrains Mono', monospace;
            display: flex; align-items: center; gap: 6px;
        }}
        .cards-list {{ display: flex; flex-direction: column; gap: 16px; min-height: 120px; }}
        .card {{
            background: var(--surface-color); backdrop-filter: blur(12px);
            border: 1px solid var(--surface-border); border-radius: 18px;
            padding: 16px; display: flex; gap: 20px; align-items: center;
            animation: fadeIn 0.4s ease;
        }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: translateY(0); }} }}

        .thumb-container {{
            position: relative; width: 100px; height: 100px; flex-shrink: 0;
        }}
        .card-thumb {{
            width: 100%; height: 100%; border-radius: 14px; object-fit: cover;
            box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
        }}
        .equalizer {{
            position: absolute; bottom: 8px; right: 8px;
            display: flex; align-items: flex-end; gap: 3px; height: 16px;
            background: rgba(0, 0, 0, 0.6); padding: 3px 5px; border-radius: 6px;
        }}
        .bar {{ width: 3px; background: var(--accent-cyan); border-radius: 2px; animation: eq 1.2s infinite ease-in-out; }}
        .bar-1 {{ height: 40%; animation-delay: 0.1s; }}
        .bar-2 {{ height: 90%; animation-delay: 0.3s; }}
        .bar-3 {{ height: 60%; animation-delay: 0.2s; }}
        .bar-4 {{ height: 80%; animation-delay: 0.4s; }}
        @keyframes eq {{ 0%, 100% {{ height: 25%; }} 50% {{ height: 100%; }} }}

        .card-info {{ flex: 1; min-width: 0; }}
        .badge-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }}
        .badge-guild {{
            font-size: 0.75rem; background: rgba(255, 255, 255, 0.08);
            padding: 4px 10px; border-radius: 6px; color: var(--accent-cyan); font-weight: 600;
        }}
        .badge-ap {{
            font-size: 0.75rem; background: rgba(121, 80, 242, 0.2);
            padding: 4px 10px; border-radius: 6px; color: #a78bfa; font-weight: 600;
        }}
        .badge-queue {{
            font-size: 0.75rem; background: rgba(16, 185, 129, 0.2);
            padding: 4px 10px; border-radius: 6px; color: #34d399; font-weight: 600;
        }}
        .track-title {{
            font-size: 1.1rem; font-weight: 700; margin-bottom: 4px;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }}
        .track-artist {{ font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 12px; }}
        .progress-bar-bg {{
            background: rgba(255, 255, 255, 0.1); border-radius: 999px; height: 6px;
            overflow: hidden; margin-bottom: 6px;
        }}
        .progress-bar-fill {{
            background: linear-gradient(90deg, var(--accent-purple), var(--accent-cyan));
            height: 100%; border-radius: 999px; transition: width 0.8s ease;
        }}
        .track-time {{
            display: flex; justify-content: space-between; font-size: 0.75rem;
            color: var(--text-secondary); font-family: 'JetBrains Mono', monospace;
        }}
        .empty-state {{
            background: var(--surface-color); border: 1px dashed var(--surface-border);
            border-radius: 18px; padding: 40px; text-align: center; color: var(--text-secondary);
            line-height: 1.6;
        }}
        footer {{
            margin-top: auto; padding-top: 40px; text-align: center;
            font-size: 0.85rem; color: var(--text-secondary);
        }}
        code {{
            background: rgba(255, 255, 255, 0.1); padding: 2px 6px; border-radius: 4px;
            color: var(--accent-cyan); font-family: 'JetBrains Mono', monospace;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-group">
                <div class="bot-avatar">🎵</div>
                <div>
                    <div class="brand-title">Rythm & Voice Companion</div>
                    <div class="brand-subtitle">Discord Music 24/7 · AI DJ · Voice Activity Tracker</div>
                </div>
            </div>
            <div class="status-pill" id="statusPill">
                <div class="dot"></div>
                <span id="statusText">SYSTEM ONLINE 24/7</span>
            </div>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">⏱️ Uptime</div>
                <div class="stat-value" id="valUptime">{uptime_str}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">📶 Latency (Ping)</div>
                <div class="stat-value" id="valPing" style="color: var(--accent-cyan);">{ping} ms</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">🌐 Server Terhubung</div>
                <div class="stat-value" id="valGuilds">{guilds_count}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">🔊 Voice Room Aktif</div>
                <div class="stat-value" id="valVoice" style="color: var(--accent-purple);">{voice_count}</div>
            </div>
        </div>

        <div class="section-header">
            <span>🎶 Sedang Diputar Saat Ini (Now Playing)</span>
            <span class="live-indicator"><span class="dot" style="width:6px;height:6px;"></span> LIVE AUTO-SYNC (3s)</span>
        </div>
        <div class="cards-list" id="cardsList">
            {playing_html}
        </div>

        <footer>
            Built for Discord Music & 24/7 Voice Companion • Powered by discord.py & Google Gemini AI
        </footer>
    </div>

    <script>
        function escapeHtml(str) {{
            if (!str) return '';
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }}

        async function updateDashboard() {{
            try {{
                const res = await fetch('/api/stats');
                if (!res.ok) throw new Error('Network error');
                const data = await res.json();

                // 1. Update status pill
                const pill = document.getElementById('statusPill');
                const statusText = document.getElementById('statusText');
                pill.style.background = 'rgba(16, 185, 129, 0.15)';
                pill.style.borderColor = 'rgba(16, 185, 129, 0.3)';
                pill.style.color = 'var(--accent-green)';
                statusText.textContent = 'SYSTEM ONLINE 24/7';

                // 2. Update stat cards
                document.getElementById('valUptime').textContent = data.uptime_str;
                const pingEl = document.getElementById('valPing');
                pingEl.textContent = data.latency_ms + ' ms';
                pingEl.style.color = data.latency_ms < 120 ? 'var(--accent-cyan)' : (data.latency_ms < 250 ? '#facc15' : 'var(--accent-pink)');
                document.getElementById('valGuilds').textContent = data.guild_count;
                document.getElementById('valVoice').textContent = data.voice_connections;

                // 3. Update Now Playing cards
                const container = document.getElementById('cardsList');
                if (!data.tracks || data.tracks.length === 0) {{
                    container.innerHTML = '<div class="empty-state">💤 Saat ini tidak ada lagu yang sedang diputar.<br><span style="font-size:0.85rem; opacity:0.7">Gunakan <code>/play</code> atau <code>/radio</code> di Discord untuk memulai musik 24/7!</span></div>';
                    return;
                }}

                let htmlCards = '';
                for (const t of data.tracks) {{
                    const titleEsc = escapeHtml(t.title);
                    const titleShort = titleEsc.length > 45 ? titleEsc.slice(0, 45) + '…' : titleEsc;
                    const uploaderEsc = escapeHtml(t.uploader);
                    const guildEsc = escapeHtml(t.guild_name);
                    const qInfo = t.queue_count > 0 ? `<span class="badge-queue">+ ${{t.queue_count}} di antrean</span>` : '';
                    const apInfo = t.autoplay ? '<span class="badge-ap">📻 AutoPlay 24/7</span>' : '';

                    htmlCards += `
                    <div class="card" id="card-${{t.guild_id}}">
                        <div class="thumb-container">
                            <img src="${{escapeHtml(t.thumbnail)}}" alt="Thumbnail" class="card-thumb" onerror="this.src='https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=300'">
                            <div class="equalizer">
                                <span class="bar bar-1"></span>
                                <span class="bar bar-2"></span>
                                <span class="bar bar-3"></span>
                                <span class="bar bar-4"></span>
                            </div>
                        </div>
                        <div class="card-info">
                            <div class="badge-row">
                                <span class="badge-guild">📍 ${{guildEsc}}</span>
                                ${{apInfo}}
                                ${{qInfo}}
                            </div>
                            <div class="track-title" title="${{titleEsc}}">${{titleShort}}</div>
                            <div class="track-artist">👤 ${{uploaderEsc}}</div>
                            <div class="progress-bar-bg">
                                <div class="progress-bar-fill" style="width: ${{t.progress_pct}}%"></div>
                            </div>
                            <div class="track-time"><span>${{t.elapsed_str}}</span><span>${{t.duration_str}}</span></div>
                        </div>
                    </div>
                    `;
                }}
                container.innerHTML = htmlCards;

            }} catch (err) {{
                const pill = document.getElementById('statusPill');
                const statusText = document.getElementById('statusText');
                pill.style.background = 'rgba(244, 63, 94, 0.15)';
                pill.style.borderColor = 'rgba(244, 63, 94, 0.3)';
                pill.style.color = 'var(--accent-pink)';
                statusText.textContent = 'RECONNECTING...';
            }}
        }}

        // Poll every 3 seconds for real-time live updates
        setInterval(updateDashboard, 3000);
    </script>
</body>
</html>"""
        return web.Response(text=html_content, content_type="text/html")

    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/stats", handle_api_stats)
    return app


async def start_web_server(bot, host: str = "0.0.0.0", port: int = 8080):
    """Menjalankan server web dashboard sebagai asyncio background task."""
    app = create_dashboard_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
        logger.info(f"🌐 Web Dashboard & Healthcheck aktif di http://{host}:{port}/")
    except Exception as e:
        logger.warning(f"⚠️ Gagal menjalankan Web Dashboard di port {port}: {e}")
