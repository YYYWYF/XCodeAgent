"""单 Unit Candidate 的局部校验规则；不编译跨 Unit 依赖或完整 DAG。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.planning_issues import ValidationIssue
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.unit_candidate_platform_rules import (
    validate_local_inputs,
    validate_platform_boundaries,
)
from app.services.unit_candidate_task_rules import validate_candidate_task_rules
from app.services.unit_generation_contracts import UnitGenerationContext


def _identity(value: Any) -> str | None:
    """只接受无首尾空白的非空字符串身份，不修剪或转换模型值。"""

    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def _dependency_issue(
    code: str,
    message: str,
    *,
    current_unit_id: str,
    task_ids: Sequence[str],
    involved_unit_ids: Sequence[str] = (),
    **details: Any,
) -> ValidationIssue:
    """构造归因到当前 Candidate 的可重试 Unit generation 问题。"""

    unit_ids = tuple(dict.fromkeys((current_unit_id, *involved_unit_ids)))
    return ValidationIssue(
        code=code,
        level="unit",
        category="generation",
        unit_ids=unit_ids,
        task_ids=tuple(dict.fromkeys(task_ids)),
        retry_unit_ids=(current_unit_id,),
        retryable=True,
        message=message,
        details=details,
    )


def _retained_dependency_index(
    dependency_context: Mapping[str, Any],
    current_unit_id: str,
) -> tuple[set[str], dict[str, str]]:
    """区分当前 Unit 可引用的 retained IDs 与显式属于其他 Unit 的 IDs。

    UnitGenerationContext 本身就是按 Unit 冻结的切片，因此没有 ``unit_id`` 的旧摘要
    仍属于当前 Unit；一旦摘要显式声明 ``unit_id``，则必须与当前 Unit 精确相等。
    无合法 ID 的摘要不能进入 allowlist，完整 Context 输入校验留给后续 Local Validator。
    """

    allowed: set[str] = set()
    cross_unit: dict[str, str] = {}
    summaries = dependency_context.get("retained_task_summaries", ())
    if not isinstance(summaries, (list, tuple)):
        return allowed, cross_unit
    for summary in summaries:
        if not isinstance(summary, Mapping):
            continue
        task_id = _identity(summary.get("id"))
        if task_id is None:
            continue
        declared_unit = summary.get("unit_id")
        if declared_unit is None:
            allowed.add(task_id)
            continue
        retained_unit_id = _identity(declared_unit)
        if retained_unit_id == current_unit_id:
            allowed.add(task_id)
        elif retained_unit_id is not None:
            cross_unit[task_id] = retained_unit_id
    return allowed, cross_unit


def _known_unit_ids(
    dependency_context: Mapping[str, Any],
    current_unit_id: str,
) -> set[str]:
    """收集 Context 明示的 Unit IDs，用于拒绝把 Unit 身份当作 Task 依赖。"""

    result = {current_unit_id}
    values = dependency_context.get("dependency_unit_ids", ())
    if isinstance(values, (list, tuple)):
        result.update(value for item in values if (value := _identity(item)) is not None)
    summaries = dependency_context.get("retained_task_summaries", ())
    if isinstance(summaries, (list, tuple)):
        for summary in summaries:
            if isinstance(summary, Mapping):
                unit_id = _identity(summary.get("unit_id"))
                if unit_id is not None:
                    result.add(unit_id)
    return result


def _candidate_cycles(graph: Mapping[str, set[str]]) -> list[tuple[str, ...]]:
    """使用 Tarjan SCC 精确返回当前 Candidate 内的自环和多 Task 环。"""

    index = 0
    indices: dict[str, int] = {}
    low_links: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[tuple[str, ...]] = []

    def visit(task_id: str) -> None:
        """深度遍历一个 Candidate Task，并在 SCC 根节点输出精确环成员。"""

        nonlocal index
        indices[task_id] = index
        low_links[task_id] = index
        index += 1
        stack.append(task_id)
        on_stack.add(task_id)

        for dependency in sorted(graph[task_id]):
            if dependency not in indices:
                visit(dependency)
                low_links[task_id] = min(low_links[task_id], low_links[dependency])
            elif dependency in on_stack:
                low_links[task_id] = min(low_links[task_id], indices[dependency])

        if low_links[task_id] != indices[task_id]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == task_id:
                break
        members = tuple(sorted(component))
        if len(members) > 1 or task_id in graph[task_id]:
            cycles.append(members)

    for task_id in sorted(graph):
        if task_id not in indices:
            visit(task_id)
    return sorted(cycles)


def validate_unit_candidate_dependencies(
    candidate_tasks: Sequence[Mapping[str, Any]],
    dependency_context: Mapping[str, Any],
    current_unit_id: str,
) -> list[ValidationIssue]:
    """校验 Candidate dependency allowlist 与 Candidate 内 same-unit cycles。

    允许目标仅为当前 Candidate Task IDs 或 Context 明示的当前 Unit retained IDs。
    该函数不修改任务、不推导跨 Unit dependency，也不检查完整 DAG 的 global cycle。
    输入应先通过 T3.1 Raw Candidate Parser；其他 Task schema 规则属于后续 T3.5。
    """

    candidate_ids = {
        task_id
        for task in candidate_tasks
        if (task_id := _identity(task.get("id"))) is not None
    }
    retained_ids, cross_unit_ids = _retained_dependency_index(
        dependency_context,
        current_unit_id,
    )
    unit_ids = _known_unit_ids(dependency_context, current_unit_id)
    graph = {task_id: set() for task_id in candidate_ids}
    issues: list[ValidationIssue] = []
    reported: set[tuple[str, str, str]] = set()

    for task in candidate_tasks:
        task_id = _identity(task.get("id"))
        if task_id is None:
            continue
        dependencies = task.get("dependencies", ())
        if dependencies is None:
            dependencies = ()
        if not isinstance(dependencies, (list, tuple)):
            issues.append(
                _dependency_issue(
                    "CANDIDATE_DEPENDENCIES_TYPE_INVALID",
                    f"Candidate Task {task_id} 的 dependencies 必须是数组。",
                    current_unit_id=current_unit_id,
                    task_ids=(task_id,),
                    actual_type=type(dependencies).__name__,
                )
            )
            continue

        for dependency_index, raw_dependency in enumerate(dependencies):
            dependency = _identity(raw_dependency)
            if dependency is None:
                marker = (task_id, repr(raw_dependency), "invalid")
                if marker not in reported:
                    reported.add(marker)
                    issues.append(
                        _dependency_issue(
                            "CANDIDATE_DEPENDENCY_TARGET_INVALID",
                            f"Candidate Task {task_id} 的 dependency target 必须是有效 Task ID。",
                            current_unit_id=current_unit_id,
                            task_ids=(task_id,),
                            dependency_index=dependency_index,
                            actual_type=type(raw_dependency).__name__,
                        )
                    )
                continue
            if dependency in candidate_ids:
                graph[task_id].add(dependency)
                continue
            if dependency in retained_ids:
                continue
            if dependency in cross_unit_ids:
                issue_code = "CANDIDATE_DEPENDENCY_CROSS_UNIT"
                marker = (task_id, dependency, issue_code)
                if marker not in reported:
                    reported.add(marker)
                    target_unit_id = cross_unit_ids[dependency]
                    issues.append(
                        _dependency_issue(
                            issue_code,
                            f"Candidate Task {task_id} 不得依赖其他 Unit 的 Task {dependency}。",
                            current_unit_id=current_unit_id,
                            task_ids=(task_id, dependency),
                            involved_unit_ids=(target_unit_id,),
                            dependency_id=dependency,
                            dependency_unit_id=target_unit_id,
                        )
                    )
                continue
            issue_code = (
                "CANDIDATE_DEPENDENCY_UNIT_TARGET"
                if dependency in unit_ids
                else "CANDIDATE_DEPENDENCY_UNKNOWN"
            )
            marker = (task_id, dependency, issue_code)
            if marker in reported:
                continue
            reported.add(marker)
            message = (
                f"Candidate Task {task_id} 不得把 Unit ID {dependency} 作为 Task dependency。"
                if issue_code == "CANDIDATE_DEPENDENCY_UNIT_TARGET"
                else f"Candidate Task {task_id} 引用了未授权的未知 Task {dependency}。"
            )
            issues.append(
                _dependency_issue(
                    issue_code,
                    message,
                    current_unit_id=current_unit_id,
                    task_ids=(task_id,),
                    involved_unit_ids=(dependency,) if dependency in unit_ids else (),
                    dependency_id=dependency,
                )
            )

    for cycle in _candidate_cycles(graph):
        direct_self_cycle = len(cycle) == 1
        issues.append(
            _dependency_issue(
                (
                    "CANDIDATE_DEPENDENCY_SELF_CYCLE"
                    if direct_self_cycle
                    else "CANDIDATE_DEPENDENCY_CYCLE"
                ),
                (
                    f"Candidate Task {cycle[0]} 不得直接依赖自身。"
                    if direct_self_cycle
                    else "Candidate 内存在 same-unit dependency cycle："
                    + " -> ".join(cycle)
                    + "。"
                ),
                current_unit_id=current_unit_id,
                task_ids=cycle,
                cycle_task_ids=cycle,
            )
        )
    return issues


def validate_unit_candidate(
    context: UnitGenerationContext | Mapping[str, Any],
    candidate_tasks: Sequence[Mapping[str, Any]],
    reuse_facts: ReuseFacts | Mapping[str, Any] | None = None,
) -> list[ValidationIssue]:
    """校验一个 parsed Unit Candidate，返回 Local Retry 的唯一内容 Issues。

    冻结 Context、ReuseFacts 或 parsed-Candidate 调用契约错误属于 input/platform
    fatal，不携带 retry target；只有模型可修复的当前 Candidate 内容错误才归因并
    重试当前 Unit。本函数不修改 Candidate、不执行 retry、不 Assembly 或全局校验。
    """

    frozen_context, retained_owners, fatal_issues = validate_local_inputs(
        context,
        reuse_facts,
    )
    if fatal_issues:
        return fatal_issues
    if frozen_context is None:  # pragma: no cover - validate_local_inputs 的契约保护
        raise RuntimeError("validated UnitGenerationContext unexpectedly missing")
    if isinstance(candidate_tasks, (str, bytes)) or not isinstance(candidate_tasks, Sequence):
        return [ValidationIssue(
            code="UNIT_VALIDATION_PARSED_CANDIDATE_INVALID",
            level="pre_generation",
            category="platform",
            unit_ids=(frozen_context.unit_id,),
            task_ids=(),
            retry_unit_ids=(),
            retryable=False,
            message="Local Validator 必须接收 T3.1 Parser 输出的 Task 数组。",
            details={"actual_type": type(candidate_tasks).__name__},
        )]
    tasks = tuple(candidate_tasks)
    if any(not isinstance(task, Mapping) for task in tasks):
        return [ValidationIssue(
            code="UNIT_VALIDATION_PARSED_CANDIDATE_INVALID",
            level="pre_generation",
            category="platform",
            unit_ids=(frozen_context.unit_id,),
            task_ids=(),
            retry_unit_ids=(),
            retryable=False,
            message="T3.1 Parser 输出中不得包含非对象 Task。",
            details={
                "invalid_indexes": [
                    index for index, task in enumerate(tasks)
                    if not isinstance(task, Mapping)
                ]
            },
        )]

    task_issues, deliverable_records = validate_candidate_task_rules(
        frozen_context,
        tasks,
    )
    dependency_issues = validate_unit_candidate_dependencies(
        tasks,
        frozen_context.dependency_context,
        frozen_context.unit_id,
    )
    platform_boundary_issues = validate_platform_boundaries(
        frozen_context,
        tasks,
        deliverable_records,
        retained_owners,
    )
    return [*task_issues, *dependency_issues, *platform_boundary_issues]
