from types import SimpleNamespace

import pytest

from app import store, worker
from app.downloader import DownloadError, DownloadResult


@pytest.fixture
def run(monkeypatch, tmp_path):
    stages: list[dict] = []
    finished: list[tuple] = []
    settings = SimpleNamespace(temp_dir=str(tmp_path), max_upload_mb=50, max_job_attempts=1,
                               max_duration_sec=0, file_ttl_min=15)
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(store, "set_progress", lambda rid, **f: stages.append(f))
    monkeypatch.setattr(store, "is_cancelled", lambda token: False)
    monkeypatch.setattr(store, "cache_set", lambda *a: None)
    monkeypatch.setattr(store, "finish_request", lambda rid, status, **kw: finished.append((status, kw)))

    tg = SimpleNamespace(edit_message=lambda *a: None, send_file=lambda *a: "FID")
    job = {"token": "t", "db_id": "00000000-0000-0000-0000-000000000001", "fmt": "video",
           "url": "https://youtu.be/x", "platform": "youtube", "user_id": 1, "chat_id": 1,
           "message_id": 2, "attempt": 1}

    def go(download):
        monkeypatch.setattr(worker, "download", download)
        worker.process(dict(job), tg)
        return [s["stage"] for s in stages], stages, finished
    return go


def test_progress_stages_for_a_successful_job(run, tmp_path):
    path = tmp_path / "f.mp4"
    path.write_bytes(b"x")
    result = DownloadResult(path=str(path), title="Clip", duration=3, size=1)
    names, stages, finished = run(lambda *a, **k: result)
    assert names == ["downloading", "uploading", "done"]
    assert stages[1]["title"] == "Clip"
    assert finished == [("done", {"file_size": 1, "title": "Clip", "duration_ms": finished[0][1]["duration_ms"]})]


def test_progress_records_the_failure_code(run):
    def boom(*a, **k):
        raise DownloadError("E-PRIVATE", "login required")
    names, stages, _ = run(boom)
    assert names == ["downloading", "failed"]
    assert stages[-1]["error_code"] == "E-PRIVATE"


def test_progress_store_outage_does_not_break_the_job(run, monkeypatch, tmp_path):
    def down(*a, **k):
        raise ConnectionError("redis down")
    monkeypatch.setattr(store, "set_progress", down)
    path = tmp_path / "f.mp4"
    path.write_bytes(b"x")
    _, _, finished = run(lambda *a, **k: DownloadResult(path=str(path), title="Clip", duration=3, size=1))
    assert finished[0][0] == "done"
