from typing import Protocol, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class CaseReasoningProvider(Protocol):
    def name(self) -> str: ...
    async def generate_structured(self, stage: str, context: dict, schema: type[T]) -> T: ...


class LLMError(RuntimeError):
    """Generation or structured validation failed; never silently use a stub."""
