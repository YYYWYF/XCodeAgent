"""T4.2 append-only Scope Assembly 的累计任务、冲突与合同保留测试。"""

from copy import deepcopy
import unittest

from app.services.authorization_capability_dependency import (
    AUTH_GUARD_UNIT_ID,
    current_auth_resource_capability,
)
from app.services.build_task_reuse_contracts import ExternalCapability, ReuseFacts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.planning_frozen import plain_json
from app.services.scope_assembly import ScopeAssemblyError, assemble_scope_build_task_plan
from app.services.unit_generation_contracts import (
    AttemptIdentity,
    CandidateAttempt,
    GenerationRequirement,
)
from tests.dag_planning_baseline_fixtures import (
    build_context,
    confirmed_baseline,
    execution_scope,
    project_plan,
    task,
    workspace_snapshot,
)


SHARED_UNIT = "frontend:api-client"


def _candidate(unit_id: str, tasks: list[dict], marker: str = "a") -> CandidateAttempt:
    """构造一个已有平台身份且通过 Local Validation 的当前 Candidate。"""

    return CandidateAttempt(
        candidate_id=f"candidate-{marker * 32}",
        identity=AttemptIdentity(
            planning_run_id="planning-run-scope-assembly",
            unit_id=unit_id,
            generation_round=1,
            attempt_in_round=1,
            attempt_id=f"attempt-{marker * 32}",
        ),
        input_fingerprint=f"input-{unit_id}",
        status="valid",
        tasks=tasks,
        validation_issues=(),
        generation_metadata={"source": "test"},
    )


def _requirement(unit_id: str) -> tuple[GenerationRequirement, ...]:
    """为一个待生成 Unit 创建非空 generation requirement。"""

    return (
        GenerationRequirement(
            requirement_id=f"requirement:{unit_id}",
            description=f"补齐 {unit_id} 当前职责",
            source_refs={"unit_id": unit_id},
        ),
    )


def _reuse_facts(plan: dict) -> ReuseFacts:
    """从测试 confirmed registry 建立与正式任务精确一致的最小 ReuseFacts。"""

    retained: dict[str, list[str]] = {}
    for task_id, retained_task in plan["task_registry"].items():
        retained.setdefault(retained_task["unit_id"], []).append(task_id)
    return ReuseFacts(
        retained_task_ids_by_unit=retained,
        reusable_capabilities_by_unit={},
        retained_endpoint_owners=(),
        external_capabilities=(),
    )


def _customer_api_task(task_id: str = "customers:api-current") -> dict:
    """创建追加到共享 API Client Unit 的当前客户接口任务。"""

    candidate = task(
        task_id,
        SHARED_UNIT,
        "frontend.api_module",
        "frontend/src/apis/customers.ts",
        "customers.list",
        dependencies=("api:adapter",),
    )
    candidate["task_type"] = "frontend.code"
    return candidate


def _base_inputs() -> dict:
    """建立包含 orders 正式历史和 customers 当前 Scope 的真实编译输入。"""

    plan = project_plan()
    baseline = confirmed_baseline(plan, execution_scope())
    context = build_context(plan, execution_scope(name="customers"))
    skeleton = ensure_build_unit_skeleton(plan, workspace_snapshot(), baseline)
    candidate = _candidate(SHARED_UNIT, [_customer_api_task()])
    return {
        "base_confirmed_plan": baseline,
        "skeleton_plan": skeleton,
        "project_plan": plan,
        "build_context": context,
        "reuse_facts": _reuse_facts(baseline),
        "generation_requirements_by_unit": {SHARED_UNIT: _requirement(SHARED_UNIT)},
        "candidates_by_unit": {SHARED_UNIT: candidate},
    }


