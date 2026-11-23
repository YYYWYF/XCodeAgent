"""验证权限资源结果复用时的旧依赖清理与资源更新边界。"""

from copy import deepcopy
from tempfile import TemporaryDirectory
import unittest

from app.services.backend_authorization_resources import (
    BACKEND_AUTH_TASK, BACKEND_AUTH_UNIT, backend_resources_applied, record_backend_resources,
)
from app.services.backend_auth_resource_candidate import build_backend_auth_resource_candidate
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.build_unit_compiler import apply_unit_compilation
from app.services.unit_generation_requirements import resolve_generation_requirements
from tests.dag_planning_baseline_fixtures import project_plan


class BackendAuthResourceDependencyTests(unittest.TestCase):
    """只抵扣当前清单的可信完成事实，不删除其他来源不明的依赖。"""

    def setUp(self) -> None:
        """准备保留旧权限依赖的 Bootstrap 与平台 Unit 边。"""
        self.constants = [{"name": "ORDERS_LIST_RESOURCE", "resourceKey": "orders_list"}]
        self.bootstrap = {
            "id": "bootstrap", "unit_id": "backend:bootstrap", "status": "completed",
            "dependencies": [BACKEND_AUTH_TASK],
        }
        self.plan = {
            "build_units": {unit: {"id": unit} for unit in (BACKEND_AUTH_UNIT, "backend:bootstrap")},
            "unit_graph": {"edges": [{
                "from": BACKEND_AUTH_UNIT, "to": "backend:bootstrap", "type": "depends_on",
            }]},
        }

    def compile_tasks(self, resources: dict, *, provider: dict | None = None) -> list[dict]:
        """通过生成和确认共用的编译器重建保留任务依赖。"""
        tasks = [self.bootstrap] if provider is None else [self.bootstrap, provider]
        return apply_unit_compilation(
            self.plan, tasks, {"backend_auth_resources": resources},
            preserve_compiled_task_ids={task["id"] for task in tasks},
        )

    def requirements(self, resources: dict):
        """编译权限资源职责，核对当前成功清单是否产生新任务。"""
        return resolve_generation_requirements(
            required_unit_ids=[BACKEND_AUTH_UNIT], unit_skeleton=self.plan,
            build_execution_scope={"type": "endpoint", "targetId": "orders.list", "apiContractId": "orders-api"},
            reuse_facts=ReuseFacts(
                retained_task_ids_by_unit={}, reusable_capabilities_by_unit={},
                retained_endpoint_owners=(), external_capabilities=(),
            ), formal_target=project_plan(), backend_auth_resources=resources,
        )

    def test_same_successful_list_skips_task_and_removes_stale_dependency(self) -> None:
        """真实成功清单抵扣生成，旧 Bootstrap 边被清除且重复编译稳定。"""
        with TemporaryDirectory() as directory:
            record_backend_resources(directory, self.constants)
            resources = {"constants": self.constants, "applied": backend_resources_applied(directory, self.constants)}
        self.assertTrue(resources["applied"])
        self.assertIsNone(build_backend_auth_resource_candidate(self.requirements(resources)))
        original = deepcopy(self.bootstrap)
        compiled = self.compile_tasks(resources)
        self.assertEqual([task["id"] for task in compiled], ["bootstrap"])
        self.assertEqual(compiled[0]["status"], "completed")
        self.assertEqual(compiled[0]["dependencies"], [])
        self.assertEqual(compiled[0]["invalid_dependencies"], [])
        self.assertEqual(compiled[0]["missing_unit_dependencies"], [])
        self.assertEqual(self.bootstrap, original)
        again = apply_unit_compilation(
            self.plan, compiled, {"backend_auth_resources": resources}, preserve_compiled_task_ids={"bootstrap"},
        )
        self.assertEqual(again, compiled)

    def test_changed_list_generates_pending_provider_and_keeps_dependency(self) -> None:
        """清单变化不能沿用旧成功状态，消费者必须等待当前权限任务。"""
        changed = [*self.constants, {"name": "ORDERS_EDIT_RESOURCE", "resourceKey": "orders_edit"}]
        with TemporaryDirectory() as directory:
            record_backend_resources(directory, self.constants)
            resources = {"constants": changed, "applied": backend_resources_applied(directory, changed)}
        self.assertFalse(resources["applied"])
        provider = build_backend_auth_resource_candidate(self.requirements(resources))["tasks"][0]
        self.assertEqual(provider["status"], "pending")
        compiled = self.compile_tasks(resources, provider=provider)
        self.assertEqual(compiled[0]["dependencies"], [BACKEND_AUTH_TASK])
        self.assertEqual(compiled[0]["invalid_dependencies"], [])

    def test_absent_success_evidence_keeps_missing_dependency_blocked(self) -> None:
        """完成证据缺失或无效时，不允许靠裁剪旧边绕过权限前置要求。"""
        for resources in ({}, {"constants": self.constants, "applied": False},
                          {"constants": self.constants, "applied": "true"}):
            with self.subTest(resources=resources):
                compiled = self.compile_tasks(resources)[0]
                self.assertEqual(compiled["invalid_dependencies"], [BACKEND_AUTH_TASK])
                if resources.get("constants"):
                    self.assertIn(BACKEND_AUTH_UNIT, compiled["missing_unit_dependencies"])

    def test_unrelated_missing_dependency_is_not_removed(self) -> None:
        """权限已满足不能消除其他缺失依赖。"""
        self.bootstrap["dependencies"].append("missing-business-task")
        compiled = self.compile_tasks({"constants": self.constants, "applied": True})[0]
        self.assertEqual(compiled["dependencies"], ["missing-business-task"])
        self.assertEqual(compiled["invalid_dependencies"], ["missing-business-task"])
