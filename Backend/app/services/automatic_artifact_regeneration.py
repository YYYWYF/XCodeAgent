"""只重置开发内部的失效任务计划，上游文档问题交给原节点检查报告。"""
from typing import Any

from app.services.stage_prerequisites import regeneration_for_retry


async def prepare_automatic_regeneration(inputs: dict[str, Any], *, run_id: str, thread_id: str):
    """保留开发目标与阶段，只在旧 DAG 失效时重新进入开发检查。"""
    if inputs.get("workflow_scope") or inputs.get("workflow_action") or inputs.get("ended_resume"):
        return None
    workspace = inputs.get("workspace")
    if not workspace:
        return None
    values = inputs.get("resume_values") or {}
    node = inputs.get("resume_from") or "api_design_readiness_gate"
    issue = regeneration_for_retry(workspace, node, state={**values, "workspace": workspace})
    if issue is None or issue.key != "build_task_plan":
        # 正式文档缺失或失效不授权返回规划；原开发节点仍检查并报告具体原因。
        return None
    from app.services.retry_node_state import retry_node_state
    # 任务计划属于开发阶段；重新生成后仍由原 DAG 确认门等待用户确认。
    inputs["resume_from"] = "api_design_readiness_gate"
    inputs["resume_values"] = retry_node_state(values, "prepare_build_tasks", planning=False)
    return None
