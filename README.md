# senti_geolocalizacion_ms

Geolocation microservice for **SENTI**. It keeps track of where users are and, when
a robbery is reported, finds the users close to it and queues a notification for
each of them.

- Receives the users' locations (sent periodically by the mobile app `senti_ma`)
  as `Sentinel.UbicacionActualizada` events from Azure Event Grid, and stores them
  in Redis.
- Receives `Sentinel.IncidenteReportado` events from Azure Event Grid.
- Finds nearby users with Redis `GEOSEARCH`.
- Publishes one notification message per nearby user to an Azure Service Bus queue,
  consumed by `senti_notificaciones_ms`.

Both kinds of events are published by the API gateway, which receives the mobile
app's requests and sets the user id from the caller's JWT. This service never
receives requests from the app directly.

## Architecture

```
senti_ma ──▶ API gateway ──▶ Event Grid
                                 │
                                 ├─ Sentinel.UbicacionActualizada ─▶ POST /webhook/locations
                                 └─ Sentinel.IncidenteReportado ───▶ POST /webhook/incidents
                                                                            │
                                     ┌──────────────────────────────────────┘
                                     ▼
                          senti_geolocalizacion_ms ──GEOADD/GEOSEARCH──▶ Redis
                                     │ one message per nearby user
                                     ▼
                          Service Bus queue ──▶ senti_notificaciones_ms
```

Each webhook has its own Event Grid subscription, so they're retried and scaled
independently (location updates are far more frequent than incidents).

## Tech stack

| Concern               | Choice                                                          |
|-----------------------|-----------------------------------------------------------------|
| Runtime               | Python 3.13, FastAPI, Uvicorn                                   |
| Dependency management | [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`) |
| Location store        | Redis 7.4 (geospatial sorted set)                               |
| Messaging             | Azure Service Bus (queue), Azure Event Grid (webhook)           |
| Configuration         | `pydantic-settings` (environment variables / `.env`)            |
| Local infrastructure  | Docker Compose: Redis, Service Bus emulator, Azure SQL Edge     |

## Getting started

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker with Docker Compose

### 1. Configure the environment

Create a `.env` file in the project root (it's git-ignored):

```dotenv
REDIS_URL=redis://localhost:6379
LOCATION_KEY=users_location
RADIUS_ALERT=500
RADIUS_UNITY=M

CONNECTION_STR=Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;
QUEUE_NAME=notificaciones

DEBUG=true
```

The `CONNECTION_STR` above targets the local Service Bus emulator: `SAS_KEY_VALUE` is
literal and `UseDevelopmentEmulator=true` is required. Against Azure, use the
namespace's real connection string instead (and drop that flag).

### 2. Start the infrastructure

```bash
docker compose up -d
```

This starts Redis, the Service Bus emulator and Azure SQL Edge (the emulator's
internal storage). The emulator takes ~30 seconds to be ready; check with
`docker logs senti_servicebus` until you see `Emulator Service is Successfully Up!`.

Queues are declared in `servicebus-emulator/Config.json` and created on every
emulator start. Emulator data does **not** survive a container restart.

### 3. Run the service

```bash
uv sync
uv run uvicorn app.main:app --reload
```

With `DEBUG=true`, the interactive API docs are at http://localhost:8000/docs.

### Running everything in containers

The `app` service in `docker-compose.yml` sits behind the `full` profile, so it
doesn't start during day-to-day development. To run the whole stack as a black box
(e.g. to test a consumer service against it):

```bash
docker compose --profile full up -d --build
docker compose --profile full down
```

Pass `--profile full` to `down` as well, otherwise the `app` container is left
running.

## Configuration

| Variable                | Required | Default | Description                                                    |
|-------------------------|----------|---------|----------------------------------------------------------------|
| `REDIS_URL`             | yes      |         | Redis connection URL (`redis://` locally, `rediss://` for TLS) |
| `REDIS_TIMEOUT_SECONDS` | no       | `5.0`   | Connect/read timeout, so an unresponsive Redis fails fast      |
| `LOCATION_KEY`          | yes      |         | Redis key holding the geospatial set of user locations         |
| `RADIUS_ALERT`          | yes      |         | Alert radius around an incident                                |
| `RADIUS_UNITY`          | yes      |         | Radius unit: `M`, `KM`, `FT` or `MI` (see known limitations)   |
| `CONNECTION_STR`        | yes      |         | Service Bus connection string                                  |
| `QUEUE_NAME`            | yes      |         | Queue where notifications are published                        |
| `DEBUG`                 | no       | `false` | Enables `/docs`, DEBUG logging and debug info in 5xx errors    |

## API

All business endpoints are under `/api/v1`, and all of them are Azure Event Grid
webhooks (Event Grid schema):

- The body is always an **array** of events, even with a single event.
- Each webhook answers the subscription handshake
  (`Microsoft.EventGrid.SubscriptionValidationEvent`) with
  `{"validationResponse": "<code>"}`, and any other delivery with
  `{"status": "ok"}`.
- Events with another `eventType`, or with invalid `data`, are logged and skipped
  without aborting the rest of the delivery.

### `POST /api/v1/webhook/locations`

Receives `Sentinel.UbicacionActualizada` events and stores each location
(`GEOADD` upserts, so a redelivered event just writes the same location again):

```json
[
  {
    "id": "evt-loc-001",
    "topic": "/subscriptions/.../topics/...",
    "subject": "ubicaciones/firebase-uid-abc123",
    "eventType": "Sentinel.UbicacionActualizada",
    "eventTime": "2026-10-03T15:30:00.000Z",
    "dataVersion": "1.0",
    "data": {
      "userId": "firebase-uid-abc123",
      "latitude": 4.6097,
      "longitude": -74.0817
    }
  }
]
```

`latitude` must be within ±85.05112878 and `longitude` within ±180 (the ranges
Redis geo commands accept); events outside them are skipped as invalid data.

Event Grid doesn't guarantee delivery order, so a retried older location can
overwrite a newer one. Nothing guards against that yet: doing so needs the event's
timestamp and a per-user "last seen" check.

### `POST /api/v1/webhook/incidents`

Receives `Sentinel.IncidenteReportado` events, finds the users near each incident
and queues a notification for each of them:

```json
[
  {
    "id": "evt-001",
    "topic": "/subscriptions/.../topics/...",
    "subject": "incidentes/theft",
    "eventType": "Sentinel.IncidenteReportado",
    "eventTime": "2026-10-01T15:30:00.000Z",
    "dataVersion": "1.0",
    "data": {
      "type": "theft",
      "latitude": 4.635336,
      "longitude": -74.064562,
      "timestamp": 1759331400000,
      "reportingUserId": "firebase-uid-xyz"
    }
  }
]
```

`type` is one of `armed_robbery`, `theft`, `burglary`.

### Health checks

| Endpoint               | Purpose                                      |
|------------------------|----------------------------------------------|
| `GET /health`          | Liveness: the process is up                  |
| `GET /api/v1/check/db` | Readiness: Redis is reachable (`503` if not) |

## Published messages

One message per nearby user is sent to `QUEUE_NAME`, as JSON:

```json
{
  "eventId": "evt-001",
  "userId": "firebase-uid-abc123",
  "incidentType": "theft",
  "latitude": 4.635336,
  "longitude": -74.064562,
  "incidentTimestamp": 1759331400000,
  "distanceMeters": 120.5
}
```

`incidentTimestamp` is when the incident happened (epoch millis), not when it was
processed. Messages are sent in batches to save network round-trips, but each one
is an independent message on the queue.

## Error handling

Every error response has the same shape:

```json
{
  "error": {
    "code": "location_store_unavailable",
    "message": "The location store is temporarily unavailable.",
    "request_id": "9e72b7ee1b364351970effd256f0b315",
    "details": []
  }
}
```

`code` is stable and meant for clients to branch on; `message` never contains
internal details. `details` is present for validation errors only.

| Status | `code`                        | Meaning                                         |
|--------|-------------------------------|-------------------------------------------------|
| 400    | `invalid_event_payload`       | Malformed Event Grid body (permanent)           |
| 404    | `not_found`                   | Unknown route                                   |
| 500    | `internal_error`              | Unexpected error (a bug)                        |
| 503    | `location_store_unavailable`  | Redis unreachable or timed out (transient)      |
| 503    | `message_broker_unavailable`  | Service Bus failed (transient)                  |

`503` responses include a `Retry-After` header. The status codes are chosen for
Event Grid's retry policy: it does **not** retry `400` (the event is dead-lettered
if dead-lettering is configured, dropped otherwise) and **does** retry `500`/`503`.

