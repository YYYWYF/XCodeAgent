from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from pathlib import Path
from typing import Any

from app.services.build_task_menu import (
    reconcile_live_page_paths,
)
from app.services.business_acceptance import (
    DELIVERABLE_KINDS,
    business_acceptance_contract_errors,
    compile_business_acceptance,
    normalize_deliverables,
    normalize_repo_path,
)
from app.services.engineering_acceptance import (
    compile_engineering_acceptance,
    engineering_acceptance_contract_errors,
    ensure_engineering_acceptance,
)
from app.services.build_unit_compiler import (
    annotate_unit_inputs,
    apply_unit_compilation,
)
from app.services.task_scheduler import annotate_task_execution, build_execution_batches


logger = logging.getLogger(__name__)


TASK_STATUSES = ("pending", "running", "completed", "failed", "already_satisfied")
_HIGH_RISK_DATABASE_OPERATIONS = {
    "drop_table",
    "drop_column",
    "delete_data",
    "truncate",
    "drop",
    "delete",
}
_CODE_PATH_SUFFIXES = (
    ".java",
    ".kt",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".vue",
    ".less",
    ".css",
)
_TEMPLATE_BOUNDARY_PATHS = {
    "frontend/src/constants/menus.ts",
    "src/constants/menus.ts",
    "frontend/src/constants/routes.ts",
    "src/constants/routes.ts",
    "frontend/src/utils/route.tsx",
    "src/utils/route.tsx",
    "frontend/src/routes/index.tsx",
    "src/routes/index.tsx",
}
_TEMPLATE_PAGE_ENTRY_PREFIXES = ("frontend/src/pages/", "src/pages/")
_FRONTEND_ENDPOINT_IMPLEMENTATION_CHECK_KINDS = {
    "frontend.api_contract",
    "frontend.static_data_contract",
}


