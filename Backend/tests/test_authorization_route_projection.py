from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.authorization_route_projection import (
    AuthorizationRouteProjectionError,
    apply_authorization_route_projection,
)


class AuthorizationRouteProjectionTests(unittest.TestCase):
    """验证平台只在确认后写入模板声明的共享路由托管区。"""

    def test_writes_deterministic_projection_inside_declared_markers(self) -> None:
        """受控页面投影只能替换标记之间的内容，且重复执行无额外变更。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = self._write_auth_template_contract(workspace)
            projection = [{"pageId": "orders", "route": "/orders", "resourceKey": "orders"}]

            first = apply_authorization_route_projection(workspace, projection)
            after_first = target.read_text(encoding="utf-8")
            second = apply_authorization_route_projection(workspace, projection)

            self.assertTrue(first["applied"])
            self.assertTrue(second["applied"])
            self.assertIn('resourceKey: "orders"', after_first)
            self.assertEqual(after_first, target.read_text(encoding="utf-8"))
            self.assertIn("template-owned prefix", after_first)
            self.assertIn("template-owned suffix", after_first)

    def test_missing_descriptor_blocks_route_projection(self) -> None:
        """模板没有授权路由契约时不得猜测或改写任意 Router 文件。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_manifest(workspace)

            with self.assertRaisesRegex(AuthorizationRouteProjectionError, "缺少 RouteGuard 托管区声明"):
                apply_authorization_route_projection(
                    workspace,
                    [{"pageId": "orders", "route": "/orders", "resourceKey": "orders"}],
                )

    def _write_auth_template_contract(self, workspace: Path) -> Path:
        """构造由 auth 模板提供的描述文件和受管路由配置文件。"""

        self._write_manifest(workspace)
        descriptor = workspace / "frontend/.xcodeagent/route-guard-projection.json"
        descriptor.parent.mkdir(parents=True)
        descriptor.write_text(
            json.dumps(
                {
                    "schemaVersion": "xcodeagent.route-guard-projection.v1",
                    "targetPath": "src/authorization/generated/businessRouteGuards.ts",
                    "startMarker": "// XCODEAGENT_ROUTE_GUARDS_START",
                    "endMarker": "// XCODEAGENT_ROUTE_GUARDS_END",
                }
            ),
            encoding="utf-8",
        )
        target = workspace / "frontend/src/authorization/generated/businessRouteGuards.ts"
        target.parent.mkdir(parents=True)
        target.write_text(
            "template-owned prefix\n// XCODEAGENT_ROUTE_GUARDS_START\nold\n"
            "// XCODEAGENT_ROUTE_GUARDS_END\ntemplate-owned suffix\n",
            encoding="utf-8",
        )
        return target

    def _write_manifest(self, workspace: Path) -> None:
        """写入最小模板 manifest，声明前端实际来自 auth 分支。"""

        path = workspace / ".xcodeagent/template-generation-manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "steps": {
                        "download": {"targets": {"frontend": {"branch": "auth"}}}
                    }
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
