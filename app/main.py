"""Application entry point.

Run:  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import api, auth, routes
from app.config import BASE_DIR, COOKIE_SECURE, SIM_INTERVAL, SIMULATION
from app.database import SessionLocal, init_db
from app.simulator import PlantSim, run_simulator

logger = logging.getLogger("pipeline_leak_alert")


@asynccontextmanager
async def lifespan(app_: FastAPI):
    init_db()
    with SessionLocal() as db:
        auth.ensure_admin(db)
    app_.state.sim = PlantSim()
    task = asyncio.create_task(run_simulator(app_.state.sim, SIM_INTERVAL)) if SIMULATION else None
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(
    title="Pipeline Leak Alert System",
    description="Pipeline monitoring and control: IoT ingestion, leak detection, alerts, equipment control.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(routes.router)
app.include_router(api.router)

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store" if request.url.path in ("/login", "/logout") or request.url.path.startswith("/api/") else response.headers.get("Cache-Control", "")
    if COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path not in DOCS_PATHS:  # Swagger UI needs inline scripts/CDN
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; form-action 'self'"
        )
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Log details on the server; never show a traceback to the user."""
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
    return PlainTextResponse("Internal server error", status_code=500)
