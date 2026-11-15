"""FastAPI application factory.

Run:  uvicorn causaltwin.api.app:app --port 8000   (from the backend folder)
Docs: http://localhost:8000/docs
Deployment settings (CORS origins, autostart, discovery workers, benchmark runs, private
simulations) come from environment variables; see backend/.env.example and causaltwin/api/runtime.py.
Each browser tab gets its own live simulation; see causaltwin/api/sessions.py.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from causaltwin import __version__
from causaltwin.api.info_routes import router as info_router
from causaltwin.api.routes import router
from causaltwin.api.runtime import RuntimeSettings
from causaltwin.api.sessions import requested_session_id
from causaltwin.api.state import AppState
from causaltwin.config import BYOD_MAX_UPLOAD_BYTES
from causaltwin.intelligence.training import TrainedModels, train_models
from causaltwin.utils.activity_log import get_activity_logger, set_log_session


def _readable_validation_message(error: RequestValidationError) -> str:
    """One sentence listing each invalid field, for example "body.horizon: must be <= 120"."""
    parts = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
        parts.append(f"{location or 'request'}: {item.get('msg', 'invalid value')}")
    return "Invalid request - " + "; ".join(parts)


async def _validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    """422 with a readable `detail` string (the UI shows it) plus the structured errors.

    The rejected input values are not echoed back: they may not even be valid JSON (NaN).
    """
    assert isinstance(error, RequestValidationError)
    errors = [
        {"loc": list(item.get("loc", ())), "msg": item.get("msg", ""), "type": item.get("type", "")}
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": _readable_validation_message(error), "errors": jsonable_encoder(errors)},
    )


async def _catch_unexpected_errors(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Turn any unexpected exception into a JSON 500 with a clear message, and log it.

    This runs inside the CORS middleware, so the browser can read the error message.
    """
    try:
        return await call_next(request)
    except Exception as error:  # noqa: BLE001 - last line of defence for every route
        summary = f"{request.method} {request.url.path} failed: {type(error).__name__}: {error}"
        get_activity_logger().log("api_error", summary, level="error")
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"Internal error while handling {request.method} {request.url.path} "
                f"({type(error).__name__}). It was written to the activity log; please retry.",
                "error": type(error).__name__,
            },
        )


UPLOAD_PATH = "/byod/upload"
UPLOAD_FORM_OVERHEAD_BYTES = 64 * 1024  # multipart boundaries and the roles field


async def _reject_oversized_uploads(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Refuse a too-large upload from its Content-Length header, before the body is received."""
    if request.url.path == UPLOAD_PATH:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > BYOD_MAX_UPLOAD_BYTES + UPLOAD_FORM_OVERHEAD_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": f"The file is larger than {BYOD_MAX_UPLOAD_BYTES // (1024 * 1024)} MB."},
            )
    return await call_next(request)


async def _attach_session(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Find (or create) the viewer's live simulation and attach it to the request.

    The routes read it with `sessions.session_of(request)`. Activity log entries written while
    handling the request are tagged with the session, so each viewer's console shows its own.
    """
    state: AppState = request.app.state.causaltwin
    state.requests_served += 1
    requested = requested_session_id(request)
    session = await state.sessions.resolve(requested)
    request.state.session = session
    request.state.requested_session = requested
    set_log_session(session.session_id)
    return await call_next(request)


def create_app(
    models: TrainedModels | None = None, autostart: bool | None = None, runtime: RuntimeSettings | None = None
) -> FastAPI:
    """Build the app. Models are trained (or loaded from cache) at start-up unless given."""
    settings = runtime or RuntimeSettings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        trained = models if models is not None else await asyncio.to_thread(train_models)
        # The worker count is a deployment choice (memory), not part of the cached model.
        trained.engine.settings.discovery_n_jobs = settings.discovery_jobs
        state = AppState(trained, settings)
        state.sessions.bind(asyncio.get_running_loop())
        # Autostart applies to the shared simulation; a private one runs while its viewer is connected.
        state.live.running = autostart if autostart is not None else settings.autostart
        await asyncio.to_thread(state.live.engine.warm_up)  # start discovery workers before the first drift
        app.state.causaltwin = state

        logger = get_activity_logger()
        logger.subscribe(state.sessions.route_log)  # each entry goes to its session's viewers
        state.sessions.start()
        try:
            yield
        finally:
            state.sessions.stop()
            logger.unsubscribe(state.sessions.route_log)

    app = FastAPI(
        title="CausalTwin API",
        version=__version__,
        description="Causal digital twin of a simulated CNC spindle: discovery, what-if, recommendations, "
        "drift detection, and the Correlation Trap benchmark. All live data is simulated.",
        lifespan=lifespan,
    )
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    # Innermost: the session is attached last, right before the route runs.
    app.add_middleware(BaseHTTPMiddleware, dispatch=_attach_session)
    # Added early, so it sits inside CORS: error responses still carry the CORS headers.
    app.add_middleware(BaseHTTPMiddleware, dispatch=_catch_unexpected_errors)
    app.add_middleware(BaseHTTPMiddleware, dispatch=_reject_oversized_uploads)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(info_router)
    return app


app = create_app()
