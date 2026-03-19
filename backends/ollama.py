import os
import requests
from .base import BaseLLMBackend


class OllamaBackend(BaseLLMBackend):
    def __init__(self):
        self._url   = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/chat"
        self._model = os.environ.get("LLM_MODEL",
"MichelRosselli/apertus:latest")

    def query(self, system_prompt: str, messages: list[dict]) -> str | None:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        payload = {"model": self._model, "messages": full_messages, "stream": False}
        try:
            r = requests.post(self._url, json=payload, timeout=120)
            r.raise_for_status()
            return r.json()["message"]["content"].strip() or None
        except Exception as e:
            print(f"[Ollama error] {e}")
            return None
