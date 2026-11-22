"""TikTok profile tab: username parsing, flat listing mapping, one-time scan state, batch send."""

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from yt_dlp.utils import DownloadError as YtdlpDownloadError

from app import api, jobs, messages, profiles, store, worker
from app.config import Settings
from app.downloader import DownloadError
from app.tg import TelegramError

from .test_webapp_auth import TOKEN, make_init_data

USER_ID = 42


# --------------------------------------------------------------------------- username parsing

@pytest.mark.parametrize("raw,expected", [
    ("@abc", "abc"),
    ("abc", "abc"),
    ("  @khaby.lame ", "khaby.lame"),
    ("user_01", "user_01"),
    ("https://www.tiktok.com/@abc?lang=en", "abc"),
    ("tiktok.com/@abc/video/123", "abc"),
    ("https://m.tiktok.com/@abc", "abc"),
])
def test_normalize_username_accepts_handles_and_profile_links(raw, expected):
    assert profiles.normalize_username(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "@", "a b", "abc/def", "@ab$c", "x" * 33,
    "https://evil.example/@abc",            # SSRF: only TikTok hosts may name a profile
    "https://www.tiktok.com/video/123",     # not a profile path
    "https://www.youtube.com/@abc",
])
def test_normalize_username_rejects_everything_else(raw):
    assert profiles.normalize_username(raw) is None


# --------------------------------------------------------------------------- listing

