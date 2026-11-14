"""FastAPI application factory.

Run:  uvicorn causaltwin.api.app:app --port 8000   (from the backend folder)
Docs: http://localhost:8000/docs
CORS origins come from the CAUSALTWIN_CORS_ORIGINS environment variable (comma-separated).
"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from causaltwin import __version__
from causaltwin.api.hub import StreamHub
from causaltwin.api.info_routes import router as info_router
from causaltwin.api.routes import router
from causaltwin.api.state import AppState
from causaltwin.config import AUTOSTART_ENV, CORS_ORIGINS_ENV
from causaltwin.intelligence.training import TrainedModels, train_models
from causaltwin.utils.activity_log import ActivityLogEntry, get_activity_logger

DEFAULT_CORS_ORIGINS = "http://localhost:3000"


def create_app(models: TrainedModels | None = None, autostart: bool | None = None) -> FastAPI:
    """Build the app. Models are trained (or loaded from cache) at start-up unless given."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub = StreamHub()
        hub.bind(asyncio.get_running_loop())
        trained = models if models is not None else await asyncio.to_thread(train_models)
        state = AppState(trained, hub)
        start_running = autostart if autostart is not None else os.environ.get(AUTOSTART_ENV, "1") != "0"
        state.live.running = start_running
        await asyncio.to_thread(state.live.engine.warm_up)  # start discovery workers before the first drift
        app.state.causaltwin = state

        def forward(entry: ActivityLogEntry) -> None:
            hub.queue_log(entry.model_dump())

        logger = get_activity_logger()
        logger.subscribe(forward)
        task = asyncio.create_task(state.live.run_forever())
        try:
            yield
        finally:
            task.cancel()
            logger.unsubscribe(forward)

    app = FastAPI(
        title="CausalTwin API",
        version=__version__,
        description="Causal digital twin of a simulated CNC spindle: discovery, what-if, recommendations, "
        "drift detection, and the Correlation Trap benchmark. All live data is simulated.",
        lifespan=lifespan,
    )
    origins = [origin.strip() for origin in os.environ.get(CORS_ORIGINS_ENV, DEFAULT_CORS_ORIGINS).split(",")]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
    )
    app.include_router(router)
    app.include_router(info_router)
    return app


app = create_app()
