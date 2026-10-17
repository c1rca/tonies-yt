FROM python:3.12-slim

LABEL org.opencontainers.image.title="Tonies-YT" \
      org.opencontainers.image.version="2.0.0" \
      org.opencontainers.image.source="https://github.com/c1rca/tonies-yt" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Media tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Deno for yt-dlp JS runtime support
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:${PATH}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade yt-dlp

COPY app ./app
COPY web ./web

ENV PYTHONUNBUFFERED=1
ENV APP_PORT=8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
