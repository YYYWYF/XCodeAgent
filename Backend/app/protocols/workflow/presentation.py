"""统一公开展示状态：只保留一份 state，大块正文通过不可变资料引用读取。"""

from __future__ import annotations

from typing import Any

from app.workspace.content_artifacts import pack_materials, put_artifact
from app.services.ui_design_manifest import present_ui_pages

SUMMARY_FIELDS = frozenset({
    "status", "phase", "message", "request", "errorCode", "failureDiagnostic",
    "completedNodeCount", "failedEventCount", "qualityGatePassed", "needsRevision",
    "previewUrl", "unitTestGatePassed", "unitTestNextAction", "unitTestRepairIteration",
    "unitTestMaxRepairIterations", "integrationNextAction", "repairReturnNode",
    "repairIteration", "maxRepairIterations", "requiresUserInput", "intent",
})
DOCUMENT_FIELDS = frozenset({"requirement_spec", "product_plan", "technical_plan", "project_plan", "pending_project_plan"})
INTERNAL_FIELDS = frozenset({
    "tasks", "build_task_plan", "buildTaskPlan", "build_context", "build_results", "build_events",
    "code_change_sets", "workspace_snapshot", "unit_test_generation_context", "code_graph_index",
    "test_events", "code_review_events", "unit_test_code_change_sets", "small_task_code_change_sets",
})


def _task_view(value: Any) -> Any:
    """任务展示保留进度、范围和验收结果，移除重复的执行源资料与内部检查输入。"""
    if isinstance(value, list):
        return [_task_view(item) for item in value]
    if isinstance(value, dict):
        return {key: _task_view(item) for key, item in value.items()
                if key not in {"source_refs", "acceptance_checks", "build_context"}}
    return value


def _code_changes(workspace: str, value: Any) -> Any:
    """代码变更列表只带路径与统计，点击查看时按 hash 读取原始 diff。"""
    if not isinstance(value, dict) or not isinstance(value.get("files"), list):
        return value
    files = []
    for item in value["files"]:
        if not isinstance(item, dict):
            continue
        content = item.get("diff")
        file = {key: child for key, child in item.items() if key != "diff"}
        if isinstance(content, str) and content:
            file["diffRef"] = put_artifact(workspace, content)
        files.append(file)
    return {**value, "files": files}


def workflow_presentation(payload: dict[str, Any], *, workspace: str | None) -> dict[str, Any]:
    """将生产者的内部结果投影成唯一公开 state，不把执行状态复制成 result。"""
    previous = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    state = {**result, **previous, **{key: item for key, item in summary.items() if key not in SUMMARY_FIELDS}}
    # 确认后 clarification 会切换阶段入口，公开页面仍需携带当前 ProductPlan 的中文名称。
    designs, product = state.get("ui_designs"), state.get("product_plan")
    if isinstance(designs, dict) and isinstance(product, dict):
        state["ui_designs"] = {**designs, "pages": present_ui_pages(designs, product)}
    # 正文外置后仍投影当前版本的确认事实，前端不读取大块正文来决定模板交接。
    plan = state.get("technical_plan")
    state["technicalPlanConfirmed"] = isinstance(plan, dict) and plan.get("confirmation_status") == "confirmed"
    state["workspace"] = workspace or state.get("workspace")
    view = {key: item for key, item in payload.items() if key not in {"result", "state", "summary"}}
    view["summary"] = {key: item for key, item in summary.items() if key in SUMMARY_FIELDS}
    if workspace:
        references: dict[str, Any] = {}
        for key in DOCUMENT_FIELDS | INTERNAL_FIELDS:
            item = state.pop(key, None)
            if item is not None:
                references[key] = put_artifact(workspace, pack_materials(workspace, item))
        if references:
            state["artifactRefs"] = references
        # 任务资料可能同时嵌在确认卡 taskPlan、task_registry 与进度投影中；
        # 所有展示分支统一去掉执行源材料，完整版本已经留在 checkpoint/资料引用里。
        state = _task_view(state)
        state = pack_materials(workspace, state)
        raw_changes = payload.get("codeChanges") or state.pop("codeChanges", None) or state.pop("code_changes", None)
        state.pop("codeChanges", None)
        state.pop("code_changes", None)
        if raw_changes:
            view["codeChanges"] = _code_changes(workspace, raw_changes)
        artifact = payload.get("confirmationArtifact")
        if isinstance(artifact, dict):
            content = artifact.get("content")
            view["confirmationArtifact"] = {key: item for key, item in artifact.items() if key != "content"}
            if isinstance(content, str):
                view["confirmationArtifact"]["contentRef"] = put_artifact(workspace, content)
    view["state"] = state
    return view
