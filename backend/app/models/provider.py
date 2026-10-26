from typing import Any, Protocol, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class CaseReasoningProvider(Protocol):
    def name(self) -> str: ...
    async def generate_structured(self, stage: str, context: dict, schema: type[T]) -> T: ...


class LLMError(RuntimeError):
    """Sanitized generation failure with machine-readable recovery semantics."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "unavailable",
        recoverable: bool = False,
        request_id: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.recoverable = recoverable
        self.request_id = request_id
        self.diagnostics = diagnostics or {}
