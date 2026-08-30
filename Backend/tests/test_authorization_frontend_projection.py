import tempfile
import unittest
from pathlib import Path

from app.services.authorization_frontend_projection import apply_authorization_frontend_projection, compile_frontend_authorization_projection, verify_authorization_frontend_projection


class AuthorizationFrontendProjectionTests(unittest.TestCase):
    """验证注册任务使用资源常量和 PAGE_ROUTES 插槽。"""

    def test_generates_optional_page_resource_routes(self) -> None:
        """公开页面不写 resourceKey，受控页面引用 RESOURCES.PAGE。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routes = root / "frontend/src/constants/routes.tsx"
            routes.parent.mkdir(parents=True)
            routes.write_text("import { RESOURCES } from '@/constants/resources';\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
            projection = compile_frontend_authorization_projection(self._plan())
            apply_authorization_frontend_projection(root, projection)
            source = routes.read_text(encoding="utf-8")
            self.assertIn("RESOURCES.PAGE.PERSONAL_ASSETS", source)
            self.assertIn("<PagePublicAssets />", source)
            self.assertTrue(verify_authorization_frontend_projection(root, projection)["verified"])

    def _plan(self) -> dict:
        """构造同时包含受控和公开页面的最小计划。"""
        return {"pages": [{"pageId": "page_personal_assets", "name": "个人资产", "path": "/personal-assets"}, {"pageId": "page_public_assets", "name": "公共资产", "path": "/public-assets"}], "authorization_manifest": {"enabled": True, "resources": [{"resourceKey": "system_authorization_management", "type": "system", "targetResourceRef": "system:authorization_management"}, {"resourceKey": "page_personal_assets", "type": "page", "targetResourceRef": "page:page_personal_assets"}], "bindings": {"pages": [{"pageId": "page_personal_assets", "resourceKey": "page_personal_assets"}], "actions": [], "endpoints": []}}}


if __name__ == "__main__":
    unittest.main()
