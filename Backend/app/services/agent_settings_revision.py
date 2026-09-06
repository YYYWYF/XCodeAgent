"""Agent Settings 可视化修改的严格请求、Contract 重编译与正式确认服务。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.services.agent_development_readiness import agent_contract_sha256
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    application_lifecycle_payload,
    load_application_lifecycle,
)
from app.services.application_revision_lifecycle import (
    discard_active_revision,
    start_agent_settings_revision,
    update_active_revision_progress,
)
from app.services.project_plan import (
    recompile_technical_plan_agent_settings,
    validate_technical_plan_agent_contracts,
)
from app.services.revision_drafts import (
    confirm_revision_draft,
    create_revision_draft,
    discard_current_revision_draft,
)
from app.workspace.plan_documents import load_project_plan_json, render_project_plan_markdown
from app.workspace.revision_draft_documents import load_revision_draft
from app.services.agent_settings_revision_models import (
    AbandonAgentSettingsRevisionRequest,
    AgentSettingsRevisionRequest,
    ConfirmAgentSettingsRevisionRequest,
    GetAgentSettingsRevisionRequest,
    PrepareAgentSettingsRevisionRequest,
    parse_agent_settings_revision_request,
)
from app.services.agent_settings_revision_support import (
    AGENT_SETTINGS_ARTIFACT_KEY,
    agent_contract,
    agent_settings,
    artifact_paths,
    document_sha256,
    invalidate_old_agent_build_plan,
    lifecycle_revision,
    load_confirmed_plan,
    preview_payload,
    required_lifecycle,
    settings_field_diffs,
)


def execute_agent_settings_revision(
    request: AgentSettingsRevisionRequest,
    *,
    source_thread_id: str,
    source_run_id: str,
) -> tuple[dict[str, Any], str]:
    """执行查询、预览、确认或放弃，并返回安全的 AG-UI 业务投影。"""

    if isinstance(request, PrepareAgentSettingsRevisionRequest):
        return _prepare_revision(
            request,
            source_thread_id=source_thread_id,
            source_run_id=source_run_id,
        )
    if isinstance(request, ConfirmAgentSettingsRevisionRequest):
        return _confirm_revision(request)
    if isinstance(request, AbandonAgentSettingsRevisionRequest):
        return _abandon_revision(request)
    return _get_revision(request)


def _prepare_revision(
    request: PrepareAgentSettingsRevisionRequest,
    *,
    source_thread_id: str,
    source_run_id: str,
) -> tuple[dict[str, Any], str]:
    """编译候选 TechnicalPlan，创建 revision draft 并返回字段级预览。"""

    root, product_path, technical_path = artifact_paths(request.workspace_root)
    product_plan = load_confirmed_plan(product_path, label="ProductPlan")
    technical_plan = load_confirmed_plan(technical_path, label="TechnicalPlan")
    old_contract = agent_contract(technical_plan, request.agent_id)
    if agent_contract_sha256(old_contract) != request.based_on_contract_hash:
        raise ValueError("Agent Contract 已变化，请重新加载后再修改。")
    if document_sha256(technical_path) != request.based_on_technical_plan_sha256:
        raise ValueError("TechnicalPlan 已变化，请重新加载后再修改。")

    patch = request.settings_patch
    prompt = (
        patch.prompt.model_dump(mode="python", by_alias=True)
        if patch.prompt is not None
        else None
    )
    temperature = patch.model.generation.temperature if patch.model is not None else None
    candidate_plan = recompile_technical_plan_agent_settings(
        technical_plan,
        product_plan,
        agent_id=request.agent_id,
        prompt=prompt,
        temperature=temperature,
    )
    candidate_contract = agent_contract(candidate_plan, request.agent_id)
    if candidate_contract == old_contract:
        raise ValueError("Agent Settings 没有产生可保存的修改。")
    diffs = settings_field_diffs(old_contract, candidate_contract)
    active = start_agent_settings_revision(
        root,
        agent_id=request.agent_id,
        source_thread_id=source_thread_id,
        source_run_id=source_run_id,
    )
    try:
        markdown = render_project_plan_markdown(candidate_plan)
        metadata = create_revision_draft(
            root,
            change_id=active.change_id,
            artifact_key=AGENT_SETTINGS_ARTIFACT_KEY,
            kind="technical_plan",
            target_id=request.agent_id,
            canonical_json_path=technical_path,
            markdown=markdown,
            artifact=candidate_plan,
            based_on_paths={"product-plan": product_path},
            hidden={
                "agentId": request.agent_id,
                "basedOnContractHash": request.based_on_contract_hash,
                "candidateContractHash": agent_contract_sha256(candidate_contract),
                "changedSections": list(request.changed_sections),
                "fieldDiffs": diffs,
            },
        )
        update_active_revision_progress(
            root,
            change_id=active.change_id,
            status="awaiting_user",
            current_artifact=AGENT_SETTINGS_ARTIFACT_KEY,
        )
    except Exception:
        try:
            discard_current_revision_draft(
                root,
                change_id=active.change_id,
                artifact_key=AGENT_SETTINGS_ARTIFACT_KEY,
            )
            discard_active_revision(root, change_id=active.change_id)
        except Exception:
            pass
        raise
    return (
        preview_payload(
            metadata=metadata,
            markdown=markdown,
            lifecycle_revision=lifecycle_revision(root),
            hidden=metadata.hidden,
            settings=agent_settings(candidate_contract),
        ),
        "Agent Settings 修改预览已生成，请确认后应用。",
    )


def _confirm_revision(
    request: ConfirmAgentSettingsRevisionRequest,
) -> tuple[dict[str, Any], str]:
    """复验 revision draft 并原子更新正式 TechnicalPlan Markdown 与 JSON。"""

    root, product_path, technical_path = artifact_paths(request.workspace_root)
    lifecycle = _matching_active_lifecycle(
        root,
        agent_id=request.agent_id,
        change_id=request.change_id,
        based_on_revision=request.based_on_lifecycle_revision,
    )
    metadata, markdown, _artifact = load_revision_draft(
        root,
        change_id=request.change_id,
        artifact_key=AGENT_SETTINGS_ARTIFACT_KEY,
    )
    if hashlib.sha256(markdown.encode("utf-8")).hexdigest() != request.draft_sha256:
        raise ValueError("Agent Settings 修改预览已变化，请重新加载。")
    product_plan = load_confirmed_plan(product_path, label="ProductPlan")

    def validate_artifact(value: dict[str, Any]) -> None:
        """确认前复验完整 TechnicalPlan 与当前 ProductPlan 的闭合关系。"""

        if value.get("artifact_type") != "technical-plan":
            raise ValueError("Agent Settings draft 不是有效 TechnicalPlan。")
        errors = validate_technical_plan_agent_contracts(value, product_plan)
        if errors:
            raise ValueError("；".join(errors))

    result = confirm_revision_draft(
        root,
        change_id=request.change_id,
        artifact_key=AGENT_SETTINGS_ARTIFACT_KEY,
        canonical_markdown_path=technical_path.with_suffix(".md"),
        canonical_json_path=technical_path,
        based_on_paths={"product-plan": product_path},
        synchronize_markdown=lambda _markdown, artifact: artifact,
        validate_artifact=validate_artifact,
    )
    confirmed_plan = result["artifact"]
    confirmed_contract = agent_contract(confirmed_plan, request.agent_id)
    invalidated = invalidate_old_agent_build_plan(
        root,
        agent_id=request.agent_id,
        old_contract_hash=str(metadata.hidden.get("basedOnContractHash") or ""),
    )
    discard_active_revision(root, change_id=request.change_id)
    return (
        {
            "action": request.action,
            "active": False,
            "changeId": request.change_id,
            "agentId": request.agent_id,
            "contractHash": agent_contract_sha256(confirmed_contract),
            "technicalPlanSha256": document_sha256(technical_path),
            "agentSettings": agent_settings(confirmed_contract),
            "invalidatedArtifacts": invalidated,
            "previousLifecycleRevision": lifecycle.revision,
            "lifecycle": application_lifecycle_payload(
                required_lifecycle(root)
            ),
        },
        "Agent Settings 已应用，请重新 Build 当前智能体。",
    )


def _abandon_revision(
    request: AbandonAgentSettingsRevisionRequest,
) -> tuple[dict[str, Any], str]:
    """删除当前 revision draft 并释放正式修订 lease。"""

    root, _product_path, _technical_path = artifact_paths(request.workspace_root)
    _matching_active_lifecycle(
        root,
        agent_id=request.agent_id,
        change_id=request.change_id,
        based_on_revision=request.based_on_lifecycle_revision,
    )
    _metadata, markdown, _artifact = load_revision_draft(
        root,
        change_id=request.change_id,
        artifact_key=AGENT_SETTINGS_ARTIFACT_KEY,
    )
    if hashlib.sha256(markdown.encode("utf-8")).hexdigest() != request.draft_sha256:
        raise ValueError("Agent Settings 修改预览已变化，请重新加载。")
    discard_current_revision_draft(
        root,
        change_id=request.change_id,
        artifact_key=AGENT_SETTINGS_ARTIFACT_KEY,
    )
    discard_active_revision(root, change_id=request.change_id)
    return (
        {
            "action": request.action,
            "active": False,
            "changeId": request.change_id,
            "agentId": request.agent_id,
            "lifecycle": application_lifecycle_payload(required_lifecycle(root)),
        },
        "已放弃 Agent Settings 修改，正式配置保持不变。",
    )


def _get_revision(
    request: GetAgentSettingsRevisionRequest,
) -> tuple[dict[str, Any], str]:
    """返回当前正式身份，并恢复同一 Agent 尚未确认的修改预览。"""

    root, _product_path, technical_path = artifact_paths(request.workspace_root)
    technical_plan = load_confirmed_plan(technical_path, label="TechnicalPlan")
    current_contract = agent_contract(technical_plan, request.agent_id)
    current_identity = {
        "contractHash": agent_contract_sha256(current_contract),
        "technicalPlanSha256": document_sha256(technical_path),
    }
    lifecycle = required_lifecycle(root)
    active = lifecycle.active_formal_revision
    if (
        active is None
        or active.target.type != "agent"
        or active.target.agent_id != request.agent_id
        or active.current_artifact != AGENT_SETTINGS_ARTIFACT_KEY
    ):
        return (
            {
                "action": request.action,
                "active": False,
                "agentId": request.agent_id,
                **current_identity,
                "lifecycle": application_lifecycle_payload(lifecycle),
            },
            "当前智能体没有待确认的 Settings 修改。",
        )
    metadata, markdown, artifact = load_revision_draft(
        root,
        change_id=active.change_id,
        artifact_key=AGENT_SETTINGS_ARTIFACT_KEY,
    )
    contract = agent_contract(artifact, request.agent_id)
    payload = preview_payload(
        metadata=metadata,
        markdown=markdown,
        lifecycle_revision=lifecycle.revision,
        hidden=metadata.hidden,
        settings=agent_settings(contract),
    )
    payload.update(current_identity)
    return (
        payload,
        "已恢复待确认的 Agent Settings 修改预览。",
    )


def _matching_active_lifecycle(
    root: Path,
    *,
    agent_id: str,
    change_id: str,
    based_on_revision: int,
):
    """校验当前 lifecycle、revision 和 Agent 目标与提交完全一致。"""

    lifecycle = required_lifecycle(root)
    active = lifecycle.active_formal_revision
    if lifecycle.revision != based_on_revision:
        raise ApplicationLifecycleConflictError("Agent Settings 修改基于过期 lifecycle revision。")
    if (
        active is None
        or active.change_id != change_id
        or active.target.type != "agent"
        or active.target.agent_id != agent_id
        or active.current_artifact != AGENT_SETTINGS_ARTIFACT_KEY
        or active.status != "awaiting_user"
    ):
        raise ApplicationLifecycleConflictError("Agent Settings 修改预览已过期或目标不匹配。")
    return lifecycle
