import hmac
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import metrics
from app.config import get_settings
from app.db import SessionLocal, engine, set_system
from app.errors import ApiError
from app.routers import (
    agent,
    analysis,
    auth,
    billing,
    documents,
    health,
    jobs,
    onboarding,
    requirements,
    rfqs,
    suppliers,
    team,
)

settings = get_settings()
logger = logging.getLogger("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title="AI Sourcing Copilot API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "unmatched"  # templates, never raw paths, keep cardinality bounded
        metrics.HTTP_REQUESTS.inc(method=request.method, route=template, status=str(status))
        metrics.HTTP_DURATION.observe(time.perf_counter() - started, method=request.method, route=template)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request):
    """Prometheus scrape endpoint. Needs METRICS_TOKEN as a bearer token; disabled in production without one.
    Not under /api, so the web front end never proxies it."""
    if settings.metrics_token:
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), settings.metrics_token.encode()):
            return _error(401, "unauthorized", "Metrics need a valid bearer token", {"WWW-Authenticate": "Bearer"})
    elif settings.is_production:
        return _error(404, "not_found", "Not Found")
    body = metrics.REGISTRY.render()
    try:
        async with SessionLocal() as db:
            await set_system(db)
            body += await metrics.database_gauges(db)
            await db.rollback()
        body += "# HELP copilot_database_up Whether the database answered the metrics queries.\n# TYPE copilot_database_up gauge\ncopilot_database_up 1\n"
    except Exception:
        logger.exception("metrics database query failed")
        body += "# HELP copilot_database_up Whether the database answered the metrics queries.\n# TYPE copilot_database_up gauge\ncopilot_database_up 0\n"
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


def _error(status: int, code: str, message: str, headers=None, **extra) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, **extra}}, status_code=status, headers=headers
    )


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError):
    return _error(exc.status_code, exc.code, exc.message, exc.headers, **exc.extra)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException):
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, str(exc.detail), getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    details = [
        {"field": ".".join(str(p) for p in e["loc"][1:]), "message": str(e["msg"]).removeprefix("Value error, ")}
        for e in exc.errors()
    ]
    message = details[0]["message"] if details else "Invalid request"
    return _error(422, "validation_error", message, details=details)


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    logger.exception("unhandled error", exc_info=exc)
    return _error(500, "internal_error", "Something went wrong")


app.include_router(health.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(onboarding.router, prefix="/api/v1")
app.include_router(billing.router, prefix="/api/v1")
app.include_router(billing.webhooks, prefix="/api/v1")
app.include_router(team.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(requirements.router, prefix="/api/v1")
app.include_router(suppliers.router, prefix="/api/v1")
app.include_router(rfqs.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")