def _add_retained_auth_provider(
    baseline: dict, task_id: str, capability: str, *, status: str = "pending"
) -> None:
    """向 confirmed 夹具追加一个字段完整的历史 auth capability provider。"""

    provider = deepcopy(baseline["task_registry"]["orders:api"])
    provider.update({
        "id": task_id,
        "unit_id": AUTH_GUARD_UNIT_ID,
        "dependencies": [],
        "status": status,
        "provides_capabilities": [capability],
    })
    baseline["task_registry"][task_id] = provider
    baseline["build_units"][AUTH_GUARD_UNIT_ID]["task_ids"].append(task_id)
    baseline["task_graph"]["nodes"].append(task_id)
    baseline["task_graph"]["topological_order"].insert(0, task_id)


def _auth_inputs(*, providers: tuple[tuple[str, str, str], ...] = ()) -> tuple[dict, str, str]:
    """构造当前 R2 页面 Scope，并可追加不同版本及状态的历史 provider。"""

    inputs = _base_inputs()
    inputs["project_plan"]["authorization_manifest"] = {
        "enabled": True,
        "resources": [
            {
                "resourceKey": "customers",
                "type": "page",
                "targetResourceRef": "page:customers",
            },
            {
                "resourceKey": "customers_list",
                "type": "operation",
                "targetResourceRef": "action:customers:list",
            },
        ],
    }
    capability_r1 = "frontend.auth.resources:R1"
    capability_r2 = current_auth_resource_capability(inputs["project_plan"])
    if capability_r2 is None:
        raise AssertionError("当前权限目录必须生成 R2 capability。")
    for task_id, capability, status in providers:
        _add_retained_auth_provider(
            inputs["base_confirmed_plan"], task_id, capability, status=status
        )
    inputs["reuse_facts"] = _reuse_facts(inputs["base_confirmed_plan"])
    inputs["skeleton_plan"]["unit_graph"]["edges"].append({
        "from": AUTH_GUARD_UNIT_ID,
        "to": "page:customers",
        "type": "depends_on",
    })
    page_task = task(
        "customers:page-current",
        "page:customers",
        "frontend.page",
        "frontend/src/pages/Customers/index.tsx",
        "customers",
    )
    inputs["generation_requirements_by_unit"] = {
        "page:customers": _requirement("page:customers")
    }
    inputs["candidates_by_unit"] = {
        "page:customers": _candidate("page:customers", [page_task], "d")
    }
    return inputs, capability_r1, capability_r2


