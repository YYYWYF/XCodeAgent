"""Unit Candidate 的严格 Task、职责、文件范围和平台强规则校验。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase
import re
from typing import Any

from app.services.business_acceptance import DELIVERABLE_KINDS, normalize_repo_path
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import UnitGenerationContext


_REQUIRED_TASK_FIELDS = (
    "id", "unit_id", "owner", "task_type", "title", "description",
    "dependencies", "target_files", "change_scope", "allowed_paths",
    "deliverables", "impact_scope", "can_run_in_parallel", "parallel_reason", "status",
)
_ALLOWED_TASK_FIELDS = {*_REQUIRED_TASK_FIELDS, "source_refs", "requires_capabilities",
                        "provides_capabilities", "database_scope", "risk", "approval",
                        "engineering_context", "kind"}
_PLATFORM_FIELDS = {
    "acceptance_checks", "business_acceptance_checks", "acceptance_evidence",
    "verification_commands", "authorization", "authorization_constraints",
    "candidate_id", "validation_issues", "generation_metadata", "planning_run_id",
    "task_graph", "summary",
}
_IMPACT_LIST_FIELDS = ("affected_modules", "public_contracts", "risks")
_FRONTEND_KINDS = {
    "frontend.page", "frontend.api_module", "frontend.static_data_module",
    "frontend.shared_capability",
}
_BACKEND_KINDS = set(DELIVERABLE_KINDS) - _FRONTEND_KINDS
_OWNER_TASK_TYPES = {
    "frontend": {"frontend.code"},
    "backend": {"backend.code"},
    "database": {"database.change", "database.seed"},
}
_DRIVE_PATH = re.compile(r"^[A-Za-z]:/")


def _identity(value: Any) -> str | None:
    """只接受不需要平台修剪或转换的非空字符串。"""

    return value if isinstance(value, str) and value and value == value.strip() else None


def _task_id(task: Mapping[str, Any]) -> str:
    """返回可用于 Issue 归因的合法 Task ID，非法 ID 不伪造占位身份。"""

    return _identity(task.get("id")) or ""


def _issue(
    code: str, message: str, *, context: UnitGenerationContext,
    task_ids: Sequence[str] = (), **details: Any,
) -> ValidationIssue:
    """构造只重试当前 Unit 的模型内容问题。"""

    return ValidationIssue(
        code=code, level="unit", category="generation",
        unit_ids=(context.unit_id,), task_ids=tuple(item for item in task_ids if item),
        retry_unit_ids=(context.unit_id,), retryable=True, message=message, details=details,
    )


def _strict_path(value: Any) -> str | None:
    """验证精确、安全的仓库相对路径，不归一化后继续使用模型值。"""

    path = _identity(value)
    if (
        path is None or "\\" in path or path.startswith(("/", "./"))
        or _DRIVE_PATH.match(path) or ".." in path.split("/")
        or normalize_repo_path(path) != path
    ):
        return None
    return path


def _string_array_issues(
    task: Mapping[str, Any], field: str, *, context: UnitGenerationContext,
    required_non_empty: bool = True, issue_task_id: str | None = None,
) -> tuple[list[str], list[ValidationIssue]]:
    """严格读取字符串数组；重复或非法项目都显式报错。"""

    value = task.get(field)
    task_id = _task_id(task) if issue_task_id is None else issue_task_id
    if not isinstance(value, (list, tuple)):
        return [], [_issue(
            "CANDIDATE_TASK_FIELD_TYPE_INVALID",
            f"Candidate Task {task_id or '<unknown>'} 的 {field} 必须是数组。",
            context=context, task_ids=(task_id,), field=field,
            actual_type=type(value).__name__,
        )]
    items: list[str] = []
    issues: list[ValidationIssue] = []
    for index, item in enumerate(value):
        identity = _identity(item)
        if identity is None:
            issues.append(_issue(
                "CANDIDATE_TASK_ARRAY_ITEM_INVALID",
                f"Candidate Task {task_id or '<unknown>'} 的 {field}[{index}] 必须是精确非空字符串。",
                context=context, task_ids=(task_id,), field=field, index=index,
            ))
        else:
            items.append(identity)
    if required_non_empty and not value:
        issues.append(_issue(
            "CANDIDATE_TASK_FIELD_EMPTY",
            f"Candidate Task {task_id or '<unknown>'} 的 {field} 不得为空。",
            context=context, task_ids=(task_id,), field=field,
        ))
    duplicates = sorted({item for item in items if items.count(item) > 1})
    if duplicates:
        issues.append(_issue(
            "CANDIDATE_TASK_ARRAY_DUPLICATE",
            f"Candidate Task {task_id or '<unknown>'} 的 {field} 含重复值。",
            context=context, task_ids=(task_id,), field=field, duplicate_values=duplicates,
        ))
    return items, issues


def _path_array_issues(
    task: Mapping[str, Any], field: str, *, context: UnitGenerationContext,
    issue_task_id: str | None = None,
) -> tuple[list[str], list[ValidationIssue]]:
    """校验路径数组的类型、安全性和精确性。"""

    values, issues = _string_array_issues(
        task, field, context=context, issue_task_id=issue_task_id,
    )
    task_id = _task_id(task) if issue_task_id is None else issue_task_id
    valid: list[str] = []
    for index, value in enumerate(values):
        path = _strict_path(value)
        if path is None:
            issues.append(_issue(
                "CANDIDATE_PATH_INVALID",
                f"Candidate Task {task_id or '<unknown>'} 的 {field}[{index}] 不是安全的精确相对路径。",
                context=context, task_ids=(task_id,), field=field, index=index, path=value,
            ))
        else:
            valid.append(path)
    return valid, issues


def _path_allowed(path: str, allowed_paths: Sequence[str]) -> bool:
    """按精确路径或显式 glob 判断执行授权，不扩大目录权限。"""

    return any(path == pattern or fnmatchcase(path, pattern) for pattern in allowed_paths)


def _schema_issues(task: Mapping[str, Any], context: UnitGenerationContext) -> list[ValidationIssue]:
    """校验 Task 顶层字段、基础类型与固定状态，不填充默认值。"""

    task_id = _task_id(task)
    issues: list[ValidationIssue] = []
    for field in _REQUIRED_TASK_FIELDS:
        if field not in task:
            issues.append(_issue(
                "CANDIDATE_TASK_FIELD_MISSING",
                f"Candidate Task {task_id or '<unknown>'} 缺少必填字段 {field}。",
                context=context, task_ids=(task_id,), field=field,
            ))
    for field in sorted(set(task) - _ALLOWED_TASK_FIELDS):
        code = "CANDIDATE_PLATFORM_FIELD_FORBIDDEN" if field in _PLATFORM_FIELDS else "CANDIDATE_TASK_FIELD_UNSUPPORTED"
        issues.append(_issue(
            code, f"Candidate Task {task_id or '<unknown>'} 不得输出字段 {field}。",
            context=context, task_ids=(task_id,), field=field,
        ))
    for field in ("id", "unit_id", "owner", "task_type", "title", "description", "parallel_reason"):
        if field in task and _identity(task.get(field)) is None:
            issues.append(_issue(
                "CANDIDATE_TASK_FIELD_TYPE_INVALID",
                f"Candidate Task {task_id or '<unknown>'} 的 {field} 必须是精确非空字符串。",
                context=context, task_ids=(task_id,), field=field,
            ))
    if "can_run_in_parallel" in task and not isinstance(task.get("can_run_in_parallel"), bool):
        issues.append(_issue(
            "CANDIDATE_TASK_FIELD_TYPE_INVALID", "can_run_in_parallel 必须是布尔值。",
            context=context, task_ids=(task_id,), field="can_run_in_parallel",
        ))
    if "status" in task and task.get("status") != "pending":
        issues.append(_issue(
            "CANDIDATE_STATUS_INVALID", f"Candidate Task {task_id or '<unknown>'} 的 status 必须精确为 pending。",
            context=context, task_ids=(task_id,), actual_status=task.get("status"),
        ))
    if task.get("kind") == "repair" or str(task.get("task_type") or "").endswith(".verify"):
        issues.append(_issue(
            "CANDIDATE_STRONG_RULE_VIOLATION",
            f"Candidate Task {task_id or '<unknown>'} 不得生成 repair 或 verification 职责。",
            context=context, task_ids=(task_id,), rule="no_repair_or_verification_tasks",
        ))
    source_refs = task.get("source_refs")
    if source_refs is not None and not isinstance(source_refs, Mapping):
        issues.append(_issue(
            "CANDIDATE_TASK_FIELD_TYPE_INVALID", "source_refs 必须是对象。",
            context=context, task_ids=(task_id,), field="source_refs",
        ))
    elif isinstance(source_refs, Mapping) and "authorization" in source_refs:
        issues.append(_issue(
            "CANDIDATE_PLATFORM_FIELD_FORBIDDEN", "Candidate 不得输出平台拥有的 source_refs.authorization。",
            context=context, task_ids=(task_id,), field="source_refs.authorization",
        ))
    for field in ("requires_capabilities", "provides_capabilities"):
        if field in task:
            _, field_issues = _string_array_issues(
                task, field, context=context, required_non_empty=False,
            )
            issues.extend(field_issues)
    for field in ("database_scope", "approval", "engineering_context"):
        if field in task and not isinstance(task.get(field), Mapping):
            issues.append(_issue(
                "CANDIDATE_TASK_FIELD_TYPE_INVALID",
                f"Candidate Task {task_id or '<unknown>'} 的 {field} 必须是对象。",
                context=context, task_ids=(task_id,), field=field,
            ))
    if "risk" in task and task.get("risk") not in ("low", "medium", "high"):
        issues.append(_issue(
            "CANDIDATE_TASK_FIELD_VALUE_INVALID",
            f"Candidate Task {task_id or '<unknown>'} 的 risk 非法。",
            context=context, task_ids=(task_id,), field="risk", value=task.get("risk"),
        ))
    if "kind" in task and _identity(task.get("kind")) is None:
        issues.append(_issue(
            "CANDIDATE_TASK_FIELD_TYPE_INVALID",
            f"Candidate Task {task_id or '<unknown>'} 的 kind 必须是精确非空字符串。",
            context=context, task_ids=(task_id,), field="kind",
        ))
    return issues


def _identity_issues(task: Mapping[str, Any], context: UnitGenerationContext) -> list[ValidationIssue]:
    """校验 Task 的 Unit、owner 与 task_type 必须匹配冻结 Context。"""

    task_id = _task_id(task)
    issues: list[ValidationIssue] = []
    if _identity(task.get("unit_id")) is not None and task.get("unit_id") != context.unit_id:
        issues.append(_issue(
            "CANDIDATE_UNIT_ID_MISMATCH",
            f"Candidate Task {task_id or '<unknown>'} 必须属于冻结 Unit {context.unit_id}。",
            context=context, task_ids=(task_id,), actual_unit_id=task.get("unit_id"),
        ))
    expected_owner = context.constraints.get("owner")
    owner = _identity(task.get("owner"))
    if owner is not None and owner != expected_owner:
        issues.append(_issue(
            "CANDIDATE_OWNER_MISMATCH",
            f"Candidate Task {task_id or '<unknown>'} 的 owner 必须为 {expected_owner}。",
            context=context, task_ids=(task_id,), actual_owner=owner,
        ))
    task_type = _identity(task.get("task_type"))
    if owner in _OWNER_TASK_TYPES and task_type is not None and task_type not in _OWNER_TASK_TYPES[owner]:
        issues.append(_issue(
            "CANDIDATE_TASK_TYPE_INVALID",
            f"Candidate Task {task_id or '<unknown>'} 的 task_type 与 owner {owner} 不匹配。",
            context=context, task_ids=(task_id,), task_type=task_type, owner=owner,
        ))
    return issues


def _scope_issues(task: Mapping[str, Any], context: UnitGenerationContext) -> tuple[set[str], list[ValidationIssue]]:
    """校验 target_files、change_scope 与 allowed_paths 的精确闭合关系。"""

    task_id = _task_id(task)
    target_files, issues = _path_array_issues(task, "target_files", context=context)
    allowed_paths, allowed_issues = _path_array_issues(task, "allowed_paths", context=context)
    issues.extend(allowed_issues)
    changes = task.get("change_scope")
    change_paths: list[str] = []
    if not isinstance(changes, (list, tuple)):
        issues.append(_issue(
            "CANDIDATE_CHANGE_SCOPE_TYPE_INVALID", "change_scope 必须是非空对象数组。",
            context=context, task_ids=(task_id,), actual_type=type(changes).__name__,
        ))
    elif not changes:
        issues.append(_issue(
            "CANDIDATE_CHANGE_SCOPE_EMPTY", "change_scope 不得为空。",
            context=context, task_ids=(task_id,),
        ))
    else:
        for index, change in enumerate(changes):
            if not isinstance(change, Mapping):
                issues.append(_issue(
                    "CANDIDATE_CHANGE_SCOPE_ITEM_INVALID", "change_scope 项必须是对象。",
                    context=context, task_ids=(task_id,), index=index,
                ))
                continue
            if set(change) != {"operation", "path", "description"}:
                issues.append(_issue(
                    "CANDIDATE_CHANGE_SCOPE_SCHEMA_INVALID",
                    "change_scope 项必须且只能包含 operation、path、description。",
                    context=context, task_ids=(task_id,), index=index, fields=sorted(change),
                ))
            operation = change.get("operation")
            if operation not in ("add", "modify", "delete"):
                issues.append(_issue(
                    "CANDIDATE_CHANGE_OPERATION_INVALID", "change_scope.operation 必须为 add、modify 或 delete。",
                    context=context, task_ids=(task_id,), index=index, operation=operation,
                ))
            if _identity(change.get("description")) is None:
                issues.append(_issue(
                    "CANDIDATE_CHANGE_DESCRIPTION_INVALID", "change_scope.description 必须是精确非空字符串。",
                    context=context, task_ids=(task_id,), index=index,
                ))
            path = _strict_path(change.get("path"))
            if path is None:
                issues.append(_issue(
                    "CANDIDATE_CHANGE_PATH_INVALID", "change_scope.path 必须是安全的精确相对路径。",
                    context=context, task_ids=(task_id,), index=index, path=change.get("path"),
                ))
            else:
                change_paths.append(path)
    if len(change_paths) != len(set(change_paths)):
        issues.append(_issue(
            "CANDIDATE_CHANGE_PATH_DUPLICATE", "change_scope 不得重复声明同一路径。",
            context=context, task_ids=(task_id,),
        ))
    if target_files and change_paths and set(target_files) != set(change_paths):
        issues.append(_issue(
            "CANDIDATE_TARGET_FILES_MISMATCH",
            "target_files 必须与 change_scope 的路径集合完全一致。",
            context=context, task_ids=(task_id,), target_files=target_files, change_scope_paths=change_paths,
        ))
    unauthorized = sorted(path for path in set(target_files + change_paths) if not _path_allowed(path, allowed_paths))
    if unauthorized:
        issues.append(_issue(
            "CANDIDATE_PATH_OUTSIDE_ALLOWED_SCOPE", "Task 的目标路径超出 allowed_paths。",
            context=context, task_ids=(task_id,), paths=unauthorized,
        ))
    scope_paths = set(target_files) | set(change_paths)
    owner = _identity(task.get("owner"))
    expected_prefix = "frontend/" if owner == "frontend" else "backend/" if owner == "backend" else None
    wrong_layer = sorted(path for path in scope_paths if expected_prefix and not path.startswith(expected_prefix))
    if wrong_layer:
        issues.append(_issue(
            "CANDIDATE_TARGET_FILE_OWNER_MISMATCH",
            f"Candidate Task {task_id or '<unknown>'} 的目标文件不属于 owner {owner} 的工程层。",
            context=context, task_ids=(task_id,), owner=owner, paths=wrong_layer,
        ))
    return scope_paths, issues


def _deliverable_issues(
    task: Mapping[str, Any], context: UnitGenerationContext, scope_paths: set[str],
) -> tuple[list[tuple[str, str, tuple[str, ...], str]], list[ValidationIssue]]:
    """校验 deliverable schema、owner、文件范围并返回职责索引。"""

    task_id = _task_id(task)
    value = task.get("deliverables")
    issues: list[ValidationIssue] = []
    records: list[tuple[str, str, tuple[str, ...], str]] = []
    if not isinstance(value, (list, tuple)):
        return records, [_issue(
            "CANDIDATE_DELIVERABLES_TYPE_INVALID", "deliverables 必须是非空对象数组。",
            context=context, task_ids=(task_id,), actual_type=type(value).__name__,
        )]
    if not value:
        return records, [_issue(
            "CANDIDATE_DELIVERABLES_EMPTY", "模型生成 Unit 的 deliverables 不得为空。",
            context=context, task_ids=(task_id,),
        )]
    seen_ids: set[str] = set()
    owner = _identity(task.get("owner"))
    for index, deliverable in enumerate(value):
        if not isinstance(deliverable, Mapping):
            issues.append(_issue(
                "CANDIDATE_DELIVERABLE_ITEM_INVALID", "deliverables 项必须是对象。",
                context=context, task_ids=(task_id,), index=index,
            ))
            continue
        if set(deliverable) != {"id", "kind", "target_id", "paths", "provides"}:
            issues.append(_issue(
                "CANDIDATE_DELIVERABLE_SCHEMA_INVALID",
                "deliverable 必须且只能包含 id、kind、target_id、paths、provides。",
                context=context, task_ids=(task_id,), index=index, fields=sorted(deliverable),
            ))
        deliverable_id = _identity(deliverable.get("id"))
        kind = _identity(deliverable.get("kind"))
        target_id = _identity(deliverable.get("target_id"))
        if deliverable_id is None or kind is None or target_id is None:
            issues.append(_issue(
                "CANDIDATE_DELIVERABLE_IDENTITY_INVALID",
                "deliverable 的 id、kind、target_id 必须是精确非空字符串。",
                context=context, task_ids=(task_id,), index=index,
            ))
        if deliverable_id is not None:
            if deliverable_id in seen_ids:
                issues.append(_issue(
                    "CANDIDATE_DELIVERABLE_ID_DUPLICATE", "同一 Task 不得重复 deliverable ID。",
                    context=context, task_ids=(task_id,), deliverable_id=deliverable_id,
                ))
            seen_ids.add(deliverable_id)
        if kind is not None and kind not in DELIVERABLE_KINDS:
            issues.append(_issue(
                "CANDIDATE_DELIVERABLE_KIND_INVALID", f"deliverable kind {kind} 不在平台允许列表中。",
                context=context, task_ids=(task_id,), kind=kind,
            ))
        if (
            kind in _FRONTEND_KINDS and owner != "frontend"
            or kind in _BACKEND_KINDS and owner != "backend"
        ):
            issues.append(_issue(
                "CANDIDATE_DELIVERABLE_OWNER_MISMATCH", "deliverable kind 与 Task owner 不匹配。",
                context=context, task_ids=(task_id,), kind=kind, owner=owner,
            ))
        paths, path_issues = _path_array_issues(
            deliverable, "paths", context=context, issue_task_id=task_id,
        )
        provides, provides_issues = _string_array_issues(
            deliverable, "provides", context=context, issue_task_id=task_id,
        )
        issues.extend(path_issues)
        issues.extend(provides_issues)
        outside = sorted(set(paths) - scope_paths)
        if outside:
            issues.append(_issue(
                "CANDIDATE_DELIVERABLE_PATH_OUTSIDE_SCOPE",
                "deliverable.paths 必须全部属于 Task 的 target_files/change_scope。",
                context=context, task_ids=(task_id,), paths=outside,
            ))
        if kind is not None and target_id is not None:
            records.append((kind, target_id, tuple(provides), task_id))
    return records, issues


def _impact_issues(task: Mapping[str, Any], context: UnitGenerationContext) -> list[ValidationIssue]:
    """校验 impact_scope 的固定结构，避免编译器补摘要或丢非法列表。"""

    task_id = _task_id(task)
    value = task.get("impact_scope")
    if not isinstance(value, Mapping):
        return [_issue(
            "CANDIDATE_IMPACT_SCOPE_INVALID", "impact_scope 必须是对象。",
            context=context, task_ids=(task_id,), actual_type=type(value).__name__,
        )]
    issues: list[ValidationIssue] = []
    expected = {"summary", *_IMPACT_LIST_FIELDS}
    if set(value) != expected:
        issues.append(_issue(
            "CANDIDATE_IMPACT_SCOPE_SCHEMA_INVALID", "impact_scope 字段不完整或包含额外字段。",
            context=context, task_ids=(task_id,), fields=sorted(value),
        ))
    if _identity(value.get("summary")) is None:
        issues.append(_issue(
            "CANDIDATE_IMPACT_SCOPE_SUMMARY_INVALID", "impact_scope.summary 必须是精确非空字符串。",
            context=context, task_ids=(task_id,),
        ))
    for field in _IMPACT_LIST_FIELDS:
        if not isinstance(value.get(field), (list, tuple)) or any(_identity(item) is None for item in value.get(field, ())):
            issues.append(_issue(
                "CANDIDATE_IMPACT_SCOPE_LIST_INVALID", f"impact_scope.{field} 必须是字符串数组。",
                context=context, task_ids=(task_id,), field=field,
            ))
    return issues


def _requirement_issues(
    context: UnitGenerationContext,
    records: Sequence[tuple[str, str, tuple[str, ...], str]],
) -> list[ValidationIssue]:
    """要求 Candidate 精确提供全部且仅提供本轮缺失职责身份。"""

    requirements = {item.requirement_id: item for item in context.generation_requirements}
    provided = {capability for _, _, capabilities, _ in records for capability in capabilities}
    issues: list[ValidationIssue] = []
    for requirement_id in sorted(set(requirements) - provided):
        issues.append(_issue(
            "CANDIDATE_GENERATION_REQUIREMENT_MISSING",
            f"Candidate 未提供冻结 generation requirement {requirement_id}。",
            context=context, requirement_id=requirement_id,
        ))
    for kind, target_id, capabilities, task_id in records:
        matched = [requirements[capability] for capability in capabilities if capability in requirements]
        if not matched:
            issues.append(_issue(
                "CANDIDATE_UNREQUESTED_DELIVERABLE",
                f"Candidate Task {task_id} 输出了不属于本轮 generation requirements 的 deliverable。",
                context=context, task_ids=(task_id,), kind=kind, target_id=target_id,
            ))
            continue
        if not any(item.source_refs.get("kind") == kind for item in matched):
            issues.append(_issue(
                "CANDIDATE_REQUIREMENT_KIND_MISMATCH",
                f"Candidate Task {task_id} 的 deliverable kind 与职责声明不一致。",
                context=context, task_ids=(task_id,), kind=kind,
            ))
        target_field = (
            "page_id" if kind == "frontend.page"
            else "endpoint_id" if kind in {
                "frontend.api_module", "frontend.static_data_module", "backend.endpoint_controller",
            }
            else "target_id" if kind == "frontend.shared_capability"
            else "data_source_type" if kind == "backend.bootstrap"
            else "entity_id"
        )
        expected_targets = {
            str(item.source_refs[target_field])
            for item in matched
            if _identity(item.source_refs.get(target_field)) is not None
        }
        if expected_targets and target_id not in expected_targets:
            issues.append(_issue(
                "CANDIDATE_REQUIREMENT_TARGET_MISMATCH",
                f"Candidate Task {task_id} 的 deliverable target_id 与冻结职责目标不一致。",
                context=context, task_ids=(task_id,), target_id=target_id,
                expected_targets=sorted(expected_targets),
            ))
    return issues


def validate_candidate_task_rules(
    context: UnitGenerationContext,
    candidate_tasks: Sequence[Mapping[str, Any]],
) -> tuple[list[ValidationIssue], list[tuple[str, str, tuple[str, ...], str]]]:
    """汇总不含依赖图与 retained owner 的全部 Candidate Task 规则。"""

    issues: list[ValidationIssue] = []
    all_records: list[tuple[str, str, tuple[str, ...], str]] = []
    task_ids: list[str] = []
    deliverable_owners: dict[str, list[str]] = {}
    for task in candidate_tasks:
        task_id = _task_id(task)
        if task_id:
            task_ids.append(task_id)
        deliverables = task.get("deliverables")
        if isinstance(deliverables, (list, tuple)):
            for deliverable in deliverables:
                if isinstance(deliverable, Mapping) and (deliverable_id := _identity(deliverable.get("id"))):
                    deliverable_owners.setdefault(deliverable_id, []).append(task_id)
        issues.extend(_schema_issues(task, context))
        issues.extend(_identity_issues(task, context))
        _, dependency_issues = _string_array_issues(
            task, "dependencies", context=context, required_non_empty=False,
        )
        issues.extend(dependency_issues)
        scope_paths, scope_issues = _scope_issues(task, context)
        issues.extend(scope_issues)
        records, deliverable_issues = _deliverable_issues(task, context, scope_paths)
        all_records.extend(records)
        issues.extend(deliverable_issues)
        issues.extend(_impact_issues(task, context))
    duplicate_ids = sorted({task_id for task_id in task_ids if task_ids.count(task_id) > 1})
    for task_id in duplicate_ids:
        issues.append(_issue(
            "CANDIDATE_TASK_ID_DUPLICATE", f"Candidate 含重复 Task ID {task_id}。",
            context=context, task_ids=(task_id,),
        ))
    for deliverable_id, owner_task_ids in sorted(deliverable_owners.items()):
        if len(owner_task_ids) > 1:
            issues.append(_issue(
                "CANDIDATE_DELIVERABLE_ID_DUPLICATE",
                f"Candidate 含跨 Task 重复 deliverable ID {deliverable_id}。",
                context=context, task_ids=owner_task_ids, deliverable_id=deliverable_id,
            ))
    issues.extend(_requirement_issues(context, all_records))
    return issues, all_records
