"""SmallTask 的路径边界、并行冲突和正式工作流升级判定。"""

from __future__ import annotations

import fnmatch
from typing import Any


SMALL_TASK_DEFAULT_CONCURRENCY = 2
SMALL_TASK_MAX_CONCURRENCY = 3
_FORMAL_PATH_MARKERS = (
    ".xcodeagent/",
    "requirement-spec",
    "project-plan",
    "build-task-plan",
    "build-task-dag",
)
_DATABASE_PATH_MARKERS = ("migration/", "migrations/", "schema/", "ddl/")


def select_parallel_small_task_batch(
    tasks: list[dict[str, Any]],
    *,
    max_concurrency: int = SMALL_TASK_DEFAULT_CONCURRENCY,
) -> list[dict[str, Any]]:
    """从待执行任务中选出依赖满足且路径、资源互不冲突的并行批次。"""

    limit = max(
        1,
        min(
            int(max_concurrency or SMALL_TASK_DEFAULT_CONCURRENCY),
            SMALL_TASK_MAX_CONCURRENCY,
        ),
    )
    completed_ids = {
        str(task.get("id") or "")
        for task in tasks
        if str(task.get("status") or "") in {"completed", "already_satisfied"}
    }
    candidates: list[dict[str, Any]] = []
    for task in tasks:
        if str(task.get("status") or "pending") != "pending":
            continue
        dependencies = set(_string_list(task.get("dependencies"), limit=100))
        if not dependencies.issubset(completed_ids):
            continue
        if task.get("can_run_in_parallel") is False:
            if not candidates:
                return [task]
            continue
        candidates.append(task)

    selected: list[dict[str, Any]] = []
    for candidate in candidates:
        if any(_tasks_conflict(candidate, existing) for existing in selected):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected or candidates[:1]


def small_task_preflight(task: dict[str, Any]) -> dict[str, str]:
    """在调用 Agent 前拦截数据库、正式产物和无授权路径任务。"""

    owner = str(task.get("owner") or "").strip().lower()
    paths = _task_paths(task)
    normalized_paths = [path.casefold() for path in paths]
    if owner == "database" or any(
        any(marker in path for marker in _DATABASE_PATH_MARKERS)
        for path in normalized_paths
    ):
        return {
            "reasonCode": "database_change",
            "reason": "数据库结构、迁移或 DDL 变更必须由实体设计确认或专门数据库流程处理。",
            "workflowIntent": "detail_confirmation",
        }
    if not paths or any(_is_placeholder_path(path) for path in paths) or any(
        any(marker in path for marker in _FORMAL_PATH_MARKERS)
        for path in normalized_paths
    ):
        return {
            "reasonCode": "formal_artifact_or_missing_scope",
            "reason": "任务需要正式工件或没有可验证的代码文件范围。",
            "workflowIntent": "prepare_build_tasks",
        }
    return {}


def workflow_target_for_small_task(escalation: dict[str, Any]) -> str:
    """把 SmallTask Agent 的升级原因映射为确定性的主工作流节点。"""

    requested = str(
        escalation.get("workflowIntent")
        or escalation.get("workflow_intent")
        or ""
    ).strip()
    if requested in {
        "detail_confirmation",
        "project_planning",
        "inspect_workspace",
        "prepare_build_tasks",
        "build",
    }:
        return requested
    reason_code = str(
        escalation.get("reasonCode") or escalation.get("reason_code") or ""
    ).strip()
    if reason_code in {"database_change", "migration", "ddl_change"}:
        return "detail_confirmation"
    if reason_code in {
        "formal_artifact_change",
        "new_page",
        "new_api",
        "api_contract_change",
        "product_decision",
        "confirmed_requirement_change",
    }:
        return "detail_confirmation"
    return "prepare_build_tasks"


def apply_confirmed_scope(
    tasks: list[dict[str, Any]],
    *,
    task_ids: list[str],
    requested_paths: list[str],
) -> list[dict[str, Any]]:
    """把用户批准的具体路径追加到指定任务，并保持原任务其他约束不变。"""

    normalized_ids = set(task_ids)
    safe_paths = [path for path in requested_paths if _safe_relative_path(path)]
    return [
        (
            {
                **task,
                "allowed_paths": list(dict.fromkeys([*_task_paths(task), *safe_paths])),
                "target_files": list(
                    dict.fromkeys(
                        [*_string_list(task.get("target_files"), limit=100), *safe_paths]
                    )
                ),
                "status": "pending",
            }
            if str(task.get("id") or "") in normalized_ids
            else task
        )
        for task in tasks
    ]