def tasks_from_build_task_plan(build_task_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """按任务图读取完整任务注册表，无效图不得通过部分拓扑序静默丢任务。"""

    registry = build_task_plan.get("task_registry")
    task_graph = build_task_plan.get("task_graph")
    if not isinstance(registry, dict) or not isinstance(task_graph, dict):
        return []
    nodes = _string_list(task_graph.get("nodes"))
    topological_order = _string_list(task_graph.get("topological_order"))
    registry_ids = [str(task_id) for task_id in registry]
    complete_ids = _dedupe_strings([*nodes, *registry_ids])
    validation = task_graph.get("validation")
    is_valid = isinstance(validation, dict) and validation.get("is_valid") is True
    task_ids = (
        topological_order
        if is_valid and set(topological_order) == set(complete_ids)
        else complete_ids
    )
    return [
        dict(registry[task_id])
        for task_id in task_ids
        if isinstance(registry.get(task_id), dict)
    ]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """把不可信列表收敛为字典列表。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dedupe_normalized_strings(values: list[str]) -> list[str]:
    """按规范化文本去重模型输出列表，避免同一句验收点或路径重复落库。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalized_text_key(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _task_target_files(task: dict[str, Any]) -> list[str]:
    """读取当前 DAG v3 任务的目标文件。"""

    return _string_list(task.get("target_files"))


def _task_dependencies(task: dict[str, Any]) -> list[str]:
    """读取当前 DAG v3 任务的依赖列表。"""

    return _string_list(task.get("dependencies"))


def _task_can_run_in_parallel(task: dict[str, Any]) -> bool:
    """读取当前 DAG v3 任务的并行标记。"""

    return bool(task.get("can_run_in_parallel", True))


def _normalized_text_key(value: str) -> str:
    """生成文本去重键，忽略大小写、空白和常见中英文标点差异。"""

    text = str(value or "").strip().lower()
    if not text:
        return ""
    punctuation = " \t\r\n。．.，,；;：:、!！?？（）()[]【】{}<>《》\"'`"
    return "".join(char for char in text if char not in punctuation)


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _default_operation_for_path(path: str, workspace_root: str | Path | None) -> str:
    """按磁盘是否已存在该文件决定 add/modify。

    build-task-plan 的 change_scope 默认一律标 modify，但模板工程拉取后业务 API、
    新增页面等文件并不存在；build 阶段实际产生 added 差异，与 modify 期望不符，
    导致工程验收报"预期 modified 实际 added"。这里以磁盘存在性作为确定性事实：
    文件已存在 → modify，不存在 → add，使期望与实际差异类型对齐。
    模型显式填写的 add 或 modify 同样必须服从该事实；delete 保留其独立的
    删除语义，不参与本规则归一化。
    与当前工作区差异校验的 `modify if entry_exists else add` 约定保持一致。
    """

    if not workspace_root:
        return "modify"
    # 去掉前导 ./ 和虚拟绝对前缀 /，使路径相对工作区解析；与验收器
    # _resolved_path_error 的 (root / path).resolve() + lstrip("./") 约定一致。
    cleaned = normalize_repo_path(path)
    if not cleaned:
        return "modify"
    try:
        target = (Path(workspace_root).expanduser() / cleaned).resolve()
    except (OSError, ValueError):
        return "modify"
    return "modify" if target.is_file() else "add"


def _change_scope(
    value: Any,
    target_files: list[str],
    *,
    workspace_root: str | Path | None = None,
) -> list[dict[str, str]]:
    operations = {"add", "modify", "delete"}
    result: list[dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                path = normalize_repo_path(item)
                if path:
                    result.append(
                        {
                            "operation": _default_operation_for_path(
                                path, workspace_root
                            ),
                            "path": path,
                            "description": "按任务要求调整该文件。",
                        }
                    )
                continue
            if not isinstance(item, dict):
                continue
            path = normalize_repo_path(item.get("path") or item.get("file"))
            if not path:
                continue
            # 文件是否存在是工作区的确定性事实。无论模型是否显式填写 add/modify，
            # 都统一由磁盘状态归一化；仅 delete 保留模型的删除语义。
            raw_operation = item.get("operation")
            if raw_operation is None or str(raw_operation).strip() == "":
                operation = _default_operation_for_path(path, workspace_root)
            else:
                operation = str(raw_operation).strip().lower()
                if operation not in operations:
                    operation = _default_operation_for_path(path, workspace_root)
                # 未提供工作区根目录时没有可验证的事实源，保留模型显式意图，避免
                # 把无法判定的新增文件错误改写为 modify。
                elif operation != "delete" and workspace_root:
                    operation = _default_operation_for_path(path, workspace_root)
            result.append(
                {
                    "operation": operation,
                    "path": path,
                    "description": _text(item.get("description"), "按任务要求调整该文件。"),
                }
            )
    if result:
        return result
    return [
        {
            "operation": _default_operation_for_path(path, workspace_root),
            "path": path,
            "description": "按任务要求调整该文件。",
        }
        for path in (normalize_repo_path(item) for item in target_files)
    ]


def _impact_scope(value: Any, description: str) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "summary": _text(source.get("summary"), description),
        "affected_modules": _string_list(
            source.get("affected_modules") or source.get("affectedModules")
        ),
        "public_contracts": _string_list(
            source.get("public_contracts") or source.get("publicContracts")
        ),
        "risks": _string_list(source.get("risks")),
    }


def _workspace_analysis(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "inspection_status": "completed" if source else "incomplete",
        "stack": _string_list(source.get("stack")),
        "inspected_directories": _string_list(
            source.get("inspected_directories") or source.get("inspectedDirectories")
        ),
        "entry_files": _string_list(
            source.get("entry_files") or source.get("entryFiles")
        ),
        "conventions": _string_list(source.get("conventions")),
        "summary": _text(
            source.get("summary"),
            "主 Agent 返回中缺少可解析的工作目录检查摘要，已使用项目计划兜底拆分任务。",
        ),
    }


def _workspace_analysis_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or not snapshot:
        return _workspace_analysis({})

    entry_files = [
        str(entry.get("path"))
        for entry in snapshot.get("entrypoints", [])
        if isinstance(entry, dict) and entry.get("path")
    ]
    inspected_directories = [
        str(root.get("path"))
        for root in snapshot.get("project_roots", [])
        if isinstance(root, dict) and root.get("path")
    ]
    conventions = [
        f"{command.get('kind')}: {command.get('command')}"
        for command in snapshot.get("build_commands", [])
        if isinstance(command, dict) and command.get("command")
    ]
    return {
        "inspection_status": "completed",
        "workspace_revision": snapshot.get("workspace_revision"),
        "snapshot_schema_version": snapshot.get("schema_version"),
        "stack": _string_list(snapshot.get("tech_stack")),
        "inspected_directories": inspected_directories,
        "entry_files": entry_files,
        "conventions": conventions,
        "summary": "WorkspaceSnapshot provided deterministic project roots, stack, entrypoints, commands, and contract hints before task planning.",
    }


def _normalize_agent_tasks(
    raw_tasks: Any,
    *,
    workspace_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """把模型返回的候选任务规整为 v3 叶子任务。"""

    if not isinstance(raw_tasks, list):
        return []
    tasks: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw_tasks, start=1):
        if not isinstance(item, dict):
            continue
        base_id = _text(item.get("id"), f"task-{index:03d}")
        task_id = base_id
        suffix = 2
        while task_id in used_ids:
            task_id = f"{base_id}-{suffix}"
            suffix += 1
        used_ids.add(task_id)

        owner = _text(item.get("owner"), "frontend")
        if owner not in {"frontend", "backend", "database"}:
            owner = (
                "database"
                if owner in {"data_source", "data-source", "data", "db"}
                else "backend"
                if owner in {"api", "server"}
                else "frontend"
            )
        default_task_type = (
            "database.change"
            if owner == "database"
            else "backend.code"
            if owner == "backend"
            else "frontend.code"
        )
        description = _text(item.get("description"), _text(item.get("title"), task_id))
        target_files = [
            normalize_repo_path(path)
            for path in _string_list(item.get("target_files"))
            if normalize_repo_path(path)
        ]
        change_scope = _change_scope(
            item.get("change_scope"),
            target_files,
            workspace_root=workspace_root,
        )
        # target_files 必须覆盖 change_scope 里的全部路径：模型常把页面入口
        # （frontend/src/pages/<Key>/index.tsx）只放在 change_scope 而漏进
        # target_files。这里始终合并（去重），供只读页面路径校对和工程验收使用。
        target_files = _dedupe_normalized_strings(
            [*target_files, *[change["path"] for change in change_scope]]
        )
        dependencies = _dedupe_normalized_strings(
            _string_list(item.get("dependencies"))
        )
        can_parallel = bool(item.get("can_run_in_parallel", True))
        database_scope = _dict_value(item.get("database_scope"))
        allowed_paths = (
            _dedupe_normalized_strings(
                [
                    normalize_repo_path(path)
                    for path in _string_list(item.get("allowed_paths"))
                    if normalize_repo_path(path)
                ]
            )
            or target_files
        )
        if (
            owner != "database"
            and not change_scope
            and not target_files
            and not allowed_paths
            and not database_scope
        ):
            logger.info(
                "build_task_plan_excluded_verification_task task_id=%s title=%s",
                task_id,
                _text(item.get("title"), description),
            )
            continue
        tasks.append(
            {
                "id": task_id,
                "owner": owner,
                "task_type": _text(
                    item.get("task_type"),
                    default_task_type,
                ),
                "title": _text(item.get("title"), description),
                "description": description,
                "dependencies": dependencies,
                "status": "pending",
                "unit_id": _text(item.get("unit_id"), "application:root"),
                "source_refs": _dict_value(item.get("source_refs")),
                "deliverables": normalize_deliverables(item.get("deliverables")),
                "requires_capabilities": _string_list(
                    item.get("requires_capabilities")
                ),
                "provides_capabilities": _string_list(
                    item.get("provides_capabilities")
                ),
                "database_scope": database_scope,
                "risk": _text(item.get("risk"), "low"),
                "approval": _dict_value(item.get("approval")),
                "allowed_paths": allowed_paths,
                "target_files": _dedupe_normalized_strings(target_files),
                "change_scope": change_scope,
                "impact_scope": _impact_scope(
                    item.get("impact_scope"), description
                ),
                "can_run_in_parallel": can_parallel,
                "parallel_reason": _text(
                    item.get("parallel_reason"),
                    "依赖满足且目标文件不冲突时可并行。",
                ),
                "engineering_context": _dict_value(item.get("engineering_context")),
            }
        )
    return tasks


def _is_template_boundary_path(value: Any) -> bool:
    """判断路径是否属于模板初始化负责的共享菜单或路由注册文件。"""

    normalized = str(value or "").strip().replace("\\", "/").lstrip("./")
    return normalized in _TEMPLATE_BOUNDARY_PATHS


def _is_auth_constants_path(value: Any) -> bool:
    """识别模板拥有的 AuthConstants 文件，禁止模型任务将其纳入写入范围。"""

    normalized = str(value or "").strip().replace("\\", "/").lower()
    return normalized.endswith("/authconstants.java") or normalized == "authconstants.java"


def _is_template_page_entry_path(value: Any) -> bool:
    """判断路径是否是模板初始化负责创建的页面入口文件。"""

    normalized = str(value or "").strip().replace("\\", "/").lstrip("./")
    return (
        any(normalized.startswith(prefix) for prefix in _TEMPLATE_PAGE_ENTRY_PREFIXES)
        and normalized.endswith("/index.tsx")
        and normalized.count("/") >= 4
    )


def merge_exact_duplicate_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在工程验收和任务图编译前合并可确定完全相同的任务。"""

    merged: list[dict[str, Any]] = []
    key_to_index: dict[str, int] = {}
    replacement: dict[str, str] = {}
    for task in tasks:
        duplicate_key = _exact_duplicate_key(task)
        existing_index = key_to_index.get(duplicate_key) if duplicate_key else None
        if existing_index is None:
            merged.append(deepcopy(task))
            if duplicate_key:
                key_to_index[duplicate_key] = len(merged) - 1
            continue
        existing = merged[existing_index]
        existing_id = str(existing.get("id") or "")
        duplicate_id = str(task.get("id") or "")
        if duplicate_id and existing_id and duplicate_id != existing_id:
            replacement[duplicate_id] = existing_id
        existing["dependencies"] = _dedupe_strings(
            [
                dependency
                for dependency in [
                    *_task_dependencies(existing),
                    *_task_dependencies(task),
                ]
                if dependency not in {existing_id, duplicate_id}
            ]
        )
        existing["source_refs"] = _merge_source_refs(
            existing.get("source_refs"), task.get("source_refs")
        )
        existing["requires_capabilities"] = _dedupe_strings(
            [
                *_string_list(existing.get("requires_capabilities")),
                *_string_list(task.get("requires_capabilities")),
            ]
        )
        existing["provides_capabilities"] = _dedupe_strings(
            [
                *_string_list(existing.get("provides_capabilities")),
                *_string_list(task.get("provides_capabilities")),
            ]
        )
    if not replacement:
        return merged
    for task in merged:
        task["dependencies"] = _dedupe_strings(
            [replacement.get(dependency, dependency) for dependency in _task_dependencies(task)]
        )
    return merged


def frontend_endpoint_ownership_errors(tasks: list[dict[str, Any]]) -> list[str]:
    """拒绝多个普通前端任务重复实现同一个正式 Endpoint。"""

    owners_by_endpoint: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for task in tasks:
        if str(task.get("owner") or "") != "frontend" or task.get("kind") == "repair":
            continue
        task_id = str(task.get("id") or "<unknown>")
        seen_task_endpoints: set[tuple[str, str]] = set()
        for check in _dict_items(task.get("business_acceptance_checks")):
            if str(check.get("kind") or "") not in _FRONTEND_ENDPOINT_IMPLEMENTATION_CHECK_KINDS:
                continue
            expected = check.get("expected") if isinstance(check.get("expected"), dict) else {}
            paths = _dedupe_normalized_strings(
                [
                    normalize_repo_path(path)
                    for path in [
                        *_string_list(check.get("target_paths")),
                        *_task_target_files(task),
                    ]
                    if normalize_repo_path(path)
                ]
            )
            for endpoint in _dict_items(expected.get("endpoints")):
                endpoint_id = str(endpoint.get("endpoint_id") or "").strip()
                if not endpoint_id:
                    continue
                contract_id = str(endpoint.get("api_contract_id") or "").strip()
                endpoint_key = (contract_id.casefold(), endpoint_id.casefold())
                if endpoint_key in seen_task_endpoints:
                    continue
                seen_task_endpoints.add(endpoint_key)
                owners_by_endpoint.setdefault(endpoint_key, []).append(
                    {
                        "task_id": task_id,
                        "contract_id": contract_id,
                        "endpoint_id": endpoint_id,
                        "paths": paths,
                    }
                )

    errors: list[str] = []
    for owners in owners_by_endpoint.values():
        if len(owners) < 2:
            continue
        first = owners[0]
        endpoint_label = (
            f"{first['contract_id']} + {first['endpoint_id']}"
            if first["contract_id"]
            else first["endpoint_id"]
        )
        owner_labels = [
            f"{owner['task_id']} ({', '.join(owner['paths']) or 'no target path'})"
            for owner in owners
        ]
        errors.append(
            f"Frontend endpoint {endpoint_label} has multiple implementation owners: "
            f"{'; '.join(owner_labels)}. Keep one API module owner and make other tasks reuse it."
        )
    return errors


def _exact_duplicate_key(task: dict[str, Any]) -> str:
    """生成只包含确定性结构的完全重复任务键，不做语义相似度推断。"""

    owner = _normalized_text_key(_text(task.get("owner")))
    unit_id = _normalized_text_key(_text(task.get("unit_id")))
    task_type = _normalized_text_key(_text(task.get("task_type")))
    target_files = sorted(
        _normalized_text_key(path) for path in _task_target_files(task) if path
    )
    change_scope = sorted(
        (
            _normalized_text_key(str(change.get("operation") or "modify")),
            _normalized_text_key(str(change.get("path") or change.get("file") or "")),
        )
        for change in task.get("change_scope") or []
        if isinstance(change, dict) and (change.get("path") or change.get("file"))
    )
    raw_database_scope = task.get("database_scope")
    database_scope = (
        _stable_json_key(raw_database_scope)
        if isinstance(raw_database_scope, dict) and raw_database_scope
        else ""
    )
    target_key = _stable_json_key({"target_files": target_files, "change_scope": change_scope})
    if not target_files and not change_scope and not database_scope:
        return ""
    return "|".join((owner, unit_id, task_type, target_key, database_scope))


def _stable_json_key(value: Any) -> str:
    """把范围对象编码为稳定字符串，避免字典键顺序影响重复判断。"""

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return ""


def _merge_source_refs(left: Any, right: Any) -> dict[str, Any]:
    """合并重复任务来源引用，列表按稳定文本去重并保留首次顺序。"""

    left_value = left if isinstance(left, dict) else {}
    right_value = right if isinstance(right, dict) else {}
    result: dict[str, Any] = dict(left_value)
    for key, value in right_value.items():
        if key not in result:
            result[key] = deepcopy(value)
            continue
        existing = result[key]
        if isinstance(existing, list) and isinstance(value, list):
            result[key] = _merge_source_ref_list(existing, value)
        elif isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _merge_source_refs(existing, value)
    return result


def _merge_source_ref_list(left: list[Any], right: list[Any]) -> list[Any]:
    """合并来源引用列表并用稳定 JSON 键去重。"""

    result: list[Any] = []
    seen: set[str] = set()
    for item in [*left, *right]:
        key = _stable_json_key(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(deepcopy(item))
    return result


def _raw_agent_tasks(agent_plan: dict[str, Any] | None) -> Any:
    """兼容读取模型输出中的 tasks 或 dag.tasks 候选列表。"""

    if not isinstance(agent_plan, dict):
        return None
    if isinstance(agent_plan.get("tasks"), list):
        return agent_plan["tasks"]
    dag = agent_plan.get("dag")
    if isinstance(dag, dict):
        for key in ("tasks", "nodes"):
            if isinstance(dag.get(key), list):
                return dag[key]
    return None


def build_task_candidate_contract_errors(
    agent_plan: dict[str, Any] | None,
) -> list[str]:
    """在归一化前校验模型任务的交付物结构，避免非法字段被静默丢弃。"""

    raw_tasks = _raw_agent_tasks(agent_plan)
    if not isinstance(raw_tasks, list):
        return []
    errors: list[str] = []
    for task_index, task in enumerate(raw_tasks):
        if not isinstance(task, dict):
            continue
        task_id = _text(task.get("id"), f"tasks[{task_index}]")
        source_refs = task.get("source_refs")
        if isinstance(source_refs, dict) and "authorization" in source_refs:
            errors.append(
                f"Task {task_id} must not output platform-owned source_refs.authorization."
            )
        if "authorization_constraints" in task or "authorization" in task:
            errors.append(
                f"Task {task_id} must not output platform-owned authorization fields."
            )
        candidate_paths = [
            str(change.get("path") or "")
            for change in task.get("change_scope") or []
            if isinstance(change, dict)
        ] + _string_list(task.get("allowed_paths"))
        if any(_is_auth_constants_path(path) for path in candidate_paths):
            errors.append(
                f"Task {task_id} must not modify platform-owned AuthConstants."
            )
        owner = _text(task.get("owner"))
        unit_id = _text(task.get("unit_id"))
        task_kind = _text(task.get("kind"))
        requires_deliverable = (
            task_kind != "repair"
            and owner in {"frontend", "backend"}
            and unit_id
            not in {
                "frontend:shell",
                "frontend:api-client",
                "frontend:auth-guard",
                "backend:bootstrap",
            }
        )
        deliverables = task.get("deliverables")
        if not isinstance(deliverables, list) or not deliverables:
            if requires_deliverable:
                errors.append(
                    f"Task {task_id} deliverables must be a non-empty array."
                )
            continue
        for deliverable_index, deliverable in enumerate(deliverables):
            field_path = f"Task {task_id} deliverables[{deliverable_index}]"
            if not isinstance(deliverable, dict):
                errors.append(f"{field_path} must be an object.")
                continue
            if not _text(deliverable.get("id")):
                errors.append(f"{field_path}.id is required.")
            kind = _text(deliverable.get("kind"))
            if not kind:
                errors.append(f"{field_path}.kind is required.")
            elif kind not in DELIVERABLE_KINDS:
                errors.append(f"{field_path}.kind {kind} is unsupported.")
            if not _text(deliverable.get("target_id")):
                errors.append(f"{field_path}.target_id is required.")
            paths = deliverable.get("paths")
            if not _string_list(paths):
                suffix = (
                    '; singular field "path" is not supported.'
                    if _text(deliverable.get("path"))
                    else "."
                )
                errors.append(
                    f"{field_path}.paths must be a non-empty string array{suffix}"
                )
            if not _string_list(deliverable.get("provides")):
                errors.append(
                    f"{field_path}.provides must be a non-empty string array."
                )
    return errors


def _annotate_parallelism(
    tasks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """为任务补齐并行元信息和执行批次。"""

    requested_parallel = {
        task["id"]: _task_can_run_in_parallel(task) for task in tasks
    }
    annotated = annotate_task_execution(tasks)
    batches = build_execution_batches(annotated)
    parallel_by_task: dict[str, list[str]] = {}
    for batch in batches:
        task_ids = _string_list(batch.get("tasks"))
        if batch.get("mode") != "parallel":
            continue
        for task_id in task_ids:
            parallel_by_task[task_id] = [candidate for candidate in task_ids if candidate != task_id]
    for task in annotated:
        task["can_run_in_parallel"] = _task_can_run_in_parallel(task)
        task["parallel_with"] = parallel_by_task.get(task["id"], [])
        if not task["can_run_in_parallel"] and requested_parallel.get(task["id"]):
            task["parallel_reason"] = str(
                task.get("directWriteReason") or "调度器检测到文件或契约冲突，必须串行。"
            )
    return annotated, batches


def _topological_order(tasks: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """对任务依赖执行拓扑排序，并返回缺失依赖或环路错误。"""

    by_id = {task["id"]: task for task in tasks}
    incoming = {
        task_id: set(_task_dependencies(task))
        for task_id, task in by_id.items()
    }
    errors = [
        f"Task {task_id} depends on missing task {dependency}."
        for task_id, dependencies in incoming.items()
        for dependency in sorted(dependencies)
        if dependency not in by_id
    ]
    for dependencies in incoming.values():
        dependencies.intersection_update(by_id)

    ready = sorted(task_id for task_id, dependencies in incoming.items() if not dependencies)
    order: list[str] = []
    while ready:
        task_id = ready.pop(0)
        order.append(task_id)
        for candidate_id, dependencies in incoming.items():
            if task_id not in dependencies:
                continue
            dependencies.remove(task_id)
            if not dependencies and candidate_id not in order and candidate_id not in ready:
                ready.append(candidate_id)
        ready.sort()

    if len(order) != len(tasks):
        blocked = sorted(set(by_id) - set(order))
        errors.append(f"Task dependency graph contains a cycle involving: {', '.join(blocked)}.")
    return order, errors


def _build_task_graph(
    tasks: list[dict[str, Any]],
    execution_batches: list[dict[str, Any]],
    build_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据叶子任务构造可校验的任务 DAG。"""

    task_ids = [task["id"] for task in tasks]
    edges = [
        {"from": dependency, "to": task["id"], "type": "depends_on"}
        for task in tasks
        for dependency in _task_dependencies(task)
    ]
    incoming = {task_id: 0 for task_id in task_ids}
    outgoing = {task_id: 0 for task_id in task_ids}
    for edge in edges:
        if edge["to"] in incoming:
            incoming[edge["to"]] += 1
        if edge["from"] in outgoing:
            outgoing[edge["from"]] += 1

    topological_order, validation_errors = _topological_order(tasks)
    missing_dependency_errors = [
        f"Task {edge['to']} depends on missing task {edge['from']}."
        for edge in edges
        if edge["from"] not in incoming
    ]
    semantic_errors = _task_semantic_errors(tasks, build_context or {})
    all_errors = _dedupe_strings(
        [*missing_dependency_errors, *validation_errors, *semantic_errors]
    )
    return {
        "schema_version": "build-task-graph.v3",
        "nodes": task_ids,
        "edges": edges,
        "roots": [task_id for task_id in task_ids if incoming[task_id] == 0],
        "leaves": [task_id for task_id in task_ids if outgoing[task_id] == 0],
        "topological_order": topological_order,
        "execution_layers": execution_batches,
        "validation": {
            "is_valid": not all_errors,
            "errors": all_errors,
        },
    }


def _task_semantic_errors(
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any],
) -> list[str]:
    """校验 DAG 拓扑之外的任务边界、owner、Unit、数据库职责和审批语义。"""

    errors: list[str] = []
    errors.extend(_authorization_coverage_errors(tasks, build_context))
    required_unit_ids = _string_list(build_context.get("required_unit_ids"))
    validate_task_scope = build_context.get("_validate_task_scope", True) is not False
    allow_missing_deliverable_task_ids = set(
        _string_list(build_context.get("_allow_missing_business_deliverable_task_ids"))
    )
    errors.extend(_required_bootstrap_task_errors(tasks, build_context))
    errors.extend(frontend_endpoint_ownership_errors(tasks))
    for task in tasks:
        task_id = str(task.get("id") or "")
        owner = str(task.get("owner") or "")
        unit_id = str(task.get("unit_id") or "")
        task_type = str(task.get("task_type") or "")
        paths = _task_declared_paths(task)
        errors.extend(_template_boundary_errors(task, paths=paths))
        if validate_task_scope and required_unit_ids and unit_id not in required_unit_ids:
            errors.append(
                f"Task {task_id} is outside the current Build scope: Unit {unit_id}."
            )
        if unit_id.startswith("database:") and owner != "database":
            errors.append(f"Task {task_id} is in database Unit {unit_id} but owner is {owner}.")
        if unit_id.startswith("backend:") and owner != "backend":
            errors.append(f"Task {task_id} is in backend Unit {unit_id} but owner is {owner}.")
        if unit_id.startswith(("page:", "frontend:")) and owner != "frontend":
            errors.append(f"Task {task_id} is in frontend/page Unit {unit_id} but owner is {owner}.")
        if owner == "database":
            if required_unit_ids and not any(
                candidate.startswith("database:") for candidate in required_unit_ids
            ):
                errors.append(
                    f"Database task {task_id} is not allowed in normal Build scope; "
                    "database changes are completed during entity confirmation."
                )
            errors.extend(
                _database_task_semantic_errors(
                    task,
                    paths=paths,
                )
            )
        elif task.get("database_scope"):
            errors.append(f"Task {task_id} is {owner} owner but declares database_scope.")
        if owner == "backend" and task_type.startswith("database."):
            errors.append(f"Task {task_id} is backend owner but declares database task_type {task_type}.")
        errors.extend(
            business_acceptance_contract_errors(
                task,
                # 只对未被本轮替换的历史基线任务放宽缺失交付物，当前模型新任务仍必须完整声明。
                allow_missing_deliverable=task_id in allow_missing_deliverable_task_ids,
            )
        )
        errors.extend(engineering_acceptance_contract_errors(task))
    return errors


def _authorization_coverage_errors(
    tasks: list[dict[str, Any]], build_context: dict[str, Any]) -> list[str]:
    """校验当前范围内页面和后端 Endpoint 的权限实现覆盖。"""

    constraints = build_context.get("authorization_constraints")
    if not isinstance(constraints, dict):
        return []
    errors: list[str] = []
    task_paths = [
        path
        for task in tasks
        for path in _task_scope_paths(task)
    ]
    for page in _dict_items(constraints.get("pages")):
        page_id = _text(page.get("pageId"))
        if page_id and f"page:{page_id}" not in units:
            errors.append(f"Authorization page {page_id} is missing its page Unit task.")
    # 权限不是独立 Build Unit。仅当当前范围本来就要求实现某个后端 endpoint
    # Unit 时，才要求该 Unit 中存在 Controller 交付物；纯静态及范围外接口不应
    # 因携带 operationResourceKeys 被误判为缺少后端 Controller。
    required_unit_ids = set(_string_list(build_context.get("required_unit_ids")))
    for endpoint in _dict_items(constraints.get("endpoints")):
        keys = _string_list(endpoint.get("operationResourceKeys"))
        contract_id = _text(endpoint.get("apiContractId"))
        endpoint_id = _text(endpoint.get("endpointId"))
        unit_id = f"backend:endpoint:{contract_id}:{endpoint_id}"
        if not keys or unit_id not in required_unit_ids:
            continue
        has_controller_task = any(
            str(task.get("unit_id") or "") == unit_id
            and str(task.get("owner") or "") == "backend"
            and any(
                str(deliverable.get("kind") or "") == "backend.endpoint_controller"
                for deliverable in _dict_items(task.get("deliverables"))
            )
            for task in tasks
        )
        if not has_controller_task:
            errors.append(
                f"Authorization endpoint {contract_id}:{endpoint_id} is missing its "
                "Controller implementation task."
            )
    if any(_is_template_boundary_path(path) for path in task_paths):
        errors.append("Build tasks must not modify platform-owned shared route or menu files.")
    if any(_is_auth_constants_path(path) for path in task_paths):
        errors.append("Build tasks must not modify platform-owned AuthConstants.")
    return errors


def _task_scope_paths(task: dict[str, Any]) -> list[str]:
    """提取任务声明的变更与允许路径，供平台边界校验复用。"""

    return [
        str(item.get("path") or "")
        for item in task.get("change_scope") or []
        if isinstance(item, dict) and str(item.get("path") or "")
    ] + _string_list(task.get("allowed_paths"))


def _required_bootstrap_task_errors(
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any],
) -> list[str]:
    """数据库规划范围要求 bootstrap 时，拒绝模型遗漏该可执行任务。"""

    planning_unit_ids = set(_string_list(build_context.get("planning_unit_ids")))
    if "backend:bootstrap" not in planning_unit_ids:
        return []
    if any(str(task.get("unit_id") or "") == "backend:bootstrap" for task in tasks):
        return []
    return [
        "Database Build scope requires a backend:bootstrap task to validate Maven "
        "dependencies and datasource/MyBatis configuration."
    ]


