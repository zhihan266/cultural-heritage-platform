"""FastAPI 应用入口。

负责创建应用、配置跨域、注册路由、初始化数据库，并在后台启动 AI 健康检查。
"""

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.endpoints import ai, tasks, user, worker
from app.db.session import init_db
from app.services.ai_health import check_enabled_ai_agents
from config.logging import configure_logging
from config.settings import settings
from app.core.redis import redis_client

configure_logging()

# 应用名称和版本统一从配置模块读取。
app = FastAPI(
    title=settings.app_name,
    description="FastAPI backend for cultural heritage QA collaboration.",
    version=settings.app_version,
)

# 允许配置中的前端地址跨域调用后端接口。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 按用户、工人和 AI 三类业务注册路由。
app.include_router(user.router)
app.include_router(worker.router)
app.include_router(ai.router)
app.include_router(tasks.router)


@app.on_event("startup")
async def startup() -> None:
    """初始化数据库，并创建不阻塞启动流程的 AI 健康检查任务。"""
    init_db()
    # create_task 只负责调度，健康检查在事件循环后台执行。
    app.state.ai_health_task = asyncio.create_task(check_enabled_ai_agents())


@app.get("/")
async def root():
    """返回服务名称和常用入口。"""
    return {
        "message": "Cultural Heritage QA Backend",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health():
    """提供后端自身的存活检测接口。"""
    return {"status": "ok"}

@app.on_event("startup")
def startup_event():
    try:
        redis_client.ping()
        print("✅ Redis connected successfully")
    except Exception as e:
        print("❌ Redis connection failed:", e)