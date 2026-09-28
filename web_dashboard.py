"""
🌐 Web Dashboard & Healthcheck Endpoint
- Menggunakan aiohttp.web (ringan & terintegrasi langsung dengan event loop asyncio bot)
- Menyediakan endpoint healthcheck /health untuk cloud deployment (Docker, VPS, Railway, Render)
- Menyediakan UI Dashboard Web modern bertema dark-glassmorphism untuk memantau status bot real-time
"""

import os
import time
import logging
from aiohttp import web
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

logger = logging.getLogger("WebDashboard")

START_TIME = time.time()


def create_dashboard_app(bot) -> web.Application:
    app = web.Application()

    async def handle_health(request):
        uptime_sec = int(time.time() - START_TIME)
        playing_count = sum(
            1 for q in bot.music_queues.values() if q.current is not None
        )
        data = {
            "status": "healthy",
            "bot_user": str(bot.user) if bot.user else "Offline",
            "uptime_seconds": uptime_sec,
            "latency_ms": round(bot.latency * 1000, 1) if bot.latency else 0,
            "guild_count": len(bot.guilds),
            "voice_connections": len(bot.voice_clients_dict),
            "playing_servers": playing_count,
        }
        return web.json_response(data)

    async def handle_dashboard(request):
        uptime_sec = int(time.time() - START_TIME)
        hours = uptime_sec // 3600
        minutes = (uptime_sec % 3600) // 60
        seconds = uptime_sec % 60
        uptime_str = f"{hours}h {minutes}m {seconds}s"

        ping = round(bot.latency * 1000, 1) if bot.latency else 0
        guilds_count = len(bot.guilds)
        voice_count = len(bot.voice_clients_dict)

        # Collect currently playing songs
        playing_cards = []
        for gid, q in bot.music_queues.items():
            if q.current:
                guild = bot.get_guild(gid)
                gname = guild.name if guild else f"Server #{gid}"
                elapsed = int(q.get_elapsed_seconds())
                elapsed_min, elapsed_sec = divmod(elapsed, 60)
                elapsed_str = f"{elapsed_min:02d}:{elapsed_sec:02d}"
                dur_str = q.current.duration_str or "Live"
                title = q.current.title[:45] + ("…" if len(q.current.title) > 45 else "")
                uploader = q.current.uploader or "Unknown Artist"
                thumb = q.current.thumbnail or "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=300"
                
                pct = 0
                if q.current.duration and q.current.duration > 0:
                    pct = min(100, int((elapsed / q.current.duration) * 100))

                playing_cards.append(f"""
                <div class="card">
                    <img src="{thumb}" alt="Thumbnail" class="card-thumb" onerror="this.src='https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=300'">
                    <div class="card-info">
                        <span class="badge-guild">📍 {gname}</span>
                        <div class="track-title">{title}</div>
                        <div class="track-artist">👤 {uploader}</div>
                        <div class="progress-bar-bg">
                            <div class="progress-bar-fill" style="width: {pct}%"></div>
                        </div>
                        <div class="track-time"><span>{elapsed_str}</span><span>{dur_str}</span></div>
                    </div>
                </div>
                """)

        if not playing_cards:
            playing_html = """<div class="empty-state">💤 Saat ini tidak ada lagu yang sedang diputar. Gunakan <code>/play</code> di Discord untuk memulai!</div>"""
        else:
            playing_html = "".join(playing_cards)

        html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎵 Rythm & Voice Companion — Live Dashboard</title>
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
            font-size: 0.85rem; font-weight: 600;
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
            display: flex; align-items: center; gap: 10px;
        }}
        .cards-list {{ display: flex; flex-direction: column; gap: 16px; }}
        .card {{
            background: var(--surface-color); backdrop-filter: blur(12px);
            border: 1px solid var(--surface-border); border-radius: 18px;
            padding: 16px; display: flex; gap: 20px; align-items: center;
        }}
        .card-thumb {{
            width: 100px; height: 100px; border-radius: 14px; object-fit: cover;
            box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
        }}
        .card-info {{ flex: 1; }}
        .badge-guild {{
            display: inline-block; font-size: 0.75rem; background: rgba(255, 255, 255, 0.08);
            padding: 4px 10px; border-radius: 6px; color: var(--accent-cyan); margin-bottom: 6px;
        }}
        .track-title {{ font-size: 1.1rem; font-weight: 700; margin-bottom: 4px; }}
        .track-artist {{ font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 12px; }}
        .progress-bar-bg {{
            background: rgba(255, 255, 255, 0.1); border-radius: 999px; height: 6px;
            overflow: hidden; margin-bottom: 6px;
        }}
        .progress-bar-fill {{
            background: linear-gradient(90deg, var(--accent-purple), var(--accent-cyan));
            height: 100%; border-radius: 999px; transition: width 0.5s ease;
        }}
        .track-time {{
            display: flex; justify-content: space-between; font-size: 0.75rem;
            color: var(--text-secondary); font-family: 'JetBrains Mono', monospace;
        }}
        .empty-state {{
            background: var(--surface-color); border: 1px dashed var(--surface-border);
            border-radius: 18px; padding: 40px; text-align: center; color: var(--text-secondary);
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
                    <div class="brand-subtitle">Discord Music, AI DJ & Pomodoro System</div>
                </div>
            </div>
            <div class="status-pill">
                <div class="dot"></div>
                SYSTEM ONLINE
            </div>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">⏱️ Uptime</div>
                <div class="stat-value">{uptime_str}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">📶 Latency</div>
                <div class="stat-value" style="color: var(--accent-cyan);">{ping} ms</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">🌐 Server Terhubung</div>
                <div class="stat-value">{guilds_count}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">🔊 Voice Room Aktif</div>
                <div class="stat-value" style="color: var(--accent-purple);">{voice_count}</div>
            </div>
        </div>

        <div class="section-header">
            <span>🎶 Sedang Diputar Saat Ini (Now Playing)</span>
        </div>
        <div class="cards-list">
            {playing_html}
        </div>

        <footer>
            Built for Discord Music & Voice Competition • Powered by discord.py & Google Gemini AI
        </footer>
    </div>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/health", handle_health)
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
