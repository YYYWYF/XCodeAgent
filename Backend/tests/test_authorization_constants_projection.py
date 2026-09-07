from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.authorization_constants_projection import (
    AuthorizationConstantsProjectionError,
    apply_authorization_constants_projection,
)


class AuthorizationConstantsProjectionTests(unittest.TestCase):
    """验证操作资源常量只由平台写入 auth 模板受管区域。"""

    def test_writes_java_constants_inside_declared_markers(self) -> None:
        """常量值保持原资源键，且重复写入不会改变模板其他内容。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = self._write_auth_template_contract(workspace)
            projection = [
                {"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"},
                {"name": "ORDERS_RECHECK_RESOURCE", "resourceKey": "orders_recheck"},
            ]

            result = apply_authorization_constants_projection(workspace, projection)
            content = target.read_text(encoding="utf-8")
            apply_authorization_constants_projection(workspace, projection)

            self.assertTrue(result["applied"])
            self.assertIn('public static final String ORDERS_APPROVE_RESOURCE = "orders_approve";', content)
            self.assertIn('public static final String ORDERS_RECHECK_RESOURCE = "orders_recheck";', content)
            self.assertIn("template-owned prefix", content)
            self.assertIn("template-owned suffix", content)
            self.assertEqual(content, target.read_text(encoding="utf-8"))

    def test_rejects_system_resource_constant(self) -> None:
        """系统管理资源固定复用模板常量，不得由业务投影重新声明。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_auth_template_contract(workspace)
            with self.assertRaisesRegex(AuthorizationConstantsProjectionError, "非法、重复或漂移"):
                apply_authorization_constants_projection(
                    workspace,
                    [{"name": "SYSTEM_AUTHORIZATION_MANAGEMENT_RESOURCE", "resourceKey": "system_authorization_management"}],
                )

    def test_rejects_projection_when_effective_capability_is_missing(self) -> None:
        """固定 marker 存在也不能绕过 Engine effective capability 门禁。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = self._write_auth_template_contract(workspace)
            state = workspace / ".xcodeagent/template-state.json"
            state.write_text('{"templateRevision":"r1","managedFiles":{},"requested":{},"effective":{}}', encoding="utf-8")
            with self.assertRaisesRegex(AuthorizationConstantsProjectionError, "effective"):
                apply_authorization_constants_projection(
                    workspace,
                    [{"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"}],
                )
            self.assertNotIn("ORDERS_APPROVE_RESOURCE", target.read_text(encoding="utf-8"))

    def _write_auth_template_contract(self, workspace: Path) -> Path:
        """构造 authorization effective State 和固定 Java 常量托管文件。"""

        state = workspace / ".xcodeagent/template-state.json"
        state.parent.mkdir(parents=True)
        state.write_text('{"templateRevision":"r1","managedFiles":{},"requested":{"authorization":{"enabled":true}},"effective":{"authorization":{"enabled":true}}}', encoding="utf-8")
        target = workspace / (
            "backend/src/main/java/com/cmbchina/backend/auth/domain/constant/"
            "AuthConstants.java"
        )
        target.parent.mkdir(parents=True)
        target.write_text(
            "template-owned prefix\n// XCODEAGENT_AUTH_CONSTANTS_START\nold\n"
            "// XCODEAGENT_AUTH_CONSTANTS_END\ntemplate-owned suffix\n",
            encoding="utf-8",
        )
        return target


if __name__ == "__main__":
    unittest.main()
