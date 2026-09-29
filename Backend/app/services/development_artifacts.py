"""集中管理初次开发事实、当前产物目录与应用级测试门禁。"""

from __future__ import annotations

from app.branding import WORKSPACE_ARTIFACT_DIR

import json
from pathlib import Path
from typing import Any

from app.domain.application_lifecycle import ApplicationLifecycle, WorkbenchExecution, utc_now
from app.domain.development_artifacts import (
    DevelopmentArtifactProgress,
    DevelopmentArtifacts,
    DevelopmentArtifactTarget,
    EntityDevelopmentProgress,
    TestEntryGate,
)
from app.services.repository_branch import read_workspace_branch_name
from app.workspace.detail_design_documents import hydrate_external_detail_designs

INITIAL_DEVELOPMENT_PHASES = frozenset({
    "api_design_readiness_gate", "inspect_workspace", "prepare_build_tasks",
    "authorization_bootstrap", "build", "unit_test", "unit_test_repair",
    "test_phase_confirmation",
})
ACTIVE_STATUSES = frozenset({"running", "stopping", "awaiting_user"})
_BUILD_PLAN_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / 'plans/build-task-plan.json'


def development_artifact_key(target: DevelopmentArtifactTarget) -> str:
    """生成产物的稳定标识，用于记录当前 Build 范围。"""

    if target.type == "page":
        return f"page:{target.page_id}"
    if target.type == "entity":
        return f"entity:{target.entity_id}"
    return f"endpoint:{target.api_contract_id}:{target.endpoint_id}"


def _target_unit_id(target: DevelopmentArtifactTarget) -> str | None:
    """映射产物到它在构建计划里的 Unit 标识；实体没有独立 Unit。"""

    if target.type == "page":
        return f"page:{target.page_id}"
    if target.type == "endpoint":
        return f"backend:endpoint:{target.api_contract_id}:{target.endpoint_id}"
    return None


def in_scope_unit_ids(workspace: str | Path) -> set[str] | None:
    """读取构建计划里当前执行范围内的 Unit 标识；计划不可用时返回 None。

    范围的唯一标记是 Unit 上的 `input_fingerprint`：它只对进入 `required_unit_ids`
    的 Unit 写入（见 build_unit_compiler）。规划器据此确定当前 Build 任务，
    不改变应用级初次开发完成门禁的统计范围。

    计划缺失或不可解析时返回 None，调用方不记录范围外目标。
    """

    plan_path = Path(workspace).expanduser().resolve() / _BUILD_PLAN_RELATIVE_PATH
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(plan, dict):
        return None
    units = plan.get("build_units")
    if not isinstance(units, dict):
        return None
    return {
        unit_id
        for unit_id, unit in units.items()
        if isinstance(unit, dict) and "input_fingerprint" in unit
    }


def out_of_scope_keys(
    workspace: str | Path, targets: list[DevelopmentArtifactTarget]
) -> list[str]:
    """列出本次迭代范围之外的产物键；无法判定范围时返回空列表。"""

    scope = in_scope_unit_ids(workspace)
    if scope is None:
        return []
    keys: list[str] = []
    for target in targets:
        unit_id = _target_unit_id(target)
        if unit_id is None or unit_id in scope:
            continue
        keys.append(development_artifact_key(target))
    return keys


class DevelopmentArtifactsIncompleteError(ValueError):
    """以结构化业务错误阻止绕过全部产物完成门禁。"""

    code = "development_artifacts_incomplete"

    def __init__(self, gate: TestEntryGate) -> None:
        """携带同一份权威门禁，供 AG-UI 错误和快照投影。"""

        super().__init__(gate.reason or "全部开发产物完成后才能进入测试阶段。")
        self.gate = gate


def _confirmed_plan(workspace: str | Path, name: str) -> dict[str, Any]:
    """只读取正式且已确认的当前规划，不从草稿或历史文件推断。"""

    path = Path(workspace) / WORKSPACE_ARTIFACT_DIR / "plans" / f"{name}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("confirmation_status") != "confirmed":
        raise ValueError(f"{name} 尚未确认。")
    return value


