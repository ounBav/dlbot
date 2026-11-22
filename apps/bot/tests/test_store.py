import threading

import pytest

from app import store


@pytest.fixture(autouse=True)
def fresh_store():
    store.reset()
    yield
    store.reset()


def test_singleton_is_shared():
    assert store.get_store() is store.get_store()
    store.upsert_user(1, "ann")
    store.set_banned(1, True)
    assert store.is_banned(1) and not store.is_banned(2)


def test_rate_limit_per_minute(monkeypatch):
    monkeypatch.setattr(store.time, "time", lambda: 1_700_000_010)
    results = [store.rate_limit_hit(7, per_minute=3, per_day=100) for _ in range(4)]
    assert [ok for ok, _ in results] == [True, True, True, False]
    assert results[-1][1] == 60 - (1_700_000_010 % 60)


def test_rate_limit_per_day_and_usage(monkeypatch):
    clock = [1_700_000_000]
    monkeypatch.setattr(store.time, "time", lambda: clock[0])
    for _ in range(3):
        assert store.rate_limit_hit(7, per_minute=100, per_day=3)[0]
        clock[0] += 61  # new minute window, same day
    assert store.rate_limit_hit(7, per_minute=100, per_day=3)[0] is False
    assert store.usage_today(7) == 4
    assert store.usage_today(8) == 0


def test_claim_is_one_shot_per_format():
    assert store.claim("tok", "video") is True
    assert store.claim("tok", "video") is False
    assert store.claim("tok", "audio") is True


def test_claim_is_one_shot_under_concurrency():
    wins = []
    barrier = threading.Barrier(16)

    def tap():
        barrier.wait()
        wins.append(store.claim("race", "video"))

    threads = [threading.Thread(target=tap) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wins.count(True) == 1


def test_dedupe_update():
    assert store.dedupe_update(100) is True
    assert store.dedupe_update(100) is False
    assert store.dedupe_update(101) is True


def test_cancel_flag_and_active_job():
    assert store.active_job(1) is None
    store.set_active(1, "tok")
    assert store.active_job(1) == "tok"
    assert not store.is_cancelled("tok")
    store.mark_cancel("tok")
    assert store.is_cancelled("tok")


def test_keys_expire(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(store.time, "time", lambda: clock[0])
    store.save_request("tok", {"url": "u", "platform": "youtube", "user_id": 1})
    store.set_progress("job", stage="queued")
    assert store.load_request("tok")["url"] == "u"
    clock[0] += store.REQUEST_TTL + 1
    assert store.load_request("tok") is None
    assert store.get_progress("job") is None


def test_queue_is_fifo():
    store.enqueue({"n": 1})
    store.enqueue({"n": 2})
    assert store.dequeue(timeout=0.1)["n"] == 1
    assert store.dequeue(timeout=0.1)["n"] == 2
    assert store.dequeue(timeout=0.01) is None


def test_progress_merges_fields_as_strings():
    store.set_progress("j", stage="queued", token="t")
    store.set_progress("j", stage="uploading", title=None)
    assert store.get_progress("j") == {"stage": "uploading", "token": "t", "title": ""}


def test_request_log_ownership_order_and_stats():
    a = store.create_request(1, "https://youtu.be/a", "youtube", "video")
    b = store.create_request(1, "https://youtu.be/b", "youtube", "audio", source="miniapp")
    store.create_request(2, "https://youtu.be/c", "youtube", "video")
    store.finish_request(a, "done", file_size=10, title="A")
    store.finish_request(b, "failed", error_code="E-PRIVATE")

    assert store.get_request(a, 2) is None
    assert store.get_request(a, 1)["title"] == "A"
    assert [r["id"] for r in store.recent_requests(1)] == [b, a]
    assert store.stats() == {"total": 3, "done": 1, "failed": 1, "users": 2}


def test_request_log_is_bounded(monkeypatch):
    monkeypatch.setattr(store, "MAX_REQUEST_LOG", 3)
    ids = [store.create_request(1, f"https://youtu.be/{i}", "youtube", "video") for i in range(5)]
    assert [r["id"] for r in store.recent_requests(1, limit=10)] == ids[:1:-1]


def test_file_id_cache():
    assert store.cache_get("h", "video") is None
    store.cache_set("h", "video", "FID")
    assert store.cache_get("h", "video") == "FID"
    assert store.cache_get("h", "audio") is None
