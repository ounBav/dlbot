"""Whole process: lifespan starts the poller + workers; updates arrive through getUpdates."""

import os
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main, store, worker
from app.config import Settings
from app.downloader import DownloadError, DownloadResult

DONE = "✅ Sent. Send another link anytime."


class FakeTg:
    """Telegram stand-in. Tests push updates; the poller pulls them with get_updates."""

    def __init__(self):
        self.sent, self.edits, self.files, self.offsets = [], [], [], []
        self.webhook_deleted = 0
        self.http = SimpleNamespace(close=lambda: None)
        self._updates: list[dict] = []
        self._lock = threading.Lock()
        self._next_id = 1

    def push(self, update: dict) -> None:
        with self._lock:
            self._updates.append({"update_id": self._next_id, **update})
            self._next_id += 1

    # polling
    def delete_webhook(self):
        self.webhook_deleted += 1

    def get_updates(self, offset, timeout, allowed_updates):
        self.offsets.append(offset)
        with self._lock:
            # Telegram semantics: offset confirms everything below it.
            if offset is not None:
                self._updates = [u for u in self._updates if u["update_id"] >= offset]
            batch = list(self._updates)
        if not batch:
            time.sleep(0.01)  # stand-in for the long-poll hold
        return batch

    # bot API used by handlers / worker
    def set_commands(self, *a, **k):
        pass

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return {"message_id": len(self.sent)}

    def edit_message(self, chat_id, message_id, text):
        self.edits.append(text)

    def answer_callback(self, cid):
        pass

    def send_cached(self, *a):
        pass

    def send_file(self, chat_id, fmt, path, caption):
        assert os.path.exists(path)  # the file exists while uploading
        self.files.append(path)
        return "FID"


@pytest.fixture
def bot(monkeypatch, tmp_path):
    store.reset()
    settings = Settings(_env_file=None, bot_token="1:x", temp_dir=str(tmp_path), worker_concurrency=1,
                        max_job_attempts=2, admin_ids="", poll_timeout=1)
    for target in (main, worker):
        monkeypatch.setattr(target, "get_settings", lambda: settings)
    monkeypatch.setattr("app.handlers.get_settings", lambda: settings)
    tg = FakeTg()
    monkeypatch.setattr(main, "TelegramClient", lambda token: tg)
    with TestClient(main.app) as client:
        yield client, tg, tmp_path
    store.reset()


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def text(uid, body):
    return {"message": {"chat": {"id": uid, "type": "private"}, "from": {"id": uid}, "text": body}}


def tap(uid, data, message_id=9):
    return {"callback_query": {"id": "c", "from": {"id": uid}, "data": data,
                               "message": {"chat": {"id": uid}, "message_id": message_id}}}


def keyboard_token(tg, button=0):
    return tg.sent[-1][2]["inline_keyboard"][0][button]["callback_data"].split("|")[2]


def test_no_webhook_endpoint_and_health_reports_polling(bot):
    client, tg, _ = bot
    assert client.post("/telegram/webhook", json={}).status_code in (404, 405)
    assert wait_for(lambda: client.get("/health").json()["polling"] is True)
    assert tg.webhook_deleted == 1  # getUpdates needs the webhook removed


def test_updates_are_handled_once_and_offset_advances(bot):
    client, tg, _ = bot
    tg.push(text(1, "/help"))
    tg.push(text(1, "/terms"))
    assert wait_for(lambda: len(tg.sent) == 2)
    assert wait_for(lambda: 3 in tg.offsets)  # confirmed update_ids 1 and 2
    time.sleep(0.1)
    assert len(tg.sent) == 2                  # nothing redelivered


def test_link_to_file_end_to_end_and_temp_is_removed(bot, monkeypatch):
    _, tg, tmp_path = bot
    seen_dirs = []

    def fake_download(url, fmt, out_dir, max_bytes):
        seen_dirs.append(out_dir)
        path = os.path.join(out_dir, "media.mp4")
        with open(path, "wb") as fh:
            fh.write(b"video")
        return DownloadResult(path=path, title="Clip", duration=3, size=5)

    monkeypatch.setattr(worker, "download", fake_download)
    tg.push(text(7, "https://youtu.be/abc"))
    assert wait_for(lambda: tg.sent and tg.sent[-1][2])
    tg.push(tap(7, f"dl|v|{keyboard_token(tg)}"))

    assert wait_for(lambda: tg.edits and tg.edits[-1] == DONE)
    assert len(tg.files) == 1
    assert seen_dirs and str(tmp_path) in seen_dirs[0]
    assert wait_for(lambda: not os.path.exists(seen_dirs[0]))  # temp folder deleted after send
    assert os.listdir(tmp_path) == []
    row = store.recent_requests(7)[0]
    assert row["status"] == "done" and row["title"] == "Clip"


def test_failed_job_retries_then_fails_and_cleans_up(bot, monkeypatch):
    _, tg, tmp_path = bot
    calls = []

    def flaky(url, fmt, out_dir, max_bytes):
        calls.append(out_dir)
        open(os.path.join(out_dir, "media.part"), "wb").close()
        raise DownloadError("E-FAILED", "network")

    monkeypatch.setattr(worker, "download", flaky)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    tg.push(text(8, "https://youtu.be/xyz"))
    assert wait_for(lambda: tg.sent and tg.sent[-1][2])
    tg.push(tap(8, f"dl|a|{keyboard_token(tg, button=1)}"))

    assert wait_for(lambda: store.recent_requests(8) and store.recent_requests(8)[0]["status"] == "failed")
    assert len(calls) == 2  # max_job_attempts=2
    assert store.recent_requests(8)[0]["error_code"] == "E-FAILED"
    assert wait_for(lambda: os.listdir(tmp_path) == [])  # removed in `finally`


def test_startup_removes_leftover_job_folders(monkeypatch, tmp_path):
    (tmp_path / "job-old").mkdir()
    (tmp_path / "job-old" / "media.mp4").write_bytes(b"x")
    (tmp_path / "keep.txt").write_text("x")
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(temp_dir=str(tmp_path)))
    worker.clean_temp_dir()
    assert sorted(os.listdir(tmp_path)) == ["keep.txt"]
