"""右侧设置沿当前 Formal Revision 草稿确认，不通过聊天或直接修改生成源码。"""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus, utc_now
from app.domain.application_revision import ActiveFormalRevision, FormalRevisionBranch, RevisionTarget
from app.services.application_lifecycle import (
    _application_lifecycle_lock, application_lifecycle_path, application_lifecycle_payload,
    load_application_lifecycle, write_application_lifecycle,
)
from app.services.artifact_invalidation import canonical_sha256
from app.services.application_config import read_application_config
from app.services.backend_process_registry import backend_launch_lock
from app.services.preview_runtime_guard import maintenance_lock, require_no_maintenance
from app.services.python_runtime_checks import runtime_check_plan
from app.services.revision_drafts import create_revision_draft, discard_current_revision_draft
from app.services.runtime_agent_contracts import agent_contract_index, agent_fact_digest
from app.services.runtime_agent_editable_config import SettingsPatch, apply_settings_patch, editable_settings, settings_diffs
from app.services.runtime_agent_settings_requests import GetSettingsRequest, PreviewSettingsRequest, SubmitSettingsRequest
from app.services.workflow_quality_results import pending_quality_results
from app.services.technical_plan_topology_selection import rebind_selected_topology
from app.workspace.plan_documents import _write_text_atomically, render_project_plan_markdown
from app.workspace.revision_draft_documents import load_revision_draft, write_revision_draft


ARTIFACT_KEY = "agent-settings"


def _lifecycle(root: Path):
    """要求当前应用生命周期存在，不为设置修改创建另一套应用身份。"""

    current = load_application_lifecycle(root)
    if current is None:
        raise ValueError("当前应用生命周期尚未初始化。")
    return current


def _plan(root: Path, agent_id: str) -> tuple[dict, dict, Path]:
    """仅读取已确认的 Runtime 规划，Java 应用不能进入此配置写入路径。"""

    plan = runtime_check_plan(root, {})
    contract = agent_contract_index(plan).get(agent_id) if plan else None
    if contract is None:
        raise ValueError("当前已确认的 Runtime 技术规划中不存在这个 Agent。")
    return plan, contract, root / WORKSPACE_ARTIFACT_DIR / "plans/technical-plan.json"


def _upstreams(root: Path) -> dict[str, Path]:
    """只绑定配置编辑实际消费的应用配置和 ProductPlan，不依赖 UI 设计文件。"""

    base = root / WORKSPACE_ARTIFACT_DIR
    # Prompt／Temperature 不消费 UI 设计；技术规划自身由 baseCanonicalSha256 单独保护。
    return {"application": base / "application.json", "product-plan": base / "plans/product-plan.json"}


def _available(current, plan: dict, *, owned_change: str | None = None) -> None:
    """只阻止应用级规划和实际共享 Agent 工厂的开发占用，不阻止无关页面或接口。"""

    active = current.active_formal_revision
    if current.resource_locks.application is not None or current.pending_revision_impact:
        raise ValueError("当前应用有应用级规划或开发占用，请先处理该任务后修改配置。")
    # 各 Agent 共用 factory.py，任何绑定 Agent 的页面正在开发时都可能写同一个文件。
    pages = {page for contract in agent_contract_index(plan).values() for page in contract["pageIds"]}
    keys = {f"page:{page}" for page in pages}
    related = next((execution for execution in current.active_executions.values()
        if execution.scope == "application" or execution.page_id in pages
        or execution.scope == "page" and execution.target_id in pages
        or keys.intersection(execution.resource_keys)), None)
    if related is not None or pages.intersection(current.resource_locks.pages) or current.resource_locks.agents:
        label = related.page_id or related.target_id if related else "关联页面"
        raise ValueError(f"{label} 的开发任务占用 Agent 工厂，请先完成或结束该任务后修改配置；无关页面和接口不受限制。")
    if active is not None and (active.change_id != owned_change or active.current_artifact != ARTIFACT_KEY):
        raise ValueError("当前应用有未完成的正式修订，请先处理原修订。")
    if (current.initialization.stage != ApplicationLifecycleStage.READY_FOR_WORKBENCH
            or current.initialization.status != ApplicationLifecycleStatus.COMPLETED):
        raise ValueError("请先完成应用技术规划确认和模板准备，再修改 Agent 配置。")


