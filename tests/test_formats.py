from app import formats
from tests.fixtures import sample_info


def test_video_options_cover_all_resolutions():
    opts = formats.video_options(sample_info())
    assert [o["quality"] for o in opts] == [2160, 1440, 1080, 720, 480, 360, 240, 144]
    top = opts[0]
    assert top["badge"] == "4K" and top["fps"] == 60 and top["hdr"] is True
    assert opts[2]["badge"] == "Full HD"


def test_vertical_short_uses_short_side():
    opts = formats.video_options(sample_info(vertical=True))
    assert opts[0]["quality"] == 2160
    assert {o["label"] for o in opts} >= {"1080p", "720p"}


def test_mp4_prefers_h264_and_aac():
    s = formats.pick_streams(sample_info(), 1080, "mp4")
    assert (s.video_id, s.audio_id) == ("137", "140")
    assert s.vcodec == "H.264" and s.acodec == "AAC"


def test_mp4_above_1080_falls_back_to_best_sdr_codec():
    s = formats.pick_streams(sample_info(), 2160, "mp4")
    assert s.video_id == "313" and not s.hdr


def test_webm_uses_vp9_or_av1_with_opus():
    s = formats.pick_streams(sample_info(), 720, "webm")
    assert s.video_id in {"247", "398"} and s.audio_id == "251"


def test_mkv_takes_hdr_at_4k():
    s = formats.pick_streams(sample_info(), 2160, "mkv")
    assert s.video_id == "337" and s.hdr


def test_audio_skips_drc_and_dubbed_tracks():
    _, audio, _ = formats.split_formats(sample_info()["formats"])
    assert "140-drc" not in {a["format_id"] for a in audio}
    assert formats.pick_audio(audio)["format_id"] == "251"
    assert formats.pick_audio(audio, "m4a")["format_id"] == "140"


def test_size_estimate_from_bitrate():
    s = formats.pick_streams(sample_info(), 1080, "mp4")
    assert s.size == int((1080 * 3 + 129) * 1000 / 8 * 212)


def test_selector_uses_exact_ids_with_fallback():
    spec, sort, size = formats.format_selector(sample_info(), "video", 1080, "mp4", None)
    assert spec.startswith("137+140/")
    assert sort[0] == "res:1080"
    spec, _, _ = formats.format_selector(sample_info(), "audio", None, None, "mp3")
    assert spec.startswith("251/")
    spec, _, _ = formats.format_selector(None, "audio", None, None, "m4a")
    assert spec == "ba[acodec^=mp4a]/ba/b"


def test_postprocessors_for_mp3():
    pps, thumb = formats.postprocessors("audio", None, "mp3", 320, True)
    keys = [p["key"] for p in pps]
    assert keys == ["FFmpegThumbnailsConvertor", "FFmpegExtractAudio", "FFmpegMetadata", "EmbedThumbnail"]
    assert pps[1]["preferredcodec"] == "mp3" and pps[1]["preferredquality"] == "320"
    assert thumb


def test_postprocessors_for_wav_have_no_cover():
    pps, thumb = formats.postprocessors("audio", None, "wav", None, True)
    assert not thumb and "EmbedThumbnail" not in [p["key"] for p in pps]


def test_validate_choice():
    import pytest
    formats.validate_choice("video", 1080, "mp4", None, None)
    formats.validate_choice("audio", None, None, "flac", None)
    for args in [("video", 1080, "avi", None, None), ("audio", None, None, "mp3", 999), ("audio", None, None, "exe", None), ("x", 1, "mp4", None, None)]:
        with pytest.raises(formats.BadRequest):
            formats.validate_choice(*args)


def test_normalize_info_shape():
    data = formats.normalize_info(sample_info())
    assert data["id"] == "dQw4w9WgXcQ"
    assert data["thumbnail"].endswith("maxresdefault.jpg")
    assert data["audio"]["source"]["codec"] == "Opus"
    assert data["audio"]["available"]


def test_youtube_label_wins_for_widescreen():
    f = {"height": 800, "width": 1920, "format_note": "1080p60"}
    assert formats.short_side(f) == 1080
    assert formats.short_side({"height": 800, "width": 1920}) == 800
