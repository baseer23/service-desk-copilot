from typing import Protocol


class LocalModelProvider(Protocol):
    """Protocol implemented by all local provider adapters."""

    def name(self) -> str:
        """Return the provider identifier (e.g. stub, ollama)."""

    def generate(self, prompt: str) -> str:
        """Generate a response for the supplied prompt."""
