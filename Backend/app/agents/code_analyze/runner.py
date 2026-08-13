"""前端代码审查 Agent 的有界调用和强制 Skill 轨迹校验。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

from app.agents.tool_activity_stream import (
    AgentInvocationCancelled,
    ToolActivity,
    invoke_agent_with_tool_activity,
)
from app.services.code_analysis import (
    FrontendSourceInventory,
    code_audit_report_relative_path,
    discover_frontend_sources,
    read_code_audit_report,
    resolve_code_analysis_workspace,
)
from app.tools.code_audit_report import create_code_audit_tools


ToolActivityCallback = Callable[[ToolActivity], None]
_REQUIRED_FIRST_TOOL = "load_mayun_frontend_code_review_skill"
_REQUIRED_REPORT_TOOL = "save_code_audit_report"


def run_frontend_code_analysis(
    workspace_root: str,
    *,
    cancellation_event: Event | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> dict[str, Any]:
    """发现前端源码、执行专用 Agent，并以落盘报告生成公开结果。"""

    cancel_event = cancellation_event or Event()
    root = resolve_code_analysis_workspace(workspace_root)
    inventory = discover_frontend_sources(root)
    report_path = code_audit_report_relative_path()
    tools, tool_state = create_code_audit_tools(
        root,
        report_path,
        cancellation_requested=cancel_event.is_set,
    )
    from app.agents.registry import create_code_analyze_agent_for_workspace

    agent = create_code_analyze_agent_for_workspace(
        workspace_root=str(root),
        source_roots=tuple(inventory.roots),
        tools=tools,
    )
    activities: list[ToolActivity] = []

    def capture(activity: ToolActivity) -> None:
        """保存工具轨迹并向协议层投射安全化活动。"""

        activities.append(activity)
        if on_tool_activity is not None:
            on_tool_activity(activity)

    invoke_agent_with_tool_activity(
        agent,
        {
            "messages": [
                {
                    "role": "user",
                    "content": _code_analysis_prompt(inventory, report_path),
                }
            ]
        },
        workspace=str(root),
        on_tool_activity=capture,
        cancellation_requested=cancel_event.is_set,
    )
    if cancel_event.is_set():
        raise AgentInvocationCancelled("代码审查运行已取消。")
    _validate_required_tool_trace(activities, tool_state)
    _content, report_summary = read_code_audit_report(root, report_path)
    if int(report_summary.get("reportedFileCount") or 0) != len(inventory.files):
        raise RuntimeError("代码审查报告中的检视文件数与确定性源码清单不一致。")
    report_summary.pop("reportedFileCount", None)
    return {
        "action": "scan",
        "reportPath": report_path,
        "scannedFiles": len(inventory.files),
        "generatedAt": datetime.fromtimestamp(
            Path(root / report_path).stat().st_mtime
        ).astimezone().isoformat(),
        **report_summary,
    }


def _code_analysis_prompt(
    inventory: FrontendSourceInventory,
    report_path: str,
) -> str:
    """构造只含工作区相对路径和有界候选清单的审查输入。"""

    manifest = inventory.files[:1_000]
    omitted = max(0, len(inventory.files) - len(manifest))
    return (
        "Audit the current frontend source snapshot. Your first tool call MUST be "
        "load_mayun_frontend_code_review_skill; do not browse any workspace path before it. "
        "After loading the skill, cover every supplied source root with glob/grep and progressively "
        "read only evidence-bearing files. The deterministic inventory count is authoritative. "
        "Never read Backend/backend. Save the final report only through save_code_audit_report.\n\n"
        f"Required report path: /{report_path}\n"
        f"Frontend source roots: {json.dumps(inventory.roots, ensure_ascii=False)}\n"
        f"Deterministic source file count: {len(inventory.files)}\n"
        f"Candidate manifest (first {len(manifest)} paths; {omitted} omitted):\n"
        f"{json.dumps(manifest, ensure_ascii=False)}"
    )


def _validate_required_tool_trace(
    activities: list[ToolActivity],
    tool_state: dict[str, Any],
) -> None:
    """确认首个模型工具为强制 Skill，并且正式报告只保存一次。"""

    started_tools: list[str] = []
    seen_calls: set[str] = set()
    for activity in activities:
        if activity.get("status") != "running":
            continue
        call_id = str(activity.get("callId") or "")
        if not call_id or call_id in seen_calls:
            continue
        seen_calls.add(call_id)
        started_tools.append(str(activity.get("tool") or ""))
    if not started_tools or started_tools[0] != _REQUIRED_FIRST_TOOL:
        raise RuntimeError("codeAnalyzeAgent 未把 mayun-frontend-code-review 作为首个工具调用。")
    if int(tool_state.get("loadCount") or 0) != 1:
        raise RuntimeError("codeAnalyzeAgent 必须且只能加载一次前端审查 Skill。")
    if started_tools.count(_REQUIRED_REPORT_TOOL) != 1 or not tool_state.get("reportSaved"):
        raise RuntimeError("codeAnalyzeAgent 未通过受控工具生成唯一正式报告。")