### Request tracing

Every request gets a request id, returned in the `X-Request-ID` response header and
included in every log line. If the caller sends a safe `X-Request-ID`
(`[A-Za-z0-9._-]`, up to 128 chars) it's reused; otherwise a new one is generated.

```
2026-10-02 07:28:28,560 INFO [30f32f2a…] app.services.incidents: Incident evt-1: 1 user(s) nearby, dispatching notifications.
```

## Project structure

```
app/
├── main.py                 # app factory, lifespan (clients), logging setup
├── config.py               # Settings (pydantic-settings)
├── dependencies.py         # FastAPI dependencies returning the shared clients
├── schemas.py              # event, response and queue message models
├── exceptions.py           # domain exceptions (status code + error code)
├── error_handlers.py       # turns every error into the common JSON shape
├── request_context.py      # request id, access log, last-resort error boundary
├── logging_config.py       # single log format (UTC timestamps + request id)
├── event_grid.py           # shared webhook plumbing: event parsing, handshake
├── api/v1/routers/
│   ├── webhook.py          # POST /webhook/locations, POST /webhook/incidents
│   └── health.py           # GET /check/db
└── services/
    ├── location.py         # GEOADD / GEOSEARCH
    ├── incidents.py        # orchestration: find nearby users, then notify
    └── notifications.py    # builds and sends queue messages
servicebus-emulator/
└── Config.json             # queues created by the local Service Bus emulator
```

Routers only handle HTTP concerns; services hold the logic and receive their
clients (Redis, Service Bus sender) as plain parameters, so they can be called and
tested without FastAPI. Clients are created once at startup and closed on
shutdown (`lifespan` in `main.py`).

## Docker image

The `Dockerfile` is a multi-stage build: dependencies are installed with `uv` from
the lockfile in a builder stage, and only the virtualenv and `app/` are copied into
a slim runtime image, which runs as a non-root user.

```bash
docker build -t senti-geolocalizacion-ms .
```
