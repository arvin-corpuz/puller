from __future__ import annotations

import asyncio

import uvicorn
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from puller.logging_setup import get_logger
from puller.metrics import registry as metrics_registry

log = get_logger(component="health")


class ReadinessState:
    """Flips to ready once config is loaded and the state store is
    initialized. A transient error in one watcher's poll cycle does NOT
    affect this — readiness reflects whether the service started up
    successfully, not the health of individual registry connections."""

    def __init__(self) -> None:
        self.ready = False

    def set_ready(self) -> None:
        self.ready = True


def create_app(readiness: ReadinessState) -> Starlette:
    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    async def readyz(request: Request) -> Response:
        if readiness.ready:
            return PlainTextResponse("ok")
        return PlainTextResponse("not ready", status_code=503)

    async def metrics_endpoint(request: Request) -> Response:
        return Response(generate_latest(metrics_registry), media_type=CONTENT_TYPE_LATEST)

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/readyz", readyz),
            Route("/metrics", metrics_endpoint),
        ]
    )


async def serve_health(
    host: str, port: int, readiness: ReadinessState, shutdown: asyncio.Event
) -> None:
    app = create_app(readiness)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())
    log.info("health_server_started", host=host, port=port)

    await shutdown.wait()
    server.should_exit = True
    await server_task
