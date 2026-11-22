import logging

from app.log import setup_logging


def test_http_client_loggers_do_not_log_request_urls():
    # Telegram request URLs embed the bot token (NFR-05), so httpx INFO lines must be suppressed.
    setup_logging()
    for name in ("httpx", "httpcore"):
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
