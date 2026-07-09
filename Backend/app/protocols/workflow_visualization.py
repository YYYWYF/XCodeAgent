from __future__ import annotations

from typing import Any, AsyncIterator, Iterable
from uuid import uuid4

from ag_ui.core import (
    CustomEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi.encoders import jsonable_encoder

from app.protocols.workflow_request import workflow_run_inputs

WORKFLOW_EVENT_PROTOCOL = "xcodeagent.workflow.event.v1"

WORKFLOW_NODE_LABELS = {
    "classify_request_complexity": "判断需求复杂度",
    "requirements": "需求确认与 RequirementSpec",
    "direct_modification": "简单需求直接修改",
    "project_planning": "项目级计划生成",
    "detail_confirmation": "页面细节确认",
    "prepare_build_tasks": "构建任务 DAG 生成",
    "build": "代码生成与构建协调",
    "integration_test": "集成测试与质量门禁",
    "launch_project": "启动本地预览",
    "acceptance": "用户验收",
    "finalize_project": "完成项目",
    "handle_failure": "失败处理",
}

WORKFLOW_STATIC_NEXT_NODES = {
    "requirements": ["project_planning"],
    "direct_modification": ["integration_test"],
    "project_planning": ["detail_confirmation"],
    "detail_confirmation": ["prepare_build_tasks"],
    "prepare_build_tasks": ["build"],
    "build": ["integration_test"],
    "launch_project": ["acceptance"],
    "acceptance": ["finalize_project"],
}

WORKFLOW_ARTIFACT_FIELDS = (
    "requirement_spec_path",
    "requirement_spec_json_path",
    "project_plan_path",
    "project_plan_json_path",
    "build_task_plan_path",
    "test_report_path",
    "repair_task_plan_path",
)


def workflow_capabilities() -> dict[str, Any]:
    return {
        "name": "workflow-run",
        "description": (
            "Run the app-generation workflow as an AG-UI SSE stream with custom "
            "events, state snapshots, and assistant-facing summary text."
        ),
        "endpoint": "/workflow/run",
        "transport": "ag-ui-sse",
        "input": {
            "request": "Optional one-line user requirement for simple HTTP callers.",
            "message": "Optional alias for request.",
            "messages": "AG-UI-style messages; the last user message is used when request/message is absent.",
            "threadId": "Optional AG-UI thread id reused as the workflow thread id.",
            "runId": "Optional AG-UI run id reused as the workflow run id.",
            "projectId": "Optional project id used by workflow document writers.",
            "workspace": "Optional project workspace path/context reference.",
            "forwardedProps.workspaceRoot": "Preferred workspace path for AG-UI callers.",
            "forwardedProps.application": "Optional application metadata; application.id and workspaceRoot are used as fallbacks.",
            "originalRequest": "Optional original request used when submitting clarification answers.",
            "clarificationAnswers": "Optional structured user answers that are merged with originalRequest before rerunning workflow.",
            "resumeState": "Optional previous workflow payload/state used by the backend to infer which waiting phase to resume.",
            "resumeFrom": "Optional backend/debug override for the workflow phase to resume from. Currently supports requirements, project_planning, detail_confirmation, and prepare_build_tasks.",
        },
        "output": {
            "summary": "Human-readable and machine-readable workflow result summary.",
            "events": f"Ordered event list using {WORKFLOW_EVENT_PROTOCOL}.",
            "agUi": "AG-UI-compatible custom-event/state-snapshot payload for frontend visualization.",
            "result": "Final LangGraph ProjectState.",
        },
        "eventProtocol": {
            "version": WORKFLOW_EVENT_PROTOCOL,
            "eventTypes": [
                "workflow.run.started",
                "workflow.node.started",
                "workflow.node.completed",
                "workflow.run.finished",
                "workflow.run.failed",
            ],
        },
        "agUi": {
            "customEventName": "workflow-run",
            "stateSnapshotKey": "workflow",
            "suggestedAgentMode": "workflow",
        },
        "phases": [
            {"id": node_id, "label": label}
            for node_id, label in WORKFLOW_NODE_LABELS.items()
        ],
    }


def build_workflow_ag_ui_stream(
    *,
    graph: Any,
    payload: dict[str, Any],
    accept: str | None = None,
) -> AsyncIterator[str]:
    encoder = EventEncoder(accept or "text/event-stream")
    workflow_inputs = workflow_run_inputs(payload)
    thread_id = workflow_inputs["thread_id"] or str(uuid4())
    run_id = workflow_inputs["run_id"] or f"workflow-{uuid4().hex[:12]}"
    message_id = str(uuid4())

    async def stream() -> AsyncIterator[str]:
        events: list[dict[str, Any]] = []
        result: dict[str, Any] = {}

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

            project_id = workflow_inputs["project_id"] or None
            workspace = workflow_inputs["workspace"] or None
            resume_from = workflow_inputs.get("resume_from") or None
            initial_state: dict[str, Any] = {
                "request": request,
                "timeline": [],
            }
            initial_state.update(workflow_inputs.get("resume_values") or {})
            first_node_name = _workflow_start_node(resume_from)

            if resume_from:
                initial_state["resume_from"] = resume_from

            if project_id:
                initial_state["project_id"] = project_id

            if workspace:
                initial_state["workspace"] = workspace

            config = {"configurable": {"thread_id": thread_id}}

            started_event = _workflow_event(
                events,
                "workflow.run.started",
                run_id=run_id,
                thread_id=thread_id,
                status="running",
                message="Workflow run started.",
                data={"request": request, "projectId": project_id, "resumeFrom": resume_from},
            )
            for frame in _workflow_ag_ui_frames(
                encoder,
                run_id=run_id,
                thread_id=thread_id,
                events=events,
                result=result,
                event=started_event,
            ):
                yield frame
            for frame in _text_delta_frames(
                encoder,
                message_id,
                f"{started_event['message']}\n",
            ):
                yield frame

            first_node_event = _workflow_event(
                events,
                "workflow.node.started",
                run_id=run_id,
                thread_id=thread_id,
                node_name=first_node_name,
                status="running",
                message=f"正在执行：{_workflow_node_label(first_node_name)}",
            )
            for frame in _workflow_ag_ui_frames(
                encoder,
                run_id=run_id,
                thread_id=thread_id,
                events=events,
                result=result,
                event=first_node_event,
            ):
                yield frame
            for frame in _text_delta_frames(
                encoder,
                message_id,
                f"{first_node_event['message']}\n",
            ):
                yield frame

            async for chunk in graph.astream(
                initial_state,
                config=config,
                stream_mode="updates",
            ):
                for node_name, update in chunk.items():
                    if not isinstance(update, dict):
                        continue

                    detail = _workflow_node_detail(node_name, update)
                    node_payload = {
                        "phase": update.get("phase", node_name),
                        "stateDelta": update,
                        "detail": detail.get("data", {}),
                        "artifacts": _workflow_artifacts(update),
                    }
                    completed_event = _workflow_event(
                        events,
                        "workflow.node.completed",
                        run_id=run_id,
                        thread_id=thread_id,
                        node_name=node_name,
                        status=str(update.get("status") or "completed"),
                        message=detail.get("message")
                        or f"完成：{_workflow_node_label(node_name)}",
                        data=node_payload,
                    )
                    for frame in _workflow_ag_ui_frames(
                        encoder,
                        run_id=run_id,
                        thread_id=thread_id,
                        events=events,
                        result=update,
                        event=completed_event,
                    ):
                        yield frame
                    for frame in _text_delta_frames(
                        encoder,
                        message_id,
                        f"{completed_event['message']}\n",
                    ):
                        yield frame

                    for next_node in _workflow_next_nodes(node_name, update):
                        next_event = _workflow_event(
                            events,
                            "workflow.node.started",
                            run_id=run_id,
                            thread_id=thread_id,
                            node_name=next_node,
                            status="running",
                            message=f"正在执行：{_workflow_node_label(next_node)}",
                        )
                        for frame in _workflow_ag_ui_frames(
                            encoder,
                            run_id=run_id,
                            thread_id=thread_id,
                            events=events,
                            result=update,
                            event=next_event,
                        ):
                            yield frame
                        for frame in _text_delta_frames(
                            encoder,
                            message_id,
                            f"{next_event['message']}\n",
                        ):
                            yield frame

            result = dict(graph.get_state(config).values)
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
                event=finished_event,
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
                            "result": result,
                        }
                    ),
                )
            )
        except Exception as exc:
            result = {"status": "failed", "phase": "failed", "error": str(exc)}
            summary = _workflow_summary(result, events)
            summary["message"] = f"Workflow failed：{type(exc).__name__}: {exc}"
            failed_event = _workflow_event(
                events,
                "workflow.run.failed",
                run_id=run_id,
                thread_id=thread_id,
                status="failed",
                message=summary["message"],
                data={"error": {"type": type(exc).__name__, "message": str(exc)}},
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
                event=failed_event,
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

    return stream()


async def build_workflow_response(
    *,
    graph: Any,
    request: str,
    project_id: str | None = None,
    workspace: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    resume_from: str | None = None,
) -> dict[str, Any]:
    thread_id = thread_id or str(uuid4())
    run_id = run_id or f"workflow-{uuid4().hex[:12]}"
    initial_state: dict[str, Any] = {
        "request": request,
        "timeline": [],
    }
    request_inputs = workflow_run_inputs(
        {
            "request": request,
            "project_id": project_id,
            "workspace": workspace,
            "thread_id": thread_id,
            "run_id": run_id,
            "resume_from": resume_from,
        }
    )
    initial_state.update(request_inputs.get("resume_values") or {})
    first_node_name = _workflow_start_node(resume_from)

    if resume_from:
        initial_state["resume_from"] = resume_from

    if project_id:
        initial_state["project_id"] = project_id

    if workspace:
        initial_state["workspace"] = workspace

    config = {"configurable": {"thread_id": thread_id}}
    events: list[dict[str, Any]] = []
    result: dict[str, Any] = {}

    _workflow_event(
        events,
        "workflow.run.started",
        run_id=run_id,
        thread_id=thread_id,
        status="running",
        message="Workflow run started.",
        data={"request": request, "projectId": project_id, "resumeFrom": resume_from},
    )
    _workflow_event(
        events,
        "workflow.node.started",
        run_id=run_id,
        thread_id=thread_id,
        node_name=first_node_name,
        status="running",
        message=f"正在执行：{_workflow_node_label(first_node_name)}",
    )

    try:
        async for chunk in graph.astream(
            initial_state,
            config=config,
            stream_mode="updates",
        ):
            for node_name, update in chunk.items():
                if not isinstance(update, dict):
                    continue

                detail = _workflow_node_detail(node_name, update)
                node_payload = {
                    "phase": update.get("phase", node_name),
                    "stateDelta": update,
                    "detail": detail.get("data", {}),
                    "artifacts": _workflow_artifacts(update),
                }
                _workflow_event(
                    events,
                    "workflow.node.completed",
                    run_id=run_id,
                    thread_id=thread_id,
                    node_name=node_name,
                    status=str(update.get("status") or "completed"),
                    message=detail.get("message")
                    or f"完成：{_workflow_node_label(node_name)}",
                    data=node_payload,
                )

                for next_node in _workflow_next_nodes(node_name, update):
                    _workflow_event(
                        events,
                        "workflow.node.started",
                        run_id=run_id,
                        thread_id=thread_id,
                        node_name=next_node,
                        status="running",
                        message=f"正在执行：{_workflow_node_label(next_node)}",
                    )

        result = dict(graph.get_state(config).values)
        summary = _workflow_summary(result, events)
        _workflow_event(
            events,
            "workflow.run.finished",
            run_id=run_id,
            thread_id=thread_id,
            status=str(summary.get("status") or "completed"),
            message=str(summary.get("message") or "Workflow run finished."),
            data={"summary": summary},
        )
    except Exception as exc:
        result = {"status": "failed", "phase": "failed", "error": str(exc)}
        summary = _workflow_summary(result, events)
        summary["message"] = f"Workflow failed：{type(exc).__name__}: {exc}"
        _workflow_event(
            events,
            "workflow.run.failed",
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            message=summary["message"],
            data={"error": {"type": type(exc).__name__, "message": str(exc)}},
        )

    visual_payload = _workflow_visual_payload(
        run_id=run_id,
        thread_id=thread_id,
        summary=summary,
        events=events,
        result=result,
    )

    return {
        "tool": "workflow-run",
        "status": summary.get("status"),
        "runId": run_id,
        "threadId": thread_id,
        "capability": workflow_capabilities(),
        "summary": summary,
        "events": events,
        "agUi": {
            "customEvent": {
                "name": "workflow-run",
                "value": visual_payload,
            },
            "stateSnapshot": {
                "workflow": visual_payload,
            },
            "textMessage": summary.get("message"),
        },
        "timeline": result.get("timeline", []),
        "quality_gate_passed": result.get("quality_gate_passed"),
        "needs_revision": result.get("needs_revision"),
        "preview_url": result.get("preview_url"),
        "artifacts": summary.get("artifacts", {}),
        "result": result,
    }


def _workflow_ag_ui_frames(
    encoder: EventEncoder,
    *,
    run_id: str,
    thread_id: str,
    events: list[dict[str, Any]],
    result: dict[str, Any],
    event: dict[str, Any],
    visual_payload: dict[str, Any] | None = None,
) -> Iterable[str]:
    payload = visual_payload or _workflow_visual_payload(
        run_id=run_id,
        thread_id=thread_id,
        summary=_workflow_progress_summary(result, events),
        events=events,
        result=result,
    )
    safe_event = jsonable_encoder(event)
    safe_payload = jsonable_encoder(payload)

    yield encoder.encode(
        CustomEvent(
            name=str(event.get("agUi", {}).get("customEventName") or event["type"]),
            value=safe_event,
        )
    )
    yield encoder.encode(CustomEvent(name="workflow-run", value=safe_payload))
    yield encoder.encode(StateSnapshotEvent(snapshot={"workflow": safe_payload}))


def _text_delta_frames(
    encoder: EventEncoder,
    message_id: str,
    text: str,
    *,
    size: int = 80,
) -> Iterable[str]:
    for chunk in _chunk_text(text, size=size):
        yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=chunk))


