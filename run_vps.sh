#!/bin/bash
# ==============================================================================
# 🎵 Discord Music & 24/7 Voice Bot — VPS One-Click Runner & Manager
# ==============================================================================
# Skrip otomatis untuk menjalankan, memantau, dan mengelola bot Discord di VPS.
# Mendukung Docker Compose (24/7 background) dan Systemd Service.
# ==============================================================================

set -e

# Warna Terminal
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Direktori Proyek
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# ─── Inisialisasi Berkas Penting (Cegah Docker Directory Bug) ─────────────────
init_preflight() {
    mkdir -p data logs
    # Pastikan cookies.txt adalah file, BUKAN direktori yang salah dibuat Docker
    if [ -d "cookies.txt" ]; then
        rm -rf cookies.txt
    fi
    if [ ! -f "cookies.txt" ]; then
        touch cookies.txt
    fi

    # Cek .env
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            echo -e "${YELLOW}⚠️ Berkas .env tidak ditemukan. Menyalin dari .env.example...${NC}"
            cp .env.example .env
            echo -e "${RED}❗ Buka file .env dan masukkan DISCORD_TOKEN sebelum menjalankan bot: nano .env${NC}"
        else
            echo -e "${RED}❌ Berkas .env tidak ditemukan! Silakan buat berkas .env terlebih dahulu.${NC}"
        fi
    fi
}

# ─── Cek Instalasi Docker ─────────────────────────────────────────────────────
check_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${YELLOW}🐳 Docker belum terinstal di VPS ini.${NC}"
        read -p "Apakah Anda ingin menginstal Docker otomatis sekarang? (y/n): " install_doc
        if [[ "$install_doc" =~ ^[Yy]$ ]]; then
            echo -e "${CYAN}🚀 Menginstal Docker resmi...${NC}"
            sudo apt-get update && sudo apt-get install -y curl git
            curl -fsSL https://get.docker.com -o get-docker.sh
            sudo sh get-docker.sh
            sudo usermod -aG docker $USER || true
            rm -f get-docker.sh
            echo -e "${GREEN}✅ Docker berhasil diinstal!${NC}"
        else
            echo -e "${RED}Docker dibutuhkan untuk mode kontainer.${NC}"
            return 1
        fi
    fi
    return 0
}

# ─── Perintah-perintah Operasional ───────────────────────────────────────────
start_docker() {
    init_preflight
    check_docker || return 1
    echo -e "${CYAN}🚀 Menjalankan Bot Discord di latar belakang (Docker Compose)...${NC}"
    docker compose up -d --build
    echo -e "${GREEN}✅ Bot berhasil dijalankan 24/7!${NC}"
    echo -e "   Gunakan opsi 3 atau '${CYAN}./run_vps.sh logs${NC}' untuk memantau log."
}

stop_docker() {
    echo -e "${YELLOW}🛑 Menghentikan kontainer bot Discord...${NC}"
    docker compose down
    echo -e "${GREEN}✅ Bot telah dihentikan.${NC}"
}

restart_docker() {
    init_preflight
    echo -e "${CYAN}🔄 Merestart kontainer bot...${NC}"
    docker compose restart
    echo -e "${GREEN}✅ Bot berhasil direstart.${NC}"
}

view_logs() {
    echo -e "${CYAN}📜 Menampilkan log real-time (Tekan Ctrl+C untuk keluar)...${NC}\n"
    docker compose logs -f
}

check_status() {
    echo -e "${BOLD}📊 Status Kontainer Docker:${RESET}"
    docker compose ps
    echo ""
    echo -e "${BOLD}🌐 Healthcheck Endpoint:${RESET}"
    if curl -s -f http://localhost:8080/health &> /dev/null; then
        curl -s http://localhost:8080/health | python3 -m json.tool 2>/dev/null || curl -s http://localhost:8080/health
        echo -e "\n${GREEN}✅ Healthcheck OK (Status Healthy)${NC}"
    else
        echo -e "${YELLOW}⚠️ Tidak dapat menghubungi http://localhost:8080/health (Bot mungkin sedang startup atau offline).${NC}"
    fi
}

update_git() {
    echo -e "${CYAN}📥 Menarik pembaruan kode terbaru dari GitHub...${NC}"
    git pull origin main || git pull
    echo -e "${CYAN}🔄 Membangun ulang kontainer dengan versi terbaru...${NC}"
    start_docker
}

