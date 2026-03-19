from .base import BaseLLMBackend
from .ollama import OllamaBackend
from .claude import ClaudeBackend
from .publicai import PublicAIBackend


def _make_backend(name: str) -> BaseLLMBackend:
    match name.lower():
        case "ollama":   return OllamaBackend()
        case "claude":   return ClaudeBackend()
        case "publicai": return PublicAIBackend()
        case _:          raise ValueError(f"Unknown LLM backend: {name!r}")


class FallbackBackend(BaseLLMBackend):
    def __init__(self, backends: list[BaseLLMBackend]):
        self._backends = backends

    def query(self, system_prompt: str, messages: list[dict]) -> str | None:
        for backend in self._backends:
            result = backend.query(system_prompt, messages)
            if result is not None:
                return result
        return None


def get_backend(names: str) -> BaseLLMBackend:
    name_list = [n.strip() for n in names.split(",") if n.strip()]
    backends = []
    for name in name_list:
        try:
            backends.append(_make_backend(name))
        except (ValueError, KeyError) as e:
            print(f"[Backend] Skipping {name!r}: {e}")
    if not backends:
        raise RuntimeError("No LLM backends could be initialized. Check your .env configuration.")
    if len(backends) == 1:
        return backends[0]
    return FallbackBackend(backends)


__all__ = ["BaseLLMBackend", "FallbackBackend", "get_backend"]
