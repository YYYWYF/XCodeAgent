from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main() -> None:
    load_backend_env()

    import uvicorn

    host = os.getenv("XCODEAGENT_BACKEND_HOST", DEFAULT_HOST)
    port = parse_port(os.getenv("XCODEAGENT_BACKEND_PORT"))
    log_level = os.getenv("XCODEAGENT_BACKEND_LOG_LEVEL", "info")

    uvicorn.run("app.main:app", host=host, port=port, log_level=log_level)


def load_backend_env() -> None:
    env_file = resolve_env_file()
    if env_file:
        load_dotenv(env_file, override=False)

    bundled_docs_dir = resolve_bundled_docs_dir()
    if bundled_docs_dir:
        os.environ.setdefault("ANTD_V4_DOCS_DIR", str(bundled_docs_dir))

    from app.services.builtin_skills import validate_required_builtin_skills

    validate_required_builtin_skills()


def resolve_env_file() -> Path | None:
    configured_path = os.getenv("XCODEAGENT_BACKEND_ENV_FILE")
    if configured_path:
        return Path(configured_path).expanduser().resolve()

    default_env_file = executable_dir() / ".env"
    return default_env_file if default_env_file.is_file() else None


def resolve_bundled_docs_dir() -> Path | None:
    candidates = [
        resource_root() / "resources" / "docs" / "antd-v4",
        executable_dir() / "resources" / "docs" / "antd-v4",
        executable_dir() / "_internal" / "resources" / "docs" / "antd-v4",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def resource_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parents[1]


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def parse_port(value: str | None) -> int:
    if not value:
        return DEFAULT_PORT
    try:
        port = int(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid XCODEAGENT_BACKEND_PORT: {value}") from exc
    if port < 1 or port > 65535:
        raise RuntimeError(f"XCODEAGENT_BACKEND_PORT out of range: {port}")
    return port


if __name__ == "__main__":
    main()
