"""Application error types and standard error formatting."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base application exception carrying machine-readable error codes."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | list | str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details if details is not None else {}

    def to_dict(self) -> dict[str, Any]:
        """Format error payload according to frozen API contract."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Global FastAPI exception handler for AppError instances."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )
