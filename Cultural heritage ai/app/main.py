"""FastAPI application, feature routers and resource lifecycle."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.redis import redis_client
from app.api.task_routes import router as task_compat_router
from app.db.session import engine, init_db
from app.modules.account.router import router as account_router
from app.modules.agent.health import check_enabled_ai_agents
from app.modules.agent.router import router as agent_router
from app.modules.rag.api.admin_router import router as admin_rag_router
from app.modules.rag.api.router import router as rag_router
from app.modules.rag_task.api.router import router as task_router
from app.modules.rag_task.application.facade import crowd_task_facade
from app.modules.hcp_mad.api.router import router as hcp_mad_router
from app.modules.hcp_mad.application.facade import hcp_mad_facade
from app.modules.hcp_mad.application.task.facade import TaskMode, task_facade

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
    health_task = None
    crowd_consumer = None
    if settings.rag_task_consumer_enabled:
        if not (settings.rag_task_aggregator_ai_id or "").strip():
            logger.info("RAG Task aggregation policy: unbound jobs select an enabled usable Judge "
                        "via AgentFacade.ranked_agents; RAG_TASK_AGGREGATOR_AI_ID is an optional override.")
        crowd_consumer = crowd_task_facade.consumer()
        application.state.rag_task_consumer = crowd_consumer
        crowd_consumer.start()
    else:
        logger.warning("RAG Task consumer disabled: pending aggregation/ingestion jobs will not run "
                       "in this instance. Set RAG_TASK_CONSUMER_ENABLED=true to enable it.")
    if settings.ai_health_check_on_startup and not getattr(
        application.state, "ai_health_check_started", False,
    ):
        # 标记在创建任务前设置；任务结束或取消后都不自动重试。
        application.state.ai_health_check_started = True
        health_task = asyncio.create_task(check_enabled_ai_agents())
        application.state.ai_health_task = health_task
    try:
        yield
    finally:
        if crowd_consumer is not None:
            await crowd_consumer.stop()
        if health_task is not None:
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
task_facade.register_workflow(TaskMode.HCP_MAD, hcp_mad_facade.run_claimed_task)
for router in (account_router, agent_router, task_router, task_compat_router, rag_router, admin_rag_router, hcp_mad_router):
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
