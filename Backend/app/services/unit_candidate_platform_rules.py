"""Unit Local Validation 的冻结输入、平台边界与 retained owner 规则。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fnmatch import fnmatchcase
from typing import Any

from pydantic import ValidationError

from app.services.build_task_reuse_contracts import RetainedEndpointOwner, ReuseFacts
from app.services.business_acceptance import DELIVERABLE_KINDS, normalize_repo_path
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import UnitGenerationContext


_KIND_OWNER = {"page": "frontend", "frontend": "frontend", "backend": "backend", "database": "database"}
_NON_MODEL_UNITS = {"application:root", "app:integration", "frontend:shell", "frontend:auth-guard", "frontend:route-registry"}
_BUILTIN_STRONG_RULES = {
    "exact_unit_owner", "exact_file_scope", "no_platform_owned_fields",
    "no_platform_owned_tasks", "no_repair_or_verification_tasks", "status_pending",
}
_CUSTOM_STRONG_RULE_FIELDS = {
    "forbidden_fields", "forbidden_paths", "forbidden_task_types",
    "required_status", "allowed_deliverable_kinds",
}
_ENDPOINT_DELIVERABLE_KINDS = {"frontend.api_module", "frontend.static_data_module"}
_PLATFORM_MANAGED_PATHS = {
    "frontend/src/constants/menus.ts", "src/constants/menus.ts",
    "frontend/src/constants/routes.ts", "src/constants/routes.ts",
    "frontend/src/constants/routes.tsx", "src/constants/routes.tsx",
    "frontend/src/routes/index.tsx", "src/routes/index.tsx",
    "frontend/src/utils/route.tsx", "src/utils/route.tsx",
    "frontend/src/constants/resources.ts",
}


def _identity(value: Any) -> str | None:
    """只接受不需要修剪的非空字符串。"""

    return value if isinstance(value, str) and value and value == value.strip() else None


def _fatal_issue(
    code: str, message: str, *, category: str, unit_id: str | None = None,
    task_ids: Sequence[str] = (), **details: Any,
) -> ValidationIssue:
    """构造不进入 Local Retry 的平台或输入问题。"""

    return ValidationIssue(
        code=code, level="pre_generation", category=category,
        unit_ids=(unit_id,) if unit_id else (), task_ids=tuple(task_ids),
        retry_unit_ids=(), retryable=False, message=message, details=details,
    )


def _local_issue(
    code: str, message: str, *, context: UnitGenerationContext,
    task_ids: Sequence[str] = (), **details: Any,
) -> ValidationIssue:
    """构造归因到当前 Unit Candidate 的可重试内容问题。"""

    return ValidationIssue(
        code=code, level="unit", category="generation", unit_ids=(context.unit_id,),
        task_ids=tuple(dict.fromkeys(item for item in task_ids if item)),
        retry_unit_ids=(context.unit_id,), retryable=True, message=message, details=details,
    )


def _validation_error_details(exc: ValidationError) -> list[dict[str, Any]]:
    """把 Pydantic 错误投影为可序列化诊断，避免泄漏异常对象。"""

    return [
        {
            "location": [str(item) for item in error.get("loc", ())],
            "type": str(error.get("type") or "validation_error"),
            "message": str(error.get("msg") or "invalid input"),
        }
        for error in exc.errors(include_url=False, include_context=False, include_input=False)
    ]


def _context_unit_id(value: Any) -> str | None:
    """尽可能从非法 Context 中保留合法 Unit 归因，不猜测或转换身份。"""

    return _identity(value.get("unit_id")) if isinstance(value, Mapping) else None


def _string_sequence(value: Any) -> tuple[str, ...] | None:
    """严格读取无重复的非空字符串序列。"""

    if not isinstance(value, (list, tuple)):
        return None
    result = tuple(item for item in value if _identity(item) is not None)
    if len(result) != len(value) or len(set(result)) != len(result):
        return None
    return result


def _path(value: Any) -> str | None:
    """验证平台约束中的精确仓库相对路径或显式 glob。"""

    item = _identity(value)
    if (
        item is None or "\\" in item or item.startswith(("/", "./"))
        or (len(item) >= 3 and item[1:3] == ":/") or ".." in item.split("/")
    ):
        return None
    return item if normalize_repo_path(item) == item else None


def _context_contract_issues(context: UnitGenerationContext) -> list[ValidationIssue]:
    """检查 Pydantic 外的跨字段冻结 Context 契约。"""

    issues: list[ValidationIssue] = []
    unit_id = context.unit_id
    expected_owner = _KIND_OWNER.get(context.unit_kind)
    actual_owner = context.constraints.get("owner")
    if unit_id in _NON_MODEL_UNITS or expected_owner is None:
        issues.append(_fatal_issue(
            "UNIT_LOCAL_VALIDATION_NOT_MODEL_UNIT",
            f"Unit {unit_id} 不是可进入模型 Local Validation 的 Unit。",
            category="platform", unit_id=unit_id,
        ))
    if _identity(actual_owner) is None or actual_owner != expected_owner:
        issues.append(_fatal_issue(
            "UNIT_VALIDATION_OWNER_CONSTRAINT_INVALID",
            "冻结 Context 必须声明与 unit_kind 一致的唯一 owner。",
            category="input", unit_id=unit_id,
            expected_owner=expected_owner, actual_owner=actual_owner,
        ))
    requirement_ids = [item.requirement_id for item in context.generation_requirements]
    if not requirement_ids:
        issues.append(_fatal_issue(
            "UNIT_VALIDATION_REQUIREMENTS_EMPTY",
            "没有 generation requirements 的 Unit 不得进入 Candidate Local Validation。",
            category="platform", unit_id=unit_id,
        ))
    elif len(set(requirement_ids)) != len(requirement_ids):
        issues.append(_fatal_issue(
            "UNIT_VALIDATION_REQUIREMENTS_DUPLICATE", "冻结 Context 含重复 generation requirement。",
            category="input", unit_id=unit_id, requirement_ids=requirement_ids,
        ))
    for requirement in context.generation_requirements:
        refs = requirement.source_refs
        if refs.get("capability_id") != requirement.requirement_id or refs.get("kind") not in DELIVERABLE_KINDS:
            issues.append(_fatal_issue(
                "UNIT_VALIDATION_REQUIREMENT_CONTRACT_INVALID",
                f"generation requirement {requirement.requirement_id} 缺少精确 capability_id 或 deliverable kind。",
                category="input", unit_id=unit_id, requirement_id=requirement.requirement_id,
            ))
    dependency_units = _string_sequence(context.dependency_context.get("dependency_unit_ids", ()))
    summaries = context.dependency_context.get("retained_task_summaries", ())
    if dependency_units is None or not isinstance(summaries, (list, tuple)):
        issues.append(_fatal_issue(
            "UNIT_VALIDATION_DEPENDENCY_CONTEXT_INVALID", "冻结 dependency_context 的 Unit IDs 或 retained 摘要非法。",
            category="input", unit_id=unit_id,
        ))
    elif any(
        not isinstance(summary, Mapping) or _identity(summary.get("id")) is None
        or (summary.get("unit_id") is not None and _identity(summary.get("unit_id")) is None)
        for summary in summaries
    ):
        issues.append(_fatal_issue(
            "UNIT_VALIDATION_RETAINED_SUMMARY_INVALID", "retained_task_summaries 必须携带精确 Task 身份。",
            category="input", unit_id=unit_id,
        ))
    elif len({str(summary["id"]) for summary in summaries}) != len(summaries):
        issues.append(_fatal_issue(
            "UNIT_VALIDATION_RETAINED_SUMMARY_DUPLICATE", "retained_task_summaries 不得含重复 Task ID。",
            category="input", unit_id=unit_id,
        ))
    managed_files = _string_sequence(context.constraints.get("managed_files", ()))
    if managed_files is None or any(_path(path) is None for path in managed_files):
        issues.append(_fatal_issue(
            "UNIT_VALIDATION_MANAGED_FILES_INVALID", "constraints.managed_files 必须是精确、安全且不重复的路径数组。",
            category="input", unit_id=unit_id,
        ))
    issues.extend(_strong_rule_contract_issues(context))
    return issues


def _strong_rule_contract_issues(context: UnitGenerationContext) -> list[ValidationIssue]:
    """校验强规则只能使用 Validator 明确支持的结构，未知规则直接平台失败。"""

    rules = context.constraints.get("strong_rules", ())
    if isinstance(rules, (list, tuple)):
        values = _string_sequence(rules)
        if values is not None and set(values) <= _BUILTIN_STRONG_RULES:
            return []
    elif isinstance(rules, Mapping):
        if set(rules) <= _CUSTOM_STRONG_RULE_FIELDS:
            arrays = {
                field: _string_sequence(rules.get(field, ()))
                for field in ("forbidden_fields", "forbidden_paths", "forbidden_task_types", "allowed_deliverable_kinds")
            }
            arrays_valid = all(value is not None for value in arrays.values())
            forbidden_paths = arrays["forbidden_paths"] or ()
            allowed_kinds = arrays["allowed_deliverable_kinds"] or ()
            paths_valid = all(_path(item) is not None for item in forbidden_paths)
            status = rules.get("required_status", "pending")
            if arrays_valid and paths_valid and status == "pending" and set(allowed_kinds) <= set(DELIVERABLE_KINDS):
                return []
    return [_fatal_issue(
        "UNIT_VALIDATION_STRONG_RULE_CONTRACT_INVALID",
        "constraints.strong_rules 含 Validator 无法确定执行的未知或非法规则。",
        category="platform", unit_id=context.unit_id,
    )]


def _slice_owners(value: Any) -> tuple[RetainedEndpointOwner, ...]:
    """严格解析 unit-local retained owner slice。"""

    if not isinstance(value, (list, tuple)):
        raise ValueError("retained_endpoint_owners 必须是数组")
    return tuple(RetainedEndpointOwner.model_validate(item) for item in value)


def _slice_issues(value: Any) -> tuple[ValidationIssue, ...]:
    """严格解析 unit-local reuse slice 中的前置 Issues。"""

    if not isinstance(value, (list, tuple)):
        raise ValueError("issues 必须是数组")
    return tuple(ValidationIssue.model_validate(item) for item in value)


def _merge_retained_owners(
    context: UnitGenerationContext,
    *groups: Sequence[RetainedEndpointOwner],
) -> tuple[tuple[RetainedEndpointOwner, ...], list[ValidationIssue]]:
    """合并同一事实的重复投影，并拒绝 retained baseline 自身的多 owner 冲突。"""

    unique = {
        (owner.api_contract_id, owner.endpoint_id, owner.owner_task_id, owner.owner_unit_id): owner
        for group in groups for owner in group
    }
    owners = tuple(unique.values())
    by_endpoint: dict[tuple[str, str], list[RetainedEndpointOwner]] = {}
    for owner in owners:
        by_endpoint.setdefault((owner.api_contract_id, owner.endpoint_id), []).append(owner)
    conflicts = [
        (key, records) for key, records in sorted(by_endpoint.items())
        if len(records) > 1
    ]
    if not conflicts:
        return owners, []
    return (), [_fatal_issue(
        "UNIT_VALIDATION_RETAINED_OWNER_BASELINE_CONFLICT",
        "retained owner 输入自身存在 Endpoint 多 owner 冲突，不能归因给当前模型 Candidate。",
        category="platform", unit_id=context.unit_id,
        conflicts=[{
            "api_contract_id": key[0], "endpoint_id": key[1],
            "owner_task_ids": sorted(owner.owner_task_id for owner in records),
            "owner_unit_ids": sorted(owner.owner_unit_id for owner in records),
        } for key, records in conflicts],
    )]


def validate_local_inputs(
    context: UnitGenerationContext | Mapping[str, Any],
    reuse_facts: ReuseFacts | Mapping[str, Any] | None,
) -> tuple[UnitGenerationContext | None, tuple[RetainedEndpointOwner, ...], list[ValidationIssue]]:
    """验证冻结 Context 与 ReuseFacts/slice，并在异常时只返回 fatal Issues。"""

    try:
        frozen_context = UnitGenerationContext.model_validate(context)
    except ValidationError as exc:
        unit_id = _context_unit_id(context)
        return None, (), [_fatal_issue(
            "UNIT_VALIDATION_CONTEXT_INVALID", "Unit Local Validation 收到非法冻结 Context。",
            category="input", unit_id=unit_id, errors=_validation_error_details(exc),
        )]
    context_issues = _context_contract_issues(frozen_context)
    if context_issues:
        return frozen_context, (), context_issues
    contextual_owners_value = frozen_context.dependency_context.get(
        "retained_owner_constraints",
        frozen_context.dependency_context.get("retained_endpoint_owners", ()),
    )
    try:
        contextual_owners = _slice_owners(contextual_owners_value)
    except (ValidationError, ValueError, TypeError) as exc:
        return frozen_context, (), [_fatal_issue(
            "UNIT_VALIDATION_RETAINED_OWNER_CONTEXT_INVALID",
            "冻结 dependency_context 的 retained owner constraints 非法。",
            category="input", unit_id=frozen_context.unit_id,
            error_type=type(exc).__name__, error_message=str(exc),
        )]
    if reuse_facts is None:
        owners: tuple[RetainedEndpointOwner, ...] = ()
    else:
        try:
            if isinstance(reuse_facts, ReuseFacts) or (
                isinstance(reuse_facts, Mapping)
                and "retained_task_ids_by_unit" in reuse_facts
            ):
                facts = ReuseFacts.model_validate(reuse_facts)
                if facts.issues:
                    if any(issue.retryable for issue in facts.issues):
                        return frozen_context, (), [_fatal_issue(
                            "UNIT_VALIDATION_REUSE_ISSUE_CONTRACT_INVALID",
                            "ReuseFacts 的前置问题不得携带 Local Retry 目标。",
                            category="platform", unit_id=frozen_context.unit_id,
                        )]
                    return frozen_context, (), list(facts.issues)
                owners = facts.retained_endpoint_owners
            elif isinstance(reuse_facts, Mapping):
                unsupported = set(reuse_facts) - {"retained_endpoint_owners", "issues"}
                if unsupported:
                    raise ValueError(f"unsupported slice fields: {sorted(unsupported)}")
                slice_issues = _slice_issues(reuse_facts.get("issues", ()))
                if slice_issues:
                    if any(issue.retryable for issue in slice_issues):
                        return frozen_context, (), [_fatal_issue(
                            "UNIT_VALIDATION_REUSE_ISSUE_CONTRACT_INVALID",
                            "unit-local reuse slice 的前置问题不得携带 Local Retry 目标。",
                            category="platform", unit_id=frozen_context.unit_id,
                        )]
                    return frozen_context, (), list(slice_issues)
                owners = _slice_owners(reuse_facts.get("retained_endpoint_owners", ()))
            else:
                raise ValueError("reuse_facts 必须是 ReuseFacts、unit-local slice 或 None")
        except (ValidationError, ValueError, TypeError) as exc:
            return frozen_context, (), [_fatal_issue(
                "UNIT_VALIDATION_REUSE_FACTS_INVALID", "Unit Local Validation 收到非法 ReuseFacts/slice。",
                category="platform", unit_id=frozen_context.unit_id,
                error_type=type(exc).__name__, error_message=str(exc),
            )]
    owners, owner_issues = _merge_retained_owners(
        frozen_context,
        contextual_owners,
        owners,
    )
    return frozen_context, owners, owner_issues


def _declared_paths(task: Mapping[str, Any]) -> set[str]:
    """收集 Candidate Task 明示的全部写入及授权路径，不修剪非法值。"""

    paths: set[str] = set()
    for field in ("target_files", "allowed_paths"):
        values = task.get(field)
        if isinstance(values, (list, tuple)):
            paths.update(item for item in values if isinstance(item, str))
    changes = task.get("change_scope")
    change_items = changes if isinstance(changes, (list, tuple)) else ()
    paths.update(
        item.get("path") for item in change_items
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    )
    deliverables = task.get("deliverables")
    for deliverable in deliverables if isinstance(deliverables, (list, tuple)) else ():
        if isinstance(deliverable, Mapping):
            deliverable_paths = deliverable.get("paths")
            if isinstance(deliverable_paths, (list, tuple)):
                paths.update(item for item in deliverable_paths if isinstance(item, str))
    return paths


def _matches_constraint(path: str, constraints: Sequence[str]) -> bool:
    """判断路径是否命中平台管理或强规则的精确路径/glob。"""

    return any(
        path == pattern or fnmatchcase(path, pattern) or fnmatchcase(pattern, path)
        for pattern in constraints
    )


def _is_platform_managed_path(path: str) -> bool:
    """识别不依赖 Context 的平台固定管理文件。"""

    normalized = normalize_repo_path(path)
    return (
        normalized in _PLATFORM_MANAGED_PATHS
        or any(fnmatchcase(managed, normalized) for managed in _PLATFORM_MANAGED_PATHS)
        or normalized.lower().endswith("/authconstants.java")
        or normalized.lower() == "authconstants.java"
    )


def _custom_strong_rule_issues(
    context: UnitGenerationContext, task: Mapping[str, Any], records: Sequence[tuple[str, str, tuple[str, ...], str]],
) -> list[ValidationIssue]:
    """执行 Context 中已通过契约校验的自定义强规则。"""

    rules = context.constraints.get("strong_rules", ())
    if not isinstance(rules, Mapping):
        return []
    task_id = _identity(task.get("id")) or ""
    issues: list[ValidationIssue] = []
    forbidden_fields = set(rules.get("forbidden_fields", ()))
    for field in sorted(set(task) & forbidden_fields):
        issues.append(_local_issue(
            "CANDIDATE_STRONG_RULE_VIOLATION", f"Candidate Task {task_id} 命中 forbidden_fields 强规则。",
            context=context, task_ids=(task_id,), rule="forbidden_fields", field=field,
        ))
    task_type = _identity(task.get("task_type"))
    if task_type is not None and task_type in set(rules.get("forbidden_task_types", ())):
        issues.append(_local_issue(
            "CANDIDATE_STRONG_RULE_VIOLATION", f"Candidate Task {task_id} 使用了禁止的 task_type。",
            context=context, task_ids=(task_id,), rule="forbidden_task_types", task_type=task.get("task_type"),
        ))
    forbidden_paths = tuple(rules.get("forbidden_paths", ()))
    matched_paths = sorted(path for path in _declared_paths(task) if _matches_constraint(path, forbidden_paths))
    if matched_paths:
        issues.append(_local_issue(
            "CANDIDATE_STRONG_RULE_VIOLATION", f"Candidate Task {task_id} 命中 forbidden_paths 强规则。",
            context=context, task_ids=(task_id,), rule="forbidden_paths", paths=matched_paths,
        ))
    allowed_kinds = set(rules.get("allowed_deliverable_kinds", ()))
    if allowed_kinds:
        invalid = sorted({kind for kind, _, _, record_task_id in records if record_task_id == task_id and kind not in allowed_kinds})
        if invalid:
            issues.append(_local_issue(
                "CANDIDATE_STRONG_RULE_VIOLATION", f"Candidate Task {task_id} 输出了强规则未允许的 deliverable kind。",
                context=context, task_ids=(task_id,), rule="allowed_deliverable_kinds", kinds=invalid,
            ))
    return issues


def _candidate_endpoint_owners(
    context: UnitGenerationContext,
    records: Sequence[tuple[str, str, tuple[str, ...], str]],
    retained: Sequence[RetainedEndpointOwner],
) -> dict[tuple[str, str], set[str]]:
    """从职责 capability 或唯一 retained endpoint_id 提取 Candidate Endpoint owner。"""

    requirements = {item.requirement_id: item for item in context.generation_requirements}
    retained_by_endpoint_id: dict[str, set[tuple[str, str]]] = {}
    for owner in retained:
        retained_by_endpoint_id.setdefault(owner.endpoint_id, set()).add((owner.api_contract_id, owner.endpoint_id))
    result: dict[tuple[str, str], set[str]] = {}
    for kind, target_id, capabilities, task_id in records:
        if kind not in _ENDPOINT_DELIVERABLE_KINDS:
            continue
        keys = {
            (str(refs.get("api_contract_id")), str(refs.get("endpoint_id")))
            for capability in capabilities if capability in requirements
            for refs in (requirements[capability].source_refs,)
            if _identity(refs.get("api_contract_id")) and _identity(refs.get("endpoint_id"))
        }
        if not keys:
            retained_keys = retained_by_endpoint_id.get(target_id, set())
            if len(retained_keys) == 1:
                keys = set(retained_keys)
        for key in keys:
            result.setdefault(key, set()).add(task_id)
    return result


def validate_platform_boundaries(
    context: UnitGenerationContext,
    candidate_tasks: Sequence[Mapping[str, Any]],
    records: Sequence[tuple[str, str, tuple[str, ...], str]],
    retained_owners: Sequence[RetainedEndpointOwner],
) -> list[ValidationIssue]:
    """校验 managed files、自定义强规则和当前 Unit 可判断的 owner 冲突。"""

    issues: list[ValidationIssue] = []
    managed_files = tuple(context.constraints.get("managed_files", ()))
    for task in candidate_tasks:
        task_id = _identity(task.get("id")) or ""
        conflicts = sorted(
            path for path in _declared_paths(task)
            if _matches_constraint(path, managed_files) or _is_platform_managed_path(path)
        )
        if conflicts:
            issues.append(_local_issue(
                "CANDIDATE_MANAGED_FILE_CONFLICT",
                f"Candidate Task {task_id or '<unknown>'} 不得写入平台 managed files。",
                context=context, task_ids=(task_id,), paths=conflicts,
            ))
        issues.extend(_custom_strong_rule_issues(context, task, records))
    candidate_owners = _candidate_endpoint_owners(context, records, retained_owners)
    retained_by_key: dict[tuple[str, str], list[RetainedEndpointOwner]] = {}
    for owner in retained_owners:
        retained_by_key.setdefault((owner.api_contract_id, owner.endpoint_id), []).append(owner)
    for (contract_id, endpoint_id), task_ids in sorted(candidate_owners.items()):
        if len(task_ids) > 1:
            issues.append(_local_issue(
                "CANDIDATE_ENDPOINT_OWNER_CONFLICT",
                f"Candidate 内 Endpoint {contract_id}/{endpoint_id} 存在多个实现 owner。",
                context=context, task_ids=sorted(task_ids),
                api_contract_id=contract_id, endpoint_id=endpoint_id,
            ))
        for retained in retained_by_key.get((contract_id, endpoint_id), ()):
            issues.append(_local_issue(
                "CANDIDATE_RETAINED_ENDPOINT_OWNER_CONFLICT",
                f"Candidate 不得重新实现 retained Endpoint {contract_id}/{endpoint_id}。",
                context=context, task_ids=(*sorted(task_ids), retained.owner_task_id),
                api_contract_id=contract_id, endpoint_id=endpoint_id,
                retained_unit_id=retained.owner_unit_id,
            ))
    return issues
