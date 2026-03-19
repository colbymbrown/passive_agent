from abc import ABC, abstractmethod


class BaseLLMBackend(ABC):
    @abstractmethod
    def query(self, system_prompt: str, messages: list[dict]) -> str | None:
        """Send system prompt + message list to the LLM; return text or None on failure."""
        ...
