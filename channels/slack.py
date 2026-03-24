import os
import time
import requests
from .base import BaseChannel


class SlackChannel(BaseChannel):

    poll_interval = 10  # seconds

    def __init__(self) -> None:
        self._token      = os.environ["SLACK_BOT_TOKEN"]
        self._user_id    = os.environ.get("SLACK_USER_ID")   # set for DM mode
        self._channel_id = os.environ.get("SLACK_CHANNEL_ID", "")  # set for channel mode
        self._oldest     = str(time.time())  # only fetch messages after startup
        self._fail_streak = 0

    def send(self, text: str) -> None:
        url     = "https://slack.com/api/chat.postMessage"
        headers = {"Authorization": f"Bearer {self._token}"}
        payload = {"channel": self._channel_id, "text": text}
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                print(f"[Slack send error] {data.get('error')}")
        except Exception as e:
            print(f"[Slack send error] {e}")

    def get_updates(self) -> list[str]:
        url     = "https://slack.com/api/conversations.history"
        headers = {"Authorization": f"Bearer {self._token}"}
        params  = {"channel": self._channel_id, "oldest": self._oldest, "limit": 20}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                print(f"[Slack poll error] {data.get('error')}")
                return []
            self._fail_streak = 0
        except Exception as e:
            self._fail_streak += 1
            print(f"[Slack poll error] streak={self._fail_streak} — {e}")
            return []

        messages = []
        for msg in reversed(data.get("messages", [])):
            ts   = msg.get("ts", "0")
            text = msg.get("text", "").strip()
            # skip bot messages (subtype present means it's a bot/system message)
            if msg.get("subtype") or msg.get("bot_id"):
                if float(ts) > float(self._oldest):
                    self._oldest = ts
                continue
            if text and float(ts) > float(self._oldest):
                self._oldest = ts
                messages.append(text)

        return messages

    def on_startup(self) -> None:
        # DM mode: resolve the user ID to a DM channel ID via conversations.open
        if self._user_id:
            url     = "https://slack.com/api/conversations.open"
            headers = {"Authorization": f"Bearer {self._token}"}
            try:
                r = requests.post(url, json={"users": self._user_id}, headers=headers, timeout=10)
                r.raise_for_status()
                data = r.json()
                if data.get("ok"):
                    self._channel_id = data["channel"]["id"]
                else:
                    print(f"[Slack] Could not open DM with {self._user_id}: {data.get('error')}")
            except Exception as e:
                print(f"[Slack] Could not open DM: {e}")
        self.get_updates()  # drain stale messages; sets self._oldest to now
        self.send("Agent started.")
