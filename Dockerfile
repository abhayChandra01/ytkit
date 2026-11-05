# syntax=docker/dockerfile:1
FROM python:3.12-slim

# ffmpeg merges and converts streams; Deno is the JavaScript runtime yt-dlp uses
# to solve YouTube's player challenges.
COPY --from=denoland/deno:bin-2.5.6 /deno /usr/local/bin/deno
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    YTKIT_DOWNLOAD_DIR=/data/downloads \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 ytkit \
 && mkdir -p /data/downloads \
 && chown -R ytkit:ytkit /data /app
USER ytkit

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/health', timeout=4)"

ENTRYPOINT ["/usr/bin/tini", "--"]
# --proxy-headers lets uvicorn see the real scheme behind a reverse proxy.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
