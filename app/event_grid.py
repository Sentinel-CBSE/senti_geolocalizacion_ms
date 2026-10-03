"""
Plumbing shared by every Azure Event Grid webhook (Event Grid schema).

Each webhook endpoint has its own Event Grid subscription, so each one receives its
own validation handshake when that subscription is created.
"""
import logging
from typing import TypeVar

from fastapi import Request
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.exceptions import InvalidEventPayloadError, summarize_validation_errors
from app.schemas import BrokerEvent, SubscriptionValidation

logger = logging.getLogger(__name__)

VALIDATION_EVENT_TYPE = 'Microsoft.EventGrid.SubscriptionValidationEvent'

_events_adapter = TypeAdapter(list[BrokerEvent])

DataModel = TypeVar('DataModel', bound=BaseModel)


async def parse_events(request: Request) -> list[BrokerEvent]:
    """
    Event Grid always sends an ARRAY of events, even when there's only one.

    :raises InvalidEventPayloadError: the body isn't a valid array of events (400,
        which Event Grid does not retry).
    """
    try:
        return _events_adapter.validate_json(await request.body())
    except ValidationError as e:
        raise InvalidEventPayloadError.from_validation_error(e) from e


def handshake_response(events: list[BrokerEvent]) -> SubscriptionValidation | None:
    """
    Returns the reply to the subscription validation handshake, or None if this is
    a regular delivery. The handshake always arrives alone, never mixed with real
    events; without echoing its validationCode the subscription stays in Failed.

    :raises InvalidEventPayloadError: the handshake has no validationCode.
    """
    for event in events:
        if event.event_type != VALIDATION_EVENT_TYPE:
            continue
        code = event.data.get('validationCode')
        if not isinstance(code, str) or not code:
            raise InvalidEventPayloadError(
                "Subscription validation event is missing 'validationCode'.",
            )
        logger.info("Handled Event Grid subscription validation handshake.")
        return SubscriptionValidation(validationResponse=code)
    return None


def parse_event_data(
        event: BrokerEvent, expected_type: str, model: type[DataModel],
) -> DataModel | None:
    """
    Validates an event's `data` against `model`. Returns None (and logs why) when the
    event has another type or invalid data, so the caller skips it without aborting
    the other events of the same delivery.
    """
    if event.event_type != expected_type:
        logger.warning(
            "Ignoring unrecognized event type %s (id=%s)", event.event_type, event.id,
        )
        return None
    try:
        return model.model_validate(event.data)
    except ValidationError as e:
        logger.warning(
            "Skipping event %s (%s), invalid data: %s",
            event.id, event.event_type, summarize_validation_errors(e.errors()),
        )
        return None
