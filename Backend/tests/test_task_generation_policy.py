"""验证每轮重规划与页面逐接口跳过规则，不调用模型或执行工作区任务。"""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.build_task_reuse_contracts import ReuseFacts, RetainedEndpointOwner
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.authorization_overlay import unit_authorization_slice
from app.services.build_task_planner import _authorization_coverage_errors
from app.services.build_unit_compiler import _unit_source_refs, _apply_unit_task_dependencies
from app.services.retained_task_projection import project_retained_tasks
from app.services.task_generation_policy import (
    completed_backend_units, completed_page_backend_endpoints, generation_reuse_facts,
)
from app.services.unit_generation_requirements import resolve_generation_requirements
from app.services.unit_generation_requirements_contracts import GenerationRequirementsError
from tests.dag_planning_baseline_fixtures import project_plan
from tests.dag_planning_orchestrator_fixtures import planning_inputs
from tests.test_build_task_reuse import _plan, _task


class TaskGenerationPolicyTests(unittest.TestCase):
    """覆盖配置复用、旧任务退出及同模块部分接口完成的边界。"""

    def test_old_units_outside_current_skeleton_do_not_block_planning(self):
        """旧页面、模块和静态单元不在当前骨架时仍可规划，且不带入旧任务。"""
        inputs = planning_inputs()
        baseline = _plan(*[_task(f"old-{index}", unit_id=unit) for index, unit in enumerate(
            ("page:removed", "backend:module:removed", "frontend:data:static"))])
        facts = resolve_reuse_facts(
            confirmed_plan=baseline, unit_skeleton=inputs.skeleton_plan,
            build_context=inputs.build_context, workspace_snapshot=inputs.workspace_snapshot,
            formal_plan=inputs.project_plan,
        )
        self.assertEqual(facts.issues, ())
        facts = facts.model_copy(update={"external_capabilities": inputs.reuse_facts.external_capabilities})
        current = inputs.model_copy(update={"base_confirmed_plan": baseline, "reuse_facts": facts})
        requirements = current.requirements()
        self.assertEqual(requirements.planning_unit_ids, ("page:a", "page:b", "page:c"))
        run = current.create_run(requirements, planning_run_id="current", workflow_run_id="workflow",
                                 thread_id="thread", at="2026-11-11T00:00:00Z")
        self.assertTrue(all(not unit.retained_task_ids for unit in run.unit_states.values()))
        self.assertEqual(project_retained_tasks(
            list(baseline["task_registry"].values()), project_plan=current.project_plan,
            build_context=current.build_context, reuse_facts=facts,
            planning_unit_ids=set(requirements.planning_unit_ids),
        ), [])
        # 旧任务无需进入骨架，但完整基线覆盖及本轮必需单元门禁仍必须生效。
        invalid = current.model_copy(update={"reuse_facts": inputs.reuse_facts})
        with self.assertRaises(GenerationRequirementsError) as mismatch:
            invalid.requirements()
        self.assertEqual(mismatch.exception.issues[0].code, "PLANNING_REUSE_BASELINE_MISMATCH")
        skeleton = {**inputs.skeleton_plan, "build_units": {key: unit
                    for key, unit in inputs.skeleton_plan["build_units"].items() if key != "page:a"}}
        with self.assertRaises(GenerationRequirementsError) as missing:
            current.model_copy(update={"skeleton_plan": skeleton}).requirements()
        self.assertEqual(missing.exception.issues[0].code, "REQUIRED_UNIT_MISSING")

    def inputs(self):
        """构造同模块列表与详情接口，页面同时调用两者。"""
        plan = project_plan()
        contract = plan["api_contracts"][0]
        contract["endpoints"].append({**deepcopy(contract["endpoints"][0]),
                                      "id": "orders.detail", "path": "/orders/{id}"})
        plan["page_implementation_contracts"][0]["requiredEndpointIds"].append("orders.detail")
        facts = ReuseFacts(retained_task_ids_by_unit={}, reusable_capabilities_by_unit={},
                           retained_endpoint_owners=(), external_capabilities=())
        return {
            "required_unit_ids": ["page:orders", "frontend:api-client", "backend:bootstrap",
                                  "backend:module:orders-api"],
            "build_execution_scope": {"type": "page", "targetId": "orders"},
            "unit_skeleton": ensure_build_unit_skeleton(plan, {}),
            "reuse_facts": facts, "formal_target": plan,
            "endpoint_designs": [{
                "apiContractId": "orders-api", "endpointId": endpoint["id"],
                "fieldMappings": [], "databaseWrites": [], "externalApiBindings": [],
                "sourceBinding": {"sourceType": "database"},
            } for endpoint in contract["endpoints"]],
        }

    def completed_context(self):
        """构造页面同时调用已完成 A 和待开发 B 的冻结上下文。"""
        return {
            "target": {"type": "page", "id": "orders"},
            "required_unit_ids": ["backend:module:orders-api"],
            "endpoint_designs": self.inputs()["endpoint_designs"],
            "completed_backend_endpoints": [
                {"api_contract_id": "orders-api", "endpoint_id": "orders.list"},
            ],
            "authorization_constraints": {"endpoints": [
                {"apiContractId": "orders-api", "endpointId": endpoint,
                 "operationResourceKeys": ["orders.read"]}
                for endpoint in ("orders.list", "orders.detail")
            ]},
        }

    def test_authorization_requires_only_current_backend_controller(self):
        """已完成 A 无需 Controller，未完成 B 缺失时仍失败，显式开发 A 仍校验。"""
        context = self.completed_context()
        task = {"id": "controller", "unit_id": "backend:module:orders-api", "owner": "backend",
                "deliverables": [{"kind": "backend.endpoint_controller", "target_id": "orders.detail"}]}
        self.assertEqual(_authorization_coverage_errors([task], context), [])
        errors = _authorization_coverage_errors([], context)
        self.assertEqual(len(errors), 1)
        self.assertIn("orders.detail", errors[0])
        context["target"] = {"type": "endpoint"}
        self.assertIn("orders.list", _authorization_coverage_errors([task], context)[0])

    def test_module_inputs_exclude_completed_backend_but_page_keeps_it(self):
        """后端设计及权限只含 B，页面仍保留 A/B 的完整调用上下文。"""
        context = self.completed_context()
        refs = _unit_source_refs("backend:module:orders-api", {}, context)
        self.assertEqual(refs["endpoint_ids"], ["orders.detail"])
        authorization = unit_authorization_slice("backend:module:orders-api", context)
        self.assertEqual([item["endpointId"] for item in authorization["endpoints"]], ["orders.detail"])
        page = _unit_source_refs("page:orders", {}, context)
        self.assertEqual(page["endpoint_designs"], context["endpoint_designs"])
        context["completed_backend_endpoints"].append(
            {"api_contract_id": "orders-api", "endpoint_id": "orders.detail"})
        self.assertEqual(_authorization_coverage_errors([], context), [])

    def test_all_completed_module_does_not_create_missing_execution_dependency(self):
        """全部接口完成时，页面不会要求不存在的模块任务，也不会补入旧任务。"""
        context = self.completed_context()
        context["completed_backend_endpoints"].append(
            {"api_contract_id": "orders-api", "endpoint_id": "orders.detail"})
        graph = {"edges": [{"from": "backend:module:orders-api", "to": "page:orders", "type": "depends_on"}]}
        tasks = [{"id": "page", "unit_id": "page:orders"}]
        compiled = _apply_unit_task_dependencies(tasks, {"backend:module:orders-api": {}}, graph, context)
        self.assertEqual(compiled[0]["missing_unit_dependencies"], [])
        self.assertEqual(compiled[0]["dependencies"], [])

    def test_only_bootstrap_capabilities_reduce_generation(self):
        """即使旧任务覆盖所有职责，新一轮仍生成全部非配置任务。"""
        inputs = self.inputs()
        duties = resolve_generation_requirements(**inputs).generation_requirements_by_unit
        facts = ReuseFacts(
            retained_task_ids_by_unit={unit: [unit + ":old"] for unit in duties},
            reusable_capabilities_by_unit={
                unit: {item.requirement_id: [unit + ":old"] for item in items}
                for unit, items in duties.items()
            },
            retained_endpoint_owners=[RetainedEndpointOwner(
                api_contract_id="orders-api", endpoint_id="orders.list",
                owner_task_id="frontend:api-client:old", owner_unit_id="frontend:api-client",
            )], external_capabilities=(),
        )
        inputs["reuse_facts"] = generation_reuse_facts(facts)
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(result.generation_strategy_by_unit["backend:bootstrap"], "reuse_only")
        for unit in ("page:orders", "frontend:api-client", "backend:module:orders-api"):
            self.assertEqual(result.generation_requirements_by_unit[unit], duties[unit])
        self.assertEqual(inputs["reuse_facts"].retained_endpoint_owners, ())
        self.assertTrue(facts.retained_endpoint_owners)

    def test_page_skips_completed_a_and_generates_b_in_same_module(self):
        """页面只跳过已完成 A 的后端职责，B 与前端 A/B 调用仍生成。"""
        inputs = self.inputs()
        result = resolve_generation_requirements(
            **inputs, completed_backend_endpoints=[("orders-api", "orders.list")],
        )
        backend = result.generation_requirements_by_unit["backend:module:orders-api"]
        self.assertEqual({item.source_refs["endpoint_id"] for item in backend}, {"orders.detail"})
        self.assertEqual({item.source_refs["kind"] for item in backend}, {
            "backend.foundation", "backend.objects", "backend.repository",
            "backend.application_service", "backend.endpoint_controller",
        })
        frontend = result.generation_requirements_by_unit["frontend:api-client"]
        self.assertEqual({item.source_refs["endpoint_id"] for item in frontend},
                         {"orders.list", "orders.detail"})

    def test_explicit_endpoint_development_still_generates_completed_endpoint(self):
        """完成接口跳过仅作用于页面，显式开发接口仍重新规划。"""
        inputs = self.inputs()
        inputs["build_execution_scope"] = {
            "type": "endpoint", "targetId": "orders.list", "apiContractId": "orders-api",
        }
        inputs["required_unit_ids"] = ["backend:bootstrap", "backend:module:orders-api"]
        result = resolve_generation_requirements(
            **inputs, completed_backend_endpoints=[("orders-api", "orders.list")],
        )
        self.assertTrue(result.generation_requirements_by_unit["backend:module:orders-api"])
        self.assertTrue(result.generation_requirements_by_unit["backend:bootstrap"])

    def test_all_page_endpoints_completed_keeps_frontend_generation(self):
        """页面接口全部完成时不生成模块候选，前端页面及 API 调用仍生成。"""
        result = resolve_generation_requirements(
            **self.inputs(), completed_backend_endpoints=[
                ("orders-api", "orders.list"), ("orders-api", "orders.detail"),
            ],
        )
        self.assertEqual(result.generation_strategy_by_unit["backend:module:orders-api"], "not_required")
        self.assertEqual(result.generation_strategy_by_unit["backend:bootstrap"], "not_required")
        self.assertEqual(result.generation_strategy_by_unit["frontend:api-client"], "model")
        self.assertEqual(result.generation_strategy_by_unit["page:orders"], "model")

    def test_lifecycle_completion_is_per_contract_and_requires_no_change(self):
        """任务完成或同名接口不足以跳过，必须有精确且无变更的产物完成事实。"""
        lifecycle = SimpleNamespace(development_artifacts=SimpleNamespace(endpoints={
            "orders-api": {
                "orders.list": SimpleNamespace(initial_development_status="completed", change_status="unchanged"),
                "orders.detail": SimpleNamespace(initial_development_status="completed", change_status="changed"),
            },
        }))
        with patch("app.services.application_lifecycle.load_application_lifecycle", return_value=lifecycle):
            completed = completed_page_backend_endpoints("unused", [
                ("orders-api", "orders.list"), ("orders-api", "orders.detail"), ("other", "orders.list"),
            ])
        self.assertEqual(completed, [{"api_contract_id": "orders-api", "endpoint_id": "orders.list"}])
        context = {"target": {"type": "page"}, "completed_backend_endpoints": completed,
                   "endpoint_designs": self.inputs()["endpoint_designs"]}
        self.assertEqual(completed_backend_units(context), set())
        context["endpoint_designs"] = context["endpoint_designs"][:1]
        self.assertEqual(completed_backend_units(context), {"backend:module:orders-api"})

    def test_new_plan_only_retains_configuration_tasks(self):
        """已完成业务任务不以完成状态或旧依赖进入新计划。"""
        tasks = [{"id": "config", "unit_id": "backend:bootstrap", "status": "completed"},
                 {"id": "business", "unit_id": "backend:module:orders-api", "status": "completed"}]
        context = self.completed_context()
        context["required_unit_ids"].append("backend:bootstrap")
        kept = project_retained_tasks(tasks, project_plan={}, build_context=context,
                                      reuse_facts=self.inputs()["reuse_facts"], planning_unit_ids=set())
        self.assertEqual([task["id"] for task in kept], ["config"])

    def test_no_backend_work_drops_retained_configuration(self):
        """纯页面、全部后端已完成及纯静态接口均不能因旧配置任务存在而带入它。"""
        active = self.completed_context()
        active["required_unit_ids"].append("backend:bootstrap")
        completed = deepcopy(active)
        completed["completed_backend_endpoints"].append(
            {"api_contract_id": "orders-api", "endpoint_id": "orders.detail"})
        static = deepcopy(active)
        static["endpoint_designs"][1]["sourceBinding"] = {"sourceType": "static"}
        for context in ({}, {**active, "endpoint_designs": []}, completed, static):
            with self.subTest(context=context):
                kept = project_retained_tasks(
                    [{"id": "config", "unit_id": "backend:bootstrap", "status": "completed"}],
                    project_plan={}, build_context=context,
                    reuse_facts=self.inputs()["reuse_facts"], planning_unit_ids=set(),
                )
                self.assertEqual(kept, [])

    def test_bootstrap_uses_only_unfinished_backend_sources(self):
        """已完成数据库接口不触发配置，未完成外部接口仍要求对应配置。"""
        inputs = self.inputs()
        inputs["endpoint_designs"][1]["sourceBinding"] = {"sourceType": "external_api"}
        result = resolve_generation_requirements(
            **inputs, completed_backend_endpoints=[("orders-api", "orders.list")],
        )
        duties = result.generation_requirements_by_unit["backend:bootstrap"]
        self.assertEqual([item.source_refs["data_source_type"] for item in duties], ["external_api"])

    def test_page_without_endpoints_does_not_generate_bootstrap(self):
        """页面没有接口依赖时，即使骨架存在配置单元也不生成配置职责。"""
        inputs = self.inputs()
        inputs["formal_target"]["page_implementation_contracts"][0]["requiredEndpointIds"] = []
        inputs["required_unit_ids"] = ["page:orders", "backend:bootstrap"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(result.generation_strategy_by_unit["backend:bootstrap"], "not_required")
        self.assertEqual(result.planning_unit_ids, ("page:orders",))
