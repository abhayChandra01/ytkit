import base64
import os

import yt_dlp

from app import cookies

SAMPLE = (
    "# Netscape HTTP Cookie File\n"
    "# This is a generated file! Do not edit.\n\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tPREF\tf4=4000000&tz=Asia.Calcutta\n"
    ".youtube.com\tTRUE\t/\tTRUE\t0\tYSC\tabc123\n"
)


def _load(monkeypatch, value=None, file=None):
    monkeypatch.delenv("YTKIT_COOKIES", raising=False)
    monkeypatch.delenv("YTKIT_COOKIES_FILE", raising=False)
    if value is not None:
        monkeypatch.setenv("YTKIT_COOKIES", value)
    if file is not None:
        monkeypatch.setenv("YTKIT_COOKIES_FILE", str(file))
    cookies.cookie_text.cache_clear()
    with cookies.cookie_file() as path:
        if not path:
            return None, None
        with yt_dlp.YoutubeDL({"cookiefile": path, "quiet": True}) as ydl:
            jar = {c.name: c.value for c in ydl.cookiejar}
        return path, jar


def test_raw_env(monkeypatch):
    path, jar = _load(monkeypatch, SAMPLE)
    assert jar == {"PREF": "f4=4000000&tz=Asia.Calcutta", "YSC": "abc123"}
    assert not os.path.exists(path)  # temp copy is cleaned up


def test_base64_env(monkeypatch):
    _, jar = _load(monkeypatch, base64.b64encode(SAMPLE.encode()).decode())
    assert jar["YSC"] == "abc123"


def test_tabs_turned_into_spaces(monkeypatch):
    _, jar = _load(monkeypatch, SAMPLE.replace("\t", "    ").replace("\n", "\r\n"))
    assert jar["PREF"] == "f4=4000000&tz=Asia.Calcutta"


def test_read_only_file(monkeypatch, tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(SAMPLE)
    f.chmod(0o400)
    _, jar = _load(monkeypatch, file=f)
    assert jar["YSC"] == "abc123"


def test_none(monkeypatch):
    assert _load(monkeypatch) == (None, None)
