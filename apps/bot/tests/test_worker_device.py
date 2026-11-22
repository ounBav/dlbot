"""Worker: Mini App "save to device" delivery, cancel from the progress hook, link expiry."""

import os
from types import SimpleNamespace

import pytest

from app import store, worker
from app.downloader import DownloadError, DownloadResult


@pytest.fixture
def setup(monkeypatch, tmp_path):
    store.reset()
    settings = SimpleNamespace(temp_dir=str(tmp_path), max_upload_mb=50, max_job_attempts=1,
                               max_duration_sec=0, file_ttl_min=15)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    calls = []
    tg = SimpleNamespace(edit_message=lambda *a: calls.append(("edit", a)),
                         send_file=lambda *a: calls.append(("send", a)) or "FID")
    db_id = store.create_request(1, "https://youtu.be/x", "youtube", "video", source="miniapp")
    job = {"token": "tok", "db_id": db_id, "fmt": "video", "url": "https://youtu.be/x",
           "platform": "youtube", "user_id": 1, "chat_id": None, "message_id": None,
           "attempt": 1, "quality": 480, "delivery": "device"}
    yield SimpleNamespace(tg=tg, calls=calls, job=job, db_id=db_id, tmp=tmp_path)
    store.reset()


def _writes_file(seen):
    def fake(url, fmt, out_dir, max_bytes, **kw):
        seen.update(kw, out_dir=out_dir)
        kw["on_progress"]("downloading", 0.5)
        kw["on_progress"]("processing", None)
        path = os.path.join(out_dir, "media.mp4")
        with open(path, "wb") as fh:
            fh.write(b"video")
        return DownloadResult(path=path, title='My: "clip"/1', duration=3, size=5)
    return fake


def test_device_job_keeps_the_file_behind_a_link_and_never_touches_the_chat(setup, monkeypatch):
    seen = {}
    monkeypatch.setattr(worker, "download", _writes_file(seen))
    worker.process(dict(setup.job), setup.tg)

    assert setup.calls == []                                 # no chat edits, no upload
    assert seen["quality"] == 480
    progress = store.get_progress(setup.db_id)
    assert progress["stage"] == "done" and progress["file_name"] == "My clip 1.mp4"
    meta = store.get_file(progress["file_token"])
    assert meta["size"] == 5 and os.path.isfile(meta["path"])   # kept after the job
    assert store.recent_requests(1)[0]["status"] == "done"


def test_expired_links_are_swept_with_their_folder(setup, monkeypatch):
    seen = {}
    monkeypatch.setattr(worker, "download", _writes_file(seen))
    setup.job  # noqa: B018
    monkeypatch.setattr(worker.get_settings(), "file_ttl_min", -1)   # expire immediately
    worker.process(dict(setup.job), setup.tg)
    assert os.path.isdir(seen["out_dir"])
    assert worker.sweep_files() == 1
    assert not os.path.exists(seen["out_dir"])
    assert os.listdir(setup.tmp) == []


def test_progress_hook_updates_live_fraction(setup, monkeypatch):
    fractions = []

    def fake(url, fmt, out_dir, max_bytes, **kw):
        kw["on_progress"]("downloading", 0.25)
        fractions.append(store.get_progress(setup.db_id)["progress"])
        raise DownloadError("E-PRIVATE")
    monkeypatch.setattr(worker, "download", fake)
    worker.process(dict(setup.job), setup.tg)
    assert fractions == ["0.250"]
    assert store.get_progress(setup.db_id)["stage"] == "failed"


def test_cancel_during_download_finishes_as_cancelled_and_cleans_up(setup, monkeypatch):
    dirs = []

    def fake(url, fmt, out_dir, max_bytes, **kw):
        dirs.append(out_dir)
        store.mark_cancel("tok")
        assert kw["is_cancelled"]() is True
        raise DownloadError("E-CANCELLED", "cancelled by user")
    monkeypatch.setattr(worker, "download", fake)
    worker.process(dict(setup.job), setup.tg)
    assert store.recent_requests(1)[0]["status"] == "cancelled"
    assert store.get_progress(setup.db_id)["stage"] == "cancelled"
    assert not os.path.exists(dirs[0])


def test_display_name_is_safe_for_every_os():
    assert worker.display_name('a/b\\c:d*e?f"g<h>i|j', "/x/media.mp4") == "a b c d e f g h i j.mp4"
    assert worker.display_name("   ", "/x/media.mp3") == "video.mp3"
    assert worker.display_name("Ünïcode ✓", "/x/media.MP4") == "Ünïcode ✓.mp4"
    assert len(worker.display_name("x" * 500, "/x/m.mp4")) == 84


def test_chat_job_with_quality_uses_its_own_cache_key(monkeypatch, setup):
    cached = []
    monkeypatch.setattr(store, "cache_set", lambda h, key, fid: cached.append(key))
    monkeypatch.setattr(worker, "download", _writes_file({}))
    job = {**setup.job, "delivery": "chat", "chat_id": 1, "message_id": 2}
    worker.process(job, setup.tg)
    assert cached == ["video:480"]
    assert [c[0] for c in setup.calls].count("send") == 1
