from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any


_CHANGE_TYPES = {
    "add": "added",
    "modify": "modified",
    "delete": "deleted",
}

_PAGE_PLACEHOLDER_MARKERS = (
    "hello agent!",
    "待 Agent 生成真实内容",
    "待agent生成真实内容",
    "临时占位",
    "placeholder content",
)


def compile_engineering_acceptance(
    tasks: list[dict[str, Any]],
    task_preparation_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """为任务编译纯工程验收检查，不生成业务契约或字符串验收标准。"""

    context = task_preparation_context if isinstance(task_preparation_context, dict) else {}
    return [_compile_task(task, context, recovery=False) for task in tasks]


def ensure_engineering_acceptance(task: dict[str, Any]) -> dict[str, Any]:
    """为当前任务补齐可由任务元数据确定的工程验收检查。"""

    # 任务对象中的检查字段属于不可信输入；每次进入执行或验证边界都重新编译，
    # 防止模型、旧持久化内容或 Repair 提示词注入未注册的工程检查类型。
    return _compile_task(task, {}, recovery=True)


def compile_repair_engineering_acceptance(
    task: dict[str, Any],
    parent_task: dict[str, Any],
) -> dict[str, Any]:
    """为修复任务编译本轮文件检查，并继承父任务的最终工程结果检查。"""

    compiled = deepcopy(task)
    parent_checks = _dict_items(parent_task.get("acceptance_checks"))
    outcome_checks = [
        check
        for check in parent_checks
        if check.get("kind") not in {"file_operation", "repair_change", "scope_boundary"}
    ]
    if str(task.get("owner") or "") == "database":
        checks = outcome_checks or _database_checks(parent_task)
    else:
        repair_scope = _dict_items(task.get("change_scope"))
        checks = _file_operation_checks(task) if repair_scope else [
            _repair_change_check(task, parent_checks)
        ]
        checks.append(_scope_boundary_check(task))
        checks.extend(outcome_checks)
    compiled["acceptance_checks"] = checks
    compiled["repair_acceptance_version"] = "repair-acceptance.v2"
    return compiled


def engineering_acceptance_contract_errors(task: dict[str, Any]) -> list[str]:
    """校验任务是否具备可执行的工程验收契约。"""

    task_id = str(task.get("id") or "")
    checks = _dict_items(task.get("acceptance_checks"))
    errors: list[str] = []
    if not checks:
        return [f"Task {task_id} does not define engineering acceptance checks."]
    check_ids = [str(check.get("id") or "") for check in checks]
    if any(not check_id for check_id in check_ids):
        errors.append(f"Task {task_id} contains an acceptance check without id.")
    if len(set(check_ids)) != len(check_ids):
        errors.append(f"Task {task_id} contains duplicate acceptance check ids.")
    if (
        str(task.get("owner") or "") != "database"
        and str(task.get("status") or "pending") not in {"completed", "already_satisfied"}
        and not any(
            check.get("kind") in {"file_operation", "repair_change"}
            for check in checks
        )
    ):
        errors.append(f"Task {task_id} does not define any file operation acceptance check.")
    return errors


def _compile_task(
    task: dict[str, Any],
    context: dict[str, Any],
    *,
    recovery: bool,
) -> dict[str, Any]:
    """根据文件范围和正式契约编译单个任务的工程检查。"""

    del context, recovery
    compiled = deepcopy(task)
    compiled.pop("acceptance_criteria", None)
    compiled.pop("verification_commands", None)
    checks: list[dict[str, Any]] = []
    if str(task.get("owner") or "") == "database":
        checks.extend(_database_checks(task))
    else:
        checks.extend(_file_operation_checks(task))
        checks.append(_scope_boundary_check(task))
        checks.extend(_page_structure_checks(task))
        checks.extend(_frontend_api_boundary_checks(task))
        checks.extend(_frontend_authorization_checks(task))
        checks.extend(_backend_authorization_checks(task))
    compiled["acceptance_checks"] = checks
    if (
        compiled.get("status") == "already_satisfied"
        and compiled.get("satisfied_by") == "frontend-template-page-scaffold"
    ):
        compiled["acceptance_evidence"] = [
            {
                "check_id": check.get("id"),
                "kind": check.get("kind"),
                "status": "passed",
                "evidence": "任务准备阶段已通过确定性菜单解析器确认现有工程状态。",
            }
            for check in checks
        ]
    return compiled


def _file_operation_checks(task: dict[str, Any]) -> list[dict[str, Any]]:
    """把 change_scope 编译为逐文件且可由工作区差异验证的检查。"""

    changes = _dict_items(task.get("change_scope"))
    if not changes:
        fallback_paths = _string_list(task.get("target_files")) or [
            path
            for path in _string_list(task.get("allowed_paths"))
            if not any(token in path for token in ("*", "?", "["))
        ]
        changes = [
            {"operation": "modify", "path": path}
            for path in fallback_paths
        ]
    result: list[dict[str, Any]] = []
    for change in changes:
        path = _normalize_path(change.get("path"))
        if not path:
            continue
        operation = str(change.get("operation") or "modify").strip().lower()
        if operation not in _CHANGE_TYPES:
            operation = "modify"
        description = (
            f"工程文件 {path} 必须完成 {operation} 操作，"
            f"工作区差异类型必须为 {_CHANGE_TYPES[operation]}。"
        )
        result.append(
            _check(
                task,
                kind="file_operation",
                description=description,
                target_paths=[path],
                expected={
                    "operation": operation,
                    "change_type": _CHANGE_TYPES[operation],
                },
            )
        )
    return result


def _page_structure_checks(task: dict[str, Any]) -> list[dict[str, Any]]:
    """为页面入口、默认导出、占位替换和任务内组件可达性生成工程检查。"""

    page_deliverables = [
        item
        for item in _dict_items(task.get("deliverables"))
        if str(item.get("kind") or "") == "frontend.page"
    ]
    checks: list[dict[str, Any]] = []
    for deliverable in page_deliverables:
        paths = [_normalize_path(path) for path in _string_list(deliverable.get("paths"))]
        paths = [path for path in paths if path]
        entry_path = next(
            (path for path in paths if path.casefold().endswith("/index.tsx")),
            "",
        )
        if not entry_path:
            continue
        page_id = str(deliverable.get("target_id") or task.get("unit_id") or "")
        checks.extend(
            [
                _check(
                    task,
                    kind="page_entry",
                    description="页面交付物必须包含可读取的 index.tsx 页面入口。",
                    target_paths=[entry_path],
                    expected={"page_id": page_id, "entry_path": entry_path},
                ),
                _check(
                    task,
                    kind="page_default_export",
                    description="页面入口必须提供 default export，确保路由可以加载页面组件。",
                    target_paths=[entry_path],
                    expected={"entry_path": entry_path, "requires_default_export": True},
                ),
                _check(
                    task,
                    kind="page_placeholder",
                    description="页面入口不得保留模板占位内容。",
                    target_paths=[entry_path],
                    expected={
                        "entry_path": entry_path,
                        "forbidden_markers": list(_PAGE_PLACEHOLDER_MARKERS),
                    },
                ),
            ]
        )
        for component_path in paths:
            if component_path.casefold() == entry_path.casefold() or not component_path.casefold().endswith(".tsx"):
                continue
            component_symbol = _component_symbol(component_path)
            checks.append(
                _check(
                    task,
                    kind="page_component_reachability",
                    description="页面任务内新增组件必须导出，并由页面入口通过 import 或符号引用可达。",
                    target_paths=[entry_path, component_path],
                    expected={
                        "entry_path": entry_path,
                        "component_path": component_path,
                        "component_symbol": component_symbol,
                    },
                )
            )
    return checks


def _frontend_authorization_checks(task: dict[str, Any]) -> list[dict[str, Any]]:
    """为带平台 Action 权限约束的页面任务编译确定性接入检查。"""

    if str(task.get("owner") or "") != "frontend" or not str(
        task.get("unit_id") or ""
    ).startswith("page:"):
        return []
    source_refs = _dict_value(task.get("source_refs"))
    authorization = _dict_value(source_refs.get("authorization"))
    actions = [
        {
            "actionId": str(item.get("actionId") or "").strip(),
            "resourceKey": str(item.get("resourceKey") or "").strip(),
        }
        for item in _dict_items(authorization.get("actions"))
        if str(item.get("actionId") or "").strip()
        and str(item.get("resourceKey") or "").strip()
    ]
    if not actions:
        return []
    bindings = _dict_value(source_refs.get("page_implementation_contract")).get(
        "actionBindings"
    )
    all_action_ids = {
        str(item.get("actionId") or "").strip()
        for item in _dict_items(bindings)
        if str(item.get("actionId") or "").strip()
    }
    controlled_action_ids = {item["actionId"] for item in actions}
    paths = [
        _normalize_path(path)
        for deliverable in _dict_items(task.get("deliverables"))
        if str(deliverable.get("kind") or "") == "frontend.page"
        for path in _string_list(deliverable.get("paths"))
        if _normalize_path(path).casefold().endswith(".tsx")
    ]
    if not paths:
        paths = [
            path
            for path in _allowed_paths(task)
            if path.casefold().endswith(".tsx")
        ]
    if not paths:
        return []
    return [
        _check(
            task,
            kind="frontend_authorization",
            description="受控页面操作必须以平台给定 resourceKey 接入唯一 hidden Permission，且页面不得直连 HTTP 客户端。",
            target_paths=_dedupe(paths),
            expected={
                "controlledActions": actions,
                "uncontrolledActionIds": sorted(all_action_ids - controlled_action_ids),
            },
        )
    ]


def _frontend_api_boundary_checks(task: dict[str, Any]) -> list[dict[str, Any]]:
    """为页面任务禁止直接 HTTP 客户端调用的边界编译检查。"""

    if str(task.get("owner") or "") != "frontend" or not str(
        task.get("unit_id") or ""
    ).startswith("page:"):
        return []
    paths = [
        _normalize_path(path)
        for deliverable in _dict_items(task.get("deliverables"))
        if str(deliverable.get("kind") or "") == "frontend.page"
        for path in _string_list(deliverable.get("paths"))
        if _normalize_path(path).casefold().endswith(".tsx")
    ]
    if not paths:
        paths = [
            path
            for path in _allowed_paths(task)
            if path.casefold().endswith(".tsx")
        ]
    if not paths:
        return []
    return [
        _check(
            task,
            kind="frontend_api_boundary",
            description="页面和任务内组件不得直接调用 fetch、axios 或 service；业务接口必须经 src/apis/ 与 useRequest。",
            target_paths=_dedupe(paths),
            expected={},
        )
    ]


def _backend_authorization_checks(task: dict[str, Any]) -> list[dict[str, Any]]:
    """为后端 Endpoint 任务编译唯一 Controller ANY-OF 注解检查。"""

    if str(task.get("owner") or "") != "backend" or not str(
        task.get("unit_id") or ""
    ).startswith("backend:endpoint:"):
        return []
    source_refs = _dict_value(task.get("source_refs"))
    authorization = _dict_value(source_refs.get("authorization"))
    endpoints = _dict_items(authorization.get("endpoints"))
    if len(endpoints) != 1:
        return []
    endpoint = endpoints[0]
    http_method = str(endpoint.get("httpMethod") or "").strip().upper()
    path = str(endpoint.get("path") or "").strip()
    resource_keys = _string_list(endpoint.get("operationResourceKeys"))
    if not http_method or not path.startswith("/") or str(endpoint.get("semantics") or "") != "ANY_OF":
        return []
    constants = {
        str(item.get("resourceKey") or "").strip(): str(item.get("name") or "").strip()
        for item in _dict_items(authorization.get("authConstants"))
    }
    if resource_keys and any(not constants.get(key) for key in resource_keys):
        return []
    paths = [
        _normalize_path(path)
        for deliverable in _dict_items(task.get("deliverables"))
        if str(deliverable.get("kind") or "") == "backend.endpoint_controller"
        for path in _string_list(deliverable.get("paths"))
        if _normalize_path(path).casefold().endswith(".java")
    ]
    if not paths:
        return []
    return [
        _check(
            task,
            kind="backend_authorization",
            description="Controller 目标 Endpoint 必须且只能以一个 RequireAnyResource 使用平台给定常量实现 ANY-OF。",
            target_paths=_dedupe(paths),
            expected={
                "endpointIdentity": {
                    "apiContractId": str(endpoint.get("apiContractId") or "").strip(),
                    "endpointId": str(endpoint.get("endpointId") or "").strip(),
                    "httpMethod": http_method,
                    "path": path,
                },
                "operationResourceKeys": resource_keys,
                "semantics": "ANY_OF",
                "authConstants": [
                    {"name": constants[key], "resourceKey": key}
                    for key in resource_keys
                ],
            },
        )
    ]


def _scope_boundary_check(task: dict[str, Any]) -> dict[str, Any]:
    """生成任务文件变更不得越过授权范围的检查。"""

    allowed_paths = _allowed_paths(task)
    return _check(
        task,
        kind="scope_boundary",
        description="任务实际文件变更必须全部位于 change_scope 或 allowed_paths 声明范围内。",
        target_paths=allowed_paths,
        expected={"allowed_paths": allowed_paths},
    )


def _repair_change_check(
    task: dict[str, Any],
    parent_checks: list[dict[str, Any]],
) -> dict[str, Any]:
    """为未声明精确修复文件的兼容任务生成至少一处授权变更检查。"""

    allowed_paths = _allowed_paths(task)
    target_states = [
        check
        for check in parent_checks
        if check.get("kind") == "file_operation"
    ]
    return _check(
        task,
        kind="repair_change",
        description="修复完成时必须至少产生一处授权文件变更；已满足时必须证明原目标状态成立。",
        target_paths=allowed_paths,
        expected={
            "allowed_paths": allowed_paths,
            "target_states": target_states,
        },
    )


def _database_checks(task: dict[str, Any]) -> list[dict[str, Any]]:
    """根据数据库 gap 与审批标记生成数据库工程验收检查。"""

    scope = _dict_value(task.get("database_scope"))
    gap_ids = _string_list(scope.get("gap_ids"))
    if not gap_ids:
        gap_ids = [
            str(gap.get("id") or "")
            for gap in _dict_items(scope.get("gaps"))
            if gap.get("id")
        ]
    checks = [
        _check(
            task,
            kind="database_gap",
            description="数据库执行后复查必须确认任务声明的 schema gap 已全部消除。",
            target_paths=[],
            expected={"gap_ids": gap_ids},
        )
    ]
    approval = _dict_value(task.get("approval"))
    if approval.get("required") is True:
        checks.append(
            _check(
                task,
                kind="database_approval",
                description="高风险数据库变更必须在执行前获得与当前计划指纹匹配的用户审批。",
                target_paths=[],
                expected={"required": True},
            )
        )
    return checks


def _allowed_paths(task: dict[str, Any]) -> list[str]:
    """汇总任务声明的精确和通配授权路径。"""

    paths = _string_list(task.get("allowed_paths"))
    paths.extend(_string_list(task.get("target_files")))
    paths.extend(
        str(change.get("path") or "")
        for change in _dict_items(task.get("change_scope"))
    )
    return _dedupe(_normalize_path(path) for path in paths if path)


def _normalize_path(value: Any) -> str:
    """统一工程验收中的 Windows、macOS 和 Linux 相对路径。"""

    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return "/".join(part for part in text.split("/") if part not in {"", "."})


def _component_symbol(path: str) -> str:
    """根据组件文件名生成用于可达性检查的稳定符号提示。"""

    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    parts = [part for part in re.split(r"[-_\s]+", stem) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Component"


def _check(
    task: dict[str, Any],
    *,
    kind: str,
    description: str,
    target_paths: list[str],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """生成包含稳定 ID 的内部工程验收检查。"""

    payload = json.dumps(
        {"kind": kind, "target_paths": target_paths, "expected": expected},
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = sha256(payload.encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"acceptance:{task.get('id') or 'task'}:{kind}:{digest}",
        "kind": kind,
        "description": description,
        "required": True,
        "target_paths": target_paths,
        "expected": expected,
        "verification_stage": "build",
    }


def _dedupe(values: Any) -> list[str]:
    """按原顺序去重字符串。"""

    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _string_list(value: Any) -> list[str]:
    """把不可信列表规整为非空字符串列表。"""

    return _dedupe(value if isinstance(value, list) else [])


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """把不可信列表规整为字典列表。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict_value(value: Any) -> dict[str, Any]:
    """把不可信对象规整为字典。"""

    return dict(value) if isinstance(value, dict) else {}
