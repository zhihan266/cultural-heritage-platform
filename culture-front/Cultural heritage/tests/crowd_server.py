"""Disposable HTTP test host: real account/crowd routes, JWT, SQLite, no consumers.

Run using the backend virtualenv, passing its root directory as argument one.
This process never imports the production app lifespan, calls models, or writes MySQL.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])

from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
import uvicorn  # noqa: E402

from app.core.redis import InMemoryRedis  # noqa: E402
from app.db.session import Base, init_db  # noqa: E402
from app.modules.account import dependencies, repository, router, service  # noqa: E402
from app.modules.agent import repository as agents, router as agent_router  # noqa: E402
from app.modules.rag_task.api import crowd_router  # noqa: E402
from app.modules.rag_task.application.facade import CrowdTaskFacade  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix="crowd-browser-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'test.db'}", connect_args={"check_same_thread": False})
        init_db()
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        redis = InMemoryRedis()
        repository.SessionLocal = factory
        agents.SessionLocal = factory
        dependencies.redis_client = service.redis_client = redis
        crowd_router.crowd_task_facade = CrowdTaskFacade(session_factory=factory)
        app = FastAPI()
        for routes in (router.router, agent_router.router, crowd_router.router):
            app.include_router(routes)
        try:
            uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]) if len(sys.argv) > 2 else 8197, log_level="warning")
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
