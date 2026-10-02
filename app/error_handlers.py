"""
Exception handlers that turn every error into the same JSON shape:

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

`code` is stable and machine-readable (clients branch on it), `message` is for
humans and never carries internal details. In debug mode, 5xx responses also get a
`debug` block with the underlying exception, to speed up local troubleshooting.
"""
import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.exceptions import (
    AppError,
    DependencyUnavailableError,
    summarize_validation_errors,
)
from app.schemas import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)

_ERROR_DESCRIPTIONS = {
    400: 'Malformed Event Grid payload (`invalid_event_payload`). '
         'Event Grid does not retry it.',
    422: 'Invalid request body (`validation_error`).',
    500: 'Unexpected error (`internal_error`).',
    503: 'Redis or Service Bus is unavailable (transient). '
         'Includes a `Retry-After` header.',
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """
    OpenAPI docs for the errors an endpoint can return, for a route's `responses=`.
    Also replaces FastAPI's default 422 schema, which doesn't match ours.
    """
    return {
        code: {'model': ErrorResponse, 'description': _ERROR_DESCRIPTIONS[code]}
        for code in status_codes
    }


def build_error_response(
        request: Request,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
        exc: BaseException | None = None,
        headers: dict[str, str] | None = None,
) -> JSONResponse:
    debug = None
    if settings.debug and exc is not None and status_code >= 500:
        root = exc.__cause__ or exc
        debug = {'exception': type(root).__name__, 'message': str(root)}

    # Built through the same model the OpenAPI docs use, so they can't drift apart.
    body = ErrorResponse(error=ErrorBody(
        code=code,
        message=message,
        request_id=getattr(request.state, 'request_id', None),
        details=details or None,
        debug=debug,
    ))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode='json', exclude_none=True),
        headers=headers,
    )


async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    headers = None
    if isinstance(exc, DependencyUnavailableError):
        headers = {'Retry-After': str(exc.retry_after_seconds)}
        # An outage repeats on every request (health probes hit every few seconds):
        # the cause is what matters, a traceback per request would flood the logs.
        logger.error('%s: %s cause=%r', exc.code, exc.message, exc.__cause__)
    elif exc.status_code >= 500:
        logger.error('%s: %s', exc.code, exc.message, exc_info=exc)
    else:
        logger.warning('%s: %s details=%s', exc.code, exc.message, exc.details)

    return build_error_response(
        request, exc.status_code, exc.code, exc.message,
        details=exc.details, exc=exc, headers=headers,
    )


async def _handle_request_validation_error(
        request: Request, exc: RequestValidationError,
) -> JSONResponse:
    details = summarize_validation_errors(exc.errors())
    logger.warning(
        'validation_error on %s %s: %s', request.method, request.url.path, details,
    )
    return build_error_response(
        request, 422, 'validation_error', 'Request validation failed.', details,
    )


async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException,
) -> JSONResponse:
    # Covers FastAPI/Starlette's own errors too (404 unknown route, 405, ...).
    phrase = HTTPStatus(exc.status_code).phrase
    message = exc.detail if isinstance(exc.detail, str) else phrase
    return build_error_response(
        request, exc.status_code, phrase.lower().replace(' ', '_'), message,
        headers=exc.headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Unhandled exceptions are not registered here: see request_context.py."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
