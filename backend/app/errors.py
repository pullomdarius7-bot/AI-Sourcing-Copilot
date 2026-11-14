class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
        extra: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.extra = extra or {}  # additional fields of the error object, e.g. remaining credits


def unauthorized(message: str = "Authentication required", code: str = "unauthorized") -> ApiError:
    return ApiError(401, code, message, {"WWW-Authenticate": "Bearer"})


def forbidden(message: str = "You do not have permission to do this", code: str = "forbidden"):
    return ApiError(403, code, message)


def not_found(message: str = "Not found") -> ApiError:
    return ApiError(404, "not_found", message)


def bad_request(code: str, message: str) -> ApiError:
    return ApiError(400, code, message)


def conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)
