"""Unified error envelope.

All error responses share the shape:

    { "error": {
        "code": "VALIDATION_FAILED" | "NOT_FOUND" | "CONFLICT" | "INVALID_REQUEST",
        "message": "<human summary>",
        "fields":    [ ... ],  // optional — per-field Pydantic errors
        "errors":    [ ... ],  // optional — semantic validation errors (list[str])
        "conflicts": [ ... ],  // optional — structured conflict objects
    }}

Route handlers keep raising `HTTPException` with their existing `detail` shape;
the handlers below translate that to the envelope so clients have one parser.
"""

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

_STATUS_CODE_MAP: dict[int, str] = {
    400: "INVALID_REQUEST",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_FAILED",
}


def _envelope(
    status_code: int,
    code: str,
    message: str = "",
    **extra: Any,
) -> JSONResponse:
    payload: dict[str, Any] = {"code": code, "message": message}
    payload.update({k: v for k, v in extra.items() if v})
    return JSONResponse(status_code=status_code, content={"error": payload})


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODE_MAP.get(exc.status_code, "ERROR")
    detail = exc.detail

    if isinstance(detail, str):
        return _envelope(exc.status_code, code, message=detail)

    if isinstance(detail, list):
        # List of dicts → structured conflicts; list of strings → semantic errors
        if detail and isinstance(detail[0], dict):
            return _envelope(exc.status_code, code, conflicts=detail)
        return _envelope(exc.status_code, code, errors=list(detail))

    # Unknown detail type: fall back to string rep.
    return _envelope(exc.status_code, code, message=str(detail))


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _envelope(
        422,
        "VALIDATION_FAILED",
        message="Input validation failed.",
        fields=[{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
