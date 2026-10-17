FROM python:3.12-slim

LABEL org.opencontainers.image.title="Tonies-YT" \
      org.opencontainers.image.version="2.0.0" \
      org.opencontainers.image.source="https://github.com/c1rca/tonies-yt" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Pinned upstream Deno release archives, verified before execution (amd64/arm64).
# Checksums: https://github.com/denoland/deno/releases/tag/v2.9.7
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl unzip ca-certificates \
    && case "$(dpkg --print-architecture)" in \
         amd64) deno_arch=x86_64; deno_sha=c6527f24f4b16031d3ae4fa9f658d5f11534c8d84ce7dc8502420280919c3490 ;; \
         arm64) deno_arch=aarch64; deno_sha=c832298b1ad4422481334855f6003e0f54145762c5a134f20a489511d2f65bbf ;; \
         *) echo "Unsupported Deno architecture" >&2; exit 1 ;; \
       esac \
    && curl --fail --location --proto '=https' --tlsv1.2 \
       "https://github.com/denoland/deno/releases/download/v2.9.7/deno-${deno_arch}-unknown-linux-gnu.zip" -o /tmp/deno.zip \
    && printf '%s  /tmp/deno.zip\n' "$deno_sha" | sha256sum -c - \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && apt-get purge -y --auto-remove unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade 'yt-dlp[default]' \
    && python -m pip uninstall --yes pip

# Code/tools remain root-owned; only new persistent data belongs to the app.
# Docker initializes a fresh named volume with this directory's ownership.
RUN groupadd --gid 10001 tonies \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp/home --shell /usr/sbin/nologin tonies \
    && install -d -m 0700 -o 10001 -g 10001 /app/data
COPY app ./app
COPY web ./web

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONNOUSERSITE=1
ENV HOME=/tmp/home
ENV XDG_CACHE_HOME=/tmp/cache
ENV DENO_DIR=/tmp/cache/deno
ENV DATA_DIR=/app/data
ENV OMP_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV APP_PORT=8080

USER 10001:10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

# No proxy headers trusted by default. A proxy deployment must explicitly opt in.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers"]
