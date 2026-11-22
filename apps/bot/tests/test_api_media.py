"""Mini App preview, quality, "save to device" delivery, one-job-per-user and file links."""

import os
import secrets
import time

from app import api, store
from app.downloader import DownloadError, MediaInfo

from .test_api import USER_ID, YT, env  # noqa: F401  (fixture)
from .test_webapp_auth import make_init_data

INFO = MediaInfo(title="Song", uploader="Ann", duration=212, thumbnail="https://i.ytimg.com/x.jpg",
                 has_video=True, qualities=[360, 480, 720, 1080])


def _fake_probe(monkeypatch, result=INFO, calls=None):
    def fake(url, max_duration=0):
        if calls is not None:
            calls.append(url)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(api, "probe", fake)


def test_info_returns_preview(env, monkeypatch):
    client, _, _ = env
    calls = []
    _fake_probe(monkeypatch, calls=calls)
    resp = client.post("/api/info", json={"url": f"check this {YT}!"})
    assert resp.status_code == 200
    assert resp.json() == {"url": YT, "platform": "youtube", "title": "Song", "uploader": "Ann",
                           "duration": 212, "thumbnail": "https://i.ytimg.com/x.jpg",
                           "has_video": True, "qualities": [360, 480, 720, 1080]}
    assert calls == [YT]


def test_info_rejects_unsupported_hosts_without_calling_yt_dlp(env, monkeypatch):
    client, _, _ = env
    calls = []
    _fake_probe(monkeypatch, calls=calls)
    for junk in ("https://evil.example/watch?v=1", "https://youtube.com.evil.example/x", "no link"):
        resp = client.post("/api/info", json={"url": junk})
        assert resp.status_code == 400 and resp.json()["detail"]["code"] == "E-UNSUPPORTED"
    assert calls == []


def test_info_maps_extractor_errors_to_codes(env, monkeypatch):
    client, _, _ = env
    for code in ("E-PRIVATE", "E-LIVE", "E-PLAYLIST", "E-TOOLONG", "E-BLOCKED"):
        _fake_probe(monkeypatch, DownloadError(code, "raw yt-dlp text"))
        resp = client.post("/api/info", json={"url": YT})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == code
        assert "raw yt-dlp text" not in resp.text   # never leak extractor output


def test_info_requires_init_data(env):
    client, _, _ = env
    assert client.post("/api/info", json={"url": YT}, headers={"Authorization": ""}).status_code == 401


def test_info_is_rate_limited_per_user(env, monkeypatch):
    client, _, _ = env
    _fake_probe(monkeypatch)
    for _ in range(20):
        assert client.post("/api/info", json={"url": YT}).status_code == 200
    resp = client.post("/api/info", json={"url": YT})
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "E-RATELIMIT" and resp.json()["detail"]["retry_after"] >= 1


def test_device_download_needs_no_chat_and_carries_quality(env):
    client, state, tg = env
    resp = client.post("/api/downloads", json={"url": YT, "format": "video", "quality": 480,
                                               "delivery": "device"})
    assert resp.status_code == 200
    job = state["queue"][0]
    assert job["delivery"] == "device" and job["quality"] == 480
    assert job["chat_id"] is None and job["message_id"] is None
    assert tg.sent == [] and tg.edits == []      # nothing posted in the chat


def test_device_download_ignores_the_chat_file_id_cache(env):
    client, state, tg = env
    state["cache"]["video"] = "FILE_ID"
    body = client.post("/api/downloads", json={"url": YT, "format": "video", "delivery": "device"}).json()
    assert body["cached"] is False and len(state["queue"]) == 1 and tg.cached == []


def test_audio_drops_quality(env):
    client, state, _ = env
    client.post("/api/downloads", json={"url": YT, "format": "audio", "quality": 1080, "delivery": "device"})
    assert "quality" not in state["queue"][0]


def test_unknown_quality_or_delivery_is_422(env):
    client, _, _ = env
    assert client.post("/api/downloads", json={"url": YT, "format": "video", "quality": 999}).status_code == 422
    assert client.post("/api/downloads", json={"url": YT, "format": "video", "delivery": "email"}).status_code == 422


