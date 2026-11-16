"""Explicit structured domain exceptions with HTTP status and error codes."""

from typing import Any


class DomainError(Exception):
    """Structured domain failure raised by Aurora deterministic backend services."""

    def __init__(
        self,
        error_code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
