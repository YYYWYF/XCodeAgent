"""集中管理初次开发事实、当前产物目录与应用级测试门禁。"""

from __future__ import annotations

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.development_change_snapshot import read_change_snapshot

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
from app.services.backend_module_scope import scope_endpoint_keys
from app.services.unit_task_manifest import backend_module_unit_id
from app.workspace.detail_design_documents import hydrate_external_detail_designs

INITIAL_DEVELOPMENT_PHASES = frozenset({
    "api_design_readiness_gate", "inspect_workspace", "prepare_build_tasks",
    "authorization_bootstrap", "builtin_authorization_bootstrap", "build", "unit_test", "unit_test_repair",
    "test_phase_confirmation",
})
ACTIVE_STATUSES = frozenset({"running", "stopping", "awaiting_user"})
_BUILD_PLAN_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / 'plans/build-task-plan.json'
# 生命周期 extensions 里记录"本轮变更已重做完成"的产物标识。
# 变更快照是技术规划确认时一次性冻结的本轮结论，不会随开发完成更新，因此需要
# 这份独立事实让 reconcile 不再把已重做的产物反复标回 changed。
_REDEVELOPED_EXTENSION = "redevelopedChangedTargets"


def redeveloped_target_keys(state: "ApplicationLifecycle") -> set[str]:
    """读取本轮已针对变更重新开发完成的产物标识，供目录校准与门禁共用。"""

    values = (state.extensions or {}).get(_REDEVELOPED_EXTENSION)
    if not isinstance(values, list):
        return set()
    return {str(item) for item in values if isinstance(item, str) and item}


def _with_redeveloped_target(state: "ApplicationLifecycle", target: DevelopmentArtifactTarget) -> "ApplicationLifecycle":
    """把目标并入"本轮已重做"集合，返回合并后的生命周期（不写盘）。"""

    keys = redeveloped_target_keys(state) | {development_artifact_key(target)}
    return state.model_copy(update={
        "extensions": {**(state.extensions or {}), _REDEVELOPED_EXTENSION: sorted(keys)},
    })


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
        return backend_module_unit_id(target.api_contract_id or "")
    return None


def in_scope_unit_ids(workspace: str | Path) -> set[str] | None:
    """读取构建计划里当前执行范围内的 Unit 标识；计划不可用时返回 None。

    范围的唯一标记是 Unit 上的 `input_fingerprint`：它只对进入 `required_unit_ids`
    的 Unit 写入（见 build_unit_compiler）。规划器据此确定当前 Build 任务，
    不改变应用级初次开发完成门禁的统计范围。

    计划缺失或不可解析时返回 None，调用方不记录范围外目标。
    """

    plan = _build_plan_for_scope(workspace)
    return _scoped_unit_ids(plan) if plan is not None else None


def _build_plan_for_scope(workspace: str | Path) -> dict[str, Any] | None:
    """读取一次正式计划，供 Unit 和接口范围诊断使用同一份数据。"""

    plan_path = Path(workspace).expanduser().resolve() / _BUILD_PLAN_RELATIVE_PATH
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return plan if isinstance(plan, dict) else None


def _scoped_unit_ids(plan: dict[str, Any]) -> set[str] | None:
    """提取当前计划中本轮写入了输入指纹的 Unit。"""

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

    plan = _build_plan_for_scope(workspace)
    units = _scoped_unit_ids(plan) if plan is not None else None
    if plan is None or units is None:
        return []
    scope = plan.get("build_execution_scope")
    scoped_endpoints = isinstance(scope, dict) and scope.get("type") in {"page", "endpoint"} and bool(scope.get("targetId"))
    registry = plan.get("task_registry")
    tasks = [task for task in registry.values() if isinstance(task, dict)] if isinstance(registry, dict) else []
    endpoint_keys = scope_endpoint_keys(tasks, scope) if scoped_endpoints else set()
    keys: list[str] = []
    for target in targets:
        unit_id = _target_unit_id(target)
        if unit_id is None:
            continue
        endpoint_outside = (
            target.type == "endpoint" and scoped_endpoints
            and (target.api_contract_id, target.endpoint_id) not in endpoint_keys
        )
        if unit_id not in units or endpoint_outside:
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


