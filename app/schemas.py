from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict

# ---------------------------------------------------------------------------------------
#                                       REQUESTS
# ---------------------------------------------------------------------------------------
class UserLocation(BaseModel):
    user_id: str = Field(alias='userId')
    latitude: float
    longitude: float


class IncidentType(str, Enum):
    ARMED_ROBBERY = 'armed_robbery'
    THEFT = 'theft'
    BURGLARY = 'burglary'


class DataReportedIncident(BaseModel):
    incidentType: IncidentType = Field(alias='type')
    latitude: float
    longitude: float
    timestamp: int = Field(ge=0)
    reporting_user_id: str = Field(alias='reportingUserId', min_length=1)


class BrokerEvent(BaseModel):
    id: str
    topic: str
    data: dict[str, Any]
    event_type: str = Field(alias="eventType")


# ---------------------------------------------------------------------------------------
#                                       RESPONSES
# ---------------------------------------------------------------------------------------
class SubscriptionValidation(BaseModel):
    """Echoes Event Grid's validationCode back to complete the subscription handshake."""
    model_config = ConfigDict(populate_by_name=True)

    validation_response: str = Field(
        alias="validationResponse", serialization_alias="validationResponse",
        examples=['512d38b6-c7b8-40c8-89fe-f46f9e9622b6'],
    )


class StatusResponse(BaseModel):
    status: Literal['ok'] = 'ok'


class ErrorDetail(BaseModel):
    loc: list[str | int] = Field(examples=[['body', 'latitude']])
    msg: str = Field(examples=['Field required'])
    type: str = Field(examples=['missing'])


class ErrorBody(BaseModel):
    code: str = Field(
        description='Stable, machine-readable error code.',
        examples=['location_store_unavailable'],
    )
    message: str = Field(
        description='Human-readable message, never contains internal details.',
        examples=['The location store is temporarily unavailable.'],
    )
    request_id: str | None = Field(
        description='Same value as the X-Request-ID response header.',
        examples=['9e72b7ee1b364351970effd256f0b315'],
    )
    details: list[ErrorDetail] | None = Field(
        default=None, description='Only present for validation errors.',
    )
    debug: dict[str, str] | None = Field(
        default=None, description='Only present on 5xx responses when DEBUG=true.',
    )


class ErrorResponse(BaseModel):
    """Shape of every error response (built by app/error_handlers.py)."""
    error: ErrorBody


# ---------------------------------------------------------------------------------------
#                                   MESSAGES QUEUE
# ---------------------------------------------------------------------------------------
class NotificationMessage(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    event_id: str = Field(serialization_alias="eventId")
    user_id: str = Field(serialization_alias="userId")
    incident_type: IncidentType = Field(serialization_alias="incidentType")
    latitude: float
    longitude: float
    incident_timestamp: float = Field(serialization_alias="incidentTimestamp")
    distance_meters: float = Field(serialization_alias="distanceMeters")
