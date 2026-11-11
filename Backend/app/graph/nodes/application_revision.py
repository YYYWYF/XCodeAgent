"""主 Workflow 的工作台正式修订草稿入口节点。"""

from __future__ import annotations

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.agents.main.technical_plan_language import validate_technical_plan_chinese_prose

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from app.agents.main.planner import plan_project_with_chat_model
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.domain.application_planning_recovery import application_planning_sha256
from app.services.application_lifecycle import load_application_lifecycle
from app.services.application_config import read_application_config
from app.services.entity_table_binding import bind_plan_entity_tables
from app.services.application_revision_lifecycle import (
    discard_active_revision,
    issue_revision_continuation,
    update_active_revision_progress,
)
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.entity_display_language import validate_entity_display_language
from app.services.data_source_policy import uses_builtin_database
from app.services.page_dependencies import validate_project_plan_dependencies
from app.services.page_implementation_contract import (
    attach_page_implementation_contracts,
    materialize_technical_plan_runtime,
    validate_page_implementation_contracts,
)
from app.services.project_plan import (
    validate_project_plan_datasource_policy,
    validate_technical_plan_api_contracts,
)
from app.services.revision_drafts import (
    confirm_revision_draft,
    create_revision_draft,
    discard_current_revision_draft,
)
from app.workspace.plan_documents import (
    load_project_plan_json,
)
from app.workspace.revision_draft_documents import load_revision_draft
from app.workspace.spec_documents import load_requirement_spec_json, load_ui_designs_json


def start_application_revision(state: ProjectState) -> dict[str, Any]:
    """创建、审阅或确认当前唯一草稿，并用 continuation 衔接 Build 准备链。"""

    workspace = Path(workspace_from_state(state))
    lifecycle = load_application_lifecycle(workspace)
    active = lifecycle.active_formal_revision if lifecycle is not None else None
    if active is None:
        raise ValueError("没有可继续的 active formal revision。")
    interaction = state.get("revision_interaction")
    if isinstance(interaction, dict) and interaction:
        return _handle_revision_interaction(
            workspace,
            active,
            interaction,
            source_execution_run_id=str(state.get("active_run_id") or ""),
        )
    if active.status == "building":
        # TechnicalPlan 确认后的 continuation 已由另一条前端开发会话消费；
        # 此时只允许进入共同 DAG 链，不能再次生成同一份草稿。
        return _revision_artifacts_confirmed(workspace, active.change_id)
    if active.formal_branch.value == "design_stage_revision":
        return _continue_design_revision(workspace, active)
    draft = _create_technical_plan_draft(workspace, active)
    return _await_revision_draft(workspace, active.change_id, draft)


