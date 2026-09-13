from __future__ import annotations

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


class FMSException(HTTPException):
    def __init__(self, status_code: int, detail: str, error_code: str | None = None):
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code


class NotFoundError(FMSException):
    def __init__(self, resource: str, identifier: str | int):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource} not found: {identifier}",
            error_code="NOT_FOUND",
        )


class ConflictError(FMSException):
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            error_code="CONFLICT",
        )


class ValidationError(FMSException):
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
            error_code="VALIDATION_ERROR",
        )


class UnauthorizedError(FMSException):
    def __init__(self, detail: str = "Invalid credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            error_code="UNAUTHORIZED",
        )


class ForbiddenError(FMSException):
    def __init__(self, detail: str = "Insufficient permissions"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            error_code="FORBIDDEN",
        )


class OptimisticLockError(FMSException):
    def __init__(self, detail: str = "Resource was modified concurrently, please retry"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            error_code="OPTIMISTIC_LOCK",
        )


async def fms_exception_handler(request: Request, exc: FMSException) -> JSONResponse:
    content = {"detail": exc.detail}
    if exc.error_code:
        content["error_code"] = exc.error_code
    return JSONResponse(status_code=exc.status_code, content=content)