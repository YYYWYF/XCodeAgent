from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.code_graph.manager import CodeGraphManager, get_code_graph_manager
from app.services.code_graph.models import CodeGraphQuery


class CodeGraphContextResolver:
    """把代码图查询结果裁剪为执行 Agent 可消费的 bounded context。"""

    def __init__(self, manager: CodeGraphManager | None = None) -> None:
        """绑定指定管理器，默认使用后端进程级管理器。"""

        self.manager = manager or get_code_graph_manager()

    def resolve(
        self,
        workspace_root: Path,
        *,
        operation: str,
        query: str = "",
        paths: list[str] | None = None,
        direction: str = "both",
        max_results: int = 20,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """执行一次受限查询并返回稳定的 JSON 结构。"""

        request = CodeGraphQuery(
            operation=operation,
            query=str(query or "")[:500],
            paths=tuple(str(item)[:500] for item in (paths or [])[:20]),
            direction=direction,
            max_results=max(1, min(max_results, 40)),
            max_depth=max(0, min(max_depth, 2)),
        )
        return self.manager.query(workspace_root, request).as_dict()
