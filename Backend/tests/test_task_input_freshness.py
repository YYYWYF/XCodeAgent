"""用真实平台合同编译证明直接依赖变化会撤销旧覆盖，其他依赖不会被误伤。"""

from copy import deepcopy
import unittest

from app.services.task_input_freshness import task_covers_current_input
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from tests.dag_planning_baseline_fixtures import (
    project_plan, execution_scope, confirmed_baseline, build_context, workspace_snapshot,
)


class TaskInputFreshnessTests(unittest.TestCase):
    """只使用确定性正式产物和已编译任务，不调用或修改 Agent。"""

    def setUp(self):
        """创建订单页面及其接口已完成的真实任务合同。"""
        self.plan = project_plan()
        self.scope = execution_scope()
        self.baseline = confirmed_baseline(self.plan, self.scope)
        for task in self.baseline["task_registry"].values():
            task["status"] = "completed"

    def freshness(self, plan):
        """以当前直接输入计算每项历史任务的覆盖结果。"""
        context = build_context(plan, self.scope)
        return {key: task_covers_current_input(task, plan, context)
                for key, task in self.baseline["task_registry"].items()}

    def test_unchanged_inputs_keep_completed_tasks(self):
        """相同内容再次确认不要求重复实现已完成任务。"""
        self.assertTrue(all(self.freshness(self.plan).values()))

    def test_application_scope_does_not_make_unchanged_page_stale(self):
        """应用级任务编译与复用采用同一输入范围，不因重算时改变 target 而反复生成页面。"""
        from app.services.build_unit_compiler import apply_unit_compilation
        from app.services.business_acceptance import compile_business_acceptance
        context = build_context(self.plan, self.scope)
        context.update(target={"type": "application", "id": "application"}, project_plan=self.plan)
        context.pop("page_implementation_contract", None)
        tasks = apply_unit_compilation(self.baseline, list(self.baseline["task_registry"].values()), context)
        tasks = compile_business_acceptance(tasks, context)
        for task in tasks:
            self.assertTrue(task_covers_current_input(task, self.plan, context), task["id"])

    def test_query_default_change_invalidates_backend_client_and_page(self):
        """参数默认值是三类任务直接消费的接口合同，旧完成事实不能覆盖新值。"""
        changed = deepcopy(self.plan)
        changed["api_contracts"][0]["endpoints"][0]["parameters"] = [
            {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 13}},
        ]
        before = deepcopy(self.baseline)
        fresh = self.freshness(changed)
        for key in ("orders:controller", "orders:api", "orders:page"):
            self.assertFalse(fresh[key], key)
        self.assertTrue(fresh["backend:bootstrap::config"])
        context = build_context(changed, self.scope)
        facts = resolve_reuse_facts(confirmed_plan=self.baseline,
            unit_skeleton=ensure_build_unit_skeleton(changed, workspace_snapshot(), self.baseline),
            build_context=context, workspace_snapshot=workspace_snapshot(), formal_plan=changed)
        self.assertEqual(facts.retained_endpoint_owners, ())
        self.assertEqual(dict(facts.reusable_capabilities_by_unit["frontend:api-client"]), {})
        self.assertEqual(self.baseline, before)

    def test_other_endpoint_and_confirmation_metadata_do_not_invalidate(self):
        """客户接口变化及映射确认元数据变化，均不能扩大成订单任务失效。"""
        changed = deepcopy(self.plan)
        changed["api_contracts"][1]["endpoints"][0]["parameters"] = [
            {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 99}},
        ]
        self.assertTrue(all(self.freshness(changed).values()))
        context = build_context(self.plan, self.scope)
        for design in context["endpoint_designs"]:
            design.update(artifactRevision="new-confirmation", confirmedAt="2030-01-01T00:00:00Z",
                          basedOn=[{"artifactKey": "technical-plan", "sha256": "f" * 64}])
        for task in self.baseline["task_registry"].values():
            self.assertTrue(task_covers_current_input(task, self.plan, context), task["id"])

    def test_changed_task_is_replaced_without_completed_status(self):
        """真实 Assembly 接纳固定 ID 新任务，旧完成事实与正式基线保持隔离。"""
        from app.services.scope_assembly import assemble_scope_build_task_plan
        from tests.test_scope_assembly import _candidate, _reuse_facts, _requirement
        from tests.dag_planning_baseline_fixtures import task

        changed = deepcopy(self.plan)
        changed["api_contracts"][0]["endpoints"][0]["parameters"] = [
            {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 13}},
        ]
        before = deepcopy(self.baseline)
        original = self.baseline["task_registry"]["orders:api"]
        candidate = task("orders:api", "frontend:api-client", "frontend.api_module",
                         original["target_files"][0], "orders.list")
        candidate["status"] = "pending"
        result = assemble_scope_build_task_plan(base_confirmed_plan=self.baseline,
            skeleton_plan=ensure_build_unit_skeleton(changed, workspace_snapshot(), self.baseline),
            project_plan=changed, product_plan={"pages": []},
            build_context=build_context(changed, self.scope), build_execution_scope=self.scope,
            reuse_facts=_reuse_facts(self.baseline),
            generation_requirements_by_unit={"frontend:api-client": _requirement("frontend:api-client")},
            candidates_by_unit={"frontend:api-client": _candidate("frontend:api-client", [candidate])})
        current = result.assembled_plan["task_registry"]["orders:api"]
        self.assertEqual(current["status"], "pending")
        self.assertNotIn("orders:api", result.retained_task_ids)
        self.assertIn("orders:api", result.candidate_task_ids)
        endpoint = current["business_acceptance_checks"][0]["expected"]["endpoints"][0]
        self.assertEqual(endpoint["parameters"][0]["schema"]["default"], 13)
        self.assertEqual(self.baseline, before)
