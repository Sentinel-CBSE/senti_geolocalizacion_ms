import logging

from fastapi import APIRouter, Request, Depends
from pydantic import ValidationError, TypeAdapter

from app.services import incidents
from app.dependencies import get_redis, get_sender
from app.error_handlers import error_responses
from app.exceptions import InvalidEventPayloadError, summarize_validation_errors
from app.schemas import (
    BrokerEvent,
    DataReportedIncident,
    StatusResponse,
    SubscriptionValidation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/webhook', tags=['Webhook Manager'])

VALIDATION_EVENT_TYPE = 'Microsoft.EventGrid.SubscriptionValidationEvent'
INCIDENT_REPORTED_EVENT_TYPE = 'Sentinel.IncidenteReportado'

_events_adapter = TypeAdapter(list[BrokerEvent])


@router.post('/incidents', responses=error_responses(400, 500, 503))
async def get_event(
        request: Request,
        redis = Depends(get_redis),
        sender = Depends(get_sender)
) -> SubscriptionValidation | StatusResponse:
    """
    Azure Event Grid webhook. Event Grid always sends the body as an ARRAY of events,
    even when there's only one — that's why it's validated as list[BrokerEvent], not
    a single bare object.

    Handles two cases, identified by event_type:
    - Microsoft.EventGrid.SubscriptionValidationEvent: the handshake Event Grid fires
      once when the subscription is created (always arrives alone, never mixed with
      real events). We have to echo back the same validationCode it sent, or the
      subscription stays in Failed and real events never start arriving.
    - Sentinel.IncidenteReportado: the real event. Its `data` is validated as
      DataReportedIncident and handed off to incidents.orchestrate_nearby_incidents.

    Any event with another event_type, or that fails DataReportedIncident validation,
    is skipped with `continue` — it doesn't abort processing of the other events in
    the same array.

    The status code drives Event Grid's retry policy, so it's chosen on purpose:
    - 400 (malformed body): permanent, Event Grid does NOT retry it (it dead-letters
      it if dead-lettering is configured, otherwise drops it).
    - 503 (Redis/Service Bus down): transient, Event Grid retries it.
    - 500 (unexpected bug): Event Grid retries it too.
    """
    try:
        events = _events_adapter.validate_json(await request.body())
    except ValidationError as e:
        raise InvalidEventPayloadError.from_validation_error(e) from e

    for event in events:
        if event.event_type == VALIDATION_EVENT_TYPE:
            code = event.data.get('validationCode')
            if not isinstance(code, str) or not code:
                raise InvalidEventPayloadError(
                    "Subscription validation event is missing 'validationCode'.",
                )
            logger.info("Handled Event Grid subscription validation handshake.")
            return SubscriptionValidation(validationResponse=code)

        if event.event_type != INCIDENT_REPORTED_EVENT_TYPE:
            logger.warning(
                "Ignoring unrecognized event type %s (id=%s)",
                event.event_type, event.id,
            )
            continue

        try:
            data = DataReportedIncident.model_validate(event.data)
        except ValidationError as e:
            logger.warning(
                "Skipping event %s, invalid incident payload: %s",
                event.id, summarize_validation_errors(e.errors()),
            )
            continue

        logger.info(
            "Processing incident event %s (type=%s, lat=%s, lon=%s)",
            event.id, data.incidentType, data.latitude, data.longitude,
        )
        await incidents.orchestrate_nearby_incidents(
            longitude=data.longitude,
            latitude=data.latitude,
            redis=redis,
            event_id=event.id,
            incident_type=data.incidentType,
            incident_timestamp=data.timestamp,
            sender=sender
        )

    return StatusResponse()
