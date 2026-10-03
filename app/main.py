import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
import redis.asyncio as redis
from azure.servicebus.aio import ServiceBusClient

from app.config import settings, RadioUnity
from app.error_handlers import register_error_handlers
from app.logging_config import configure_logging
from app.request_context import request_context_middleware
from app.schemas import StatusResponse
from app.api.v1.routers import health_router, webhook_router

configure_logging(settings.debug)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Starting up: connecting to Redis (%s) and Service Bus...", settings.redis_url,
    )

    if settings.radius_unity != RadioUnity.METERS:
        logger.warning(
            "radius_unity is set to %s, not METERS — GEOSEARCH will return distances "
            "in that unit, but outgoing notification payloads always report them "
            "under the field distance_meters with no unit conversion. Consumers will "
            "receive the raw %s value mislabeled as meters.",
            settings.radius_unity, settings.radius_unity,
        )

    app.state.redis = redis.Redis.from_url(
        url=settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
    )
    client = ServiceBusClient.from_connection_string(conn_str=settings.connection_str)
    sender = client.get_queue_sender(queue_name=settings.queue_name)
    app.state.client = client
    app.state.sender = sender
    logger.info("Startup complete.")

    yield

    logger.info("Shutting down: closing Service Bus and Redis connections...")
    await app.state.sender.close()
    await app.state.client.close()
    await app.state.redis.aclose()
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    app: FastAPI = FastAPI(
        lifespan=lifespan,
        docs_url='/docs' if settings.debug else None
    )

    # ------ Errors & request context ------
    app.middleware('http')(request_context_middleware)
    register_error_handlers(app)

    # ------ Routers ------
    app.include_router(health_router, prefix='/api/v1')
    app.include_router(webhook_router, prefix='/api/v1')

    # ----- Health endpoint -----
    @app.get('/health')
    def health() -> StatusResponse:
        return StatusResponse()

    return app


app = create_app()
