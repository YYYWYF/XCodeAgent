"""为当前 Build 切片补充只读任务展示信息，不修改正式任务或调度状态。"""

from collections.abc import Mapping, Sequence

from app.services.backend_module_scope import backend_task_scope_projection


def project_execution_tasks(tasks: Sequence[dict], all_tasks: Sequence[Mapping]) -> list[dict]:
    """从完整任务集合解析依赖名称，复用确认卡的后端阶段与接口投影。"""

    titles = {task["id"]: task["title"] for task in all_tasks if task.get("id") and task.get("title")}
    return [
        {
            **task,
            **backend_task_scope_projection(task),
            "dependencyTitles": {
                dependency: titles[dependency]
                for dependency in task.get("dependencies") or []
                if dependency in titles
            },
        }
        for task in tasks
    ]