def _preview(root: Path, current, agent_id: str) -> dict | None:
    """从同一个 Formal Revision 草稿恢复修改前后内容，不保存第二份正式配置。"""

    active = current.active_formal_revision
    if active is None or active.current_artifact != ARTIFACT_KEY or (active.target.model_extra or {}).get("agentId") != agent_id:
        return None
    metadata, markdown, candidate = load_revision_draft(root, change_id=active.change_id, artifact_key=ARTIFACT_KEY)
    contract = agent_contract_index(candidate)[agent_id]
    after = editable_settings(contract)
    return {"changeId": active.change_id, "agentId": agent_id, "lifecycleRevision": current.revision,
            "draftSha256": sha256(markdown.encode()).hexdigest(), "before": metadata.hidden["before"],
            "after": after, "fieldDiffs": settings_diffs(metadata.hidden["before"], after), "pageIds": contract["pageIds"],
            "markdown": markdown}


def execute_settings(request: GetSettingsRequest | PreviewSettingsRequest | SubmitSettingsRequest,
                     *, thread_id: str, run_id: str) -> dict:
    """串行执行读取、预览和确认；每次变更复用应用锁与当前修订草稿路径。"""

    root = Path(request.workspaceRoot).expanduser().resolve()
    with maintenance_lock, backend_launch_lock(root), _application_lifecycle_lock(application_lifecycle_path(root)):
        if request.action != "get_settings":
            require_no_maintenance(str(root))
        current = _lifecycle(root)
        if isinstance(request, SubmitSettingsRequest):
            return _submit(root, current, request)
        plan, contract, path = _plan(root, request.agentId)
        if isinstance(request, PreviewSettingsRequest):
            return _prepare(root, current, plan, contract, path, request, thread_id, run_id)
        return {"contractHash": agent_fact_digest(contract), "technicalPlanSha256": canonical_sha256(path),
                "editableSettings": editable_settings(contract),
                "preview": _preview(root, current, request.agentId)}


def _prepare(root: Path, current, plan: dict, contract: dict, path: Path,
             request: PreviewSettingsRequest, thread_id: str, run_id: str) -> dict:
    """创建可编辑配置候选与正式修订占用；预览阶段不改变 canonical 和质量结果。"""

    _available(current, plan)
    if (agent_fact_digest(contract) != request.contractHash
            or canonical_sha256(path) != request.technicalPlanSha256):
        raise ValueError("Agent 配置或技术规划已变化，请重新加载后再预览。")
    if path.with_suffix(".md").read_text(encoding="utf-8") != render_project_plan_markdown(plan):
        raise ValueError("技术规划 Markdown 有未同步的修改，请先完成原技术规划的确认。")
    before = editable_settings(contract)
    candidate = apply_settings_patch(plan, request.agentId, request.settingsPatch)
    candidate = rebind_selected_topology(candidate, read_application_config(root))
    after = editable_settings(agent_contract_index(candidate)[request.agentId])
    if not settings_diffs(before, after):
        raise ValueError("Agent 配置没有变化。")
    change_id = f"chg_{uuid4().hex}"
    active = ActiveFormalRevision(changeId=change_id, formalBranch=FormalRevisionBranch.WORKBENCH_PLAN_REVISION,
        sourceThreadId=thread_id, sourceRunId=run_id, request=f"修改智能体 {request.agentId} 的 Prompt／Temperature",
        target=RevisionTarget(type="application", agentId=request.agentId), impactInteractionId=change_id,
        planningThreadId=current.initialization.thread_id or thread_id, planningRunId=run_id,
        status="awaiting_user", currentArtifact=ARTIFACT_KEY)
    markdown = render_project_plan_markdown({**candidate, "confirmation_status": "pending_user_confirmation"})
    try:
        metadata = create_revision_draft(root, change_id=change_id, artifact_key=ARTIFACT_KEY, kind="technical_plan",
            target_id=request.agentId, canonical_json_path=path, markdown=markdown, artifact=candidate,
            based_on_paths=_upstreams(root))
        metadata = metadata.model_copy(update={"hidden": {"before": before,
            "settingsPatch": request.settingsPatch.model_dump(exclude_unset=True),
            "markdownSha256": canonical_sha256(path.with_suffix(".md"))}})
        # 附加预览身份复用现有 metadata.hidden，不增加一份应用配置权威。
        _, _, draft = load_revision_draft(root, change_id=change_id, artifact_key=ARTIFACT_KEY)
        write_revision_draft(root, metadata=metadata, markdown=markdown, artifact=draft)
        updated = write_application_lifecycle(root, current.model_copy(update={
            "revision": current.revision + 1, "updated_at": utc_now(), "active_formal_revision": active,
        }), expected_revision=current.revision)
    except Exception:
        discard_current_revision_draft(root, change_id=change_id, artifact_key=ARTIFACT_KEY)
        raise
    return {"preview": _preview(root, updated, request.agentId)}