def _await_revision_draft(
    workspace: Path,
    change_id: str,
    draft: dict[str, Any],
    *,
    remaining_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    """登记当前 TechnicalPlan 草稿，并以技术规划节点语义返回确认卡。"""

    # 正文进入统一文档引用链路；确认信息只保留版本身份，不复制整份计划。
    review = {key: value for key, value in draft.items() if key not in {"technicalPlan", "productPlan", "baseline"}}
    update_active_revision_progress(
        workspace,
        change_id=change_id,
        status="awaiting_user",
        current_artifact=str(draft["artifactKey"]),
        remaining_artifacts=remaining_artifacts,
    )
    clarification = {
        "mode": "revision_draft_confirmation",
        "status": "requires_user_input",
        "message": "技术规划已重新生成，请审阅当前草稿；确认后才更新正式版本。",
        "revisionDraft": review,
        "questions": [
            {
                "id": "revision_draft_confirmation",
                "header": "正式产物草稿",
                "question": "请确认重新生成的 TechnicalPlan，或放弃本次修改。",
                "type": "single",
                "options": [
                    {"label": "确认当前版本", "value": "confirm"},
                    {"label": "放弃本次修改", "value": "discard"},
                ],
            }
        ],
    }
    return {
        "phase": "technical_planning",
        "status": "requires_user_input",
        "node_name": "technical_planning",
        "change_id": change_id,
        "revision_draft": review,
        "technical_plan": draft["technicalPlan"],
        "technical_plan_baseline": draft.get("baseline", {}),
        "product_plan": draft["productPlan"],
        "clarification": clarification,
        "timeline": ["technical_planning"],
    }


def _handle_revision_interaction(
    workspace: Path,
    active: Any,
    interaction: dict[str, Any],
    *,
    source_execution_run_id: str,
) -> dict[str, Any]:
    """执行当前草稿的修订、确认或放弃，确认前不覆盖正式产物。"""

    artifact_key = str(interaction.get("artifact_key") or "")
    action = str(interaction.get("action") or "")
    if action == "discard":
        discard_current_revision_draft(
            workspace,
            change_id=active.change_id,
            artifact_key=artifact_key,
        )
        discard_active_revision(workspace, change_id=active.change_id)
        return {
            "phase": "application_revision",
            "status": "discarded",
            "change_id": active.change_id,
            "revision_draft": {},
            "clarification": {},
            "timeline": ["application_revision"],
        }
    if action == "revise":
        if artifact_key != "technical-plan":
            raise ValueError("当前合同只允许修订 TechnicalPlan。")
        # 当前草稿同时作为下一轮编辑和差异审阅的基线，保留前面各轮修改。
        _, previous_draft = load_revision_draft(
            workspace, change_id=active.change_id, artifact_key=artifact_key,
        )
        feedback = str(interaction.get("feedback") or "").strip()
        revision_request = f"{active.request}\n\n本轮草稿修改意见：{feedback}"
        # 生成及完整校验成功后才写入新草稿；失败时旧稿及其元数据均保留。
        draft = _create_technical_plan_draft(
            workspace,
            active,
            revision_request=revision_request,
            existing_plan=previous_draft,
        )
        return _await_revision_draft(
            workspace,
            active.change_id,
            draft,
            remaining_artifacts=list(active.remaining_artifacts),
        )
    if action != "confirm":
        raise ValueError("不支持的 revision draft action。")
    if artifact_key != "technical-plan":
        raise ValueError("当前合同只允许确认 TechnicalPlan 草稿。")
    _confirm_current_draft(workspace, active, artifact_key)
    return _revision_continuation_ready(
        workspace,
        active.change_id,
        source_execution_run_id=source_execution_run_id,
    )


def _continue_design_revision(workspace: Path, active: Any) -> dict[str, Any]:
    """消费设计阶段已确认的 TechnicalPlan 后直接进入当前 Build 准备链。"""

    return _revision_artifacts_confirmed(workspace, active.change_id)


def _revision_continuation_ready(
    workspace: Path,
    change_id: str,
    *,
    source_execution_run_id: str,
) -> dict[str, Any]:
    """确认工作台 TechnicalPlan 后停图，并把 DAG continuation 交给开发会话。"""

    technical_plan_path = workspace / WORKSPACE_ARTIFACT_DIR / "plans" / "technical-plan.json"
    token, active = issue_revision_continuation(
        workspace,
        change_id=change_id,
        technical_plan_path=technical_plan_path,
        source_execution_run_id=source_execution_run_id,
    )
    return {
        **_current_plan_state(workspace),
        "phase": "application_revision",
        "status": "revision_continuation_ready",
        "change_id": change_id,
        "revision_draft": {},
        "revision_continuation": {
            "changeId": active.change_id,
            "formalBranch": active.formal_branch.value,
            "action": "continue_revision_build",
            "token": token,
            "technicalPlanSha256": active.technical_plan_sha256,
        },
        "clarification": {},
        "timeline": ["application_revision"],
    }


def _revision_artifacts_confirmed(workspace: Path, change_id: str) -> dict[str, Any]:
    """标记正式产物闭包已确认，让 Graph 唯一地路由到 inspect_workspace。"""

    update_active_revision_progress(
        workspace,
        change_id=change_id,
        status="building",
        current_artifact=None,
        remaining_artifacts=[],
    )
    return {
        **_current_plan_state(workspace),
        "phase": "application_revision",
        "status": "revision_artifacts_confirmed",
        "change_id": change_id,
        "revision_draft": {},
        "clarification": {},
        "timeline": ["application_revision"],
    }


def _create_technical_plan_draft(
    workspace: Path,
    active: Any,
    *,
    revision_request: str | None = None,
    existing_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """基于当前草稿或首次修订的正式版本生成候选，完整校验后替换待确认草稿。"""

    plans = workspace / WORKSPACE_ARTIFACT_DIR / "plans"
    specs = workspace / WORKSPACE_ARTIFACT_DIR / "specs"
    canonical_json = plans / "technical-plan.json"
    existing = deepcopy(existing_plan) if existing_plan is not None else _load_json_object(canonical_json)
    requirement_spec = load_requirement_spec_json(specs / "requirement-spec.json")
    product_plan = load_project_plan_json(plans / "product-plan.json")
    ui_designs = load_ui_designs_json(specs / "ui-designs.json")
    if not requirement_spec or not product_plan:
        raise ValueError("TechnicalPlan revision 缺少已确认 RequirementSpec/ProductPlan。")
    technical_input = {
        **requirement_spec,
        "pages": product_plan.get("pages", requirement_spec.get("pages", [])),
        "confirmed_product_plan": product_plan,
        "application_config": read_application_config(workspace),
        "planning_adjustment_request": revision_request or active.request,
    }
    artifact = plan_project_with_chat_model(
        technical_input,
        # 编辑器可以修改候选，审阅基线必须保留修改前的独立快照。
        existing_plan=deepcopy(existing),
    )
    artifact = bind_plan_entity_tables(artifact, technical_input["application_config"])
    artifact = attach_page_implementation_contracts(artifact, product_plan, ui_designs)
    artifact["confirmation_status"] = "pending_user_confirmation"
    _validate_technical_plan(artifact, requirement_spec, product_plan, ui_designs, technical_input["application_config"])
    based_on_paths = {
        "product-plan": plans / "product-plan.json",
    }
    create_revision_draft(
        workspace,
        change_id=active.change_id,
        artifact_key="technical-plan",
        kind="technical_plan",
        target_id="application",
        canonical_json_path=canonical_json,
        artifact=artifact,
        based_on_paths=based_on_paths,
    )
    metadata, artifact = load_revision_draft(
        workspace, change_id=active.change_id, artifact_key="technical-plan",
    )
    return {**_draft_projection(metadata.artifact_key, artifact, product_plan, metadata), "baseline": existing}


def _confirm_current_draft(workspace: Path, active: Any, artifact_key: str) -> None:
    """按当前 TechnicalPlan 合同重新编译依赖、校验并原子覆盖正式数据。"""

    plans = workspace / WORKSPACE_ARTIFACT_DIR / "plans"
    specs = workspace / WORKSPACE_ARTIFACT_DIR / "specs"
    if artifact_key != "technical-plan":
        raise ValueError("当前合同只允许确认 TechnicalPlan 草稿。")
    requirement_spec = load_requirement_spec_json(specs / "requirement-spec.json")
    product_plan = load_project_plan_json(plans / "product-plan.json")
    ui_designs = load_ui_designs_json(specs / "ui-designs.json")
    if not requirement_spec or not product_plan:
        raise ValueError("TechnicalPlan 确认缺少已确认上游。")

    def prepare(artifact: dict[str, Any]) -> dict[str, Any]:
        """以当前应用配置及已确认上游重新编译结构化草稿。"""

        artifact.pop("basedOn", None)
        synchronized = bind_plan_entity_tables(artifact, read_application_config(workspace))
        return attach_page_implementation_contracts(
            synchronized,
            product_plan,
            ui_designs,
        )

    confirm_revision_draft(
        workspace,
        change_id=active.change_id,
        artifact_key=artifact_key,
        canonical_json_path=plans / "technical-plan.json",
        based_on_paths={
            "product-plan": plans / "product-plan.json",
        },
        prepare_artifact=prepare,
        validate_artifact=lambda artifact: _validate_technical_plan(
            artifact,
            requirement_spec,
            product_plan,
            ui_designs,
            read_application_config(workspace),
        ),
    )


def _validate_technical_plan(
    artifact: dict[str, Any],
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
    ui_designs: dict[str, Any],
    application: dict[str, Any],
) -> None:
    """复用当前 TechnicalPlan/PIC 校验集合，任何错误都阻止 canonical 提交。"""

    runtime = materialize_technical_plan_runtime(
        artifact,
        requirement_spec,
        product_plan,
        ui_designs,
    )
    errors = [
        *validate_project_plan_dependencies(runtime),
        *validate_api_contract_consistency(runtime),
        *validate_project_plan_datasource_policy(runtime),
        *validate_technical_plan_api_contracts(artifact),
        *validate_technical_plan_chinese_prose(artifact),
        *validate_entity_display_language(artifact, builtin=uses_builtin_database(application)),
        *validate_page_implementation_contracts(artifact, product_plan, ui_designs),
    ]
    if errors:
        raise ValueError("TechnicalPlan revision 校验失败：" + "；".join(errors[:12]))


def _current_plan_state(workspace: Path) -> dict[str, Any]:
    """重新加载刚确认的 canonical，避免后续 Build 使用请求开始前的旧快照。"""

    plans = workspace / WORKSPACE_ARTIFACT_DIR / "plans"
    specs = workspace / WORKSPACE_ARTIFACT_DIR / "specs"
    technical_path = plans / "technical-plan.json"
    technical_plan = load_project_plan_json(technical_path)
    requirement_spec = load_requirement_spec_json(specs / "requirement-spec.json")
    product_plan = load_project_plan_json(plans / "product-plan.json")
    ui_designs = load_ui_designs_json(specs / "ui-designs.json")
    if not isinstance(technical_plan, dict) or not product_plan:
        raise ValueError("正式产物收口后无法重新投影 TechnicalPlan。")
    runtime = materialize_technical_plan_runtime(
        technical_plan,
        requirement_spec,
        product_plan,
        ui_designs,
    )
    return {
        "technical_plan": technical_plan,
        "technical_plan_json_path": str(technical_path),
        "project_plan": runtime,
        "project_plan_path": str(technical_path),
        "project_plan_json_path": str(technical_path),
        "pages": [page for page in runtime.get("pages", []) if isinstance(page, dict)],
    }


def _draft_projection(
    artifact_key: str, artifact: dict[str, Any], product_plan: dict[str, Any], metadata: Any,
) -> dict[str, Any]:
    """构造供可视化审阅的结构化草稿，确认哈希绑定服务端当前候选。"""

    return {
        "artifactKey": artifact_key,
        "technicalPlan": artifact,
        "productPlan": product_plan,
        "draftSha256": application_planning_sha256(artifact),
        "basedOn": [
            item.model_dump(mode="json", by_alias=True)
            for item in metadata.based_on_canonical
        ],
        "status": "pending_user_confirmation",
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    """严格读取一个当前 canonical JSON 对象。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"正式产物无法读取：{path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"正式产物必须是 JSON 对象：{path}")
    return value
