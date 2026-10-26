import os
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .auth import TokenService
from .db import UserRepository
from .models import Health
from .routers import auth, dashboard, runs, workers
from .store import MemoryStore, Problem


def error_response(status: int, code: str, message: str, details: dict | None = None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error, "request_id": str(uuid4())})


def create_app(store: MemoryStore | None = None, secret: str | None = None,
               database_url: str | None = None) -> FastAPI:
    users = UserRepository(database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if app.state.tokens is None:
            app.state.tokens = TokenService(secret)
        users.migrate()
        try:
            yield
        finally:
            users.close()

    app = FastAPI(title="RunGuard API", version="1.0.0", lifespan=lifespan)
    app.state.users = users
    app.state.store = store if store is not None else MemoryStore(users)
    # Keep module import side-effect free; uvicorn validates the configured secret at startup.
    app.state.tokens = TokenService(secret) if secret is not None else None
    origins = os.environ.get("RUNGUARD_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173")
    app.add_middleware(CORSMiddleware, allow_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(Problem)
    async def problem_handler(_request: Request, exc: Problem):
        return error_response(exc.status, exc.code, exc.message, exc.details)

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
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(workers.router)
    app.include_router(runs.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
