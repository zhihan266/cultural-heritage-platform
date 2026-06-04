from fastapi import FastAPI

from app.api.endpoints import ai, user, worker
from config.logging import configure_logging
from config.settings import settings

configure_logging()

app = FastAPI(
    title=settings.app_name,
    description="FastAPI backend for cultural heritage QA collaboration.",
    version=settings.app_version,
)

app.include_router(user.router)
app.include_router(worker.router)
app.include_router(ai.router)


@app.get("/")
async def root():
    return {
        "message": "Cultural Heritage QA Backend",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
