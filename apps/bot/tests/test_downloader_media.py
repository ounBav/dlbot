"""Preview (probe), quality selection, progress/cancel hooks and the yt-dlp call sequence (mocked)."""

import os
import time

import pytest
from yt_dlp.utils import DownloadCancelled, DownloadError as YtError

from app import downloader
from app.downloader import (DownloadError, _check_playable, _find_output, _options, _Progress,
                            _qualities, _single_entry, classify_probe_error)


def fmt(h=None, w=None, vcodec="avc1"):
    return {"height": h, "width": w, "vcodec": vcodec}


def test_qualities_follow_the_short_side_and_cap_at_1080():
    assert _qualities({"formats": [fmt(2160, 3840), fmt(720, 1280)]}) == [360, 480, 720, 1080]
    assert _qualities({"formats": [fmt(1280, 720)]}) == [360, 480, 720]        # vertical TikTok 720x1280
    assert _qualities({"formats": [fmt(240, 426)]}) == [360]                   # tiny: still one choice
    assert _qualities({"formats": [fmt(None, None, "none")]}) == []            # audio only
    assert _qualities({}) == []


def test_quality_pick_sorts_by_resolution_and_prefers_mp4():
    opts = _options("video", "/tmp/x", 1, quality=480)
    assert opts["format"] == "bv*+ba/b"
    assert opts["format_sort"] == ["res:480", "vcodec:h264", "ext:mp4:m4a"]
    assert opts["merge_output_format"] == "mp4"
    assert {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"} in opts["postprocessors"]
    assert opts["noplaylist"] is True and opts["cachedir"] is False


def test_chat_flow_options_are_unchanged_without_quality():
    opts = _options("video", "/tmp/x", 1)
    assert "format_sort" not in opts and "height<=720" in opts["format"]
    audio = _options("audio", "/tmp/x", 1, quality=1080)
    assert audio["format"] == "bestaudio/best" and "format_sort" not in audio


def test_live_playlist_and_too_long_are_refused():
    with pytest.raises(DownloadError) as e:
        _check_playable({"live_status": "is_live"}, 0)
    assert e.value.code == "E-LIVE"
    with pytest.raises(DownloadError) as e:
        _check_playable({"live_status": "is_upcoming"}, 0)
    assert e.value.code == "E-LIVE"
    with pytest.raises(DownloadError) as e:
        _check_playable({"duration": 1801}, 1800)
    assert e.value.code == "E-TOOLONG"
    _check_playable({"duration": 99999}, 0)       # 0 = no limit
    with pytest.raises(DownloadError) as e:
        _single_entry({"_type": "playlist", "entries": [{"id": 1}, {"id": 2}]})
    assert e.value.code == "E-PLAYLIST"
    assert _single_entry({"_type": "playlist", "entries": [{"id": 1}]}) == {"id": 1}


def test_probe_error_codes():
    assert classify_probe_error("Sign in to confirm you're not a bot") == "E-BLOCKED"
    assert classify_probe_error("The uploader has not made this video available in your country") == "E-GEO"
    assert classify_probe_error("Video unavailable. This video has been removed") == "E-UNAVAILABLE"
    assert classify_probe_error("Private video. Sign in if you've been granted access") == "E-PRIVATE"
    assert classify_probe_error("Unsupported URL: https://x") == "E-UNSUPPORTED"
    assert classify_probe_error("boom") == "E-FAILED"


def test_no_output_file_means_skipped_by_max_filesize(tmp_path):
    (tmp_path / "media.mp4.part").write_bytes(b"x")
    with pytest.raises(DownloadError) as e:
        _find_output(str(tmp_path))
    assert e.value.code == "E-TOOBIG"


def test_progress_is_monotonic_across_video_and_audio_parts():
    seen = []
    p = _Progress(lambda stage, f: seen.append((stage, f)), None)
    two = {"requested_formats": [{}, {}]}
    p.download_hook({"status": "downloading", "filename": "v", "downloaded_bytes": 50, "total_bytes": 100, "info_dict": two})
    p.download_hook({"status": "finished", "filename": "v", "info_dict": two})
    p.download_hook({"status": "downloading", "filename": "a", "downloaded_bytes": 0, "total_bytes": 100, "info_dict": two})
    p.download_hook({"status": "downloading", "filename": "a", "fragment_index": 1, "fragment_count": 2, "info_dict": two})
    p.postprocessor_hook({"status": "started"})
    fractions = [f for s, f in seen if f is not None]
    assert fractions == sorted(fractions)                 # never jumps back
    assert fractions[0] == 0.25 and fractions[1] == 0.5 and fractions[-1] == 0.75
    assert seen[-1] == ("processing", None)


def test_progress_hook_raises_when_cancelled():
    p = _Progress(None, lambda: True)
    with pytest.raises(DownloadCancelled):
        p.download_hook({"status": "downloading"})
    with pytest.raises(DownloadCancelled):
        p.postprocessor_hook({"status": "started"})


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: extract_info(download=False) then process_ie_result."""
    info: dict = {}
    error: Exception | None = None
    downloaded = False
    last_opts: dict = {}

    def __init__(self, opts):
        FakeYDL.last_opts = opts
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        assert download is False          # metadata first, checks, then process_ie_result
        if FakeYDL.error:
            raise FakeYDL.error
        return dict(FakeYDL.info)

    def process_ie_result(self, info, download=True):
        FakeYDL.downloaded = True
        for hook in self.opts.get("progress_hooks", []):
            hook({"status": "finished", "filename": "media.mp4", "info_dict": info})
        out = os.path.join(os.path.dirname(self.opts["outtmpl"]), "media.mp4")
        with open(out, "wb") as fh:
            fh.write(b"12345")
        return info


@pytest.fixture
def ydl(monkeypatch):
    FakeYDL.info = {"title": "Clip", "duration": 10}
    FakeYDL.error = None
    FakeYDL.downloaded = False
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


def test_download_checks_before_downloading_and_reports_progress(ydl, tmp_path):
    seen = []
    res = downloader.download("https://youtu.be/x", "video", str(tmp_path), 1000, quality=720,
                              on_progress=lambda s, f: seen.append((s, f)))
    assert res.title == "Clip" and res.size == 5 and res.path.endswith("media.mp4")
    assert ydl.downloaded and ("downloading", 1.0) in seen
    assert ydl.last_opts["format_sort"][0] == "res:720"


def test_download_refuses_live_and_too_long_without_downloading(ydl, tmp_path):
    ydl.info = {"title": "Live", "is_live": True}
    with pytest.raises(DownloadError) as e:
        downloader.download("https://youtu.be/x", "video", str(tmp_path), 1000)
    assert e.value.code == "E-LIVE" and not ydl.downloaded

    ydl.info = {"title": "Long", "duration": 4000}
    with pytest.raises(DownloadError) as e:
        downloader.download("https://youtu.be/x", "audio", str(tmp_path), 1000, max_duration=1800)
    assert e.value.code == "E-TOOLONG" and not ydl.downloaded


def test_download_cancel_maps_to_cancelled(ydl, tmp_path):
    with pytest.raises(DownloadError) as e:
        downloader.download("https://youtu.be/x", "video", str(tmp_path), 1000, is_cancelled=lambda: True)
    assert e.value.code == "E-CANCELLED"

    ydl.error = YtError("ERROR: interrupted")
    with pytest.raises(DownloadError) as e:
        downloader.download("https://youtu.be/x", "video", str(tmp_path), 1000, is_cancelled=lambda: True)
    assert e.value.code == "E-CANCELLED"


def test_probe_returns_preview(ydl):
    ydl.info = {"title": "Clip", "uploader": "Ann", "duration": 61, "thumbnail": "https://i.ytimg.com/a.jpg",
                "formats": [fmt(1080, 1920), fmt(None, None, "none")]}
    info = downloader.probe("https://youtu.be/x")
    assert (info.title, info.uploader, info.duration) == ("Clip", "Ann", 61)
    assert info.has_video and info.qualities == [360, 480, 720, 1080]
    assert info.thumbnail == "https://i.ytimg.com/a.jpg"
    assert ydl.last_opts["skip_download"] is True and not ydl.downloaded


def test_probe_drops_non_https_thumbnails_and_maps_errors(ydl):
    ydl.info = {"title": "Clip", "thumbnail": "http://insecure/a.jpg", "formats": [fmt(None, None, "none")]}
    info = downloader.probe("https://youtu.be/x")
    assert info.thumbnail is None and info.has_video is False and info.qualities == []

    ydl.error = YtError("ERROR: Sign in to confirm you're not a bot")
    with pytest.raises(DownloadError) as e:
        downloader.probe("https://youtu.be/x")
    assert e.value.code == "E-BLOCKED"


def test_probe_times_out(monkeypatch):
    monkeypatch.setattr(downloader, "_probe", lambda url, max_duration: time.sleep(1))
    with pytest.raises(DownloadError) as e:
        downloader.probe("https://youtu.be/x", timeout=0.05)
    assert e.value.code == "E-FAILED"
