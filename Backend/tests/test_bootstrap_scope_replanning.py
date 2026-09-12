"""跨接口规划时共享基础设施检查必须使用当前范围的回归测试。"""

from copy import deepcopy
import unittest
from unittest.mock import patch

from app.graph.nodes.tasks import _merge_prepared_scope_tasks, _replaceable_unit_ids


class BootstrapScopeReplanningTests(unittest.TestCase):
    """验证规划选择和任务合并均不会复用前一接口的基础设施检查。"""

    def test_existing_bootstrap_is_replanned_for_each_scope(self) -> None:
        """接口和页面即使已有完成的 bootstrap，也必须规划当前范围检查。"""
        for target in (
            {"type": "endpoint", "id": "list", "api_contract_id": "orders"},
            {"type": "page", "id": "orders"},
            {"type": "application", "id": "application"},
        ):
            for status in ("pending", "completed", "already_satisfied"):
                with self.subTest(target=target, status=status):
                    plan = {
                        "build_units": {"backend:bootstrap": {"task_ids": ["bootstrap"]}},
                        "task_registry": {"bootstrap": {"status": status}},
                    }
                    self.assertIn(
                        "backend:bootstrap",
                        _replaceable_unit_ids(plan, {"target": target}, {"backend:bootstrap"}),
                    )

    def test_merge_replaces_previous_source_check_and_keeps_other_endpoint(self) -> None:
        """数据库与外部 API 双向切换时替换旧检查，并保留其他接口及依赖。"""
        for previous, current in (("Feign", "MyBatis/MySQL"), ("MyBatis/MySQL", "Feign")):
            with self.subTest(previous=previous, current=current):
                bootstrap_id = "backend:bootstrap::bootstrap"
                old_task = {
                    "id": bootstrap_id, "unit_id": "backend:bootstrap",
                    "description": previous, "dependencies": [],
                    "source_refs": {"source_types": [previous]},
                }
                other_task = {
                    "id": "other", "unit_id": "backend:endpoint:api:other",
                    "description": "保留已规划接口", "dependencies": [bootstrap_id],
                }
                plan = {
                    "build_units": {
                        "backend:bootstrap": {"task_ids": [bootstrap_id]},
                        "backend:endpoint:api:other": {"task_ids": ["other"]},
                    },
                    "task_registry": {bootstrap_id: old_task, "other": other_task},
                    "task_graph": {"nodes": [bootstrap_id, "other"]},
                }
                candidate = {
                    **old_task, "description": current,
                    "source_refs": {"source_types": [current]},
                }
                context = {
                    "target": {"type": "endpoint", "api_contract_id": "api", "id": "next"},
                    "required_unit_ids": ["backend:bootstrap", "backend:endpoint:api:next"],
                }
                original = deepcopy(plan)
                with patch("app.graph.nodes.tasks.compile_build_task_plan_scope", return_value={}) as compile_scope:
                    _merge_prepared_scope_tasks(plan, {"tasks": [candidate]}, context)
                merged_tasks = compile_scope.call_args.args[1]
                self.assertEqual(merged_tasks, [other_task, candidate])
                self.assertEqual(plan, original)
                self.assertEqual(compile_scope.call_args.kwargs["preserve_compiled_task_ids"], {"other"})
