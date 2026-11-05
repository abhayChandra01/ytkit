import time

import pytest
from fastapi.testclient import TestClient

from app import main, ytdl
from tests.fixtures import sample_info


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ytdl, "fetch_info", lambda url, use_cache=True: sample_info())

    def fake_download(url, out_dir, *, on_progress, postprocessors, **kw):
        on_progress({"stage": "downloading", "percent": 50, "downloaded": 5, "total": 10, "speed": 1, "eta": 1})
        on_progress({"stage": "processing", "percent": 100, "message": "Converting audio"})
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "Sample ünïcode [dQw4w9WgXcQ].mp3"
        f.write_bytes(b"ID3fake")
        return f

    monkeypatch.setattr(ytdl, "download", fake_download)
    main.info_limiter.hits.clear()
    main.job_limiter.hits.clear()
    with TestClient(main.app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]


def test_index_and_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "YTKit" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_info(client):
    r = client.get("/api/info", params={"q": "https://youtu.be/dQw4w9WgXcQ"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "dQw4w9WgXcQ" and body["video"][0]["quality"] == 2160


def test_info_bad_input(client):
    r = client.get("/api/info", params={"q": "https://vimeo.com/1"})
    assert r.status_code == 400 and "YouTube" in r.json()["detail"]


def test_unknown_api_route_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["detail"]


def test_job_lifecycle(client):
    r = client.post("/api/jobs", json={"video_id": "dQw4w9WgXcQ", "kind": "audio", "format": "mp3", "bitrate": 320})
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]
    for _ in range(50):
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["stage"] == "done":
            break
        time.sleep(0.05)
    assert data["stage"] == "done" and data["download_url"]
    f = client.get(data["download_url"])
    assert f.status_code == 200 and f.content == b"ID3fake"
    assert "filename*=UTF-8''Sample%20%C3%BCn%C3%AFcode" in f.headers["content-disposition"]

    events = client.get(f"/api/jobs/{job_id}/events")
    assert '"stage": "done"' in events.text


def test_job_validation(client):
    bad = [
        {"video_id": "short", "kind": "video", "quality": 1080, "container": "mp4"},
        {"video_id": "dQw4w9WgXcQ", "kind": "video", "quality": 1080, "container": "avi"},
        {"video_id": "dQw4w9WgXcQ", "kind": "audio", "format": "mp3", "bitrate": 999},
    ]
    for body in bad:
        assert client.post("/api/jobs", json=body).status_code in (400, 422)


def test_missing_job(client):
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main.info_limiter, "per_minute", 2)
    codes = [client.get("/api/info", params={"q": "dQw4w9WgXcQ"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_friendly_errors():
    assert ytdl.friendly_error(Exception("ERROR: [youtube] x: Private video. Sign in")).status == 403
    assert "bot" in str(ytdl.friendly_error(Exception("Sign in to confirm you're not a bot")))
    assert ytdl.friendly_error(Exception("HTTP Error 429: Too Many Requests")).status == 503
