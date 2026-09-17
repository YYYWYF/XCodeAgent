"""平台确定性 Build Task 执行器及显式注册表。"""

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from app.domain.models import DETERMINISTIC_PLATFORM_EXECUTOR_ALLOWLIST

from app.services.platform_task_executors.authorization_frontend_resources import (
    EXECUTOR_NAME as AUTHORIZATION_FRONTEND_RESOURCES_EXECUTOR,
    execute_authorization_frontend_resources,
)


PlatformTaskExecutor = Callable[
    [Mapping[str, Any], Mapping[str, Any]],
    dict[str, Any],
]


class PlatformTaskExecutorRegistryError(ValueError):
    """表示 deterministic Task 引用的执行器没有注册。"""


_PLATFORM_TASK_EXECUTORS: Mapping[str, PlatformTaskExecutor] = MappingProxyType(
    {
        AUTHORIZATION_FRONTEND_RESOURCES_EXECUTOR: (
            execute_authorization_frontend_resources
        ),
    }
)

if frozenset(_PLATFORM_TASK_EXECUTORS) != DETERMINISTIC_PLATFORM_EXECUTOR_ALLOWLIST:
    raise RuntimeError("Deterministic executor allowlist 与 platform executor registry 不一致。")


def resolve_platform_task_executor(name: str) -> PlatformTaskExecutor:
    """按精确名称读取确定性执行器，未知名称显式失败且不得回退 Agent。"""

    executor = _PLATFORM_TASK_EXECUTORS.get(name)
    if executor is None:
        raise PlatformTaskExecutorRegistryError(
            f"Platform task executor is not registered: {name or '<empty>'}."
        )
    return executor


__all__ = [
    "PlatformTaskExecutor",
    "PlatformTaskExecutorRegistryError",
    "execute_authorization_frontend_resources",
    "resolve_platform_task_executor",
]
