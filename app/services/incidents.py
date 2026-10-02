import logging

from redis.asyncio import Redis
from azure.servicebus.aio import ServiceBusSender

from app.schemas import IncidentType
from .location import get_users_nearby
from .notifications import send_messages

logger = logging.getLogger(__name__)


async def orchestrate_nearby_incidents(
        longitude: float,
        latitude: float,
        redis: Redis,
        event_id: str,
        incident_type: IncidentType,
        incident_timestamp: float,
        sender: ServiceBusSender
) -> None:
    """
    Orchestrates the reaction to an already-reported incident: finds users near where
    it happened and, if there are any, sends them a notification through the queue.
    If nobody is nearby, does nothing.

    Doesn't create the event or publish to Event Grid — that already happened before
    this was called (see webhook.py). This function only reacts once the incident is
    already a confirmed fact.

    :param longitude: longitude of where the incident happened.
    :param latitude: latitude of where the incident happened.
    :param redis: already-connected async Redis client, passed to get_users_nearby.
    :param event_id: Event Grid event id — used to correlate logs and, later, as
        part of each notification's message_id (dedup by event+user).
    :param incident_type: incident type (armed_robbery/theft/burglary), included
        in every notification.
    :param incident_timestamp: epoch millis of when the incident happened (not
        when it's processed).
    :param sender: already-connected Service Bus sender, passed to send_messages.
    """
    users_nearby = await get_users_nearby(
        longitude=longitude,
        latitude=latitude,
        db=redis
    )
    if not users_nearby:
        logger.info("Incident %s: no users nearby, nothing to notify.", event_id)
        return

    logger.info(
        "Incident %s: %d user(s) nearby, dispatching notifications.",
        event_id, len(users_nearby),
    )
    await send_messages(
        sender=sender,
        longitude=longitude,
        latitude=latitude,
        event_id=event_id,
        incident_type=incident_type,
        incident_timestamp=incident_timestamp,
        users_nearby=users_nearby
    )
    return
