"""T6.4 新 Assembly 对精确 deterministic resources writer 的最小门禁例外。"""

from copy import deepcopy
import unittest

from app.services.build_task_planner import _template_boundary_errors
from app.services.deterministic_unit_candidates import is_deterministic_auth_resource_task, retained_auth_resource_task_ids


class SequentialAuthBoundaryTests(unittest.TestCase):
    def _task(self):
        """创建只用于边界识别测试的平台任务身份，不伪装成模型 Local 候选。"""

        return {
            "id": "frontend-auth-resources-" + "a" * 64, "unit_id": "frontend:auth-guard", "owner": "frontend",
            "execution_strategy": "deterministic", "platform_executor": "authorization.frontend_resources",
            "provides_capabilities": ["frontend.auth.resources:" + "a" * 64],
        }

    def test_only_new_assembly_allows_exact_platform_identity(self):
        """同一合法平台身份仅在新 Assembly 标记下通过，旧入口仍执行原门禁。"""

        task = self._task()
        args = {"paths": ["frontend/src/constants/resources.ts"]}
        self.assertTrue(_template_boundary_errors(task, **args))
        self.assertEqual(_template_boundary_errors(task, **args, allow_deterministic_auth_resources=True), [])

    def test_wrong_identity_executor_fingerprint_or_extra_path_remains_blocked(self):
        """普通模型任务、伪造标记、指纹不一致、附带路由文件均不能获得例外。"""

        for change in ({"unit_id": "page:orders"}, {"owner": "backend"}, {"execution_strategy": "model"},
                       {"platform_executor": "other"}, {"id": "model-task"}, {"provides_capabilities": []},
                       {"provides_capabilities": None}, {"provides_capabilities": "frontend.auth.resources:" + "a" * 64}):
            with self.subTest(change=change):
                task = {**self._task(), **change}
                self.assertTrue(_template_boundary_errors(task, paths=["frontend/src/constants/resources.ts"],
                                allow_deterministic_auth_resources=True))
        for paths in (["frontend/src/constants/resources.ts", "frontend/src/constants/routes.tsx"],
                      ["frontend/src/constants/resources.ts", "frontend/src/apis/extra.ts"]):
            with self.subTest(paths=paths):
                self.assertTrue(_template_boundary_errors(self._task(), paths=paths,
                                                        allow_deterministic_auth_resources=True))

    def test_retained_versioned_platform_tasks_are_recognized_without_mutation(self):
        """append-only 历史平台任务按各自完整指纹识别，不把旧 R 任务改成当前 R。"""

        task = self._task()
        old = deepcopy(task)
        self.assertTrue(is_deterministic_auth_resource_task(task, ["frontend/src/constants/resources.ts"]))
        self.assertEqual(task, old)

    def test_result_updates_preserve_only_existing_unchanged_platform_contract(self):
        """允许更新已有平台任务状态；新增或改动写入合同不能借结果重建获得资格。"""

        task = {**self._task(), "target_files": ["frontend/src/constants/resources.ts"],
                "allowed_paths": ["frontend/src/constants/resources.ts"], "source_refs": {}, "deliverables": []}
        plan = {"task_registry": {task["id"]: deepcopy(task)}}
        self.assertEqual(retained_auth_resource_task_ids(plan, [{**task, "status": "completed"}]), {task["id"]})
        self.assertEqual(retained_auth_resource_task_ids({}, [task]), set())
        for change in (
            {"allowed_paths": ["frontend/src/constants/routes.tsx"]},
            {"platform_executor": "other"}, {"source_refs": {"resource_catalog_fingerprint": "changed"}},
            {"execution_strategy": "agent"}, {"provides_capabilities": []},
        ):
            with self.subTest(change=change):
                self.assertEqual(retained_auth_resource_task_ids(plan, [{**task, **change}]), set())
