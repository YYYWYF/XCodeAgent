import tempfile
import unittest
from pathlib import Path

from app.services.authorization_frontend_projection import apply_authorization_frontend_projection, compile_frontend_authorization_projection, verify_authorization_frontend_projection
from app.services.route_projection import apply_route_projection, compile_route_projection, verify_route_projection


class AuthorizationFrontendProjectionTests(unittest.TestCase):
    """验证权限投影只提供资源与 route decoration。"""

    def test_generates_optional_page_resource_routes(self) -> None:
        """通用路由覆盖全部页面，权限仅为受控页附加 resourceKey。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routes = root / "frontend/src/constants/routes.tsx"
            routes.parent.mkdir(parents=True)
            for page_key in ("PagePersonalAssets", "PagePublicAssets"):
                entry = root / "frontend/src/pages" / page_key / "index.tsx"
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text("export default null;\n", encoding="utf-8")
            routes.write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
            plan = self._plan()
            projection = compile_frontend_authorization_projection(plan)
            apply_route_projection(root, compile_route_projection(plan), authorization_decorations=projection["routeDecorations"])
            apply_authorization_frontend_projection(root, projection)
            source = routes.read_text(encoding="utf-8")
            self.assertIn('resourceKey: "page_personal_assets"', source)
            self.assertIn("<PagePublicAssets />", source)
            self.assertTrue(verify_authorization_frontend_projection(root, projection)["verified"])
            self.assertTrue(verify_route_projection(root, compile_route_projection(plan), authorization_decorations=projection["routeDecorations"])["verified"])

    def _plan(self) -> dict:
        """构造同时包含受控和公开页面的最小计划。"""
        return {"pages": [{"pageId": "page_personal_assets", "name": "个人资产", "path": "/personal-assets"}, {"pageId": "page_public_assets", "name": "公共资产", "path": "/public-assets"}], "authorization_manifest": {"enabled": True, "resources": [{"resourceKey": "system_authorization_management", "type": "system", "targetResourceRef": "system:authorization_management"}, {"resourceKey": "page_personal_assets", "type": "page", "targetResourceRef": "page:page_personal_assets"}], "bindings": {"pages": [{"pageId": "page_personal_assets", "resourceKey": "page_personal_assets"}], "actions": [], "endpoints": []}}}


if __name__ == "__main__":
    unittest.main()
