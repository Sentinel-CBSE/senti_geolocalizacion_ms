"""
Per-request context. Gives every request an id (the caller's X-Request-ID when it's
sane, a fresh one otherwise), exposes it to every log line through a logging filter,
echoes it back in the response, writes the access log, and acts as the last error
boundary for exceptions that no handler in error_handlers.py recognized.
"""
import logging
import re
import time
import uuid
from contextvars import ContextVar

from fastapi import Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from app.error_handlers import build_error_response

REQUEST_ID_HEADER = 'X-Request-ID'
_SAFE_REQUEST_ID = re.compile(r'[A-Za-z0-9._-]{1,128}')
# Hit every few seconds by the Container App probes: logged at DEBUG so they don't
# drown everything else.
_QUIET_PATHS = {'/health', '/api/v1/check/db'}

request_id_var: ContextVar[str] = ContextVar('request_id', default='-')

logger = logging.getLogger(__name__)
access_logger = logging.getLogger('app.access')


class RequestIdLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def _resolve_request_id(incoming: str | None) -> str:
    # The caller's id ends up in our logs verbatim, so only reuse it when it can't
    # inject newlines or fake log fields.
    if incoming and _SAFE_REQUEST_ID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


async def request_context_middleware(
        request: Request, call_next: RequestResponseEndpoint,
) -> Response:
    request_id = _resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            # Caught here instead of with an `Exception` handler: Starlette runs those
            # outside every middleware and re-raises afterwards, which would skip the
            # X-Request-ID header and log the same traceback twice.
            logger.exception(
                'Unhandled error on %s %s', request.method, request.url.path,
            )
            response = build_error_response(
                request, 500, 'internal_error', 'Internal server error.', exc=exc,
            )

        response.headers[REQUEST_ID_HEADER] = request_id
        access_logger.log(
            logging.DEBUG if request.url.path in _QUIET_PATHS else logging.INFO,
            '%s %s -> %d (%.1f ms) client=%s',
            request.method, request.url.path, response.status_code,
            (time.perf_counter() - started) * 1000,
            request.client.host if request.client else '-',
        )
        return response
    finally:
        # Reset only after the access log, so that line still carries the request id.
        request_id_var.reset(token)
