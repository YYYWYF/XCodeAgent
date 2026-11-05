"""事件只保存展示上下文增量；Graph 状态和正式产物继续由原持久化层拥有。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


EVENT_STATE_SCALARS = frozenset({
    "phase", "status", "selectedPageId", "selected_api_contract_id",
    "selected_endpoint_id", "selected_entity_id", "detail_target_type",
    "requirement_spec_path", "product_plan_path", "technical_plan_path",
    "project_plan_path", "test_report_path", "workspace_revision",
    "requirementsConfirmed",
})
_DOCUMENT_DETAILS = frozenset({
    "buildTaskPlan", "productPlan", "projectPlan", "technicalPlan",
})
_TRACE_DETAILS = frozenset({"buildEvents", "buildResults", "testEvents"})
_RUN_SUMMARY_FIELDS = frozenset({
    "status", "phase", "message", "errorCode", "completedNodeCount", "failedEventCount",
    "qualityGatePassed", "needsRevision", "unitTestGatePassed",
})


def _execution_slice(value: Any) -> Any:
    """事件中的任务保留展示及验收证据，执行源文档仍留在正式计划与 checkpoint。"""

    if not isinstance(value, dict) or not isinstance(value.get("tasks"), list):
        return value
    return {
        **value,
        "tasks": [
            {key: item for key, item in task.items() if key not in {"source_refs", "acceptance_checks"}}
            for task in value["tasks"] if isinstance(task, dict)
        ],
    }


def event_state_context(value: dict[str, Any]) -> dict[str, Any]:
    """只选择事件消费者实际读取的有界上下文，不遍历任务、源文档或完整快照。"""

    context = {
        key: item[:4096] if isinstance(item, str) else item
        for key, item in value.items()
        if key in EVENT_STATE_SCALARS
        and (item is None or isinstance(item, (str, bool, int, float)))
        and not (key.endswith("_path") and str(item).lower().endswith(".json"))
    }
    if isinstance(value.get("timeline"), list):
        context["timeline"] = [
            item[:160] for item in value["timeline"][-256:] if isinstance(item, str)
        ]
    # 该字段只能接收既有十二 KB 展示投影，不能用内部 workspace_snapshot 代替。
    if isinstance(value.get("workspaceInspection"), dict):
        context["workspaceInspection"] = value["workspaceInspection"]
    return context


def compact_event_data(
    events: list[dict[str, Any]], data: dict[str, Any] | None,
    *, run_id: str,
) -> dict[str, Any]:
    """冻结当前事件的轻量详情，并仅记录相对同一事件序列发生变化的展示字段。"""

    source = data or {}
    compact = {key: item for key, item in source.items() if key != "stateDelta"}
    summary = source.get("summary")
    if isinstance(summary, dict):
        # 终态事件也不能借 summary 再附带 buildTaskPlan、clarification 或 lifecycle 全量副本。
        compact["summary"] = {
            key: item for key, item in summary.items()
            if key in _RUN_SUMMARY_FIELDS and (item is None or isinstance(item, (str, bool, int, float)))
        }
        for key in ("failureDiagnostic", "artifacts"):
            if isinstance(summary.get(key), dict):
                compact["summary"][key] = summary[key]
    detail = source.get("detail")
    if isinstance(detail, dict):
        compact["detail"] = {
            key: _execution_slice(item) if key == "buildExecutionSlice" else item
            for key, item in detail.items()
            if key not in _DOCUMENT_DETAILS | _TRACE_DETAILS
        }
        # 完整计划、执行结果和日志仍在 state/result/checkpoint；事件只记录关联数量。
        for key in _DOCUMENT_DETAILS:
            document = detail.get(key)
            if isinstance(document, dict):
                graph = document.get("task_graph")
                tasks = graph.get("tasks") if isinstance(graph, dict) else None
                compact["detail"][f"{key}Summary"] = {
                    field: document[field] for field in ("status", "schema_version", "version")
                    if isinstance(document.get(field), (str, int, bool))
                }
                if isinstance(tasks, list):
                    compact["detail"][f"{key}Summary"]["taskCount"] = len(tasks)
        for key in _TRACE_DETAILS:
            if isinstance(detail.get(key), list):
                compact["detail"][f"{key}Count"] = len(detail[key])
    raw_state = source.get("stateDelta")
    if isinstance(raw_state, dict):
        current = event_state_context(raw_state)
        previous: dict[str, Any] = {}
        missing = set(current)
        # 从最近事件向前寻找每个字段的最后一次变更，不复制或重建完整 Graph State。
        for event in reversed(events):
            if event.get("runId") != run_id:
                continue
            delta = (event.get("data") or {}).get("stateDelta")
            if not isinstance(delta, dict):
                continue
            for key in missing.intersection(delta):
                previous[key] = delta[key]
            missing.difference_update(delta)
            if not missing:
                break
        delta = {
            key: item for key, item in current.items()
            if key not in previous or previous[key] != item
        }
        if delta:
            compact["stateDelta"] = delta
    return deepcopy(compact)
