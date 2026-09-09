"""串行 Planning 的冻结输入、前置校验与单 Unit Catalog Context 构造。"""

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, StringConstraints

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.frozen_contract_catalog import (
    ContractCatalogBindingError,
    FormalContractSourceRef,
    build_unit_contract_catalog,
    canonicalize_formal_source_refs,
)
from app.services.frozen_contract_manifest import compile_expected_unit_formal_source_refs
from app.services.frozen_contract_store import FrozenContractStore, PlanningFormalInputs
from app.services.planning_frozen import (
    FrozenJsonObject,
    FrozenPlanningModel,
    plain_json,
    tuple_input,
)
from app.services.planning_run_contracts import PlanningRun, UnitRunState
from app.services.unit_generation_contracts import UnitGenerationContext
from app.services.unit_generation_requirement_targets import scoped_formal_targets
from app.services.unit_generation_requirements import resolve_generation_requirements
from app.services.unit_generation_requirements_contracts import (
    UnitGenerationRequirements, fail_requirement_input,
)


def _input_digest(value: Mapping) -> str:
    """对当前内存输入规范化编码取摘要，不读取正式文件或依赖磁盘路径。"""

    return sha256(json.dumps(plain_json(value), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


_FormalSourceRefs = Annotated[
    tuple[FormalContractSourceRef, ...],
    BeforeValidator(tuple_input),
    AfterValidator(canonicalize_formal_source_refs),
]
_Identifier = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$"),
]


class SequentialPlanningInputs(FrozenPlanningModel):
    """一次 Run 的完整只读业务输入；不接受 Pending/checkpoint 替代正式基线。"""

    project_plan: FrozenJsonObject
    base_confirmed_plan: FrozenJsonObject | None
    skeleton_plan: FrozenJsonObject
    build_context: FrozenJsonObject
    build_execution_scope: FrozenJsonObject
    workspace_snapshot: FrozenJsonObject
    reuse_facts: ReuseFacts
    formal_contract_inputs: PlanningFormalInputs
    formal_source_refs: _FormalSourceRefs

    def requirements(self) -> UnitGenerationRequirements:
        """验证基线身份及 retained 全量覆盖，再调用 T2.3 计算本轮职责。"""

        baseline = self.base_confirmed_plan
        graph = baseline.get("task_graph") if baseline is not None else None
        validation = graph.get("validation") if isinstance(graph, Mapping) else None
        if baseline is not None and (
            baseline.get("confirmation_status") != "confirmed"
            or baseline.get("schema_version") != "build-dag.v3"
            or baseline.get("status") == "failed"
            or not isinstance(baseline.get("task_registry"), Mapping)
            or not isinstance(validation, Mapping) or validation.get("is_valid") is not True
        ):
            fail_requirement_input("CONFIRMED_BASELINE_INVALID", "Planning 只接受有效 ConfirmedPlan 或空基线。")
        registry = baseline["task_registry"] if baseline is not None else {}
        retained = [(key, value["unit_id"]) for key, value in registry.items()
                    if isinstance(value, Mapping) and value.get("id") == key and isinstance(value.get("unit_id"), str)]
        actual = [(key, unit) for unit, keys in self.reuse_facts.retained_task_ids_by_unit.items() for key in keys]
        if len(retained) != len(registry) or len(actual) != len(set(actual)) or set(retained) != set(actual):
            fail_requirement_input("PLANNING_REUSE_BASELINE_MISMATCH", "ReuseFacts 必须精确覆盖完整 ConfirmedPlan。")
        if any(unit not in self.skeleton_plan.get("build_units", {}) for _, unit in retained):
            fail_requirement_input("CONFIRMED_UNIT_MISSING", "所有历史 Task Unit 必须保留在当前骨架中。")
        scope = self.build_context.get("scope")
        if scope is not None and scope != self.build_execution_scope:
            fail_requirement_input("PLANNING_SCOPE_MISMATCH", "BuildContext 与显式 BuildExecutionScope 不一致。")
        if "required_unit_ids" not in self.build_context:
            fail_requirement_input("PLANNING_REQUIRED_UNITS_MISSING", "BuildContext 必须显式提供 required_unit_ids；空 Scope 也需提供空数组。")
        if self.formal_contract_inputs.technical_plan.content != self.project_plan:
            fail_requirement_input(
                "FORMAL_CONTRACT_INPUT_MISMATCH",
                "FrozenContractStore 的 TechnicalPlan 正文必须与 Planning 正式 target 完全一致。",
            )
        return resolve_generation_requirements(
            required_unit_ids=self.build_context.get("required_unit_ids", ()),
            build_execution_scope=self.build_execution_scope, unit_skeleton=self.skeleton_plan,
            reuse_facts=self.reuse_facts, formal_target=self.project_plan,
        )

    def create_run(
        self, requirements: UnitGenerationRequirements, *, planning_run_id: str,
        workflow_run_id: str, thread_id: str, at: str,
    ) -> PlanningRun:
        """绑定冻结输入摘要及正式基线摘要，创建本次串行 Run 的唯一初始身份。"""

        units = {}
        for key, strategy in requirements.generation_strategy_by_unit.items():
            if strategy in {"model", "deterministic"} and not requirements.generation_requirements_by_unit[key]:
                fail_requirement_input("PLANNING_EMPTY_GENERATION_STRATEGY", "空职责的生成策略不能创建可调度 Unit。", unit_ids=(key,))
            units[key] = UnitRunState.create(
                unit_id=key, kind=self.skeleton_plan["build_units"][key]["kind"],
                generation_strategy=strategy,
                retained_task_ids=self.reuse_facts.retained_task_ids_by_unit.get(key, ()),
                reusable_capabilities=tuple(self.reuse_facts.reusable_capabilities_by_unit.get(key, {})),
            )
        return PlanningRun(
            planning_run_id=planning_run_id, workflow_run_id=workflow_run_id, thread_id=thread_id,
            build_execution_scope=self.build_execution_scope,
            input_fingerprint=_input_digest(self.model_dump(mode="json")),
            base_confirmed_plan_digest=_input_digest(self.base_confirmed_plan)
            if self.base_confirmed_plan is not None else None,
            required_unit_ids=tuple(units), planning_unit_ids=requirements.planning_unit_ids,
            unit_states=units, started_at=at, updated_at=at,
        )

    def unit_context(
        self,
        run: PlanningRun,
        requirements: UnitGenerationRequirements,
        unit_id: str,
        *,
        frozen_contract_store: FrozenContractStore,
    ) -> UnitGenerationContext:
        """为当前 Unit 构造 Frozen Store catalog，绝不内联合同或读取 Candidate。"""

        plan = plain_json(self.project_plan)
        pages, endpoints = scoped_formal_targets(plan, self.build_execution_scope)
        duties = requirements.generation_requirements_by_unit[unit_id]
        selected_pages = [page for key, page in pages.items() if unit_id == f"page:{key}"]
        endpoint_keys = {(duty.source_refs.get("api_contract_id"), duty.source_refs.get("endpoint_id"))
                         for duty in duties if duty.source_refs.get("endpoint_id")}
        if selected_pages:
            referenced = {key for page in selected_pages for key in page.get("requiredEndpointIds", ())}
            endpoint_keys.update(key for key in endpoints if key[1] in referenced)
        elif not endpoint_keys:
            # 公共 adapter/bootstrap 的职责依赖当前 Scope 的接口与数据源，但不包含其他 Scope。
            endpoint_keys.update(endpoints)
        unit = run.unit_states[unit_id]
        if frozen_contract_store.planning_run_id != run.planning_run_id:
            fail_requirement_input(
                "UNIT_CONTRACT_SOURCE_BINDING_INVALID",
                "FrozenContractStore 与当前 PlanningRun 身份不一致。",
                unit_ids=(unit_id,),
            )
        try:
            expected_formal_source_refs = compile_expected_unit_formal_source_refs(
                unit_id=unit_id,
                unit_kind=unit.kind,
                generation_requirements=duties,
                scoped_endpoint_keys=tuple(endpoints),
                frozen_contract_store=frozen_contract_store,
            )
            contract_catalog = build_unit_contract_catalog(
                unit_id=unit_id,
                unit_kind=unit.kind,
                generation_requirements=duties,
                formal_source_refs=self.formal_source_refs,
                expected_formal_source_refs=expected_formal_source_refs,
                frozen_contract_store=frozen_contract_store,
            )
        except ContractCatalogBindingError as exc:
            fail_requirement_input(
                "UNIT_CONTRACT_SOURCE_BINDING_INVALID",
                str(exc),
                unit_ids=(unit_id,),
            )
        registry = self.base_confirmed_plan["task_registry"] if self.base_confirmed_plan is not None else {}
        summaries = [{field: registry[key][field] for field in ("id", "unit_id", "title", "description", "provides_capabilities")
                      if field in registry[key]} for key in unit.retained_task_ids]
        return UnitGenerationContext(
            planning_run_id=run.planning_run_id, unit_id=unit_id, unit_kind=unit.kind,
            build_execution_scope=run.build_execution_scope, input_fingerprint=run.input_fingerprint,
            base_confirmed_plan_digest=run.base_confirmed_plan_digest, generation_requirements=duties,
            contract_catalog=contract_catalog,
            workspace_context=self.workspace_snapshot,
            dependency_context={
                "dependency_unit_ids": sorted({edge["from"] for edge in self.skeleton_plan["unit_graph"].get("edges", ())
                                               if edge.get("to") == unit_id and edge.get("type") == "depends_on"}),
                "retained_task_summaries": summaries,
                "retained_owner_constraints": [owner.model_dump(mode="json") for owner in self.reuse_facts.retained_endpoint_owners
                                               if (owner.api_contract_id, owner.endpoint_id) in endpoint_keys],
            },
            constraints={"owner": "frontend" if unit.kind == "page" else unit.kind,
                         "managed_files": [], "strong_rules": ["exact_unit_owner", "exact_file_scope",
                         "no_platform_owned_fields", "no_platform_owned_tasks", "no_repair_or_verification_tasks", "status_pending"]},
        )


class MainlinePlanningInputs(SequentialPlanningInputs):
    """汇集 Workflow 身份与正式规划输入，作为 mainline service 的只读入口。"""

    owner_session_id: _Identifier
    workflow_run_id: _Identifier
    thread_id: _Identifier

    def sequential_inputs(self) -> SequentialPlanningInputs:
        """剥离 Workflow 身份并构造 Scheduler 唯一接受的冻结输入 DTO。"""

        return SequentialPlanningInputs.model_validate(
            self.model_dump(
                mode="python",
                exclude={"owner_session_id", "workflow_run_id", "thread_id"},
            )
        )


def assemble_mainline_planning_inputs(
    *,
    project_plan: Mapping,
    base_confirmed_plan: Mapping | None,
    skeleton_plan: Mapping,
    build_context: Mapping,
    build_execution_scope: Mapping,
    workspace_snapshot: Mapping,
    reuse_facts: ReuseFacts,
    formal_contract_inputs: PlanningFormalInputs,
    owner_session_id: str,
    workflow_run_id: str,
    thread_id: str,
) -> MainlinePlanningInputs:
    """从 Workflow 正式输入编译 mainline DTO，不向 adapter 暴露 Unit Catalog 内部状态。

    期望 binding manifest 由当前职责与 Frozen Store 独立编译；调用方只负责提供
    已确认产物、Scope、工作区证据和只读 ReuseFacts，不创建 PlanningRun、Scheduler
    或 Candidate。
    """

    provisional = SequentialPlanningInputs(
        project_plan=project_plan,
        base_confirmed_plan=base_confirmed_plan,
        skeleton_plan=skeleton_plan,
        build_context=build_context,
        build_execution_scope=build_execution_scope,
        workspace_snapshot=workspace_snapshot,
        reuse_facts=reuse_facts,
        formal_contract_inputs=formal_contract_inputs,
        formal_source_refs=(),
    )
    requirements = provisional.requirements()
    manifest_store = FrozenContractStore.create(
        planning_run_id="mainline-input-manifest",
        formal_inputs=formal_contract_inputs,
    )
    _, endpoints = scoped_formal_targets(
        plain_json(provisional.project_plan),
        provisional.build_execution_scope,
    )
    formal_source_refs = tuple(
        source_ref
        for unit_id, unit_requirements in requirements.generation_requirements_by_unit.items()
        if unit_requirements
        for source_ref in compile_expected_unit_formal_source_refs(
            unit_id=unit_id,
            unit_kind=provisional.skeleton_plan["build_units"][unit_id]["kind"],
            generation_requirements=unit_requirements,
            scoped_endpoint_keys=tuple(endpoints),
            frozen_contract_store=manifest_store,
        )
    )
    return MainlinePlanningInputs(
        **provisional.model_dump(mode="python", exclude={"formal_source_refs"}),
        formal_source_refs=formal_source_refs,
        owner_session_id=owner_session_id,
        workflow_run_id=workflow_run_id,
        thread_id=thread_id,
    )
