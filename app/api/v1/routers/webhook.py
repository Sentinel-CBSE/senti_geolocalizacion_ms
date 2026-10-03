"""
Azure Event Grid webhooks. Each endpoint has its own Event Grid subscription and
handles one event type, plus the subscription validation handshake (app/event_grid.py).

The status code drives Event Grid's retry policy, so it's chosen on purpose:
- 400 (malformed body): permanent, Event Grid does NOT retry it (it dead-letters it
  if dead-lettering is configured, otherwise drops it).
- 503 (Redis/Service Bus down): transient, Event Grid retries it.
- 500 (unexpected bug): Event Grid retries it too.

Events with another event type, or with invalid data, are skipped (logged) without
aborting the other events of the same delivery.
"""
import logging

from fastapi import APIRouter, Request, Depends

from app.services import incidents
from app.services.location import store_user_location
from app.dependencies import get_redis, get_sender
from app.error_handlers import error_responses
from app.event_grid import handshake_response, parse_event_data, parse_events
from app.schemas import (
    DataReportedIncident,
    StatusResponse,
    SubscriptionValidation,
    UserLocation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/webhook', tags=['Webhook Manager'])

INCIDENT_REPORTED_EVENT_TYPE = 'Sentinel.IncidenteReportado'
LOCATION_UPDATED_EVENT_TYPE = 'Sentinel.UbicacionActualizada'


@router.post('/incidents', responses=error_responses(400, 500, 503))
async def receive_incident_events(
        request: Request,
        redis = Depends(get_redis),
        sender = Depends(get_sender)
) -> SubscriptionValidation | StatusResponse:
    """
    Receives `Sentinel.IncidenteReportado` events: for each one, finds the users near
    the incident and queues a notification for each of them.
    """
    events = await parse_events(request)
    if (handshake := handshake_response(events)) is not None:
        return handshake

    for event in events:
        data = parse_event_data(
            event, INCIDENT_REPORTED_EVENT_TYPE, DataReportedIncident,
        )
        if data is None:
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


@router.post('/locations', responses=error_responses(400, 500, 503))
async def receive_location_events(
        request: Request,
        redis = Depends(get_redis),
) -> SubscriptionValidation | StatusResponse:
    """
    Receives `Sentinel.UbicacionActualizada` events (the location the mobile app
    sends periodically, published by the API gateway) and stores each one in Redis.
    GEOADD upserts, so a redelivered event just writes the same location again.
    """
    events = await parse_events(request)
    if (handshake := handshake_response(events)) is not None:
        return handshake

    for event in events:
        location = parse_event_data(event, LOCATION_UPDATED_EVENT_TYPE, UserLocation)
        if location is None:
            continue

        await store_user_location(
            redis, location.user_id, location.latitude, location.longitude,
        )
        logger.debug(
            "Stored location for user %s (lat=%s, lon=%s, event=%s)",
            location.user_id, location.latitude, location.longitude, event.id,
        )

    return StatusResponse()
