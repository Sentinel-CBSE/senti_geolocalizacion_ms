"""
Domain exceptions. Services translate infrastructure errors (Redis, Service Bus)
into these, so routers never deal with library-specific exceptions and every error
leaves the API with the same shape (see error_handlers.py).

`message` is always safe to show to clients. The original cause stays in the
exception chain (`raise ... from e`) and only ever reaches the logs.
"""
from typing import Any, Iterable

from pydantic import ValidationError


class AppError(Exception):
    status_code: int = 500
    code: str = 'internal_error'
    message: str = 'Internal server error.'

    def __init__(
            self,
            message: str | None = None,
            details: list[dict[str, Any]] | None = None,
    ):
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)


class InvalidEventPayloadError(AppError):
    """Permanent: the same payload will never succeed, so it must not be retried."""
    status_code = 400
    code = 'invalid_event_payload'
    message = 'The event payload is malformed.'

    @classmethod
    def from_validation_error(cls, error: ValidationError) -> 'InvalidEventPayloadError':
        return cls(details=summarize_validation_errors(error.errors()))


class DependencyUnavailableError(AppError):
    """Transient: a downstream dependency failed, the caller should retry later."""
    status_code = 503
    code = 'dependency_unavailable'
    message = 'A required service is temporarily unavailable.'
    retry_after_seconds = 30


class LocationStoreUnavailableError(DependencyUnavailableError):
    code = 'location_store_unavailable'
    message = 'The location store is temporarily unavailable.'


class MessageBrokerUnavailableError(DependencyUnavailableError):
    code = 'message_broker_unavailable'
    message = 'The message broker is temporarily unavailable.'


def summarize_validation_errors(
        errors: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keeps loc/msg/type only: drops `input`, which can echo back the whole payload."""
    return [
        {'loc': list(error['loc']), 'msg': error['msg'], 'type': error['type']}
        for error in errors
    ]
