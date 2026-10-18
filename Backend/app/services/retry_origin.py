"""固定一次重试的原始业务失败，避免恢复异常替换节点及用户输入。"""

import hashlib
import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.branding import WORKSPACE_ARTIFACT_DIR


class RetryOrigin(BaseModel):
    """只记录原始失败身份、节点、输入及该恢复机会的消费状态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    thread_id: str
    source_run_id: str
    node: str
    regeneration_node: str = ""
    request: str
    checkpoint_config: dict | None = None
    checkpoint_attempted: bool = False
    node_completed: bool = False


# 完成标记补写期间立即停用旧锚点；不持有执行租约、资源锁或业务 worker。
_pending_completions: dict[Path, RetryOrigin] = {}


def _path(workspace: str, run_id: str) -> Path:
    """使用运行标识摘要定位当前重试记录，不接受标识中的路径片段。"""

    key = hashlib.sha256(run_id.encode()).hexdigest()
    return Path(workspace) / WORKSPACE_ARTIFACT_DIR / "recovery" / "retry-origins" / f"{key}.json"


def load_retry_origin(workspace: str, run_id: str, thread_id: str) -> RetryOrigin | None:
    """读取尚未完成原业务节点的锚点；成功后的其他节点失败按其自身现场处理。"""

    try:
        path = _path(workspace, run_id)
        pending = _pending_completions.get(path)
        if pending is not None and pending.thread_id == thread_id:
            return None
        if not path.is_file():
            return None
        origin = RetryOrigin.model_validate_json(path.read_text())
        if origin.thread_id != thread_id:
            raise ValueError("重试原始现场与当前 Thread 不一致。")
    except (OSError, ValueError):
        # 辅助记录不能阻断原 checkpoint；不使用损坏内容或其他 Thread 的节点与输入。
        logging.getLogger(__name__).warning("原始重试记录不可用，改从原执行 checkpoint 定位入口。")
        return None
    return None if origin.node_completed else origin


def save_retry_origin(workspace: str, run_id: str, origin: RetryOrigin) -> None:
    """原子记录原始现场及已消费的恢复机会，进程重启后也不重复恢复新失败。"""

    path = _path(workspace, run_id)
    pending = _pending_completions.get(path)
    if pending is not None and (pending.source_run_id, pending.thread_id, pending.node) == (
            origin.source_run_id, origin.thread_id, origin.node):
        origin = pending
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(origin.model_dump(mode="json"), ensure_ascii=False))
    os.replace(temporary, path)
    # 新来源写入成功即取代旧补写责任；延迟任务不能覆盖后续节点的新失败锚点。
    if pending is not None and _pending_completions.get(path) is pending:
        _pending_completions.pop(path)


async def complete_retry_origin(workspace: str, run_id: str, origin: RetryOrigin) -> None:
    """业务成功立即解除旧锚点，完成标记的存储失败由现有后台补写机制承担。"""
    from app.services.execution_cleanup import retry_cleanup_persistence
    from app.services.failed_run_cleanup import shield_run_cleanup
    path = _path(workspace, run_id)
    if path in _pending_completions:
        return
    completed = origin.model_copy(update={"node_completed": True})
    _pending_completions[path] = completed

    async def persist() -> None:
        """只补写仍属于本次原节点的标记，后续新失败登记后自动停止旧补写。"""
        if _pending_completions.get(path) is completed:
            save_retry_origin(workspace, run_id, completed)

    # 用户取消下游或连接结束也不丢掉补写责任，管理任务仍由既有清理任务集合持有。
    await shield_run_cleanup(retry_cleanup_persistence(persist))
