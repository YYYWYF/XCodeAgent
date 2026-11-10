"""统一任务生成与确认时对正式基线的当前职责投影。"""

from collections.abc import Mapping, Sequence

from app.services.backend_module_freshness import project_current_backend_task
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.task_input_freshness import task_covers_current_input


def project_retained_tasks(
    tasks: Sequence[dict], *, project_plan: Mapping, build_context: Mapping,
    reuse_facts: ReuseFacts, planning_unit_ids: set[str],
) -> list[dict]:
    """仅按当前正式输入裁剪过期职责，生成和确认必须使用相同的保留集合。"""

    retained = []
    for task in tasks:
        unit_id = task["unit_id"]
        if unit_id.startswith("backend:module:"):
            projected = project_current_backend_task(
                task, project_plan, build_context,
                reuse_facts.reusable_capabilities_by_unit.get(unit_id, {}),
            )
            if projected is not None:
                retained.append(projected)
        elif unit_id not in planning_unit_ids or task_covers_current_input(
            task, project_plan, build_context,
        ):
            retained.append(task)
    return retained
