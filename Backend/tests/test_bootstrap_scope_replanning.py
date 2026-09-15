"""跨接口切换来源时，共享基础设施必须规划当前缺项。"""

import unittest

from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.unit_generation_requirements import resolve_generation_requirements
from tests.test_build_task_reuse import _task, _plan as _confirmed_plan
from tests.test_unit_generation_requirement_targets import _plan, _design


class BootstrapScopeReplanningTests(unittest.TestCase):
    """使用新 Unit 规划入口验证旧基础设施职责不会掩盖新的来源需求。"""

    def test_switching_source_plans_missing_capability_and_retains_existing_task(self) -> None:
        """数据库与外部 API 双向切换时，按精确能力保留旧任务并规划新缺项。"""
        for previous, current in (("database", "external_api"), ("external_api", "database")):
            for status in ("pending", "completed", "already_satisfied"):
                with self.subTest(previous=previous, current=current, status=status):
                    task = _task("bootstrap-existing", unit_id="backend:bootstrap",
                                 status=status, provides=[f"backend.bootstrap:{previous}"])
                    plan = _plan()
                    skeleton = ensure_build_unit_skeleton(plan, {})
                    facts = resolve_reuse_facts(
                        confirmed_plan=_confirmed_plan(task), unit_skeleton=skeleton,
                        build_context={"required_unit_ids": ["backend:bootstrap"]},
                        workspace_snapshot={}, formal_plan=plan,
                    )
                    result = resolve_generation_requirements(
                        required_unit_ids=["backend:bootstrap"], unit_skeleton=skeleton,
                        build_execution_scope={"type": "endpoint", "targetId": "orders.list",
                                               "apiContractId": "orders-api"},
                        reuse_facts=facts, formal_target=plan, endpoint_designs=[_design(current)],
                    )
                    self.assertEqual(
                        [item.requirement_id for item in result.generation_requirements_by_unit["backend:bootstrap"]],
                        [f"backend.bootstrap:{current}"],
                    )
                    self.assertEqual(
                        facts.retained_task_ids_by_unit["backend:bootstrap"],
                        ("bootstrap-existing",),
                    )
