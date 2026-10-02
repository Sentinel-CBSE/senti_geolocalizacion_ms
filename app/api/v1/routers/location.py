import logging

from redis.asyncio import Redis
from fastapi import APIRouter, Depends, status

from app.schemas import UserLocation
from app.dependencies import get_redis
from app.error_handlers import error_responses
from app.services.location import store_user_location

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/location', tags=['Location Manager'])


@router.post(
    '/add_location',
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(422, 500, 503),
)
async def add_location(
        request: UserLocation,
        redis_db: Redis = Depends(get_redis)
) -> None:
    """
    Stores/updates a user's location. Responds 204 with no body on purpose — the
    caller (the mobile app) only needs to know it was saved. Errors (invalid body,
    Redis down) are turned into responses by app/error_handlers.py.
    """
    await store_user_location(
        redis_db, request.user_id, request.latitude, request.longitude,
    )
    logger.debug(
        "Stored location for user %s (lat=%s, lon=%s)",
        request.user_id, request.latitude, request.longitude,
    )