def _submit(root: Path, current, request: SubmitSettingsRequest) -> dict:
    """确认只接受预览身份，基线漂移或其他 Agent 的草稿不能被覆盖。"""

    active = current.active_formal_revision
    if (active is None or active.change_id != request.changeId or active.current_artifact != ARTIFACT_KEY
            or (active.target.model_extra or {}).get("agentId") != request.agentId):
        raise ValueError("配置修改预览已失效或不属于当前 Agent，请重新加载。")
    preview = _preview(root, current, request.agentId)
    if preview["draftSha256"] != request.draftSha256 or current.revision != request.lifecycleRevision:
        raise ValueError("配置预览或应用状态已变化，请重新加载预览后确认。")
    updated = current.model_copy(update={"revision": current.revision + 1, "updated_at": utc_now(), "active_formal_revision": None})
    if request.action == "cancel_settings":
        updated = write_application_lifecycle(root, updated, expected_revision=current.revision)
        discard_current_revision_draft(root, change_id=request.changeId, artifact_key=ARTIFACT_KEY)
        return {"cancelled": True, "lifecycle": application_lifecycle_payload(updated)}
    plan, contract, path = _plan(root, request.agentId)
    _available(current, plan, owned_change=request.changeId)
    metadata, markdown, draft = load_revision_draft(root, change_id=request.changeId, artifact_key=ARTIFACT_KEY)
    if (canonical_sha256(path) != metadata.base_canonical_sha256
            or canonical_sha256(path.with_suffix(".md")) != metadata.hidden["markdownSha256"]
            or {ref.artifact_key: ref.sha256 for ref in metadata.based_on_canonical}
            != {key: canonical_sha256(value) for key, value in _upstreams(root).items()}):
        raise ValueError("技术规划、应用配置或上游文档已变化，请放弃当前预览后重新修改。")
    candidate = apply_settings_patch(plan, request.agentId, SettingsPatch.model_validate(metadata.hidden["settingsPatch"]))
    candidate = rebind_selected_topology(candidate, read_application_config(root))
    agent_contract_index(candidate)
    expected = {**candidate, "confirmation_status": "pending_user_confirmation", "basedOn": draft.get("basedOn")}
    if draft != expected or markdown != render_project_plan_markdown({**candidate, "confirmation_status": "pending_user_confirmation"}):
        raise ValueError("配置草稿包含未预览的修改，请放弃后重新生成预览。")
    # 保留首次开发完成证据，只把关联页面的当前实现标记为需重新开发。
    artifacts = deepcopy(updated.development_artifacts)
    agent_progress = artifacts.agents.get(request.agentId)
    if agent_progress and agent_progress.initial_development_status == "completed":
        artifacts.agents[request.agentId] = agent_progress.model_copy(update={"change_status": "changed",
            "change_reason": "智能体配置已修改，请更新实现。", "checked_change_id": request.changeId})
    for page_id in contract["pageIds"]:
        progress = artifacts.pages.get(page_id)
        if progress and progress.initial_development_status == "completed":
            artifacts.pages[page_id] = progress.model_copy(update={"change_status": "changed",
                "change_reason": f"智能体 {contract['name']} 的配置已修改，请通过关联页面更新 Agent 实现。", "checked_change_id": request.changeId})
    updated = updated.model_copy(update={"development_artifacts": artifacts, "extensions": pending_quality_results(updated.extensions)})
    updated = _commit(root, path, candidate, current, updated)
    discard_current_revision_draft(root, change_id=request.changeId, artifact_key=ARTIFACT_KEY)
    return {"saved": True, "agent": {**agent_contract_index(candidate)[request.agentId],
            "contractHash": agent_fact_digest(agent_contract_index(candidate)[request.agentId])},
            "lifecycle": application_lifecycle_payload(updated)}


def _commit(root: Path, path: Path, candidate: dict, current, updated):
    """持有同一应用锁提交 Markdown、JSON 和生命周期，写入异常时恢复原文件。"""

    import json

    markdown_path = path.with_suffix(".md")
    lifecycle_path = application_lifecycle_path(root)
    originals = {item: item.read_text(encoding="utf-8") for item in (path, markdown_path, lifecycle_path)}
    try:
        _write_text_atomically(markdown_path, render_project_plan_markdown(candidate))
        _write_text_atomically(path, json.dumps(candidate, ensure_ascii=False, indent=2) + "\n")
        return write_application_lifecycle(root, updated, expected_revision=current.revision)
    except Exception:
        for item, content in originals.items():
            _write_text_atomically(item, content)
        raise
