"""由 Build DAG 调度的模板 Route Projection 确定性执行器。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.services.template_route_projector import (
    TemplateRouteProjectorError,
    apply_template_route_projection,
    build_route_projector_input,
)


EXECUTOR_NAME = "template.route_projection"


def execute_template_route_projection(task: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    """使用当前 Build Run 绑定的正式输入调用模板路由投影，不读取可变规划文件。"""

    if task.get("id") != "platform_route_projection" or task.get("execution_strategy") != "deterministic" or task.get("platform_executor") != EXECUTOR_NAME:
        return _failure(task, "invalid_task_contract", "Route Projection Task 合同无效。")
    workspace = context.get("workspace")
    product_plan = context.get("product_plan")
    formal_plan = context.get("formal_plan")
    if not isinstance(workspace, (str, Path)) or not isinstance(product_plan, Mapping) or not isinstance(formal_plan, Mapping):
        return _failure(task, "invalid_execution_context", "Route Projection 缺少冻结的 workspace、ProductPlan 或 TechnicalPlan。")
    try:
        route_input = build_route_projector_input(product_plan, formal_plan.get("authorization_manifest"))
        result = apply_template_route_projection(workspace, route_input, run_id=str(context.get("build_run_id") or ""))
    except TemplateRouteProjectorError as exc:
        return _failure(task, "template_route_projection_failed", str(exc))
    return {
        "task_id": task.get("id"), "owner": task.get("owner"), "status": "completed",
        "changed_files": [], "commands": ["template Route Projector apply"],
        "executed_by": {"agent": "platform", "mode": "deterministic", "source": EXECUTOR_NAME},
        "routeProjector": result,
    }


def _failure(task: Mapping[str, Any], category: str, reason: str) -> dict[str, Any]:
    """生成符合普通 Build Scheduler 契约的平台失败结果。"""

    return {"task_id": task.get("id"), "owner": task.get("owner"), "status": "failed", "failure_category": category, "failure_reason": reason, "agent_note": reason, "changed_files": [], "commands": []}
