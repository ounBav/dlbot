from types import SimpleNamespace

import pytest

from app import handlers, messages, store

USER = 10
ADMIN = 99


class FakeTg:
    def __init__(self):
        self.sent, self.edits, self.cached, self.answered = [], [], [], []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return {"message_id": len(self.sent)}

    def edit_message(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))

    def send_cached(self, chat_id, fmt, file_id, caption):
        self.cached.append((chat_id, fmt, file_id))

    def answer_callback(self, cid):
        self.answered.append(cid)


@pytest.fixture
def tg(monkeypatch):
    store.reset()
    settings = SimpleNamespace(rate_per_minute=5, rate_per_day=30, miniapp_url="", terms_url="",
                               admin_id_set={ADMIN})
    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    yield FakeTg()
    store.reset()


def msg(text, user=USER):
    return {"message": {"chat": {"id": user, "type": "private"}, "from": {"id": user}, "text": text}}


def tap(data, user=USER, chat=USER, message_id=5):
    return {"callback_query": {"id": "cq", "from": {"id": user}, "data": data,
                               "message": {"chat": {"id": chat}, "message_id": message_id}}}


def last_text(tg):
    return tg.sent[-1][1]


def token_from_keyboard(tg):
    return tg.sent[-1][2]["inline_keyboard"][0][0]["callback_data"].split("|")[2]


def test_link_gets_format_keyboard(tg):
    handlers.handle_update(msg("look https://youtu.be/abc"), tg)
    buttons = tg.sent[-1][2]["inline_keyboard"][0]
    assert [b["callback_data"][:4] for b in buttons] == ["dl|v", "dl|a"]


def test_unsupported_host(tg):
    handlers.handle_update(msg("https://evil.example/x"), tg)
    assert last_text(tg) == messages.UNSUPPORTED


@pytest.mark.parametrize("cmd, expected", [
    ("/start", messages.WELCOME), ("/help", messages.HELP), ("/nope", messages.UNKNOWN_COMMAND)])
def test_commands(tg, cmd, expected):
    handlers.handle_update(msg(cmd), tg)
    assert last_text(tg) == expected


def test_non_admin_admin_is_unknown(tg):
    handlers.handle_update(msg("/admin stats"), tg)
    assert last_text(tg) == messages.UNKNOWN_COMMAND


def test_admin_ban_then_banned_user_is_refused(tg):
    handlers.handle_update(msg(f"/admin ban {USER}", user=ADMIN), tg)
    handlers.handle_update(msg("https://youtu.be/abc"), tg)
    assert last_text(tg) == messages.BANNED


def test_group_chats_are_ignored(tg):
    update = msg("https://youtu.be/abc")
    update["message"]["chat"]["type"] = "group"
    handlers.handle_update(update, tg)
    assert tg.sent == []


def test_tap_enqueues_once_and_other_users_are_ignored(tg):
    handlers.handle_update(msg("https://youtu.be/abc"), tg)
    token = token_from_keyboard(tg)
    handlers.handle_update(tap(f"dl|v|{token}", user=777), tg)   # someone else
    handlers.handle_update(tap(f"dl|v|{token}"), tg)
    handlers.handle_update(tap(f"dl|v|{token}"), tg)              # duplicate tap
    job = store.dequeue(timeout=0.1)
    assert job["fmt"] == "video" and job["url"] == "https://youtu.be/abc" and job["user_id"] == USER
    assert store.dequeue(timeout=0.01) is None
    assert tg.edits[-1][2] == messages.QUEUED


def test_cached_file_id_is_sent_without_job(tg, monkeypatch):
    handlers.handle_update(msg("https://youtu.be/abc"), tg)
    token = token_from_keyboard(tg)
    from app.url_utils import url_hash
    store.cache_set(url_hash("https://youtu.be/abc"), "audio", "FID")
    handlers.handle_update(tap(f"dl|a|{token}"), tg)
    assert tg.cached == [(USER, "audio", "FID")]
    assert store.dequeue(timeout=0.01) is None


def test_expired_token(tg):
    handlers.handle_update(tap("dl|v|missing"), tg)
    assert tg.edits[-1][2] == messages.EXPIRED


def test_cancel_marks_active_job(tg):
    handlers.handle_update(msg("/cancel"), tg)
    assert last_text(tg) == messages.NO_ACTIVE_JOB
    handlers.handle_update(msg("https://youtu.be/abc"), tg)
    token = token_from_keyboard(tg)
    handlers.handle_update(tap(f"dl|v|{token}"), tg)
    handlers.handle_update(msg("/cancel"), tg)
    assert last_text(tg) == messages.CANCEL_REQUESTED
    assert store.is_cancelled(token)


def test_rate_limit(tg):
    for _ in range(5):
        handlers.handle_update(msg("https://youtu.be/abc"), tg)
    handlers.handle_update(msg("https://youtu.be/abc"), tg)
    assert tg.sent[-1][2] is None  # no keyboard: rate limited reply
