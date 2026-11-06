import pytest
from yt_dlp.utils import DownloadError

from app import ytdl


class FakeYDL:
    calls = []

    def __init__(self, opts):
        self.clients = (opts.get("extractor_args") or {}).get("youtube", {}).get("player_client")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        FakeYDL.calls.append(self.clients)
        if self.clients != ["mweb", "tv", "web_safari"]:
            raise DownloadError("ERROR: [youtube] x: Sign in to confirm you’re not a bot. Use --cookies")
        return {"id": "dQw4w9WgXcQ", "formats": []}

    def sanitize_info(self, info):
        return info


@pytest.fixture
def fake(monkeypatch):
    FakeYDL.calls = []
    monkeypatch.setattr(ytdl.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(ytdl, "_working_clients", None)
    monkeypatch.setattr(ytdl, "_ENV_CLIENTS", None)
    ytdl._cache.clear()


def test_falls_back_to_other_clients_and_remembers(fake):
    info = ytdl.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", use_cache=False)
    assert info["id"] == "dQw4w9WgXcQ"
    assert FakeYDL.calls == [None, ["mweb", "tv", "web_safari"]]
    assert ytdl._working_clients == "mweb,tv,web_safari"
    assert ytdl.base_options()["extractor_args"]["youtube"]["player_client"] == ["mweb", "tv", "web_safari"]
    FakeYDL.calls = []
    ytdl.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", use_cache=False)
    assert FakeYDL.calls == [["mweb", "tv", "web_safari"]]


def test_other_errors_do_not_retry(fake, monkeypatch):
    def boom(self, url, download=False):
        FakeYDL.calls.append(self.clients)
        raise DownloadError("ERROR: Private video")

    monkeypatch.setattr(FakeYDL, "extract_info", boom)
    with pytest.raises(ytdl.FetchError) as e:
        ytdl.fetch_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", use_cache=False)
    assert e.value.status == 403 and len(FakeYDL.calls) == 1