def _template_boundary_errors(
    task: dict[str, Any],
    *,
    paths: list[str],
) -> list[str]:
    """显式报告模板职责越界，不修改候选任务以掩盖规划边界错误。"""

    task_id = str(task.get("id") or "")
    errors: list[str] = []
    boundary_paths = sorted(
        {
            path
            for path in paths
            if _is_template_boundary_path(path)
        }
    )
    if boundary_paths:
        errors.append(
            f"Task {task_id} crosses the template initialization boundary and must not "
            "modify shared menu or route files: "
            f"{', '.join(boundary_paths)}."
        )
    for change in _dict_items(task.get("change_scope")):
        path = str(change.get("path") or change.get("file") or "").strip()
        operation = str(change.get("operation") or "modify").strip().lower()
        if _is_template_page_entry_path(path) and operation == "add":
            errors.append(
                f"Task {task_id} must not add template page entry {path}; "
                "template initialization must create it before DAG generation."
            )
    return errors


def _database_task_semantic_errors(
    task: dict[str, Any],
    *,
    paths: list[str],
) -> list[str]:
    """校验 database task 只能处理数据库，不能混入代码修改。"""

    task_id = str(task.get("id") or "")
    task_type = str(task.get("task_type") or "")
    errors: list[str] = []
    if task_type not in {"database.change", "database.seed", "database.verify"}:
        errors.append(f"Database task {task_id} has invalid task_type {task_type}.")
    if not isinstance(task.get("database_scope"), dict) or not task.get("database_scope"):
        errors.append(f"Database task {task_id} must declare non-empty database_scope.")
    code_paths = [path for path in paths if _is_code_path(path)]
    if code_paths:
        errors.append(
            f"Database task {task_id} must not modify code files: {', '.join(code_paths)}."
        )
    if _database_task_requires_approval(task) and not _approval_required(task):
        errors.append(f"High-risk database task {task_id} must require user approval.")
    return errors


