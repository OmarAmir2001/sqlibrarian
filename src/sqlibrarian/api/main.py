"""The SQLibrarian API.

    uv run uvicorn sqlibrarian.api.main:app --port 8000

Needs both databases (READER_DB_URL, APP_DB_URL), JWT_SECRET, and a model server at
MODEL_SERVER_URL (llama.cpp `llama-server` or vLLM, see README).

Routes:
    GET  /health        is the API up, and can it reach both databases?
    POST /auth/login    username + password -> token
    POST /ask           a question -> answer, the SQL that ran, the rows
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from psycopg_pool import AsyncConnectionPool

from sqlibrarian.api.generator import Generator
from sqlibrarian.api.identity import CurrentUser, authenticate, create_token
from sqlibrarian.api.orchestrator import Orchestrator
from sqlibrarian.api.schemas import AskRequest, AskResponse, LoginRequest, TokenResponse
from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.log import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    s = get_settings()
    reader = AsyncConnectionPool(s.require("reader_db_url"), open=False, min_size=1, max_size=10)
    app_db = AsyncConnectionPool(s.require("app_db_url"), open=False, min_size=1, max_size=5)
    await reader.open()
    await app_db.open()
    generator = getattr(app.state, "generator", None) or Generator()
    app.state.app_pool = app_db
    app.state.orchestrator = Orchestrator(generator, reader, app_db)
    log.info("api.started", model_server=s.model_server_url)
    try:
        yield
    finally:
        await generator.aclose()
        await reader.close()
        await app_db.close()


app = FastAPI(title="SQLibrarian", lifespan=lifespan)


@app.get("/health")
async def health(request: Request) -> dict:
    checks = {}
    for name, pool in (
        ("records_db", request.app.state.orchestrator.reader_pool),
        ("app_db", request.app.state.app_pool),
    ):
        try:
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
            checks[name] = "ok"
        except Exception as e:  # health must report, never crash
            checks[name] = f"down: {type(e).__name__}"
    return {"status": "ok" if all(v == "ok" for v in checks.values()) else "degraded", **checks}


@app.post("/auth/login")
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    identity = await authenticate(request.app.state.app_pool, body.username, body.password)
    return TokenResponse(
        access_token=create_token(identity),
        role=identity.role,
        expires_in_minutes=get_settings().jwt_ttl_minutes,
    )


@app.post("/ask")
async def ask(body: AskRequest, request: Request, identity: CurrentUser) -> AskResponse:
    result = await request.app.state.orchestrator.ask(body.question, identity)
    return AskResponse(
        answer=result.answer, sql=result.sql, columns=result.columns, rows=result.rows,
        outcome=result.outcome, timings_ms=result.timings_ms,
    )  # fmt: skip
