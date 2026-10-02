"""
Logging setup: one format for every log line (ours, libraries' and Uvicorn's), with
UTC ISO-8601 timestamps and the current request id.

    2026-10-02T21:35:58.618Z INFO [f46764d5...] app.access: POST /api/v1/... -> 204
"""
import logging
import time

from app.request_context import RequestIdLogFilter


class _UtcIsoFormatter(logging.Formatter):
    # Servers (and containers) usually run in UTC: the trailing Z makes that explicit
    # instead of showing a time that looks wrong next to the local clock.
    converter = time.gmtime

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        base = time.strftime('%Y-%m-%dT%H:%M:%S', self.converter(record.created))
        return f'{base}.{int(record.msecs):03d}Z'


def configure_logging(debug: bool) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_UtcIsoFormatter(
        '%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s',
    ))
    handler.addFilter(RequestIdLogFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # Uvicorn installs its own handlers with a different format: send its loggers
    # through ours instead. Its access log is replaced by the one in
    # request_context.py, which can include the request id and the duration.
    for name in ('uvicorn', 'uvicorn.error'):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger('uvicorn.access').disabled = True
