"""Extract bounded Azure error identifiers without logging external bodies."""

import re
from typing import Any


def azure_error_code(error: Any) -> str:
    stored = getattr(error, "azure_error_code", "")
    if isinstance(stored, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", stored):
        return stored
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        detail = body.get("error", body)
        code = detail.get("code") if isinstance(detail, dict) else None
    else:
        code = getattr(error, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", code):
        return code
    cause = getattr(error, "__cause__", None)
    if cause and cause is not error:
        return azure_error_code(cause)
    status = getattr(error, "status_code", getattr(error, "status", None))
    return (
        f"HTTP_{status}" if isinstance(status, int) and 400 <= status <= 599 else "REQUEST_FAILED"
    )


class AzureOperationError(RuntimeError):
    def __init__(self, message: str, code: str) -> None:
        self.azure_error_code = code
        super().__init__(f"{message} [{code}]")
