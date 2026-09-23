"""T4.2 append-only Scope Assembly 的累计任务、冲突与合同保留测试。"""

from copy import deepcopy
import unittest

from app.services.authorization_capability_dependency import (
    AUTH_GUARD_UNIT_ID,
    current_auth_resource_capability,
)
from app.services.build_task_reuse_contracts import (
    ExternalCapability,
    RetainedEndpointOwner,
    ReuseFacts,
)
from app.services.build_task_planner import (
    create_build_task_plan,
    frontend_endpoint_ownership_errors,
    retained_frontend_endpoint_owner_conflict_errors,
)
from app.services.business_acceptance import (
    canonical_frontend_api_module_path,
    frontend_api_task_id,
)
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
    candidate_tasks,
    confirmed_baseline,
    execution_scope,
    project_plan,
    task,
    workspace_snapshot,
)


SHARED_UNIT = "frontend:api-client"


def _candidate(unit_id: str, tasks: list[dict], marker: str = "a") -> CandidateAttempt:
    """构造一个已有平台身份且通过 Local Validation 的当前 Candidate。"""

    return CandidateAttempt.from_generated_attempt(
        candidate_id=f"candidate-{marker * 32}",
        attempt=AttemptIdentity(
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
    )
    candidate["task_type"] = "frontend.code"
    return candidate


def _api_requirement(endpoint_id: str) -> GenerationRequirement:
    """构造同一 API Contract 的当前增量 Endpoint requirement。"""

    return GenerationRequirement(
        requirement_id=f"frontend.api_module:orders-api:{endpoint_id}",
        description=f"实现订单 API Endpoint {endpoint_id}",
        source_refs={
            "artifact": "technical-plan",
            "kind": "frontend.api_module",
            "capability_id": f"frontend.api_module:orders-api:{endpoint_id}",
            "api_contract_id": "orders-api",
            "endpoint_id": endpoint_id,
        },
    )


def _canonicalize_api_candidate(
    candidate: dict, *, endpoint_ids: tuple[str, ...], task_id: str
) -> dict:
    """将测试 Candidate 的 API 交付物绑定到共享 canonical module。"""

    path = canonical_frontend_api_module_path("orders-api")
    candidate["id"] = task_id
    candidate.setdefault("source_refs", {})["endpoint_ids"] = list(endpoint_ids)
    candidate["target_files"] = [path]
    candidate["allowed_paths"] = [path]
    candidate["change_scope"] = [{
        "operation": "modify",
        "path": path,
        "description": "扩展订单 API canonical module",
    }]
    candidate["deliverables"][0].update({
        "id": f"{task_id}:deliverable",
        "paths": [path],
    })
    return candidate


def _canonical_confirmed_baseline() -> tuple[dict, dict, dict]:
    """建立已有 orders.list canonical API Task 的 confirmed DAG 夹具。"""

    plan = project_plan()
    scope = execution_scope()
    snapshot = workspace_snapshot()
    context = build_context(plan, scope)
    skeleton = ensure_build_unit_skeleton(plan, snapshot)
    generated = candidate_tasks(context)
    api_candidate = next(item for item in generated if item["unit_id"] == SHARED_UNIT)
    _canonicalize_api_candidate(
        api_candidate,
        endpoint_ids=("orders.list",),
        task_id=frontend_api_task_id(SHARED_UNIT, "orders-api", ("orders.list",)),
    )
    baseline = create_build_task_plan(
        plan,
        agent_plan={"tasks": generated},
        workspace_snapshot=snapshot,
        base_build_task_plan=skeleton,
        build_context=context,
        build_execution_scope=scope,
    )
    if baseline["status"] != "ready":
        raise AssertionError(baseline["task_graph"]["validation"]["errors"])
    baseline["confirmation_status"] = "confirmed"
    return baseline, plan, scope


def _base_inputs() -> dict:
    """建立包含 orders 正式历史和 customers 当前 Scope 的真实编译输入。"""

    plan = project_plan()
    baseline = confirmed_baseline(plan, execution_scope())
    current_scope = execution_scope(name="customers")
    context = build_context(plan, current_scope)
    skeleton = ensure_build_unit_skeleton(plan, workspace_snapshot(), baseline)
    candidate = _candidate(SHARED_UNIT, [_customer_api_task()])
    return {
        "base_confirmed_plan": baseline,
        "skeleton_plan": skeleton,
        "project_plan": plan,
        "product_plan": {"pages": [{"pageId": "orders", "name": "订单"}, {"pageId": "customers", "name": "客户"}]},
        "build_context": context,
        "build_execution_scope": current_scope,
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
    def test_assembly_never_appends_route_projector_task(self) -> None:
        """Route Projection 已是成功 DAG 的收尾步骤，Assembly 不得添加伪业务任务。"""

        inputs = _base_inputs()
        result = assemble_scope_build_task_plan(**inputs)
        assembled = plain_json(result.assembled_plan)

        self.assertNotIn("platform_route_projection", assembled["task_registry"])
        self.assertEqual(result.platform_task_ids, ())

    def test_new_page_does_not_append_route_projector_task(self) -> None:
        """页面规划只产生业务任务，路由由成功后的统一 Finalization 处理。"""

        first = plain_json(assemble_scope_build_task_plan(**_base_inputs()).assembled_plan)
        confirmed = {**first, "confirmation_status": "confirmed"}
        inputs = _base_inputs()
        inputs["base_confirmed_plan"] = confirmed
        inputs["reuse_facts"] = _reuse_facts(confirmed)
        page_unit = "page:customers"
        inputs["generation_requirements_by_unit"] = {
            page_unit: _requirement(page_unit)
        }
        inputs["candidates_by_unit"] = {
            page_unit: _candidate(page_unit, [task(
                "customers:page-current", page_unit, "frontend.page",
                "frontend/src/pages/Customers/index.tsx", "customers",
            )], "b")
        }

        result = assemble_scope_build_task_plan(**inputs)
        assembled = plain_json(result.assembled_plan)
        self.assertTrue(assembled["task_graph"]["validation"]["is_valid"])
        self.assertNotIn("platform_route_projection", assembled["task_graph"]["nodes"])
        self.assertNotIn("platform_route_projection", result.retained_task_ids)
        self.assertEqual(result.platform_task_ids, ())

    def test_assembly_does_not_persist_route_projection_pages(self) -> None:
        """DAG 只能表达平台动作，不能保存第二份页面事实。"""

        inputs = _base_inputs()
        assembled = plain_json(assemble_scope_build_task_plan(**inputs).assembled_plan)
        self.assertNotIn("route_projection", assembled)
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

    def test_template_context_is_promoted_from_current_build_context(self) -> None:
        """Scope Assembly 必须把本轮冻结的 V2 模板绑定原样提升到 Plan root。"""

        inputs = _base_inputs()
        inputs["base_confirmed_plan"]["template_context"]["template_revision"] = "confirmed-template-r0"
        expected = deepcopy(inputs["build_context"]["template_context"])

        result = assemble_scope_build_task_plan(**inputs)

        self.assertEqual(result.assembled_plan["template_context"], expected)

    def test_plan_root_metadata_binds_current_scope_and_drops_prior_lifecycle(self) -> None:
        """跨 Scope Assembly 只重绑当前 root metadata，不携带旧生命周期字段。"""

        inputs = _base_inputs()
        stale_root_metadata = {
            "confirmed_from": {
                "planning_run_id": "confirmed-endpoint-run",
                "draft_digest": "e" * 64,
            },
            "draft_identity": {"planning_run_id": "confirmed-endpoint-run"},
            "last_update": {
                "stage": "build_scheduler",
                "updated_at": "2026-09-04T00:00:00+00:00",
            },
        }
        inputs["base_confirmed_plan"].update(deepcopy(stale_root_metadata))
        inputs["skeleton_plan"].update(deepcopy(stale_root_metadata))

        result = assemble_scope_build_task_plan(**inputs)
        assembled = result.assembled_plan

        self.assertEqual(assembled["build_execution_scope"], inputs["build_execution_scope"])
        for field in (
            "confirmation_status",
            "confirmed_at",
            "confirmed_from",
            "draft_identity",
            "last_update",
        ):
            self.assertNotIn(field, assembled)
        self.assertEqual(
            assembled["task_registry"]["orders:api"]["status"],
            inputs["base_confirmed_plan"]["task_registry"]["orders:api"]["status"],
        )

    def test_missing_template_context_stops_scope_assembly(self) -> None:
        """缺少模板绑定时必须以输入问题终止 Assembly，不能返回部分 DAG。"""

        inputs = _base_inputs()
        inputs["build_context"].pop("template_context")

        with self.assertRaises(ScopeAssemblyError) as raised:
            assemble_scope_build_task_plan(**inputs)

        issue = raised.exception.issues[0]
        self.assertEqual(issue.code, "SCOPE_TEMPLATE_CONTEXT_INVALID")
        self.assertEqual(issue.category, "input")

    def test_malformed_template_context_stops_scope_assembly(self) -> None:
        """模板绑定结构非法时必须拒绝，不得继承 confirmed root 的旧值。"""

        inputs = _base_inputs()
        inputs["build_context"]["template_context"] = {
            **inputs["build_context"]["template_context"],
            "template_revision": "",
        }

        with self.assertRaises(ScopeAssemblyError) as raised:
            assemble_scope_build_task_plan(**inputs)

        self.assertEqual(raised.exception.issues[0].code, "SCOPE_TEMPLATE_CONTEXT_INVALID")

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

    def test_incremental_same_contract_appends_new_task_without_mutating_retained_api_task(self) -> None:
        """同 Contract 增量 Endpoint 使用新 Task ID、共享 canonical 文件并保留旧 Task。"""

        baseline, plan, scope = _canonical_confirmed_baseline()
        plan["api_contracts"][0]["endpoints"].append({
            "id": "orders.update",
            "method": "PUT",
            "path": "/orders/{id}",
            "request_schema_ref": "#/schemas/Order",
            "response_schema_ref": "#/schemas/Order",
            "parameters": [{
                "name": "id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }],
            "operation_semantics": {"operation_kind": "update"},
        })
        snapshot = workspace_snapshot()
        current_context = build_context(plan, scope)
        update_task_id = frontend_api_task_id(
            SHARED_UNIT, "orders-api", ("orders.update",)
        )
        update_task = task(
            update_task_id,
            SHARED_UNIT,
            "frontend.api_module",
            canonical_frontend_api_module_path("orders-api"),
            "orders.update",
        )
        update_task["task_type"] = "frontend.code"
        update_task.setdefault("source_refs", {})["endpoint_ids"] = ["orders.update"]
        facts = _reuse_facts(baseline).model_copy(update={
            "retained_endpoint_owners": (
                RetainedEndpointOwner(
                    api_contract_id="orders-api",
                    endpoint_id="orders.list",
                    owner_task_id=next(
                        task_id
                        for task_id, item in baseline["task_registry"].items()
                        if item.get("unit_id") == SHARED_UNIT
                    ),
                    owner_unit_id=SHARED_UNIT,
                ),
            ),
        })
        inputs = {
            "base_confirmed_plan": baseline,
            "skeleton_plan": ensure_build_unit_skeleton(plan, snapshot, baseline),
            "project_plan": plan,
            "product_plan": {
                "pages": [
                    {"pageId": "orders", "name": "订单"},
                    {"pageId": "customers", "name": "客户"},
                ]
            },
            "build_context": current_context,
            "build_execution_scope": scope,
            "reuse_facts": facts,
            "generation_requirements_by_unit": {
                SHARED_UNIT: (_api_requirement("orders.update"),),
            },
            "candidates_by_unit": {
                SHARED_UNIT: _candidate(SHARED_UNIT, [update_task], "b"),
            },
        }

        result = assemble_scope_build_task_plan(**inputs)
        registry = result.assembled_plan["task_registry"]
        retained_api_task_id = next(
            task_id for task_id, item in baseline["task_registry"].items()
            if item.get("unit_id") == SHARED_UNIT
        )

        self.assertIn(retained_api_task_id, registry)
        self.assertIn(update_task_id, registry)
        self.assertEqual(result.retained_task_ids.count(retained_api_task_id), 1)
        self.assertEqual(result.candidate_task_ids, (update_task_id,))
        self.assertEqual(
            registry[update_task_id]["deliverables"][0]["paths"],
            (canonical_frontend_api_module_path("orders-api"),),
        )
        self.assertEqual(
            registry[retained_api_task_id]["deliverables"][0]["paths"],
            (canonical_frontend_api_module_path("orders-api"),),
        )

    def test_endpoint_ownership_uses_contract_endpoint_not_shared_path(self) -> None:
        """不同 Endpoint 共用 API 文件时允许并存，但重复复合身份仍失败。"""

        def api_check(endpoint_id: str) -> dict:
            return {
                "kind": "frontend.api_contract",
                "expected": {
                    "endpoints": [{
                        "api_contract_id": "profile-api",
                        "endpoint_id": endpoint_id,
                    }]
                },
                "target_paths": ["frontend/src/apis/profileApi.ts"],
            }

        distinct_owners = [
            {
                "id": "profile-get",
                "unit_id": "frontend:api-client",
                "owner": "frontend",
                "business_acceptance_checks": [api_check("profile_api.get")],
            },
            {
                "id": "profile-update",
                "unit_id": "frontend:api-client",
                "owner": "frontend",
                "business_acceptance_checks": [api_check("profile_api.update")],
            },
        ]

        self.assertEqual(frontend_endpoint_ownership_errors(distinct_owners), [])

        duplicate_owner = [
            distinct_owners[0],
            {
                **distinct_owners[1],
                "id": "profile-get-duplicate",
                "business_acceptance_checks": [api_check("profile_api.get")],
            },
        ]
        errors = frontend_endpoint_ownership_errors(duplicate_owner)
        self.assertEqual(len(errors), 1)
        self.assertIn("profile-api + profile_api.get", errors[0])
        self.assertEqual(
            retained_frontend_endpoint_owner_conflict_errors(
                [distinct_owners[0]],
                [{
                    "api_contract_id": "profile-api",
                    "endpoint_id": "profile_api.get",
                    "owner_task_id": "profile-get",
                    "owner_unit_id": SHARED_UNIT,
                }],
            ),
            [],
        )

    def test_fully_reused_scope_records_review_and_reused_task_ids(self) -> None:
        """没有 Candidate 的当前 Scope 也必须在 Assembly 产出完整复用任务集合。"""

        inputs = _base_inputs()
        inputs["build_context"]["required_unit_ids"] = [SHARED_UNIT]
        inputs["generation_requirements_by_unit"] = {SHARED_UNIT: ()}
        inputs["candidates_by_unit"] = {}

        result = assemble_scope_build_task_plan(**inputs)

        self.assertEqual(result.candidate_task_ids, ())
        self.assertEqual(result.review_task_ids, ("orders:api",))
        self.assertEqual(result.reused_task_ids, ("orders:api",))
        self.assertTrue(all(
            result.task_origins[task_id] == "retained"
            for task_id in result.review_task_ids
        ))

    def test_candidate_retained_id_collision_fails_before_registry_rebuild(self) -> None:
        """Candidate 撞正式 Task ID 时归因当前 Unit，且不 rename 或覆盖历史任务。"""

        inputs = _base_inputs()
        inputs["candidates_by_unit"] = {SHARED_UNIT: _candidate(
            SHARED_UNIT,
            [_customer_api_task("orders:api")],
        )}
        before = deepcopy(inputs["base_confirmed_plan"])

        with self.assertRaises(ScopeAssemblyError) as raised:
            assemble_scope_build_task_plan(**inputs)

        issue = raised.exception.issues[0]
        self.assertEqual(issue.code, "GLOBAL_TASK_ID_COLLISION")
        self.assertEqual(issue.task_ids, ("orders:api",))
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
            "build_execution_scope": deepcopy(inputs["build_execution_scope"]),
            "reuse_facts": inputs["reuse_facts"].model_dump(mode="json"),
            "candidates_by_unit": {
                unit_id: candidate.model_dump(mode="json")
                for unit_id, candidate in inputs["candidates_by_unit"].items()
            },
        }

        assemble_scope_build_task_plan(**inputs)

        for name in (
            "base_confirmed_plan",
            "skeleton_plan",
            "project_plan",
            "build_context",
            "build_execution_scope",
        ):
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
