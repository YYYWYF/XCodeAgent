"""从当前 Build 绑定及执行切片提取页面一起完成的接口。"""

from typing import Any

from app.domain.development_artifacts import DevelopmentArtifactTarget
from app.services.backend_module_scope import scope_endpoint_keys, task_endpoint_keys
from app.services.build_scheduler import resolve_execution_slice
from app.workspace.task_documents import build_task_plan_sha256


def completed_page_endpoints(
    state: dict[str, Any], *, page_id: str, run_id: str,
) -> list[DevelopmentArtifactTarget]:
    """按接口汇总共享模块任务，记录同轮执行且通过单测门的完成事实。"""

    scope = state.get("build_execution_scope")
    plan = state.get("build_task_plan")
    execution_slice = state.get("build_execution_slice")
    if (state.get("active_run_id") != run_id
            or state.get("unit_test_gate_passed") is not True
            or not isinstance(scope, dict)
            or scope.get("type") != "page" or scope.get("targetId") != page_id
            or not isinstance(plan, dict)
            or plan.get("confirmation_status") != "confirmed"
            or plan.get("build_execution_scope") != scope
            or not state.get("build_run_id")
            or state.get("build_run_plan_sha256") != build_task_plan_sha256(plan)
            or not isinstance(execution_slice, dict)
            or execution_slice.get("scope") != scope):
        return []
    registry = plan.get("task_registry")
    tasks = state.get("tasks")
    slice_ids = execution_slice.get("task_ids")
    if (not isinstance(registry, dict) or not isinstance(tasks, list)
            or not isinstance(slice_ids, list)):
        return []
    runtime = {task["id"]: task for task in tasks
               if isinstance(task, dict) and isinstance(task.get("id"), str)}
    # 用与调度器相同的依赖闭包过滤累计 DAG；旧接口任务不能因状态成功被补记。
    scoped = resolve_execution_slice(
        build_task_plan=plan,
        tasks=[task for task in registry.values() if isinstance(task, dict)],
        build_execution_scope=scope,
    )
    endpoint_keys = scope_endpoint_keys(scoped["tasks"], scope)
    by_endpoint: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for task in scoped["tasks"]:
        for key in task_endpoint_keys(task) & endpoint_keys:
            by_endpoint.setdefault(key, []).append(task)
    targets: list[DevelopmentArtifactTarget] = []
    for (contract_id, endpoint_id), endpoint_tasks in sorted(by_endpoint.items()):
        # 与 Build 汇总一致：修复关闭的任务和已满足任务均属于成功终态。
        if not all(
            task.get("id") in slice_ids
            and runtime.get(task["id"], {}).get("unit_id") == task["unit_id"]
            and runtime.get(task["id"], {}).get("status") in {"completed", "already_satisfied"}
            for task in endpoint_tasks
        ):
            continue
        targets.append(DevelopmentArtifactTarget(
            type="endpoint", apiContractId=contract_id, endpointId=endpoint_id,
        ))
    return targets