def _chunk_text(text: str, *, size: int = 80) -> Iterable[str]:
    if not text:
        yield ""
        return

    for index in range(0, len(text), size):
        yield text[index : index + size]


def _workflow_progress_summary(
    result: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    last_event = events[-1] if events else {}
    completed_nodes = [
        event for event in events if event.get("type") == "workflow.node.completed"
    ]
    failed_events = [event for event in events if str(event.get("status")) == "failed"]
    node = last_event.get("node") if isinstance(last_event.get("node"), dict) else {}

    return {
        "status": last_event.get("status") or result.get("status") or "running",
        "phase": result.get("phase") or node.get("id"),
        "message": last_event.get("message") or "Workflow is running.",
        "completedNodeCount": len(completed_nodes),
        "failedEventCount": len(failed_events),
        "timeline": result.get("timeline", []),
        "qualityGatePassed": result.get("quality_gate_passed"),
        "needsRevision": result.get("needs_revision"),
        "previewUrl": result.get("preview_url"),
        "buildSummary": result.get("build_summary", {}),
        "testSummary": {},
        "artifacts": _workflow_artifacts(result),
        "clarification": result.get("clarification", {}),
    }


def _workflow_node_label(node_name: str) -> str:
    return WORKFLOW_NODE_LABELS.get(node_name, node_name)


def _workflow_start_node(resume_from: str | None) -> str:
    if resume_from == "requirements":
        return "requirements"
    if resume_from == "project_planning":
        return "project_planning"
    if resume_from == "detail_confirmation":
        return "detail_confirmation"
    if resume_from == "prepare_build_tasks":
        return "prepare_build_tasks"
    return "classify_request_complexity"


def _workflow_next_nodes(node_name: str, update: dict[str, Any]) -> list[str]:
    if node_name == "classify_request_complexity":
        return (
            ["direct_modification"]
            if update.get("request_complexity") == "simple"
            else ["requirements"]
        )
    if node_name == "integration_test":
        return (
            ["launch_project"]
            if update.get("quality_gate_passed")
            else ["handle_failure"]
        )
    if node_name == "requirements":
        clarification = update.get("clarification")
        if (
            isinstance(clarification, dict)
            and clarification.get("status") == "requires_user_input"
        ):
            return []
        return ["project_planning"]
    if node_name == "project_planning":
        if update.get("status") == "requires_user_input":
            return []
        return ["detail_confirmation"]
    if node_name == "detail_confirmation":
        if update.get("status") == "requires_user_input":
            return []
        return ["prepare_build_tasks"]
    if node_name == "prepare_build_tasks":
        if update.get("status") == "requires_user_input":
            return []
        return ["build"]
    return WORKFLOW_STATIC_NEXT_NODES.get(node_name, [])


def _workflow_artifacts(value: dict[str, Any]) -> dict[str, Any]:
    return {
        field: value.get(field)
        for field in WORKFLOW_ARTIFACT_FIELDS
        if value.get(field)
    }


def _workflow_node_detail(node_name: str, update: dict[str, Any]) -> dict[str, Any]:
    if node_name == "classify_request_complexity":
        return {
            "message": f"复杂度={update.get('request_complexity')}，原因={update.get('complexity_reason')}",
            "data": {
                "requestComplexity": update.get("request_complexity"),
                "complexityDecision": update.get("complexity_decision"),
            },
        }
    if node_name == "requirements":
        clarification = update.get("clarification")
        questions = (
            clarification.get("questions", [])
            if isinstance(clarification, dict)
            and isinstance(clarification.get("questions"), list)
            else []
        )
        status = (
            clarification.get("status")
            if isinstance(clarification, dict)
            else None
        )
        message = f"需求文档={update.get('requirement_spec_path')}"
        if questions:
            message += f"，待确认问题={len(questions)}"
        return {
            "message": message,
            "data": {
                "clarification": clarification,
                "requiresUserInput": status == "requires_user_input",
            },
        }
    if node_name == "project_planning":
        clarification = update.get("clarification")
        status = update.get("status")
        if status == "requires_user_input":
            questions = (
                clarification.get("questions", [])
                if isinstance(clarification, dict)
                and isinstance(clarification.get("questions"), list)
                else []
            )
            return {
                "message": (
                    f"计划文档={update.get('project_plan_path')}，"
                    f"待确认问题={len(questions)}"
                ),
                "data": {
                    "projectPlan": update.get("project_plan"),
                    "clarification": clarification,
                    "requiresUserInput": True,
                },
            }
        return {
            "message": (
                f"计划文档={update.get('project_plan_path')}，"
                f"结构化状态={update.get('project_plan_json_path')}"
            ),
            "data": {"projectPlan": update.get("project_plan")},
        }
    if node_name == "detail_confirmation":
        clarification = update.get("clarification")
        status = update.get("status")
        if status == "requires_user_input":
            questions = (
                clarification.get("questions", [])
                if isinstance(clarification, dict)
                and isinstance(clarification.get("questions"), list)
                else []
            )
            return {
                "message": f"页面/数据源详细设计待确认，问题={len(questions)}",
                "data": {
                    "clarification": clarification,
                    "requiresUserInput": True,
                    "detailSelection": update.get("detail_selection"),
                    "pageSpecDraft": update.get("page_spec_draft"),
                },
            }
        return {
            "message": f"页面={update.get('selected_page_id')}，计划文档已更新",
            "data": {
                "detailSelection": update.get("detail_selection"),
                "pageSpecConfirmation": update.get("page_spec_confirmation"),
                "detailPlans": update.get("detail_plans", []),
            },
        }
    if node_name == "prepare_build_tasks":
        clarification = update.get("clarification")
        if update.get("status") == "requires_user_input":
            questions = (
                clarification.get("questions", [])
                if isinstance(clarification, dict)
                and isinstance(clarification.get("questions"), list)
                else []
            )
            return {
                "message": f"ProjectPlan 未确认，已阻止代码生成，待确认问题={len(questions)}",
                "data": {
                    "projectPlan": update.get("project_plan"),
                    "clarification": clarification,
                    "requiresUserInput": True,
                },
            }
        tasks = update.get("tasks") if isinstance(update.get("tasks"), list) else []
        return {
            "message": f"任务数={len(tasks)}，任务DAG={update.get('build_task_plan_path')}",
            "data": {
                "buildTaskPlan": update.get("build_task_plan"),
                "taskCount": len(tasks),
            },
        }
    if node_name == "build":
        summary = update.get("build_summary", {})
        return {
            "message": f"完成={summary.get('completed', 0)}，失败={summary.get('failed', 0)}",
            "data": {
                "buildSummary": summary,
                "buildEvents": update.get("build_events", []),
                "buildResults": update.get("build_results", []),
            },
        }
    if node_name == "integration_test":
        report = update.get("test_report", {})
        summary = report.get("summary", {}) if isinstance(report, dict) else {}
        return {
            "message": (
                f"通过={report.get('passed') if isinstance(report, dict) else None}，"
                f"检查={summary.get('passed', 0)}/{summary.get('total', 0)}，"
                f"报告={update.get('test_report_path')}"
            ),
            "data": {
                "testReport": report,
                "testEvents": update.get("test_events", []),
                "qualityGatePassed": update.get("quality_gate_passed"),
                "needsRevision": update.get("needs_revision"),
                "revisionRequests": update.get("revision_requests", []),
                "repairTaskPlan": update.get("repair_task_plan"),
            },
        }
    if node_name == "launch_project":
        return {
            "message": f"预览地址={update.get('preview_url')}",
            "data": {"previewUrl": update.get("preview_url")},
        }
    if node_name == "acceptance":
        return {
            "message": f"验收={update.get('accepted')}",
            "data": {"accepted": update.get("accepted")},
        }
    if node_name in {"finalize_project", "handle_failure"}:
        return {
            "message": f"状态={update.get('status')}",
            "data": {"status": update.get("status"), "phase": update.get("phase")},
        }
    return {"message": "", "data": {}}


def _workflow_event(
    events: list[dict[str, Any]],
    event_type: str,
    *,
    run_id: str,
    thread_id: str,
    node_name: str | None = None,
    status: str = "running",
    message: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "id": f"workflow-event-{len(events) + 1:04d}",
        "protocol": WORKFLOW_EVENT_PROTOCOL,
        "sequence": len(events) + 1,
        "type": event_type,
        "runId": run_id,
        "threadId": thread_id,
        "status": status,
        "message": message,
        "nodeName": node_name,
        "node": (
            {"id": node_name, "label": _workflow_node_label(node_name)}
            if node_name
            else None
        ),
        "data": data or {},
        "agUi": {
            "customEventName": event_type.replace(".", "-"),
            "stateSnapshotKey": "workflow",
        },
    }
    events.append(event)
    return event


def _workflow_summary(
    result: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    status = str(result.get("status") or "completed")
    completed_nodes = [
        event for event in events if event.get("type") == "workflow.node.completed"
    ]
    failed_events = [event for event in events if str(event.get("status")) == "failed"]
    test_report = (
        result.get("test_report") if isinstance(result.get("test_report"), dict) else {}
    )
    test_summary = (
        test_report.get("summary")
        if isinstance(test_report.get("summary"), dict)
        else {}
    )
    build_summary = (
        result.get("build_summary")
        if isinstance(result.get("build_summary"), dict)
        else {}
    )
    clarification = (
        result.get("clarification")
        if isinstance(result.get("clarification"), dict)
        else {}
    )
    artifacts = _workflow_artifacts(result)
    if status == "requires_user_input":
        question_count = len(clarification.get("questions", []))
        message = f"Workflow 等待用户确认/补充：完成 {len(completed_nodes)} 个节点，待确认问题 {question_count} 个。"
    else:
        message = (
            f"Workflow {status}：完成 {len(completed_nodes)} 个节点，"
            f"质量门禁={'通过' if result.get('quality_gate_passed') else '未通过'}。"
        )
    if result.get("preview_url"):
        message += f" 预览地址：{result.get('preview_url')}。"

    return {
        "status": status,
        "phase": result.get("phase"),
        "message": message,
        "completedNodeCount": len(completed_nodes),
        "failedEventCount": len(failed_events),
        "timeline": result.get("timeline", []),
        "qualityGatePassed": result.get("quality_gate_passed"),
        "needsRevision": result.get("needs_revision"),
        "previewUrl": result.get("preview_url"),
        "buildSummary": build_summary,
        "testSummary": test_summary,
        "artifacts": artifacts,
        "clarification": clarification,
    }


def _workflow_visual_payload(
    *,
    run_id: str,
    thread_id: str,
    summary: dict[str, Any],
    events: list[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "runId": run_id,
        "threadId": thread_id,
        "summary": summary,
        "events": events,
        "state": {
            "status": summary.get("status"),
            "request": result.get("request"),
            "phase": summary.get("phase"),
            "timeline": summary.get("timeline", []),
            "artifacts": summary.get("artifacts", {}),
            "qualityGatePassed": summary.get("qualityGatePassed"),
            "needsRevision": summary.get("needsRevision"),
            "previewUrl": summary.get("previewUrl"),
            "tasks": result.get("tasks", []),
            "buildSummary": result.get("build_summary", {}),
            "testReport": result.get("test_report", {}),
            "repairTaskPlan": result.get("repair_task_plan"),
            "clarification": result.get("clarification", {}),
            "project_plan": result.get("project_plan"),
            "pending_project_plan": result.get("pending_project_plan"),
            "project_plan_path": result.get("project_plan_path"),
            "project_plan_json_path": result.get("project_plan_json_path"),
            "detail_selection": result.get("detail_selection"),
            "selected_page_id": result.get("selected_page_id"),
            "selected_data_source_id": result.get("selected_data_source_id"),
            "page_spec_draft": result.get("page_spec_draft"),
        },
        "result": result,
    }
