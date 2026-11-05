"""A trimmed-down yt-dlp info dict shaped like a real YouTube response."""


def _v(fid, h, w, vcodec, fps=30, tbr=1000, hdr="SDR", ext="mp4", size=None):
    return {
        "format_id": fid, "height": h, "width": w, "vcodec": vcodec, "acodec": "none", "fps": fps,
        "tbr": tbr, "dynamic_range": hdr, "ext": ext, "protocol": "https", "url": "https://x/" + fid,
        "filesize": size,
    }


def _a(fid, acodec, abr, ext, note="medium", lang_pref=None):
    return {
        "format_id": fid, "vcodec": "none", "acodec": acodec, "abr": abr, "tbr": abr, "asr": 48000 if acodec == "opus" else 44100,
        "ext": ext, "protocol": "https", "url": "https://x/" + fid, "format_note": note, "audio_channels": 2,
        "language_preference": lang_pref,
    }


def sample_info(vertical=False):
    def dims(p):
        long = {144: 256, 240: 426, 360: 640, 480: 854, 720: 1280, 1080: 1920, 1440: 2560, 2160: 3840}[p]
        return (long, p) if vertical else (p, long)

    fmts = [
        {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none", "format_note": "storyboard", "url": "https://x/sb"},
        _a("139", "mp4a.40.5", 48, "m4a", "low"),
        _a("140", "mp4a.40.2", 129, "m4a"),
        _a("140-drc", "mp4a.40.2", 129, "m4a"),
        _a("251", "opus", 135, "webm"),
        _a("251-dub", "opus", 140, "webm", "Spanish", lang_pref=-1),
        {**_v("18", *dims(360), "avc1.42001E", tbr=500), "acodec": "mp4a.40.2"},
    ]
    for p, h264, vp9, av1 in [
        (144, "160", "278", "394"), (240, "133", "242", "395"), (360, "134", "243", "396"),
        (480, "135", "244", "397"), (720, "136", "247", "398"), (1080, "137", "248", "399"),
    ]:
        h, w = dims(p)
        fmts.append(_v(h264, h, w, "avc1.640028", tbr=p * 3))
        fmts.append(_v(vp9, h, w, "vp09.00.40.08", tbr=p * 2.5, ext="webm"))
        fmts.append(_v(av1, h, w, "av01.0.08M.08", tbr=p * 2))
    for p, vp9, av1 in [(1440, "271", "400"), (2160, "313", "401")]:
        h, w = dims(p)
        fmts.append(_v(vp9, h, w, "vp09.00.50.08", fps=60, tbr=p * 6, ext="webm"))
        fmts.append(_v(av1, h, w, "av01.0.12M.08", fps=60, tbr=p * 5))
    h, w = dims(2160)
    fmts.append(_v("337", h, w, "vp09.02.51.10", fps=60, tbr=20000, hdr="HDR10", ext="webm"))
    return {
        "id": "dQw4w9WgXcQ", "title": "Sample / Video: \"Test\"", "channel": "Sample Channel", "duration": 212,
        "duration_string": "3:32", "view_count": 1234567, "like_count": 4321, "upload_date": "20091025",
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "width": w, "height": h,
        "thumbnails": [
            {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", "width": 480, "height": 360},
            {"url": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/maxresdefault.webp", "width": 1920, "height": 1080},
            {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg", "width": 1280, "height": 720},
        ],
        "formats": fmts,
    }
