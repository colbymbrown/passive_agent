import os
import requests
from .base import BaseChannel


class TelegramChannel(BaseChannel):

    poll_interval = 5  # seconds

    def __init__(self) -> None:
        self._token   = os.environ["TELEGRAM_TOKEN"]
        self._chat_id = os.environ["TELEGRAM_CHAT_ID"]
        self._offset  = 0

    def send(self, text: str) -> None:
        url     = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text}
        try:
            r = requests.post(url, json=payload, timeout=10)
            r.raise_for_status()
        except Exception as e:
            print(f"[Telegram send error] {e}")

    def get_updates(self) -> list[str]:
        url    = f"https://api.telegram.org/bot{self._token}/getUpdates"
        params = {"offset": self._offset, "timeout": 5}
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[Telegram poll error] {e}")
            return []

        messages = []
        for update in data.get("result", []):
            self._offset = max(self._offset, update["update_id"] + 1)
            msg  = update.get("message", {})
            if str(msg.get("chat", {}).get("id", "")) == str(self._chat_id):
                text = msg.get("text", "").strip()
                if text:
                    messages.append(text)

        return messages

    def on_startup(self) -> None:
        self.get_updates()  # drain stale messages
        self.send("Agent started. I will message you if I notice something worth your attention.")
