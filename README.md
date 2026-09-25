# 🎵 Discord Music, 24/7 Voice & Study Companion Bot

Bot Discord lengkap dengan fitur musik ala Rythm, penjaga voice channel 24/7 (*Keep-Online*), Pomodoro Study Timer dengan suara bel notifikasi (*Audio Ducking*), dan Voice Activity Tracker / Leaderboard berbasis SQLite async.

---

## ✨ Fitur Utama

1. **Music Player (Full Controls)**
   - `/play <query/url>`: Memutar musik dari YouTube (judul atau direct link).
   - `/search <query>`: Menampilkan 5 pilihan lagu teratas dalam interactive Dropdown Menu.
   - `/queue`, `/nowplaying`, `/skip`, `/pause`, `/resume`, `/stop`, `/loop`, `/volume`.
   - **Loop Bypass**: Fitur force skip cerdas agar perintah `/skip` tetap bekerja normal saat mode repeat/looping aktif.

2. **24/7 Voice Keeper (Keep-Online)**
   - `/join`: Bot bergabung ke voice channel dan standby menjaga channel tetap online.
   - **Auto-Reconnect**: Otomatis menyambung ulang jika jaringan drop atau terputus.
   - `/leave`: Bot keluar dari voice channel.
   - `/status`: Informasi latency, uptime voice room, status musik, dan antrean.

3. **🍅 Pomodoro Voice Companion**
   - `/pomodoro action:start work:25 break_time:5 cycles:4`: Memulai sesi sprint belajar/kerja kelompok.
   - **Anti-Drift Timer**: Timer presisi berbasis timestamp absolut (tidak mengalami deviasi waktu).
   - **Audio Ducking**: Bel notifikasi berbunyi di voice channel dengan menurunkan volume musik sejenak tanpa memutus streaming audio.

4. **🏆 Voice Activity Tracker & Leaderboard**
   - `/voicetop`: Papan peringkat member teraktif di voice channel.
   - `/voicetime`: Cek total waktu nongkrong dan ranking diri sendiri atau member lain.
   - **Anti-AFK & Deaf Filter**: Otomatis mengabaikan member yang sedang *deafened* (tuli) atau sendirian di room kosong untuk mencegah kecurangan farming waktu.
   - **Async SQLite (`aiosqlite`)**: Non-blocking database I/O untuk performa tinggi.

---

## 🚀 Panduan Instalasi (VPS / Server)

### 1. Prasyarat Sistem
Pastikan Python 3.10+ dan FFmpeg sudah terinstal di server:
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv ffmpeg git
```

### 2. Clone Repository
```bash
git clone https://github.com/NaUzAr/bot-discord-music.git
cd bot-discord-music
```

### 3. Setup Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Konfigurasi Environment (`.env`)
Salin template `.env.example` ke `.env`:
```bash
cp .env.example .env
nano .env
```
Isi dengan token bot kamu:
```env
DISCORD_TOKEN=your_bot_token_here
```

### 5. Jalankan Bot
- **Manual (Testing):**
  ```bash
  python bot_music.py
  ```
- **24/7 Service via Systemd:**
  Buat file `/etc/systemd/system/discord-bot.service`:
  ```ini
  [Unit]
  Description=Discord Music & Voice Bot
  After=network.target

  [Service]
  Type=simple
  User=botuser
  WorkingDirectory=/home/botuser/bot-discord-music
  ExecStart=/home/botuser/bot-discord-music/venv/bin/python /home/botuser/bot-discord-music/bot_music.py
  Restart=always
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  ```
  Aktifkan service:
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl enable --now discord-bot
  ```