def _deleted_page_ids(product: dict[str, Any]) -> set[str]:
    """取正式 ProductPlan 里标记为本轮删除的页面 ID。

    正式产物刻意保留 `_change_status="deleted"`（见 product_planning 的落盘逻辑）：
    这是开发阶段清理页面代码、以及"全部产物完成"门禁必须知道的事实。
    其它状态（added/modified/unchanged）落盘时已剥离，这里只会看到 deleted。
    """

    return {
        _identifier(page.get("pageId"))
        for page in _records(product.get("pages"), "ProductPlan.pages")
        if str(page.get("_change_status") or "") == "deleted"
    }


def _target_change_status(
    target: DevelopmentArtifactTarget,
    pages: dict[str, Any],
    endpoints: dict[str, Any],
) -> str:
    """从变更快照取某个开发产物本轮的变更类型，缺失按未变更处理。

    实体不参与构建跳过判定（技术规划实体不要求独立设计），因此不在这里返回。
    """

    if target.type == "page":
        return str(pages.get(target.page_id or "") or "unchanged")
    if target.type == "endpoint":
        contract_changes = endpoints.get(target.api_contract_id or "") or {}
        return str(contract_changes.get(target.endpoint_id or "") or "unchanged")
    return "unchanged"


def read_confirmed_technical_plan(workspace: str | Path) -> dict[str, Any]:
    """供内部接口调试只读获取当前正式技术规划。"""
    return _confirmed_plan(workspace, "technical-plan")


