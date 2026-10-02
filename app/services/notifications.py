import logging

from azure.servicebus.aio import ServiceBusSender
from azure.servicebus import ServiceBusMessage, ServiceBusMessageBatch
from azure.servicebus.exceptions import MessageSizeExceededError, ServiceBusError

from app.exceptions import MessageBrokerUnavailableError
from app.schemas import IncidentType, NotificationMessage

logger = logging.getLogger(__name__)


async def send_messages(
        sender: ServiceBusSender,
        longitude: float,
        latitude: float,
        event_id: str,
        incident_type: IncidentType,
        incident_timestamp: float,
        users_nearby: list[tuple[str, int]]
):
    """
    Builds one NotificationMessage per nearby user (not a single one holding all of
    them) and sends them to the queue. One message per user is deliberate: each one
    gets retried, fails, or gets deduplicated independently on the consumer's side —
    that doesn't stop them from being sent together in just a few network
    round-trips, which is what the batching below handles.

    Messages are grouped into batches (ServiceBusMessageBatch) so we don't pay a
    network round-trip per user. A batch has a size limit; if it fills up, that batch
    is sent and a new one is started with the message that didn't fit, without
    losing any.

    :param sender: already-connected Service Bus sender (app.state.sender singleton).
    :param longitude: incident longitude, repeated identically in every message.
    :param latitude: incident latitude, repeated identically in every message.
    :param event_id: id of the Event Grid event that triggered this incident.
    :param incident_type: incident type, repeated identically in every message.
    :param incident_timestamp: epoch millis of when the incident happened.
    :param users_nearby: list of (user_id, distance) from get_users_nearby — one
        message goes out per element of this list.
    :raises MessageBrokerUnavailableError: Service Bus failed. Batches sent before
        the failure are NOT rolled back, so a retry resends them: consumers rely on
        message_id deduplication to avoid duplicate notifications.
    """
    messages: list[NotificationMessage] = []
    for user_id, distance in users_nearby:
        messages.append(NotificationMessage(
            event_id=event_id,
            user_id=user_id,
            incident_type=incident_type,
            latitude=latitude,
            longitude=longitude,
            incident_timestamp=incident_timestamp,
            distance_meters=distance
        ))

    batches: list[ServiceBusMessageBatch] = []
    try:
        batch_message = await sender.create_message_batch()
        for message in messages:
            sb_message = ServiceBusMessage(message.model_dump_json())
            try:
                batch_message.add_message(sb_message)
            except MessageSizeExceededError:
                logger.warning(
                    "Incident %s: batch full after %d message(s), starting a new one.",
                    event_id, len(batch_message),
                )
                batches.append(batch_message)
                batch_message = await sender.create_message_batch()
                batch_message.add_message(sb_message)
        batches.append(batch_message)

        for batch in batches:
            await sender.send_messages(batch)
    except MessageSizeExceededError:
        # A single message too big for an empty batch is our bug, not an outage:
        # let it surface as a 500 instead of a misleading "broker unavailable".
        raise
    except ServiceBusError as e:
        raise MessageBrokerUnavailableError() from e

    logger.info(
        "Incident %s: sent %d notification(s) in %d batch(es).",
        event_id, len(messages), len(batches),
    )
