from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.authorization_edd import verify_authorization_edd
from app.services.authorization_platform_projection import apply_authorization_platform_projections


class AuthorizationPlatformProjectionTests(unittest.TestCase):
    """验证显式前端路由和后端常量由平台统一投影。"""

    def test_records_frontend_and_backend_diffs(self) -> None:
        """首次生成三处平台文件，重复执行不再产生差异。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_template(workspace)
            first = apply_authorization_platform_projections(workspace, self._plan())
            second = apply_authorization_platform_projections(workspace, self._plan())

        self.assertEqual(first["summary"]["files"], 3)
        self.assertEqual(second["summary"]["files"], 0)

    def test_edd_reports_route_drift_without_rewrite(self) -> None:
        """EDD 仅报告业务路由漂移，不能通过重写掩盖失败。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_template(workspace)
            plan = self._plan()
            apply_authorization_platform_projections(workspace, plan)
            route_file = workspace / "frontend/src/routes/index.tsx"
            route_file.write_text("// drift", encoding="utf-8")
            errors = verify_authorization_edd(workspace, plan)

        self.assertTrue(any("权限共享投影 EDD 失败" in error for error in errors), errors)

    def _plan(self) -> dict:
        """构造最小确认 Build DAG 权限投影。"""

        return {
            "authorization_frontend_projection": {
                "resources": [
                    {"group": "SYSTEM", "name": "AUTHORIZATION_MANAGEMENT", "resourceKey": "system_authorization_management"},
                    {"group": "PAGE", "name": "ORDERS", "resourceKey": "orders"},
                    {"group": "OPERATION", "name": "ORDERS_APPROVE", "resourceKey": "orders_approve"},
                ],
                "pages": [{"pageId": "orders", "path": "/orders", "pageKey": "Orders", "resourceGroup": "PAGE", "resourceName": "ORDERS"}],
            },
            "authorization_constants_projection": [{"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"}],
        }

    def _write_template(self, workspace: Path) -> None:
        """创建带固定业务路由托管区的最小 auth 模板。"""

        self._write(workspace / ".xcodeagent/template-generation-manifest.json", json.dumps({"steps": {"download": {"targets": {"backend": {"branch": "auth"}}}}}))
        self._write(workspace / "frontend/src/routes/index.tsx", "import { Layout } from '@/layout';\nimport { RouteGuard } from '@/authorization/RouteGuard';\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\nexport const routes = [\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n];\n")
        self._write(workspace / "backend/.xcodeagent/auth-constants-projection.json", json.dumps({"schemaVersion": "xcodeagent.auth-constants-projection.v1", "targetPath": "src/main/java/example/AuthConstants.java", "startMarker": "// XCODEAGENT_AUTH_CONSTANTS_START", "endMarker": "// XCODEAGENT_AUTH_CONSTANTS_END"}))
        self._write(workspace / "backend/src/main/java/example/AuthConstants.java", "// XCODEAGENT_AUTH_CONSTANTS_START\n// XCODEAGENT_AUTH_CONSTANTS_END\n")

    def _write(self, path: Path, content: str) -> None:
        """创建测试用 UTF-8 文件。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
