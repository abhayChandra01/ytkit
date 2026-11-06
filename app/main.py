"""HTTP API and static site."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
import threading
import time
import urllib.request
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import __version__, cookies, formats, parse, pot, ytdl
from .config import settings
from .jobs import TERMINAL, JobManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ytkit")

STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")

manager: JobManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager
    manager = JobManager()
    await run_in_threadpool(pot.server.start)
    log.info(
        "YTKit %s ready (yt-dlp %s, ffmpeg %s, js runtime %s)",
        __version__,
        yt_dlp.version.__version__,
        "found" if shutil.which("ffmpeg") else "MISSING",
        _js_runtime() or "MISSING",
    )
    yield
    manager.shutdown()
    pot.server.stop()


app = FastAPI(title="YTKit", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


# ---------------------------------------------------------------------------
# Middleware: security headers and a tiny per-IP rate limiter
# ---------------------------------------------------------------------------

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https://i.ytimg.com https://*.ytimg.com https://yt3.ggpht.com; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith(("/api/docs", "/openapi.json")):  # Swagger UI loads from a CDN
        response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.per_minute <= 0:
            return
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                retry = int(60 - (now - q[0])) + 1
                raise HTTPException(429, detail=f"Slow down a little. Try again in {retry}s.", headers={"Retry-After": str(retry)})
            q.append(now)
            if len(self.hits) > 10000:
                for k in [k for k, v in self.hits.items() if not v]:
                    self.hits.pop(k, None)


info_limiter = RateLimiter(settings.info_rate)
job_limiter = RateLimiter(settings.job_rate)


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.exception_handler(ytdl.FetchError)
async def fetch_error_handler(request: Request, exc: ytdl.FetchError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status if 400 <= exc.status < 600 else 502)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _js_runtime() -> str | None:
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return name
    return None


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "version": __version__,
        "yt_dlp": yt_dlp.version.__version__,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "js_runtime": _js_runtime(),
        "cookies": cookies.configured(),
        "po_token_server": pot.server.healthy(),
        "active_jobs": manager.active_count() if manager else 0,
    }


@app.get("/api/info")
async def info(request: Request, q: str = Query(..., max_length=2048, description="YouTube URL or video ID")):
    try:
        video_id = parse.extract_video_id(q)
    except parse.InvalidInput as e:
        raise HTTPException(400, detail=str(e))
    info_limiter.check(client_ip(request))
    raw = await run_in_threadpool(ytdl.fetch_info, parse.watch_url(video_id))
    if settings.max_duration and (raw.get("duration") or 0) > settings.max_duration:
        hours = settings.max_duration / 3600
        raise HTTPException(413, detail=f"This video is longer than this server's {hours:g}-hour limit.")
    data = formats.normalize_info(raw)
    if not data["video"] and not data["audio"]["available"]:
        raise HTTPException(404, detail="No downloadable streams were found for this video.")
    return data


class JobRequest(BaseModel):
    video_id: str = Field(..., min_length=11, max_length=11, pattern=r"^[A-Za-z0-9_-]{11}$")
    kind: Literal["video", "audio"]
    quality: int | None = None
    container: Literal["mp4", "webm", "mkv"] | None = None
    format: str | None = None
    bitrate: int | None = None


@app.post("/api/jobs", status_code=201)
def create_job(body: JobRequest, request: Request):
    try:
        formats.validate_choice(body.kind, body.quality, body.container, body.format, body.bitrate)
    except formats.BadRequest as e:
        raise HTTPException(400, detail=str(e))
    job_limiter.check(client_ip(request))
    url = parse.watch_url(body.video_id)
    raw = ytdl.fetch_info(url)  # cached from /api/info in the normal flow
    if settings.max_duration and (raw.get("duration") or 0) > settings.max_duration:
        raise HTTPException(413, detail="This video is longer than this server allows.")
    job = manager.submit(
        video_id=body.video_id,
        url=url,
        kind=body.kind,
        quality=body.quality,
        container=body.container,
        audio_format=body.format,
        bitrate=body.bitrate if body.kind == "audio" else None,
    )
    return job.public()


def _job_or_404(job_id: str):
    job = manager.get(job_id) if manager else None
    if not job:
        raise HTTPException(404, detail="That download has expired. Start it again.")
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    return _job_or_404(job_id).public()


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str):
    _job_or_404(job_id)
    return {"cancelled": manager.cancel(job_id)}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request):
    """Server-sent events stream of a job's progress."""
    job = _job_or_404(job_id)

    async def stream():
        last_version = -1
        last_beat = time.monotonic()
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                break
            if job.version != last_version:
                last_version = job.version
                yield f"data: {json.dumps(job.public())}\n\n"
                last_beat = time.monotonic()
                if job.stage in TERMINAL:
                    break
            elif time.monotonic() - last_beat > 15:
                yield ": keep-alive\n\n"
                last_beat = time.monotonic()
            await asyncio.sleep(0.25)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


def _content_disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode().replace('"', "").strip() or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@app.get("/api/jobs/{job_id}/file")
def job_file(job_id: str):
    job = _job_or_404(job_id)
    if job.stage != "done" or not job.file or not job.file.is_file():
        raise HTTPException(409, detail="This file isn't ready yet.")
    return FileResponse(
        job.file,
        filename=job.filename,
        headers={"Content-Disposition": _content_disposition(job.filename or job.file.name)},
    )


@app.get("/api/thumbnail/{video_id}")
def thumbnail(video_id: str, request: Request):
    """Download the highest resolution thumbnail as a file."""
    if not parse.VIDEO_ID_RE.match(video_id):
        raise HTTPException(400, detail="Invalid video ID.")
    info_limiter.check(client_ip(request))
    handlers = []
    if settings.proxy:
        handlers.append(urllib.request.ProxyHandler({"http": settings.proxy, "https": settings.proxy}))
    opener = urllib.request.build_opener(*handlers)
    for name in ("maxresdefault", "sddefault", "hqdefault"):
        try:
            with opener.open(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", timeout=10) as r:
                data = r.read(10 * 1024 * 1024)
            if data:
                return Response(
                    data,
                    media_type="image/jpeg",
                    headers={"Content-Disposition": _content_disposition(f"{video_id}-thumbnail.jpg")},
                )
        except Exception:  # noqa: BLE001 - try the next size
            continue
    raise HTTPException(404, detail="No thumbnail found.")


# ---------------------------------------------------------------------------
# Static site (mounted last so /api routes win)
# ---------------------------------------------------------------------------


@app.get("/api/{rest:path}", include_in_schema=False)
def api_not_found(rest: str):
    raise HTTPException(404, detail="Not found.")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
