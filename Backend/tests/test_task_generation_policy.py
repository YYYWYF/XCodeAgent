"""验证每轮重规划与页面逐接口跳过规则，不调用模型或执行工作区任务。"""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.build_task_reuse_contracts import ReuseFacts, RetainedEndpointOwner
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.retained_task_projection import project_retained_tasks
from app.services.task_generation_policy import (
    completed_backend_units, completed_page_backend_endpoints, generation_reuse_facts,
)
from app.services.unit_generation_requirements import resolve_generation_requirements
from tests.dag_planning_baseline_fixtures import project_plan


class TaskGenerationPolicyTests(unittest.TestCase):
    """覆盖配置复用、旧任务退出及同模块部分接口完成的边界。"""

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
        inputs["required_unit_ids"] = ["backend:module:orders-api"]
        result = resolve_generation_requirements(
            **inputs, completed_backend_endpoints=[("orders-api", "orders.list")],
        )
        self.assertTrue(result.generation_requirements_by_unit["backend:module:orders-api"])

    def test_all_page_endpoints_completed_keeps_frontend_generation(self):
        """页面接口全部完成时不生成模块候选，前端页面及 API 调用仍生成。"""
        result = resolve_generation_requirements(
            **self.inputs(), completed_backend_endpoints=[
                ("orders-api", "orders.list"), ("orders-api", "orders.detail"),
            ],
        )
        self.assertEqual(result.generation_strategy_by_unit["backend:module:orders-api"], "not_required")
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
        kept = project_retained_tasks(tasks, project_plan={}, build_context={},
                                      reuse_facts=self.inputs()["reuse_facts"], planning_unit_ids=set())
        self.assertEqual([task["id"] for task in kept], ["config"])
