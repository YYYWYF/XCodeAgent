from __future__ import annotations

import os
import sys
from app.environment import load_backend_environment


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main() -> None:
    """尽早安装日志采集，再启动冻结的 Python HTTP 服务。"""
    from app.services.code_graph.parser_probe import dispatch_parser_probe

    dispatch_parser_probe(sys.argv[1:])
    # 后端只接受无参数启动及专用探测模式，禁止解释器参数意外启动第二个服务。
    if sys.argv[1:]:
        raise SystemExit(2)
    load_backend_env()

    import uvicorn

    host = os.getenv("DEVAGENTSTUDIO_BACKEND_HOST", DEFAULT_HOST)
    port = parse_port(os.getenv("DEVAGENTSTUDIO_BACKEND_PORT"))
    log_level = os.getenv("DEVAGENTSTUDIO_BACKEND_LOG_LEVEL", "info")

    uvicorn.run("app.main:app", host=host, port=port, log_level=log_level)


def load_backend_env() -> None:
    """加载后端配置并在启动前校验冻结资源完整性。"""

    load_backend_environment()

    from app.observability.backend_logs import install_backend_log_capture

    install_backend_log_capture()

    from app.services.builtin_skills import validate_required_builtin_skills
    from app.services.page_templates import validate_page_templates

    validate_required_builtin_skills()
    validate_page_templates()


def parse_port(value: str | None) -> int:
    """校验 Electron 分配或命令行传入的监听端口。"""
    if not value:
        return DEFAULT_PORT
    try:
        port = int(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid DEVAGENTSTUDIO_BACKEND_PORT: {value}") from exc
    if port < 1 or port > 65535:
        raise RuntimeError(f"DEVAGENTSTUDIO_BACKEND_PORT out of range: {port}")
    return port


if __name__ == "__main__":
    main()
