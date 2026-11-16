"""请求准备只做可用产物投影；产物错误交给已持久化的阶段入口统一收口。"""

from collections.abc import Callable
from typing import Any

from app.services.managed_database_metadata import ManagedMetadataResourceBusyError
from app.services.stage_prerequisites import regeneration_for_retry


def pregraph_artifact_values(workspace: str, node: str, read: Callable[[], dict[str, Any]], *, state: dict | None = None) -> dict[str, Any]:
    """必要产物不可用时延后到节点读取，避免在身份、原提问进入 checkpoint 前抛错。"""
    try:
        issue = regeneration_for_retry(workspace, node, state=state) if workspace else None
        if issue:
            # 正式节点会在调用 Agent 前重新读取并检查磁盘输入。
            return {}
        try:
            return read()
        except (OSError, ValueError):
            # 文件可能被并发替换；只延后能定位到生成节点的产物错误。
            if workspace and regeneration_for_retry(workspace, node, state=state):
                return {}
            raise
    except ManagedMetadataResourceBusyError:
        # 入图后用原请求和完整 Run 身份重新检查并记录结果。
        return {}
