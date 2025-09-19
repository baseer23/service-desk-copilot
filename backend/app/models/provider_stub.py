from .provider import LocalModelProvider
from ..core.config import DEFAULT_STUB_ANSWER


class StubProvider(LocalModelProvider):
    def generate(self, prompt: str) -> str:
        return DEFAULT_STUB_ANSWER

