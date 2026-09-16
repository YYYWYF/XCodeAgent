from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import hmac
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from threading import Lock, RLock

from app.services.build_task_plan_lifecycle import DraftIdentity
from app.services.planning_frozen import plain_json
from app.workspace.json_documents import write_json_atomic
from app.workspace.spec_documents import workflow_artifact_root, workspace_root


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

# 同一后端进程内按规范化工作区隔离 Pending 写入与 Confirm 的读验写删，避免不同应用互相阻塞。
_PENDING_LIFECYCLE_LOCKS: dict[str, RLock] = {}
_PENDING_LIFECYCLE_LOCKS_GUARD = Lock()


def build_task_plan_lifecycle_lock(workspace: str | Path) -> RLock:
    """返回指定工作区共享的 Pending 生命周期可重入锁。"""

    key = _pending_lifecycle_workspace_key(workspace)
    with _PENDING_LIFECYCLE_LOCKS_GUARD:
        lock = _PENDING_LIFECYCLE_LOCKS.get(key)
        if lock is None:
            lock = RLock()
            _PENDING_LIFECYCLE_LOCKS[key] = lock
        return lock


def _pending_lifecycle_workspace_key(workspace: str | Path) -> str:
    """生成 Pending 生命周期锁使用的规范化绝对工作区键。"""

    return os.path.normcase(str(Path(workspace).expanduser().resolve(strict=False)))


def build_task_plan_json_path(state: dict[str, Any]) -> Path:
    """返回当前工作区唯一的 Build Task Plan JSON 路径，不接受旧 checkpoint 路径覆盖。"""

    return workflow_artifact_root(state) / "plans" / "build-task-plan.json"


def build_task_plan_pending_json_path(state: dict[str, Any]) -> Path:
    """返回当前工作区唯一的 Pending Build Task Plan JSON 路径。"""

    return (
        workflow_artifact_root(state)
        / "drafts"
        / "plans"
        / "build-task-plan.pending.json"
    )


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


def build_task_plan_draft_sha256(pending_plan: Mapping[str, Any]) -> str:
    """计算 PendingPlan 的 canonical SHA-256，并排除 draft_digest 自身字段。"""

    if not isinstance(pending_plan, Mapping):
        raise ValueError("PendingPlan 必须是 JSON object。")
    canonical = deepcopy(dict(pending_plan))
    identity = canonical.get("draft_identity")
    if isinstance(identity, Mapping):
        identity_without_self = deepcopy(dict(identity))
        identity_without_self.pop("draft_digest", None)
        canonical["draft_identity"] = identity_without_self
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_pending_self_digest(pending_plan: Mapping[str, Any]) -> DraftIdentity:
    """验证 PendingPlan 的 DraftIdentity schema 与服务端重算摘要完全一致。"""

    if not isinstance(pending_plan, Mapping):
        raise ValueError("PendingPlan 必须是 JSON object。")
    identity = DraftIdentity.model_validate(pending_plan.get("draft_identity"))
    actual_digest = build_task_plan_draft_sha256(pending_plan)
    if not hmac.compare_digest(identity.draft_digest, actual_digest):
        raise ValueError("PendingPlan 的 draft_digest 与当前内容不匹配。")
    return identity


def build_run_task_plan_json_path(state: dict[str, Any], build_run_id: str) -> Path:
    """返回指定 Build Run 的只读任务计划副本路径。"""

    normalized_id = str(build_run_id or "").strip()
    if not re.fullmatch(r"build-[a-f0-9]{32}", normalized_id):
        raise ValueError("Build Run 标识无效，不能创建任务计划副本。")
    return workflow_artifact_root(state) / "plans" / "build-runs" / f"{normalized_id}.json"