def _tasks_conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """判断两个任务是否共享路径、资源或高风险工程文件。"""

    left_paths = _task_paths(left)
    right_paths = _task_paths(right)
    if any(_paths_overlap(a, b) for a in left_paths for b in right_paths):
        return True
    left_resources = _resource_keys(left)
    right_resources = _resource_keys(right)
    if left_resources.intersection(right_resources):
        return True
    return any(_shared_file_marker(path) for path in [*left_paths, *right_paths])


def _task_paths(task: dict[str, Any]) -> list[str]:
    """汇总任务的目标、授权和 change scope 路径。"""

    values: list[str] = []
    for key in ("allowed_paths", "allowedPaths", "target_files", "targetFiles"):
        values.extend(_string_list(task.get(key), limit=100))
    change_scope = task.get("change_scope") or task.get("changeScope")
    if isinstance(change_scope, list):
        values.extend(
            str(item.get("path") or "").strip()
            for item in change_scope
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        )
    return list(dict.fromkeys(path.lstrip("./") for path in values if path))


def _resource_keys(task: dict[str, Any]) -> set[str]:
    """读取任务声明的业务资源锁，阻止跨资源并行副作用。"""

    values = task.get("resource_locks") or task.get("resourceLocks") or []
    if isinstance(values, dict):
        values = list(values.values())
    return {
        str(item.get("key") or item.get("id") or item).strip()
        for item in values
        if str(item.get("key") or item.get("id") or item).strip()
    } if isinstance(values, list) else set()


def _path_matches_task(path: str, task: dict[str, Any]) -> bool:
    """使用大小写不敏感的精确、目录和 glob 规则匹配任务范围。"""

    candidate = _normalize_path(path).casefold()
    if not candidate:
        return False
    return any(_paths_overlap(candidate, scope.casefold()) for scope in _task_paths(task))


def _paths_overlap(left: str, right: str) -> bool:
    """判断两个路径或通配范围是否可能写入同一文件。"""

    left = _normalize_path(left).casefold()
    right = _normalize_path(right).casefold()
    if not left or not right:
        return False
    if fnmatch.fnmatchcase(left, right) or fnmatch.fnmatchcase(right, left):
        return True
    left_prefix = _static_prefix(left)
    right_prefix = _static_prefix(right)
    return bool(
        left_prefix
        and right_prefix
        and (
            left_prefix == right_prefix
            or left_prefix.startswith(f"{right_prefix}/")
            or right_prefix.startswith(f"{left_prefix}/")
        )
    )


def _normalize_path(value: Any) -> str:
    """统一路径分隔符和工作区根前缀。"""

    return str(value or "").strip().replace("\\", "/").lstrip("/").rstrip("/")


def _static_prefix(value: str) -> str:
    """截取 glob 前的路径前缀，支持目录范围冲突判断。"""

    parts: list[str] = []
    for part in value.split("/"):
        if any(token in part for token in ("*", "?", "[")):
            break
        if part:
            parts.append(part)
    return "/".join(parts)


def _shared_file_marker(path: str) -> bool:
    """识别依赖锁、配置和全局注册文件等不适合并行写入的文件。"""

    normalized = path.casefold()
    return any(
        marker in normalized
        for marker in (
            "package.json",
            "pnpm-lock",
            "package-lock",
            "yarn.lock",
            "pyproject.toml",
            "requirements.txt",
            "vite.config",
            "tsconfig",
            "router",
            "routes",
            "menu",
        )
    )


def _safe_relative_path(path: str) -> bool:
    """校验用户批准的新路径仍是工作区内的非敏感相对路径。"""

    normalized = _normalize_path(path)
    if not normalized or ".." in normalized.split("/"):
        return False
    lower = normalized.casefold()
    return not any(marker in lower for marker in _FORMAL_PATH_MARKERS + (".env",))


def _is_placeholder_path(path: str) -> bool:
    """识别只代表命令级修复、不能作为代码写入授权的哨兵路径。"""

    return path.casefold().startswith("<no file paths")


def _string_list(value: Any, *, limit: int) -> list[str]:
    """把任意列表值裁剪为稳定字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item).strip()[:1_000] for item in value if str(item).strip()][:limit]


def _bounded_items(value: Any, *, limit: int) -> list[dict[str, Any]]:
    """裁剪任务对象列表，保留小任务上下文的结构信息。"""

    if not isinstance(value, list):
        return []
    return [
        {str(key)[:100]: str(item)[:1_000] for key, item in value_item.items()}
        for value_item in value[:limit]
        if isinstance(value_item, dict)
    ]
