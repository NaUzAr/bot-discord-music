# 🎵 Discord Music, 24/7 Voice & Study Companion Bot

Bot Discord lengkap dengan fitur musik ala Rythm, penjaga voice channel 24/7 (*Keep-Online*), Pomodoro Study Timer dengan suara bel notifikasi (*Audio Ducking*), dan Voice Activity Tracker / Leaderboard berbasis SQLite async.

---

## ✨ Fitur Utama

1. **Music Player (Full Controls & Universal Streaming)**
   - `/play <query/url>`: Memutar musik dari YouTube, Spotify Track Link (`open.spotify.com/track/...`), SoundCloud, atau YouTube Playlist URL (otomatis antrekan album/playlist sekaligus!).
   - `/search <query>`: Menampilkan 5 pilihan lagu teratas dalam interactive Dropdown Menu.
   - `/shuffle`: Mengacak antrean lagu secara instan (Fisher-Yates shuffle).
   - `/seek <waktu>`: Melompat ke detik atau menit tertentu (`01:30`) secara hot-reload.
   - `/history`: Melihat 20 riwayat lagu terakhir yang telah diputar.
   - `/diag`: Uji diagnostik kesehatan bot, audio latency, SQLite async I/O, dan koneksi Gemini AI langsung dari Discord.
   - `/queue`, `/nowplaying`, `/skip`, `/pause`, `/resume`, `/stop`, `/loop`, `/volume`.
   - **Loop Bypass**: Fitur force skip cerdas agar perintah `/skip` tetap bekerja normal saat mode repeat/looping aktif.

2. **24/7 Voice Keeper (Keep-Online)**
   - `/join`: Bot bergabung ke voice channel dan standby menjaga channel tetap online.
   - **Auto-Reconnect**: Otomatis menyambung ulang jika jaringan drop atau terputus.
   - `/leave`: Bot keluar dari voice channel.
   - `/status`: Informasi latency, uptime voice room, status musik, dan antrean.

3. **🍅 Pomodoro Voice Companion**
   - `/pomo-start`: Memulai sesi sprint belajar/kerja kelompok (Fokus → Istirahat → Fokus).
   - **Anti-Drift Timer**: Timer presisi berbasis timestamp absolut (tidak mengalami deviasi waktu).
   - **Audio Ducking**: Bel notifikasi berbunyi di voice channel dengan menurunkan volume musik sejenak tanpa memutus streaming audio.
   - `/pomo-pause`, `/pomo-resume`, `/pomo-stop`, `/pomo-status`.

4. **🏆 Voice Activity Tracker & Leaderboard**
   - `/voicetop`: Papan peringkat member teraktif di voice channel.
   - `/voicetime`: Cek total waktu nongkrong dan ranking diri sendiri atau member lain.
   - **Deaf & AFK Filter**: Otomatis mengabaikan member yang sedang *deafened* (tuli) atau berada di voice channel AFK server.
   - **Async SQLite (`aiosqlite`)**: Non-blocking database I/O untuk performa tinggi.

5. **🤖 AI Smart Playlist, Recommendations & Aesthetic (Google Gemini)**
   - `/aiplaylist <prompt> [jumlah]`: Racik playlist tematik dari mood/skenario.
   - `/aidj <vibe>`: Shortcut instan untuk DJ AI meracik 5 lagu.
   - `/recommend`: Rekomendasi AI berdasarkan alur lagu dengan tombol instan *One-Click Play*.
   - `/mood`: Analisis estetika, vibe, skenario pendengaran, dan energy level lagu saat ini.

6. **📁 Custom Playlist Manager (Personal Library)**
   - `/playlist save <nama>`: Simpan antrean lagu saat ini ke database SQLite pribadi.
   - `/playlist load <nama>`: Muat dan putar playlist dengan autocomplete cerdas.
   - `/playlist list`: Tampilkan semua playlist yang tersimpan beserta jumlah lagu.
   - `/playlist delete <nama>`: Hapus playlist tersimpan.

