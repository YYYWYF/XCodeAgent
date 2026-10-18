"""请求准备只做可用产物投影；产物错误交给已持久化的阶段入口统一收口。"""

from collections.abc import Callable
from typing import Any

from app.services.stage_prerequisites import regeneration_for_retry


def pregraph_artifact_values(workspace: str, node: str, read: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """必要产物不可用时延后到节点读取，避免在身份、原提问进入 checkpoint 前抛错。"""
    issue = regeneration_for_retry(workspace, node) if workspace else None
    if issue:
        # 这里不把坏文件解释为空资产供 Agent 使用；正式节点 wrapper 会在 Agent 前
        # 再读磁盘并抛结构化前置提示，由原失败、清理和重试协议发布重新生成入口。
        return {}
    try:
        return read()
    except (OSError, ValueError):
        # 请求投影后文件可能被并发替换；只延后已能定位生成节点的产物错误。
        issue = regeneration_for_retry(workspace, node) if workspace else None
        if issue:
            return {}
        raise
