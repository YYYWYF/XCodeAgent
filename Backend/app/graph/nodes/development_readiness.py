"""开发目标进入任务拆解前的 Endpoint 映射门禁入口。"""

from __future__ import annotations

from app.graph.state import ProjectState
from app.graph.nodes.api_design import api_design_readiness_gate


def development_readiness_gate(state: ProjectState) -> dict:
    """统一旧显式入口到 Endpoint 字段映射门禁，不再要求独立实体设计。"""

    return api_design_readiness_gate(state)
