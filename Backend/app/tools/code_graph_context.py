from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.code_graph.context import CodeGraphContextResolver


CodeGraphOperation = Literal[
    "search_symbols",
    "file_summary",
    "references",
    "impact",
    "related_tests",
    "entrypoints",
]


class CodeGraphContextInput(BaseModel):
    """定义 Agent 代码图导航工具的有界入参。"""

    operation: CodeGraphOperation = Field(
        description="要执行的导航操作：search_symbols、file_summary、references、impact、related_tests 或 entrypoints。"
    )
    query: str = Field(
        default="",
        max_length=500,
        description="符号名、任务关键词或 workspace-relative 文件路径。",
    )
    paths: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="可选的 workspace-relative 候选文件路径。",
    )
    direction: Literal["both", "incoming", "outgoing", "callers", "callees"] = Field(
        default="both",
        description="references 查询的关系方向。",
    )
    max_results: int = Field(default=20, ge=1, le=40)
    max_depth: int = Field(default=2, ge=0, le=2)


def create_code_graph_context_tool(workspace_root: str | Path | None):
    """创建绑定到一个用户 workspaceRoot 的只读代码图工具。"""

    root = _validated_workspace_root(workspace_root)

    @tool("code_graph_context", args_schema=CodeGraphContextInput)
    def code_graph_context(
        operation: CodeGraphOperation,
        query: str = "",
        paths: list[str] | None = None,
        direction: Literal[
            "both", "incoming", "outgoing", "callers", "callees"
        ] = "both",
        max_results: int = 20,
        max_depth: int = 2,
    ) -> str:
        """查询当前用户工作区的代码图，不读取源码正文或其他目录。"""

        if root is None:
            return json.dumps(
                {
                    "schemaVersion": "xcodeagent.code_graph_context.v1",
                    "status": "skipped",
                    "reason": "no_explicit_workspace",
                    "operation": operation,
                    "message": "没有显式 workspaceRoot，本次不执行代码图扫描。",
                    "fallback": "workspace_search",
                },
                ensure_ascii=False,
            )
        try:
            result = CodeGraphContextResolver().resolve(
                root,
                operation=operation,
                query=query,
                paths=paths,
                direction=direction,
                max_results=max_results,
                max_depth=max_depth,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            result = {
                "schemaVersion": "xcodeagent.code_graph_context.v1",
                "status": "unavailable",
                "operation": operation,
                "message": f"代码图查询不可用：{type(exc).__name__}。",
                "fallback": "workspace_search",
            }
        return json.dumps(result, ensure_ascii=False)

    return code_graph_context


def _validated_workspace_root(workspace_root: str | Path | None) -> Path | None:
    """只接受调用方显式传入且存在的工作区，绝不回退到当前进程目录。"""

    if workspace_root is None or not str(workspace_root).strip():
        return None
    root = Path(workspace_root).expanduser().resolve()
    return root if root.is_dir() else None
