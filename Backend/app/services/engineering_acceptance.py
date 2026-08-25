from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any


_CHANGE_TYPES = {
    "add": "added",
    "modify": "modified",
    "delete": "deleted",
}


def compile_engineering_acceptance(
    tasks: list[dict[str, Any]],
    task_preparation_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """为任务编译纯工程验收检查，并覆盖模型返回的业务验收文案。"""

    context = task_preparation_context if isinstance(task_preparation_context, dict) else {}
    return [_compile_task(task, context, recovery=False) for task in tasks]


def ensure_engineering_acceptance(task: dict[str, Any]) -> dict[str, Any]:
    """为恢复出的旧任务补齐可由任务元数据确定的基础工程验收检查。"""

    existing = _dict_items(task.get("acceptance_checks"))
    if existing:
        if not _task_requires_contract_binding(task):
            # 旧计划可能把 endpoint id 机械投影成配置任务的契约验收；
            # 恢复时只移除错误归属的契约检查，保留文件、范围和其他确定性门禁。
            existing = [
                check
                for check in existing
                if check.get("kind")
                not in {"frontend_contract_binding", "backend_contract_binding"}
            ]
        return {
            **task,
            "acceptance_checks": existing,
            "acceptance_criteria": _criteria_from_checks(existing),
        }
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
    compiled["acceptance_criteria"] = _criteria_from_checks(checks)
    compiled["engineering_acceptance_recompile_required"] = False
    compiled["repair_acceptance_version"] = "repair-acceptance.v2"
    return compiled


def migrate_legacy_repair_acceptance(
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """迁移因继承父任务历史文件差异而失败的旧 Repair，并允许安全续跑。"""

    tasks_by_id = {
        str(task.get("id") or ""): task
        for task in tasks
        if str(task.get("id") or "")
    }
    migrated: list[dict[str, Any]] = []
    for task in tasks:
        if task.get("kind") != "repair":
            migrated.append(task)
            continue
        parent_id = str(_dict_value(task.get("repairs")).get("task_id") or "")
        parent_task = tasks_by_id.get(parent_id)
        if not parent_task:
            migrated.append(task)
            continue
        failure_reason = str(task.get("failure_reason") or "")
        add_change_mismatch = (
            task.get("status") == "failed"
            and task.get("failure_category") == "acceptance_verification_failed"
            and "预期差异类型 added" in failure_reason
        )
        if task.get("repair_acceptance_version") and not add_change_mismatch:
            migrated.append(task)
            continue
        repair_scope = _legacy_repair_scope(task, parent_task) or _dict_items(
            task.get("change_scope")
        )
        candidate = compile_repair_engineering_acceptance(
            {**task, "change_scope": repair_scope},
            parent_task,
        )
        if add_change_mismatch:
            candidate = {
                **candidate,
                "status": "pending",
                "last_result_status": None,
                "failure_category": None,
                "failure_reason": None,
                "legacy_acceptance_recovered": True,
            }
        migrated.append(candidate)
    return migrated


def _legacy_repair_scope(
    task: dict[str, Any],
    parent_task: dict[str, Any],
) -> list[dict[str, str]]:
    """从旧修复描述中提取明确提及的父任务精确路径。"""

    repair_text = "\n".join(
        str(value or "")
        for value in (
            task.get("title"),
            task.get("description"),
            task.get("repair_strategy"),
        )
    )
    parent_paths = [
        str(item.get("path") or "").strip()
        for item in _dict_items(parent_task.get("change_scope"))
        if str(item.get("path") or "").strip()
    ]
    return [
        {
            "operation": "modify",
            "path": path,
            "description": "从旧 Repair 描述恢复的精确修复目标。",
        }
        for path in parent_paths
        if path in repair_text or path.lstrip("./") in repair_text
    ]


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
    criteria = _string_list(task.get("acceptance_criteria"))
    if criteria != _criteria_from_checks(checks):
        errors.append(
            f"Task {task_id} acceptance_criteria is not the projection of acceptance_checks."
        )
    if (
        str(task.get("owner") or "") != "database"
        and str(task.get("status") or "pending") not in {"completed", "already_satisfied"}
        and not any(
            check.get("kind") in {"file_operation", "repair_change"}
            for check in checks
        )
    ):
        errors.append(f"Task {task_id} does not define any file operation acceptance check.")
    if task.get("engineering_acceptance_recompile_required") is True:
        errors.append(
            f"Task {task_id} lacks endpoint contract metadata for engineering acceptance; "
            "rerun prepare_build_tasks."
        )
    endpoint_ids = _string_list(_dict_value(task.get("source_refs")).get("endpoint_ids"))
    contract_kinds = {"frontend_contract_binding", "backend_contract_binding"}
    if endpoint_ids and _task_requires_contract_binding(task) and not any(
        check.get("kind") in contract_kinds for check in checks
    ):
        errors.append(
            f"Task {task_id} references endpoints but has no deterministic contract binding check."
        )
    return errors


def _compile_task(
    task: dict[str, Any],
    context: dict[str, Any],
    *,
    recovery: bool,
) -> dict[str, Any]:
    """根据文件范围和正式契约编译单个任务的工程检查。"""

    compiled = deepcopy(task)
    checks: list[dict[str, Any]] = []
    if str(task.get("owner") or "") == "database":
        checks.extend(_database_checks(task))
    else:
        checks.extend(_file_operation_checks(task))
        checks.append(_scope_boundary_check(task))
        contract_check = (
            _contract_binding_check(task, context)
            if _task_requires_contract_binding(task)
            else None
        )
        if contract_check:
            checks.append(contract_check)
    compiled["acceptance_checks"] = checks
    compiled["acceptance_criteria"] = _criteria_from_checks(checks)
    source_refs = _dict_value(task.get("source_refs"))
    endpoint_ids = _string_list(source_refs.get("endpoint_ids"))
    compiled["engineering_acceptance_recompile_required"] = bool(
        recovery
        and endpoint_ids
        and _task_requires_contract_binding(task)
        and not any(
            check.get("kind") in {"frontend_contract_binding", "backend_contract_binding"}
            for check in checks
        )
    )
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
        path = str(change.get("path") or "").strip().lstrip("./")
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


def _contract_binding_check(
    task: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any] | None:
    """从当前可执行详情中提取接口与 Schema，生成代码契约检查。"""

    source_refs = _dict_value(task.get("source_refs"))
    endpoint_ids = set(_string_list(source_refs.get("endpoint_ids")))
    if not endpoint_ids:
        return None
    executable = _dict_value(context.get("executable_details"))
    contracts = _dict_items(executable.get("api_contracts"))
    data_sources = {
        str(item.get("id") or ""): str(item.get("type") or "").lower()
        for item in _dict_items(executable.get("data_sources"))
    }
    confirmed_source_types = _confirmed_endpoint_source_types(context, executable)
    page_bindings = _page_response_bindings(executable)
    expectations: list[dict[str, Any]] = []
    for contract in contracts:
        contract_id = str(contract.get("id") or "")
        schemas = _dict_value(contract.get("schemas"))
        contract_entity_ids = set(_string_list(contract.get("entity_ids")))
        planned_source_type = next(
            (
                str(source.get("type") or "").lower()
                for source in _dict_items(executable.get("data_sources"))
                if contract_entity_ids
                and contract_entity_ids
                & {
                    str(entity.get("id") or "")
                    for entity in _dict_items(source.get("entities"))
                }
            ),
            "",
        )
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "")
            if endpoint_id not in endpoint_ids:
                continue
            response_fields = _schema_fields(
                schemas,
                endpoint.get("response_schema_ref"),
            )
            source_type = (
                confirmed_source_types.get((contract_id, endpoint_id))
                or confirmed_source_types.get(("", endpoint_id))
                or planned_source_type
            )
            expectations.append(
                {
                    "api_contract_id": contract_id,
                    "endpoint_id": endpoint_id,
                    "method": str(endpoint.get("method") or "").upper(),
                    "path": str(endpoint.get("path") or ""),
                    "request_schema_ref": str(endpoint.get("request_schema_ref") or ""),
                    "response_schema_ref": str(endpoint.get("response_schema_ref") or ""),
                    "request_fields": _schema_fields(
                        schemas,
                        endpoint.get("request_schema_ref"),
                    ),
                    "response_fields": response_fields,
                    "response_binding_fields": page_bindings.get(endpoint_id, []),
                    "source_type": source_type,
                }
            )
    if not expectations:
        return None
    owner = str(task.get("owner") or "")
    kind = "backend_contract_binding" if owner == "backend" else "frontend_contract_binding"
    endpoint_text = "、".join(
        f"{item['method']} {item['path']}" for item in expectations
    )
    has_frontend_api = any(
        "/src/apis/" in f"/{path.replace(chr(92), '/')}"
        for path in _allowed_paths(task)
    )
    if owner == "backend":
        description = (
            f"后端必须实现已确认接口 {endpoint_text}，并通过 DTO JSON 映射匹配请求、响应 Schema。"
        )
    elif has_frontend_api:
        description = (
            f"前端 API 模块必须通过集中 service 绑定 {endpoint_text}，并声明请求、响应 Schema 字段。"
        )
    else:
        description = (
            f"页面必须绑定已确认接口 {endpoint_text}，并引用 PageImplementationContract 声明的响应绑定字段。"
        )
    return _check(
        task,
        kind=kind,
        description=description,
        target_paths=_allowed_paths(task),
        expected={"endpoints": expectations},
    )


def _task_requires_contract_binding(task: dict[str, Any]) -> bool:
    """仅让真正拥有接口实现或前端接口消费代码的任务承担契约验收。"""

    endpoint_ids = _string_list(_dict_value(task.get("source_refs")).get("endpoint_ids"))
    if not endpoint_ids:
        return False
    owner = str(task.get("owner") or "")
    paths = [
        "/" + path.lstrip("/").replace("\\", "/").lower()
        for path in _allowed_paths(task)
    ]
    if owner == "backend":
        return any(_is_backend_endpoint_implementation_path(path) for path in paths)
    if owner == "frontend":
        return any(
            "/src/apis/" in path
            or ("/src/pages/" in path and path.endswith((".tsx", ".ts", ".jsx", ".js")))
            for path in paths
        )
    return False


def _is_backend_endpoint_implementation_path(path: str) -> bool:
    """识别可承载 Spring Mapping 的后端处理器路径，排除配置与基础设施前置文件。"""

    if not path.endswith((".java", ".kt")):
        return False
    filename = path.rsplit("/", 1)[-1]
    return (
        any(token in path for token in ("/controller/", "/adapter/web/", "/web/", "/api/"))
        or filename.endswith(
            (
                "controller.java",
                "controller.kt",
                "resource.java",
                "resource.kt",
                "endpoint.java",
                "endpoint.kt",
                "handler.java",
                "handler.kt",
            )
        )
    )


def _confirmed_endpoint_source_types(
    context: dict[str, Any],
    executable: dict[str, Any],
) -> dict[tuple[str, str], str]:
    """从当前范围已确认实体绑定推导接口数据来源。"""

    entity_designs = [
        *_dict_items(context.get("entity_designs")),
        *_dict_items(executable.get("entity_designs")),
    ]
    source_types = [
        str(detail.get("data_source_type") or "").lower()
        for detail in entity_designs
        if str(detail.get("data_source_type") or "").strip()
    ]
    source_type = source_types[0] if len(set(source_types)) == 1 else "mixed" if source_types else ""
    result: dict[tuple[str, str], str] = {}
    if not source_type:
        return result
    for contract in _dict_items(executable.get("api_contracts")):
        contract_id = str(contract.get("id") or "")
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "")
            if endpoint_id:
                result[(contract_id, endpoint_id)] = source_type
                result.setdefault(("", endpoint_id), source_type)
    return result


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