class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL. `result` is the info dict, or an exception to raise."""
    result = None
    seen: list = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        FakeYDL.seen.append((url, self.opts))
        r = FakeYDL.result
        if isinstance(r, list):
            r = r.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def ydl(monkeypatch):
    FakeYDL.seen = []
    monkeypatch.setattr(profiles.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(profiles, "RETRY_DELAY", 0)
    return FakeYDL


def entry(i, **over):
    e = {"id": str(7000000000000000000 + i), "url": "https://ignored.example/x", "title": f"clip {i}",
         "duration": 15 + i, "timestamp": 1_700_000_000 + i, "view_count": 100 * i, "channel": "Abc Nick",
         "thumbnails": [{"id": "dynamicCover", "url": "https://p16.example/dyn.webp"},
                        {"id": "cover", "url": f"https://p16.example/cover{i}.jpg"}]}
    e.update(over)
    return e


def test_scan_builds_the_profile_url_and_maps_entries(ydl):
    ydl.result = {"_type": "playlist", "entries": [entry(1), entry(2)]}
    result = profiles.scan("abc", 10)

    url, opts = ydl.seen[0]
    assert url == "https://www.tiktok.com/@abc"
    assert opts["extract_flat"] == "in_playlist" and opts["playlistend"] == 10
    assert result.nickname == "Abc Nick"
    first = result.videos[0]
    # The video URL is rebuilt from the id, never taken from the extractor (NFR-06).
    assert first.url == "https://www.tiktok.com/@abc/video/7000000000000000001"
    assert first.thumbnail == "https://p16.example/cover1.jpg"
    assert (first.title, first.duration, first.view_count) == ("clip 1", 16, 100)


def test_scan_drops_bad_ids_duplicates_and_non_https_thumbnails(ydl):
    ydl.result = {"entries": [entry(1), entry(1), {"id": "../../etc"}, None,
                              entry(3, thumbnails=[{"id": "cover", "url": "http://insecure/x.jpg"}],
                                    thumbnail=None)]}
    videos = profiles.scan("abc", 10).videos
    assert [v.id[-1] for v in videos] == ["1", "3"]
    assert videos[1].thumbnail is None


def test_scan_respects_the_limit_even_if_the_extractor_returns_more(ydl):
    ydl.result = {"entries": [entry(i) for i in range(30)]}
    assert len(profiles.scan("abc", 10).videos) == 10


def test_scan_with_no_videos_is_e_novideos(ydl):
    ydl.result = {"entries": []}
    with pytest.raises(DownloadError) as exc:
        profiles.scan("abc", 10)
    assert exc.value.code == "E-NOVIDEOS"


@pytest.mark.parametrize("message,code", [
    ("This user's account is private. Log into an account that has access", "E-PRIVATE"),
    ("This account does not have any videos posted", "E-NOVIDEOS"),
    ("Unable to extract secondary user ID. If you are able to get the channel_id", "E-PROFILE"),
    ("HTTP Error 403: Forbidden", "E-FAILED"),
])
def test_classify_profile_error(message, code):
    assert profiles.classify_profile_error(f"ERROR: [tiktok:user] abc: {message}") == code


def test_scan_retries_once_when_tiktok_refuses(ydl):
    ydl.result = [YtdlpDownloadError("Unable to extract secondary user ID"), {"entries": [entry(1)]}]
    assert len(profiles.scan("abc", 10, attempts=2).videos) == 1
    assert len(ydl.seen) == 2


def test_scan_does_not_retry_a_private_profile(ydl):
    ydl.result = [YtdlpDownloadError("account is private"), {"entries": [entry(1)]}]
    with pytest.raises(DownloadError) as exc:
        profiles.scan("abc", 10, attempts=3)
    assert exc.value.code == "E-PRIVATE" and len(ydl.seen) == 1


# --------------------------------------------------------------------------- store

def test_scan_state_is_one_per_user_and_expires():
    store.reset()
    store.put_scan(1, {"id": "a", "videos": []}, ttl=60)
    store.put_scan(1, {"id": "b", "videos": []}, ttl=60)
    assert store.get_scan(1)["id"] == "b"
    assert store.drop_scan(1, "a") is False      # an old scan id cannot drop the new listing
    assert store.drop_scan(1, "b") is True
    assert store.get_scan(1) is None

    store.put_scan(2, {"id": "c", "videos": []}, ttl=-1)
    assert store.get_scan(2) is None


def test_reserve_daily_is_all_or_nothing_and_can_be_released():
    store.reset()
    assert store.reserve_daily(7, 8, per_day=10) == (True, 2)
    assert store.reserve_daily(7, 3, per_day=10) == (False, 2)
    assert store.usage_today(7) == 8
    store.release_daily(7, 8)
    assert store.usage_today(7) == 0
    assert store.reserve_daily(7, 10, per_day=10) == (True, 0)


# --------------------------------------------------------------------------- API

class FakeTg:
    def __init__(self):
        self.sent, self.cached, self.fail_send = [], [], False

    def send_message(self, chat_id, text, reply_markup=None):
        if self.fail_send:
            raise TelegramError("Forbidden: bot can't initiate conversation with a user")
        self.sent.append((chat_id, text))
        return {"message_id": 900 + len(self.sent)}

    def edit_message(self, chat_id, message_id, text):
        raise AssertionError("batch jobs have no status message to edit")

    def send_cached(self, chat_id, fmt, file_id, caption):
        self.cached.append((chat_id, fmt, file_id))


@pytest.fixture
def client(monkeypatch):
    store.reset()
    settings = Settings(_env_file=None, bot_token=TOKEN, rate_per_day=30, profile_max_send=5,
                        profile_max_videos=40, profile_scan_rate_per_minute=3)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    calls = []

    def fake_scan(username, limit, attempts=2):
        calls.append((username, limit))
        if username == "private_one":
            raise DownloadError("E-PRIVATE")
        n = min(limit, 12)
        return profiles.ProfileScan(username=username, nickname="Nick", videos=[
            profiles._video(entry(i), username) for i in range(n)])

    monkeypatch.setattr(profiles, "scan", fake_scan)
    tg = FakeTg()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_tg] = lambda: tg
    c = TestClient(app)
    c.headers["Authorization"] = (
        f"tma {make_init_data(auth_date=int(time.time()), user={'id': USER_ID, 'first_name': 'Ann'})}")
    return c, tg, calls


def scan(c, username="@abc", limit=10):
    return c.post("/api/tiktok/scan", json={"username": username, "limit": limit})


def test_scan_endpoint_returns_the_listing_and_keeps_it_once(client):
    c, _, calls = client
    r = scan(c)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["username"] == "abc" and data["count"] == 10 and data["max_send"] == 5
    assert data["truncated"] is False
    assert set(data["videos"][0]) == {"id", "url", "title", "duration", "thumbnail", "timestamp", "view_count"}
    assert store.get_scan(USER_ID)["id"] == data["scan_id"]
    assert calls == [("abc", 10)]


def test_scan_all_is_capped_by_profile_max_videos(client):
    c, _, calls = client
    data = scan(c, limit="all").json()
    assert calls == [("abc", 40)]
    assert data["count"] == 12 and data["truncated"] is False


def test_scan_rejects_bad_input(client):
    c, _, calls = client
    assert scan(c, username="https://evil.example/@abc").json()["detail"]["code"] == "E-USERNAME"
    assert scan(c, limit=25).status_code == 422
    assert calls == []


def test_scan_errors_are_passed_through(client):
    c, _, _ = client
    r = scan(c, username="private_one")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "E-PRIVATE"


def test_scan_is_rate_limited(client):
    c, _, _ = client
    for _ in range(3):
        assert scan(c).status_code == 200
    r = scan(c)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "E-RATELIMIT"


def test_new_scan_replaces_the_previous_one(client):
    c, _, _ = client
    first = scan(c).json()["scan_id"]
    second = scan(c, username="other").json()["scan_id"]
    assert store.get_scan(USER_ID)["id"] == second
    r = c.post(f"/api/tiktok/scan/{first}/send", json={"video_ids": ["7000000000000000001"]})
    assert r.status_code == 410 and r.json()["detail"]["code"] == "E-SCANGONE"


def test_send_queues_chat_jobs_posts_one_message_and_forgets_the_scan(client):
    c, tg, _ = client
    data = scan(c).json()
    ids = [v["id"] for v in data["videos"][:3]]
    r = c.post(f"/api/tiktok/scan/{data['scan_id']}/send", json={"video_ids": ids + [ids[0], "999999"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] == 3 and [i["video_id"] for i in body["items"]] == ids
    assert tg.sent == [(USER_ID, messages.from_profile(3, "abc"))]
    assert store.get_scan(USER_ID) is None           # one-time listing
    assert store.usage_today(USER_ID) == 3

    queued = [store.dequeue(0) for _ in range(3)]
    assert [j["url"] for j in queued] == [data["videos"][i]["url"] for i in range(3)]
    assert all(j["message_id"] is None and j["chat_id"] == USER_ID and j["fmt"] == "video"
               and "delivery" not in j for j in queued)
    assert queued[0]["title"] == "clip 0"
    # The jobs are visible to the Mini App like any other download.
    job = c.get(f"/api/downloads/{body['items'][0]['id']}").json()
    assert job["status"] == "queued" and job["delivery"] == "chat" and job["title"] == "clip 0"

    again = c.post(f"/api/tiktok/scan/{data['scan_id']}/send", json={"video_ids": ids})
    assert again.status_code == 410


def test_send_uses_the_file_id_cache(client, monkeypatch):
    c, tg, _ = client
    data = scan(c).json()
    monkeypatch.setattr(store, "cache_get", lambda h, fmt: "FILEID")
    body = c.post(f"/api/tiktok/scan/{data['scan_id']}/send",
                  json={"video_ids": [data["videos"][0]["id"]]}).json()
    assert body["items"][0] == {"video_id": data["videos"][0]["id"], "id": None, "status": "done"}
    assert tg.cached == [(USER_ID, "video", "FILEID")]


def test_send_limits(client):
    c, _, _ = client
    data = scan(c).json()
    url = f"/api/tiktok/scan/{data['scan_id']}/send"
    too_many = c.post(url, json={"video_ids": [v["id"] for v in data["videos"][:6]]})
    assert too_many.status_code == 400 and too_many.json()["detail"] == {"code": "E-TOOMANY", "max": 5}
    assert c.post(url, json={"video_ids": ["123"]}).json()["detail"]["code"] == "E-NOTFOUND"
    assert c.post(url, json={"video_ids": []}).status_code == 422
    assert store.get_scan(USER_ID) is not None        # a refused send keeps the listing


def test_send_respects_the_daily_quota(client):
    c, _, _ = client
    data = scan(c).json()
    store.reserve_daily(USER_ID, 28, per_day=30)
    url = f"/api/tiktok/scan/{data['scan_id']}/send"
    r = c.post(url, json={"video_ids": [v["id"] for v in data["videos"][:3]]})
    assert r.status_code == 429 and r.json()["detail"] == {"code": "E-QUOTA", "remaining": 2}
    store.reserve_daily(USER_ID, 2, per_day=30)
    r = c.post(url, json={"video_ids": [data["videos"][0]["id"]]})
    assert r.json()["detail"]["code"] == "E-RATELIMIT" and r.json()["detail"]["retry_after"] > 0


def test_send_without_a_started_chat_refunds_the_quota(client):
    c, tg, _ = client
    data = scan(c).json()
    tg.fail_send = True
    r = c.post(f"/api/tiktok/scan/{data['scan_id']}/send", json={"video_ids": [data["videos"][0]["id"]]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "E-NOCHAT"
    assert store.usage_today(USER_ID) == 0
    assert store.get_scan(USER_ID) is not None        # the user can tap Start and send again


def test_discard_forgets_the_listing(client):
    c, _, _ = client
    scan(c)
    assert c.post("/api/tiktok/scan/discard").json() == {"discarded": True}
    assert store.get_scan(USER_ID) is None
    assert c.post("/api/tiktok/scan/discard").json() == {"discarded": False}


def test_profile_endpoints_need_init_data(client):
    c, _, _ = client
    assert c.post("/api/tiktok/scan", json={"username": "abc"}, headers={"Authorization": ""}).status_code == 401
    assert c.post("/api/tiktok/scan/discard", headers={"Authorization": ""}).status_code == 401


# --------------------------------------------------------------------------- jobs / worker

def test_submit_without_status_message_does_not_edit(monkeypatch):
    store.reset()
    tg = FakeTg()
    db_id = jobs.submit(tg, token="t", user_id=1, chat_id=1, message_id=None,
                        url="https://www.tiktok.com/@a/video/7000000000000000001", platform="tiktok",
                        fmt="video", source="profile", title="My clip")
    assert db_id and store.get_progress(db_id)["title"] == "My clip"


def test_failed_batch_job_tells_the_chat_which_video(monkeypatch):
    store.reset()
    tg = FakeTg()
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(_env_file=None, bot_token=TOKEN))
    job = {"db_id": "12345678-aaaa", "chat_id": 5, "message_id": None, "user_id": 5, "title": "My clip"}
    worker._fail(job, "E-PRIVATE", tg, extra={})
    assert len(tg.sent) == 1 and tg.sent[0][0] == 5
    assert "My clip" in tg.sent[0][1] and "Ref: 12345678" in tg.sent[0][1]


def test_failed_device_job_sends_nothing(monkeypatch):
    store.reset()
    tg = FakeTg()
    monkeypatch.setattr(worker, "get_settings", lambda: Settings(_env_file=None, bot_token=TOKEN))
    worker._fail({"db_id": "x" * 12, "chat_id": None, "message_id": None, "user_id": 5,
                  "delivery": "device"}, "E-FAILED", tg, extra={})
    assert tg.sent == []
