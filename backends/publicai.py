import os
import requests
from .base import BaseLLMBackend

PUBLIC_AI_API_URL = "https://api.publicai.co/v1/chat/completions"


class PublicAIBackend(BaseLLMBackend):
    def __init__(self):
        self._api_key = os.environ["PUBLICAI_API_KEY"]
        self._model   = os.environ.get("LLM_MODEL", "swiss-ai/apertus-70b-instruct")

    def query(self, system_prompt: str, messages: list[dict]) -> str | None:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent":    "passive-agent/1.0",
            "Content-Type":  "application/json",
        }
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {
            "model":    self._model,
            "messages": full_messages,
        }
        try:
            r = requests.post(PUBLIC_AI_API_URL, headers=headers, json=payload, timeout=120)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip() or None
        except Exception as e:
            print(f"[PublicAI error] {e}")
            return None