def _records(value: Any, label: str) -> list[dict[str, Any]]:
    """严格拒绝损坏的目录，避免过滤坏记录后误判全部完成。"""

    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} 必须是对象数组。")
    return value


def _identifier(value: Any) -> str:
    """要求正式目录提供非空的稳定字符串标识。"""

    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("开发产物缺少有效的稳定标识。")
    return value


def catalog_targets(workspace: str | Path) -> list[DevelopmentArtifactTarget]:
    """使用 ProductPlan 页面与 TechnicalPlan 接口、实体，与工作台目录保持一致。"""

    product = _confirmed_plan(workspace, "product-plan")
    technical = _confirmed_plan(workspace, "technical-plan")
    targets: list[DevelopmentArtifactTarget] = []
    for page in _records(product.get("pages"), "ProductPlan.pages"):
        targets.append(DevelopmentArtifactTarget(type="page", pageId=_identifier(page.get("pageId"))))
    contracts: set[str] = set()
    for contract in _records(technical.get("api_contracts"), "TechnicalPlan.api_contracts"):
        contract_id = _identifier(contract.get("id"))
        if contract_id in contracts:
            raise ValueError("开发产物 API Contract 标识重复。")
        contracts.add(contract_id)
        for endpoint in _records(contract.get("endpoints"), "API Contract.endpoints"):
            targets.append(DevelopmentArtifactTarget(
                type="endpoint", apiContractId=contract_id,
                endpointId=_identifier(endpoint.get("id")),
            ))
    for entity in _records(technical.get("entities", []), "TechnicalPlan.entities"):
        targets.append(DevelopmentArtifactTarget(type="entity", entityId=_identifier(entity.get("id"))))
    keys = [target.model_dump_json() for target in targets]
    if len(set(keys)) != len(keys):
        raise ValueError("开发产物标识重复。")
    return targets


def artifact_progress(
    artifacts: DevelopmentArtifacts, target: DevelopmentArtifactTarget,
) -> DevelopmentArtifactProgress | EntityDevelopmentProgress | None:
    """按完整目标身份读取状态，不把页面依赖接口视作同一产物。"""

    if target.type == "page":
        return artifacts.pages.get(target.page_id or "")
    if target.type == "entity":
        return artifacts.entities.get(target.entity_id or "")
    return artifacts.endpoints.get(target.api_contract_id or "", {}).get(target.endpoint_id or "")


