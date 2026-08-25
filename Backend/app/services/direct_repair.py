"""自由对话自动修复的确定性范围、计划和升级规则。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.direct_modification import direct_path_matches_owner
from app.services.small_task import build_small_task_handoff
from app.services.small_task_scope import (
    _task_paths,
    small_task_preflight,
    workflow_target_for_small_task,
)
from app.workspace.task_documents import write_repair_task_plan_json


REPAIR_OWNERS = ("frontend", "backend")


def authorized_direct_repair_paths(state: dict[str, Any]) -> dict[str, list[str]]:
    """从真实前后端差异提取本轮自动修复的父级授权路径。"""

    paths = {owner: [] for owner in REPAIR_OWNERS}

    def add(owner: str, path: Any) -> None:
        """仅记录职责目录内且非敏感的相对路径。"""

        normalized = _normalize_path(path)
        if not normalized or not _safe_repair_path(normalized):
            return
        if owner in paths and direct_path_matches_owner(normalized, owner):
            if normalized.casefold() not in {item.casefold() for item in paths[owner]}:
                paths[owner].append(normalized)

    stage_results = state.get("direct_stage_results")
    if isinstance(stage_results, dict):
        for stage, result in stage_results.items():
            owner = "backend" if stage in {"backend", "data_source"} else stage
            if owner not in paths or not isinstance(result, dict):
                continue
            for path in result.get("changedFiles", []):
                add(owner, path)

    change_sets = [
        *direct_repair_dict_list(state.get("direct_code_change_sets")),
        *direct_repair_dict_list(state.get("small_task_code_change_sets")),
        state.get("code_changes") if isinstance(state.get("code_changes"), dict) else {},
    ]
    for change_set in change_sets:
        for file_item in change_set.get("files", []) if isinstance(change_set, dict) else []:
            if not isinstance(file_item, dict):
                continue
            for owner in REPAIR_OWNERS:
                add(owner, file_item.get("path"))

    handoff = state.get("backend_handoff")
    if isinstance(handoff, dict):
        for path in handoff.get("changedFiles", []):
            add("backend", path)
    return paths


def scoped_direct_repair_tasks(
    authorized_paths: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """把父级授权文件编译成供 RepairPlanner 继承的最小执行切片。"""

    return [
        {
            "id": f"conversation:{owner}",
            "owner": owner,
            "unit_id": f"conversation:{owner}",
            "status": "completed",
            "allowed_paths": list(paths),
            "target_files": list(paths),
            "change_scope": [
                {"operation": "modify", "path": path, "description": "局部测试失败修复。"}
                for path in paths
            ],
        }
        for owner, paths in authorized_paths.items()
        if paths
    ]


def normalize_direct_revision_requests(value: Any) -> list[dict[str, Any]]:
    """把测试返修 owner 归一为自由对话可执行的前端、后端或数据库边界。"""

    normalized: list[dict[str, Any]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        failed_check = (
            dict(raw.get("failed_check"))
            if isinstance(raw.get("failed_check"), dict)
            else {}
        )
        check_id = str(failed_check.get("id") or raw.get("id") or "unknown_check")
        failed_check.setdefault("id", check_id)
        failed_check.setdefault("name", str(raw.get("reason") or check_id))
        failed_check.setdefault("passed", False)
        failed_check.setdefault(
            "evidence",
            str(raw.get("evidence") or raw.get("reason") or ""),
        )
        owners = raw.get("owners") if isinstance(raw.get("owners"), list) else []
        owners = owners or [raw.get("owner")]
        mapped = _unique(
            _normalize_repair_owner(owner, check_id)
            for owner in owners
            if str(owner or "").strip()
        )
        if not mapped:
            mapped = [_normalize_repair_owner("", check_id)]
        item["failed_check"] = failed_check
        item["owner"] = mapped[0]
        item["owners"] = mapped
        normalized.append(item)
    return normalized[:50]


def normalize_direct_repair_tasks(
    value: Any,
    *,
    authorized_paths: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], str]:
    """校验 Planner 任务只能使用本轮真实差异中的精确文件。"""

    result: list[dict[str, Any]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        task = deepcopy(raw)
        owner = _normalize_repair_owner(task.get("owner"), "")
        if owner not in {"frontend", "backend", "database"}:
            return result, "RepairPlanner 返回了无法识别的修复 owner，已停止自动修复。"
        task["owner"] = owner
        paths = _task_paths(task)
        if owner in authorized_paths:
            allowed = {path.casefold() for path in authorized_paths[owner]}
            outside = [
                path
                for path in paths
                if _normalize_path(path).casefold() not in allowed
            ]
            if outside and not all(_is_placeholder_path(path) for path in outside):
                return result, "RepairPlanner 请求修改本轮授权之外的文件，已停止自动修复。"
        result.append(task)
    return result, ""


def repair_plan_handoff(plan: dict[str, Any]) -> dict[str, Any]:
    """把 RepairPlanner 的范围升级转换为现有 SmallTask 确认卡。"""

    requested_paths = [
        str(path) for path in plan.get("requestedPaths", []) if str(path).strip()
    ]
    requested_resources = plan.get("requestedResources", [])
    escalation = {
        "reason": str(plan.get("reason") or "修复计划需要扩大范围或产品决策。"),
        "requestedPaths": requested_paths,
        "requestedResources": (
            requested_resources if isinstance(requested_resources, list) else []
        ),
        "workflowIntent": "development_readiness_gate",
    }
    return build_small_task_handoff(
        mode=(
            "small_task_scope_confirmation"
            if requested_paths
            else "small_task_workflow_handoff"
        ),
        reason=escalation["reason"],
        tasks=candidate_repair_tasks(plan),
        escalation=escalation,
        target_node=workflow_target_for_small_task(escalation),
    )


def candidate_repair_tasks(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """读取需要用户确认的候选任务，不把其视为已授权可执行任务。"""

    value = plan.get("candidateTasks") or plan.get("tasks") or []
    return [deepcopy(item) for item in value if isinstance(item, dict)]


def terminal_repair_plan(*, reason: str) -> dict[str, Any]:
    """构造可持久化的自由对话自动修复终止计划。"""

    return {
        "version": "0.1.0",
        "status": "terminal_failure",
        "decision": "terminal_failure",
        "source": "conversation",
        "reason": reason,
        "tasks": [],
    }


def persist_direct_repair_plan(state: dict[str, Any], plan: dict[str, Any]) -> str:
    """持久化内部 RepairPlan，失败时返回空路径而不掩盖业务终态。"""

    if not plan:
        return ""
    try:
        return write_repair_task_plan_json(state, plan)
    except OSError:
        return ""


def first_direct_repair_preflight(tasks: list[dict[str, Any]]) -> dict[str, str]:
    """返回第一个不允许自动执行的任务边界。"""

    for task in tasks:
        result = small_task_preflight(task)
        if result:
            return result
    return {}


def direct_repair_plan_has_files(change_set: Any) -> bool:
    """判断快照差异是否包含真实文件。"""

    return bool(
        isinstance(change_set, dict)
        and any(
            isinstance(item, dict) and str(item.get("path") or "").strip()
            for item in change_set.get("files", [])
        )
    )


def direct_repair_dict_list(value: Any) -> list[dict[str, Any]]:
    """从任意状态值提取字典列表并保持有界结构。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _normalize_repair_owner(owner: Any, check_id: str) -> str:
    """统一测试 owner 命名，并为缺省 owner 按检查前缀选择后端或前端。"""

    value = str(owner or "").strip().lower()
    if value in {"data_source", "datasource"}:
        return "backend"
    if value in {"frontend", "backend", "database"}:
        return value
    if str(check_id).startswith("frontend_"):
        return "frontend"
    if str(check_id).startswith("backend_") or check_id == "api_contract":
        return "backend"
    return "database" if value else "backend"


def _safe_repair_path(path: str) -> bool:
    """拒绝越出工作区、敏感文件和内部正式工件路径。"""

    parts = path.casefold().split("/")
    return bool(path) and ".." not in parts and ".xcodeagent" not in parts and not any(
        part == ".env" or part.startswith(".env.") for part in parts
    )


def _normalize_path(value: Any) -> str:
    """统一 Agent 产生的相对路径格式。"""

    return str(value or "").strip().replace("\\", "/").lstrip("/").rstrip("/")


def _is_placeholder_path(path: str) -> bool:
    """识别不能提供写入授权的命令级哨兵路径。"""

    return path.casefold().startswith("<no file paths")


def _unique(values: Any) -> list[str]:
    """按输入顺序去重字符串值。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result
