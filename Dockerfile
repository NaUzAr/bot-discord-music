FROM python:3.11-slim

# Menghindari buffering log Python dan file bytecode .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Install ffmpeg, nodejs (JS challenge solver untuk yt-dlp), dan library audio Opus
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    libopus0 \
    libopus-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Salin dan install dependencies Python terlebih dahulu agar ter-cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Salin seluruh file proyek
COPY . .

# Expose port untuk Web Dashboard & Healthcheck Render
EXPOSE 8080

# Jalankan Bot Discord Musik & Web Dashboard
CMD ["python", "bot_music.py"]
