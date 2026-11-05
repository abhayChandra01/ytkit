# YTKit

Paste a YouTube link (or just the video ID) and download the video in any quality it exists in, from 144p up to 4K/8K and HDR, or grab the audio as MP3, M4A, Opus, OGG, WAV or FLAC.

YTKit is self-hosted. It needs **no YouTube API key, no Google account and no third-party service**. Everything runs on your own server with open source tools: [yt-dlp](https://github.com/yt-dlp/yt-dlp) reads YouTube's streams and [ffmpeg](https://ffmpeg.org) merges and converts them.

<p align="center"><img src="docs/screenshot-dark.png" alt="YTKit in dark mode showing every quality for a video" width="49%"> <img src="docs/screenshot-light.png" alt="YTKit in light mode" width="49%"></p>

![stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20yt--dlp%20%2B%20ffmpeg-ff5c8a) ![frontend](https://img.shields.io/badge/frontend-vanilla%20JS%2C%20no%20build-ff8a4c) ![license](https://img.shields.io/badge/keys%20required-none-3ee08f)

---

## Features

- **Every quality.** All resolutions YouTube has for the video (144p to 4320p), with frame rate, codec, HDR flag and an estimated file size for each.
- **Maximum quality by design.** Above 360p YouTube only serves video and audio as separate streams. YTKit downloads the best video stream for your resolution plus the best audio stream and merges them losslessly with ffmpeg. Nothing is re-encoded for video.
- **Three video containers.** MP4 (H.264 + AAC where available, for maximum compatibility), WebM (VP9/AV1 + Opus) and MKV (the absolute best streams, HDR included).
- **Seven audio options.** MP3 (128 to 320 kbps), M4A/AAC, Opus, OGG Vorbis, WAV, FLAC, and Original (the untouched source stream). Cover art, title and artist tags are embedded automatically where the format supports it.
- **Live progress.** Server-sent events stream download percentage, speed, time left, and the merge/convert stage to the browser.
- **Polished UI.** Animated gradient background, glowing command bar, auto-fetch on paste, skeleton loading with an equalizer animation, sliding tabs, a floating downloads dock, toasts, dark and light themes, keyboard shortcuts, and a fully responsive layout. Respects `prefers-reduced-motion`.
- **Accepts anything.** `youtube.com/watch`, `youtu.be`, Shorts, YouTube Music, embeds, `/live/` links, bare IDs, or a whole sentence with a link in it.
- **Safe defaults.** Per-IP rate limiting, a concurrent download limit, a maximum video length, automatic deletion of finished files, a strict Content Security Policy, and a non-root Docker image.

## How sites like this work

Downloader sites don't use the official YouTube Data API. That API only returns metadata and never gives you the media. Instead they do what the YouTube web player does:

1. Ask YouTube's internal player endpoint for the video's *streaming data*. That's the full list of available streams (itags) with resolution, codec, bitrate and URL.
2. Solve the JavaScript challenges YouTube uses to sign and unthrottle those URLs.
3. Download the chosen video-only and audio-only streams in parallel chunks.
4. Merge them (and optionally transcode the audio) with ffmpeg.

YouTube changes steps 1 and 2 often, so maintaining that yourself is a full-time job. [yt-dlp](https://github.com/yt-dlp/yt-dlp) is the open source project that keeps up with it, which is why YTKit builds on it rather than reimplementing it. That gives you no API keys, maximum quality, and a fix for YouTube changes that is usually a single `pip install -U yt-dlp`.

---

## Run it locally

You need **Python 3.10+**, **ffmpeg**, and a **JavaScript runtime** (Deno is recommended; Node 20+ also works). yt-dlp uses the JS runtime to solve YouTube's player challenges.

```bash
# 1. System tools
#    macOS:          brew install ffmpeg deno
#    Ubuntu/Debian:  sudo apt install ffmpeg && curl -fsSL https://deno.land/install.sh | sh
#    Windows:        winget install Gyan.FFmpeg DenoLand.Deno

# 2. Python deps
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Start
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. The footer shows a green **All systems go** badge when ffmpeg and a JS runtime are both found.

### Run with Docker (recommended)

```bash
docker compose up -d --build
# → http://localhost:8000
```

The image bundles Python, ffmpeg and Deno, runs as a non-root user, and has a health check.

### Run the tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests cover link parsing, stream selection, the HTTP API, and a real download → merge → convert run through ffmpeg for every audio format and video container, using a locally generated clip, so they don't need internet access.

---

## Deploy

YTKit is a normal long-running web server. It needs ffmpeg on the machine and somewhere to write temporary files, so **serverless platforms (Vercel, Netlify Functions, Cloudflare Workers) won't work**. Anything that runs a Docker container will.

### Option A: Render (easiest)

1. Push this repo to GitHub (already done).
2. In Render, choose **New → Web Service** and pick the repo.
3. Runtime: **Docker**. Render detects the `Dockerfile`.
4. Instance type: at least 512 MB RAM. 1 GB+ is better for 4K merges.
5. Deploy. Render sets `PORT` automatically and the Dockerfile honours it.

### Option B: Railway

1. **New Project → Deploy from GitHub repo** and select this repo.
2. Railway builds the `Dockerfile` and injects `PORT`. Under **Settings → Networking**, click **Generate Domain**.

### Option C: Fly.io

```bash
fly launch --no-deploy        # accept the Dockerfile, set internal port 8000
fly deploy
```

### Option D: Any VPS (DigitalOcean, Hetzner, Lightsail, Oracle free tier…)

```bash
git clone https://github.com/abhayChandra01/ytkit.git && cd ytkit
docker compose up -d --build
```

Put a reverse proxy with HTTPS in front. With [Caddy](https://caddyserver.com) it's two lines:

```caddyfile
ytkit.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

With nginx, turn buffering off for the progress stream:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;          # needed for live progress (SSE)
    proxy_read_timeout 1h;
    client_max_body_size 1m;
}
```

The Docker image already starts uvicorn with `--proxy-headers`, so rate limiting sees real client IPs behind Render, Railway, Fly or your own proxy. If you run uvicorn yourself behind a proxy, set `YTKIT_TRUST_PROXY=true` instead. (The front end also falls back to polling automatically if a proxy buffers the event stream.)

### Keep yt-dlp fresh

YouTube changes its player regularly, and yt-dlp ships fixes within days. If lookups suddenly start failing, update it:

```bash
pip install -U "yt-dlp[default]"          # local
docker compose build --pull --no-cache && docker compose up -d   # Docker
```

Rebuilding the image weekly (a cron job or a scheduled redeploy) keeps things smooth.

---

## Configuration

Everything is optional. Set these as environment variables.

| Variable | Default | What it does |
| --- | --- | --- |
| `PORT` | `8000` | Port the Docker image listens on. |
| `YTKIT_MAX_CONCURRENT` | `3` | Downloads that run at the same time. The rest wait in a queue. |
| `YTKIT_FILE_TTL` | `1800` | Seconds a finished file stays on the server before it's deleted. |
| `YTKIT_MAX_DURATION` | `14400` | Longest video accepted, in seconds (4 h). `0` = no limit. |
| `YTKIT_INFO_TTL` | `600` | Seconds video metadata is cached. |
| `YTKIT_INFO_RATE` | `30` | Lookups per minute per IP. `0` disables. |
| `YTKIT_JOB_RATE` | `12` | Downloads per minute per IP. `0` disables. |
| `YTKIT_TRUST_PROXY` | `false` | Use `X-Forwarded-For` for client IPs (only behind your own proxy). |
| `YTKIT_DOWNLOAD_DIR` | system temp `/ytkit` | Where temporary files go. |
| `YTKIT_EMBED_METADATA` | `true` | Embed title, artist and cover art in audio files. |
| `YTKIT_COOKIES_FILE` | none | Path to a Netscape `cookies.txt`. See below. |
| `YTKIT_PROXY` | none | Outbound proxy for YouTube traffic, e.g. `http://user:pass@host:port` or `socks5://host:port`. |

### If YouTube says "Sign in to confirm you're not a bot"

YouTube is stricter with traffic from cloud/datacenter IP ranges than with home connections. Running YTKit on your own computer or a home server almost never hits this. On a cloud host you may see it eventually. Options, from simplest:

1. **Cookies.** Log in to YouTube in a private browser window, export cookies with an extension such as *Get cookies.txt LOCALLY*, close that window, and mount the file:
   ```yaml
   # docker-compose.yml
   environment:
     YTKIT_COOKIES_FILE: /run/secrets/cookies.txt
   volumes:
     - ./cookies.txt:/run/secrets/cookies.txt:ro
   ```
   Use a throwaway Google account for this, not your main one.
2. **A residential proxy.** Set `YTKIT_PROXY`. Any HTTP or SOCKS5 proxy works. This is the only place a paid "key" might come in, and it's optional.
3. **PO token provider.** For heavy use, the community [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) plugin can run as a sidecar and generate the tokens YouTube asks for. Install the plugin with pip in the image and run its server next to YTKit.

Cookies also unlock age-restricted videos.

---

## API

Interactive docs live at `/api/docs`.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Server status: versions, ffmpeg and JS runtime presence. |
| `GET` | `/api/info?q=<url or id>` | Video metadata plus every video quality and audio option. |
| `POST` | `/api/jobs` | Start a download. Body: `{"video_id","kind":"video","quality":1080,"container":"mp4"}` or `{"video_id","kind":"audio","format":"mp3","bitrate":320}`. |
| `GET` | `/api/jobs/{id}` | Job status (stage, percent, speed, eta, error). |
| `GET` | `/api/jobs/{id}/events` | The same status as a server-sent event stream. |
| `GET` | `/api/jobs/{id}/file` | The finished file. |
| `DELETE` | `/api/jobs/{id}` | Cancel a job. |
| `GET` | `/api/thumbnail/{video_id}` | Highest resolution thumbnail as a JPEG download. |

Audio `format` is one of `mp3`, `m4a`, `opus`, `ogg`, `wav`, `flac`, `original`. Allowed bitrates: MP3 320/256/192/128, M4A 256/192/128, Opus 160/128/96, OGG 320/192/128. Lossless and original formats take no bitrate.

---

## Project layout

```
app/
  main.py        HTTP API, security headers, rate limiting, static site
  ytdl.py        yt-dlp wrapper: metadata cache, downloads, friendly errors
  formats.py     picks the best streams per quality/container and builds ffmpeg steps
  jobs.py        background job queue, progress tracking, file cleanup
  parse.py       turns any pasted text into a video ID
  config.py      environment settings
  static/        the website: index.html, css/, js/, img/ (no build step)
tests/           pytest suite
Dockerfile, docker-compose.yml
```

## Notes on quality

- **Video is never re-encoded.** You get YouTube's original streams, merged.
- **MP4 above 1080p** uses VP9 or AV1 because YouTube doesn't offer H.264 at those resolutions. Modern players handle these fine. If an old device won't play it, pick 1080p MP4.
- **HDR** streams are only used for MKV, so MP4/WebM files never look washed out on SDR screens.
- **Audio.** YouTube's best audio is usually Opus at about 130–160 kbps. Converting to 320 kbps MP3 preserves as much as possible but can't add detail that isn't there. WAV, FLAC and Original keep the source exactly.

## Legal

YTKit is a tool. Only download videos you own or have permission to download, and respect creators' rights and YouTube's Terms of Service. This project isn't affiliated with YouTube or Google.