def _task_declared_paths(task: dict[str, Any]) -> list[str]:
    """汇总任务声明的所有文件路径，供职责校验使用。"""

    paths = [*_task_target_files(task)]
    paths.extend(_string_list(task.get("allowed_paths")))
    for change in task.get("change_scope") if isinstance(task.get("change_scope"), list) else []:
        if isinstance(change, dict) and change.get("path"):
            paths.append(str(change.get("path")))
    return _dedupe_normalized_strings(
        [normalize_repo_path(path) for path in paths if normalize_repo_path(path)]
    )


def _is_code_path(path: str) -> bool:
    """判断路径是否属于代码或前端样式文件，database task 不允许修改。"""

    normalized = normalize_repo_path(path).lower()
    return normalized.endswith(_CODE_PATH_SUFFIXES)


def _database_task_requires_approval(task: dict[str, Any]) -> bool:
    """识别删除、截断等高危数据库操作是否需要人工审批。"""

    scope = _dict_value(task.get("database_scope"))
    operations = _database_operation_names(scope)
    return any(operation in _HIGH_RISK_DATABASE_OPERATIONS for operation in operations)


def _database_operation_names(value: Any) -> set[str]:
    """只读取结构化 operation 字段，避免 endpoint 名称中的 delete 等词误触发审批。"""

    operations: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"operation", "operations"}:
                candidates = item if isinstance(item, list) else [item]
                operations.update(
                    str(candidate).strip().lower()
                    for candidate in candidates
                    if str(candidate).strip()
                )
            operations.update(_database_operation_names(item))
    elif isinstance(value, list):
        for item in value:
            operations.update(_database_operation_names(item))
    return operations