def _page_response_bindings(executable: dict[str, Any]) -> dict[str, list[str]]:
    """按 endpoint 汇总页面实际绑定的响应字段，避免要求页面消费完整 Schema。"""

    result: dict[str, list[str]] = {}
    details = _dict_items(executable.get("page_implementation_contracts"))
    for detail in details:
        bindings = detail.get("responseBindings") or detail.get("response_bindings")
        for binding in _dict_items(bindings):
            endpoint_id = str(binding.get("endpointId") or binding.get("endpoint_id") or "")
            source_path = str(binding.get("sourcePath") or binding.get("source_path") or "")
            field = _terminal_field(source_path)
            if endpoint_id and field and field not in result.setdefault(endpoint_id, []):
                result[endpoint_id].append(field)
    return result


def _schema_fields(schemas: dict[str, Any], schema_ref: Any) -> list[str]:
    """递归提取指定 Schema 的属性名，并限制递归引用深度。"""

    schema_name = str(schema_ref or "").rsplit("/", 1)[-1]
    fields: list[str] = []
    visited: set[str] = set()

    def visit(schema: Any, depth: int) -> None:
        """遍历对象、数组和本地引用，收集可在生成代码中检查的字段。"""

        if depth > 4 or not isinstance(schema, dict):
            return
        ref_name = str(schema.get("$ref") or "").rsplit("/", 1)[-1]
        if ref_name:
            if ref_name in visited:
                return
            visited.add(ref_name)
            visit(schemas.get(ref_name), depth + 1)
        properties = _dict_value(schema.get("properties"))
        for name, child in properties.items():
            field = str(name).strip()
            if field and field not in fields:
                fields.append(field)
            visit(child, depth + 1)
        visit(schema.get("items"), depth + 1)

    visit(schemas.get(schema_name), 0)
    return fields[:100]


def _terminal_field(path: str) -> str:
    """从 JSONPath 或点路径中提取最终字段名。"""

    value = str(path or "").replace("[*]", "").replace("[]", "").strip("$.")
    return value.rsplit(".", 1)[-1] if value else ""


def _allowed_paths(task: dict[str, Any]) -> list[str]:
    """汇总任务声明的精确和通配授权路径。"""

    paths = _string_list(task.get("allowed_paths"))
    paths.extend(_string_list(task.get("target_files")))
    paths.extend(
        str(change.get("path") or "")
        for change in _dict_items(task.get("change_scope"))
    )
    return _dedupe(path.lstrip("./") for path in paths if path)


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


def _criteria_from_checks(checks: list[dict[str, Any]]) -> list[str]:
    """把内部检查投影为现有前端继续使用的字符串验收点。"""

    return _dedupe(
        str(check.get("description") or "").strip()
        for check in checks
        if str(check.get("description") or "").strip()
    )


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
