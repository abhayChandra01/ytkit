"""Thin wrapper around yt-dlp: metadata lookup with caching, and downloads."""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from .config import settings

log = logging.getLogger("ytkit.ytdl")


class FetchError(Exception):
    """A user-facing error from YouTube or yt-dlp."""

    def __init__(self, message: str, status: int = 502, raw: str = ""):
        super().__init__(message)
        self.status = status
        self.raw = raw


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def friendly_error(err: Exception) -> FetchError:
    """Map yt-dlp's messages onto something a person can act on."""
    fe = _friendly(_ANSI.sub("", str(err)))
    fe.raw = _ANSI.sub("", str(err))
    return fe


def _friendly(raw: str) -> FetchError:
    msg = raw.lower()
    if "private video" in msg:
        return FetchError("This video is private.", 403)
    if "sign in to confirm your age" in msg or "age-restricted" in msg or "inappropriate for some users" in msg:
        return FetchError("This video is age-restricted. The server needs a cookies file to fetch it (see README).", 403)
    if "sign in to confirm you" in msg and "bot" in msg:
        return FetchError(
            "YouTube is asking this server to prove it's not a bot. Add a cookies file or a proxy (see README).",
            503,
        )
    if "members-only" in msg or "join this channel" in msg:
        return FetchError("This video is for channel members only.", 403)
    if "video unavailable" in msg or "not available" in msg or "has been removed" in msg or "does not exist" in msg:
        return FetchError("This video is unavailable. It may have been removed or blocked in the server's region.", 404)
    if "premieres in" in msg or "live event will begin" in msg or "is upcoming" in msg:
        return FetchError("This video hasn't premiered yet.", 409)
    if "http error 429" in msg or "too many requests" in msg:
        return FetchError("YouTube is rate-limiting this server. Try again in a few minutes.", 503)
    if "ffmpeg" in msg and ("not found" in msg or "not installed" in msg):
        return FetchError("ffmpeg isn't installed on the server.", 500)
    if "unable to download" in msg or "timed out" in msg or "connection" in msg:
        return FetchError("Couldn't reach YouTube from the server. Please try again.", 502)
    log.warning("Unmapped yt-dlp error: %s", raw)
    return FetchError("Something went wrong while talking to YouTube. Please try again.", 502)


def base_options() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "logger": _QuietLogger(),
        "socket_timeout": 20,
        "retries": 5,
        "fragment_retries": 10,
        "extractor_retries": 2,
        # yt-dlp needs a JavaScript runtime to solve YouTube's player challenges.
        # Whichever of these is installed will be used.
        "js_runtimes": {"deno": {}, "node": {}, "bun": {}},
    }
    if settings.cookies_file:
        opts["cookiefile"] = settings.cookies_file
    if settings.proxy:
        opts["proxy"] = settings.proxy
    return opts


class _QuietLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        log.debug("yt-dlp warning: %s", msg)

    def error(self, msg):
        log.debug("yt-dlp error: %s", msg)


# ---------------------------------------------------------------------------
# Metadata, with a small TTL cache so browsing the options never re-hits YouTube
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}


def _cache_get(key: str) -> dict | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < settings.info_ttl:
            return hit[1]
        if hit:
            _cache.pop(key, None)
    return None


def _cache_put(key: str, info: dict) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), info)
        if len(_cache) > 256:
            oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[:64]
            for k, _ in oldest:
                _cache.pop(k, None)


def fetch_info(url: str, *, use_cache: bool = True) -> dict:
    """Return yt-dlp's full info dict for a single video."""
    if use_cache:
        cached = _cache_get(url)
        if cached is not None:
            return cached

    # If another request is already fetching this URL, wait for it instead of
    # sending YouTube a duplicate request.
    with _cache_lock:
        waiter = _inflight.get(url)
        if waiter is None:
            _inflight[url] = threading.Event()
    if waiter is not None:
        waiter.wait(timeout=60)
        cached = _cache_get(url)
        if cached is not None:
            return cached

    try:
        opts = base_options() | {"skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise FetchError("YouTube returned no data for this video.", 502)
        if info.get("_type") == "playlist":
            raise FetchError("Playlists aren't supported yet. Paste a single video link.", 400)
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
            raise FetchError("Live streams can't be downloaded until they've ended.", 409)
        info = ydl.sanitize_info(info)
        _cache_put(url, info)
        return info
    except FetchError:
        raise
    except (DownloadError, ExtractorError) as e:
        raise friendly_error(e) from e
    finally:
        with _cache_lock:
            ev = _inflight.pop(url, None)
        if ev is not None:
            ev.set()


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[dict], None]

