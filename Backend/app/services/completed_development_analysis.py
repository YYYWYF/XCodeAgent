"""正式修订返回开发时只计算已完成目标的职责缺口，不创建 PlanningRun 或任务。"""

from pathlib import Path
from typing import Any

from app.domain.application_lifecycle import utc_now
from app.domain.development_artifacts import DevelopmentArtifactTarget
from app.services.build_context_resolver import (
    _load_current_confirmation_plan, resolve_confirmation_context,
)
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.application_config import read_application_config
from app.services.authorization_overlay import compile_authorization_overlay
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.development_artifacts import (
    artifact_progress, catalog_targets, reconcile_development_artifacts,
)
from app.services.page_task_freshness import page_task_covers_current_input
from app.services.unit_generation_requirements import resolve_generation_requirements
from app.workspace.task_documents import load_confirmed_build_task_plan
from app.branding import WORKSPACE_ARTIFACT_DIR


def analyze_completed_target(
    *, project_plan: dict, baseline: dict, context: dict, target: DevelopmentArtifactTarget,
) -> tuple[str, str]:
    """复用正式 DAG 的覆盖与缺项规则，只返回当前目标状态，不调模型或组装 DAG。"""

    scope = {
        "type": target.type,
        "targetId": target.page_id if target.type == "page" else target.endpoint_id,
        **({"apiContractId": target.api_contract_id} if target.type == "endpoint" else {}),
    }
    # 页面依赖允许读取，但后端 Endpoint 不成为本次页面的生成职责；未完成目标无调度入口。
    required = [unit for unit in context["required_unit_ids"] if unit != "frontend:shell"
                and (target.type != "page" or not unit.startswith("backend:"))]
    build_context = {**context, "scope": scope, "required_unit_ids": required, "project_plan": project_plan}
    skeleton = ensure_build_unit_skeleton(project_plan, {}, baseline)
    facts = resolve_reuse_facts(
        confirmed_plan=baseline, unit_skeleton=skeleton, build_context=build_context,
        workspace_snapshot={}, formal_plan=project_plan,
    )
    # 已确认但尚未执行的任务不能证明旧实现已经满足当前职责。
    completed = tuple(key for key, task in baseline["task_registry"].items()
                      if task.get("status") in {"completed", "already_satisfied"})
    facts = facts.retained_subset(completed)
    requirements = resolve_generation_requirements(
        required_unit_ids=required, build_execution_scope=scope,
        unit_skeleton=skeleton, reuse_facts=facts, formal_target=project_plan,
        endpoint_designs=context.get("endpoint_designs", []),
    )
    missing = [item.description for duties in requirements.generation_requirements_by_unit.values() for item in duties]
    if missing:
        return "changed", "；".join(missing)
    return "unchanged", "已有已完成任务仍覆盖当前正式职责。"


def _target_analysis(workspace: Path, target: DevelopmentArtifactTarget, plan: dict, baseline: dict) -> tuple[str, str]:
    """先比较页面自身合同，再解析直接依赖；映射不可用时不伪造无变更结论。"""

    if target.type == "page":
        tasks = [task for task in baseline["task_registry"].values() if task["unit_id"] == f"page:{target.page_id}"]
        if tasks and any(not page_task_covers_current_input(task, plan) for task in tasks):
            return "changed", "页面实现合同已变化，原任务不再覆盖当前页面职责。"
    scope = {"type": target.type, "targetId": target.page_id or target.endpoint_id}
    if target.type == "endpoint":
        scope["apiContractId"] = target.api_contract_id
    context = resolve_confirmation_context(workspace, scope, project_plan=plan)["build_context"]
    context = compile_authorization_overlay(plan, {**context, "scope": scope},
                                            application_config=read_application_config(workspace))
    return analyze_completed_target(project_plan=plan, baseline=baseline, context=context, target=target)


def analyze_completed_development(workspace: str, *, change_id: str) -> dict[str, int]:
    """在当前修订归属下批量检查并提交状态；未完成页面/API完全跳过，首次完成证据不改写。"""

    from app.services.application_lifecycle import (
        _application_lifecycle_lock, application_lifecycle_path, load_application_lifecycle,
        write_application_lifecycle,
    )

    root = Path(workspace).expanduser().resolve()
    with _application_lifecycle_lock(application_lifecycle_path(root)):
        current = load_application_lifecycle(root)
        active = current.active_formal_revision if current else None
        if active is None or active.change_id != change_id or active.status != "building":
            raise ValueError("已完成目标变更分析必须属于当前正式修订的开发续接。")
        state = reconcile_development_artifacts(root, current).model_copy(deep=True)
        if state.development_artifacts.catalog_error:
            raise ValueError(state.development_artifacts.catalog_error)
        targets = [target for target in catalog_targets(root) if target.type in {"page", "endpoint"}
                   and artifact_progress(state.development_artifacts, target).initial_development_status == "completed"]
        summary = {"checked": len(targets), "changed": 0, "unavailable": 0}
        # 整轮只加载一次正式基线与运行时页面合同；空目标集合也正常显示选择卡片。
        plan: dict[str, Any] = {}
        baseline = None
        input_error = None
        if targets:
            try:
                baseline = load_confirmed_build_task_plan(root)
                if baseline is None:
                    raise ValueError("缺少有效的已确认任务基线。")
                plan = _load_current_confirmation_plan(
                    root, target_type="page" if any(target.type == "page" for target in targets) else "endpoint",
                    plan_path=root / WORKSPACE_ARTIFACT_DIR / "plans/technical-plan.json",
                )
            except (OSError, ValueError, TypeError) as exc:
                input_error = str(exc)
        for target in targets:
            progress = artifact_progress(state.development_artifacts, target)
            try:
                if input_error:
                    raise ValueError(input_error)
                status, reason = _target_analysis(root, target, plan, baseline)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                status, reason = "unavailable", f"变更检查暂无法完成：{exc}"
            progress.change_status = status
            progress.change_reason = reason
            progress.checked_change_id = change_id
            if status in summary:
                summary[status] += 1
        if state.development_artifacts != current.development_artifacts:
            updated = state.model_copy(update={"revision": current.revision + 1, "updated_at": utc_now()})
            write_application_lifecycle(root, updated, expected_revision=current.revision)
        return summary
