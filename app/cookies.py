"""YouTube cookies, from an environment variable or a file.

Hosts like Vercel can't mount files, so the cookies.txt contents can be pasted
straight into the YTKIT_COOKIES environment variable (raw text or base64).

yt-dlp writes cookies back to the file when it finishes, so every yt-dlp run gets
its own writable temporary copy. That also works when the original file lives on
a read-only mount such as Render's /etc/secrets.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import tempfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

log = logging.getLogger("ytkit.cookies")

HEADER = "# Netscape HTTP Cookie File"


def normalize(text: str) -> str:
    """Repair common copy/paste damage: CRLF endings, tabs turned into spaces, a missing header."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        if "\t" not in line:
            parts = line.split()
            if len(parts) >= 7:
                line = "\t".join(parts[:6] + [" ".join(parts[6:])])
            elif len(parts) == 6:  # cookie with an empty value
                line = "\t".join(parts + [""])
        lines.append(line)
    return HEADER + "\n\n" + "\n".join(lines) + "\n"


def _decode(value: str) -> str:
    value = value.strip()
    if "\t" in value or "youtube.com" in value or value.startswith("#"):
        return value
    try:
        decoded = base64.b64decode(value, validate=False).decode("utf-8")
        if "youtube.com" in decoded:
            return decoded
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    return value


@lru_cache(maxsize=1)
def cookie_text() -> str | None:
    env = os.getenv("YTKIT_COOKIES", "").strip()
    if env:
        text = normalize(_decode(env))
        log.info("Using YouTube cookies from YTKIT_COOKIES (%d entries)", text.count("\n") - 2)
        return text
    path = os.getenv("YTKIT_COOKIES_FILE", "").strip()
    if path:
        try:
            text = normalize(Path(path).read_text(encoding="utf-8", errors="replace"))
            log.info("Using YouTube cookies from %s", path)
            return text
        except OSError as e:
            log.error("Couldn't read YTKIT_COOKIES_FILE %s: %s", path, e)
    return None


def configured() -> bool:
    return cookie_text() is not None


@contextmanager
def cookie_file() -> Iterator[str | None]:
    """A private, writable copy of the cookies for one yt-dlp run (or None)."""
    text = cookie_text()
    if not text:
        yield None
        return
    fd, path = tempfile.mkstemp(prefix="ytkit-cookies-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(path, 0o600)
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
