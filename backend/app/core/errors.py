from __future__ import annotations

from collections import defaultdict
import logging
from typing import Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

logger = logging.getLogger(__name__)


def api_error(
    status_code: int,
    message: str,
    *,
    field_errors: dict[str, list[str]] | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "message": message,
            "field_errors": field_errors or {},
        },
    )


def _normalize_detail(detail: Any) -> dict[str, Any]:
    if isinstance(detail, dict):
        return {
            "message": str(detail.get("message") or "Request failed."),
            "field_errors": detail.get("field_errors") or {},
        }
    if isinstance(detail, str):
        return {"message": detail, "field_errors": {}}
    return {"message": "Request failed.", "field_errors": {}}


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = _normalize_detail(exc.detail)
    logger.warning(
        "Handled API error on %s %s: %s",
        request.method,
        request.url.path,
        detail["message"],
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    field_errors: dict[str, list[str]] = defaultdict(list)
    for issue in exc.errors():
        location = [str(part) for part in issue.get("loc", []) if part != "body"]
        field_name = location[-1] if location else "request"
        field_errors[field_name].append(issue.get("msg", "Invalid value."))

    message = "Request validation failed. Review the highlighted fields and try again."
    logger.warning(
        "Validation failed on %s %s: %s",
        request.method,
        request.url.path,
        message,
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "message": message,
                "field_errors": dict(field_errors),
            }
        },
    )


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.exception(
        "Unhandled API exception on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "message": (
                    "The local API hit an unexpected error. Review the runtime logs and try again."
                ),
                "field_errors": {},
            }
        },
    )
