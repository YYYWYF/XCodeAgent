"""执行 LangGraph 主工作流并协调其对外 AG-UI 生命周期。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from urllib.parse import urlencode
from uuid import uuid4

from ag_ui.core import (
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi.encoders import jsonable_encoder

from app.protocols.workflow.projection import (
    _public_workflow_state,
    _workflow_artifacts,
    _workflow_event,
    _workflow_next_nodes,
    _workflow_node_detail,
    _workflow_node_label,
    _workflow_start_node,
    _workflow_summary,
    _workflow_visual_payload,
)
from app.protocols.workflow.request import workflow_run_inputs
from app.protocols.workflow.run_control import (
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
from app.persistence.checkpoints import cleanup_workflow_checkpoints
from app.services.user_skill_runtime import validate_selected_user_skills
from app.workspace.run_lease import WorkspaceRunLease, workspace_run_leases


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
    return "completed"


def _terminal_process_title(node_name: str, status: str) -> str:
    """按步骤终态生成一致的中文标题。"""

    prefix = {
        "completed": "已完成",
        "failed": "执行失败",
        "requires_user_input": "等待确认",
    }.get(status, "已完成")
    return f"{prefix} {_workflow_node_label(node_name)}"


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
        node_attempts: dict[str, int] = {}
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
            workflow_scope = workflow_inputs.get("workflow_scope") or None
            settings = Settings.from_env()
            observability = _workflow_observability(
                settings=settings,
                run_id=run_id,
                thread_id=thread_id,
                project_id=project_id,
                workspace=workspace,
            )
            active_graph = (
                await graph(workspace=workspace, project_id=project_id)
                if callable(graph)
                else graph
            )
            await cleanup_workflow_checkpoints(
                workspace=workspace,
                project_id=project_id,
            )
            workspace_lease = workspace_run_leases.acquire(
                workspace_root=workspace,
                project_id=project_id,
                thread_id=thread_id,
                run_id=run_id,
            )
            resume_from = workflow_inputs.get("resume_from") or None
            initial_state: dict[str, Any] = {
                "request": request,
                "selected_skill_names": list(selected_skill_names),
                "timeline": [],
                "observability": observability,
                "active_thread_id": thread_id,
                "active_run_id": run_id,
            }
            initial_state.update(workflow_inputs.get("resume_values") or {})
            first_node_name = _workflow_start_node(resume_from, workflow_scope)

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
            first_node_event = _workflow_event(
                events,
                "workflow.node.started",
                run_id=run_id,
                thread_id=thread_id,
                node_name=first_node_name,
                status="running",
                message=f"正在执行：{_workflow_node_label(first_node_name)}",
                attempt=first_node_attempt,
                iteration_kind=first_node_iteration_kind,
            )
            for frame in _workflow_ag_ui_frames(
                encoder,
                run_id=run_id,
                thread_id=thread_id,
                events=events,
                result=result,
            ):
                yield frame
            process_sequence = 1
            yield _process_frame(
                encoder,
                id=_process_step_id(first_node_name, first_node_attempt),
                kind="workflow",
                status="running",
                title=f"正在执行 {_workflow_node_label(first_node_name)}",
                detail=str(first_node_event["message"]),
                sequence=process_sequence,
                node_name=first_node_name,
                attempt=first_node_attempt,
                iteration_kind=first_node_iteration_kind,
            )
            reasoning_steps: dict[str, str] = {}
            tool_steps: dict[str, dict[str, str]] = {}
            tool_indexes: dict[int, str] = {}

            async for stream_mode, chunk in active_graph.astream(
                initial_state,
                config=config,
                stream_mode=["updates", "messages", "custom"],
            ):
                if stream_mode == "custom":
                    progress = chunk if isinstance(chunk, dict) else {}
                    event_type = progress.get("type")
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
                    if event_type == "integration_test.checks":
                        checks = integration_test_checks(progress)
                        if not checks:
                            continue
                        process_sequence += 1
                        test_attempt = _current_node_attempt(
                            node_attempts, "integration_test"
                        )
                        yield _process_frame(
                            encoder,
                            id=_process_step_id("integration_test", test_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_workflow_node_label('integration_test')}",
                            detail=integration_test_check_summary(checks),
                            sequence=process_sequence,
                            checks=checks,
                            node_name="integration_test",
                            attempt=test_attempt,
                            iteration_kind=_iteration_kind(
                                "integration_test", test_attempt
                            ),
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
                    if not isinstance(update, dict):
                        continue

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
                        or f"完成：{_workflow_node_label(node_name)}",
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
                        integration_test_checks(update.get("test_results", []))
                        if node_name == "integration_test"
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
                        title=_terminal_process_title(node_name, terminal_status),
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
                    )
                    for frame in _tool_result_frames(
                        encoder,
                        update=update,
                        tool_steps=tool_steps,
                        sequence=process_sequence,
                    ):
                        yield frame

                    for next_node in _workflow_next_nodes(node_name, update):
                        next_attempt = _next_node_attempt(node_attempts, next_node)
                        next_iteration_kind = _iteration_kind(next_node, next_attempt)
                        next_event = _workflow_event(
                            events,
                            "workflow.node.started",
                            run_id=run_id,
                            thread_id=thread_id,
                            node_name=next_node,
                            status="running",
                            message=f"正在执行：{_workflow_node_label(next_node)}",
                            attempt=next_attempt,
                            iteration_kind=next_iteration_kind,
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
                        yield _process_frame(
                            encoder,
                            id=_process_step_id(next_node, next_attempt),
                            kind="workflow",
                            status="running",
                            title=f"正在执行 {_workflow_node_label(next_node)}",
                            detail=str(next_event["message"]),
                            sequence=process_sequence,
                            node_name=next_node,
                            attempt=next_attempt,
                            iteration_kind=next_iteration_kind,
                        )

            result = dict((await active_graph.aget_state(config)).values)
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
        except Exception as exc:
            error_code = getattr(exc, "code", None)
            result = {
                "status": "failed",
                "phase": "failed",
                "error": str(exc),
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
            for frame in _text_delta_frames(
                encoder,
                message_id,
                f"{summary['message']}\n",
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
                            "workflow": failed_payload,
                            "summary": summary,
                            "events": events,
                            "result": result,
                        }
                    ),
                )
            )
        finally:
            # 正常完成和消费端取消都必须释放任务注册及工作区占用。
            workflow_run_registry.unregister(run_id, task)
            if workspace_lease is not None:
                workspace_lease.release()

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
