import httpx
import pytest

from app import poller as poller_mod
from app import store
from app.poller import Poller
from app.tg import TelegramClient, TelegramError


@pytest.fixture(autouse=True)
def fresh_store():
    store.reset()
    yield
    store.reset()


class ScriptedTg:
    """get_updates returns (or raises) the scripted results in order."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.deleted = 0

    def get_updates(self, offset, timeout, allowed_updates):
        self.calls.append((offset, timeout, allowed_updates))
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def delete_webhook(self):
        self.deleted += 1


@pytest.fixture
def handled(monkeypatch):
    seen = []
    monkeypatch.setattr(poller_mod, "handle_update", lambda u, tg: seen.append(u["update_id"]))
    return seen


@pytest.fixture
def waits(monkeypatch):
    """Record backoff waits instead of sleeping."""
    recorded = []
    monkeypatch.setattr(Poller, "_sleep_after_error", _recording(Poller._sleep_after_error, recorded))
    return recorded


def _recording(original, recorded):
    def wrapper(self, what, exc):
        self._stop.wait = lambda d: recorded.append(d)
        original(self, what, exc)
    return wrapper


def test_offset_advances_and_params(handled):
    tg = ScriptedTg([{"update_id": 10}, {"update_id": 11}], [])
    p = Poller(tg, poll_timeout=25)
    assert p.poll_once() == 2
    assert handled == [10, 11] and p.offset == 12
    p.poll_once()
    assert tg.calls[0] == (None, 25, ["message", "callback_query"])
    assert tg.calls[1][0] == 12


def test_handler_crash_does_not_stop_or_replay(monkeypatch):
    seen = []

    def boom(update, tg):
        seen.append(update["update_id"])
        if update["update_id"] == 1:
            raise RuntimeError("bad update")

    monkeypatch.setattr(poller_mod, "handle_update", boom)
    p = Poller(ScriptedTg([{"update_id": 1}, {"update_id": 2}]))
    p.poll_once()
    assert seen == [1, 2] and p.offset == 3


def test_duplicate_update_ids_are_skipped(handled):
    p = Poller(ScriptedTg([{"update_id": 5}], [{"update_id": 5}]))
    p.poll_once()
    p.poll_once()
    assert handled == [5]


def test_network_errors_back_off_exponentially_then_reset(handled, waits):
    err = httpx.ConnectError("down")
    p = Poller(ScriptedTg(err, err, err, [], err))
    for _ in range(5):
        p.poll_once()
    assert waits == [1.0, 2.0, 4.0, 1.0]  # reset after the successful empty poll


def test_conflict_clears_webhook_and_waits(handled, waits):
    tg = ScriptedTg(TelegramError("Conflict: terminated by other getUpdates request", code=409))
    Poller(tg).poll_once()
    assert tg.deleted == 1 and waits == [5.0]


def test_flood_control_honours_retry_after(handled, waits):
    Poller(ScriptedTg(TelegramError("Too Many Requests", code=429, retry_after=7))).poll_once()
    assert waits == [7.0]


def test_bad_token_waits_long(handled, waits):
    Poller(ScriptedTg(TelegramError("Unauthorized", code=401))).poll_once()
    assert waits == [poller_mod.MAX_BACKOFF]


def test_run_deletes_webhook_then_stops(handled):
    tg = ScriptedTg()
    p = Poller(tg)

    def stop_after_first(offset, timeout, allowed):
        p._stop.set()
        return []

    tg.get_updates = stop_after_first
    p.run()
    assert tg.deleted == 1


def test_client_parses_error_code_and_retry_after():
    def handler(request):
        return httpx.Response(429, json={"ok": False, "error_code": 429, "description": "Too Many Requests",
                                         "parameters": {"retry_after": 3}})

    tg = TelegramClient("1:x")
    tg.http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(TelegramError) as info:
        tg.get_updates(None, 25, ["message"])
    assert info.value.code == 429 and info.value.retry_after == 3


def test_client_get_updates_sends_offset_and_long_timeout():
    captured = {}

    def handler(request):
        import json
        captured["body"] = json.loads(request.content)
        captured["timeout"] = request.extensions["timeout"]["read"]
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 1}]})

    tg = TelegramClient("1:x")
    tg.http = httpx.Client(transport=httpx.MockTransport(handler))
    assert tg.get_updates(42, 25, ["message"]) == [{"update_id": 1}]
    assert captured["body"] == {"timeout": 25, "allowed_updates": ["message"], "offset": 42}
    assert captured["timeout"] > 25  # HTTP read timeout outlasts the long poll
