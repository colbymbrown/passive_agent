import os
import requests
from .base import BaseLLMBackend

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


class ClaudeBackend(BaseLLMBackend):
    def __init__(self):
        self._api_key = os.environ["ANTHROPIC_API_KEY"]
        self._model   = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")

    def query(self, system_prompt: str, messages: list[dict]) -> str | None:
        headers = {
            "x-api-key":         self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        }
        payload = {
            "model":      self._model,
            "max_tokens": 1024,
            "system":     system_prompt,
            "messages":   messages,
        }
        try:
            r = requests.post(ANTHROPIC_API_URL, headers=headers, json=payload, timeout=120)
            r.raise_for_status()
            return r.json()["content"][0]["text"].strip() or None
        except Exception as e:
            print(f"[Claude error] {e}")
            return None