def catalog_targets(workspace: str | Path) -> list[DevelopmentArtifactTarget]:
    """使用 ProductPlan 页面与 TechnicalPlan 接口、实体，与工作台目录保持一致。"""

    product = _confirmed_plan(workspace, "product-plan")
    technical = _confirmed_plan(workspace, "technical-plan")
    targets: list[DevelopmentArtifactTarget] = []
    # 本轮删除的页面不再属于开发产物：留在目录里会让"全部产物完成"门禁永远无法通过，
    # 也会让构建范围继续以为它还需要开发。
    # 删除事实有两个来源：正式 ProductPlan 上残留的 deleted 标记（模型漏掉删除时的兜底），
    # 以及技术规划确认时冻结的变更快照（正常路径）。
    changes = read_change_snapshot(workspace) or {}
    deleted_pages = _deleted_page_ids(product) | {
        page_id for page_id, status in (changes.get("pages") or {}).items() if status == "deleted"
    }
    for page in _records(product.get("pages"), "ProductPlan.pages"):
        page_id = _identifier(page.get("pageId"))
        targets.append(DevelopmentArtifactTarget(type="page", pageId=page_id))
    # 删除页面可能已经退出 ProductPlan，快照仍保留待清理的稳定身份。
    listed_pages = {target.page_id for target in targets}
    targets.extend(DevelopmentArtifactTarget(type="page", pageId=page_id)
                   for page_id in sorted(deleted_pages - listed_pages))
    contracts: set[str] = set()
    # 接口级的删除结论以快照为准：分组整体消失时，它的每个接口都必须是 deleted。
    deleted_endpoints = {
        (contract_id, endpoint_id)
        for contract_id, endpoints in (changes.get("endpoints") or {}).items()
        if isinstance(endpoints, dict)
        for endpoint_id, status in endpoints.items()
        if status == "deleted"
    }
    for contract in _records(technical.get("api_contracts"), "TechnicalPlan.api_contracts"):
        contract_id = _identifier(contract.get("id"))
        if contract_id in contracts:
            raise ValueError("开发产物 API Contract 标识重复。")
        contracts.add(contract_id)
        for endpoint in _records(contract.get("endpoints"), "API Contract.endpoints"):
            endpoint_id = _identifier(endpoint.get("id"))
            # 计划删除仍是一项开发清理任务；只有本轮节点完成后才允许进入测试。
            targets.append(DevelopmentArtifactTarget(
                type="endpoint", apiContractId=contract_id,
                endpointId=endpoint_id,
            ))
    listed_endpoints = {(target.api_contract_id, target.endpoint_id)
                        for target in targets if target.type == "endpoint"}
    targets.extend(DevelopmentArtifactTarget(type="endpoint", apiContractId=contract_id, endpointId=endpoint_id)
                   for contract_id, endpoint_id in sorted(deleted_endpoints - listed_endpoints))
    deleted_entities = {
        entity_id for entity_id, status in (changes.get("entities") or {}).items() if status == "deleted"
    }
    for entity in _records(technical.get("entities", []), "TechnicalPlan.entities"):
        entity_id = _identifier(entity.get("id"))
        if entity_id in deleted_entities:
            continue
        targets.append(DevelopmentArtifactTarget(type="entity", entityId=entity_id))
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
    # 本轮的变更结论来自技术规划确认时冻结的快照。它决定"已完成但本轮改过"的产物
    # 必须重做（见 test_entry_gate 与 completed_page_backend_endpoints）。
    changes = read_change_snapshot(workspace) or {}
    changed_pages = changes.get("pages") or {}
    changed_entities = changes.get("entities") or {}
    changed_endpoints = changes.get("endpoints") or {}
    # 本轮已按最新正式产物重新开发并通过门禁的产物：快照是"本轮相对基线"的一次性冻结结论，
    # 不会因开发完成而更新，所以必须单独记录这些目标已经重做完成，否则每次 reconcile
    # 都会把它们重新标回 changed，被测试入口门禁永远算作未完成。
    redeveloped = redeveloped_target_keys(state)
    # 快照缺失时无法判断"改没改"。一律按 unchanged 处理会让已改过的产物被跳过，
    # 所以这里记下缺口并关闭测试入口，要求重新确认技术规划。
    if not changes:
        artifacts = artifacts.model_copy(update={
            "catalog_error": "缺少本轮变更快照，无法判断已完成产物是否需要重做，请重新确认技术规划。",
        })
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
            # 跳过仅属于当前工作区的当前实体，不继承到其他应用或新迭代。
            if previous_entity and previous_entity.initial_development_status == "skipped" and not confirmed:
                artifacts.entities[target.entity_id or ""] = previous_entity
                continue
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
        # 本轮变更类型：只有"已完成 + 本轮改过"才需要重做，未完成的产物本来就要开发。
        # 已经针对本轮变更重新开发完成的目标，变更结论已经落实，不再算作待重做。
        status = _target_change_status(target, changed_pages, changed_endpoints)
        if status in {"added", "modified", "deleted"} and development_artifact_key(target) not in redeveloped:
            progress = progress.model_copy(update={
                "change_status": "changed",
                "change_reason": (
                    "本轮技术规划删除了该产物，需要执行代码与设计稿清理。"
                    if status == "deleted" else
                    "本轮技术规划新增或修改了该产物，需要按最新正式产物重新开发。"
                ),
            })
        else:
            progress = progress.model_copy(update={"change_status": "unchanged", "change_reason": None})
        if target.type == "page":
            artifacts.pages[target.page_id or ""] = progress
        else:
            artifacts.endpoints.setdefault(target.api_contract_id or "", {})[target.endpoint_id or ""] = progress
    return state.model_copy(update={"development_artifacts": artifacts})


def test_entry_gate(state: ApplicationLifecycle) -> TestEntryGate:
    """只检查页面和 Endpoint 的完成事实；技术规划实体不要求独立设计，空集合不放行。"""

    artifacts = state.development_artifacts
    records = [
        (DevelopmentArtifactTarget(type="page", pageId=key), progress)
        for key, progress in artifacts.pages.items()
    ] + [
        (DevelopmentArtifactTarget(type="endpoint", apiContractId=contract, endpointId=key), progress)
        for contract, endpoints in artifacts.endpoints.items()
        for key, progress in endpoints.items()
    ]
    # Build 计划只描述当前目标的执行范围；不能据此跳过其他尚未初次完成的产物。
    completed = sum(progress.initial_development_status == "completed" and progress.change_status == "unchanged"
                    for _, progress in records)
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
        blockers=[target for target, progress in records
                  if progress.initial_development_status != "completed" or progress.change_status != "unchanged"],
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


