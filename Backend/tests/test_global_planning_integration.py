"""T4.1 与 T4.2/T4.3 的 Assembly Issue 到 Global 归因集成测试。"""

from copy import deepcopy
import unittest

from app.services.authorization_capability_dependency import AUTH_GUARD_UNIT_ID
from app.services.global_issue_attribution import attribute_global_issues
from app.services.global_planning_validation import CandidateOwnership, TaskProvenance
from app.services.planning_issues import ValidationIssue
from app.services.scope_assembly import ScopeAssemblyError, assemble_scope_build_task_plan
from tests.dag_planning_baseline_fixtures import task
from tests.test_scope_assembly import (
    SHARED_UNIT,
    _add_retained_auth_provider,
    _auth_inputs,
    _base_inputs,
    _candidate,
    _customer_api_task,
    _requirement,
    _reuse_facts,
)


def _caught_assembly_issues(inputs: dict) -> tuple[ValidationIssue, ...]:
    """执行真实 Scope Assembly 并返回其结构化失败问题。"""

    try:
        assemble_scope_build_task_plan(**inputs)
    except ScopeAssemblyError as exc:
        return exc.issues
    raise AssertionError("测试输入必须由 Scope Assembly 产生结构化 Global issue。")


def _attribution_inputs(inputs: dict) -> dict:
    """从真实 Candidate 与 ReuseFacts 构造完整 ownership/provenance 快照。"""

    candidates = tuple(inputs["candidates_by_unit"].values())
    owners = tuple(CandidateOwnership.from_candidate(candidate) for candidate in candidates)
    provenance = [
        TaskProvenance(task_id=task_id, unit_id=unit_id, source="retained")
        for unit_id, task_ids in inputs["reuse_facts"].retained_task_ids_by_unit.items()
        for task_id in task_ids
    ]
    provenance.extend(
        TaskProvenance(
            task_id=task_id,
            unit_id=owner.unit_id,
            source="candidate",
            candidate_id=owner.candidate_id,
        )
        for owner in owners
        for task_id in owner.task_ids
    )
    return {
        "planning_unit_ids": tuple(inputs["candidates_by_unit"]),
        "candidate_ownership": owners,
        "task_provenance": provenance,
        "reuse_facts": inputs["reuse_facts"],
    }


class GlobalPlanningIntegrationTests(unittest.TestCase):
    def test_candidate_retained_collision_routes_to_current_unit(self) -> None:
        """真实 retained ID 冲突经完整 provenance 归因后只重试当前 Candidate Unit。"""

        inputs = _base_inputs()
        inputs["candidates_by_unit"] = {
            SHARED_UNIT: _candidate(SHARED_UNIT, [_customer_api_task("api:adapter")])
        }

        decision = attribute_global_issues(
            _caught_assembly_issues(inputs),
            **_attribution_inputs(inputs),
        )

        self.assertTrue(decision.retryable)
        self.assertEqual(decision.retry_unit_ids, (SHARED_UNIT,))

    def test_retained_candidate_auth_provider_conflict_routes_to_auth_candidate(self) -> None:
        """R2 retained 与 auth Candidate 冲突时只归因当前 auth-guard Candidate。"""

        inputs, _, capability_r2 = _auth_inputs()
        _add_retained_auth_provider(
            inputs["base_confirmed_plan"], "auth-r2-retained", capability_r2
        )
        inputs["reuse_facts"] = _reuse_facts(inputs["base_confirmed_plan"])
        auth_task = deepcopy(inputs["base_confirmed_plan"]["task_registry"]["auth-r2-retained"])
        auth_task.update({
            "id": "auth-r2-candidate",
            "status": "pending",
            "provides_capabilities": [capability_r2],
        })
        inputs["generation_requirements_by_unit"] = {
            **inputs["generation_requirements_by_unit"],
            AUTH_GUARD_UNIT_ID: _requirement(AUTH_GUARD_UNIT_ID),
        }
        inputs["candidates_by_unit"] = {
            **inputs["candidates_by_unit"],
            AUTH_GUARD_UNIT_ID: _candidate(AUTH_GUARD_UNIT_ID, [auth_task], "e"),
        }

        decision = attribute_global_issues(
            _caught_assembly_issues(inputs),
            **_attribution_inputs(inputs),
        )

        self.assertTrue(decision.retryable)
        self.assertEqual(decision.retry_unit_ids, (AUTH_GUARD_UNIT_ID,))

    def test_symmetric_candidate_collision_remains_nonretryable(self) -> None:
        """两个 Candidate 的对称同 ID 冲突经过完整链路仍不得猜测责任方。"""

        inputs = _base_inputs()
        tasks_by_unit = {
            "page:orders": task(
                "candidate-collision",
                "page:orders",
                "frontend.page",
                "frontend/src/pages/Orders/Details.tsx",
                "orders",
            ),
            "page:customers": task(
                "candidate-collision",
                "page:customers",
                "frontend.page",
                "frontend/src/pages/Customers/Details.tsx",
                "customers",
            ),
        }
        inputs["generation_requirements_by_unit"] = {
            unit_id: _requirement(unit_id) for unit_id in tasks_by_unit
        }
        inputs["candidates_by_unit"] = {
            "page:orders": _candidate("page:orders", [tasks_by_unit["page:orders"]], "b"),
            "page:customers": _candidate(
                "page:customers", [tasks_by_unit["page:customers"]], "c"
            ),
        }

        decision = attribute_global_issues(
            _caught_assembly_issues(inputs),
            **_attribution_inputs(inputs),
        )

        self.assertFalse(decision.retryable)
        self.assertEqual(decision.retry_unit_ids, ())

    def test_platform_blocker_prevents_otherwise_retryable_global_repair(self) -> None:
        """真实可归因冲突与平台 blocker 并存时，整轮 Global Repair 不得启动。"""

        inputs = _base_inputs()
        inputs["candidates_by_unit"] = {
            SHARED_UNIT: _candidate(SHARED_UNIT, [_customer_api_task("api:adapter")])
        }
        issues = [*_caught_assembly_issues(inputs), ValidationIssue(
            code="GLOBAL_COMPILER_ERROR",
            level="global",
            category="platform",
            task_ids=("api:adapter",),
            retryable=False,
            message="平台编译阻断。",
        )]

        decision = attribute_global_issues(issues, **_attribution_inputs(inputs))

        self.assertFalse(decision.retryable)
        self.assertEqual(decision.retry_unit_ids, ())
        self.assertEqual(decision.issues[0].retry_unit_ids, (SHARED_UNIT,))


if __name__ == "__main__":
    unittest.main()
