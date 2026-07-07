"""FastAPI app: GET /api/health, POST /api/plan (docs/api.md)."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings
from .google_routes import GoogleRoutesClient, MockRoutesClient
from .models import (
    PlanRequest,
    PlanResponse,
    RideTokenRequest,
    RideTokenResponse,
    ScoutRequest,
    ScoutResponse,
)
from .planner import PlanError, TTLCache, plan, scout, scout_events
from .ridetoken import build_ride_token
from .ratelimit import RateLimiter

logger = logging.getLogger("ihatehighways.main")

# uvicorn only configures its own loggers; without a root handler the app's INFO lines
# (Overpass mirror failures, OSM gating decisions) are invisible in production logs.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    client = (
        MockRoutesClient()
        if settings.ihh_mock
        else GoogleRoutesClient(settings.google_maps_api_key)
    )
    # ToS: Routes API responses must not be persisted; transient in-memory cache only.
    cache = TTLCache(settings.cache_ttl_s)
    limiter = RateLimiter(settings.rate_per_ip_hour, settings.rate_daily_cap)

    def client_ip(request: Request) -> str:
        if settings.trust_forwarded_for:
            forwarded = request.headers.get("x-forwarded-for", "")
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        return request.client.host if request.client else "unknown"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(title="ihatehighways", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.ihh_cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_input_handler(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = ".".join(str(part) for part in first.get("loc", []) if part != "body")
            message = f"{loc}: {first.get('msg', 'invalid input')}" if loc else str(
                first.get("msg", "invalid input")
            )
        else:
            message = "Invalid input."
        return JSONResponse(
            status_code=400,
            content={"detail": {"code": "INVALID_INPUT", "message": message}},
        )

    @app.get("/api/health")
    async def health() -> dict:
        # key_configured guards against deploys that silently drop the secret (an
        # assets-only misdeploy once wiped it and health kept reporting ok).
        return {
            "ok": True,
            "mock": settings.ihh_mock,
            "key_configured": bool(settings.google_maps_api_key) or settings.ihh_mock,
        }

    def check_rate_limit(request: Request) -> None:
        # Cached hits are free; only uncached plans (which cost Google calls) count.
        denied = limiter.check(client_ip(request))
        if denied == "RATE_LIMITED":
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "RATE_LIMITED",
                    "message": "Too many plans from this address. Try again in a bit.",
                },
            )
        if denied == "DAILY_CAP":
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "DAILY_CAP",
                    "message": "Daily planning budget is used up. Back tomorrow.",
                },
            )

    @app.post("/api/plan", response_model=PlanResponse)
    async def plan_route(req: PlanRequest, request: Request) -> PlanResponse:
        key = json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        cached = cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        check_rate_limit(request)
        try:
            result = await plan(req, client, settings)
        except PlanError as exc:
            raise HTTPException(
                status_code=exc.status,
                detail={"code": exc.code, "message": exc.message},
            )
        cache.set(key, result)
        return result

    @app.post("/api/scout", response_model=ScoutResponse)
    async def scout_route(req: ScoutRequest, request: Request) -> ScoutResponse:
        key = "scout:" + json.dumps(
            req.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        cached = cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        check_rate_limit(request)
        try:
            result = await scout(req, client, settings)
        except PlanError as exc:
            raise HTTPException(
                status_code=exc.status,
                detail={"code": exc.code, "message": exc.message},
            )
        cache.set(key, result)
        return result

    @app.post("/api/scout/stream")
    async def scout_route_stream(req: ScoutRequest, request: Request) -> StreamingResponse:
        """NDJSON progress stream: route -> corridors -> scored* -> probing -> cut* ->
        done|error. Shares cache and rate limits with /api/scout."""
        key = "scout:" + json.dumps(
            req.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        cached = cache.get(key)
        if cached is not None:

            async def replay():
                payload = cached.model_dump(mode="json")  # type: ignore[union-attr]
                yield json.dumps({"type": "done", "scout": payload}) + "\n"

            return StreamingResponse(
                replay(), media_type="application/x-ndjson", headers=headers
            )
        check_rate_limit(request)

        async def stream():
            async for event in scout_events(req, client, settings):
                if event["type"] == "done":
                    cache.set(key, ScoutResponse.model_validate(event["scout"]))
                yield json.dumps(event, separators=(",", ":")) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers=headers)

    @app.post("/api/ride-token", response_model=RideTokenResponse)
    async def ride_token(req: RideTokenRequest, request: Request) -> RideTokenResponse:
        # 1-3 paid Google calls, and tokens must be fresh (Google: use within
        # minutes) — rate limited, NEVER cached.
        check_rate_limit(request)
        try:
            return await build_ride_token(req, client, settings)
        except PlanError as exc:
            raise HTTPException(
                status_code=exc.status,
                detail={"code": exc.code, "message": exc.message},
            )

    return app


app = create_app()
