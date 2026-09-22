from __future__ import annotations

import os
import subprocess
from typing import Any


WINDOWS_CREATE_NEW_PROCESS_GROUP = int(
    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
)
WINDOWS_CREATE_NO_WINDOW = int(
    getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
)


def preview_process_creation_options(platform_name: str | None = None) -> dict[str, Any]:
    """为长期预览服务构造跨平台且不污染宿主控制台的启动参数。"""

    current_platform = platform_name or os.name
    if current_platform == "nt":
        # Windows 的 start_new_session 参数不会创建进程组；显式隔离控制台，
        # 防止 pnpm、Node 或 Java 的控制信号传播到 DevAgent Studio 后端。
        return {
            "creationflags": (
                WINDOWS_CREATE_NEW_PROCESS_GROUP | WINDOWS_CREATE_NO_WINDOW
            )
        }
    # macOS/POSIX 保留现有进程组继承语义，应用退出时仍可统一回收。
    return {}
