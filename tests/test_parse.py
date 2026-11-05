import pytest

from app.parse import InvalidInput, extract_video_id

ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "text",
    [
        ID,
        f" {ID} ",
        f"https://www.youtube.com/watch?v={ID}",
        f"https://youtube.com/watch?v={ID}&t=42s&list=PL123",
        f"http://m.youtube.com/watch?feature=share&v={ID}",
        f"https://music.youtube.com/watch?v={ID}&si=abc",
        f"https://youtu.be/{ID}",
        f"https://youtu.be/{ID}?si=xyz&t=10",
        f"youtu.be/{ID}",
        f"www.youtube.com/watch?v={ID}",
        f"https://www.youtube.com/shorts/{ID}",
        f"https://youtube.com/shorts/{ID}?feature=share",
        f"https://www.youtube.com/embed/{ID}?autoplay=1",
        f"https://www.youtube-nocookie.com/embed/{ID}",
        f"https://www.youtube.com/live/{ID}",
        f"https://www.youtube.com/v/{ID}",
        f"Check this out: https://youtu.be/{ID}!!",
        f"<https://www.youtube.com/watch?v={ID}>",
    ],
)
def test_extracts_id(text):
    assert extract_video_id(text) == ID


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "hello world",
        "https://vimeo.com/123456",
        "https://www.youtube.com/",
        "https://www.youtube.com/playlist?list=PL123",
        "https://evil.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
        "dQw4w9WgXc",
        "x" * 3000,
    ],
)
def test_rejects(text):
    with pytest.raises(InvalidInput):
        extract_video_id(text)