run_diagnostics() {
    init_preflight
    echo -e "${CYAN}🧪 Menjalankan uji diagnostik sistem bot...${NC}"
    if [ -f "venv/bin/python" ]; then
        venv/bin/python test_bot.py
    elif command -v python3 &> /dev/null; then
        python3 test_bot.py
    elif docker compose ps -q discord-bot &> /dev/null; then
        docker compose exec discord-bot python test_bot.py
    else
        echo -e "${RED}❌ Python3 atau kontainer bot tidak ditemukan untuk menjalankan tes.${NC}"
    fi
}

setup_systemd() {
    echo -e "${CYAN}⚙️ Menyiapkan Systemd Service (Deploy tanpa Docker)...${NC}"
    SERVICE_FILE="/etc/systemd/system/discord-music-bot.service"
    CURRENT_USER=$(whoami)
    PYTHON_EXEC="$PROJECT_DIR/venv/bin/python"

    if [ ! -f "$PYTHON_EXEC" ]; then
        echo -e "${YELLOW}Virtualenv belum ada. Membuat venv di $PROJECT_DIR/venv...${NC}"
        sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv ffmpeg
        python3 -m venv venv
        venv/bin/pip install -r requirements.txt
    fi

    echo -e "Menulis file $SERVICE_FILE..."
    sudo bash -c "cat <<EOF > $SERVICE_FILE
[Unit]
Description=Discord Music & Voice Companion Bot
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_EXEC $PROJECT_DIR/bot_music.py
Restart=always
RestartSec=10
EnvironmentFile=$PROJECT_DIR/.env

[Install]
WantedBy=multi-user.target
EOF"

    sudo systemctl daemon-reload
    sudo systemctl enable --now discord-music-bot
    echo -e "${GREEN}✅ Systemd service aktif & berjalan!${NC}"
    echo -e "   Cek status: ${CYAN}sudo systemctl status discord-music-bot${NC}"
    echo -e "   Lihat log:  ${CYAN}journalctl -u discord-music-bot -f${NC}"
}

# ─── Argumen Baris Perintah Langsung (CLI Mode) ──────────────────────────────
case "$1" in
    start)
        start_docker
        exit 0
        ;;
    stop)
        stop_docker
        exit 0
        ;;
    restart)
        restart_docker
        exit 0
        ;;
    logs)
        view_logs
        exit 0
        ;;
    status)
        check_status
        exit 0
        ;;
    update)
        update_git
        exit 0
        ;;
    test)
        run_diagnostics
        exit 0
        ;;
    systemd)
        setup_systemd
        exit 0
        ;;
esac

# ─── Menu Interaktif (Interactive CLI) ────────────────────────────────────────
while true; do
    echo -e "\n${CYAN}${BOLD}╔═════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}${BOLD}║      🎵 DISCORD MUSIC BOT — VPS MANAGER & RUNNER 24/7       ║${NC}"
    echo -e "${CYAN}${BOLD}╚═════════════════════════════════════════════════════════════╝${NC}"
    echo -e "  ${BOLD}1)${NC} ${GREEN}🚀 Jalankan Bot 24/7 (Docker Compose Up)${NC}"
    echo -e "  ${BOLD}2)${NC} ${BLUE}📊 Cek Status & Healthcheck (/health)${NC}"
    echo -e "  ${BOLD}3)${NC} ${CYAN}📜 Pantau Log Real-time (logs -f)${NC}"
    echo -e "  ${BOLD}4)${NC} ${YELLOW}🔄 Restart Bot (docker compose restart)${NC}"
    echo -e "  ${BOLD}5)${NC} ${RED}🛑 Hentikan Bot (docker compose down)${NC}"
    echo -e "  ${BOLD}6)${NC} ${GREEN}📥 Update Kode dari Git & Rebuild${NC}"
    echo -e "  ${BOLD}7)${NC} ${CYAN}🛠️ Jalankan Tes Diagnostik Sistem & Bug${NC}"
    echo -e "  ${BOLD}8)${NC} ${YELLOW}⚙️ Setup Systemd Service (Mode Non-Docker)${NC}"
    echo -e "  ${BOLD}9)${NC} Keluar"
    echo -e "${CYAN}─────────────────────────────────────────────────────────────${NC}"
    read -p "Pilih menu [1-9]: " choice

    case "$choice" in
        1) start_docker ;;
        2) check_status ;;
        3) view_logs ;;
        4) restart_docker ;;
        5) stop_docker ;;
        6) update_git ;;
        7) run_diagnostics ;;
        8) setup_systemd ;;
        9) echo -e "${GREEN}Sampai jumpa! 👋${NC}"; exit 0 ;;
        *) echo -e "${RED}Pilihan tidak valid! Masukkan angka 1 sampai 9.${NC}" ;;
    esac
done
