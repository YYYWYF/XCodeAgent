"""前端代码审查的独立 AG-UI 协议。"""

from __future__ import annotations

import asyncio
import re
from concurrent.futures import Future
from threading import Event
from typing import Any, AsyncIterator

from app.agents.code_analyze.runner import run_frontend_code_analysis
from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    build_ag_ui_action_stream,
)
from app.services.code_analysis import (
    CodeAnalysisReportRequest,
    CodeAnalysisScanRequest,
    read_code_audit_report,
    resolve_code_analysis_workspace,
)


CODE_ANALYSIS_EVENT_NAME = "code-analysis"


def code_analysis_capabilities() -> dict[str, Any]:
    """发布前端代码审查端点的稳定 AG-UI 契约。"""

    return {
        "name": "code-analysis",
        "endpoint": "/code-analysis/run",
        "transport": "ag-ui-sse",
        "actions": ["scan", "get-report"],
        "customEventName": CODE_ANALYSIS_EVENT_NAME,
        "stateSnapshotKey": "codeAnalysis",
        "reportRoot": ".xcodeagent/codeAudit",
        "workflowIndependent": True,
        "cancellable": True,
    }


def build_code_analysis_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """校验 forwardedProps 并创建完整代码审查 AG-UI 生命周期。"""

    code_analysis_input = _code_analysis_input(payload)
    action = code_analysis_input.get("action")

    async def streaming_operation(report, _report_text) -> AgUiActionResult:
        """执行扫描或安全读取报告，并持续上报阶段和工具活动。"""

        if action == "get-report":
            request = CodeAnalysisReportRequest.model_validate(code_analysis_input)
            root = resolve_code_analysis_workspace(request.workspace_root)
            content, summary = read_code_audit_report(root, request.report_path)
            await report(
                AgUiActionProgress(
                    stage="completed",
                    message="代码审查报告已加载",
                    percent=100,
                    data={"action": action, "reportPath": request.report_path},
                )
            )
            return AgUiActionResult(
                data={
                    "action": action,
                    "reportPath": request.report_path,
                    "content": content,
                    **summary,
                },
                message="已加载前端代码审查报告。",
            )

        request = CodeAnalysisScanRequest.model_validate(code_analysis_input)
        cancellation_event = Event()
        pending_reports: list[Future[None]] = []
        loop = asyncio.get_running_loop()

        async def stage(name: str, message: str, percent: int) -> None:
            """发送不包含宿主机路径的扫描阶段。"""

            await report(
                AgUiActionProgress(
                    stage=name,
                    message=message,
                    percent=percent,
                    data={"action": action},
                )
            )

        def activity_callback(activity: dict[str, Any]) -> None:
            """从工作线程把安全化工具活动转发回事件循环。"""

            tool_name = activity.get("tool")
            if tool_name == "load_mayun_frontend_code_review_skill":
                stage_name = "loading_skill"
            elif tool_name == "save_code_audit_report":
                stage_name = "writing_report"
            else:
                stage_name = "analyzing"
            pending_reports.append(
                asyncio.run_coroutine_threadsafe(
                    report(
                        AgUiActionProgress(
                            stage=stage_name,
                            message=str(activity.get("message") or "正在分析前端代码"),
                            percent=(
                                90
                                if stage_name == "writing_report"
                                else 15
                                if stage_name == "loading_skill"
                                else 55
                            ),
                            data={"action": action, "activeToolActivity": activity},
                        )
                    ),
                    loop,
                )
            )

        await stage("validating_workspace", "正在校验工作区", 5)
        await stage("loading_skill", "正在加载前端代码审查规范", 15)
        await stage("discovering_sources", "正在发现前端业务源码", 25)
        try:
            result = await asyncio.to_thread(
                run_frontend_code_analysis,
                request.workspace_root,
                cancellation_event=cancellation_event,
                on_tool_activity=activity_callback,
            )
            if pending_reports:
                await asyncio.gather(
                    *(asyncio.wrap_future(item) for item in pending_reports),
                    return_exceptions=True,
                )
            await stage("writing_report", "正在校验并保存审查报告", 95)
            await stage("completed", "前端代码扫描已完成", 100)
            return AgUiActionResult(
                data=result,
                message=(
                    f"前端代码扫描完成：已扫描 {result['scannedFiles']} 个文件，"
                    f"发现 {result['issueCount']} 个问题。"
                ),
            )
        except Exception as exc:
            raise RuntimeError(
                _sanitized_code_analysis_error(exc, request.workspace_root)
            ) from None
        finally:
            cancellation_event.set()

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=CODE_ANALYSIS_EVENT_NAME,
        state_key="codeAnalysis",
        run_id_prefix="code-analysis",
        streaming_operation=streaming_operation,
        error_message_prefix="前端代码扫描失败",
        error_data=lambda _exc: {"action": action},
        accept=accept,
        emit_progress_text=False,
    )


def _code_analysis_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 中提取代码审查输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    code_analysis = forwarded_props.get("codeAnalysis")
    return code_analysis if isinstance(code_analysis, dict) else {}


def _sanitized_code_analysis_error(exc: Exception, workspace_root: str) -> str:
    """保留可操作错误原因，同时剥离工作区及常见宿主机绝对路径。"""

    message = str(exc).replace(workspace_root, "工作区") if workspace_root else str(exc)
    message = re.sub(
        r"(?:[A-Za-z]:/|/(?:Users|home|private|var)/)[^\s,;:]+",
        "工作区路径",
        message,
    )
    return message[:500] or "前端代码扫描执行失败。"
