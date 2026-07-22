from __future__ import annotations

import asyncio

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.state import ProjectState
from app.domain.application_lifecycle import (
    ArtifactReference,
    ApplicationLifecycleError,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    PendingInteractionType,
    utc_now,
)
from app.persistence.checkpoints import workflow_checkpoint_db_path, workflow_checkpointer
from app.services.application_planning_persistence import confirm_application_planning_artifacts
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    ensure_application_lifecycle,
    load_application_lifecycle,
    persist_pending_interaction_submission,
    persist_application_lifecycle_transition,
)


def _route_start(state: ProjectState) -> str:
    """根据独立创建规划会话的恢复点选择两节点入口。"""

    resume_from = state.get("resume_from")
    return resume_from if resume_from == "project_planning" else "requirements"


def _route_requirements(state: ProjectState) -> str:
    """需求未确认时结束当前轮次，否则进入项目规划。"""

    clarification = state.get("clarification")
    return "await_user_input" if isinstance(clarification, dict) and clarification.get("status") == "requires_user_input" else "project_planning"


def _requirements(state: ProjectState) -> dict:
    """在需求节点前后同步工作区权威生命周期并记录错误。"""

    workspace = _workspace(state)
    lifecycle = _ensure_lifecycle(state)
    try:
        lifecycle = _submit_interaction_if_present(workspace, lifecycle, state)
        if lifecycle.lifecycle.stage in {
            ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
            ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
        }:
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
                active_thread_id=state.get("active_thread_id"),
                active_run_id=state.get("active_run_id"),
            )
        elif (
            lifecycle.lifecycle.stage == ApplicationLifecycleStage.ANALYZING_REQUIREMENT
            and lifecycle.lifecycle.status in {
                ApplicationLifecycleStatus.FAILED,
                ApplicationLifecycleStatus.CANCELLED,
            }
        ):
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
                active_thread_id=state.get("active_thread_id"),
                active_run_id=state.get("active_run_id"),
            )
        elif lifecycle.lifecycle.stage == ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION:
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
                status=ApplicationLifecycleStatus.RUNNING,
                active_thread_id=state.get("active_thread_id"),
                active_run_id=state.get("active_run_id"),
            )
        update = nodes.requirements(state)
        lifecycle = _persist_requirement_result(workspace, update, state)
        return {**update, "lifecycle": application_lifecycle_payload(lifecycle)}
    except asyncio.CancelledError:
        _persist_node_cancelled(workspace, state)
        raise
    except Exception as exc:
        _persist_node_error(workspace, state, exc)
        raise


def _project_planning(state: ProjectState) -> dict:
    """复用项目规划节点，并在用户确认后校验 specs/plans 产物。"""

    workspace = _workspace(state)
    try:
        lifecycle = load_application_lifecycle(workspace) or _ensure_lifecycle(state)
        lifecycle = _submit_interaction_if_present(workspace, lifecycle, state)
        lifecycle = _prepare_project_planning_lifecycle(workspace, lifecycle, state)
        if (
            lifecycle.lifecycle.stage == ApplicationLifecycleStage.GENERATING_PROJECT_PLAN
            and lifecycle.lifecycle.status in {
                ApplicationLifecycleStatus.FAILED,
                ApplicationLifecycleStatus.CANCELLED,
            }
        ):
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
                status=ApplicationLifecycleStatus.RUNNING,
                active_thread_id=state.get("active_thread_id"),
                active_run_id=state.get("active_run_id"),
            )
        if lifecycle.lifecycle.stage in {
            ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
            ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
        }:
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
                status=ApplicationLifecycleStatus.RUNNING,
                active_thread_id=state.get("active_thread_id"),
                active_run_id=state.get("active_run_id"),
            )
        update = nodes.project_planning(state)
        if update.get("status") != "completed":
            lifecycle = persist_application_lifecycle_transition(
                workspace,
                stage=ApplicationLifecycleStage.AWAITING_PROJECT_PLAN_CONFIRMATION,
                status=ApplicationLifecycleStatus.AWAITING_USER,
                pending_type=PendingInteractionType.PROJECT_PLAN_CONFIRMATION,
                pending_payload={"mode": _clarification_mode(update)},
                artifact_refs=_artifact_refs(update, "project_plan"),
                active_thread_id=state.get("active_thread_id"),
                active_run_id=state.get("active_run_id"),
            )
            return {
                **update,
                "workflow_scope": "application_planning",
                "lifecycle": application_lifecycle_payload(lifecycle),
            }
        merged_state = {**state, **update}
        confirmation = confirm_application_planning_artifacts(merged_state)
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            status=ApplicationLifecycleStatus.RUNNING,
            active_thread_id=state.get("active_thread_id"),
            active_run_id=state.get("active_run_id"),
        )
        return {
            **update,
            "workflow_scope": "application_planning",
            "application_planning_confirmation": confirmation,
            "lifecycle": application_lifecycle_payload(lifecycle),
        }
    except asyncio.CancelledError:
        _persist_node_cancelled(workspace, state)
        raise
    except Exception as exc:
        _persist_node_error(workspace, state, exc)
        raise


