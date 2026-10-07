"""从现有生命周期和 PlanningRun 恢复工作台的只读进度。"""

from app.domain.application_lifecycle import ApplicationLifecycle
from app.services.planning_run_contracts import PlanningRunProjection
from app.services.planning_run_progress import project_planning_run_progress
from app.workspace.planning_run_documents import load_planning_run


def project_workbench_progress(workspace: str, state: ApplicationLifecycle) -> dict:
    """只投影当前 execution，严格匹配 Run/Thread 后复用既有 DAG 进度协议。"""
    try:
        payload = load_planning_run({"workspace": workspace})
        planning = PlanningRunProjection.model_validate(payload) if payload else None
    except (OSError, ValueError):
        # 快照损坏不能阻断基础运行状态，也不能伪造生成进度。
        planning = None
    executions = {}
    for run_id, execution in state.active_executions.items():
        item = execution.model_dump(mode="json", by_alias=True)
        if (
            planning is not None
            and planning.workflow_run_id == run_id
            and planning.thread_id == execution.thread_id
            and execution.phase == "prepare_build_tasks"
        ):
            item["dagGeneration"] = project_planning_run_progress(planning)
        executions[run_id] = item
    return executions
