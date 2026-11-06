# syntax=docker/dockerfile:1
# PO token server (bgutil). YouTube asks flagged servers for these tokens; the
# app starts this server on 127.0.0.1:4416 and the yt-dlp plugin talks to it.
FROM brainicism/bgutil-ytdlp-pot-provider:2.0.2-deno AS bgutil

FROM python:3.12-slim

# ffmpeg merges and converts streams; Deno is the JavaScript runtime yt-dlp uses
# to solve YouTube's player challenges, and it also runs the PO token server.
COPY --from=denoland/deno:bin-2.9.5 /deno /usr/local/bin/deno
COPY --from=bgutil /app /opt/bgutil
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    YTKIT_DOWNLOAD_DIR=/data/downloads \
    DENO_DIR=/opt/bgutil/.cache/deno \
    DENO_NO_PROMPT=1 \
    DENO_NO_UPDATE_CHECK=1 \
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
