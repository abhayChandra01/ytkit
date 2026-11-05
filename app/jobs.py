"""Background download jobs: queueing, progress tracking and cleanup."""

from __future__ import annotations

import logging
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import formats, ytdl
from .config import settings

log = logging.getLogger("ytkit.jobs")

TERMINAL = {"done", "error", "cancelled"}


@dataclass
class Job:
    id: str
    video_id: str
    kind: str
    label: str
    created: float = field(default_factory=time.time)
    stage: str = "queued"
    percent: float = 0.0
    message: str = "Waiting for a free slot"
    downloaded: int | None = None
    total: int | None = None
    speed: float | None = None
    eta: int | None = None
    error: str | None = None
    file: Path | None = None
    filename: str | None = None
    size: int | None = None
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    version: int = 0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "video_id": self.video_id,
            "kind": self.kind,
            "label": self.label,
            "stage": self.stage,
            "percent": round(self.percent, 1),
            "message": self.message,
            "downloaded": self.downloaded,
            "total": self.total,
            "speed": self.speed,
            "eta": self.eta,
            "error": self.error,
            "filename": self.filename,
            "size": self.size,
            "download_url": f"/api/jobs/{self.id}/file" if self.stage == "done" else None,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=settings.max_concurrent, thread_name_prefix="ytkit-dl")
        self._stop = threading.Event()
        self._janitor = threading.Thread(target=self._cleanup_loop, name="ytkit-janitor", daemon=True)
        settings.download_dir.mkdir(parents=True, exist_ok=True)
        self._purge_orphans()
        self._janitor.start()

    # -- public API ---------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.stage not in TERMINAL)

    def submit(
        self,
        *,
        video_id: str,
        url: str,
        kind: str,
        quality: int | None,
        container: str | None,
        audio_format: str | None,
        bitrate: int | None,
    ) -> Job:
        if self.active_count() >= settings.max_concurrent * 10:
            raise ytdl.FetchError("The server is busy right now. Please try again in a minute.", 503)

        if kind == "video":
            label = f"{quality}p {container.upper()}"
        else:
            fmt = formats.AUDIO_IDS[audio_format]
            label = fmt["label"] + (f" {bitrate} kbps" if bitrate else "")

        job = Job(id=secrets.token_urlsafe(12), video_id=video_id, kind=kind, label=label)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job, url, kind, quality, container, audio_format, bitrate)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job or job.stage in TERMINAL:
            return False
        job.cancel.set()
        if job.stage == "queued":
            self._update(job, stage="cancelled", message="Cancelled")
        return True

    def shutdown(self) -> None:
        self._stop.set()
        for job in list(self._jobs.values()):
            job.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- internals ----------------------------------------------------------

    def _update(self, job: Job, **changes: Any) -> None:
        with self._lock:
            for k, v in changes.items():
                setattr(job, k, v)
            job.version += 1

    def _job_dir(self, job: Job) -> Path:
        return settings.download_dir / job.id

    def _run(self, job: Job, url: str, kind: str, quality, container, audio_format, bitrate) -> None:
        if job.cancel.is_set():
            return
        self._update(job, stage="preparing", message="Fetching stream details", percent=0.0)
        out_dir = self._job_dir(job)
        try:
            info = ytdl.fetch_info(url)
            spec, sort, expected = formats.format_selector(info, kind, quality, container, audio_format)
            embed = settings.embed_metadata
            pps, write_thumb = formats.postprocessors(kind, container, audio_format, bitrate, embed)
            merge = container if kind == "video" else None
            if merge == "webm":
                merge = "webm/mkv"

            def on_progress(p: dict) -> None:
                changes: dict[str, Any] = {"stage": p["stage"], "percent": p.get("percent", job.percent)}
                if p["stage"] == "downloading":
                    changes.update(
                        message="Downloading",
                        downloaded=p.get("downloaded"),
                        total=p.get("total"),
                        speed=p.get("speed"),
                        eta=p.get("eta"),
                    )
                else:
                    changes.update(message=p.get("message", "Processing"), speed=None, eta=None)
                self._update(job, **changes)

            self._update(job, stage="downloading", message="Starting download")
            try:
                path = ytdl.download(
                    url,
                    out_dir,
                    format_spec=spec,
                    format_sort=sort,
                    postprocessors=pps,
                    write_thumbnail=write_thumb,
                    merge_format=merge,
                    expected_size=expected,
                    on_progress=on_progress,
                    is_cancelled=job.cancel.is_set,
                )
            except ytdl.FetchError as first:
                # Cover art is a nice-to-have: if embedding it broke the job, retry without it.
                raw = first.raw.lower()
                if not write_thumb or job.cancel.is_set() or not ("thumbnail" in raw or "embed" in raw):
                    raise
                log.info("Retrying job %s without cover art after: %s", job.id, first)
                shutil.rmtree(out_dir, ignore_errors=True)
                pps = [p for p in pps if p["key"] not in ("EmbedThumbnail", "FFmpegThumbnailsConvertor")]
                path = ytdl.download(
                    url,
                    out_dir,
                    format_spec=spec,
                    format_sort=sort,
                    postprocessors=pps,
                    write_thumbnail=False,
                    merge_format=merge,
                    expected_size=expected,
                    on_progress=on_progress,
                    is_cancelled=job.cancel.is_set,
                )

            if job.cancel.is_set():
                raise ytdl.FetchError("Cancelled.", 499)
            self._update(
                job,
                stage="done",
                percent=100.0,
                message="Ready",
                file=path,
                filename=path.name,
                size=path.stat().st_size,
                finished=time.time(),
                speed=None,
                eta=None,
            )
        except ytdl.FetchError as e:
            cancelled = job.cancel.is_set()
            self._update(
                job,
                stage="cancelled" if cancelled else "error",
                message="Cancelled" if cancelled else "Failed",
                error=None if cancelled else str(e),
                finished=time.time(),
            )
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception as e:  # noqa: BLE001 - never let a worker die silently
            log.exception("Job %s crashed", job.id)
            self._update(job, stage="error", message="Failed", error="Unexpected server error. Please try again.", finished=time.time())
            shutil.rmtree(out_dir, ignore_errors=True)

    def _purge_orphans(self) -> None:
        """Delete leftovers from a previous run of the server."""
        for child in settings.download_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    def _cleanup_loop(self) -> None:
        while not self._stop.wait(60):
            try:
                self.cleanup()
            except Exception:  # noqa: BLE001
                log.exception("Cleanup failed")

    def cleanup(self, now: float | None = None) -> int:
        now = now or time.time()
        expired: list[Job] = []
        with self._lock:
            for job in list(self._jobs.values()):
                if job.finished and now - job.finished > settings.file_ttl:
                    expired.append(job)
                    self._jobs.pop(job.id, None)
        for job in expired:
            shutil.rmtree(self._job_dir(job), ignore_errors=True)
        return len(expired)
