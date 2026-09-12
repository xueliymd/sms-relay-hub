from fastapi import Request
from fastapi.responses import JSONResponse


class RelayError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code, self.code, self.message = status_code, code, message


async def relay_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RelayError):
        raise exc
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})
