"""统一任务生成与确认时对正式基线的当前职责投影。"""

from collections.abc import Mapping, Sequence

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.task_input_freshness import task_covers_current_input
from app.services.task_generation_policy import required_bootstrap_source_types


def project_retained_tasks(
    tasks: Sequence[dict], *, project_plan: Mapping, build_context: Mapping,
    reuse_facts: ReuseFacts, planning_unit_ids: set[str],
) -> list[dict]:
    """只继承本轮后端实际需要的有效配置检查，不把范围外配置带入新计划。"""
    if not required_bootstrap_source_types(build_context):
        return []
    return [task for task in tasks if task["unit_id"] == "backend:bootstrap"
            and task_covers_current_input(task, project_plan, build_context)]
