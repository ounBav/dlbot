"""Thin Telegram Bot API client. All calls go through `call`, which unwraps errors."""

import os

import httpx


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = httpx.Client(timeout=httpx.Timeout(120.0))

    def _unwrap(self, resp: httpx.Response):
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(f"HTTP {resp.status_code}")
        if not data.get("ok"):
            raise TelegramError(data.get("description", "unknown error"))
        return data["result"]

    def call(self, method: str, **params):
        return self._unwrap(self.http.post(f"{self.base}/{method}", json=params))

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None):
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup:
            params["reply_markup"] = reply_markup
        return self.call("sendMessage", **params)

    def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                      text=text, parse_mode="HTML", disable_web_page_preview=True)
        except TelegramError as exc:
            if "not modified" not in str(exc):
                raise

    def set_commands(self, commands: list[tuple[str, str]],
                     web_app_url: str = "", web_app_label: str = "") -> None:
        """Register the command menu for private chats. With web_app_url, the Menu button opens the Mini App."""
        self.call(
            "setMyCommands",
            commands=[{"command": c, "description": d} for c, d in commands],
            scope={"type": "all_private_chats"},
        )
        if web_app_url:
            button = {"type": "web_app", "text": web_app_label, "web_app": {"url": web_app_url}}
        else:
            button = {"type": "commands"}
        self.call("setChatMenuButton", menu_button=button)

    def answer_callback(self, callback_id: str) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id)

    def send_cached(self, chat_id: int, fmt: str, file_id: str, caption: str) -> None:
        method, field = ("sendAudio", "audio") if fmt == "audio" else ("sendVideo", "video")
        self.call(method, chat_id=chat_id, caption=caption, parse_mode="HTML", **{field: file_id})

    def send_file(self, chat_id: int, fmt: str, path: str, caption: str) -> str:
        """Upload a local file. Returns the Telegram file_id for later reuse."""
        if fmt == "audio":
            method, field, mime = "sendAudio", "audio", "audio/mpeg"
            data = {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"}
        else:
            method, field, mime = "sendVideo", "video", "video/mp4"
            data = {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML",
                    "supports_streaming": "true"}
        with open(path, "rb") as fh:
            resp = self.http.post(
                f"{self.base}/{method}",
                data=data,
                files={field: (os.path.basename(path), fh, mime)},
            )
        result = self._unwrap(resp)
        return result[field]["file_id"]
