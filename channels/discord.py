import os
import time
import requests
from .base import BaseChannel

_DISCORD_API = "https://discord.com/api/v10"
_DISCORD_EPOCH_MS = 1420070400000  # 2015-01-01T00:00:00 UTC


def _snowflake_now() -> int:
    """Return a Discord snowflake representing the current time."""
    return (int(time.time() * 1000) - _DISCORD_EPOCH_MS) << 22


class DiscordChannel(BaseChannel):

    poll_interval = 10  # seconds

    def __init__(self) -> None:
        self._token      = os.environ["DISCORD_BOT_TOKEN"]
        self._channel_id = os.environ["DISCORD_CHANNEL_ID"]
        self._after      = _snowflake_now()  # only fetch messages after startup
        self._fail_streak = 0

    def send(self, text: str) -> None:
        url     = f"{_DISCORD_API}/channels/{self._channel_id}/messages"
        headers = {"Authorization": f"Bot {self._token}"}
        payload = {"content": text}
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=10)
            r.raise_for_status()
        except Exception as e:
            print(f"[Discord send error] {e}")

    def get_updates(self) -> list[str]:
        url     = f"{_DISCORD_API}/channels/{self._channel_id}/messages"
        headers = {"Authorization": f"Bot {self._token}"}
        params  = {"after": self._after, "limit": 50}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            self._fail_streak = 0
        except Exception as e:
            self._fail_streak += 1
            print(f"[Discord poll error] streak={self._fail_streak} — {e}")
            return []

        messages = []
        # Discord returns newest-first; reverse so we process oldest first
        for msg in reversed(data):
            snowflake = int(msg["id"])
            if snowflake <= self._after:
                continue
            self._after = snowflake
            # skip bot messages
            if msg.get("author", {}).get("bot"):
                continue
            text = msg.get("content", "").strip()
            if text:
                messages.append(text)

        return messages

    def on_startup(self) -> None:
        self.get_updates()  # drain stale messages; advances self._after to now
        self.send("Agent started.")
