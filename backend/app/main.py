"""FastAPI app: GET /api/health, POST /api/plan (docs/api.md)."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .google_routes import GoogleRoutesClient, MockRoutesClient
from .models import PlanRequest, PlanResponse
from .planner import PlanError, TTLCache, plan

logger = logging.getLogger("ihatehighways.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    client = (
        MockRoutesClient()
        if settings.ihh_mock
        else GoogleRoutesClient(settings.google_maps_api_key)
    )
    # ToS: Routes API responses must not be persisted; transient in-memory cache only.
    cache = TTLCache(settings.cache_ttl_s)

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
        return {"ok": True, "mock": settings.ihh_mock}

    @app.post("/api/plan", response_model=PlanResponse)
    async def plan_route(req: PlanRequest) -> PlanResponse:
        key = json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        cached = cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        try:
            result = await plan(req, client, settings)
        except PlanError as exc:
            raise HTTPException(
                status_code=exc.status,
                detail={"code": exc.code, "message": exc.message},
            )
        cache.set(key, result)
        return result

    return app


app = create_app()
