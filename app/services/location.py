import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings
from app.exceptions import LocationStoreUnavailableError

logger = logging.getLogger(__name__)


async def store_user_location(
        db: Redis,
        user_id: str,
        latitude: float,
        longitude: float,
        location_key: str = settings.location_key,
) -> None:
    """
    Stores/updates a user's location. GEOADD upserts on its own: the user's first
    location and their hundredth are the same call.

    :raises LocationStoreUnavailableError: Redis is unreachable or timed out.
    """
    try:
        await db.geoadd(location_key, (longitude, latitude, user_id))
    except RedisError as e:
        raise LocationStoreUnavailableError() from e


async def get_users_nearby(
        longitude: float,
        latitude: float,
        db: Redis,
        location_key = settings.location_key,
        radius = settings.radius_alert,
        radius_unity = settings.radius_unity,
        ascendant: bool = True,
        width_distance: bool = True
) -> list[tuple[str, int]]:
    """
    Finds users with a registered location within a radius around a point, using Redis
    GEOSEARCH on the geospatial key configured in settings.location_key.

    :param longitude: longitude of the search point (comes first, Redis expects lon
        before lat).
    :param latitude: latitude of the search point.
    :param db: already-connected async Redis client (the app.state.redis singleton,
        not a new one).
    :param location_key: name of the Redis key where GEOADD stores the locations.
        Defaults to settings.location_key; can be overridden in tests.
    :param radius: search radius, in the unit given by radius_unity. Defaults to
        settings.radius_alert.
    :param radius_unity: radius unit (M/KM/FT/MI, see RadioUnity in config.py). Defaults
        to settings.radius_unity. Heads up: the distance GEOSEARCH returns is in THIS
        unit, and notifications.py sends it as-is under the distance_meters field with
        no conversion — if this isn't METERS, see the warning main.py logs at startup.
    :param ascendant: if True, sorts results from closest to farthest (GEOSEARCH ASC).
    :param width_distance: if True, requests WITHDIST — each result comes with its
        distance to the point.
    :return: list of (user_id, distance) tuples, one per user found within the radius.
    :raises LocationStoreUnavailableError: Redis is unreachable or timed out.
    """
    try:
        users_nearby = await db.geosearch(
            name=location_key,
            latitude=latitude,
            longitude=longitude,
            unit=radius_unity,
            radius=radius,
            sort='ASC' if ascendant else None,
            withdist=width_distance
        )
    except RedisError as e:
        raise LocationStoreUnavailableError() from e
    logger.debug(
        "GEOSEARCH at (%s, %s) radius=%s%s found %d result(s).",
        latitude, longitude, radius, radius_unity, len(users_nearby),
    )
    return users_nearby
