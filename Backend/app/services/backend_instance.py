"""提供当前 Backend 进程的短生命周期身份。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class BackendInstanceIdentity:
    """描述一个 Backend 进程在本地恢复系统中的唯一身份。"""

    instance_id: str
    pid: int
    started_at: datetime


_current_identity: BackendInstanceIdentity | None = None


def create_backend_instance_identity() -> BackendInstanceIdentity:
    """为当前 Backend 进程生成一个新的唯一身份。"""

    return BackendInstanceIdentity(
        instance_id=f"backend-{uuid4().hex}",
        pid=os.getpid(),
        started_at=datetime.now(timezone.utc),
    )


def initialize_backend_instance() -> BackendInstanceIdentity:
    """在 Backend lifespan 开始时登记并返回当前进程身份。"""

    global _current_identity
    _current_identity = create_backend_instance_identity()
    return _current_identity


def current_backend_instance() -> BackendInstanceIdentity:
    """读取当前 Backend 身份；测试或无 lifespan 调用时按需初始化。"""

    global _current_identity
    if _current_identity is None:
        _current_identity = create_backend_instance_identity()
    return _current_identity


__all__ = [
    "BackendInstanceIdentity",
    "create_backend_instance_identity",
    "initialize_backend_instance",
    "current_backend_instance",
]