def _ensure_lifecycle(state: ProjectState):
    """从 Graph State 元数据创建或读取工作区生命周期。"""

    requirement_spec = state.get("requirement_spec")
    app_info = requirement_spec.get("app_info") if isinstance(requirement_spec, dict) else {}
    fallback_name = app_info.get("name") if isinstance(app_info, dict) else None
    application_id = str(state.get("project_id") or _workspace(state).split("/")[-1]).strip()
    application_name = str(state.get("application_name") or fallback_name or application_id).strip()
    return ensure_application_lifecycle(
        _workspace(state),
        application_id=application_id,
        application_name=application_name,
        project_id=application_id,
        active_thread_id=state.get("active_thread_id"),
        active_run_id=state.get("active_run_id"),
    )


def _prepare_project_planning_lifecycle(workspace: str, lifecycle, state: ProjectState):
    """为旧 checkpoint 首次落盘补齐进入项目规划前的合法状态路径。"""

    common = {
        "active_thread_id": state.get("active_thread_id"),
        "active_run_id": state.get("active_run_id"),
    }
    if lifecycle.lifecycle.stage == ApplicationLifecycleStage.COLLECTING_REQUIREMENT:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if lifecycle.lifecycle.stage == ApplicationLifecycleStage.ANALYZING_REQUIREMENT:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if lifecycle.lifecycle.stage == ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            pending_type=PendingInteractionType.REQUIREMENT_CONFIRMATION,
            **common,
        )
    if lifecycle.lifecycle.stage == ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    project_plan = state.get("project_plan")
    if (
        lifecycle.lifecycle.stage == ApplicationLifecycleStage.GENERATING_PROJECT_PLAN
        and isinstance(project_plan, dict)
        and project_plan.get("confirmation_status") in {
            "pending_user_confirmation",
            "confirmed",
        }
    ):
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_PROJECT_PLAN_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            pending_type=PendingInteractionType.PROJECT_PLAN_CONFIRMATION,
            **common,
        )
    return lifecycle


def _persist_requirement_result(workspace: str, update: dict, state: ProjectState):
    """把需求节点结果映射为确定性生命周期阶段。"""

    mode = _clarification_mode(update)
    requirement_spec = update.get("requirement_spec")
    confirmation_status = (
        str(requirement_spec.get("confirmation_status") or "")
        if isinstance(requirement_spec, dict)
        else ""
    )
    common = {
        "active_thread_id": state.get("active_thread_id"),
        "active_run_id": state.get("active_run_id"),
    }
    # 澄清工具的 mode 由协议适配器生成且可能是 ask_user_question，不能用它判断业务阶段。
    if confirmation_status == "pending_user_input":
        return persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            pending_type=PendingInteractionType.REQUIREMENT_CLARIFICATION,
            pending_payload={"mode": mode, "questions": _question_refs(update)},
            **common,
        )
    current = load_application_lifecycle(workspace)
    if current is None:
        raise ValueError("需求节点完成后生命周期状态丢失。")
    if current.lifecycle.stage == ApplicationLifecycleStage.ANALYZING_REQUIREMENT:
        persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if update.get("status") == "completed":
        return persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
            status=ApplicationLifecycleStatus.RUNNING,
            **common,
        )
    if confirmation_status != "pending_user_confirmation":
        raise ValueError(
            "需求节点返回了无法映射的 confirmation_status："
            f"{confirmation_status or '<missing>'}。"
        )
    return persist_application_lifecycle_transition(
        workspace,
        stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
        status=ApplicationLifecycleStatus.AWAITING_USER,
        pending_type=PendingInteractionType.REQUIREMENT_CONFIRMATION,
        pending_payload={"mode": mode},
        artifact_refs=_artifact_refs(update, "requirement_spec"),
        **common,
    )


