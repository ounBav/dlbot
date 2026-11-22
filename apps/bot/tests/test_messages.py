from app import messages


def test_caption_escapes_html_and_formats_duration():
    text = messages.caption("<script>x</script>", "YouTube", 125)
    assert "&lt;script&gt;" in text
    assert "2:05" in text


def test_rate_limited_message_switches_to_daily_wording():
    assert "tomorrow" in messages.rate_limited(7200)
    assert "seconds" in messages.rate_limited(30)


def test_error_message_has_reference():
    assert "Ref: abc123" in messages.error("E-FAILED", "abc123", 50)


def test_bot_commands_are_valid_for_telegram_and_hide_admin():
    import re
    names = [c for c, _ in messages.BOT_COMMANDS]
    assert "admin" not in names
    assert len(names) == len(set(names))
    for cmd, desc in messages.BOT_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", cmd)   # Bot API BotCommand rules
        assert 1 <= len(desc) <= 256
        assert f"/{cmd}" in messages.HELP               # menu matches the /help guide


def test_set_commands_sends_menu_and_button(monkeypatch):
    from app.tg import TelegramClient
    calls = []
    tg = TelegramClient("x")
    monkeypatch.setattr(tg, "call", lambda method, **p: calls.append((method, p)))
    tg.set_commands([("start", "Welcome message")])
    assert calls[0] == ("setMyCommands", {
        "commands": [{"command": "start", "description": "Welcome message"}],
        "scope": {"type": "all_private_chats"},
    })
    assert calls[1] == ("setChatMenuButton", {"menu_button": {"type": "commands"}})


def test_set_commands_menu_button_opens_mini_app_when_configured(monkeypatch):
    from app.tg import TelegramClient
    calls = []
    tg = TelegramClient("x")
    monkeypatch.setattr(tg, "call", lambda method, **p: calls.append((method, p)))
    tg.set_commands([("start", "Welcome message")], web_app_url="https://app.example", web_app_label="Download")
    assert calls[1] == ("setChatMenuButton", {"menu_button": {
        "type": "web_app", "text": "Download", "web_app": {"url": "https://app.example"}}})


def test_start_shows_open_app_button_only_when_mini_app_is_configured(monkeypatch):
    from types import SimpleNamespace
    from app import handlers
    sent = []
    tg = SimpleNamespace(send_message=lambda chat_id, text, reply_markup=None: sent.append(reply_markup))

    for url in ("", "https://app.example"):
        monkeypatch.setattr(handlers, "get_settings", lambda: SimpleNamespace(miniapp_url=url))
        handlers._handle_command("/start", 1, 1, tg)

    assert sent[0] is None
    button = sent[1]["inline_keyboard"][0][0]
    assert button == {"text": messages.OPEN_APP, "web_app": {"url": "https://app.example"}}
