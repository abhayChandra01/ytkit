"""Turn whatever the user pasted into a YouTube video ID."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_YT_HOSTS = {
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "gaming.youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
}

_PATH_PREFIXES = ("shorts", "embed", "live", "v", "e", "watch")


class InvalidInput(ValueError):
    """Raised when the input does not contain a YouTube video ID."""


def _host(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def extract_video_id(raw: str) -> str:
    """Return the 11-character video ID from a URL, a share link or a bare ID."""
    text = (raw or "").strip()
    if not text:
        raise InvalidInput("Paste a YouTube link or video ID.")
    if len(text) > 2048:
        raise InvalidInput("That input is too long to be a YouTube link.")

    # A bare ID, optionally wrapped in quotes or angle brackets.
    bare = text.strip("<>\"' ")
    if VIDEO_ID_RE.match(bare):
        return bare

    # Pull the first URL-looking token out of pasted text such as
    # "check this out https://youtu.be/abc123def45 !!"
    match = re.search(r"((?:https?://)?(?:[\w-]+\.)*(?:youtube(?:-nocookie)?\.com|youtu\.be)/\S+)", text, re.I)
    if not match:
        raise InvalidInput("That doesn't look like a YouTube link or video ID.")
    url = match.group(1).rstrip(").,!?;'\"<>]")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    parsed = urlparse(url)
    host = _host(parsed.netloc)
    if host not in _YT_HOSTS:
        raise InvalidInput("Only YouTube links are supported.")

    parts = [p for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)

    candidate: str | None = None
    if host == "youtu.be":
        candidate = parts[0] if parts else None
    elif "v" in query:
        candidate = query["v"][0]
    elif len(parts) >= 2 and parts[0] in _PATH_PREFIXES:
        candidate = parts[1]
    elif parts and parts[0] == "attribution_link" and "u" in query:
        return extract_video_id("https://www.youtube.com" + query["u"][0])

    if candidate and VIDEO_ID_RE.match(candidate):
        return candidate
    if "list" in query:
        raise InvalidInput("Playlists aren't supported yet. Open a single video and paste its link.")
    raise InvalidInput("Couldn't find a video ID in that link.")


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"