def _submit_interaction_if_present(workspace: str, lifecycle, state: ProjectState):
    """校验 AG-UI 恢复请求携带的 pending interaction 并发令牌。"""

    submission = state.get("lifecycle_interaction_submission")
    if not isinstance(submission, dict):
        return lifecycle
    interaction_id = str(submission.get("id") or "")
    based_on_revision = submission.get("basedOnRevision")
    if not interaction_id or not isinstance(based_on_revision, int):
        raise ValueError("lifecycle interaction submission 格式无效。")
    return persist_pending_interaction_submission(
        workspace,
        interaction_id=interaction_id,
        based_on_revision=based_on_revision,
    )


def _persist_node_error(workspace: str, state: ProjectState, exc: Exception) -> None:
    """把节点失败记录在当前阶段，避免错误时丢失恢复位置。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        return
    persist_application_lifecycle_transition(
        workspace,
        stage=current.lifecycle.stage,
        status=ApplicationLifecycleStatus.FAILED,
        active_thread_id=state.get("active_thread_id"),
        active_run_id=state.get("active_run_id"),
        error=ApplicationLifecycleError(
            code="application_planning_failed",
            message=str(exc)[:2048] or type(exc).__name__,
            recoverable=True,
            occurredAt=utc_now(),
        ),
    )


def _persist_node_cancelled(workspace: str, state: ProjectState) -> None:
    """记录用户取消但保留同一阶段，供下次显式重试。"""

    current = load_application_lifecycle(workspace)
    if current is None:
        return
    persist_application_lifecycle_transition(
        workspace,
        stage=current.lifecycle.stage,
        status=ApplicationLifecycleStatus.CANCELLED,
        active_thread_id=state.get("active_thread_id"),
        active_run_id=state.get("active_run_id"),
    )


def _artifact_refs(update: dict, kind: str) -> list[ArtifactReference]:
    """从节点结果生成不含正文的正式产物引用。"""

    path = update.get(f"{kind}_path")
    return [ArtifactReference(kind=kind, path=str(path))] if path else []


def _clarification_mode(update: dict) -> str:
    """安全提取节点的待交互模式。"""

    clarification = update.get("clarification")
    return str(clarification.get("mode") or "") if isinstance(clarification, dict) else ""


def _question_refs(update: dict) -> list[dict[str, str]]:
    """仅保留澄清问题稳定 ID 与标题，不复制完整会话。"""

    clarification = update.get("clarification")
    questions = clarification.get("questions") if isinstance(clarification, dict) else []
    return [
        {"id": str(item.get("id") or index), "header": str(item.get("header") or "")}
        for index, item in enumerate(questions or [], start=1)
        if isinstance(item, dict)
    ]


def _workspace(state: ProjectState) -> str:
    """校验并返回创建规划工作区。"""

    workspace = str(state.get("workspace") or "").strip()
    if not workspace:
        raise ValueError("创建应用规划必须提供 workspaceRoot。")
    return workspace


def build_application_planning_graph(*, checkpointer):
    """构建确认项目规划后即结束的创建规划两节点 Graph。"""

    builder = StateGraph(ProjectState)
    builder.add_node("requirements", _requirements)
    builder.add_node("project_planning", _project_planning)
    builder.add_conditional_edges(START, _route_start, {
        "requirements": "requirements",
        "project_planning": "project_planning",
    })
    builder.add_conditional_edges("requirements", _route_requirements, {
        "project_planning": "project_planning",
        "await_user_input": END,
    })
    builder.add_edge("project_planning", END)
    return builder.compile(checkpointer=checkpointer)


_APPLICATION_PLANNING_GRAPHS: dict[str, object] = {}


async def application_planning_graph_for_request(*, workspace: str | None = None, project_id: str | None = None):
    """按工作区复用独立创建规划 Graph 与 SQLite checkpointer。"""

    db_path = workflow_checkpoint_db_path(workspace=workspace, project_id=project_id)
    cache_key = str(db_path)
    if cache_key not in _APPLICATION_PLANNING_GRAPHS:
        _APPLICATION_PLANNING_GRAPHS[cache_key] = build_application_planning_graph(
            checkpointer=await workflow_checkpointer(workspace=workspace, project_id=project_id)
        )
    return _APPLICATION_PLANNING_GRAPHS[cache_key]


def clear_application_planning_graph_cache() -> None:
    """清理创建规划 Graph 缓存，供应用退出时释放资源。"""

    _APPLICATION_PLANNING_GRAPHS.clear()
