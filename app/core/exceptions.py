class AppError(Exception):
    """Base for domain errors raised by services.

    Services raise these instead of HTTPException so they stay callable
    outside a request context (scripts, future background workers). The
    exception handlers registered in app.main translate them to HTTP.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        # `code` lets a call site narrow the machine-readable reason (e.g.
        # ForbiddenError(code="email_not_verified") vs the default "forbidden")
        # without a new subclass for every specific case, while the HTTP
        # status stays governed by the class actually raised.
        if code is not None:
            self.code = code
        self.message = message or self.code
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class RateLimitExceededError(AppError):
    status_code = 429
    code = "rate_limited"
