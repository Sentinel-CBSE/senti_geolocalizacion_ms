"""
FastAPI dependencies (used with Depends()) that return the singleton clients created
once in main.py's lifespan (app.state), to inject them into endpoints without each
one knowing how they were built or having to create a new one per request.

Only used in endpoints (via Depends), never directly in services — there, Depends
never gets resolved, since nothing calls them through FastAPI (see
location.py/notifications.py, which receive the already-resolved client as a plain
parameter instead).
"""
from fastapi import Request
from redis.asyncio import Redis
from azure.servicebus.aio import ServiceBusSender


def get_redis(request: Request) -> Redis:
    return request.app.state.redis

def get_sender(request: Request) -> ServiceBusSender:
    return request.app.state.sender
