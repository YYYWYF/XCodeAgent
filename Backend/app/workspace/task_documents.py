from __future__ import annotations

import json
import hashlib
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workflow_artifact_root


_TASK_RUNTIME_FIELDS = {
    "status",
    "last_result_status",
    "failure_category",
    "failure_reason",
    "failure_detail",
    "acceptance_status",
    "acceptance_evidence",
    "business_acceptance_evidence",
    "business_acceptance_summary",
    "scheduler",
    "updated_by",
    "updated_at",
}
_SUMMARY_RUNTIME_FIELDS = {
    "pending",
    "running",
    "completed",
    "already_satisfied",
    "failed",
    "results",
}


def build_task_plan_json_path(state: dict[str, Any]) -> Path:
    """返回当前工作区唯一的 Build Task Plan JSON 路径，不接受旧 checkpoint 路径覆盖。"""

    return workflow_artifact_root(state) / "plans" / "build-task-plan.json"


def build_task_plan_sha256(build_task_plan: dict[str, Any]) -> str:
    """计算 Build DAG 的规划内容摘要，运行状态变化不视为计划漂移。"""

    return hashlib.sha256(
        json.dumps(
            _build_task_planning_projection(build_task_plan),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _build_task_planning_projection(
    build_task_plan: dict[str, Any],
) -> dict[str, Any]:
    """移除调度器运行字段，只保留 Build Run 必须锁定的规划合同。"""

    projected = deepcopy(build_task_plan)
    registry = projected.get("task_registry")
    if isinstance(registry, dict):
        projected["task_registry"] = {
            task_id: {
                key: value
                for key, value in task.items()
                if key not in _TASK_RUNTIME_FIELDS
            }
            if isinstance(task, dict)
            else task
            for task_id, task in registry.items()
        }
    summary = projected.get("summary")
    if isinstance(summary, dict):
        projected["summary"] = {
            key: value
            for key, value in summary.items()
            if key not in _SUMMARY_RUNTIME_FIELDS
        }
    projected.pop("last_update", None)
    return projected


def build_run_task_plan_json_path(state: dict[str, Any], build_run_id: str) -> Path:
    """返回指定 Build Run 的只读任务计划副本路径。"""

    normalized_id = str(build_run_id or "").strip()
    if not re.fullmatch(r"build-[a-f0-9]{32}", normalized_id):
        raise ValueError("Build Run 标识无效，不能创建任务计划副本。")
    return workflow_artifact_root(state) / "plans" / "build-runs" / f"{normalized_id}.json"


def write_build_run_task_plan_json(
    state: dict[str, Any],
    *,
    build_run_id: str,
    build_task_plan: dict[str, Any],
) -> str:
    """持久化一次 Build Run 唯一使用的只读计划副本。"""

    path = build_run_task_plan_json_path(state, build_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_task_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def repair_task_plan_json_path(state: dict[str, Any]) -> Path:
    existing_path = state.get("repair_task_plan_path")
    return (
        Path(existing_path)
        if existing_path
        else workflow_artifact_root(state) / "plans" / "repair-task-plan.json"
    )


def write_build_task_plan_json(
    state: dict[str, Any],
    build_task_plan: dict[str, Any],
) -> str:
    path = build_task_plan_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_task_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def write_build_task_plan_execution_state(
    state: dict[str, Any],
    runtime_build_task_plan: dict[str, Any],
) -> str:
    """把同一规划合同的任务运行状态回写到工作区权威 DAG。"""

    path = build_task_plan_json_path(state)
    persisted = load_build_task_plan_json(path)
    expected_sha256 = str(state.get("build_run_plan_sha256") or "").strip()
    if not expected_sha256:
        raise ValueError("当前 Build Run 缺少任务计划摘要，不能回写执行状态。")
    if build_task_plan_sha256(persisted) != expected_sha256:
        raise ValueError("工作区 Build Task Plan 已变化，不能覆盖新的规划内容。")

    persisted_registry = persisted.get("task_registry")
    runtime_registry = runtime_build_task_plan.get("task_registry")
    if not isinstance(persisted_registry, dict) or not isinstance(runtime_registry, dict):
        raise ValueError("Build Task Plan 缺少有效 task_registry，不能回写执行状态。")

    next_registry: dict[str, Any] = {}
    for task_id, persisted_task in persisted_registry.items():
        runtime_task = runtime_registry.get(task_id)
        if not isinstance(persisted_task, dict) or not isinstance(runtime_task, dict):
            next_registry[task_id] = persisted_task
            continue
        next_task = deepcopy(persisted_task)
        for field in _TASK_RUNTIME_FIELDS:
            if field in runtime_task:
                next_task[field] = deepcopy(runtime_task[field])
            else:
                next_task.pop(field, None)
        next_registry[task_id] = next_task

    statuses = [
        str(task.get("status") or "pending")
        for task in next_registry.values()
        if isinstance(task, dict)
    ]
    summary = (
        deepcopy(persisted.get("summary"))
        if isinstance(persisted.get("summary"), dict)
        else {}
    )
    summary.update(
        {
            "pending": statuses.count("pending"),
            "running": statuses.count("running"),
            "completed": statuses.count("completed")
            + statuses.count("already_satisfied"),
            "already_satisfied": statuses.count("already_satisfied"),
            "failed": statuses.count("failed"),
        }
    )
    runtime_results = runtime_build_task_plan.get("summary")
    if isinstance(runtime_results, dict) and "results" in runtime_results:
        summary["results"] = runtime_results["results"]

    persisted["task_registry"] = next_registry
    persisted["summary"] = summary
    persisted["last_update"] = {
        "stage": "build_scheduler",
        "updated_by": "build-scheduler",
        "updated_at": datetime.now(UTC).isoformat(),
    }
    return write_build_task_plan_json(state, persisted)


def load_build_task_plan_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_repair_task_plan_json(
    state: dict[str, Any],
    repair_task_plan: dict[str, Any],
) -> str:
    path = repair_task_plan_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(repair_task_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def load_repair_task_plan_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
