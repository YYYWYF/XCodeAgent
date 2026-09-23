from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.authorization_edd import verify_authorization_edd
from app.services.authorization_platform_projection import apply_platform_projections


class AuthorizationPlatformProjectionTests(unittest.TestCase):
    """验证显式前端路由和后端常量由平台统一投影。"""

    def test_records_frontend_and_backend_diffs(self) -> None:
        """首次生成三处平台文件，重复执行不再产生差异。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_template(workspace)
            first = apply_platform_projections(workspace, self._plan())
            second = apply_platform_projections(workspace, self._plan())

        self.assertEqual(first["summary"]["files"], 2)
        self.assertEqual(second["summary"]["files"], 0)

    def test_edd_reports_resource_drift_without_rewrite(self) -> None:
        """EDD 仅报告权限资源漂移，不能通过重写掩盖失败。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_template(workspace)
            plan = self._plan()
            apply_platform_projections(workspace, plan)
            resource_file = workspace / "frontend/src/constants/resources.ts"
            resource_file.write_text("// drift", encoding="utf-8")
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
                "routeDecorations": [{"pageId": "orders", "resourceKey": "orders"}],
            },
            "authorization_constants_projection": [{"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"}],
        }

    def _write_template(self, workspace: Path) -> None:
        """创建带固定业务路由托管区的最小 auth 模板。"""

        self._write(workspace / ".devagentstudio/template-state.json", json.dumps({
            "schemaVersion": 2,
            "templateRevision": "r1",
            "requested": {"authorization": {"enabled": True, "config": {}}},
            "effective": {"authorization": {"enabled": True, "config": {}}},
            "appliedAdditions": {},
        }))
        self._write(workspace / "frontend/src/constants/resources.ts", "export const RESOURCES = {} as const;\n")
        self._write(workspace / "backend/src/main/java/com/cmbchina/backend/auth/domain/constant/AuthConstants.java", "// DEVAGENTSTUDIO_AUTH_CONSTANTS_START\n// DEVAGENTSTUDIO_AUTH_CONSTANTS_END\n")

    def _write(self, path: Path, content: str) -> None:
        """创建测试用 UTF-8 文件。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
