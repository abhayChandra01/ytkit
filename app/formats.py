"""Turn yt-dlp's raw format list into download options, and options into yt-dlp settings.

YouTube serves the highest qualities (1080p and up) as separate video-only and
audio-only DASH streams. To get maximum quality we pick the best video stream for
the requested resolution plus the best audio stream, download both, and let ffmpeg
merge them. Audio conversions (MP3, WAV, FLAC...) are done by ffmpeg from the best
audio stream available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Audio catalogue
# ---------------------------------------------------------------------------

AUDIO_FORMATS: list[dict[str, Any]] = [
    {
        "id": "mp3",
        "label": "MP3",
        "desc": "Plays everywhere",
        "bitrates": [320, 256, 192, 128],
        "default": 320,
        "lossless": False,
    },
    {
        "id": "m4a",
        "label": "M4A",
        "desc": "AAC, great for Apple devices",
        "bitrates": [256, 192, 128],
        "default": 256,
        "lossless": False,
    },
    {
        "id": "opus",
        "label": "OPUS",
        "desc": "Best quality per byte",
        "bitrates": [160, 128, 96],
        "default": 160,
        "lossless": False,
    },
    {
        "id": "ogg",
        "label": "OGG",
        "desc": "Vorbis, open format",
        "bitrates": [320, 192, 128],
        "default": 192,
        "lossless": False,
    },
    {
        "id": "wav",
        "label": "WAV",
        "desc": "Uncompressed PCM, for editing",
        "bitrates": [],
        "default": None,
        "lossless": True,
    },
    {
        "id": "flac",
        "label": "FLAC",
        "desc": "Lossless and compressed",
        "bitrates": [],
        "default": None,
        "lossless": True,
    },
    {
        "id": "original",
        "label": "Original",
        "desc": "Untouched source stream, no re-encode",
        "bitrates": [],
        "default": None,
        "lossless": False,
    },
]

AUDIO_IDS = {f["id"]: f for f in AUDIO_FORMATS}
VIDEO_CONTAINERS = ("mp4", "webm", "mkv")
# Formats where yt-dlp can embed cover art.
THUMBNAIL_EMBEDDABLE = {"mp3", "m4a", "flac", "opus", "ogg"}
# yt-dlp's name for each codec in FFmpegExtractAudio.
_EXTRACT_CODEC = {
    "mp3": "mp3",
    "m4a": "m4a",
    "opus": "opus",
    "ogg": "vorbis",
    "wav": "wav",
    "flac": "flac",
    "original": "best",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def codec_name(codec: str | None) -> str:
    c = (codec or "").lower()
    if not c or c == "none":
        return ""
    if c.startswith(("avc", "h264")):
        return "H.264"
    if c.startswith(("vp09", "vp9")):
        return "VP9"
    if c.startswith("av01"):
        return "AV1"
    if c.startswith(("hev", "hvc", "h265")):
        return "HEVC"
    if c.startswith("mp4a"):
        return "AAC"
    if c.startswith("opus"):
        return "Opus"
    if c.startswith("vorbis"):
        return "Vorbis"
    if c.startswith("ac-3") or c.startswith("ec-3"):
        return "Dolby"
    return c.split(".")[0].upper()


def _is_h264(f: dict) -> bool:
    return (f.get("vcodec") or "").lower().startswith(("avc", "h264"))


def _is_webm_video(f: dict) -> bool:
    return (f.get("vcodec") or "").lower().startswith(("vp09", "vp9", "vp8", "av01"))


def _is_hdr(f: dict) -> bool:
    dr = (f.get("dynamic_range") or "SDR").upper()
    return dr not in ("SDR", "")


def estimate_size(f: dict | None, duration: float | None) -> int | None:
    if not f:
        return None
    size = f.get("filesize") or f.get("filesize_approx")
    if size:
        return int(size)
    tbr = f.get("tbr") or ((f.get("vbr") or 0) + (f.get("abr") or 0))
    if tbr and duration:
        return int(tbr * 1000 / 8 * duration)
    return None


def short_side(f: dict) -> int | None:
    """Quality label in YouTube terms: the shorter side, so a vertical 1080x1920 Short is 1080p."""
    h, w = f.get("height"), f.get("width")
    if h and w:
        return int(min(h, w))
    return int(h) if h else None


def quality_badge(p: int) -> str | None:
    if p >= 4320:
        return "8K"
    if p >= 2160:
        return "4K"
    if p >= 1440:
        return "2K"
    if p >= 1080:
        return "Full HD"
    if p >= 720:
        return "HD"
    return None


def _usable(f: dict) -> bool:
    if f.get("has_drm"):
        return False
    if (f.get("ext") or "") == "mhtml":  # storyboards
        return False
    if "storyboard" in (f.get("format_note") or "").lower():
        return False
    return bool(f.get("url") or f.get("fragments") or f.get("manifest_url"))


def _proto_rank(f: dict) -> int:
    proto = (f.get("protocol") or "").lower()
    return 1 if proto.startswith("http") and "m3u8" not in proto else 0


def _video_key(f: dict) -> tuple:
    return (f.get("fps") or 0, _proto_rank(f), f.get("tbr") or f.get("vbr") or 0)


def _audio_key(f: dict) -> tuple:
    note = (f.get("format_note") or "").lower()
    original = 1 if "original" in note or "default" in note else 0
    return (
        original,
        f.get("language_preference") or 0,
        _proto_rank(f),
        f.get("abr") or f.get("tbr") or 0,
        f.get("asr") or 0,
    )


def _best(items: list[dict], key) -> dict | None:
    return max(items, key=key) if items else None


# ---------------------------------------------------------------------------
# Normalising yt-dlp metadata for the frontend
# ---------------------------------------------------------------------------


@dataclass
class Streams:
    """The concrete stream IDs to download for one choice."""

    video_id: str | None
    audio_id: str | None
    size: int | None
    vcodec: str
    acodec: str
    fps: int | None
    hdr: bool


def split_formats(formats: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    video_only, audio_only, muxed = [], [], []
    for f in formats or []:
        if not _usable(f):
            continue
        v = (f.get("vcodec") or "none") != "none"
        a = (f.get("acodec") or "none") != "none"
        fid = str(f.get("format_id") or "")
        if a and not v and fid.endswith("-drc"):
            continue  # dynamic-range-compressed duplicates
        if v and a:
            muxed.append(f)
        elif v and f.get("height"):
            video_only.append(f)
        elif a and not v:
            audio_only.append(f)
    return video_only, audio_only, muxed


def pick_audio(audio_only: list[dict], prefer: str | None = None) -> dict | None:
    if not audio_only:
        return None
    if prefer == "m4a":
        aac = [f for f in audio_only if (f.get("acodec") or "").startswith("mp4a")]
        if aac:
            return _best(aac, _audio_key)
    if prefer == "opus":
        opus = [f for f in audio_only if (f.get("acodec") or "").startswith("opus")]
        if opus:
            return _best(opus, _audio_key)
    return _best(audio_only, _audio_key)


def pick_streams(info: dict, height: int, container: str) -> Streams | None:
    """Choose the video+audio stream pair for a resolution and container."""
    video_only, audio_only, muxed = split_formats(info.get("formats") or [])
    duration = info.get("duration")
    at_res = [f for f in video_only if short_side(f) == height]

    chosen: dict | None = None
    if at_res:
        sdr = [f for f in at_res if not _is_hdr(f)] or at_res
        if container == "mp4":
            chosen = _best([f for f in sdr if _is_h264(f)], _video_key) or _best(sdr, _video_key)
        elif container == "webm":
            chosen = _best([f for f in at_res if _is_webm_video(f)], _video_key)
        else:  # mkv: absolute best, HDR welcome
            chosen = _best(at_res, lambda f: (_is_hdr(f),) + _video_key(f))

    if chosen is None:
        # Only a pre-muxed stream at this resolution (or nothing at all).
        mux = [f for f in muxed if short_side(f) == height]
        if container == "webm":
            mux = [f for f in mux if _is_webm_video(f)]
        m = _best(mux, _video_key)
        if not m:
            return None
        return Streams(
            video_id=str(m["format_id"]),
            audio_id=None,
            size=estimate_size(m, duration),
            vcodec=codec_name(m.get("vcodec")),
            acodec=codec_name(m.get("acodec")),
            fps=int(m["fps"]) if m.get("fps") else None,
            hdr=_is_hdr(m),
        )

    prefer = {"mp4": "m4a", "webm": "opus"}.get(container)
    audio = pick_audio(audio_only, prefer)
    vsize = estimate_size(chosen, duration)
    asize = estimate_size(audio, duration)
    size = (vsize or 0) + (asize or 0) or None
    return Streams(
        video_id=str(chosen["format_id"]),
        audio_id=str(audio["format_id"]) if audio else None,
        size=size,
        vcodec=codec_name(chosen.get("vcodec")),
        acodec=codec_name(audio.get("acodec")) if audio else "",
        fps=int(chosen["fps"]) if chosen.get("fps") else None,
        hdr=_is_hdr(chosen),
    )


def video_options(info: dict) -> list[dict]:
    video_only, _, muxed = split_formats(info.get("formats") or [])
    heights = sorted({short_side(f) for f in video_only + muxed if short_side(f)}, reverse=True)
    options = []
    for p in heights:
        variants: dict[str, dict | None] = {}
        for container in VIDEO_CONTAINERS:
            s = pick_streams(info, p, container)
            variants[container] = (
                None
                if s is None
                else {"size": s.size, "vcodec": s.vcodec, "acodec": s.acodec, "fps": s.fps, "hdr": s.hdr}
            )
        if not any(variants.values()):
            continue
        best = variants["mkv"] or variants["mp4"] or variants["webm"]
        options.append(
            {
                "quality": p,
                "label": f"{p}p",
                "badge": quality_badge(p),
                "fps": max((v["fps"] or 0) for v in variants.values() if v) or None,
                "hdr": any(v["hdr"] for v in variants.values() if v),
                "codec": best["vcodec"] if best else "",
                "variants": variants,
            }
        )
    return options


def audio_summary(info: dict) -> dict:
    _, audio_only, muxed = split_formats(info.get("formats") or [])
    best = pick_audio(audio_only) or _best(muxed, lambda f: f.get("abr") or 0)
    if not best:
        return {"available": False, "formats": AUDIO_FORMATS, "source": None}
    return {
        "available": True,
        "formats": AUDIO_FORMATS,
        "source": {
            "codec": codec_name(best.get("acodec")),
            "abr": round(best.get("abr") or best.get("tbr") or 0) or None,
            "asr": best.get("asr"),
            "channels": best.get("audio_channels"),
            "size": estimate_size(best, info.get("duration")),
        },
    }


def _best_thumbnail(info: dict) -> str | None:
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    jpg = [t for t in thumbs if ".webp" not in t["url"]]
    pool = jpg or thumbs
    if pool:
        t = max(pool, key=lambda t: ((t.get("width") or 0) * (t.get("height") or 0), t.get("preference") or -99))
        if t.get("width"):
            return t["url"]
    if info.get("id"):
        return f"https://i.ytimg.com/vi/{info['id']}/hqdefault.jpg"
    return info.get("thumbnail")


def normalize_info(info: dict) -> dict:
    duration = info.get("duration")
    width, height = info.get("width"), info.get("height")
    vertical = bool(width and height and height > width)
    return {
        "id": info.get("id"),
        "title": info.get("title") or "Untitled",
        "channel": info.get("channel") or info.get("uploader") or "",
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "channel_verified": bool(info.get("channel_is_verified")),
        "duration": duration,
        "duration_string": info.get("duration_string"),
        "thumbnail": _best_thumbnail(info),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "upload_date": info.get("upload_date"),
        "is_short": vertical or "/shorts/" in (info.get("webpage_url") or ""),
        "webpage_url": info.get("webpage_url"),
        "video": video_options(info),
        "audio": audio_summary(info),
    }


# ---------------------------------------------------------------------------
# Building yt-dlp options for a download
# ---------------------------------------------------------------------------


class BadRequest(ValueError):
    pass


def validate_choice(kind: str, quality: int | None, container: str | None, audio_format: str | None, bitrate: int | None):
    if kind == "video":
        if container not in VIDEO_CONTAINERS:
            raise BadRequest("Unknown video format.")
        if not quality or not (100 <= int(quality) <= 4320):
            raise BadRequest("Unknown video quality.")
    elif kind == "audio":
        fmt = AUDIO_IDS.get(audio_format or "")
        if not fmt:
            raise BadRequest("Unknown audio format.")
        if fmt["bitrates"] and bitrate not in fmt["bitrates"]:
            raise BadRequest("Unsupported bitrate for that format.")
    else:
        raise BadRequest("Kind must be 'video' or 'audio'.")


def format_selector(info: dict | None, kind: str, quality: int | None, container: str | None, audio_format: str | None) -> tuple[str, list[str], int | None]:
    """Return (format string, format_sort, expected size) for yt-dlp.

    Exact stream IDs come first (from the metadata we already showed the user),
    with a generic sorted fallback in case the IDs changed in between.
    """
    if kind == "video":
        sort = [f"res:{quality}", "fps"]
        if container == "mp4":
            sort += ["hdr:sdr", "vcodec:h264", "acodec:aac"]
        elif container == "webm":
            sort += ["vcodec:vp9", "acodec:opus"]
        fallback = f"bv*+ba/b"
        if container == "webm":
            fallback = "bv*[vcodec~='^(vp0?9|av01)']+ba/bv*+ba/b"
        streams = pick_streams(info, int(quality), container) if info else None
        if streams:
            exact = streams.video_id + (f"+{streams.audio_id}" if streams.audio_id else "")
            return f"{exact}/{fallback}", sort, streams.size
        return fallback, sort, None

    # audio
    prefer = {"m4a": "m4a", "opus": "opus", "ogg": "opus", "original": None}.get(audio_format or "")
    fallback = "ba/b"
    if prefer == "m4a":
        fallback = "ba[acodec^=mp4a]/ba/b"
    elif prefer == "opus":
        fallback = "ba[acodec^=opus]/ba/b"
    if info:
        _, audio_only, _ = split_formats(info.get("formats") or [])
        a = pick_audio(audio_only, prefer)
        if a:
            return f"{a['format_id']}/{fallback}", [], estimate_size(a, info.get("duration"))
    return fallback, [], None


def postprocessors(kind: str, container: str | None, audio_format: str | None, bitrate: int | None, embed: bool) -> tuple[list[dict], bool]:
    """Return (postprocessor list, whether to write a thumbnail)."""
    pps: list[dict] = []
    write_thumb = False
    if kind == "video":
        if container in ("mp4", "mkv"):
            pps.append({"key": "FFmpegVideoRemuxer", "preferedformat": container})
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
        return pps, False

    codec = _EXTRACT_CODEC[audio_format or "mp3"]
    if embed and audio_format in THUMBNAIL_EMBEDDABLE:
        write_thumb = True
        pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
    extract = {"key": "FFmpegExtractAudio", "preferredcodec": codec, "nopostoverwrites": False}
    if bitrate:
        extract["preferredquality"] = str(bitrate)
    elif codec in ("wav", "flac"):
        extract["preferredquality"] = "0"
    pps.append(extract)
    if embed:
        pps.append({"key": "FFmpegMetadata", "add_metadata": True})
    if write_thumb:
        pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    return pps, write_thumb
