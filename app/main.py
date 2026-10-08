"""FastAPI application, feature routers and resource lifecycle."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.redis import redis_client
from app.db.session import engine, init_db
from app.modules.account.router import router as account_router
from app.modules.agent.health import check_enabled_ai_agents
from app.modules.agent.router import router as agent_router
from app.modules.rag.api.admin_router import router as admin_rag_router
from app.modules.rag.api.router import router as rag_router
from app.modules.task.api.router import router as task_router

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    await asyncio.to_thread(init_db)
    try:
        await asyncio.to_thread(redis_client.ping)
        logger.info("Redis backend ready: %s", redis_client.backend_name)
    except Exception:
        logger.exception("Redis connection failed")
    health_task = asyncio.create_task(check_enabled_ai_agents())
    application.state.ai_health_task = health_task
    try:
        yield
    finally:
        health_task.cancel()
        with suppress(asyncio.CancelledError):
            await health_task
        await asyncio.to_thread(engine.dispose)


app = FastAPI(
    title=settings.app_name,
    description="FastAPI backend for cultural heritage QA collaboration.",
    version=settings.app_version,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)
for router in (account_router, agent_router, task_router, rag_router, admin_rag_router):
    app.include_router(router)


@app.get("/")
async def root():
    return {
        "message": "Cultural Heritage QA Backend",
        "docs": "/docs", "health": "/health",
    }


@app.get("/health")
async def health():
    """Application liveness, not dependency readiness."""
    return {"status": "ok"}
