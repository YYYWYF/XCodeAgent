from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.authorization_resource_catalog import (
    AuthorizationFrontendProjectionError,
    compile_frontend_resource_catalog,
    resource_catalog_fingerprint,
)
from app.services.build_scheduler import classify_task_result
from app.services.platform_task_executors import (
    execute_authorization_frontend_resources,
)


RESOURCE_PATH = "frontend/src/constants/resources.ts"
ROUTES_PATH = "frontend/src/constants/routes.tsx"


class AuthorizationFrontendResourcesExecutorTests(unittest.TestCase):
    """验证 authorization.frontend_resources 的独立确定性执行边界。"""

    def test_normal_write(self) -> None:
        """正常执行从正式目录生成 resources.ts 并报告唯一变更文件。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            result = execute_authorization_frontend_resources(
                self._task(self._fingerprint(self._formal_plan())),
                self._context(workspace),
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["changed_files"], [RESOURCE_PATH])
            self.assertIn("export const RESOURCES", (workspace / RESOURCE_PATH).read_text(encoding="utf-8"))

    def test_routes_file_is_untouched(self) -> None:
        """执行资源 Task 时既不读取也不修改 routes.tsx。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            routes = self._write(workspace / ROUTES_PATH, "// routes sentinel\n")
            result = execute_authorization_frontend_resources(
                self._task(self._fingerprint(self._formal_plan())),
                self._context(workspace),
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(routes.read_text(encoding="utf-8"), "// routes sentinel\n")

    def test_already_identical_does_not_rewrite(self) -> None:
        """已满足时返回 already_satisfied，并保持文件时间和内容不变。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            plan = self._formal_plan()
            task = self._task(self._fingerprint(plan))
            first = execute_authorization_frontend_resources(task, self._context(workspace, plan))
            target = workspace / RESOURCE_PATH
            before = (target.read_bytes(), target.stat().st_mtime_ns)
            second = execute_authorization_frontend_resources(task, self._context(workspace, plan))

            self.assertEqual(first["status"], "completed")
            self.assertEqual(second["status"], "already_satisfied")
            self.assertEqual(second["changed_files"], [])
            self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)

    def test_stale_fingerprint_is_fatal_without_write(self) -> None:
        """正式目录变化后旧 fingerprint 终止失败，已有 resources.ts 不被覆盖。"""

        old_plan = self._formal_plan()
        task = self._task(self._fingerprint(old_plan))
        current_plan = deepcopy(old_plan)
        current_plan["authorization_manifest"]["resources"].append(
            {
                "resourceKey": "orders_export",
                "type": "operation",
                "targetResourceRef": "action:orders:export",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = self._write(workspace / RESOURCE_PATH, "// existing\n")
            result = execute_authorization_frontend_resources(
                task,
                self._context(workspace, current_plan),
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], "stale_authorization_resource_catalog")
            self.assertEqual(classify_task_result(result)["action"], "terminal_failure")
            self.assertEqual(target.read_text(encoding="utf-8"), "// existing\n")

    def test_invalid_target_fails_before_any_write(self) -> None:
        """任何非 resources.ts 目标声明都作为终止型 contract failure 拒绝。"""

        plan = self._formal_plan()
        task = self._task(self._fingerprint(plan))
        task["target_files"] = [ROUTES_PATH]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            routes = self._write(workspace / ROUTES_PATH, "// routes sentinel\n")
            result = execute_authorization_frontend_resources(task, self._context(workspace, plan))

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], "invalid_task_target")
            self.assertFalse((workspace / RESOURCE_PATH).exists())
            self.assertEqual(routes.read_text(encoding="utf-8"), "// routes sentinel\n")

    def test_compile_failure_does_not_write(self) -> None:
        """正式资源数据无法 canonical compile 时显式失败且不修改目标。"""

        valid_plan = self._formal_plan()
        invalid_plan = deepcopy(valid_plan)
        invalid_plan["authorization_manifest"]["resources"].append(
            deepcopy(invalid_plan["authorization_manifest"]["resources"][0])
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = self._write(workspace / RESOURCE_PATH, "// existing\n")
            result = execute_authorization_frontend_resources(
                self._task(self._fingerprint(valid_plan)),
                self._context(workspace, invalid_plan),
            )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], "authorization_resource_compile_failed")
            self.assertEqual(target.read_text(encoding="utf-8"), "// existing\n")

    def test_output_validation_failure_is_reported(self) -> None:
        """写后 deterministic validate 失败时不能返回 completed。"""

        plan = self._formal_plan()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with patch(
                "app.services.platform_task_executors.authorization_frontend_resources.verify_frontend_resources_projection",
                side_effect=AuthorizationFrontendProjectionError("forced drift"),
            ):
                result = execute_authorization_frontend_resources(
                    self._task(self._fingerprint(plan)),
                    self._context(workspace, plan),
                )

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], "authorization_resource_output_validation_failed")
            self.assertEqual(result["changed_files"], [RESOURCE_PATH])

    def _formal_plan(self) -> dict:
        """构造包含完整已确认 authorization resource data 的最小 formal plan。"""

        return {
            "confirmation_status": "confirmed",
            "authorization_manifest": {
                "enabled": True,
                "resources": [
                    {
                        "resourceKey": "system_authorization_management",
                        "type": "system",
                        "targetResourceRef": "system:authorization_management",
                    },
                    {
                        "resourceKey": "orders",
                        "type": "page",
                        "targetResourceRef": "page:orders",
                    },
                ],
            },
        }

    def _fingerprint(self, plan: dict) -> str:
        """按正式 canonical catalog 计算测试 Task 的完整 fingerprint。"""

        catalog = compile_frontend_resource_catalog(plan["authorization_manifest"])
        return resource_catalog_fingerprint(catalog)

    def _task(self, fingerprint: str) -> dict:
        """构造与 deterministic auth candidate 相同的最小完整 Task Contract。"""

        capability = f"frontend.auth.resources:{fingerprint}"
        task_id = f"frontend-auth-resources-{fingerprint}"
        return {
            "id": task_id,
            "unit_id": "frontend:auth-guard",
            "owner": "frontend",
            "task_type": "frontend.code",
            "execution_strategy": "deterministic",
            "platform_executor": "authorization.frontend_resources",
            "target_files": [RESOURCE_PATH],
            "allowed_paths": [RESOURCE_PATH],
            "provides_capabilities": [capability],
            "source_refs": {
                "artifact": "technical-plan",
                "kind": "frontend.auth.resources",
                "capability_id": capability,
                "resource_catalog_fingerprint": fingerprint,
                "paths": [RESOURCE_PATH],
            },
            "deliverables": [
                {
                    "id": f"{task_id}-resources",
                    "kind": "frontend.shared_capability",
                    "target_id": capability,
                    "paths": [RESOURCE_PATH],
                    "provides": [capability],
                }
            ],
        }

    def _context(self, workspace: Path, plan: dict | None = None) -> dict:
        """构造 executor 的显式 workspace/formal plan 上下文。"""

        return {"workspace": workspace, "formal_plan": plan or self._formal_plan()}

    def _write(self, path: Path, content: str) -> Path:
        """写入测试哨兵文件并返回路径。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
