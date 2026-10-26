import os
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .auth import TokenService
from .db import UserRepository
from .models import Health
from .routers import auth, dashboard, runs, workers
from .store import PersistentStore, Problem
from .rate_limit import RateLimiter, RateLimited
from .outbound import allowed_public_hosts
from . import observability


def error_response(status: int, code: str, message: str, details: dict | None = None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error,
                                                    "request_id": observability.request_id() or str(uuid4())})


def create_app(store: PersistentStore | None = None, secret: str | None = None,
               database_url: str | None = None) -> FastAPI:
    users = UserRepository(database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        allowed_public_hosts()
        if app.state.tokens is None:
            app.state.tokens = TokenService(secret)
        users.migrate()
        observability.configure("runguard-api", users.database_url)
        try:
            yield
        finally:
            users.close()

    app = FastAPI(title="RunGuard API", version="1.0.0", lifespan=lifespan)
    app.state.users = users
    app.state.store = store if store is not None else PersistentStore(users)
    app.state.rate_limiter = RateLimiter()
    # Keep module import side-effect free; uvicorn validates the configured secret at startup.
    app.state.tokens = TokenService(secret) if secret is not None else None
    origins = os.environ.get("RUNGUARD_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173")
    app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"],
                       expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def journey_telemetry(request: Request, call_next):
        identifier = str(uuid4())
        bound = observability.bind_request_id(identifier)
        try:
            if request.url.path == "/health":
                response = await call_next(request)
                response.headers["X-Request-ID"] = identifier
                if response.status_code >= 500:
                    observability.emit("HEALTH_FAILED", level="ERROR")
                return response
            with observability.tracer().start_as_current_span("http.request") as span:
                response = await call_next(request)
                route = request.scope.get("route")
                template = getattr(route, "path", "unmatched")
                span.set_attribute("http.request.method", request.method)
                span.set_attribute("http.route", template)
                span.set_attribute("http.response.status_code", response.status_code)
                operation = None
                if request.method == "POST":
                    operation = {
                        "/api/v1/auth/register": "register",
                        "/api/v1/auth/login": "login",
                        "/api/v1/workers": "worker_create",
                        "/api/v1/workers/{worker_id}/runs": "enqueue",
                    }.get(template)
                if operation:
                    observability.record_api_outcome(operation, response.status_code)
                    outcome = "SUCCEEDED" if response.status_code < 400 else "FAILED"
                    observability.emit(f"{operation.upper()}_{outcome}")
                observability.emit("API_REQUEST_COMPLETED",
                                   level="ERROR" if response.status_code >= 500 else "INFO")
                response.headers["X-Request-ID"] = identifier
                return response
        finally:
            observability.reset_request_id(bound)

    @app.exception_handler(Problem)
    async def problem_handler(_request: Request, exc: Problem):
        response = error_response(exc.status, exc.code, exc.message, exc.details)
        if isinstance(exc, RateLimited):
            response.headers["Retry-After"] = str(exc.retry_after)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        status = 400 if all(e["loc"][0] in ("query", "path") for e in exc.errors()) else 422
        return error_response(status, "INVALID_QUERY" if status == 400 else "VALIDATION_ERROR",
                              "Invalid request parameters" if status == 400 else "Invalid request body")

    @app.exception_handler(Exception)
    async def unexpected_handler(_request: Request, _exc: Exception):
        return error_response(500, "INTERNAL_ERROR", "Unexpected server error")

    @app.get("/health", tags=["Health"], response_model=Health)
    def health():
        try:
            with users.sessions() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            raise Problem(503, "NOT_READY", "Database is unavailable") from None
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(workers.router)
    app.include_router(runs.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
