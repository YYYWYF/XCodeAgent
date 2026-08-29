from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.authorization_frontend_projection import (
    apply_authorization_frontend_projection,
    compile_frontend_authorization_projection,
    verify_authorization_frontend_projection,
)


class AuthorizationFrontendProjectionTests(unittest.TestCase):
    """验证平台直接生成前端 RESOURCES 与显式业务 RouteGuard 路由。"""

    def test_generates_resources_and_wraps_only_controlled_page(self) -> None:
        """受控页面必须直接引用 RESOURCES.PAGE，公开页面不得产生 RouteGuard。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            routes = workspace / "frontend/src/routes/index.tsx"
            routes.parent.mkdir(parents=True)
            routes.write_text(
                "import { Layout } from '@/layout';\n"
                "import { RouteGuard } from '@/authorization/RouteGuard';\n"
                "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n"
                "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n"
                "export const routes = [\n"
                "// XCODEAGENT_BUSINESS_ROUTES_START\n"
                "// XCODEAGENT_BUSINESS_ROUTES_END\n"
                "];\n",
                encoding="utf-8",
            )
            projection = compile_frontend_authorization_projection(self._plan())
            result = apply_authorization_frontend_projection(workspace, projection)
            source = routes.read_text(encoding="utf-8")
            resources = (workspace / "frontend/src/authorization/resources.ts").read_text(encoding="utf-8")
            verified = verify_authorization_frontend_projection(workspace, projection)["verified"]

        self.assertTrue(result["applied"])
        self.assertIn("AUTHORIZATION_MANAGEMENT", resources)
        self.assertIn("PERSONAL_ASSETS", resources)
        self.assertIn("ASSETS_EXPORT", resources)
        self.assertIn("RESOURCES.PAGE.PERSONAL_ASSETS", source)
        self.assertIn("<PagePersonalAssets />", source)
        self.assertIn("<PagePublicAssets />", source)
        public_start = source.index("<PagePublicAssets />")
        self.assertNotIn("RouteGuard", source[public_start - 100:public_start + 100])
        self.assertTrue(verified)

    def _plan(self) -> dict:
        """构造同时包含 system、page、operation 的最小 TechnicalPlan。"""

        return {
            "pages": [
                {"pageId": "page_personal_assets", "path": "/personal-assets"},
                {"pageId": "page_public_assets", "path": "/public-assets"},
            ],
            "authorization_manifest": {
                "enabled": True,
                "resources": [
                    {"resourceKey": "system_authorization_management", "type": "system", "targetResourceRef": "system:authorization_management"},
                    {"resourceKey": "page_personal_assets", "type": "page", "targetResourceRef": "page:page_personal_assets"},
                    {"resourceKey": "page_personal_assets_export", "type": "operation", "targetResourceRef": "action:page_personal_assets:export"},
                ],
                "bindings": {
                    "pages": [{"pageId": "page_personal_assets", "resourceKey": "page_personal_assets"}],
                    "actions": [{"pageId": "page_personal_assets", "actionId": "export", "resourceKey": "page_personal_assets_export", "mode": "disabled"}],
                    "endpoints": [],
                },
            },
        }


if __name__ == "__main__":
    unittest.main()
