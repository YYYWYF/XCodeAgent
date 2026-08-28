from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.authorization_platform_projection import (
    AuthorizationPlatformProjectionError,
    apply_authorization_platform_projections,
)
from app.services.authorization_edd import verify_authorization_edd


class AuthorizationPlatformProjectionTests(unittest.TestCase):
    """验证共享投影只作为平台证据记录，不归属任何 Build Agent。"""

    def test_records_managed_file_diffs_as_platform_evidence(self) -> None:
        """平台投影的两处受管文件变更必须独立返回，重复应用不再产生源码差异。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_auth_template_contract(workspace)
            plan = {
                "authorization_route_projection": [
                    {"pageId": "orders", "route": "/orders", "resourceKey": "page_orders"}
                ],
                "authorization_constants_projection": [
                    {"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"}
                ],
            }

            first = apply_authorization_platform_projections(workspace, plan)
            second = apply_authorization_platform_projections(workspace, plan)

        self.assertEqual(first["source"], "platform.authorization_projection")
        self.assertEqual(first["summary"]["files"], 2)
        self.assertEqual(
            {item["path"] for item in first["files"]},
            {
                "backend/src/main/java/example/AuthConstants.java",
                "frontend/src/authorization/generated/businessRouteGuards.ts",
            },
        )
        self.assertEqual(second["summary"]["files"], 0)
        self.assertEqual(first["planSha256"], second["planSha256"])

    def test_rejects_a_projection_input_that_differs_from_bound_plan(self) -> None:
        """平台投影必须拒绝与 Build Run 绑定摘要不一致的任务计划。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_auth_template_contract(workspace)
            with self.assertRaisesRegex(AuthorizationPlatformProjectionError, "摘要与投影输入不一致"):
                apply_authorization_platform_projections(
                    workspace,
                    {"authorization_route_projection": []},
                    build_run_id="build-0123456789abcdef0123456789abcdef",
                    plan_sha256="0" * 64,
                )

    def test_skips_without_authorization_projections(self) -> None:
        """权限关闭的确认 DAG 不访问模板托管区，也不虚构平台代码变化。"""

        with tempfile.TemporaryDirectory() as directory:
            evidence = apply_authorization_platform_projections(Path(directory), {})

        self.assertEqual(evidence["status"], "skipped")
        self.assertEqual(evidence["files"], [])

    def test_edd_verifies_projection_without_rewriting_managed_file(self) -> None:
        """EDD 只能读取已投影内容，不能通过重放投影修正漂移。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_auth_template_contract(workspace)
            plan = {
                "authorization_route_projection": [
                    {"pageId": "orders", "route": "/orders", "resourceKey": "page_orders"}
                ]
            }
            apply_authorization_platform_projections(workspace, plan)
            route_file = workspace / "frontend/src/authorization/generated/businessRouteGuards.ts"
            before = route_file.read_text(encoding="utf-8")
            errors = verify_authorization_edd(workspace, plan)
            after = route_file.read_text(encoding="utf-8")

        self.assertFalse(errors, errors)
        self.assertEqual(after, before)

    def test_edd_reports_projection_drift_without_repairing_it(self) -> None:
        """托管区漂移必须 fail closed，EDD 不得借验证之机覆盖文件。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_auth_template_contract(workspace)
            plan = {
                "authorization_route_projection": [
                    {"pageId": "orders", "route": "/orders", "resourceKey": "page_orders"}
                ]
            }
            apply_authorization_platform_projections(workspace, plan)
            route_file = workspace / "frontend/src/authorization/generated/businessRouteGuards.ts"
            route_file.write_text("// XCODEAGENT_ROUTE_GUARDS_START\n// drift\n// XCODEAGENT_ROUTE_GUARDS_END\n", encoding="utf-8")
            errors = verify_authorization_edd(workspace, plan)
            after = route_file.read_text(encoding="utf-8")

        self.assertTrue(any("RouteGuard 托管区与确认投影不一致" in error for error in errors), errors)
        self.assertIn("// drift", after)

    def _write_auth_template_contract(self, workspace: Path) -> None:
        """构造前后端均来自 auth 分支的最小托管区模板。"""

        manifest = workspace / ".xcodeagent/template-generation-manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "steps": {
                        "download": {
                            "targets": {
                                "frontend": {"branch": "auth"},
                                "backend": {"branch": "auth"},
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self._write_file(
            workspace / "frontend/.xcodeagent/route-guard-projection.json",
            json.dumps(
                {
                    "schemaVersion": "xcodeagent.route-guard-projection.v1",
                    "targetPath": "src/authorization/generated/businessRouteGuards.ts",
                    "startMarker": "// XCODEAGENT_ROUTE_GUARDS_START",
                    "endMarker": "// XCODEAGENT_ROUTE_GUARDS_END",
                }
            ),
        )
        self._write_file(
            workspace / "frontend/src/authorization/generated/businessRouteGuards.ts",
            "// XCODEAGENT_ROUTE_GUARDS_START\n// XCODEAGENT_ROUTE_GUARDS_END\n",
        )
        self._write_file(
            workspace / "backend/.xcodeagent/auth-constants-projection.json",
            json.dumps(
                {
                    "schemaVersion": "xcodeagent.auth-constants-projection.v1",
                    "targetPath": "src/main/java/example/AuthConstants.java",
                    "startMarker": "// XCODEAGENT_AUTH_CONSTANTS_START",
                    "endMarker": "// XCODEAGENT_AUTH_CONSTANTS_END",
                }
            ),
        )
        self._write_file(
            workspace / "backend/src/main/java/example/AuthConstants.java",
            "// XCODEAGENT_AUTH_CONSTANTS_START\n// XCODEAGENT_AUTH_CONSTANTS_END\n",
        )

    def _write_file(self, path: Path, content: str) -> None:
        """创建测试所需的 UTF-8 模板文件。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