7. **🎤 Synced Karaoke & Lyrics Finder**
   - `/singalong`: Mode Karaoke interaktif dengan *Synced Lyrics* (LRC timestamps) yang bergerak mengikuti progres lagu.
   - `/lyrics [judul]`: Cari lirik lagu lengkap via LRCLIB & fallback Google Gemini AI dengan paginasi tombol interaktif.

8. **🌐 Web Dashboard & Healthcheck Endpoint**
   - Live Dark-Mode Glassmorphism Web UI di port `8080` (atau env `PORT`).
   - `/health`: Endpoint JSON health check untuk Docker / Kubernetes / VPS monitoring.
   - `/dashboard`: Dapatkan link dashboard langsung di Discord.

9. **🌙 Sleep Timer & 🎛️ Audio DSP Filters**
   - `/sleep` · `/sleeptimer`: Timer tidur otomatis (stop musik / leave voice).
   - `/filter`: Hot-reload efek audio DSP (Bass Boost, Nightcore, Slowed + Reverb, 8D Audio, Lo-Fi).

---

## 🚀 Panduan Instalasi (VPS / Server)

### ⚡ Cara Instan: 1-Click VPS Runner Script (`run_vps.sh`)
Bot ini sudah dilengkapi skrip otomatis `run_vps.sh` yang menangani instalasi Docker, validasi file, background 24/7 run, pemantauan log, dan diagnosa bug dalam satu menu interaktif:

```bash
# 1. Berikan izin eksekusi skrip
chmod +x run_vps.sh

# 2. Jalankan menu interaktif
./run_vps.sh
```

Atau jalankan perintah langsung (CLI shortcuts):
- `./run_vps.sh start` — Jalankan bot 24/7 di latar belakang
- `./run_vps.sh status` — Cek status kontainer & healthcheck `/health`
- `./run_vps.sh logs` — Pantau log bot secara real-time
- `./run_vps.sh update` — Tarik update dari GitHub & rebuild otomatis
- `./run_vps.sh test` — Jalankan uji diagnostik sistem & bug
- `./run_vps.sh stop` — Hentikan bot

---

### 🐳 Metode 1: Deploy dengan Docker & Docker Compose Manual (Direkomendasikan)

Metode ini adalah cara paling mudah, stabil, dan terisolasi untuk menjalankan bot 24/7 di VPS Linux (Ubuntu, Debian, CentOS, dll.) tanpa repot menginstal dependensi manual seperti FFmpeg atau Opus.

#### 1. Install Docker & Docker Compose di VPS (Ubuntu / Debian)
Jika Docker belum terinstal di VPS Anda:
```bash
# Update sistem dan install curl & git
sudo apt update && sudo apt install -y curl git

# Install Docker resmi via script otomatis
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Beri izin user agar bisa menjalankan docker tanpa sudo (opsional)
sudo usermod -aG docker $USER
newgrp docker
```

#### 2. Clone Repository
```bash
git clone https://github.com/NaUzAr/bot-discord-music.git
cd bot-discord-music
```

#### 3. Konfigurasi Environment (`.env`)
Salin template `.env.example` ke `.env`:
```bash
cp .env.example .env
nano .env
```
Isi konfigurasi Anda:
```env
DISCORD_TOKEN=your_bot_token_here
GEMINI_API_KEY=your_gemini_api_key_here
PORT=8080
DB_PATH=/app/data/voice_tracker.db

# Opsional: Jika IP VPS Anda diblokir YouTube ("Sign in to confirm you're not a bot")
# YOUTUBE_COOKIES_BASE64=
```

#### 4. Jalankan Bot
Jalankan bot di latar belakang (*detached mode*):
```bash
docker compose up -d --build
```

