"""Resolve the existing backend and load its real configuration without a server."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "Cultural heritage ai"


def load_backend():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    # The command may be invoked from the workspace root or the backend directory.
    import os
    previous = Path.cwd()
    try:
        os.chdir(BACKEND)
        from app.core.config import Settings, get_env_file

        return Settings(_env_file=get_env_file())
    finally:
        os.chdir(previous)


def require_loopback(host: str | None) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("This verification operation accepts loopback destinations only")
