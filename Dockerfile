FROM python:3.11-slim

# Menghindari buffering log Python dan file bytecode .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Install ffmpeg, nodejs, deno (JS challenge solver untuk yt-dlp), library audio Opus, curl, dan unzip
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    libopus0 \
    libopus-dev \
    curl \
    unzip && \
    curl -fsSL https://deno.land/install.sh | sh && \
    cp /root/.deno/bin/deno /usr/local/bin/ && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Salin dan install dependencies Python terlebih dahulu agar ter-cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Salin seluruh file proyek
COPY . .

# Pastikan folder data dan logs tersedia untuk persistent volume
RUN mkdir -p /app/data /app/logs

# Expose port untuk Web Dashboard & Healthcheck
EXPOSE 8080

# Healthcheck otomatis setiap 30 detik
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Jalankan Bot Discord Musik & Web Dashboard
CMD ["python", "bot_music.py"]
