"""FastAPI entrypoint with structured JSON logging, trace_id propagation, and UI."""

import json
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.api.routes import router as api_router
from aurora.db.seed import seed_database
from aurora.db.session import AsyncSessionLocal, get_db_session
from aurora.domain.errors import DomainError
from aurora.rag.retriever import ingest_policy_corpus

logger = logging.getLogger("aurora.gateway")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
STATIC_INDEX = STATIC_DIR / "index.html"
STATIC_CALL = STATIC_DIR / "call.html"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Ensure baseline seed data and policy corpus are initialized on startup."""
    async with AsyncSessionLocal() as session:
        await seed_database(session)
        await ingest_policy_corpus(session)
    yield


app = FastAPI(
    title="Aurora AI Hotel Receptionist",
    version="0.1.0",
    description="Deterministic PostgreSQL Booking Core + LangGraph Receptionist + Voice & Staff Console",
    lifespan=lifespan,
)


@app.middleware("http")
async def trace_and_structured_logging_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or uuid4().hex
    request.state.trace_id = trace_id
    t0 = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    response.headers["X-Trace-Id"] = trace_id
    logger.info(
        json.dumps(
            {
                "event": "http_request",
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            }
        )
    )
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", uuid4().hex)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
            "trace_id": trace_id,
        },
        headers={"X-Trace-Id": trace_id},
    )


@app.get("/health")
async def health_check(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": "connected",
        "trace_id": getattr(request.state, "trace_id", ""),
    }


@app.get("/", include_in_schema=False)
async def serve_console() -> FileResponse:
    return FileResponse(STATIC_INDEX)


@app.get("/call", include_in_schema=False)
async def serve_phone_call_dialer() -> FileResponse:
    return FileResponse(STATIC_CALL)


app.include_router(api_router)