#### 5. Cek Status, Log & Healthcheck
```bash
# Cek status kontainer (harus "Up" dan "(healthy)")
docker compose ps

# Pantau log bot secara real-time
docker compose logs -f

# Tes healthcheck endpoint
curl http://localhost:8080/health
```

#### 🛠️ Perintah Manajemen Docker
- **Stop bot**: `docker compose down`
- **Restart bot**: `docker compose restart`
- **Update ke versi terbaru dari GitHub**:
  ```bash
  git pull
  docker compose up -d --build
  ```

> [!TIP]
> **Data & Database Aman:** Database SQLite disimpan di folder `./data` di host VPS, sehingga data ranking voice dan playlist tidak akan hilang saat kontainer direstart atau di-rebuild!

---

### 💻 Metode 2: Deploy Manual (Virtualenv & Systemd)

#### 1. Prasyarat Sistem
Pastikan Python 3.10+ dan FFmpeg sudah terinstal di server:
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv ffmpeg git
```

#### 2. Setup Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 3. Jalankan Bot
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

---

## ☁️ Panduan Deploy ke Render.com (Gratis & 24/7)

Bot ini sudah dilengkapi dengan `Dockerfile`, `render.yaml`, dan web server internal dengan endpoint `/health`, sehingga dapat langsung dideploy ke **Render Web Service (Docker)**.

### Langkah 1: Push Perubahan ke GitHub
Pastikan seluruh file proyek dan `Dockerfile` sudah dipush ke repository GitHub Anda:
```bash
git add .
git commit -m "Add Docker and Render deployment setup"
git push origin main
```

### Langkah 2: Buat Web Service di Render
1. Buka [dashboard.render.com](https://dashboard.render.com) dan login dengan akun GitHub Anda.
2. Klik tombol **New +** di pojok kanan atas, lalu pilih **Web Service**.
3. Pilih repository GitHub bot Anda (`bot-discord-music`), lalu klik **Connect**.
4. Isi konfigurasi berikut:
   - **Name**: `discord-music-bot` (atau nama unik pilihan Anda)
   - **Region**: Pilih yang terdekat (misal: *Singapore* untuk latency terbaik ke Discord/YouTube di Indonesia)
   - **Language / Runtime**: **Docker** *(Render akan otomatis mendeteksi Dockerfile)*
   - **Instance Type**: **Free**
5. Buka bagian **Advanced** dan atur:
   - **Health Check Path**: `/health`
6. Masukkan **Environment Variables**:
   | Key | Value | Keterangan |
   |---|---|---|
   | `DISCORD_TOKEN` | `token_bot_anda` | Token dari Discord Developer Portal |
   | `GEMINI_API_KEY` | `key_gemini_anda` | *(Opsional)* Untuk fitur AI playlist & AI DJ |
7. Klik **Deploy Web Service**.

> [!TIP]
> **Metode Cepat via Blueprint:**
> Anda juga bisa memilih **New +** → **Blueprint**, lalu pilih repo Anda. Render akan membaca file `render.yaml` secara otomatis!

---

### ⏰ Cara Agar Bot Tetap Online 24/7 (Cegah Sleep di Free Tier)
Render Free Tier akan menidurkan (*spin down*) Web Service jika tidak ada request masuk selama 15 menit. Karena bot ini menyediakan endpoint `/health`, Anda bisa menggunakan layanan Uptime Monitor gratis:

1. Daftar akun gratis di [UptimeRobot.com](https://uptimerobot.com) atau [cron-job.org](https://cron-job.org).
2. Tambahkan monitor baru:
   - **Monitor Type**: `HTTP(s)`
   - **URL**: `https://<nama-aplikasi-anda>.onrender.com/health`
   - **Monitoring Interval**: Setiap **5 menit**
3. Simpan monitor. UptimeRobot akan otomatis mengirim ping setiap 5 menit sehingga Render tidak akan pernah tidur dan bot Discord Anda tetap siaga 24/7!
