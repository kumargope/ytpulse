FROM python:3.11-slim

# Install system dependencies, real FFmpeg, curl, unzip, ca-certificates, and Node.js
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    unzip \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install official Deno runtime (preferred by yt-dlp for EJS challenge solving)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

WORKDIR /app

# Install python requirements (yt-dlp, yt-dlp-ejs, fastapi, uvicorn)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Verify all required production binaries are accessible in PATH
RUN ffmpeg -version && ffprobe -version && deno --version && yt-dlp --version

# Copy application files
COPY . .

# Create cache directory with proper permissions
RUN mkdir -p /tmp/yt_downloader_cache /tmp/secure_cookies && chmod 777 /tmp/yt_downloader_cache /tmp/secure_cookies

# Environment port for Render
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