def complete_initial_development(
    workspace: str | Path, *, run_id: str, build_state: dict[str, Any] | None = None,
) -> ApplicationLifecycle:
    """提交当前目标及同轮验证成功的接口，原子保存完成证据并保持幂等。"""

    from app.services.application_lifecycle import (
        _application_lifecycle_lock, application_lifecycle_path, write_application_lifecycle,
    )

    with _application_lifecycle_lock(application_lifecycle_path(workspace)):
        state = refresh_development_artifacts(workspace)
        execution = state.active_executions.get(run_id)
        if execution is None:
            raise ValueError("初次开发完成缺少有效的服务端 execution。")
        if execution.development_target is None:
            return state
        progress = artifact_progress(state.development_artifacts, execution.development_target)
        if not isinstance(progress, DevelopmentArtifactProgress):
            return state
        completed = progress.initial_development_status == "completed"
        if execution.development_purpose != "initial" and not completed:
            return state
        if execution.status not in ACTIVE_STATUSES:
            if completed:
                return state
            raise ValueError("已停止或失败的 execution 不能提交初次开发完成。")
        if state.development_artifacts.catalog_error:
            raise ValueError(state.development_artifacts.catalog_error)
        targets = [execution.development_target]
        if execution.development_target.type == "page" and build_state is not None:
            from app.services.development_completion_evidence import completed_page_endpoints

            targets.extend(completed_page_endpoints(
                build_state, page_id=execution.development_target.page_id or "", run_id=run_id,
            ))
        changed = False
        completed_at = utc_now()
        branch = read_workspace_branch_name(workspace) or None
        for target in targets:
            current = artifact_progress(state.development_artifacts, target)
            if not isinstance(current, DevelopmentArtifactProgress):
                continue
            if current.initial_development_status != "completed":
                current.initial_development_status = "completed"
                current.completed_at = completed_at
                current.completed_run_id = run_id
                current.completed_thread_id = execution.thread_id
                current.completed_branch_name = branch
                changed = True
            # 每轮开发成功都要刷新"最近一次完成"：重新开发一个已完成的目标时，
            # 首次完成证据保持不变（归属标签仍指向原来那一版），但本轮运行必须留痕，
            # 前端才能识别"这次运行完成了该目标"并给出继续开发引导。
            if current.last_completed_run_id != run_id:
                current.last_completed_run_id = run_id
                changed = True
            if current.last_completed_thread_id != execution.thread_id:
                current.last_completed_thread_id = execution.thread_id
                changed = True
            if current.change_status != "unchanged":
                # 仅清除本轮验证成功目标的变更，首次完成身份与归属保持不变。
                current.change_status = "unchanged"
                current.change_reason = None
                # 同轮接口也要记录本轮变更已落实，避免写盘校准再次标为 changed。
                state = _with_redeveloped_target(state, target)
                changed = True
        if not changed:
            return state
        updated = state.model_copy(update={"revision": state.revision + 1, "updated_at": utc_now()})
        return write_application_lifecycle(workspace, updated, expected_revision=state.revision)


