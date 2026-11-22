import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api, db, store
from app.config import Settings
from app.tg import TelegramError

from .test_webapp_auth import TOKEN, make_init_data

USER_ID = 42


class FakeTg:
    def __init__(self, fail_send: bool = False):
        self.fail_send = fail_send
        self.sent, self.edits, self.cached = [], [], []

    def send_message(self, chat_id, text, reply_markup=None):
        if self.fail_send:
            raise TelegramError("Forbidden: bot can't initiate conversation with a user")
        self.sent.append((chat_id, text))
        return {"message_id": 777}

    def edit_message(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))

    def send_cached(self, chat_id, fmt, file_id, caption):
        self.cached.append((chat_id, fmt, file_id))


@pytest.fixture
def env(monkeypatch):
    settings = Settings(_env_file=None, bot_token=TOKEN, db_host="db", db_schema="bot", db_username="bot",
                        db_password="bot", redis_url="redis://unused", rate_per_minute=5, rate_per_day=30,
                        miniapp_url="", cors_origins="")
    monkeypatch.setattr(api, "get_settings", lambda: settings)

    state = {"rows": {}, "progress": {}, "queue": [], "cache": {}, "banned": set(),
             "rate_ok": True, "cancelled": set()}

    def create_request(uid, url, platform, fmt, source="chat"):
        rid = str(uuid.uuid4())
        state["rows"][rid] = {"id": rid, "uid": uid, "url": url, "platform": platform, "format": fmt,
                              "status": "received", "error_code": None, "title": None, "source": source,
                              "file_size": None, "created_at": datetime.now(timezone.utc)}
        return rid

    def get_request(rid, uid):
        row = state["rows"].get(rid)
        return row if row and row["uid"] == uid else None

    state["upserts"] = []
    monkeypatch.setattr(db, "upsert_user", lambda uid, username: state["upserts"].append(uid))
    monkeypatch.setattr(db, "is_banned", lambda uid: uid in state["banned"])
    monkeypatch.setattr(db, "create_request", create_request)
    monkeypatch.setattr(db, "get_request", get_request)
    monkeypatch.setattr(db, "recent_requests",
                        lambda uid, limit=20: [r for r in state["rows"].values() if r["uid"] == uid][:limit])
    monkeypatch.setattr(store, "rate_limit_hit", lambda uid, m, d: (True, 0) if state["rate_ok"] else (False, 42))
    monkeypatch.setattr(store, "usage_today", lambda uid: 3)
    monkeypatch.setattr(store, "cache_get", lambda h, fmt: state["cache"].get(fmt))
    monkeypatch.setattr(store, "set_progress",
                        lambda rid, **f: state["progress"].setdefault(rid, {}).update({k: str(v) for k, v in f.items()}))
    monkeypatch.setattr(store, "get_progress", lambda rid: state["progress"].get(rid))
    monkeypatch.setattr(store, "enqueue", state["queue"].append)
    monkeypatch.setattr(store, "set_active", lambda uid, token: None)
    monkeypatch.setattr(store, "mark_cancel", state["cancelled"].add)

    tg = FakeTg()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_tg] = lambda: tg
    client = TestClient(app)
    client.headers["Authorization"] = f"tma {make_init_data(auth_date=int(time.time()), user={'id': USER_ID, 'first_name': 'Ann'})}"
    return client, state, tg


YT = "https://youtu.be/dQw4w9WgXcQ"


