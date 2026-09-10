"""T2.3 auth-guard 资源目录精确身份与 deterministic 策略回归。"""

from copy import deepcopy
import unittest

from app.services.unit_generation_requirements import resolve_generation_requirements
from app.services.unit_generation_requirement_targets import resource_catalog_fingerprint
from tests.test_build_task_reuse import _task
from tests.test_unit_generation_requirements import _formal_plan, _inputs


def _auth_plan() -> dict:
    """构造完整 confirmed 权限目录，资源身份独立于页面路由信息。"""

    return {**_formal_plan(), "authorization_manifest": {
        "enabled": True,
        "resources": [
            {"resourceKey": "orders", "type": "page", "targetResourceRef": "page:orders"},
            {"resourceKey": "users", "type": "page", "targetResourceRef": "page:users"},
        ],
        "bindings": {"pages": [{"pageId": "orders", "resourceKey": "orders"}, {"pageId": "users", "resourceKey": "users"}], "actions": [], "endpoints": []},
    }}


class UnitGenerationRequirementsAuthTests(unittest.TestCase):
    def test_auth_guard_generates_deterministic_resource_requirement(self) -> None:
        """缺少当前完整目录能力时，只规划 resources.ts 的 deterministic 职责。"""

        inputs = _inputs(formal_plan=_auth_plan())
        inputs["required_unit_ids"] = ["frontend:auth-guard"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(result.generation_strategy_by_unit["frontend:auth-guard"], "deterministic")
        self.assertEqual(result.planning_unit_ids, ("frontend:auth-guard",))
        requirement = result.generation_requirements_by_unit["frontend:auth-guard"][0]
        fingerprint = resource_catalog_fingerprint(inputs["formal_target"])
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(requirement.requirement_id, f"frontend.auth.resources:{fingerprint}")
        self.assertEqual(requirement.source_refs["paths"], ("frontend/src/constants/resources.ts",))
        self.assertNotIn("routes.tsx", result.model_dump_json())

    def test_current_resource_capability_is_reused_independently_of_task_status(self) -> None:
        """当前资源 fingerprint 相等即可复用，不依赖历史 Task 执行成功。"""

        plan = _auth_plan()
        capability = f"frontend.auth.resources:{resource_catalog_fingerprint(plan)}"
        for status in ("pending", "failed", "completed"):
            with self.subTest(status=status):
                inputs = _inputs(_task("resources", unit_id="frontend:auth-guard", status=status, provides=[capability]), formal_plan=plan)
                inputs["required_unit_ids"] = ["frontend:auth-guard"]
                result = resolve_generation_requirements(**inputs)
                self.assertEqual(result.generation_strategy_by_unit["frontend:auth-guard"], "reuse_only")
                self.assertEqual(result.planning_unit_ids, ())

    def test_old_resource_version_does_not_satisfy_current_directory(self) -> None:
        """R1 保留不代表 R2 已满足，新目录需求保持增量而非 replacement。"""

        plan = _auth_plan()
        old_capability = f"frontend.auth.resources:{resource_catalog_fingerprint(plan)}"
        plan["authorization_manifest"]["resources"].append({"resourceKey": "orders_export", "type": "operation", "targetResourceRef": "action:orders:export"})
        inputs = _inputs(_task("old-resources", unit_id="frontend:auth-guard", provides=[old_capability]), formal_plan=plan)
        inputs["required_unit_ids"] = ["frontend:auth-guard"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(result.generation_strategy_by_unit["frontend:auth-guard"], "deterministic")
        self.assertNotEqual(result.generation_requirements_by_unit["frontend:auth-guard"][0].requirement_id, old_capability)
        self.assertEqual(inputs["reuse_facts"].retained_task_ids_by_unit["frontend:auth-guard"], ("old-resources",))

    def test_resource_identity_ignores_order_and_routes_but_uses_complete_catalog(self) -> None:
        """指纹不受数组顺序或路由变化影响，但当前 Scope 外的资源变更也必须改变身份。"""

        plan = _auth_plan()
        original = deepcopy(plan)
        fingerprint = resource_catalog_fingerprint(plan)
        plan["authorization_manifest"]["resources"].reverse()
        plan["pages"][1]["path"] = "/all-users"
        self.assertEqual(resource_catalog_fingerprint(plan), fingerprint)
        plan["authorization_manifest"]["resources"].append({"resourceKey": "users_export", "type": "operation", "targetResourceRef": "action:users:export"})
        self.assertNotEqual(resource_catalog_fingerprint(plan), fingerprint)
        self.assertEqual(resource_catalog_fingerprint(original), fingerprint)

    def test_disabled_auth_has_no_candidate_requirement(self) -> None:
        """骨架保留 auth-guard 但权限关闭时，没有目录职责便不进入 planning。"""

        inputs = _inputs()
        inputs["required_unit_ids"] = ["frontend:auth-guard"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(result.generation_strategy_by_unit["frontend:auth-guard"], "deterministic")
        self.assertEqual(result.generation_requirements_by_unit["frontend:auth-guard"], ())
        self.assertEqual(result.planning_unit_ids, ())


if __name__ == "__main__":
    unittest.main()