def complete_scope_target_development(
    workspace: str | Path,
    *,
    scope: dict[str, Any] | None,
    run_id: str,
    thread_id: str,
) -> ApplicationLifecycle:
    """按本轮 Build 执行范围补记目标完成，供既有完成证据缺失时兜底。

    **为什么需要兜底**：`complete_initial_development` 只能从 execution 的
    `development_target` 认目标。历史运行或执行记录已收尾时该字段为空，
    于是一个已经跑完 Build 和单元测试门禁的目标不会被登记为完成；而测试入口门禁
    又要求"首次完成 + 未变更"才算完成项，本轮目标自己就会一直卡在完成数少一个，
    导致永远进不了测试阶段的确认门。

    **兜底边界**：调用方必须已经用 Build 成功和单元测试门禁通过证明本次执行确实完成，
    且只登记本轮 scope 指向的那一个目标；这里不再自行判断执行是否成功。
    """

    from app.services.application_lifecycle import (
        _application_lifecycle_lock, application_lifecycle_path, load_application_lifecycle,
        write_application_lifecycle,
    )

    target = _target_from_execution_scope(scope)
    if target is None:
        # 应用级或身份不完整的范围没有可登记的单项产物，直接返回当前生命周期。
        return load_application_lifecycle(workspace) or refresh_development_artifacts(workspace)

    with _application_lifecycle_lock(application_lifecycle_path(workspace)):
        state = refresh_development_artifacts(workspace)
        current = artifact_progress(state.development_artifacts, target)
        # 实体完成以正式绑定确认状态为证据，这里只补页面与接口的 Build 完成事实。
        if not isinstance(current, DevelopmentArtifactProgress):
            return state
        run_id = run_id.strip()
        thread_id = thread_id.strip()
        if not run_id or not thread_id:
            # 缺少稳定证据时不伪造完成记录（领域模型会拒绝没有证据的 completed）。
            return state
        changed = False
        if current.initial_development_status != "completed":
            current.initial_development_status = "completed"
            current.completed_at = utc_now()
            current.completed_run_id = run_id
            current.completed_thread_id = thread_id
            current.completed_branch_name = read_workspace_branch_name(workspace) or None
            changed = True
        # 与正常提交路径一致：本轮运行也要记入"最近一次完成"，供前端识别本轮已完成。
        if current.last_completed_run_id != run_id:
            current.last_completed_run_id = run_id
            changed = True
        if current.last_completed_thread_id != thread_id:
            current.last_completed_thread_id = thread_id
            changed = True
        if current.change_status != "unchanged":
            # 本轮已按最新正式产物重新开发并通过门禁，变更标记随之清除。
            current.change_status = "unchanged"
            current.change_reason = None
            changed = True
        # 记录"本轮该变更已重做"：快照本身不会更新，不记这一条，下一次 calibrate
        # 又会按快照把它标回 changed。
        updated = _with_redeveloped_target(state, target)
        if not changed and updated.extensions == state.extensions:
            return state
        updated = updated.model_copy(update={"revision": state.revision + 1, "updated_at": utc_now()})
        return write_application_lifecycle(workspace, updated, expected_revision=state.revision)


def _target_from_execution_scope(scope: dict[str, Any] | None) -> DevelopmentArtifactTarget | None:
    """把 Build 执行范围转换成产物目标；应用级或字段缺失的范围返回 None。

    范围里的标识可能来自 checkpoint 的 snake_case 或公开投影的 camelCase，
    两种写法都要识别，否则兜底会因为字段名不一致而静默失效。
    """

    scope = scope if isinstance(scope, dict) else {}
    target_type = str(scope.get("type") or "").strip()
    target_id = str(scope.get("targetId") or scope.get("target_id") or "").strip()
    if not target_id:
        return None
    if target_type == "page":
        return DevelopmentArtifactTarget(type="page", pageId=target_id)
    if target_type == "endpoint":
        contract_id = str(
            scope.get("apiContractId") or scope.get("api_contract_id") or ""
        ).strip()
        # 接口目标必须带契约标识，否则无法唯一定位产物。
        if not contract_id:
            return None
        return DevelopmentArtifactTarget(
            type="endpoint", apiContractId=contract_id, endpointId=target_id,
        )
    return None


def completed_deletion_target(state: dict[str, Any]) -> DevelopmentArtifactTarget | None:
    """只识别删除节点提交的同目标完成结果，测试交接不得借用其他目标或普通 Build 证据。"""

    target = _target_from_execution_scope(state.get("build_execution_scope"))
    if target is None:
        return None
    result = state.get("deleted_page_result" if target.type == "page" else "deleted_endpoint_result")
    if not isinstance(result, dict) or result.get("completed") is not True:
        return None
    matches = result.get("pageId") == target.page_id if target.type == "page" else (
        result.get("apiContractId") == target.api_contract_id and result.get("endpointId") == target.endpoint_id
    )
    return target if matches else None


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
