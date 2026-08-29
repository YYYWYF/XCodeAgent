"""执行 LangGraph 主工作流并协调其对外 AG-UI 生命周期。"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, AsyncIterator
from urllib.parse import urlencode
from uuid import uuid4

from ag_ui.core import (
    CustomEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi.encoders import jsonable_encoder
from langgraph.types import Command

from app.protocols.application_planning_interrupt import (
    application_planning_interrupt_from_snapshot,
    project_application_planning_interrupt,
)
from app.protocols.workflow.projection import (
    _public_workflow_state,
    _workflow_artifacts,
    _workflow_code_review_repair,
    _workflow_event,
    _workflow_next_nodes,
    _workflow_node_detail,
    _workflow_node_label,
    _workflow_start_node,
    _workflow_summary,
    _workflow_visual_payload,
)
from app.protocols.workflow.request import workflow_run_inputs
from app.protocols.workflow.lifecycle import (
    begin_workflow_lifecycle,
    fail_workflow_lifecycle,
    project_workflow_lifecycle_boundary,
    stop_workflow_lifecycle,
)
from app.protocols.workflow.run_control import (
    build_workflow_plan_control_ag_ui_stream,
    build_workflow_cancellation_ag_ui_stream,
    workflow_run_registry,
)
from app.protocols.workflow.stream_events import (
    integration_test_check_summary,
    integration_test_checks,
    _message_process_frames,
    _pending_tool_frames,
    _process_frame,
    _text_delta_frames,
    _tool_result_frames,
    _workflow_ag_ui_frames,
)
from app.config import Settings
from app.domain.application_planning_interaction import ApplicationPlanningInteraction
from app.graph.application_planning_interrupts import (
    validate_application_planning_review_action,
)
from app.graph.application_planning_revision import cleared_design_change_context
from app.persistence.checkpoints import cleanup_workflow_checkpoints
from app.services.user_skill_runtime import validate_selected_user_skills
from app.workspace.run_lease import WorkspaceRunLease, workspace_run_leases


_APPLICATION_PLANNING_RESUME_LOCKS: dict[str, asyncio.Lock] = {}


def _graph_stream_supports_subgraphs(graph: Any) -> bool:
    """判断 Graph 流是否支持子图命名空间参数，并兼容测试中的轻量假 Graph。"""

    try:
        parameters = inspect.signature(graph.astream).parameters.values()
    except (TypeError, ValueError, AttributeError):
        return False
    return any(
        parameter.name == "subgraphs"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _workflow_stream_chunk(item: Any) -> tuple[tuple[str, ...], str, Any]:
    """统一解析普通 Graph 与开启子图命名空间后的流式记录。"""

    if isinstance(item, tuple) and len(item) == 3:
        namespace, stream_mode, chunk = item
        normalized_namespace = (
            tuple(str(part) for part in namespace)
            if isinstance(namespace, tuple)
            else tuple()
        )
        return normalized_namespace, str(stream_mode), chunk
    if isinstance(item, tuple) and len(item) == 2:
        stream_mode, chunk = item
        return tuple(), str(stream_mode), chunk
    return tuple(), "", item


def _application_planning_resume_lock(thread_id: str) -> asyncio.Lock:
    """返回指定创建规划 thread 的进程内恢复锁。"""

    lock = _APPLICATION_PLANNING_RESUME_LOCKS.get(thread_id)
    if lock is None:
        # 单进程事件循环内创建锁不需要额外互斥；不同 thread 会得到不同锁并行执行。
        lock = asyncio.Lock()
        _APPLICATION_PLANNING_RESUME_LOCKS[thread_id] = lock
    return lock


def _validate_application_planning_resume(
    snapshot: Any,
    interaction: dict[str, Any],
) -> dict[str, Any]:
    """在产生任何恢复投影前校验提交是否仍匹配 checkpoint 的当前中断。"""

    pending = application_planning_interrupt_from_snapshot(snapshot)
    if pending is None:
        raise ValueError("当前创建规划线程没有可恢复的审阅中断。")

    submitted_gate_id = str(
        interaction.get("gate_id") or interaction.get("gateId") or ""
    )
    submitted_artifact = str(interaction.get("artifact") or "")
    submitted_revision = str(
        interaction.get("artifact_revision")
        or interaction.get("artifactRevision")
        or ""
    )
    if submitted_gate_id != str(pending.get("gateId") or ""):
        raise ValueError("提交的创建规划确认卡已经过期，请刷新后重试。")
    if submitted_artifact != str(pending.get("artifact") or ""):
        raise ValueError("创建规划交互与当前待确认产物不匹配。")
    if submitted_revision != str(pending.get("artifactRevision") or ""):
        raise ValueError("待确认产物已经更新，请基于最新版本重新提交。")
    # 动作组合必须在 Command(resume=...) 前完成校验。LangGraph 会缓存已送入
    # interrupt 的 resume 值；如果节点恢复后才抛错，下一次重试仍会复用旧动作。
    submission = ApplicationPlanningInteraction.model_validate(interaction)
    node_name = str(pending.get("phase") or "")
    validation_state = dict(snapshot.values)
    pending_clarification = pending.get("clarification")
    if isinstance(pending_clarification, dict):
        validation_state["clarification"] = pending_clarification
    validate_application_planning_review_action(
        validation_state,
        node_name,
        submission,
    )
    return pending


def _next_node_attempt(node_attempts: dict[str, int], node_name: str) -> int:
    """登记节点新一轮执行并返回从一开始的 attempt。"""

    attempt = node_attempts.get(node_name, 0) + 1
    node_attempts[node_name] = attempt
    return attempt


def _current_node_attempt(node_attempts: dict[str, int], node_name: str) -> int:
    """读取节点当前轮次，兼容缺失 started 事件的更新流。"""

    return node_attempts.get(node_name) or _next_node_attempt(node_attempts, node_name)


def _process_step_id(node_name: str, attempt: int) -> str:
    """首次执行沿用旧步骤 ID，后续轮次添加唯一 attempt 后缀。"""

    return f"workflow:{node_name}" if attempt == 1 else f"workflow:{node_name}:{attempt}"


def _iteration_kind(node_name: str, attempt: int) -> str:
    """为构建和测试轮次生成稳定的语义标签。"""

    if node_name == "build":
        return "initial_build" if attempt == 1 else "repair_build"
    if node_name == "integration_test":
        return "initial_test" if attempt == 1 else "retest"
    if node_name == "unit_test":
        return "initial_unit_test" if attempt == 1 else "unit_retest"
    if node_name == "unit_test_repair":
        return "initial_unit_repair" if attempt == 1 else "unit_repair_retest"
    return "initial"


def _terminal_process_status(node_name: str, update: dict[str, Any]) -> str:
    """按节点真实业务结果计算步骤终态，避免把门禁失败显示为完成。"""

    if update.get("status") == "requires_user_input":
        return "requires_user_input"
    if update.get("status") == "failed" or node_name == "handle_failure":
        return "failed"
    if node_name == "build":
        summary = update.get("build_summary")
        summary_status = summary.get("status") if isinstance(summary, dict) else None
        if summary_status == "requires_confirmation":
            return "requires_user_input"
        if summary_status != "completed":
            return "failed"
    if node_name == "integration_test" and update.get("quality_gate_passed") is not True:
        return "failed"
    if (
        node_name == "unit_test"
        and update.get("unit_test_next_action") == "unit_test_repair"
    ):
        return "failed"
    return "completed"


def _terminal_process_title(node_name: str, status: str) -> str:
    """按步骤终态生成一致的中文标题。"""

    prefix = {
        "completed": "已完成",
        "failed": "执行失败",
        "requires_user_input": "等待确认",
    }.get(status, "已完成")
    return f"{prefix} {_workflow_node_label(node_name)}"


def _runtime_node_label(node_name: str, state: dict[str, Any]) -> str:
    """按本次运行目标动态生成节点展示名称，保持内部节点 id 稳定。"""

    detail_target_type = str(state.get("detail_target_type") or "")
    if node_name == "entity_source_binding":
        return "实体数据源绑定"
    return _workflow_node_label(node_name)


def _application_planning_resume_node(interaction: Any) -> str:
    """按显式中断交互选择本轮首个展示节点，禁止从旧快照猜测。"""

    if not isinstance(interaction, dict):
        return ""
    if interaction.get("action") == "design_change":
        return "design_intent_analysis"
    if interaction.get("action") == "enter_planning":
        return "planning_stage_entry"
    return {
        "requirement_spec": "requirements",
        "product_plan": "product_planning",
        "ui_designs": "ui_confirmation",
        "technical_plan": "technical_planning",
    }.get(str(interaction.get("artifact") or ""), "")


def _mark_started_artifact_revision(
    state: dict[str, Any],
    interaction: dict[str, Any],
) -> None:
    """在审阅门恢复首帧标记真实修订，且只按 checkpoint 中既有产物判定文案。"""

    node_name = _application_planning_resume_node(interaction)
    if not node_name:
        return
    state["design_change_submission"] = True
    state["design_change_target"] = node_name
    state["design_change_reason"] = str(interaction.get("request") or "").strip()
    state["design_change_existing_artifacts"] = {
        "requirements": bool(
            state.get("requirement_spec") or state.get("requirement_spec_path")
        ),
        "product_planning": bool(
            state.get("product_plan") or state.get("product_plan_path")
        ),
        "ui_confirmation": bool(state.get("ui_designs")),
        "technical_planning": bool(
            state.get("technical_plan") or state.get("technical_plan_path")
        ),
    }
    if node_name == "requirements":
        # 修订运行的 started 快照也必须立即撤销旧需求确认，避免生成流尚未进入节点时仍展示旧文档。
        state["requirements_confirmed"] = False
        state["requirement_spec_path"] = ""
        state["requirement_spec_json_path"] = ""


def _runtime_terminal_process_title(
    node_name: str,
    status: str,
    state: dict[str, Any],
) -> str:
    """按步骤终态和动态节点名生成流程标题。"""

    prefix = {
        "completed": "已完成",
        "failed": "执行失败",
        "requires_user_input": "等待确认",
    }.get(status, "已完成")
    return f"{prefix} {_runtime_node_label(node_name, state)}"


def build_workflow_ag_ui_stream(
    *,
    graph: Any,
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    """以 AG-UI SSE 事件流运行或取消一次主工作流请求。"""

    encoder = EventEncoder(accept or "text/event-stream")
    workflow_inputs = workflow_run_inputs(payload)
    thread_id = workflow_inputs["thread_id"] or str(uuid4())
    run_id = workflow_inputs["run_id"] or f"workflow-{uuid4().hex[:12]}"
    plan_control_action = workflow_inputs.get("plan_control_action") or ""
    if plan_control_action:
        return build_workflow_plan_control_ag_ui_stream(
            action=plan_control_action,
            workspace=workflow_inputs["workspace"] or "",
            target_run_id=workflow_inputs.get("plan_control_run_id") or "",
            thread_id=thread_id,
            run_id=run_id,
            accept=accept,
        )
    cancel_run_id = workflow_inputs["cancel_run_id"]
    if cancel_run_id:
        # 取消请求复用同一接口，但不会因此启动第二个 Graph 运行。
        return build_workflow_cancellation_ag_ui_stream(
            thread_id=thread_id,
            run_id=run_id,
            target_run_id=cancel_run_id,
            accept=accept,
        )
    message_id = str(uuid4())

    async def stream() -> AsyncIterator[str]:
        events: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        workspace_lease: WorkspaceRunLease | None = None
        workspace: str | None = None
        lifecycle_payload: dict[str, Any] | None = None
        workflow_scope = workflow_inputs.get("workflow_scope") or None
        current_phase = "development_readiness_gate"
        node_attempts: dict[str, int] = {}
        application_planning_resume_lock: asyncio.Lock | None = None
        application_planning_resume_lock_acquired = False
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Workflow stream must run inside an asyncio task.")
        workflow_run_registry.register(run_id, task)

        yield encoder.encode(RunStartedEvent(threadId=thread_id, runId=run_id))
        yield encoder.encode(
            TextMessageStartEvent(messageId=message_id, role="assistant")
        )

        try:
            request = workflow_inputs["request"]
            if not request:
                raise ValueError(
                    "Workflow request is required. Provide request/message or a user message in messages."
                )
            selected_skills_error = workflow_inputs.get("selected_skills_error")
            if selected_skills_error:
                raise selected_skills_error
            selected_skill_names = tuple(workflow_inputs["selected_skill_names"])
            selected_skill_validation = validate_selected_user_skills(
                selected_skill_names
            )

            project_id = workflow_inputs["project_id"] or None
            workspace = workflow_inputs["workspace"] or None
            editor_mode = workflow_inputs["editor_mode"] or None
            settings = Settings.from_env()
            observability = _workflow_observability(
                settings=settings,
                run_id=run_id,
                thread_id=thread_id,
                project_id=project_id,
                workspace=workspace,
            )
            application_planning_interaction = workflow_inputs.get(
                "application_planning_interaction"
            )
            active_graph = (
                await graph(workspace=workspace, project_id=project_id)
                if callable(graph)
                else graph
            )
            if workflow_scope == "application_planning":
                if isinstance(application_planning_interaction, dict):
                    # 同一创建规划 thread 的恢复请求必须从快照校验一直串行到本轮流结束。
                    application_planning_resume_lock = _application_planning_resume_lock(
                        thread_id
                    )
                    await application_planning_resume_lock.acquire()
                    application_planning_resume_lock_acquired = True
            await cleanup_workflow_checkpoints(
                workspace=workspace,
                project_id=project_id,
            )
            if workflow_scope != "application_planning":
                # 创建规划只维护自己的 AG-UI/Graph 生命周期；在 TechnicalPlan
                # 确认前不应登记工作台写租约，更不能让普通规划占住应用资源。
                workspace_lease = workspace_run_leases.acquire(
                    workspace_root=workspace,
                    project_id=project_id,
                    execution_scope=workflow_inputs.get("resume_values", {}).get(
                        "build_execution_scope"
                    ),
                    resource_claims=workflow_inputs.get("resume_values", {}).get(
                        "execution_resource_claims"
                    ),
                    thread_id=thread_id,
                    run_id=run_id,
                )
            resume_from = workflow_inputs.get("resume_from") or None
            checkpoint_values: dict[str, Any] = {}
            if (
                workflow_scope == "application_planning"
                and hasattr(active_graph, "aget_state")
            ):
                # 轮询（无 interaction 的 no-op resume）也必须读 checkpoint，
                # 否则 initial_state 缺少 phase/clarification，started 帧的
                # summary.phase 为 None，前端误判不是 UI 确认卡片导致闪烁。
                checkpoint_snapshot = await active_graph.aget_state(
                    {"configurable": {"thread_id": thread_id}}
                )
                if isinstance(application_planning_interaction, dict):
                    _validate_application_planning_resume(
                        checkpoint_snapshot,
                        application_planning_interaction,
                    )
                checkpoint_values = dict(checkpoint_snapshot.values)
            initial_state: dict[str, Any] = {
                **checkpoint_values,
                "request": request,
                "selected_skill_names": list(selected_skill_names),
                "timeline": [],
                "observability": observability,
                "active_thread_id": thread_id,
                "active_run_id": run_id,
            }
            initial_state.update(workflow_inputs.get("resume_values") or {})
            if (
                isinstance(application_planning_interaction, dict)
                and application_planning_interaction.get("action") == "revise"
            ):
                _mark_started_artifact_revision(
                    initial_state,
                    application_planning_interaction,
                )
            # 非设计变更链路的规划轮次显式清空变更上下文：快照回传只覆盖公开字段，
            # 已终结或被放弃的旧变更指令仍可能残留在 checkpoint 里，必须在这里覆写，
            # 防止其随 checkpoint 合并复活并劫持后续自然回复轮次。
            if (
                workflow_scope == "application_planning"
                and not application_planning_interaction
            ):
                initial_state.update(cleared_design_change_context())
            first_node_name = _application_planning_resume_node(
                application_planning_interaction
            ) or _workflow_start_node(resume_from, workflow_scope)
            current_phase = first_node_name
            if not workflow_scope:
                # 独立创建规划 Graph 只维护创建阶段生命周期，不能登记为工作台开发执行。
                lifecycle_payload = begin_workflow_lifecycle(
                    workflow_inputs,
                    thread_id=thread_id,
                    run_id=run_id,
                    phase=first_node_name,
                )
                if lifecycle_payload is not None:
                    initial_state["lifecycle"] = lifecycle_payload
                    result["lifecycle"] = lifecycle_payload
                    # 生命周期写入成功后立即投影，不能等待首个 Graph 节点结束。
                    yield encoder.encode(
                        CustomEvent(
                            name="application-lifecycle",
                            value=lifecycle_payload,
                        )
                    )

            if resume_from:
                initial_state["resume_from"] = resume_from

            if project_id:
                initial_state["project_id"] = project_id

            if workflow_inputs.get("application_name"):
                initial_state["application_name"] = workflow_inputs["application_name"]

            if workspace:
                initial_state["workspace"] = workspace

            if editor_mode:
                initial_state["editor_mode"] = editor_mode

            if workflow_scope:
                initial_state["workflow_scope"] = workflow_scope

            # 维护当前运行的增量状态；custom 进度帧不能回退到本轮恢复前的快照，
            # 否则修复完成后的构建检查会被旧的 awaiting_user 状态覆盖。
            stream_state: dict[str, Any] = dict(initial_state)

            config = {
                "configurable": {"thread_id": thread_id},
                "run_name": "xcodeagent-main-workflow",
                "tags": [
                    "xcodeagent",
                    "workflow",
                    *(["langsmith"] if observability["langsmith"]["enabled"] else []),
                ],
                "metadata": {
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "project_id": project_id,
                    "workspace": workspace,
                    "selected_skill_names": list(selected_skill_names),
                    "selected_skills_revision": selected_skill_validation.revision,
                    "editor_mode": editor_mode,
                    "workflow_scope": workflow_scope,
                    "workflow": "xcodeagent-main",
                    "langsmith_enabled": observability["langsmith"]["enabled"],
                },
            }

            started_event = _workflow_event(
                events,
                "workflow.run.started",
                run_id=run_id,
                thread_id=thread_id,
                status="running",
                message="Workflow run started.",
                data={
                    "request": request,
                    "projectId": project_id,
                    "resumeFrom": resume_from,
                    "selectedSkillNames": list(selected_skill_names),
                    "selectedSkillsRevision": selected_skill_validation.revision,
                    "observability": observability,
                },
            )
            for frame in _workflow_ag_ui_frames(
                encoder,
                run_id=run_id,
                thread_id=thread_id,
                events=events,
                result=result,
            ):
                yield frame
            first_node_attempt = _next_node_attempt(node_attempts, first_node_name)
            first_node_iteration_kind = _iteration_kind(first_node_name, first_node_attempt)
            repair_resume = (
                first_node_name == "code_review"
                and isinstance(
                    initial_state.get("code_review_repair_confirmation"), dict
                )
                and initial_state["code_review_repair_confirmation"].get("action")
                == "repair_all"
            )
            first_node_event_message = (
                "正在修复审查的问题"
                if repair_resume
                else f"正在执行：{_runtime_node_label(first_node_name, initial_state)}"
            )
            first_node_event = _workflow_event(
                events,
                "workflow.node.started",
                run_id=run_id,
                thread_id=thread_id,
                node_name=first_node_name,
                status="running",
                message=first_node_event_message,
                node_label=(
                    "修复审查的问题"
                    if repair_resume
                    else _runtime_node_label(first_node_name, initial_state)
                ),
                attempt=first_node_attempt,
                iteration_kind=first_node_iteration_kind,
            )
            # node.started 帧在 UI 确认阶段（resume_from=ui_confirmation）复用 checkpoint
            # 的 clarification/ui_designs，避免换一换等单页动作 run 起始帧把 clarification
            # 投影为空导致前端短暂白屏（前端缓存可兜底，但直接带上更稳）。
            # 仅限 UI 确认阶段：需求阶段提交后必须让 clarification 为空，使前端
            # awaitingUserInput=false → showingProgress=true 切到进度页，否则会卡在
            # 按钮禁用的确认面板不动。不修改共享 result，避免影响后续 updates 聚合。
            started_result = dict(result)
            if first_node_name == "code_review":
                # 修复恢复请求的首帧也要携带原始审查快照，避免前端把修复轮次误显示为首次扫描。
                for key in (
                    "code_review_result",
                    "code_review_report_path",
                    "code_review_repair_result",
                    "code_review_repair_status",
                    "code_review_build_results",
                    "code_review_repair_iteration",
                    "code_review_max_repair_iterations",
                ):
                    if initial_state.get(key) is not None:
                        started_result[key] = initial_state[key]
                started_result["phase"] = "code_review"
                started_result["status"] = "running"
                repair_submission = initial_state.get("code_review_repair_confirmation")
                if (
                    isinstance(repair_submission, dict)
                    and repair_submission.get("action") == "repair_all"
                ):
                    repair_snapshot = dict(
                        initial_state.get("code_review_repair_result") or {}
                    )
                    repair_snapshot.update(
                        {
                            "status": "repairing",
                            "summary": "正在修复审查的问题，请稍候…",
                        }
                    )
                    started_result["code_review_repair_result"] = repair_snapshot
                    started_result["code_review_repair_status"] = "repairing"
            if workflow_scope == "application_planning":
                # 规划修订恢复运行时持续投影变更上下文，让前端从 started 帧起展示真实生成阶段。
                for key in (
                    "design_change_request",
                    "design_change_target",
                    "design_change_reason",
                    "design_change_existing_artifacts",
                ):
                    if initial_state.get(key) is not None:
                        started_result[key] = initial_state[key]
                started_result["design_change_submission"] = bool(
                    initial_state.get("design_change_submission")
                    or (
                        initial_state.get("design_change_request")
                        and not initial_state.get("ui_design_action")
                    )
                )
            if resume_from == "ui_confirmation":
                # 恢复 phase/clarification/ui_designs：轮询 no-op resume 的
                # result 为空 dict 没有 phase，前端收到 phase=None 会误判
                # 不是 UI 确认卡片导致卡片消失闪烁。
                if "phase" not in started_result and initial_state.get("phase") is not None:
                    started_result["phase"] = initial_state.get("phase")
                if "clarification" not in started_result and initial_state.get("clarification") is not None:
                    started_result["clarification"] = initial_state.get("clarification")
                if "ui_designs" not in started_result and initial_state.get("ui_designs") is not None:
                    started_result["ui_designs"] = initial_state.get("ui_designs")
            elif workflow_scope == "application_planning":
                # 需求、产品和技术阶段的 node.started 只表示本轮开始，不能把上一轮
                # 的待确认载荷带进 running 快照，否则前端会继续显示旧确认面板。
                started_result.pop("clarification", None)
            for frame in _workflow_ag_ui_frames(
                encoder,
                run_id=run_id,
                thread_id=thread_id,
                events=events,
                result=started_result,
            ):
                yield frame
            process_sequence = 1
            repair_preparation_attempt: int | None = None
            yield _process_frame(
                encoder,
                id=_process_step_id(first_node_name, first_node_attempt),
                kind="workflow",
                status="running",
                title=(
                    "正在修复审查的问题"
                    if repair_resume
                    else f"正在执行 {_runtime_node_label(first_node_name, initial_state)}"
                ),
                detail=str(first_node_event["message"]),
                sequence=process_sequence,
                node_name=first_node_name,
                attempt=first_node_attempt,
                iteration_kind=first_node_iteration_kind,
            )
            reasoning_steps: dict[str, str] = {}
            tool_steps: dict[str, dict[str, str]] = {}
            tool_indexes: dict[int, str] = {}

            graph_input: dict[str, Any] | Command[Any] = initial_state
            if workflow_scope == "application_planning" and isinstance(
                application_planning_interaction,
                dict,
            ):
                # 传输层开启新 AG-UI run，但业务执行恢复同一 thread 的原生中断任务。
                # 运行元数据由审阅节点在门禁校验成功后一次性写入；若校验失败，纯 resume
                # 不留下可与下次重试冲突的 pending writes。
                graph_input = Command(resume=application_planning_interaction)
            stream_kwargs: dict[str, Any] = {
                "config": config,
                "stream_mode": ["updates", "messages", "custom"],
            }
            if _graph_stream_supports_subgraphs(active_graph):
                # 仅开启命名空间后，作为主图节点挂载的验收子图 custom 事件才会
                # 在启动过程中即时穿透；子图 update 仍由父节点最终增量统一投影。
                stream_kwargs["subgraphs"] = True
            async for stream_item in active_graph.astream(graph_input, **stream_kwargs):
                namespace, stream_mode, chunk = _workflow_stream_chunk(stream_item)
                if namespace and stream_mode != "custom":
                    continue
                if stream_mode == "custom":
                    progress = chunk if isinstance(chunk, dict) else {}
                    event_type = progress.get("type")
                    if event_type == "entity_source_binding.progress":
                        progress_node = str(progress.get("node_name") or "entity_source_binding")
                        progress_attempt = _current_node_attempt(node_attempts, progress_node)
                        progress_detail = (
                            progress.get("detail")
                            if isinstance(progress.get("detail"), dict)
                            else {}
                        )
                        progress_state = {
                            **stream_state,
                            **(
                                {"detail_target_type": progress_detail.get("target_type")}
                                if progress_detail.get("target_type")
                                else {}
                            ),
                        }
                        progress_message = str(
                            progress.get("message") or "细节设计进度已更新。"
                        )
                        _workflow_event(
                            events,
                            "workflow.node.progress",
                            run_id=run_id,
                            thread_id=thread_id,
                            node_name=progress_node,
                            status="running",
                            message=progress_message,
                            data={
                                "phase": progress_node,
                                "detail": progress_detail,
                            },
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                            node_label=_runtime_node_label(progress_node, progress_state),
                        )
                        for frame in _workflow_ag_ui_frames(
                            encoder,
                            run_id=run_id,
                            thread_id=thread_id,
                            events=events,
                            result=progress_state,
                        ):
                            yield frame
                        yield _process_frame(
                            encoder,
                            id=_process_step_id(progress_node, progress_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_runtime_node_label(progress_node, progress_state)}",
                            detail=progress_message,
                            sequence=process_sequence,
                            node_name=progress_node,
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                        )
                        continue
                    if event_type == "workspace_inspection.progress":
                        progress_node = str(
                            progress.get("node_name") or "inspect_workspace"
                        )
                        progress_attempt = _current_node_attempt(
                            node_attempts, progress_node
                        )
                        progress_detail = (
                            progress.get("detail")
                            if isinstance(progress.get("detail"), dict)
                            else {}
                        )
                        progress_message = str(
                            progress.get("message") or "正在扫描用户工作区代码…"
                        )
                        progress_state = {
                            **stream_state,
                            "phase": progress_node,
                            "status": "in_progress",
                            "workspace_scan_progress": progress_detail,
                        }
                        _workflow_event(
                            events,
                            "workflow.node.progress",
                            run_id=run_id,
                            thread_id=thread_id,
                            node_name=progress_node,
                            status="running",
                            message=progress_message,
                            data={
                                "phase": progress_node,
                                "workspaceInspectionProgress": progress_detail,
                                "stateDelta": _public_workflow_state(progress_state),
                            },
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                            node_label=_runtime_node_label(progress_node, progress_state),
                        )
                        for frame in _workflow_ag_ui_frames(
                            encoder,
                            run_id=run_id,
                            thread_id=thread_id,
                            events=events,
                            result=progress_state,
                        ):
                            yield frame
                        yield _process_frame(
                            encoder,
                            id=_process_step_id(progress_node, progress_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_runtime_node_label(progress_node, progress_state)}",
                            detail=progress_message,
                            sequence=process_sequence,
                            node_name=progress_node,
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                            workspace_inspection_progress=progress_detail,
                        )
                        continue
                    if event_type == "launch_project.progress":
                        progress_node = str(progress.get("node_name") or "launch_project")
                        progress_attempt = _current_node_attempt(
                            node_attempts, progress_node
                        )
                        progress_detail = (
                            progress.get("detail")
                            if isinstance(progress.get("detail"), dict)
                            else {}
                        )
                        progress_message = str(
                            progress.get("message") or "正在启动项目预览…"
                        )
                        launch_progress = {
                            **progress_detail,
                            "message": progress_message,
                        }
                        progress_state = {
                            **stream_state,
                            "phase": progress_node,
                            "status": "in_progress",
                            "launch_progress": launch_progress,
                        }
                        _workflow_event(
                            events,
                            "workflow.node.progress",
                            run_id=run_id,
                            thread_id=thread_id,
                            node_name=progress_node,
                            status="running",
                            message=progress_message,
                            data={
                                "phase": progress_node,
                                "launchProgress": launch_progress,
                                "stateDelta": _public_workflow_state(progress_state),
                            },
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                            node_label=_runtime_node_label(progress_node, progress_state),
                        )
                        for frame in _workflow_ag_ui_frames(
                            encoder,
                            run_id=run_id,
                            thread_id=thread_id,
                            events=events,
                            result=progress_state,
                        ):
                            yield frame
                        yield _process_frame(
                            encoder,
                            id=_process_step_id(progress_node, progress_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_runtime_node_label(progress_node, progress_state)}",
                            detail=progress_message,
                            sequence=process_sequence,
                            node_name=progress_node,
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                        )
                        continue
                    if event_type == "ui_confirmation.progress":
                        progress_node = str(progress.get("node_name") or "ui_confirmation")
                        progress_attempt = _current_node_attempt(
                            node_attempts, progress_node
                        )
                        progress_detail = (
                            progress.get("detail")
                            if isinstance(progress.get("detail"), dict)
                            else {}
                        )
                        ready_pages = progress_detail.get("pages")
                        ready_pages = (
                            ready_pages
                            if isinstance(ready_pages, list)
                            else []
                        )
                        progress_message = str(
                            progress.get("message") or "UI设计稿生成进度已更新。"
                        )
                        # 把当前已生成的页面快照写进 ui_designs，前端在 loading 态
                        # 即可从 workflow state 边收边渲染已就绪的设计稿。
                        # 保留 checkpoint 的 clarification（mode=ui_design_confirmation），
                        # 仅把 pages 更新为当前已就绪快照、status 置 running，避免
                        # 前端 ApplicationPlanningQuestionPanel 因 clarification 为空而
                        # 走默认空表单分支白屏（换一换/多页调整 run 期间持续几十秒）。
                        checkpoint_clarification = initial_state.get("clarification")
                        if isinstance(checkpoint_clarification, dict):
                            checkpoint_pages = checkpoint_clarification.get("pages")
                            checkpoint_pages = (
                                checkpoint_pages
                                if isinstance(checkpoint_pages, list)
                                else []
                            )
                            presentation_by_id = {
                                str(page.get("pageId") or ""): page
                                for page in checkpoint_pages
                                if isinstance(page, dict)
                            }
                            presented_pages = [
                                {
                                    **presentation_by_id.get(
                                        str(page.get("pageId") or ""), {}
                                    ),
                                    **page,
                                }
                                for page in ready_pages
                                if isinstance(page, dict)
                            ]
                            progress_clarification = {
                                **checkpoint_clarification,
                                "status": "running",
                                "pages": presented_pages,
                            }
                        else:
                            progress_clarification = {}
                        progress_state = {
                            **stream_state,
                            "phase": progress_node,
                            "status": "running",
                            "ui_designs": {
                                "confirmation_status": "generating",
                                "pages": ready_pages,
                            },
                            "clarification": progress_clarification,
                        }
                        _workflow_event(
                            events,
                            "workflow.node.progress",
                            run_id=run_id,
                            thread_id=thread_id,
                            node_name=progress_node,
                            status="running",
                            message=progress_message,
                            data={
                                "phase": progress_node,
                                "detail": {
                                    "ready": progress_detail.get("ready", len(ready_pages)),
                                    "total": progress_detail.get("total", 0),
                                    "pageId": progress_detail.get("pageId"),
                                },
                            },
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                            node_label=_runtime_node_label(progress_node, progress_state),
                        )
                        for frame in _workflow_ag_ui_frames(
                            encoder,
                            run_id=run_id,
                            thread_id=thread_id,
                            events=events,
                            result=progress_state,
                        ):
                            yield frame
                        yield _process_frame(
                            encoder,
                            id=_process_step_id(progress_node, progress_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_runtime_node_label(progress_node, progress_state)}",
                            detail=progress_message,
                            sequence=process_sequence,
                            node_name=progress_node,
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                        )
                        continue
                    if event_type == "prepare_build_tasks.progress":
                        dag_generation = (
                            progress.get("dag_generation")
                            if isinstance(progress.get("dag_generation"), dict)
                            else {}
                        )
                        process_sequence += 1
                        task_attempt = _current_node_attempt(
                            node_attempts, "prepare_build_tasks"
                        )
                        yield _process_frame(
                            encoder,
                            id=_process_step_id("prepare_build_tasks", task_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_workflow_node_label('prepare_build_tasks')}",
                            detail=str(
                                progress.get("message") or "构建任务 DAG 进度已更新。"
                            ),
                            sequence=process_sequence,
                            node_name="prepare_build_tasks",
                            attempt=task_attempt,
                            iteration_kind=_iteration_kind(
                                "prepare_build_tasks", task_attempt
                            ),
                            dag_generation=dag_generation,
                        )
                        continue
                    if event_type == "workflow.build.progress":
                        progress_state = (
                            progress.get("state")
                            if isinstance(progress.get("state"), dict)
                            else {}
                        )
                        progress_node = str(progress.get("node_name") or "build")
                        progress_attempt = _current_node_attempt(node_attempts, progress_node)
                        progress_iteration_kind = _iteration_kind(
                            progress_node, progress_attempt
                        )
                        progress_message = str(
                            progress.get("message") or "构建任务进度已更新。"
                        )
                        # 工具活动只更新当前 ProcessStep，不写入 Workflow 历史或状态快照。
                        if progress.get("ephemeral") is not True:
                            _workflow_event(
                                events,
                                "workflow.node.progress",
                                run_id=run_id,
                                thread_id=thread_id,
                                node_name=progress_node,
                                status=str(progress.get("status") or "running"),
                                message=progress_message,
                                data={
                                    "phase": progress_state.get("phase", progress_node),
                                    "stateDelta": _public_workflow_state(progress_state),
                                    "detail": {
                                        "buildSummary": progress_state.get("build_summary", {}),
                                        "buildExecutionSlice": progress_state.get(
                                            "build_execution_slice"
                                        ),
                                        "buildEvents": progress_state.get("build_events", []),
                                    },
                                },
                                attempt=progress_attempt,
                                iteration_kind=progress_iteration_kind,
                            )
                            for frame in _workflow_ag_ui_frames(
                                encoder,
                                run_id=run_id,
                                thread_id=thread_id,
                                events=events,
                                result=progress_state,
                            ):
                                yield frame
                        yield _process_frame(
                            encoder,
                            id=_process_step_id(progress_node, progress_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_workflow_node_label(progress_node)}",
                            detail=progress_message,
                            sequence=process_sequence,
                            node_name=progress_node,
                            attempt=progress_attempt,
                            iteration_kind=progress_iteration_kind,
                            build_execution_slice=progress_state.get(
                                "build_execution_slice"
                            ),
                        )
                        continue
                    if event_type in {"integration_test.checks", "unit_test.checks"}:
                        checks = integration_test_checks(progress)
                        if not checks:
                            continue
                        process_sequence += 1
                        check_node = (
                            "unit_test" if event_type == "unit_test.checks" else "integration_test"
                        )
                        test_attempt = _current_node_attempt(node_attempts, check_node)
                        yield _process_frame(
                            encoder,
                            id=_process_step_id(check_node, test_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_workflow_node_label(check_node)}",
                            detail=integration_test_check_summary(checks),
                            sequence=process_sequence,
                            checks=checks,
                            node_name=check_node,
                            attempt=test_attempt,
                            iteration_kind=_iteration_kind(
                                check_node, test_attempt
                            ),
                        )
                        continue
                    if event_type in {"code_review.repair", "code_review.build_checks"}:
                        process_sequence += 1
                        progress_node = "code_review"
                        progress_attempt = _current_node_attempt(node_attempts, progress_node)
                        current_repair = dict(
                            stream_state.get("code_review_repair_result") or {}
                        )
                        if event_type == "code_review.repair":
                            current_repair.update(
                                {
                                    "status": "repairing",
                                    "iteration": progress.get("attempt", current_repair.get("iteration", 0)),
                                    "summary": str(
                                        progress.get("message") or "正在修复代码审查问题。"
                                    )[:2_000],
                                }
                            )
                        else:
                            checks = progress.get("checks")
                            checks = checks if isinstance(checks, list) else []
                            # 构建器按检查项逐条发送进度；按 id 合并而不是覆盖，
                            # 这样前端能在三项检查尚未全部结束时看到已完成的行。
                            existing_checks = current_repair.get("build_checks")
                            existing_checks = (
                                existing_checks
                                if isinstance(existing_checks, list)
                                else []
                            )
                            checks_by_id = {
                                str(item.get("id") or ""): item
                                for item in existing_checks
                                if isinstance(item, dict) and str(item.get("id") or "")
                            }
                            for check in checks:
                                if isinstance(check, dict):
                                    check_id = str(check.get("id") or "")
                                    if check_id:
                                        checks_by_id[check_id] = check
                            current_repair.update(
                                {
                                    "status": "building",
                                    "build_checks": list(checks_by_id.values())[:10],
                                    "summary": "正在执行审查修复后的前后端构建检查。",
                                }
                            )
                        # custom 事件本身不写入 LangGraph checkpoint，但必须更新本轮
                        # 运行态，避免下一个进度帧或启动进度帧回到旧的确认状态。
                        stream_state["code_review_repair_result"] = current_repair
                        stream_state["code_review_repair_status"] = current_repair.get(
                            "status", "repairing"
                        )
                        progress_state = {
                            **stream_state,
                            "phase": "code_review",
                            "status": "in_progress",
                            "code_review_repair_result": current_repair,
                        }
                        public_repair = _workflow_code_review_repair(
                            current_repair, "code_review"
                        )
                        progress_message = str(
                            progress.get("message")
                            or (
                                "正在修复代码审查问题。"
                                if event_type == "code_review.repair"
                                else "正在执行审查修复后的前后端构建检查。"
                            )
                        )
                        _workflow_event(
                            events,
                            "workflow.node.progress",
                            run_id=run_id,
                            thread_id=thread_id,
                            node_name=progress_node,
                            status="running",
                            message=progress_message,
                            data={
                                "phase": "code_review",
                                "codeReviewRepair": public_repair,
                            },
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                            node_label=_runtime_node_label(progress_node, progress_state),
                        )
                        for frame in _workflow_ag_ui_frames(
                            encoder,
                            run_id=run_id,
                            thread_id=thread_id,
                            events=events,
                            result=progress_state,
                        ):
                            yield frame
                        yield _process_frame(
                            encoder,
                            # 审查节点开始、修复和构建检查共用同一稳定步骤 ID，
                            # 前端据此原位更新，避免时间线重复显示同一审查动作。
                            id=_process_step_id(progress_node, progress_attempt),
                            kind="workflow",
                            status="running",
                            title=(
                                "正在修复审查的问题"
                                if event_type == "code_review.repair"
                                else "正在执行前后端构建检查"
                            ),
                            detail=progress_message,
                            sequence=process_sequence,
                            node_name=progress_node,
                            attempt=progress_attempt,
                            iteration_kind=_iteration_kind(progress_node, progress_attempt),
                        )
                        continue
                    if event_type == "integration_test.repair.started":
                        process_sequence += 1
                        repair_attempt = node_attempts.get("small_task_repair", 0) + 1
                        repair_preparation_attempt = repair_attempt
                        yield _process_frame(
                            encoder,
                            id=_process_step_id("small_task_repair", repair_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_workflow_node_label('small_task_repair')}",
                            detail=str(
                                progress.get("message")
                                or "正在分析失败原因并准备局部修复。"
                            ),
                            sequence=process_sequence,
                            node_name="small_task_repair",
                            attempt=repair_attempt,
                            iteration_kind=_iteration_kind(
                                "small_task_repair", repair_attempt
                            ),
                        )
                        continue
                    if event_type == "small_task.tool_activity":
                        activity = (
                            progress.get("activity")
                            if isinstance(progress.get("activity"), dict)
                            else {}
                        )
                        activity_status = str(activity.get("status") or "running")
                        if activity_status not in {"running", "completed", "failed"}:
                            activity_status = "running"
                        process_sequence += 1
                        task_id = str(activity.get("taskId") or "")
                        tool_node = str(progress.get("node_name") or "small_task_repair")
                        yield _process_frame(
                            encoder,
                            id=f"small-task-tool:{task_id or process_sequence}",
                            kind="tool",
                            status=activity_status,
                            title=str(activity.get("tool") or "SmallTask 工作区工具"),
                            detail=str(
                                activity.get("message")
                                or activity.get("path")
                                or "SmallTask Agent 正在执行局部任务。"
                            ),
                            sequence=process_sequence,
                            node_name=tool_node,
                        )
                        continue
                    if event_type == "llm.token":
                        yield encoder.encode(
                            CustomEvent(
                                name="llm.token",
                                value={
                                    "token": progress.get("token", ""),
                                    "node": progress.get("node", ""),
                                },
                            )
                        )
                        continue
                    # 其它未知 custom 事件:静默跳过,保持向后兼容。
                    continue

                if stream_mode == "messages":
                    message_chunk, metadata = chunk
                    process_frames, process_sequence = _message_process_frames(
                        encoder,
                        message_chunk=message_chunk,
                        metadata=metadata,
                        reasoning_steps=reasoning_steps,
                        tool_steps=tool_steps,
                        tool_indexes=tool_indexes,
                        sequence=process_sequence,
                    )
                    for frame in process_frames:
                        yield frame
                    continue

                for node_name, update in chunk.items():
                    if node_name == "__interrupt__":
                        continue
                    if not isinstance(update, dict):
                        continue
                    # 节点更新是 LangGraph 的增量结果；先合并进运行态，供后续
                    # launch_project.progress 和 code_review.build_checks 帧继续投影。
                    stream_state.update(update)
                    result = dict(stream_state)
                    if (
                        workflow_scope == "application_planning"
                        and node_name.endswith("_review")
                    ):
                        # review 节点只完成 interrupt 恢复交接；对外继续展示真实产物节点，
                        # 同时立即投影服务端创建的修订事务，避免生成期间文案闪回首次生成。
                        current_phase = first_node_name
                        resumed_state = {
                            **update,
                            "phase": first_node_name,
                            "status": "running",
                        }
                        for frame in _workflow_ag_ui_frames(
                            encoder,
                            run_id=run_id,
                            thread_id=thread_id,
                            events=events,
                            result=resumed_state,
                        ):
                            yield frame
                        continue
                    current_phase = node_name
                    if not workflow_scope:
                        # 创建规划节点沿用自身的确认状态，不投射工作台执行边界。
                        lifecycle_update = {**initial_state, **update}
                        lifecycle_payload = project_workflow_lifecycle_boundary(
                            workspace,
                            run_id=run_id,
                            node_name=node_name,
                            update=lifecycle_update,
                        )
                        completion = lifecycle_update.get(
                            "application_revision_completion"
                        )
                        if isinstance(completion, dict):
                            update["application_revision_completion"] = completion
                            _workflow_event(
                                events,
                                "application-revision",
                                run_id=run_id,
                                thread_id=thread_id,
                                status="completed",
                                message="应用二次修改已通过最终验收。",
                                data={"changeId": completion.get("changeId")},
                            )
                        if lifecycle_payload is not None:
                            update["lifecycle"] = lifecycle_payload
                            # 先广播最新 revision，再继续发送节点投影。
                            yield encoder.encode(
                                CustomEvent(
                                    name="application-lifecycle",
                                    value=lifecycle_payload,
                                )
                            )

                    for frame in _pending_tool_frames(
                        encoder,
                        update=update,
                        tool_steps=tool_steps,
                        sequence=process_sequence,
                    ):
                        yield frame

                    detail = _workflow_node_detail(node_name, update)
                    node_attempt = _current_node_attempt(node_attempts, node_name)
                    node_iteration_kind = _iteration_kind(node_name, node_attempt)
                    terminal_status = _terminal_process_status(node_name, update)
                    node_payload = {
                        "phase": update.get("phase", node_name),
                        "stateDelta": _public_workflow_state(update),
                        "detail": detail.get("data", {}),
                        "artifacts": _workflow_artifacts(update),
                    }
                    completed_event = _workflow_event(
                        events,
                        "workflow.node.completed",
                        run_id=run_id,
                        thread_id=thread_id,
                        node_name=node_name,
                        status=terminal_status,
                        message=detail.get("message")
                        or f"完成：{_runtime_node_label(node_name, update)}",
                        node_label=_runtime_node_label(node_name, update),
                        data=node_payload,
                        attempt=node_attempt,
                        iteration_kind=node_iteration_kind,
                    )
                    for frame in _workflow_ag_ui_frames(
                        encoder,
                        run_id=run_id,
                        thread_id=thread_id,
                        events=events,
                        result=update,
                    ):
                        yield frame
                    process_sequence += 1
                    checks = (
                        integration_test_checks(
                            update.get(
                                "unit_test_results"
                                if node_name == "unit_test"
                                else "test_results",
                                [],
                            )
                        )
                        if node_name in {"integration_test", "unit_test"}
                        else None
                    )
                    process_detail = (
                        integration_test_check_summary(checks)
                        if checks
                        else str(completed_event["message"])
                    )
                    yield _process_frame(
                        encoder,
                        id=_process_step_id(node_name, node_attempt),
                        kind="workflow",
                        status=terminal_status,
                        title=_runtime_terminal_process_title(
                            node_name,
                            terminal_status,
                            update,
                        ),
                        detail=process_detail,
                        sequence=process_sequence,
                        checks=checks,
                        node_name=node_name,
                        attempt=node_attempt,
                        iteration_kind=node_iteration_kind,
                        build_execution_slice=(
                            update.get("build_execution_slice")
                            if node_name == "build"
                            else None
                        ),
                        dag_generation=(
                            update.get("dag_generation_progress")
                            if node_name == "prepare_build_tasks"
                            else None
                        ),
                        workspace_inspection=(
                            detail.get("data", {}).get("workspaceInspection")
                            if node_name == "inspect_workspace"
                            else None
                        ),
                    )
                    for frame in _tool_result_frames(
                        encoder,
                        update=update,
                        tool_steps=tool_steps,
                        sequence=process_sequence,
                    ):
                        yield frame

                    next_nodes = _workflow_next_nodes(node_name, update)
                    if (
                        node_name == "integration_test"
                        and repair_preparation_attempt is not None
                    ):
                        if "small_task_repair" not in next_nodes:
                            process_sequence += 1
                            requires_input = (
                                update.get("integration_next_action")
                                == "await_user_input"
                            )
                            repair_plan = update.get("repair_task_plan")
                            repair_reason = (
                                str(repair_plan.get("reason") or "")
                                if isinstance(repair_plan, dict)
                                else ""
                            )
                            repair_detail = repair_reason or (
                                "自动修复需要额外确认。"
                                if requires_input
                                else "当前失败无法自动修复。"
                            )
                            yield _process_frame(
                                encoder,
                                id=_process_step_id(
                                    "small_task_repair", repair_preparation_attempt
                                ),
                                kind="workflow",
                                status=(
                                    "requires_user_input"
                                    if requires_input
                                    else "failed"
                                ),
                                title=(
                                    "局部修复范围需要确认"
                                    if requires_input
                                    else "未能启动局部修复"
                                ),
                                detail=repair_detail,
                                sequence=process_sequence,
                                node_name="small_task_repair",
                                attempt=repair_preparation_attempt,
                                iteration_kind=_iteration_kind(
                                    "small_task_repair", repair_preparation_attempt
                                ),
                            )
                        repair_preparation_attempt = None

                    for next_node in next_nodes:
                        next_attempt = _next_node_attempt(node_attempts, next_node)
                        next_iteration_kind = _iteration_kind(next_node, next_attempt)
                        next_started_result = dict(stream_state)
                        if workflow_scope == "application_planning" and next_node != "ui_confirmation":
                            # 下一阶段开始帧不能复用上一阶段的待确认载荷，避免旧面板遮住新阶段进度。
                            next_started_result.pop("clarification", None)
                        next_event = _workflow_event(
                            events,
                            "workflow.node.started",
                            run_id=run_id,
                            thread_id=thread_id,
                            node_name=next_node,
                            status="running",
                            message=f"正在执行：{_runtime_node_label(next_node, update)}",
                            node_label=_runtime_node_label(next_node, update),
                            attempt=next_attempt,
                            iteration_kind=next_iteration_kind,
                        )
                        for frame in _workflow_ag_ui_frames(
                            encoder,
                            run_id=run_id,
                            thread_id=thread_id,
                            events=events,
                            result=next_started_result,
                        ):
                            yield frame
                        process_sequence += 1
                        yield _process_frame(
                            encoder,
                            id=_process_step_id(next_node, next_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_runtime_node_label(next_node, update)}",
                            detail=str(next_event["message"]),
                            sequence=process_sequence,
                            node_name=next_node,
                            attempt=next_attempt,
                            iteration_kind=next_iteration_kind,
                        )

            # 真实 LangGraph 提供 aget_state；测试或兼容 Graph 可能只通过流更新返回状态。
            if hasattr(active_graph, "aget_state"):
                snapshot = await active_graph.aget_state(config)
                result = dict(snapshot.values)
                if workflow_scope == "application_planning":
                    result = project_application_planning_interrupt(result, snapshot)
            if lifecycle_payload is not None:
                result["lifecycle"] = lifecycle_payload
            summary = _workflow_summary(result, events)
            finished_event = _workflow_event(
                events,
                "workflow.run.finished",
                run_id=run_id,
                thread_id=thread_id,
                status=str(summary.get("status") or "completed"),
                message=str(summary.get("message") or "Workflow run finished."),
                data={"summary": summary},
            )
            final_payload = _workflow_visual_payload(
                run_id=run_id,
                thread_id=thread_id,
                summary=summary,
                events=events,
                result=result,
            )
            for frame in _workflow_ag_ui_frames(
                encoder,
                run_id=run_id,
                thread_id=thread_id,
                events=events,
                result=result,
                visual_payload=final_payload,
            ):
                yield frame
            for frame in _text_delta_frames(
                encoder,
                message_id,
                f"{summary.get('message') or finished_event['message']}\n",
            ):
                yield frame
            yield encoder.encode(TextMessageEndEvent(messageId=message_id))
            yield encoder.encode(
                RunFinishedEvent(
                    threadId=thread_id,
                    runId=run_id,
                    result=jsonable_encoder(
                        {
                            "messageId": message_id,
                            "agentMode": "workflow",
                            "workflow": final_payload,
                            "summary": summary,
                            "events": events,
                            "result": _public_workflow_state(result),
                        }
                    ),
                )
            )
        except asyncio.CancelledError:
            if not workflow_scope:
                lifecycle_payload = stop_workflow_lifecycle(
                    workspace,
                    run_id=run_id,
                    phase=current_phase,
                )
            raise
        except Exception as exc:
            if not workflow_scope:
                lifecycle_payload = fail_workflow_lifecycle(
                    workspace,
                    run_id=run_id,
                    phase=current_phase,
                    error=exc,
                )
            error_code = getattr(exc, "code", None)
            result = {
                "status": "failed",
                "phase": "failed",
                "error": str(exc),
                **({"lifecycle": lifecycle_payload} if lifecycle_payload else {}),
                **({"error_code": error_code} if error_code else {}),
            }
            summary = _workflow_summary(result, events)
            summary["message"] = f"Workflow failed：{type(exc).__name__}: {exc}"
            if error_code:
                summary["errorCode"] = error_code
            failed_event = _workflow_event(
                events,
                "workflow.run.failed",
                run_id=run_id,
                thread_id=thread_id,
                status="failed",
                message=summary["message"],
                data={
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        **({"code": error_code} if error_code else {}),
                    }
                },
            )
            failed_payload = _workflow_visual_payload(
                run_id=run_id,
                thread_id=thread_id,
                summary=summary,
                events=events,
                result=result,
            )
            for frame in _workflow_ag_ui_frames(
                encoder,
                run_id=run_id,
                thread_id=thread_id,
                events=events,
                result=result,
                visual_payload=failed_payload,
            ):
                yield frame
            yield encoder.encode(TextMessageEndEvent(messageId=message_id))
            yield encoder.encode(
                RunErrorEvent(
                    message=summary["message"],
                    code=str(error_code or "WORKFLOW_RUN_FAILED"),
                )
            )
        finally:
            # 正常完成和消费端取消都必须释放任务注册及工作区占用。
            workflow_run_registry.unregister(run_id, task)
            if workspace_lease is not None:
                workspace_lease.release()
            if (
                application_planning_resume_lock is not None
                and application_planning_resume_lock_acquired
            ):
                # 取消、预校验异常和 Graph 异常都走这里，不能把同一 thread 永久锁死。
                application_planning_resume_lock.release()

    return stream()


def _workflow_observability(
    *,
    settings: Settings,
    run_id: str,
    thread_id: str,
    project_id: str | None,
    workspace: str | None,
) -> dict[str, Any]:
    project = settings.langsmith_project or "default"
    trace_search_url = (
        "https://smith.langchain.com/"
        f"?{urlencode({'project': project, 'q': run_id})}"
    )
    return {
        "langsmith": {
            "enabled": settings.langsmith_tracing_enabled,
            "project": settings.langsmith_project,
            "endpoint": settings.langsmith_endpoint,
            "runId": run_id,
            "threadId": thread_id,
            "projectId": project_id,
            "workspace": workspace,
            "traceSearchUrl": trace_search_url
            if settings.langsmith_tracing_enabled
            else "",
        }
    }
