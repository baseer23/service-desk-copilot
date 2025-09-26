from .provider import LocalModelProvider
from ..core.config import DEFAULT_STUB_ANSWER


class StubProvider(LocalModelProvider):
    """Deterministic provider used for tests and offline fallbacks."""

    def name(self) -> str:
        """Return the provider identifier."""
        return "stub"

    def generate(self, prompt: str) -> str:
        """Ignore the prompt and return the default stub answer."""
        return DEFAULT_STUB_ANSWER
