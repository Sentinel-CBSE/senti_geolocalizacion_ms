from fastapi import Depends, APIRouter
from redis.exceptions import RedisError

from app.dependencies import get_redis
from app.error_handlers import error_responses
from app.exceptions import LocationStoreUnavailableError
from app.schemas import StatusResponse

router = APIRouter(prefix='/check', tags=['Check Manager'])


@router.get('/db', responses=error_responses(500, 503))
async def check_db(db = Depends(get_redis)) -> StatusResponse:
    """
    Redis health check. Meant to be called by the Container App's readiness/liveness
    probes — a failure responds 503 (not 200 with the error in the body) because a
    probe checks the status code, not the response content.
    """
    try:
        await db.ping()
    except RedisError as e:
        raise LocationStoreUnavailableError() from e
    return StatusResponse()