class ScopeAssemblyTests(unittest.TestCase):
    def test_assembled_draft_never_claims_confirmation_lifecycle_status(self) -> None:
        """无论图是否 ready，Assembly 都不能继承 confirmed 或提前声称正式 pending。"""

        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                inputs = _base_inputs()
                if blocked:
                    candidate_task = _customer_api_task()
                    candidate_task["dependencies"] = ["missing-task"]
                    inputs["candidates_by_unit"] = {
                        SHARED_UNIT: _candidate(SHARED_UNIT, [candidate_task])
                    }

                result = assemble_scope_build_task_plan(**inputs)

                self.assertEqual(
                    result.assembled_plan["status"],
                    "blocked" if blocked else "ready",
                )
                self.assertNotIn("confirmation_status", result.assembled_plan)
                self.assertNotIn("confirmed_at", result.assembled_plan)
                self.assertEqual(
                    inputs["base_confirmed_plan"]["confirmation_status"], "confirmed"
                )

    def test_shared_unit_retains_history_and_appends_current_candidate(self) -> None:
        """共享 Unit 同时保留正式职责和本轮新增职责，并输出完整来源索引。"""

        inputs = _base_inputs()
        result = assemble_scope_build_task_plan(**inputs)
        registry = result.assembled_plan["task_registry"]

        self.assertEqual(set(result.retained_task_ids), set(inputs["base_confirmed_plan"]["task_registry"]))
        self.assertEqual(result.candidate_task_ids, ("customers:api-current",))
        self.assertEqual(set(registry), set(result.retained_task_ids) | set(result.candidate_task_ids))
        self.assertTrue(all(result.task_origins[task_id] == "retained" for task_id in result.retained_task_ids))
        self.assertEqual(result.task_origins["customers:api-current"], "candidate")
        self.assertEqual(result.candidate_unit_by_task_id, {"customers:api-current": SHARED_UNIT})

    def test_candidate_retained_id_collision_fails_before_registry_rebuild(self) -> None:
        """Candidate 撞正式 Task ID 时归因当前 Unit，且不 rename 或覆盖历史任务。"""

        inputs = _base_inputs()
        inputs["candidates_by_unit"] = {SHARED_UNIT: _candidate(
            SHARED_UNIT,
            [_customer_api_task("api:adapter")],
        )}
        before = deepcopy(inputs["base_confirmed_plan"])

        with self.assertRaises(ScopeAssemblyError) as raised:
            assemble_scope_build_task_plan(**inputs)

        issue = raised.exception.issues[0]
        self.assertEqual(issue.code, "GLOBAL_TASK_ID_COLLISION")
        self.assertEqual(issue.task_ids, ("api:adapter",))
        self.assertEqual(issue.retry_unit_ids, (SHARED_UNIT,))
        self.assertEqual(inputs["base_confirmed_plan"], before)

    def test_candidate_candidate_id_collision_is_not_silently_resolved(self) -> None:
        """不同 Unit Candidate 的同名 Task 冲突必须失败，不能任选、覆盖或批量 rename。"""

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
            "page:customers": _candidate("page:customers", [tasks_by_unit["page:customers"]], "c"),
        }

        with self.assertRaises(ScopeAssemblyError) as raised:
            assemble_scope_build_task_plan(**inputs)

        issue = raised.exception.issues[0]
        self.assertEqual(issue.code, "GLOBAL_TASK_ID_COLLISION")
        self.assertEqual(issue.task_ids, ("candidate-collision",))
        self.assertEqual(set(issue.unit_ids), set(tasks_by_unit))
        self.assertFalse(issue.retryable)

    def test_confirmed_plan_and_other_inputs_are_not_modified(self) -> None:
        """Assembly 仅操作深复制输入，正式计划、骨架、Context、Facts 和 Candidate 均不变。"""

        inputs = _base_inputs()
        snapshots = {
            "base_confirmed_plan": deepcopy(inputs["base_confirmed_plan"]),
            "skeleton_plan": deepcopy(inputs["skeleton_plan"]),
            "project_plan": deepcopy(inputs["project_plan"]),
            "build_context": deepcopy(inputs["build_context"]),
            "reuse_facts": inputs["reuse_facts"].model_dump(mode="json"),
            "candidates_by_unit": {
                unit_id: candidate.model_dump(mode="json")
                for unit_id, candidate in inputs["candidates_by_unit"].items()
            },
        }

        assemble_scope_build_task_plan(**inputs)

        for name in ("base_confirmed_plan", "skeleton_plan", "project_plan", "build_context"):
            self.assertEqual(inputs[name], snapshots[name])
        self.assertEqual(inputs["reuse_facts"].model_dump(mode="json"), snapshots["reuse_facts"])
        self.assertEqual(
            {unit_id: candidate.model_dump(mode="json") for unit_id, candidate in inputs["candidates_by_unit"].items()},
            snapshots["candidates_by_unit"],
        )

    def test_build_unit_task_ids_are_cumulative(self) -> None:
        """重建 build_units 时同一 Unit 的 task_ids 必须包含 retained 与 Candidate 全集。"""

        inputs = _base_inputs()
        result = assemble_scope_build_task_plan(**inputs)
        shared_ids = set(result.assembled_plan["build_units"][SHARED_UNIT]["task_ids"])
        expected_retained = {
            task_id
            for task_id, retained_task in inputs["base_confirmed_plan"]["task_registry"].items()
            if retained_task["unit_id"] == SHARED_UNIT
        }

        self.assertEqual(shared_ids, expected_retained | {"customers:api-current"})

    def test_retained_acceptance_contract_is_preserved_verbatim(self) -> None:
        """正式 Task 的工程与业务 acceptance 保留历史附加字段，不按当前 Scope 重生成。"""

        inputs = _base_inputs()
        retained = inputs["base_confirmed_plan"]["task_registry"]["orders:api"]
        retained["acceptance_checks"][0]["historical_marker"] = {"revision": "confirmed-r1"}
        retained["business_acceptance_checks"][0]["historical_marker"] = {"revision": "confirmed-r1"}
        inputs["reuse_facts"] = _reuse_facts(inputs["base_confirmed_plan"])
        expected_engineering = deepcopy(retained["acceptance_checks"])
        expected_business = deepcopy(retained["business_acceptance_checks"])

        result = assemble_scope_build_task_plan(**inputs)
        assembled_retained = result.assembled_plan["task_registry"]["orders:api"]

        self.assertEqual(plain_json(assembled_retained["acceptance_checks"]), expected_engineering)
        self.assertEqual(plain_json(assembled_retained["business_acceptance_checks"]), expected_business)

    def test_candidate_acceptance_is_compiled_from_current_contracts(self) -> None:
        """Candidate 自带的伪造检查被丢弃，并从当前正式合同编译工程与业务 acceptance。"""

        inputs = _base_inputs()
        candidate_task = _customer_api_task()
        candidate_task["acceptance_checks"] = [{"id": "forged-engineering"}]
        candidate_task["business_acceptance_checks"] = [{"id": "forged-business"}]
        inputs["candidates_by_unit"] = {SHARED_UNIT: _candidate(SHARED_UNIT, [candidate_task])}

        result = assemble_scope_build_task_plan(**inputs)
        assembled_candidate = result.assembled_plan["task_registry"]["customers:api-current"]

        self.assertTrue(assembled_candidate["acceptance_checks"])
        self.assertTrue(assembled_candidate["business_acceptance_checks"])
        self.assertNotIn("forged-engineering", {check["id"] for check in assembled_candidate["acceptance_checks"]})
        self.assertNotIn("forged-business", {check["id"] for check in assembled_candidate["business_acceptance_checks"]})

    def test_exact_duplicate_with_distinct_id_is_not_silently_merged(self) -> None:
        """结构完全相同但 ID 不同的 Candidate 与 retained Task 都进入累计 registry。"""

        inputs = _base_inputs()
        duplicate = deepcopy(inputs["base_confirmed_plan"]["task_registry"]["orders:api"])
        duplicate["id"] = "orders:api-current-duplicate"
        inputs["candidates_by_unit"] = {SHARED_UNIT: _candidate(SHARED_UNIT, [duplicate])}

        result = assemble_scope_build_task_plan(**inputs)

        self.assertIn("orders:api", result.assembled_plan["task_registry"])
        self.assertIn("orders:api-current-duplicate", result.assembled_plan["task_registry"])
        self.assertEqual(result.candidate_task_ids, ("orders:api-current-duplicate",))

    def test_auth_r1_and_r2_provider_history_remains_append_only(self) -> None:
        """精确依赖编译不得从累计 auth-guard registry 删除旧 R1 provider。"""

        inputs, capability_r1, capability_r2 = _auth_inputs()
        _add_retained_auth_provider(inputs["base_confirmed_plan"], "auth-r1", capability_r1)
        _add_retained_auth_provider(inputs["base_confirmed_plan"], "auth-r2", capability_r2)
        inputs["reuse_facts"] = _reuse_facts(inputs["base_confirmed_plan"])

        registry = assemble_scope_build_task_plan(**inputs).assembled_plan["task_registry"]

        self.assertEqual(registry["auth-r1"]["provides_capabilities"], (capability_r1,))
        self.assertEqual(registry["auth-r2"]["provides_capabilities"], (capability_r2,))

    def test_current_page_depends_only_on_exact_r2_provider(self) -> None:
        """Page 需要当前 R2 时只编译 R2 provider Task，不继承整个 auth Unit 历史。"""

        inputs, capability_r1, capability_r2 = _auth_inputs()
        for task_id, capability in (("auth-r1", capability_r1), ("auth-r2", capability_r2)):
            _add_retained_auth_provider(inputs["base_confirmed_plan"], task_id, capability)
        inputs["reuse_facts"] = _reuse_facts(inputs["base_confirmed_plan"])

        page = assemble_scope_build_task_plan(**inputs).assembled_plan["task_registry"]["customers:page-current"]

        self.assertIn("auth-r2", page["dependencies"])
        self.assertNotIn("auth-r1", page["dependencies"])
        self.assertEqual(page["requires_capabilities"], (capability_r2,))

    def test_external_r2_satisfaction_creates_no_task_dependency(self) -> None:
        """ReuseFacts 明示 workspace 已满足当前 R2 时，Page 保留 capability 但无 provider 边。"""

        inputs, capability_r1, capability_r2 = _auth_inputs()
        _add_retained_auth_provider(inputs["base_confirmed_plan"], "auth-r1", capability_r1)
        facts = _reuse_facts(inputs["base_confirmed_plan"])
        inputs["reuse_facts"] = facts.model_copy(update={
            "external_capabilities": [ExternalCapability(
                unit_id=AUTH_GUARD_UNIT_ID,
                capability_id=capability_r2,
                source="authorization_resource_catalog",
                workspace_revision="scope-r2",
                source_refs={"resource_catalog_fingerprint": capability_r2.rsplit(":", 1)[1]},
            )]
        })

        page = assemble_scope_build_task_plan(**inputs).assembled_plan["task_registry"]["customers:page-current"]

        self.assertNotIn("auth-r1", page["dependencies"])
        self.assertEqual(page["requires_capabilities"], (capability_r2,))

    def test_missing_r2_provider_is_a_global_issue(self) -> None:
        """当前 R2 无 provider 且未 external satisfied 时，Assembly 必须显式失败。"""

        inputs, _, capability_r2 = _auth_inputs()

        with self.assertRaises(ScopeAssemblyError) as raised:
            assemble_scope_build_task_plan(**inputs)

        issue = raised.exception.issues[0]
        self.assertEqual(issue.code, "GLOBAL_AUTH_CAPABILITY_PROVIDER_MISSING")
        self.assertEqual(issue.level, "global")
        self.assertEqual(issue.details["capability_id"], capability_r2)

    def test_duplicate_r2_providers_are_a_global_conflict(self) -> None:
        """同一 R2 的多个 provider 不能按顺序任选其一。"""

        inputs, _, capability_r2 = _auth_inputs()
        for task_id in ("auth-r2-a", "auth-r2-b"):
            _add_retained_auth_provider(inputs["base_confirmed_plan"], task_id, capability_r2)
        inputs["reuse_facts"] = _reuse_facts(inputs["base_confirmed_plan"])

        with self.assertRaises(ScopeAssemblyError) as raised:
            assemble_scope_build_task_plan(**inputs)

        issue = raised.exception.issues[0]
        self.assertEqual(issue.code, "GLOBAL_AUTH_CAPABILITY_PROVIDER_CONFLICT")
        self.assertEqual(set(issue.task_ids), {"auth-r2-a", "auth-r2-b"})
        self.assertFalse(issue.retryable)

    def test_failed_r1_does_not_block_page_that_requires_r2(self) -> None:
        """历史 R1 的执行失败状态不应成为当前 R2 Page 的拓扑依赖。"""

        inputs, capability_r1, capability_r2 = _auth_inputs()
        _add_retained_auth_provider(
            inputs["base_confirmed_plan"], "auth-r1-failed", capability_r1, status="failed"
        )
        _add_retained_auth_provider(inputs["base_confirmed_plan"], "auth-r2", capability_r2)
        inputs["reuse_facts"] = _reuse_facts(inputs["base_confirmed_plan"])

        result = assemble_scope_build_task_plan(**inputs)
        page = result.assembled_plan["task_registry"]["customers:page-current"]

        self.assertEqual(page["dependencies"].count("auth-r2"), 1)
        self.assertNotIn("auth-r1-failed", page["dependencies"])
        self.assertEqual(result.assembled_plan["execution"]["blocked_batches"], ())


if __name__ == "__main__":
    unittest.main()