def test_one_active_job_per_user(env):
    client, state, _ = env
    first = client.post("/api/downloads", json={"url": YT, "format": "video", "delivery": "device"}).json()["id"]
    resp = client.post("/api/downloads", json={"url": YT, "format": "audio", "delivery": "device"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == {"code": "E-BUSY", "id": first}
    assert len(state["queue"]) == 1

    state["rows"][first]["status"] = "done"     # finished: the next one is accepted
    assert client.post("/api/downloads", json={"url": YT, "format": "audio", "delivery": "device"}).status_code == 200


def _finish_device_job(state, job_id, tmp_path, name="Song.mp4", ttl=900):
    path = tmp_path / "media.mp4"
    path.write_bytes(b"0123456789")
    token = secrets.token_urlsafe(32)
    store.put_file(token, {"path": str(path), "dir": str(tmp_path), "name": name, "size": 10,
                           "user_id": USER_ID, "db_id": job_id}, ttl)
    state["progress"][job_id].update(file_token=token, stage="done")
    state["rows"][job_id].update(status="done", title="Song")
    return token


def test_job_view_reports_progress_then_the_file_link(env, tmp_path):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video", "quality": 720,
                                                 "delivery": "device"}).json()["id"]
    state["progress"][job_id].update(stage="downloading", progress="0.420")
    body = client.get(f"/api/downloads/{job_id}").json()
    assert body["status"] == "downloading" and body["progress"] == 0.42
    assert body["delivery"] == "device" and body["quality"] == 720 and body["file"] is None

    token = _finish_device_job(state, job_id, tmp_path)
    body = client.get(f"/api/downloads/{job_id}").json()
    assert body["status"] == "done" and body["progress"] == 1.0
    assert body["file"]["name"] == "Song.mp4" and body["file"]["size"] == 10
    assert body["file"]["download_path"] == f"/api/files/{token}"
    assert body["file"]["expires_at"]


def test_file_link_streams_as_attachment_without_init_data(env, tmp_path):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video", "delivery": "device"}).json()["id"]
    token = _finish_device_job(state, job_id, tmp_path, name="Ünïcode clip.mp4")

    resp = client.get(f"/api/files/{token}", headers={"Authorization": ""})
    assert resp.status_code == 200 and resp.content == b"0123456789"
    assert resp.headers["content-type"] == "video/mp4"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment") and "filename*=utf-8''" in disposition.lower()
    assert "no-store" in resp.headers["cache-control"]

    head = client.head(f"/api/files/{token}")
    assert head.status_code == 200 and head.headers["content-length"] == "10"


def test_expired_unknown_or_malformed_file_links_are_410(env, tmp_path):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video", "delivery": "device"}).json()["id"]
    token = _finish_device_job(state, job_id, tmp_path, ttl=-1)   # already expired
    for t in (token, secrets.token_urlsafe(32), "..%2F..%2Fetc%2Fpasswd", "short"):
        resp = client.get(f"/api/files/{t}")
        assert resp.status_code in (404, 410), t
        if resp.status_code == 410:
            assert resp.json()["detail"]["code"] == "E-GONE"
    assert client.get(f"/api/files/{token}").status_code == 410
    assert client.get(f"/api/downloads/{job_id}").json()["file"] is None


def test_file_whose_media_was_deleted_is_410(env, tmp_path):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video", "delivery": "device"}).json()["id"]
    token = _finish_device_job(state, job_id, tmp_path)
    os.remove(tmp_path / "media.mp4")
    assert client.get(f"/api/files/{token}").status_code == 410


def test_other_users_cannot_see_a_device_job(env, tmp_path):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video", "delivery": "device"}).json()["id"]
    _finish_device_job(state, job_id, tmp_path)
    other = make_init_data(auth_date=int(time.time()), user={"id": 999, "first_name": "Eve"})
    resp = client.get(f"/api/downloads/{job_id}", headers={"Authorization": f"tma {other}"})
    assert resp.status_code == 404


def test_me_lists_qualities_and_file_ttl(env):
    client, _, _ = env
    body = client.get("/api/me").json()
    assert body["qualities"] == [360, 480, 720, 1080] and body["file_ttl_min"] == 15