_PP_LABELS = {
    "Merger": "Merging video and audio",
    "FFmpegMerger": "Merging video and audio",
    "ExtractAudio": "Converting audio",
    "FFmpegExtractAudio": "Converting audio",
    "VideoRemuxer": "Finalizing container",
    "FFmpegVideoRemuxer": "Finalizing container",
    "Metadata": "Writing tags",
    "FFmpegMetadata": "Writing tags",
    "EmbedThumbnail": "Adding cover art",
    "ThumbnailsConvertor": "Preparing cover art",
    "FFmpegThumbnailsConvertor": "Preparing cover art",
}


def download(
    url: str,
    out_dir: Path,
    *,
    format_spec: str,
    format_sort: list[str],
    postprocessors: list[dict],
    write_thumbnail: bool,
    merge_format: str | None,
    expected_size: int | None,
    on_progress: ProgressCallback,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    """Download (and convert) one video. Returns the path of the finished file."""
    out_dir.mkdir(parents=True, exist_ok=True)

    finished_bytes = 0  # bytes of parts that are fully downloaded
    seen_done: set[str] = set()
    last_pct = 0.0

    def progress_hook(d: dict) -> None:
        nonlocal finished_bytes, last_pct
        if is_cancelled():
            raise DownloadError("Cancelled")
        status = d.get("status")
        fname = d.get("filename") or ""
        if status == "downloading":
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            grand = max(expected_size or 0, finished_bytes + max(total, done))
            pct = (finished_bytes + done) / grand * 100 if grand else 0.0
            pct = min(max(pct, last_pct), 99.0)
            last_pct = pct
            on_progress(
                {
                    "stage": "downloading",
                    "percent": round(pct, 1),
                    "downloaded": finished_bytes + done,
                    "total": grand or None,
                    "speed": d.get("speed"),
                    "eta": d.get("eta"),
                }
            )
        elif status == "finished" and fname not in seen_done:
            seen_done.add(fname)
            finished_bytes += d.get("total_bytes") or d.get("downloaded_bytes") or 0

    def pp_hook(d: dict) -> None:
        if is_cancelled():
            raise DownloadError("Cancelled")
        if d.get("status") == "started":
            name = d.get("postprocessor") or ""
            label = _PP_LABELS.get(name)
            if label:
                on_progress({"stage": "processing", "percent": 100.0, "message": label})

    opts = base_options() | {
        "format": format_spec,
        "outtmpl": {"default": "%(title).120B [%(id)s].%(ext)s", "thumbnail": "%(title).120B [%(id)s].%(ext)s"},
        "paths": {"home": str(out_dir), "temp": str(out_dir)},
        "windowsfilenames": True,
        "overwrites": True,
        "continuedl": False,
        "concurrent_fragment_downloads": 4,
        "http_chunk_size": 10 * 1024 * 1024,
        "postprocessors": postprocessors,
        "writethumbnail": write_thumbnail,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [pp_hook],
    }
    if format_sort:
        opts["format_sort"] = format_sort
    if merge_format:
        opts["merge_output_format"] = merge_format

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except (DownloadError, ExtractorError) as e:
        if is_cancelled():
            raise FetchError("Cancelled.", 499) from e
        raise friendly_error(e) from e

    return _find_output(info, out_dir)


_TEMP_SUFFIXES = (".part", ".ytdl", ".temp", ".jpg", ".webp", ".png")


def _find_output(info: dict | None, out_dir: Path) -> Path:
    for item in (info or {}).get("requested_downloads") or []:
        fp = item.get("filepath") or item.get("_filename")
        if fp and Path(fp).is_file():
            return Path(fp)
    candidates = [
        p for p in out_dir.iterdir() if p.is_file() and not p.name.endswith(_TEMP_SUFFIXES) and ".f" not in p.suffixes[:-1]
    ]
    if not candidates:
        raise FetchError("The download finished but no file was produced.", 500)
    return max(candidates, key=lambda p: p.stat().st_size)
