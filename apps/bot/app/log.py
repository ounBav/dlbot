import json
import logging
import sys
import time

EXTRA_FIELDS = ("req_id", "user_id", "platform", "code", "duration_ms")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class SkipHealthAccess(logging.Filter):
    """Drop uvicorn access lines for /health: an uptime pinger hits it every few minutes."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn.access args: (client_addr, method, full_path, http_version, status_code)
        if isinstance(args, tuple) and len(args) >= 3:
            return str(args[2]).split("?", 1)[0] != "/health"
        return True


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # NFR-05: httpx/httpcore log full request URLs at INFO, and Telegram URLs contain the bot token.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, SkipHealthAccess) for f in access.filters):
        access.addFilter(SkipHealthAccess())