def reconcile_development_artifacts(workspace: str | Path, state: ApplicationLifecycle) -> ApplicationLifecycle:
    """在 lifecycle 写入锁内同步已确认目录，并从当前开发执行推导未完成项状态。"""

    old = state.development_artifacts
    try:
        targets = catalog_targets(workspace)
        technical = hydrate_external_detail_designs(
            Path(workspace) / WORKSPACE_ARTIFACT_DIR / 'plans/technical-plan.json',
            _confirmed_plan(workspace, "technical-plan"),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        # 草稿/文件错误不删除原完成事实，但必须关闭测试入口。
        return state.model_copy(update={"development_artifacts": old.model_copy(update={
            "catalog_error": f"开发产物目录不可用：{exc}",
        })})
    artifacts = DevelopmentArtifacts(catalogError=None)
    # Build 计划按当前开发目标裁剪；这里仅保留范围诊断，不改变应用级门禁。
    artifacts.out_of_scope = out_of_scope_keys(workspace, targets)
    # 本次 reconcile 所在的迭代（用于给"本轮才完成"的实体打归属标签）。只读一次。
    current_branch = read_workspace_branch_name(workspace)
    for target in targets:
        if target.type == "entity":
            # 只认当前正式绑定的显式确认；选表、生成设计和等待确认都不算完成。
            confirmed = any(
                detail.get("entity_id") == target.entity_id and detail.get("status") == "confirmed"
                for detail in technical.get("entity_detail_plans", [])
            )
            active = any(
                execution.scope == "data_source" and execution.target_id == target.entity_id
                and execution.status in ACTIVE_STATUSES
                for execution in state.active_executions.values()
            )
            # 实体完成状态每次 reconcile 都按技术规划重算，所以归属标签必须显式继承：
            # 已经是 completed 的实体保留它首次完成时的分支，不能每轮都被改写成当前分支。
            previous_entity = old.entities.get(target.entity_id or "")
            entity_completed_branch = (
                previous_entity.completed_branch_name
                if previous_entity is not None
                and previous_entity.initial_development_status == "completed"
                else None
            )
            if confirmed and not entity_completed_branch:
                entity_completed_branch = current_branch or None
            artifacts.entities[target.entity_id or ""] = EntityDevelopmentProgress(
                initialDevelopmentStatus="completed" if confirmed else "in_progress" if active else "pending",
                completedBranchName=entity_completed_branch,
            )
            continue
        progress = artifact_progress(old, target) or DevelopmentArtifactProgress()
        if progress.initial_development_status != "completed":
            # 调试重启可能登记为 revision；运行圆点仍跟随目标，首次完成资格另行校验。
            active = any(
                execution.development_target == target
                and execution.status in ACTIVE_STATUSES
                for execution in state.active_executions.values()
            )
            progress = DevelopmentArtifactProgress(initialDevelopmentStatus="in_progress" if active else "pending")
        if target.type == "page":
            artifacts.pages[target.page_id or ""] = progress
        else:
            artifacts.endpoints.setdefault(target.api_contract_id or "", {})[target.endpoint_id or ""] = progress
    return state.model_copy(update={"development_artifacts": artifacts})


def test_entry_gate(state: ApplicationLifecycle) -> TestEntryGate:
    """从同一 lifecycle 快照计算计数与门禁，空集合永不自动放行。"""

    artifacts = state.development_artifacts
    records = [
        (DevelopmentArtifactTarget(type="page", pageId=key), progress)
        for key, progress in artifacts.pages.items()
    ] + [
        (DevelopmentArtifactTarget(type="endpoint", apiContractId=contract, endpointId=key), progress)
        for contract, endpoints in artifacts.endpoints.items()
        for key, progress in endpoints.items()
    ] + [
        (DevelopmentArtifactTarget(type="entity", entityId=key), progress)
        for key, progress in artifacts.entities.items()
    ]
    # Build 计划只描述当前目标的执行范围；不能据此跳过其他尚未初次完成的产物。
    completed = sum(progress.initial_development_status == "completed" for _, progress in records)
    in_progress = sum(progress.initial_development_status == "in_progress" for _, progress in records)
    reason = artifacts.catalog_error
    if state.initialization.stage != "ready_for_workbench":
        reason = "应用尚未完成创建规划。"
    elif not reason and not records:
        reason = "暂无可开发产物。"
    elif not reason and completed != len(records):
        reason = f"完成全部开发产物后可进入测试，当前 {completed}/{len(records)}。"
    return TestEntryGate(
        allowed=reason is None, total=len(records), completed=completed,
        pending=len(records) - completed - in_progress, inProgress=in_progress,
        blockers=[target for target, progress in records if progress.initial_development_status != "completed"],
        reason=reason,
    )


def refresh_development_artifacts(workspace: str | Path) -> ApplicationLifecycle:
    """冷启动及测试边界重新校准目录，仅有变化时递增 revision 并写盘。"""

    from app.services.application_lifecycle import (
        _application_lifecycle_lock, application_lifecycle_path,
        ApplicationLifecycleMissingError, load_application_lifecycle,
        write_application_lifecycle,
    )

    with _application_lifecycle_lock(application_lifecycle_path(workspace)):
        current = load_application_lifecycle(workspace)
        if current is None:
            raise ApplicationLifecycleMissingError("应用生命周期尚未创建。")
        updated = reconcile_development_artifacts(workspace, current)
        if updated.development_artifacts == current.development_artifacts:
            return current
        updated = updated.model_copy(update={"revision": current.revision + 1, "updated_at": utc_now()})
        return write_application_lifecycle(workspace, updated, expected_revision=current.revision)


def require_test_entry(workspace: str | Path) -> ApplicationLifecycle:
    """在真实测试执行前读取服务端状态，禁止客户端快照或直接恢复绕过门禁。"""

    try:
        state = refresh_development_artifacts(workspace)
    except (OSError, ValueError) as exc:
        raise DevelopmentArtifactsIncompleteError(TestEntryGate(
            allowed=False, total=0, completed=0, pending=0, inProgress=0,
            blockers=[], reason=f"无法读取开发产物状态：{exc}",
        )) from exc
    gate = test_entry_gate(state)
    if not gate.allowed:
        raise DevelopmentArtifactsIncompleteError(gate)
    return state


def complete_initial_development(workspace: str | Path, *, run_id: str) -> ApplicationLifecycle:
    """由已校验 Build 与单测证据的节点提交当前初次开发目标，重复提交保持幂等。"""

    from app.services.application_lifecycle import (
        _application_lifecycle_lock, application_lifecycle_path, write_application_lifecycle,
    )

    with _application_lifecycle_lock(application_lifecycle_path(workspace)):
        state = refresh_development_artifacts(workspace)
        execution = state.active_executions.get(run_id)
        if execution is None:
            raise ValueError("初次开发完成缺少有效的服务端 execution。")
        if execution.development_purpose != "initial" or execution.development_target is None:
            return state
        progress = artifact_progress(state.development_artifacts, execution.development_target)
        if not isinstance(progress, DevelopmentArtifactProgress) or progress.initial_development_status == "completed":
            return state
        if execution.status not in ACTIVE_STATUSES:
            raise ValueError("已停止或失败的 execution 不能提交初次开发完成。")
        if state.development_artifacts.catalog_error:
            raise ValueError(state.development_artifacts.catalog_error)
        progress.initial_development_status = "completed"
        progress.completed_at = utc_now()
        progress.completed_run_id = run_id
        progress.completed_thread_id = execution.thread_id
        # 记下"在哪一轮完成"。读不到分支名就留空，不伪造。
        progress.completed_branch_name = read_workspace_branch_name(workspace) or None
        updated = state.model_copy(update={"revision": state.revision + 1, "updated_at": utc_now()})
        return write_application_lifecycle(workspace, updated, expected_revision=state.revision)


def execution_development_metadata(
    state: ApplicationLifecycle, *, phase: str, scope: str, target_id: str,
    api_contract_id: str | None, initial_entry: bool, replaces_run_id: str | None,
) -> dict[str, Any]:
    """只允许正式初次入口或同目标续接建立初次用途，修订与普通聊天不补记完成。"""

    previous: WorkbenchExecution | None = state.active_executions.get(replaces_run_id or "")
    if previous and previous.development_target and not initial_entry:
        target = previous.development_target
        matches = (scope == "page" and target.page_id == target_id) or (
            scope == "endpoint" and target.endpoint_id == target_id
            and target.api_contract_id == api_contract_id
        )
        if matches and phase in INITIAL_DEVELOPMENT_PHASES:
            return {
                "development_purpose": "revision" if state.active_formal_revision else previous.development_purpose,
                "development_target": target,
            }
    if scope not in {"page", "endpoint"} or phase not in INITIAL_DEVELOPMENT_PHASES:
        return {}
    if not initial_entry and not api_contract_id and scope == "endpoint":
        return {}
    target = DevelopmentArtifactTarget(
        type=scope, pageId=target_id if scope == "page" else None,
        apiContractId=api_contract_id if scope == "endpoint" else None,
        endpointId=target_id if scope == "endpoint" else None,
    )
    progress = artifact_progress(state.development_artifacts, target)
    initial = initial_entry and not state.active_formal_revision and progress is not None and (
        progress.initial_development_status != "completed"
    )
    return {"development_purpose": "initial" if initial else "revision", "development_target": target}
