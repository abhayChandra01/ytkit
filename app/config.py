"""Runtime settings, all read from environment variables.

Every setting has a sensible default so the app runs with zero configuration.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Where finished files are written. Each job gets its own subfolder.
    download_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("YTKIT_DOWNLOAD_DIR") or Path(tempfile.gettempdir()) / "ytkit"
        )
    )
    # How many downloads may run at the same time. Extra jobs wait in a queue.
    max_concurrent: int = field(default_factory=lambda: max(1, _int("YTKIT_MAX_CONCURRENT", 3)))
    # Seconds a finished file is kept before it is deleted.
    file_ttl: int = field(default_factory=lambda: max(60, _int("YTKIT_FILE_TTL", 1800)))
    # Longest video (seconds) the server accepts. 0 disables the limit.
    max_duration: int = field(default_factory=lambda: max(0, _int("YTKIT_MAX_DURATION", 4 * 3600)))
    # Seconds the metadata for a video is cached.
    info_ttl: int = field(default_factory=lambda: max(0, _int("YTKIT_INFO_TTL", 600)))
    # Optional Netscape-format cookies file (helps on servers YouTube flags as bots).
    cookies_file: str | None = field(default_factory=lambda: os.getenv("YTKIT_COOKIES_FILE") or None)
    # Optional outbound proxy, e.g. http://user:pass@host:port or socks5://host:port.
    proxy: str | None = field(default_factory=lambda: os.getenv("YTKIT_PROXY") or None)
    # Requests per minute, per client IP. 0 disables rate limiting.
    info_rate: int = field(default_factory=lambda: max(0, _int("YTKIT_INFO_RATE", 30)))
    job_rate: int = field(default_factory=lambda: max(0, _int("YTKIT_JOB_RATE", 12)))
    # Trust X-Forwarded-For (set to true only behind a reverse proxy you control).
    trust_proxy: bool = field(default_factory=lambda: _bool("YTKIT_TRUST_PROXY", False))
    # Embed cover art and tags into audio files.
    embed_metadata: bool = field(default_factory=lambda: _bool("YTKIT_EMBED_METADATA", True))


settings = Settings()
