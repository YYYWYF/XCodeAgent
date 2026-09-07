"""T6.4/T9.2 Requirements → 并发 Unit Queue → Barrier → Assembly/Global → Repair。"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import model_validator

from app.config import Settings
from app.services.authorization_resource_catalog import compile_frontend_resource_catalog, resource_catalog_fingerprint
from app.services.dag_planning_inputs import SequentialPlanningInputs
from app.services.deterministic_unit_candidates import build_auth_guard_candidate
from app.services.global_issue_attribution import GlobalRepairDecision, attribute_global_issues
from app.services.global_planning_validation import CandidateOwnership, TaskProvenance
from app.services.global_repair_orchestrator import run_global_repair_loop
from app.services.planning_frozen import FrozenPlanningModel, plain_json
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import PlanningRun, UnitRunState
from app.services.planning_run_controller import PlanningRunController, SnapshotPublisher
from app.services.planning_run_events import (
    AssemblyStarted, CandidateReady, GenerationStarted, GlobalValidationStarted,
    RunFailed, UnitAttemptStarted, UnitValidationStarted,
)
from app.services.scope_assembly import ScopeAssemblyError, ScopeAssemblyResult, assemble_scope_build_task_plan
from app.services.unit_generation import UnitGenerationInfrastructureError
from app.services.unit_generation_contracts import AttemptIdentity, CandidateAttempt, UnitGenerationAttemptResult, UnitGenerationPolicy
from app.services.unit_generation_orchestrator import UnitGenerationFatalError
from app.services.unit_generation_requirements_contracts import GenerationRequirementsError, UnitGenerationRequirements
from app.services.unit_generation_scheduler import UnitGenerationScheduler


class DagPlanningError(RuntimeError):
    """规划未通过时携带完整问题与已提交 Run；不得把失败草稿交给后续阶段。"""

    def __init__(self, issues: Sequence[ValidationIssue], snapshot: PlanningRun | None = None) -> None:
        """保存结构化证据；前置输入失败尚未创建 Run，snapshot 为 None。"""

        self.issues = tuple(ValidationIssue.model_validate(issue) for issue in issues)
        self.snapshot = snapshot
        super().__init__("；".join(issue.message for issue in self.issues))


class ValidatedAssembledPlan(FrozenPlanningModel):
    """已通过当前全局检查的内存累计 DAG，没有 Pending/Confirmed 身份。"""

    assembly: ScopeAssemblyResult
    planning_run: PlanningRun
    generation_requirements: UnitGenerationRequirements

    @model_validator(mode="after")
    def validate_success(self) -> "ValidatedAssembledPlan":
        """拒绝失败 Run、不齐全 Candidate、无效图及混入正式确认身份的结果。"""

        run = self.planning_run
        plan = self.assembly.assembled_plan
        if run.status != "active" or run.phase != "validating":
            raise ValueError("ValidatedAssembledPlan 必须来自 active/validating Run。")
        if any(run.unit_states[key].generation_status != "candidate_ready" for key in run.planning_unit_ids):
            raise ValueError("ValidatedAssembledPlan 必须拥有全部当前 Candidate。")
        if self.generation_requirements.planning_unit_ids != run.planning_unit_ids:
            raise ValueError("ValidatedAssembledPlan 的 Requirements 必须属于当前 Run。")
        current_task_ids = {task["id"] for key in run.planning_unit_ids
                            for task in run.candidates[run.unit_states[key].latest_candidate_id].tasks}
        if set(self.assembly.candidate_task_ids) != current_task_ids:
            raise ValueError("ValidatedAssembledPlan 不能引用旧轮次或缺失的 Candidate Tasks。")
        if _compiled_issues(self.assembly) or "confirmation_status" in plan or "confirmed_at" in plan:
            raise ValueError("ValidatedAssembledPlan 必须是通过编译检查且无确认身份的内存草稿。")
        return self


def _now() -> str:
    """为 Controller 事件生成 UTC 时间，测试可以注入固定值。"""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compiled_issues(assembly: ScopeAssemblyResult) -> tuple[ValidationIssue, ...]:
    """消费真实累计编译结论；旧字符串只作诊断，绝不据此猜测修复 Unit。"""

    plan = assembly.assembled_plan
    validation = plan.get("task_graph", {}).get("validation", {})
    blocked = plan.get("execution", {}).get("blocked_batches", ())
    if plan.get("status") == "ready" and validation.get("is_valid") is True and not validation.get("errors") and not blocked:
        return ()
    return (ValidationIssue(
        code="GLOBAL_COMPILED_PLAN_INVALID", level="global", category="platform", retryable=False,
        message="累计 DAG 未通过完整编译检查，缺少结构化责任证据的错误不能自动重试。",
        details={"validation": validation, "blocked_batches": blocked},
    ),)


def _attribute(
    snapshot: PlanningRun, inputs: SequentialPlanningInputs, issues: Sequence[ValidationIssue],
) -> GlobalRepairDecision:
    """从全部当前 Candidate 与完整 retained registry 构造可保留碰撞的来源序列。"""

    owners = [CandidateOwnership.from_candidate(snapshot.candidates[snapshot.unit_states[key].latest_candidate_id])
              for key in snapshot.planning_unit_ids]
    retained = inputs.base_confirmed_plan["task_registry"] if inputs.base_confirmed_plan is not None else {}
    provenance = [TaskProvenance(task_id=key, unit_id=task["unit_id"], source="retained") for key, task in retained.items()]
    provenance.extend(TaskProvenance(task_id=key, unit_id=owner.unit_id, source="candidate", candidate_id=owner.candidate_id)
                      for owner in owners for key in owner.task_ids)
    return attribute_global_issues(
        issues, task_provenance=provenance, reuse_facts=inputs.reuse_facts,
        candidate_ownership=owners, planning_unit_ids=snapshot.planning_unit_ids,
    )


async def plan_dag_sequential(
    inputs: SequentialPlanningInputs,
    *,
    workspace_state: Mapping[str, Any],
    planning_run_id: str,
    workflow_run_id: str,
    thread_id: str,
    policy: UnitGenerationPolicy,
    generation_requirements: UnitGenerationRequirements | None = None,
    settings: Settings | None = None,
    generate_once: Callable[..., Awaitable[UnitGenerationAttemptResult]] | None = None,
    publish: SnapshotPublisher | None = None,
    now: Callable[[], str] = _now,
) -> ValidatedAssembledPlan:
    """执行最多三个 model session 并发的新链路，只写轻量 planning-run.json。

    上游提供正式输入、Scope、骨架及受信 ReuseFacts；若提供已计算 requirements，必须
    与本次 T2.3 结果精确相同。所有 Context 在首个模型调用前冻结；每个模型 Unit 独立
    完成最多三次 Local，Global=2 只重开归因目标。deterministic 不消耗模型预算。
    仅成功返回 ValidatedAssembledPlan；内容/基础设施失败抛 DagPlanningError。持久化、
    发布或任务取消保持 Controller 原有异常语义。不写 Pending 或任何 Formal file。
    """

    frozen = SequentialPlanningInputs.model_validate(inputs)
    policy = UnitGenerationPolicy.model_validate(policy)
    try:
        requirements = frozen.requirements()
        if generation_requirements is not None and UnitGenerationRequirements.model_validate(generation_requirements) != requirements:
            raise GenerationRequirementsError((ValidationIssue(
                code="PLANNING_REQUIREMENTS_MISMATCH", level="pre_generation", category="input",
                retryable=False, message="提供的 generation requirements 与当前冻结输入计算结果不一致。",
            ),))
        initial = frozen.create_run(requirements, planning_run_id=planning_run_id,
                                    workflow_run_id=workflow_run_id, thread_id=thread_id, at=now())
        contexts = {key: frozen.unit_context(initial, requirements, key) for key in initial.planning_unit_ids
                    if initial.unit_states[key].generation_strategy == "model"}
    except GenerationRequirementsError as exc:
        raise DagPlanningError(exc.issues) from exc
    controller = PlanningRunController(initial, workspace_state, publish=publish)
    scheduler = UnitGenerationScheduler(
        concurrency=(settings.dag_unit_generation_concurrency if settings else 3),
    )
    assembled: ScopeAssemblyResult | None = None

    async def regenerate_deterministic(current: UnitRunState) -> None:
        """在 model worker pool 外生成并提交当前 deterministic Candidate。"""

        if current.generation_strategy != "deterministic":
            raise ValueError("确定性生成回调只能接收 deterministic Unit。")
        identity = AttemptIdentity.allocate(planning_run_id=initial.planning_run_id, unit_id=current.unit_id,
                                            generation_round=current.generation_round, attempt_in_round=1)
        await controller.apply(UnitAttemptStarted(identity=identity, at=now()))
        catalog = compile_frontend_resource_catalog(plain_json(frozen.project_plan.get("authorization_manifest", {})))
        payload = build_auth_guard_candidate(
            unit_id=current.unit_id, resource_catalog=catalog, fingerprint=resource_catalog_fingerprint(catalog),
            generation_requirements=requirements,
        )
        if payload is None:
            raise GenerationRequirementsError((ValidationIssue(
                code="PLANNING_DETERMINISTIC_CANDIDATE_MISSING", level="system", category="platform",
                retryable=False, unit_ids=(current.unit_id,), message="确定性生成 Unit 没有返回所需 Candidate。",
            ),))
        await controller.apply(UnitValidationStarted(identity=identity, at=now()))
        await controller.apply(CandidateReady(candidate=CandidateAttempt(
            identity=identity, input_fingerprint=initial.input_fingerprint, status="valid", tasks=payload["tasks"],
        ), at=now()))

    async def regenerate_round(units: tuple[UnitRunState, ...]) -> None:
        """把完整 pending 目标批次交给 FIFO Scheduler，并透传各 Unit Global feedback。"""

        pending = {
            unit_id
            for unit_id in controller.snapshot.planning_unit_ids
            if controller.snapshot.unit_states[unit_id].generation_status == "pending"
        }
        supplied = {unit.unit_id for unit in units}
        if supplied != pending:
            raise ValueError("Generation round 目标必须精确覆盖当前全部 pending Unit。")
        await scheduler.run_round(
            controller,
            contexts,
            policy,
            global_feedback_by_unit={
                unit.unit_id: unit.current_issues
                for unit in units
                if unit.generation_strategy == "model"
            },
            reuse_facts=frozen.reuse_facts,
            settings=settings,
            generate_once=generate_once,
            run_deterministic=regenerate_deterministic,
            now=now,
        )

    async def assemble_and_validate(snapshot: PlanningRun) -> GlobalRepairDecision:
        """在齐全 Barrier 后执行真实 append-only Assembly，并归因本 cycle 的完整失败。"""

        nonlocal assembled
        assembled = None
        await controller.apply(AssemblyStarted(at=now()))
        try:
            assembled = assemble_scope_build_task_plan(
                base_confirmed_plan=frozen.base_confirmed_plan, skeleton_plan=frozen.skeleton_plan,
                project_plan=frozen.project_plan, build_context=frozen.build_context,
                reuse_facts=frozen.reuse_facts, generation_requirements_by_unit=requirements.generation_requirements_by_unit,
                candidates_by_unit={key: snapshot.candidates[snapshot.unit_states[key].latest_candidate_id]
                                    for key in snapshot.planning_unit_ids},
            )
        except ScopeAssemblyError as exc:
            return _attribute(snapshot, frozen, exc.issues)
        await controller.apply(GlobalValidationStarted(at=now()))
        return _attribute(snapshot, frozen, _compiled_issues(assembled))

    try:
        await controller.apply(GenerationStarted(at=now()))
        await regenerate_round(tuple(
            controller.snapshot.unit_states[key]
            for key in initial.planning_unit_ids
        ))
        decision = await run_global_repair_loop(
            controller, validate_global=assemble_and_validate,
            regenerate_round=regenerate_round, now=now,
        )
    except asyncio.CancelledError:
        # Scheduler 已收口生成期取消；此处幂等覆盖 Assembly/Global 等其他 await 边界。
        await controller.cancel(at=now())
        raise
    except (UnitGenerationInfrastructureError, UnitGenerationFatalError) as exc:
        raise DagPlanningError((controller.snapshot.failure,), controller.snapshot) from exc
    except GenerationRequirementsError as exc:
        await controller.apply(RunFailed(issue=exc.issues[0], at=now()))
        raise DagPlanningError(exc.issues, controller.snapshot) from exc
    if decision.issues or controller.snapshot.status != "active":
        raise DagPlanningError(decision.issues, controller.snapshot)
    if assembled is None:
        raise RuntimeError("Global success 未产生完整 Assembly 结果。")
    return ValidatedAssembledPlan(assembly=assembled, planning_run=controller.snapshot, generation_requirements=requirements)