def build_run_success_evidence_path(state: dict[str, Any], build_run_id: str) -> Path:
    """返回 Build Run 成功执行证据路径，和只读 Task Plan 副本严格分离。"""

    normalized_id = str(build_run_id or "").strip()
    if not re.fullmatch(r"build-[a-f0-9]{32}", normalized_id):
        raise ValueError("Build Run 标识无效，不能定位成功执行证据。")
    return workflow_artifact_root(state) / "plans" / "build-runs" / f"{normalized_id}.evidence.json"


def persist_build_run_success_evidence(
    state: dict[str, Any], *, build_run_id: str, route_facts: Mapping[str, Any],
) -> str:
    """仅在整个 Build 成功后原子写入该 Run 的最小路由事实执行证据。"""

    facts = _validated_route_facts(route_facts)
    path = build_run_success_evidence_path(state, build_run_id)
    write_json_atomic(path, {
        "buildRunId": build_run_id,
        "status": "completed",
        "completedAt": datetime.now(UTC).isoformat(),
        "routeFacts": facts,
    })
    return str(path)


def load_latest_successful_build_route_facts(state: dict[str, Any]) -> dict[str, dict[str, str | None]] | None:
    """读取最近成功 Build 的路由执行证据；旧 Run 缺失该字段时返回空基线。"""

    directory = workflow_artifact_root(state) / "plans" / "build-runs"
    if not directory.is_dir():
        return None
    candidates: list[tuple[str, dict[str, dict[str, str | None]]]] = []
    for path in directory.glob("build-*.evidence.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, Mapping) or value.get("status") != "completed":
                continue
            completed_at = value.get("completedAt")
            if not isinstance(completed_at, str) or not completed_at:
                continue
            candidates.append((completed_at, _validated_route_facts(value.get("routeFacts"))))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _validated_route_facts(value: Mapping[str, Any] | Any) -> dict[str, dict[str, str | None]]:
    """校验成功证据中的最小 Route Facts，拒绝把损坏证据当成比较基线。"""

    if not isinstance(value, Mapping):
        raise ValueError("Build Run routeFacts 必须是对象。")
    facts: dict[str, dict[str, str | None]] = {}
    for page_id, fact in value.items():
        if not isinstance(page_id, str) or not page_id.strip() or not isinstance(fact, Mapping):
            raise ValueError("Build Run routeFacts 页面身份无效。")
        name, resource_key = fact.get("name"), fact.get("resourceKey")
        if not isinstance(name, str) or not name.strip() or resource_key is not None and (not isinstance(resource_key, str) or not resource_key.strip()):
            raise ValueError("Build Run routeFacts 内容无效。")
        facts[page_id] = {"name": name, "resourceKey": resource_key}
    return facts


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

    # 与 Pending 确认共用互斥锁，避免运行状态覆盖刚提升的正式计划。
    with build_task_plan_lifecycle_lock(workspace_root(state)):
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
        write_json_atomic(path, persisted)
        return str(path)


def load_build_task_plan_json(path: str | Path) -> dict[str, Any]:
    """读取指定路径中的 Build Task Plan JSON。"""

    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_pending_build_task_plan(state: dict[str, Any]) -> dict[str, Any] | None:
    """读取独立 PendingPlan；文件不存在时返回空，损坏内容直接报错。"""

    path = build_task_plan_pending_json_path(state)
    try:
        plan = load_build_task_plan_json(path)
    except FileNotFoundError:
        return None
    if not isinstance(plan, dict):
        raise ValueError("build-task-plan.pending.json 必须是 JSON object。")
    return plan


def write_pending_build_task_plan_atomic(
    state: dict[str, Any],
    build_task_plan: dict[str, Any],
    *,
    owner_session_id: str,
    planning_run_id: str,
    workflow_run_id: str,
    base_confirmed_plan_digest: str | None,
    input_fingerprint: str,
    build_execution_scope: Mapping[str, Any],
    created_at: str,
) -> str:
    """由后端元数据构造 DraftIdentity，并原子写入已验证的 PendingPlan。

    Root Scope 缺失时由服务端补齐；若 assembled Scope 与当前权威 Scope 不同则
    fail closed，保证 PendingPlan、DraftIdentity 和 PlanningRun 绑定同一 Scope。
    """

    if not isinstance(build_task_plan, dict):
        raise ValueError("PendingPlan 必须是 JSON object。")
    if "draft_identity" in build_task_plan:
        raise ValueError("assembled BuildTaskPlan 不得预置 draft_identity。")
    task_graph = build_task_plan.get("task_graph")
    validation = task_graph.get("validation") if isinstance(task_graph, dict) else None
    if (
        build_task_plan.get("status") == "failed"
        or not isinstance(validation, dict)
        or validation.get("is_valid") is not True
    ):
        raise ValueError("只有通过 task_graph validation 的 BuildTaskPlan 才能写入 PendingPlan。")

    if not isinstance(build_execution_scope, Mapping):
        raise ValueError("服务端 authoritative build_execution_scope 必须是 JSON object。")
    authoritative_scope = deepcopy(plain_json(build_execution_scope))
    if "build_execution_scope" in build_task_plan:
        assembled_scope = build_task_plan["build_execution_scope"]
        if (
            not isinstance(assembled_scope, Mapping)
            or plain_json(assembled_scope) != authoritative_scope
        ):
            raise ValueError(
                "assembled BuildTaskPlan 的 build_execution_scope 与当前 PlanningRun 不一致，拒绝写入。"
            )

    pending_plan = deepcopy(build_task_plan)
    # 新 Pending 不携带旧 Formal lifecycle 或 Build runtime 的 root metadata。
    pending_plan.pop("confirmed_from", None)
    pending_plan.pop("last_update", None)
    pending_plan["build_execution_scope"] = authoritative_scope
    pending_plan["confirmation_status"] = "pending"
    pending_plan["confirmed_at"] = None
    identity = DraftIdentity(
        owner_session_id=owner_session_id,
        planning_run_id=planning_run_id,
        workflow_run_id=workflow_run_id,
        draft_digest="0" * 64,
        base_confirmed_plan_digest=base_confirmed_plan_digest,
        input_fingerprint=input_fingerprint,
        build_execution_scope=authoritative_scope,
        created_at=created_at,
    )
    pending_plan["draft_identity"] = identity.model_dump(mode="json")
    identity = identity.model_copy(
        update={"draft_digest": build_task_plan_draft_sha256(pending_plan)}
    )
    pending_plan["draft_identity"] = identity.model_dump(mode="json")
    validate_pending_self_digest(pending_plan)

    path = build_task_plan_pending_json_path(state)
    with build_task_plan_lifecycle_lock(workspace_root(state)):
        write_json_atomic(path, pending_plan)
    return str(path)


def load_confirmed_build_task_plan(workspace_root: str | Path) -> dict[str, Any] | None:
    """只读正式路径中已确认且有效的当前 v4 DAG，缺失或不合格时返回空基线。"""

    path = build_task_plan_json_path({"workspace": str(workspace_root)})
    try:
        plan = load_build_task_plan_json(path)
    except FileNotFoundError:
        return None

    # 不读取 checkpoint 或 pending 文件，也不补齐状态；损坏 JSON 和其他读取错误直接上抛。
    if (
        not isinstance(plan, dict)
        or plan.get("confirmation_status") != "confirmed"
        or plan.get("schema_version") != "build-dag.v4"
        or plan.get("status") == "failed"
    ):
        return None
    unit_graph = plan.get("unit_graph")
    unit_validation = unit_graph.get("validation") if isinstance(unit_graph, dict) else None
    if (
        not isinstance(unit_validation, dict)
        or unit_validation.get("is_valid") is not True
        or unit_validation.get("errors")
    ):
        return None
    task_graph = plan.get("task_graph")
    validation = task_graph.get("validation") if isinstance(task_graph, dict) else None
    if not isinstance(validation, dict) or validation.get("is_valid") is not True:
        return None
    return plan


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
