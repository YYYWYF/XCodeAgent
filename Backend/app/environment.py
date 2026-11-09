"""源码和冻结后端共用的环境选择及配置加载入口。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values

ENVIRONMENT_DIRECTORIES: dict[str, str] = {
    "dev": ".devagentstudio_dev",
    "st": ".devagentstudio_st",
    "uat": ".devagentstudio_uat",
    "prd": ".devagentstudio",
}
STARTUP_VARIABLES: frozenset[str] = frozenset({
    "APP_ENV", "DEVAGENTSTUDIO_BACKEND_ENV_FILE", "DEVAGENTSTUDIO_BACKEND_HOST",
    "DEVAGENTSTUDIO_BACKEND_PORT", "DEVAGENTSTUDIO_WORKING_DIR", "PYTHONUTF8",
    "DEVAGENTSTUDIO_BACKEND_CONTROL_TOKEN",
})


@dataclass(frozen=True)
class BackendEnvironment:
    """保存本进程实际选择的环境和配置路径，不携带配置值。"""

    app_env: str
    env_file: Path


def resolve_environment() -> BackendEnvironment:
    """按启动环境定位唯一文件，不搜索父目录或回退其他环境。"""
    app_env = os.environ.get("APP_ENV", "dev")
    if app_env not in ENVIRONMENT_DIRECTORIES:
        raise RuntimeError("APP_ENV must be dev, st, uat or prd.")
    configured_path = os.environ.get("DEVAGENTSTUDIO_BACKEND_ENV_FILE")
    root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1]
    )
    filename = ".env" if app_env == "dev" else f".env.{app_env}"
    env_file = (
        Path(configured_path).expanduser().resolve()
        if configured_path else root / filename
    )
    if not env_file.is_file():
        raise RuntimeError(f"Missing backend configuration for {app_env}: {env_file}")
    return BackendEnvironment(app_env=app_env, env_file=env_file)


@lru_cache(maxsize=1)
def load_backend_environment() -> BackendEnvironment:
    """一次性加载业务配置，文件优先，启动控制变量始终由启动链路决定。"""
    environment = resolve_environment()
    # 解析不修改进程环境；过滤控制项后才写入，避免端口和环境身份被文件覆盖。
    values = dotenv_values(environment.env_file, encoding="utf-8-sig")
    for key, value in values.items():
        if key.upper() not in STARTUP_VARIABLES and value is not None:
            os.environ[key] = value
    os.environ["APP_ENV"] = environment.app_env
    os.environ["DEVAGENTSTUDIO_BACKEND_ENV_FILE"] = str(environment.env_file)
    os.environ["DEVAGENTSTUDIO_WORKING_DIR"] = ENVIRONMENT_DIRECTORIES[environment.app_env]
    return environment


if __name__ == "__main__":
    # 启动脚本在 Uvicorn reload 监控器之前执行此检查，缺文件时直接退出。
    selected = load_backend_environment()
    print(f"Backend configuration ready: {selected.app_env}")
