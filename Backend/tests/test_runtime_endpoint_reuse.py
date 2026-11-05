"""覆盖智能体工具接口转页面使用时的复用、失效和原文件归属。"""

from copy import deepcopy
import unittest

from app.services.build_unit_compiler import apply_unit_compilation
from app.services.business_acceptance import compile_business_acceptance
from app.services.task_input_freshness import task_covers_current_input
from app.services.task_input_freshness import task_input_change_reason
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.unit_generation_requirements import resolve_generation_requirements
from app.services.planning_run_contracts import UnitRunState
from app.services.planning_run_progress import _project_unit
from app.services.unit_candidate_task_rules import _deliverable_issues
from tests.dag_planning_baseline_fixtures import project_plan, build_context, execution_scope, task
from tests.test_unit_candidate_validator import _context, _task


class RuntimeEndpointReuseTests(unittest.TestCase):
    """通过真实编译器验证接口身份不随使用入口变化。"""

    def setUp(self):
        """创建两个工具对应的实体及一个已完成业务接口。"""
        self.plan = project_plan()
        self.plan["topology"] = {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
            "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"}
        for api in self.plan["api_contracts"]:
            for endpoint in api["endpoints"]:
                endpoint["kind"] = "business"
        self.context = build_context(self.plan, execution_scope())
        self.context.update(project_plan=self.plan, entity_ids=["Order", "Customer"])
        original = task("orders:controller", "backend:endpoint:orders-api:orders.list",
            "backend.endpoint_controller", "agent-runtime/src/app/api/endpoints/orders.py", "orders.list")
        original["status"] = "completed"
        self.task = compile_business_acceptance(apply_unit_compilation({}, [original], self.context), self.context)[0]

    def test_completed_endpoint_capabilities_skip_model_generation(self):
        """四项正式交付覆盖新页面的后端职责，策略直接为复用，不再产生模型任务。"""
        unit_id = self.task["unit_id"]
        tasks = []
        for stage, kind, capability in (
            ("objects", "backend.objects", "backend.endpoint.objects"),
            ("repository", "backend.repository", "backend.repository"),
            ("service", "backend.application_service", "backend.application_service"),
            ("controller", "backend.endpoint_controller", "backend.endpoint_controller"),
        ):
            original = task(f"orders:{stage}", unit_id, kind,
                f"agent-runtime/src/app/{stage}/orders.py", "orders.list")
            original["status"] = "completed"
            original["deliverables"][0]["provides"] = [f"{capability}:orders-api:orders.list"]
            tasks.append(original)
        compiled = compile_business_acceptance(apply_unit_compilation({}, tasks, self.context), self.context)
        baseline = {"schema_version": "build-dag.v4", "confirmation_status": "confirmed", "status": "ready",
            "task_registry": {item["id"]: item for item in compiled}, "task_graph": {"validation": {"is_valid": True}}}
        skeleton = {"build_units": {unit_id: {"id": unit_id, "kind": "backend"}}}
        facts = resolve_reuse_facts(confirmed_plan=baseline, unit_skeleton=skeleton,
            build_context=self.context, workspace_snapshot={}, formal_plan=self.plan)
        self.assertEqual(facts.issues, ())
        requirements = resolve_generation_requirements(required_unit_ids=[unit_id],
            build_execution_scope=execution_scope(), unit_skeleton=skeleton, reuse_facts=facts,
            formal_target=self.plan, endpoint_designs=self.context["endpoint_designs"])
        self.assertEqual(requirements.generation_strategy_by_unit[unit_id], "reuse_only")
        self.assertEqual(requirements.planning_unit_ids, ())

    def test_agent_to_page_uses_only_own_contract_entities(self):
        """整体 Agent 输入含其他实体时，任务仍只冻结自身接口的实体并可跨入口复用。"""
        expected = self.task["business_acceptance_checks"][0]["expected"]
        self.assertEqual([item["entity_id"] for item in expected["entities"]], ["Order"])
        page_context = {**self.context, "entity_ids": ["Order"]}
        before = deepcopy(self.task)
        self.assertTrue(task_covers_current_input(self.task, self.plan, page_context))
        self.assertEqual(self.task, before)

    def test_method_and_entity_changes_invalidate_reuse(self):
        """同一身份的 GET 改 POST 或真正使用的实体变化均撤销旧完成证据。"""
        for change in ("method", "entity"):
            with self.subTest(change=change):
                plan = deepcopy(self.plan)
                if change == "method":
                    plan["api_contracts"][0]["endpoints"][0]["method"] = "POST"
                else:
                    plan["entities"][0]["fields"][0]["type"] = "integer"
                self.assertFalse(task_covers_current_input(self.task, plan, {**self.context, "project_plan": plan}))
                reason = task_input_change_reason(self.task, plan, {**self.context, "project_plan": plan})
                self.assertIn("接口契约" if change == "method" else "实体定义", reason)

    def test_unrelated_entity_change_keeps_reuse(self):
        """另一个工具的实体变化不能导致当前接口重新开发。"""
        plan = deepcopy(self.plan)
        plan["entities"][1]["fields"][0]["type"] = "integer"
        self.assertTrue(task_covers_current_input(self.task, plan, {**self.context, "project_plan": plan}))

    def test_regeneration_keeps_original_backend_file(self):
        """重新生成可以更新原文件，但拒绝同一职责的新模块路径。"""
        context = _context("backend:endpoint:orders-api:orders.list")
        candidate = _task(context.unit_id)
        payload = context.model_dump(mode="json")
        payload["constraints"]["existing_backend_deliverables"] = deepcopy(candidate["deliverables"])
        context = type(context).model_validate(payload)
        paths = set(candidate["target_files"])
        self.assertEqual(_deliverable_issues(candidate, context, paths)[1], [])
        candidate["deliverables"][0]["paths"] = ["backend/src/main/java/com/example/orders/SecondController.java"]
        issues = _deliverable_issues(candidate, context, set(candidate["deliverables"][0]["paths"]))[1]
        self.assertIn("CANDIDATE_EXISTING_IMPLEMENTATION_PATH_CHANGED", [issue.code for issue in issues])

    def test_reuse_reason_reaches_progress_projection(self):
        """复用原因随已有 AG-UI 进度快照展示，不引入额外请求。"""
        reason = "接口契约已变化，需在原实现文件上重新生成。"
        state = UnitRunState.create(unit_id=self.task["unit_id"], kind="backend",
            generation_strategy="model", reuse_reason=reason)
        self.assertEqual(_project_unit(state)["reuseReason"], reason)