def _approval_required(task: dict[str, Any]) -> bool:
    """读取任务审批标记。"""

    approval = task.get("approval")
    return isinstance(approval, dict) and approval.get("required") is True


def _task_summary(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """按叶子任务状态和执行所有者计算 v3 计划摘要。"""

    return {
        "total": len(tasks),
        "frontend": len([task for task in tasks if task.get("owner") == "frontend"]),
        "backend": len([task for task in tasks if task.get("owner") == "backend"]),
        "database": len([task for task in tasks if task.get("owner") == "database"]),
        "pending": len([task for task in tasks if task.get("status") == "pending"]),
        "running": len([task for task in tasks if task.get("status") == "running"]),
        "completed": len(
            [
                task
                for task in tasks
                if task.get("status") in {"completed", "already_satisfied"}
            ]
        ),
        "already_satisfied": len(
            [task for task in tasks if task.get("status") == "already_satisfied"]
        ),
        "failed": len([task for task in tasks if task.get("status") == "failed"]),
    }


def replace_build_task_plan_tasks(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用最新叶子任务重建 v2 注册表、任务图和执行批次。"""

    normalized_tasks = [_canonical_task(task) for task in tasks]
    annotated_tasks, execution_batches = _annotate_parallelism(normalized_tasks)
    build_units = deepcopy(
        build_task_plan.get("build_units")
        if isinstance(build_task_plan.get("build_units"), dict)
        else {}
    )
    for unit_id, unit in build_units.items():
        if isinstance(unit, dict):
            unit["task_ids"] = [
                task["id"] for task in annotated_tasks if task.get("unit_id") == unit_id
            ]
    return {
        **build_task_plan,
        "build_units": build_units,
        "task_registry": {task["id"]: task for task in annotated_tasks},
        "task_graph": _build_task_graph(
            annotated_tasks,
            execution_batches,
            build_context or {},
        ),
        "summary": {
            **(
                build_task_plan.get("summary")
                if isinstance(build_task_plan.get("summary"), dict)
                else {}
            ),
            **_task_summary(annotated_tasks),
        },
        "execution": {
            **(
                build_task_plan.get("execution")
                if isinstance(build_task_plan.get("execution"), dict)
                else {}
            ),
            "batches": execution_batches,
            "blocked_batches": [
                batch for batch in execution_batches if batch.get("mode") == "blocked"
            ],
        },
    }


def _canonical_task(task: dict[str, Any]) -> dict[str, Any]:
    """把任务对象收敛为 DAG v3 的 snake_case 单一字段形态。"""

    canonical = dict(task)
    canonical["id"] = _text(task.get("id"), "task")
    canonical["unit_id"] = _text(task.get("unit_id"), "application:root")
    canonical["task_type"] = _text(
        task.get("task_type"),
        _default_task_type(str(task.get("owner") or "")),
    )
    canonical["dependencies"] = _dedupe_strings(_task_dependencies(task))
    canonical["target_files"] = _dedupe_normalized_strings(_task_target_files(task))
    canonical["can_run_in_parallel"] = _task_can_run_in_parallel(task)
    canonical.pop("acceptance_criteria", None)
    canonical.pop("verification_commands", None)
    canonical["deliverables"] = normalize_deliverables(task.get("deliverables"))
    canonical["source_refs"] = (
        task.get("source_refs") if isinstance(task.get("source_refs"), dict) else {}
    )
    return ensure_engineering_acceptance(canonical)


def _default_task_type(owner: str) -> str:
    """根据 owner 生成默认任务类型。"""

    if owner == "database":
        return "database.change"
    if owner == "backend":
        return "backend.code"
    return "frontend.code"


def compile_build_task_plan_scope(
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    build_context: dict[str, Any] | None = None,
    *,
    validate_task_scope: bool = True,
    preserve_compiled_task_ids: set[str] | None = None,
) -> dict[str, Any]:
    """编译本轮任务契约，并在保留历史契约的前提下重建任务图。"""

    context = build_context if isinstance(build_context, dict) else {}
    preserved_ids = preserve_compiled_task_ids or set()
    scoped_tasks = apply_unit_compilation(
        build_task_plan,
        tasks,
        context,
        preserve_compiled_task_ids=preserved_ids,
    )
    current_tasks = [
        task
        for task in scoped_tasks
        if str(task.get("id") or "") not in preserved_ids
    ]
    current_tasks = compile_engineering_acceptance(current_tasks, context)
    current_tasks = compile_business_acceptance(current_tasks, context)
    current_tasks_by_id = {
        str(task.get("id") or ""): task for task in current_tasks
    }
    scoped_tasks = [
        {
            **task,
            "business_acceptance_checks": _dict_items(
                task.get("business_acceptance_checks")
            ),
        }
        if str(task.get("id") or "") in preserved_ids
        else current_tasks_by_id[str(task.get("id") or "")]
        for task in scoped_tasks
    ]
    compiled = replace_build_task_plan_tasks(
        build_task_plan,
        scoped_tasks,
        {
            **context,
            # 增量 Build 会保留其他 Unit 的既有任务；模型候选已经单独完成范围校验，
            # 最终合并图只复核拓扑和职责，避免把保留任务误判为当前范围越界。
            "_validate_task_scope": validate_task_scope,
        },
    )
    compiled["build_units"] = annotate_unit_inputs(
        compiled.get("build_units"),
        context,
        compiled.get("task_registry"),
    )
    return compiled


def create_build_task_plan(
    project_plan: dict[str, Any],
    agent_note: str = "live main-agent build task preparation",
    agent_plan: dict[str, Any] | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
    base_build_task_plan: dict[str, Any] | None = None,
    build_context: dict[str, Any] | None = None,
    build_execution_scope: dict[str, Any] | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    """编译模型候选任务并合并到全局 Unit 骨架，不修剪语义边界。"""

    raw_tasks = _raw_agent_tasks(agent_plan)
    proposed_tasks = _normalize_agent_tasks(raw_tasks, workspace_root=workspace_root)
    context = build_context or {}
    proposed_tasks = merge_exact_duplicate_tasks(proposed_tasks)
    proposed_tasks = reconcile_live_page_paths(
        proposed_tasks,
        workspace_root=workspace_root,
        build_context=context,
    )
    logger.info(
        "build_task_plan_candidate_compilation parsed_keys=%s raw_tasks_type=%s raw_tasks_count=%s "
        "valid_tasks_count=%s valid_task_ids=%s",
        sorted(str(key) for key in agent_plan) if isinstance(agent_plan, dict) else [],
        type(raw_tasks).__name__,
        len(raw_tasks) if isinstance(raw_tasks, list) else 0,
        len(proposed_tasks),
        [task["id"] for task in proposed_tasks],
    )
    if not proposed_tasks:
        logger.warning(
            "build_task_plan_no_valid_tasks parsed_keys=%s raw_tasks_type=%s raw_tasks_count=%s",
            sorted(str(key) for key in agent_plan) if isinstance(agent_plan, dict) else [],
            type(raw_tasks).__name__,
            len(raw_tasks) if isinstance(raw_tasks, list) else 0,
        )
        raise ValueError("Build task model output did not include any valid tasks.")
    base_plan = deepcopy(base_build_task_plan or {})
    tasks = apply_unit_compilation(base_plan, proposed_tasks, context)
    acceptance_context = {
        **context,
        "project_plan": project_plan,
        "executable_details": (
            _dict_value(project_plan.get("executable_details"))
            or _dict_value(context.get("executable_details"))
        ),
    }
    tasks = compile_engineering_acceptance(tasks, acceptance_context)
    tasks = compile_business_acceptance(tasks, acceptance_context)
    tasks, execution_batches = _annotate_parallelism(tasks)
    task_graph = _build_task_graph(tasks, execution_batches, context)
    blocked_batches = [
        batch for batch in execution_batches if batch.get("mode") == "blocked"
    ]

    plan = {
        **base_plan,
        "version": "3.0.0",
        "schema_version": "build-dag.v3",
        "status": (
            "ready"
            if task_graph["validation"]["is_valid"] and not blocked_batches
            else "blocked"
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "build_execution_scope": deepcopy(build_execution_scope or context.get("scope") or {}),
        "confirmation_status": "pending",
        "confirmed_at": None,
        "application": base_plan.get("application") or {"unit_id": "application:root", "status": "prepared"},
        "build_units": base_plan.get("build_units") or {
            "application:root": {
                "id": "application:root",
                "kind": "application",
                "status": "prepared",
                "task_ids": [task["id"] for task in tasks],
                "depends_on_unit_ids": [],
                "source_refs": {},
            }
        },
        "unit_graph": base_plan.get("unit_graph") or {
            "schema_version": "build-unit-graph.v3",
            "nodes": ["application:root"],
            "edges": [],
            "validation": {"is_valid": True, "errors": []},
        },
        "execution_history": base_plan.get("execution_history") or [],
        "workspace_analysis": (
            _workspace_analysis((agent_plan or {}).get("workspace_analysis"))
            if (agent_plan or {}).get("workspace_analysis")
            else _workspace_analysis_from_snapshot(workspace_snapshot)
        ),
        "workspace_snapshot_ref": {
            "workspace_revision": (workspace_snapshot or {}).get("workspace_revision"),
            "schema_version": (workspace_snapshot or {}).get("schema_version"),
        },
        "summary": _task_summary(tasks),
        "execution": {
            "owner": "main-agent",
            "strategy": "Dispatch database prerequisites first, then run dependency-ready backend and page tasks in parallel when file locks do not overlap.",
            "batches": execution_batches,
            "blocked_batches": blocked_batches,
        },
        "prepared_by": {
            "agent": "prepare-build-tasks",
            "mode": "model-normalized",
            "model": None,
            "source": "confirmed_project_plan_and_workspace_snapshot",
        },
        "preparation_source": "confirmed_project_plan_and_workspace_snapshot",
        "agent_note": _compact_agent_note(agent_note, agent_plan),
    }
    return compile_build_task_plan_scope(plan, tasks, acceptance_context)


def _compact_agent_note(agent_note: str, agent_plan: dict[str, Any] | None) -> str:
    """保存短模型诊断，不把完整模型 JSON 重复写入 DAG。"""

    task_count = len(_raw_agent_tasks(agent_plan) or [])
    fingerprint = sha256(str(agent_note or "").encode("utf-8")).hexdigest()[:16]
    return f"task_model_response sha256={fingerprint} task_count={task_count}"


def _dict_value(value: Any) -> dict[str, Any]:
    """将不可信输入规整为字典，便于后续合并元数据。"""

    return dict(value) if isinstance(value, dict) else {}
