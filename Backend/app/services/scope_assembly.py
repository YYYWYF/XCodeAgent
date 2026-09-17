"""将正式历史任务与本轮有效 Unit Candidate 确定性组装为累计 Build DAG。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BeforeValidator,
    PlainSerializer,
    StringConstraints,
    ValidationError,
)

from app.services.build_task_planner import (
    compile_build_task_plan_scope,
    tasks_from_build_task_plan,
)
from app.services.build_unit_compiler import BuildUnitCompilationError
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.planning_frozen import (
    FrozenJsonObject,
    FrozenPlanningModel,
    freeze_json,
    plain_json,
    tuple_input,
)
from app.services.planning_issues import ValidationIssue
from app.services.template_state import validate_template_context
from app.services.unit_generation_contracts import (
    CandidateAttempt,
    GenerationRequirement,
)

_Id = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Ids = Annotated[tuple[_Id, ...], BeforeValidator(tuple_input)]
_TaskOrigins = Annotated[
    Mapping[_Id, Literal["retained", "candidate", "platform"]],
    BeforeValidator(plain_json),
    AfterValidator(freeze_json),
    PlainSerializer(plain_json, return_type=dict[str, str]),
]
_CandidateUnits = Annotated[
    Mapping[_Id, _Id],
    BeforeValidator(plain_json),
    AfterValidator(freeze_json),
    PlainSerializer(plain_json, return_type=dict[str, str]),
]


class ScopeAssemblyResult(FrozenPlanningModel):
    """保存累计 DAG 及其来源索引，供后续 Global Validation 精确归因。"""

    assembled_plan: FrozenJsonObject
    retained_task_ids: _Ids
    candidate_task_ids: _Ids
    platform_task_ids: _Ids
    review_task_ids: _Ids
    reused_task_ids: _Ids
    task_origins: _TaskOrigins
    candidate_unit_by_task_id: _CandidateUnits


class ScopeAssemblyError(ValueError):
    """携带 Assembly 阶段的结构化问题，失败时不返回部分 registry。"""

    def __init__(self, issues: Sequence[ValidationIssue]) -> None:
        """冻结全部问题，供后续 PlanningRun 或 AG-UI 边界原样处理。"""

        self.issues = tuple(ValidationIssue.model_validate(issue) for issue in issues)
        super().__init__("；".join(issue.message for issue in self.issues))


def _issue(
    code: str,
    message: str,
    *,
    unit_ids: Sequence[str] = (),
    task_ids: Sequence[str] = (),
    retry_unit_ids: Sequence[str] = (),
    category: Literal["input", "platform", "generation"] = "platform",
    **details: Any,
) -> ValidationIssue:
    """构造一个 Scope Assembly 问题，不从文本反推来源或重试目标。"""

    retry_units = tuple(dict.fromkeys(retry_unit_ids))
    return ValidationIssue(
        code=code,
        level="global" if category == "generation" else "pre_generation",
        category=category,
        unit_ids=tuple(dict.fromkeys(unit_ids)),
        task_ids=tuple(dict.fromkeys(task_ids)),
        retry_unit_ids=retry_units,
        retryable=bool(retry_units),
        message=message,
        details=details,
    )


def _raise_input(code: str, message: str, **details: Any) -> None:
    """以不可重试输入问题中止组装，避免生成可误用的部分结果。"""

    raise ScopeAssemblyError([_issue(code, message, category="input", **details)])


def _identity(value: Any) -> str | None:
    """只接受无首尾空白的非空字符串身份，不修剪或自动转换。"""

    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def _retained_tasks(
    base_confirmed_plan: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """深复制并读取完整 confirmed registry，拒绝非法身份和基线内重复。"""

    if base_confirmed_plan is None:
        return {}, []
    if not isinstance(base_confirmed_plan, Mapping):
        _raise_input(
            "SCOPE_BASELINE_INVALID",
            "base_confirmed_plan 必须是 confirmed v4 DAG 或 None。",
        )
    plan = deepcopy(plain_json(base_confirmed_plan))
    registry = plan.get("task_registry")
    graph = plan.get("task_graph")
    validation = graph.get("validation") if isinstance(graph, dict) else None
    if (
        plan.get("schema_version") != "build-dag.v4"
        or plan.get("confirmation_status") != "confirmed"
        or plan.get("status") == "failed"
        or not isinstance(registry, dict)
        or not isinstance(validation, dict)
        or validation.get("is_valid") is not True
    ):
        _raise_input(
            "SCOPE_BASELINE_INVALID",
            "base_confirmed_plan 必须是正式 confirmed 且有效的 v4 DAG。",
        )

    task_ids = [
        task_id
        for task in registry.values()
        if isinstance(task, dict) and (task_id := _identity(task.get("id")))
    ]
    duplicate_ids = sorted(
        task_id
        for task_id, count in Counter(task_ids).items()
        if count > 1 and _identity(task_id)
    )
    if duplicate_ids:
        raise ScopeAssemblyError(
            [
                _issue(
                    "GLOBAL_TASK_ID_COLLISION",
                    "confirmed baseline 内存在重复 Task ID，不能建立累计 registry。",
                    task_ids=duplicate_ids,
                    duplicate_source="retained",
                )
            ]
        )
    invalid_registry_ids = [
        str(task_id)
        for task_id, task in registry.items()
        if _identity(task_id) is None
        or not isinstance(task, dict)
        or task.get("id") != task_id
        or _identity(task.get("unit_id")) is None
    ]
    if invalid_registry_ids:
        _raise_input(
            "SCOPE_RETAINED_TASK_IDENTITY_INVALID",
            "confirmed registry 的 key、Task ID 与 Unit ID 必须精确有效。",
            registry_ids=sorted(invalid_registry_ids),
        )
    tasks = [deepcopy(task) for task in tasks_from_build_task_plan(plan)]
    if len(tasks) != len(registry) or {task["id"] for task in tasks} != set(registry):
        _raise_input(
            "SCOPE_BASELINE_INVALID", "confirmed task graph 未完整覆盖正式 registry。"
        )
    return plan, tasks


def _validate_reuse_facts(
    reuse_facts: ReuseFacts | Mapping[str, Any],
    retained_tasks: Sequence[Mapping[str, Any]],
) -> ReuseFacts:
    """确认 ReuseFacts 与正式 registry 精确一致，禁止由不完整事实裁剪历史任务。"""

    try:
        facts = ReuseFacts.model_validate(reuse_facts)
    except (ValidationError, TypeError, ValueError) as exc:
        _raise_input(
            "SCOPE_REUSE_FACTS_INVALID",
            "ReuseFacts 不满足冻结事实契约。",
            error=str(exc),
        )
    if facts.issues:
        raise ScopeAssemblyError(
            [
                issue.model_copy(update={"retryable": False, "retry_unit_ids": ()})
                for issue in facts.issues
            ]
        )
    expected = {(str(task["id"]), str(task["unit_id"])) for task in retained_tasks}
    actual_pairs = [
        (task_id, unit_id)
        for unit_id, task_ids in facts.retained_task_ids_by_unit.items()
        for task_id in task_ids
    ]
    if len(set(actual_pairs)) != len(actual_pairs) or set(actual_pairs) != expected:
        _raise_input(
            "SCOPE_REUSE_FACTS_MISMATCH",
            "ReuseFacts retained Task 归属必须精确匹配 confirmed registry。",
        )
    return facts


def _required_candidate_units(
    generation_requirements_by_unit: Mapping[str, Any],
) -> set[str]:
    """校验 generation requirements，并返回本轮必须提供 Candidate 的 Unit。"""

    required: set[str] = set()
    for raw_unit_id, requirements in generation_requirements_by_unit.items():
        unit_id = _identity(raw_unit_id)
        if (
            unit_id is None
            or isinstance(requirements, (str, bytes))
            or not isinstance(requirements, Sequence)
        ):
            _raise_input(
                "SCOPE_GENERATION_REQUIREMENTS_INVALID",
                "generation_requirements_by_unit 必须按有效 Unit ID 映射到 Requirement 数组。",
            )
        try:
            tuple(GenerationRequirement.model_validate(item) for item in requirements)
        except (ValidationError, TypeError, ValueError) as exc:
            _raise_input(
                "SCOPE_GENERATION_REQUIREMENTS_INVALID",
                f"Unit {unit_id} 的 generation requirements 不满足当前契约。",
                unit_id=unit_id,
                error=str(exc),
            )
        if requirements:
            required.add(unit_id)
    return required


def _candidate_tasks(
    candidates_by_unit: Mapping[str, Any],
    required_units: set[str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """只收集每个 required Unit 当前唯一的 valid Candidate，不做正文修复。"""

    raw_candidate_units = tuple(candidates_by_unit)
    if any(_identity(unit_id) is None for unit_id in raw_candidate_units):
        _raise_input(
            "SCOPE_CANDIDATE_SET_INVALID",
            "candidates_by_unit 的所有 key 都必须是有效 Unit ID。",
        )
    candidate_units = set(raw_candidate_units)
    if candidate_units != required_units:
        _raise_input(
            "SCOPE_CANDIDATE_SET_INVALID",
            "candidates_by_unit 必须与有非空 generation requirements 的 Unit 集合完全一致。",
            missing_unit_ids=sorted(required_units - candidate_units),
            extra_unit_ids=sorted(candidate_units - required_units),
        )
    tasks: list[dict[str, Any]] = []
    unit_by_task_id: dict[str, str] = {}
    candidate_ids: set[str] = set()
    for unit_id in sorted(candidate_units):
        try:
            candidate = CandidateAttempt.model_validate(candidates_by_unit[unit_id])
        except (ValidationError, TypeError, ValueError) as exc:
            _raise_input(
                "SCOPE_CANDIDATE_INVALID",
                f"Unit {unit_id} 的 CandidateAttempt 不满足当前契约。",
                unit_id=unit_id,
                error=str(exc),
            )
        if (
            candidate.identity.unit_id != unit_id
            or candidate.status != "valid"
            or candidate.validation_issues
        ):
            _raise_input(
                "SCOPE_CANDIDATE_INVALID",
                f"Unit {unit_id} 必须提供归属一致、无问题的 valid Candidate。",
                unit_id=unit_id,
                candidate_id=candidate.candidate_id,
            )
        if candidate.candidate_id in candidate_ids:
            _raise_input(
                "SCOPE_CANDIDATE_INVALID",
                "不同 Unit 不得复用同一个 Candidate ID。",
                candidate_id=candidate.candidate_id,
            )
        candidate_ids.add(candidate.candidate_id)
        if not candidate.tasks:
            _raise_input(
                "SCOPE_CANDIDATE_INVALID",
                f"Unit {unit_id} 的 valid Candidate 不得为空。",
                unit_id=unit_id,
                candidate_id=candidate.candidate_id,
            )
        for raw_task in candidate.tasks:
            task = deepcopy(plain_json(raw_task))
            task_id = _identity(task.get("id"))
            if task_id is None or task.get("unit_id") != unit_id:
                _raise_input(
                    "SCOPE_CANDIDATE_TASK_IDENTITY_INVALID",
                    f"Unit {unit_id} 的 Candidate Task 必须保留精确 ID 和 Unit 归属。",
                    unit_id=unit_id,
                    candidate_id=candidate.candidate_id,
                )
            tasks.append(task)
            unit_by_task_id.setdefault(task_id, unit_id)
    return tasks, unit_by_task_id


def _collision_issues(
    retained_tasks: Sequence[Mapping[str, Any]],
    candidate_tasks: Sequence[Mapping[str, Any]],
    candidate_units: Mapping[str, str],
) -> list[ValidationIssue]:
    """在 registry 构建前检查 retained/candidate 全部 ID 碰撞并保留来源证据。"""

    retained_ids = {str(task["id"]) for task in retained_tasks}
    candidate_occurrences: dict[str, list[str]] = {}
    for task in candidate_tasks:
        task_id = str(task["id"])
        candidate_occurrences.setdefault(task_id, []).append(str(task["unit_id"]))
    issues: list[ValidationIssue] = []
    for task_id in sorted(retained_ids & set(candidate_occurrences)):
        unit_id = candidate_occurrences[task_id][0]
        issues.append(
            _issue(
                "GLOBAL_TASK_ID_COLLISION",
                f"Candidate Task {task_id} 与 retained Task ID 冲突。",
                unit_ids=(unit_id,),
                task_ids=(task_id,),
                retry_unit_ids=(unit_id,),
                category="generation",
                retained=True,
                candidate_unit_id=candidate_units.get(task_id, unit_id),
            )
        )
    for task_id, units in sorted(candidate_occurrences.items()):
        if len(units) < 2:
            continue
        unique_units = tuple(sorted(set(units)))
        retry_units = unique_units if len(unique_units) == 1 else ()
        issues.append(
            _issue(
                "GLOBAL_TASK_ID_COLLISION",
                f"多个 Candidate 使用了相同 Task ID：{task_id}。",
                unit_ids=unique_units,
                task_ids=(task_id,),
                retry_unit_ids=retry_units,
                category="generation",
                candidate_units=units,
            )
        )
    return issues


def _validate_task_units(
    skeleton_plan: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """复制当前 Unit 骨架并确保累计任务均有明确归属，绝不按 Unit 删除历史任务。"""

    skeleton = deepcopy(plain_json(skeleton_plan))
    units = skeleton.get("build_units")
    if not isinstance(units, dict) or not isinstance(skeleton.get("unit_graph"), dict):
        _raise_input(
            "SCOPE_SKELETON_INVALID",
            "skeleton_plan 必须包含 build_units 与 unit_graph。",
        )
    missing_units = sorted(
        {str(task["unit_id"]) for task in tasks if task["unit_id"] not in units}
    )
    if missing_units:
        _raise_input(
            "SCOPE_TASK_UNIT_MISSING",
            "累计 Task 所属 Unit 必须全部存在于当前 skeleton_plan。",
            missing_unit_ids=missing_units,
        )
    return skeleton


def _stable_plan_task_ids(build_task_plan: Mapping[str, Any]) -> tuple[str, ...]:
    """按最终 DAG 拓扑顺序返回完整 registry ID，兼顾无效图的确定性回退。"""

    registry = build_task_plan.get("task_registry")
    if not isinstance(registry, Mapping):
        return ()
    registry_ids = {str(task_id) for task_id in registry}
    task_graph = build_task_plan.get("task_graph")
    topology = (
        task_graph.get("topological_order") if isinstance(task_graph, Mapping) else None
    )
    ordered: list[str] = []
    seen: set[str] = set()
    for raw_task_id in [
        *(topology if isinstance(topology, (list, tuple)) else ()),
        *registry,
    ]:
        task_id = str(raw_task_id).strip()
        if task_id in registry_ids and task_id not in seen:
            ordered.append(task_id)
            seen.add(task_id)
    return tuple(ordered)


def _scope_review_task_ids(
    assembled_plan: Mapping[str, Any],
    *,
    current_unit_ids: set[str],
    retained_task_ids: Sequence[str],
    candidate_task_ids: Sequence[str],
    platform_task_ids: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """在 Assembly 中确定当前 Scope 任务及其依赖闭包，不让 Confirmation 重新猜测来源。"""

    registry = assembled_plan.get("task_registry")
    if not isinstance(registry, Mapping):
        return (), ()
    tasks_by_id = {
        str(task.get("id")): task
        for task in registry.values()
        if isinstance(task, Mapping) and _identity(task.get("id")) is not None
    }
    platform_ids = {str(task_id) for task_id in platform_task_ids}
    # required Unit 是当前 Scope 的直接范围；Candidate 即使没有被 Unit 元数据回写也必须纳入 review。
    seed_ids = {
        task_id
        for task_id, task in tasks_by_id.items()
        if task_id not in platform_ids
        and str(task.get("unit_id") or "") in current_unit_ids
    }
    seed_ids.update(
        str(task_id)
        for task_id in candidate_task_ids
        if str(task_id) not in platform_ids
    )
    ancestors: set[str] = set()
    pending = [
        str(dependency)
        for task_id in seed_ids
        for dependency in tasks_by_id.get(task_id, {}).get("dependencies") or []
        if str(dependency) and str(dependency) not in platform_ids
    ]
    while pending:
        task_id = pending.pop()
        if task_id in ancestors or task_id in platform_ids:
            continue
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        ancestors.add(task_id)
        pending.extend(
            str(dependency)
            for dependency in task.get("dependencies") or []
            if (
                str(dependency)
                and str(dependency) not in ancestors
                and str(dependency) not in platform_ids
            )
        )
    review_ids = (seed_ids | ancestors) - platform_ids
    retained_ids = set(str(task_id) for task_id in retained_task_ids)
    reused_ids = review_ids & retained_ids
    ordered_ids = _stable_plan_task_ids(assembled_plan)
    return (
        tuple(task_id for task_id in ordered_ids if task_id in review_ids),
        tuple(task_id for task_id in ordered_ids if task_id in reused_ids),
    )


def assemble_scope_build_task_plan(
    *,
    base_confirmed_plan: Mapping[str, Any] | None,
    skeleton_plan: Mapping[str, Any],
    project_plan: Mapping[str, Any],
    product_plan: Mapping[str, Any],
    build_context: Mapping[str, Any],
    build_execution_scope: Mapping[str, Any],
    reuse_facts: ReuseFacts | Mapping[str, Any],
    generation_requirements_by_unit: Mapping[str, Any],
    candidates_by_unit: Mapping[str, CandidateAttempt | Mapping[str, Any]],
) -> ScopeAssemblyResult:
    """append-only 组装 retained Tasks 与当前 valid Candidates，并重编译 DAG 派生字段。

    ``build_execution_scope`` 由当前 PlanningRun 显式提供，是唯一允许写入 Plan
    root 的权威 Scope；ConfirmedPlan 和 skeleton 仅提供历史 Task 与当前编译骨架。
    本服务不选择 Candidate、不执行 retry、不写 PendingPlan，也不做 Task replacement。
    任一来源或 ID 冲突均在 registry 建立前失败，禁止自动 rename 或精确重复合并。
    """

    if not all(
        isinstance(value, Mapping)
        for value in (
            skeleton_plan,
            project_plan,
            product_plan,
            build_context,
            build_execution_scope,
            generation_requirements_by_unit,
            candidates_by_unit,
        )
    ):
        _raise_input(
            "SCOPE_ASSEMBLY_INPUT_INVALID", "Scope Assembly 的映射输入类型无效。"
        )
    # 先冻结并规范化本轮模板事实，避免 compiler 或 confirmed baseline 提供隐式回退。
    try:
        frozen_template_context = validate_template_context(
            plain_json(build_context.get("template_context"))
        )
    except (TypeError, ValueError) as exc:
        _raise_input(
            "SCOPE_TEMPLATE_CONTEXT_INVALID",
            "当前 Frozen Planning Inputs 的 template_context 不满足 V2 绑定契约。",
            error=str(exc),
        )
    _, retained = _retained_tasks(base_confirmed_plan)
    facts = _validate_reuse_facts(reuse_facts, retained)
    required_units = _required_candidate_units(generation_requirements_by_unit)
    candidates, candidate_unit_by_task_id = _candidate_tasks(
        candidates_by_unit, required_units
    )
    collision_issues = _collision_issues(
        retained, candidates, candidate_unit_by_task_id
    )
    if collision_issues:
        raise ScopeAssemblyError(collision_issues)

    # Route Projection 已移至成功 DAG 的固定收尾阶段，当前 DAG 只保留业务 Task。
    retained_for_assembly = retained
    all_tasks = [*retained_for_assembly, *candidates]
    skeleton = _validate_task_units(skeleton_plan, all_tasks)
    retained_task_ids = tuple(str(task["id"]) for task in retained_for_assembly)
    candidate_task_ids = tuple(str(task["id"]) for task in candidates)
    retained_id_set = set(retained_task_ids)
    context = {
        **deepcopy(plain_json(build_context)),
        "template_context": deepcopy(frozen_template_context),
        "project_plan": deepcopy(plain_json(project_plan)),
        "executable_details": (
            deepcopy(plain_json(project_plan.get("executable_details")))
            if isinstance(project_plan.get("executable_details"), Mapping)
            else deepcopy(plain_json(build_context.get("executable_details") or {}))
        ),
        "_allow_missing_business_deliverable_task_ids": sorted(retained_id_set),
        "_compile_auth_capability_dependencies": True,
        "external_capabilities": [
            capability.model_dump(mode="json")
            for capability in facts.external_capabilities
        ],
    }
    try:
        assembled = compile_build_task_plan_scope(
            skeleton,
            all_tasks,
            context,
            validate_task_scope=False,
            preserve_compiled_task_ids=retained_id_set,
            preserve_task_contract_ids=retained_id_set,
        )
    except BuildUnitCompilationError as exc:
        raise ScopeAssemblyError(exc.issues) from exc
    graph_valid = (
        assembled.get("task_graph", {}).get("validation", {}).get("is_valid") is True
    )
    blocked_batches = assembled.get("execution", {}).get("blocked_batches", [])
    assembled = dict(assembled)
    # Plan root 的 scope 与 TemplateState 都只绑定当前 PlanningRun，不能继承旧 baseline。
    assembled["build_execution_scope"] = deepcopy(plain_json(build_execution_scope))
    assembled["template_context"] = deepcopy(frozen_template_context)
    # Assembly 只产生等待 Global Validation 的内存草稿；正式 PendingPlan 生命周期
    # 由后续 Controller / persistence 在 Global success 后赋予，且不能继承 confirmed 基线。
    for field in (
        "confirmation_status",
        "confirmed_at",
        "confirmed_from",
        "draft_identity",
        "last_update",
    ):
        assembled.pop(field, None)
    assembled["status"] = "ready" if graph_valid and not blocked_batches else "blocked"
    # 路由页面事实仅在成功 DAG 的 Finalization 中使用，DAG 不保存第二份页面事实。
    assembled.pop("route_projection", None)
    current_unit_ids = {
        str(unit_id).strip()
        for unit_id in generation_requirements_by_unit
        if str(unit_id).strip()
    }
    # 当前冻结 BuildContext 的 required_unit_ids 优先；显式空数组也表示空 Scope，不能被生成表回填。
    context_required_unit_ids = build_context.get("required_unit_ids")
    if isinstance(context_required_unit_ids, (list, tuple, set)):
        normalized_context_units = {
            str(unit_id).strip()
            for unit_id in context_required_unit_ids
            if str(unit_id).strip()
        }
        current_unit_ids = normalized_context_units
    # 当前 Route Projection 不再是 DAG Task；保留 provenance 字段以维持 PendingPlan 契约。
    platform_task_ids: tuple[str, ...] = ()
    review_task_ids, reused_task_ids = _scope_review_task_ids(
        assembled,
        current_unit_ids=current_unit_ids,
        retained_task_ids=retained_task_ids,
        candidate_task_ids=candidate_task_ids,
        platform_task_ids=platform_task_ids,
    )
    task_origins = {
        **{task_id: "retained" for task_id in retained_task_ids},
        **{task_id: "candidate" for task_id in candidate_task_ids},
        **{task_id: "platform" for task_id in platform_task_ids},
    }
    return ScopeAssemblyResult(
        assembled_plan=assembled,
        retained_task_ids=retained_task_ids,
        candidate_task_ids=candidate_task_ids,
        platform_task_ids=platform_task_ids,
        review_task_ids=review_task_ids,
        reused_task_ids=reused_task_ids,
        task_origins=task_origins,
        candidate_unit_by_task_id=candidate_unit_by_task_id,
    )
