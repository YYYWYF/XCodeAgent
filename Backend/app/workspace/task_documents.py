from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

from app.services.build_task_plan_lifecycle import DraftIdentity
from app.workspace.json_documents import write_json_atomic
from app.workspace.spec_documents import workflow_artifact_root


def build_task_plan_json_path(state: dict[str, Any]) -> Path:
    """返回当前工作区唯一的 Build Task Plan JSON 路径，不接受旧 checkpoint 路径覆盖。"""

    return workflow_artifact_root(state) / "plans" / "build-task-plan.json"


def build_task_plan_pending_json_path(state: dict[str, Any]) -> Path:
    """返回当前工作区唯一的 Pending Build Task Plan JSON 路径。"""

    return workflow_artifact_root(state) / "plans" / "build-task-plan.pending.json"


def build_task_plan_sha256(build_task_plan: dict[str, Any]) -> str:
    """计算 Build DAG 的规范化内容摘要，作为一次 Build Run 的唯一计划身份。"""

    return hashlib.sha256(
        json.dumps(
            build_task_plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


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
    planning_run_id: str,
    base_confirmed_plan_digest: str | None,
    input_fingerprint: str,
    build_execution_scope: Mapping[str, Any],
    created_at: str,
) -> str:
    """由后端元数据构造 DraftIdentity，并原子写入已验证的 PendingPlan。"""

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

    pending_plan = deepcopy(build_task_plan)
    pending_plan["confirmation_status"] = "pending"
    pending_plan["confirmed_at"] = None
    identity = DraftIdentity(
        planning_run_id=planning_run_id,
        draft_digest="0" * 64,
        base_confirmed_plan_digest=base_confirmed_plan_digest,
        input_fingerprint=input_fingerprint,
        build_execution_scope=build_execution_scope,
        created_at=created_at,
    )
    pending_plan["draft_identity"] = identity.model_dump(mode="json")
    identity = identity.model_copy(
        update={"draft_digest": build_task_plan_draft_sha256(pending_plan)}
    )
    pending_plan["draft_identity"] = identity.model_dump(mode="json")
    validate_pending_self_digest(pending_plan)

    path = build_task_plan_pending_json_path(state)
    write_json_atomic(path, pending_plan)
    return str(path)


def load_confirmed_build_task_plan(workspace_root: str | Path) -> dict[str, Any] | None:
    """只读正式路径中已确认且有效的 v3 DAG，缺失或不合格时返回空基线。"""

    path = build_task_plan_json_path({"workspace": str(workspace_root)})
    try:
        plan = load_build_task_plan_json(path)
    except FileNotFoundError:
        return None

    # 不读取 checkpoint 或 pending 文件，也不补齐状态；损坏 JSON 和其他读取错误直接上抛。
    if (
        not isinstance(plan, dict)
        or plan.get("confirmation_status") != "confirmed"
        or plan.get("schema_version") != "build-dag.v3"
        or plan.get("status") == "failed"
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
