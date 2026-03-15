from abc import ABC, abstractmethod


class BaseChannel(ABC):

    poll_interval: int = 30  # seconds between get_updates() calls; override per subclass

    @abstractmethod
    def send(self, text: str) -> None:
        """Send a message to the user."""
        ...

    @abstractmethod
    def get_updates(self) -> list[str]:
        """
        Return new messages from the user since the last call.
        The channel tracks its own polling state internally.
        """
        ...

    def on_startup(self) -> None:
        """
        Called once before the main loop starts.
        Default: drain any pending updates so stale messages are not replayed.
        Subclasses may override to send a startup greeting or perform setup.
        """
        self.get_updates()
