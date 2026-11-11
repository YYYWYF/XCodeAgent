"""统一任务生成与确认时对正式基线的当前职责投影。"""

from collections.abc import Mapping, Sequence

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.task_input_freshness import task_covers_current_input


def project_retained_tasks(
    tasks: Sequence[dict], *, project_plan: Mapping, build_context: Mapping,
    reuse_facts: ReuseFacts, planning_unit_ids: set[str],
) -> list[dict]:
    """本轮只继承有效配置检查任务，业务任务及其完成状态不带入新计划。"""
    return [task for task in tasks if task["unit_id"] == "backend:bootstrap"
            and task_covers_current_input(task, project_plan, build_context)]
