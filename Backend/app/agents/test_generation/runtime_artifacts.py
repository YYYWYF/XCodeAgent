"""单测生成快照中由平台预览模块独立管理的瞬态文件。"""

import re


# 与前后端启动器及 preview_runtime_state 的当前文件名保持一致，不放行整个 runtime 目录。
_PREVIEW_LAUNCH_FILES = frozenset({
    "frontend.pid",
    "backend.pid",
    "preview-runtime.json",
    *(
        f"{command}.{stream}.log"
        for command in ("install", "frontend", "backend-build", "backend-repackage", "backend")
        for stream in ("stdout", "stderr")
    ),
})
_PREVIEW_MAINTENANCE_PATHS = frozenset({
    "runtime/preview-maintenance.json",
    "runtime/preview-maintenance.tmp",
})


def is_preview_runtime_artifact(relative_path: str) -> bool:
    """识别工件根目录下的精确预览运行路径，避免把并行预览写入归因给 Agent。"""

    if relative_path in _PREVIEW_MAINTENANCE_PATHS:
        return True
    prefix = "runtime/launch/"
    if not relative_path.startswith(prefix):
        return False
    filename = relative_path[len(prefix):]
    # write_record 使用 UUID 命名的同目录临时文件进行原子替换，快照可能看到中间态。
    return filename in _PREVIEW_LAUNCH_FILES or re.fullmatch(
        r"\.preview-[0-9a-f]{32}\.tmp", filename,
    ) is not None
