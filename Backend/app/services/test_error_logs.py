"""保存失败检查的原始输出，供测试卡片按需复制。"""

import logging
from pathlib import Path
from typing import Any

from app.workspace.content_artifacts import put_artifact

_LOGGER = logging.getLogger(__name__)
# JSON 转义后仍低于现有资料接口的单次读取上限。
_CHUNK_CHARS = 1024 * 1024


def record_test_error_log(
    workspace: Path, runtime_root: Path, check: dict[str, Any], *, evidence: str | None = None,
) -> None:
    """在下一次执行覆盖日志前保存不可变副本；记录失败不改变检查结果。"""

    execution = check.get("execution") or {}
    try:
        reason = evidence or str(check.get("evidence") or "")
        details = [str(check.get("name") or check["id"]), reason]
        if check.get("command"):
            details.append(f"命令：{check['command']}")
        error = execution.get("error")
        if isinstance(error, str) and error and error not in reason:
            details.append(f"错误：{error}")
        header = "\n".join(details)
        references = [
            put_artifact(workspace, header[offset:offset + _CHUNK_CHARS])
            for offset in range(0, len(header), _CHUNK_CHARS)
        ]
        for stream in ("stdout", "stderr"):
            relative = execution.get(f"{stream}_log_virtual")
            if not relative:
                continue
            path = (workspace / relative.lstrip("/")).resolve()
            if not path.is_relative_to(workspace) or not path.is_relative_to(runtime_root.resolve()):
                raise ValueError("检查日志不在测试运行目录中。")
            with path.open(encoding="utf-8", errors="replace", newline="") as log:
                heading = f"\n\n--- {stream} ---\n"
                while chunk := log.read(_CHUNK_CHARS):
                    references.append(put_artifact(workspace, heading + chunk))
                    heading = ""
        check["errorLogReferences"] = references
    except (OSError, ValueError) as exc:
        _LOGGER.warning("无法保存测试检查 %s 的完整错误日志（%s）", check.get("id"), type(exc).__name__)
        check["errorLogReferences"] = None
