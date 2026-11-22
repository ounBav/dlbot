import logging

from app.log import setup_logging


def test_http_client_loggers_do_not_log_request_urls():
    # Telegram request URLs embed the bot token (NFR-05), so httpx INFO lines must be suppressed.
    setup_logging()
    for name in ("httpx", "httpcore"):
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)


def _access(path):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                             ("1.2.3.4:5", "GET", path, "1.1", 200), None)


def test_health_access_lines_are_dropped_others_kept():
    setup_logging()
    access = logging.getLogger("uvicorn.access")
    assert not access.filter(_access("/health"))
    assert not access.filter(_access("/health?x=1"))
    assert access.filter(_access("/api/me"))
    setup_logging()                                  # idempotent: one filter only
    from app.log import SkipHealthAccess
    assert sum(isinstance(f, SkipHealthAccess) for f in access.filters) == 1