def test_requests_without_valid_init_data_are_rejected(env):
    client, _, _ = env
    assert client.get("/api/me", headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/me", headers={"Authorization": "tma garbage"}).status_code == 401
    other_bot = make_init_data(token="1:other", auth_date=int(time.time()))
    resp = client.get("/api/me", headers={"Authorization": f"tma {other_bot}"})
    assert resp.status_code == 401 and resp.json()["detail"]["code"] == "E-AUTH"


def test_me_returns_usage(env):
    client, _, _ = env
    body = client.get("/api/me").json()
    assert body["user"]["id"] == USER_ID
    assert body["usage_today"] == 3 and body["limit_per_day"] == 30


def test_banned_user_gets_403(env):
    client, state, _ = env
    state["banned"].add(USER_ID)
    resp = client.get("/api/me")
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "E-BANNED"


def test_create_download_enqueues_job_for_private_chat(env):
    client, state, tg = env
    resp = client.post("/api/downloads", json={"url": f"look {YT}", "format": "audio"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued" and body["cached"] is False

    job = state["queue"][0]
    assert job["chat_id"] == USER_ID and job["message_id"] == 777
    assert job["url"] == YT and job["fmt"] == "audio" and job["db_id"] == body["id"]
    assert state["rows"][body["id"]]["source"] == "miniapp"
    assert tg.sent[0][0] == USER_ID


def test_unsupported_link_is_rejected_before_anything_runs(env):
    client, state, tg = env
    resp = client.post("/api/downloads", json={"url": "https://evil.example/x", "format": "video"})
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "E-UNSUPPORTED"
    assert not state["queue"] and not tg.sent


def test_invalid_format_is_422(env):
    client, _, _ = env
    assert client.post("/api/downloads", json={"url": YT, "format": "gif"}).status_code == 422


def test_rate_limited(env):
    client, state, _ = env
    state["rate_ok"] = False
    resp = client.post("/api/downloads", json={"url": YT, "format": "video"})
    assert resp.status_code == 429 and resp.json()["detail"] == {"code": "E-RATELIMIT", "retry_after": 42}


def test_user_who_never_started_the_bot_gets_409(env):
    client, state, tg = env
    tg.fail_send = True
    resp = client.post("/api/downloads", json={"url": YT, "format": "video"})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "E-NOCHAT"
    assert not state["queue"]


def test_cached_file_is_sent_without_a_job(env):
    client, state, tg = env
    state["cache"]["video"] = "FILE_ID"
    body = client.post("/api/downloads", json={"url": YT, "format": "video"}).json()
    assert body == {"id": None, "status": "done", "cached": True}
    assert tg.cached == [(USER_ID, "video", "FILE_ID")] and not state["queue"]


def test_job_status_follows_live_stage_then_db(env):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video"}).json()["id"]
    assert client.get(f"/api/downloads/{job_id}").json()["status"] == "queued"

    state["progress"][job_id].update(stage="uploading", title="Song")
    body = client.get(f"/api/downloads/{job_id}").json()
    assert body["status"] == "uploading" and body["title"] == "Song"

    state["rows"][job_id].update(status="failed", error_code="E-TOOBIG")
    body = client.get(f"/api/downloads/{job_id}").json()
    assert body["status"] == "failed" and body["error_code"] == "E-TOOBIG"

    assert [i["id"] for i in client.get("/api/downloads").json()["items"]] == [job_id]


def test_other_users_jobs_and_bad_ids_are_404(env):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video"}).json()["id"]
    state["rows"][job_id]["uid"] = 999
    assert client.get(f"/api/downloads/{job_id}").status_code == 404
    assert client.get("/api/downloads/not-a-uuid").status_code == 404


def test_cancel_marks_token_and_refuses_finished_jobs(env):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video"}).json()["id"]
    assert client.post(f"/api/downloads/{job_id}/cancel").json()["status"] == "cancelling"
    assert state["cancelled"] == {state["progress"][job_id]["token"]}

    state["rows"][job_id]["status"] = "done"
    resp = client.post(f"/api/downloads/{job_id}/cancel")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "E-FINISHED"


def test_me_describes_limits_and_platforms(env):
    client, _, _ = env
    body = client.get("/api/me").json()
    assert body["limit_per_minute"] == 5 and body["max_upload_mb"] == 50
    assert {p["key"] for p in body["platforms"]} == {"youtube", "tiktok", "facebook"}
    youtube = next(p for p in body["platforms"] if p["key"] == "youtube")
    assert "youtu.be" in youtube["hosts"] and youtube["label"] == "YouTube"


def test_polling_is_read_only_and_never_cached(env):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video"}).json()["id"]
    writes = len(state["upserts"])
    for _ in range(3):
        resp = client.get(f"/api/downloads/{job_id}")
        assert resp.headers["cache-control"] == "no-store"
    client.get("/api/downloads")
    assert len(state["upserts"]) == writes  # no DB write per poll


def test_lost_job_is_reported_expired_and_cannot_be_cancelled(env):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "video"}).json()["id"]
    state["progress"].pop(job_id)                       # Redis progress TTL ran out
    state["rows"][job_id]["created_at"] -= timedelta(hours=2)
    assert client.get(f"/api/downloads/{job_id}").json()["status"] == "expired"
    resp = client.post(f"/api/downloads/{job_id}/cancel")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "E-FINISHED"


def test_fresh_job_without_progress_yet_is_still_queued(env):
    client, state, _ = env
    job_id = client.post("/api/downloads", json={"url": YT, "format": "audio"}).json()["id"]
    state["progress"].pop(job_id)
    assert client.get(f"/api/downloads/{job_id}").json()["status"] == "queued"
