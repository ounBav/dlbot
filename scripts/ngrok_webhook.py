"""Local dev: open an ngrok tunnel to the bot's web port, write the public URL
into .env as PUBLIC_BASE_URL, and register the Telegram webhook.

Usage (from the dlbot/ folder):
    python scripts/ngrok_webhook.py               # tunnel :8000, update .env, setWebhook
    python scripts/ngrok_webhook.py --port 8000 --no-webhook
    python scripts/ngrok_webhook.py --stop        # stop ngrok and deleteWebhook

Reuses a running ngrok agent if one is already up (inspector API on :4040).
Free ngrok URLs change on every agent restart, so re-run this after a restart.
Stdlib only on purpose: it must work even when app/ cannot be imported.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
NGROK_API = "http://127.0.0.1:4040/api/tunnels"
WEBHOOK_PATH = "/telegram/webhook"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def set_env_var(path: Path, key: str, value: str) -> None:
    """Replace `key=...` in place (keeping order and comments) or append it."""
    lines = path.read_text(encoding="utf-8").splitlines()
    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_https_tunnel(tunnels: list[dict], port: int) -> str | None:
    for t in tunnels:
        addr = str(t.get("config", {}).get("addr", ""))
        if t.get("public_url", "").startswith("https://") and addr.endswith(f":{port}"):
            return t["public_url"]
    return None


def _get_tunnels() -> list[dict] | None:
    try:
        with urllib.request.urlopen(NGROK_API, timeout=2) as r:
            return json.load(r).get("tunnels", [])
    except (urllib.error.URLError, OSError):
        return None


def ensure_tunnel(port: int, timeout: float = 20) -> str:
    tunnels = _get_tunnels()
    if tunnels is not None:
        url = find_https_tunnel(tunnels, port)
        if url:
            print(f"Reusing ngrok tunnel {url}")
            return url
        sys.exit(f"An ngrok agent is running but has no tunnel to :{port}. Stop it first (--stop).")

    exe = shutil.which("ngrok")
    if not exe:
        sys.exit("ngrok not found on PATH.")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [exe, "http", str(port), "--log", "stdout"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = find_https_tunnel(_get_tunnels() or [], port)
        if url:
            print(f"Started ngrok tunnel {url} -> localhost:{port}")
            return url
        time.sleep(0.5)
    sys.exit("ngrok did not report a tunnel in time. Run `ngrok http 8000` by hand to see the error.")


def telegram(token: str, method: str, **params) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.load(r)
    except urllib.error.HTTPError as exc:
        body = json.load(exc)
    if not body.get("ok"):
        # Never echo the token: only the API's own description.
        sys.exit(f"Telegram {method} failed: {body.get('description')}")
    return body["result"]


def stop(token: str | None) -> None:
    if token:
        telegram(token, "deleteWebhook")
        print("deleteWebhook: ok")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/IM", "ngrok.exe", "/F"], capture_output=True)
    else:
        subprocess.run(["pkill", "-x", "ngrok"], capture_output=True)
    print("ngrok stopped")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-webhook", action="store_true", help="only update .env")
    ap.add_argument("--stop", action="store_true", help="stop ngrok and delete the webhook")
    args = ap.parse_args()

    env = read_env(ENV_PATH)
    token = env.get("BOT_TOKEN")
    if args.stop:
        stop(token)
        return

    url = ensure_tunnel(args.port)
    set_env_var(ENV_PATH, "PUBLIC_BASE_URL", url)
    print(f"Wrote PUBLIC_BASE_URL={url} to {ENV_PATH.name}")

    if args.no_webhook:
        return
    if not token:
        sys.exit("BOT_TOKEN missing in .env")
    params = {
        "url": url + WEBHOOK_PATH,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    }
    if env.get("WEBHOOK_SECRET"):
        params["secret_token"] = env["WEBHOOK_SECRET"]
    telegram(token, "setWebhook", **params)
    info = telegram(token, "getWebhookInfo")
    print(f"setWebhook: ok -> {info.get('url')}")
    print(f"pending_update_count={info.get('pending_update_count')} "
          f"last_error={info.get('last_error_message', 'none')}")


if __name__ == "__main__":
    main()
