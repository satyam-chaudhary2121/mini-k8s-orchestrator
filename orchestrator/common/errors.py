from __future__ import annotations


class AppError(Exception):
    """Domain error rendered as the consistent API error envelope."""

    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"


class InvalidTransitionError(ConflictError):
    code = "INVALID_STATE_TRANSITION"


class UnavailableError(AppError):
    status_code = 503
    code = "UNAVAILABLE"
