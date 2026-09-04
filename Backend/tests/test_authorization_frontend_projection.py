import tempfile
import unittest
from pathlib import Path

from app.services.authorization_frontend_projection import (
    apply_frontend_resources_projection,
    apply_frontend_routes_projection,
    compile_frontend_authorization_projection,
    compile_frontend_resources_projection,
    compile_frontend_routes_projection,
    verify_authorization_frontend_projection,
    verify_frontend_resources_projection,
    verify_frontend_routes_projection,
)


class AuthorizationFrontendProjectionTests(unittest.TestCase):
    """验证注册任务使用资源常量和 PAGE_ROUTES 插槽。"""

    def test_resources_only_projection_does_not_require_routes(self) -> None:
        """资源独立编译和写入不读取页面路由，也不要求 routes.tsx 存在。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan()
            plan["pages"] = [{"path": "invalid-route"}]
            projection = compile_frontend_resources_projection(plan)
            result = apply_frontend_resources_projection(root, projection)

            self.assertEqual(result["resourcesPath"], "frontend/src/constants/resources.ts")
            self.assertFalse((root / "frontend/src/constants/routes.tsx").exists())
            self.assertTrue(verify_frontend_resources_projection(root, projection)["verified"])

    def test_routes_only_projection_keeps_resource_file_absent(self) -> None:
        """路由独立写入保持既有语义，并且不创建或写入 resources.ts。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routes = self._write_routes_template(root)
            projection = compile_frontend_routes_projection(self._plan())
            apply_frontend_routes_projection(root, projection)

            source = routes.read_text(encoding="utf-8")
            self.assertIn("RESOURCES.PAGE.PERSONAL_ASSETS", source)
            self.assertIn("<PagePublicAssets />", source)
            self.assertFalse(routes.with_name("resources.ts").exists())
            self.assertTrue(verify_frontend_routes_projection(root, projection)["verified"])

    def test_combined_compile_matches_split_projections(self) -> None:
        """既有 combined compile 保持 resources/pages 数据形状与内容兼容。"""

        plan = self._plan()
        combined = compile_frontend_authorization_projection(plan)
        resources = compile_frontend_resources_projection(plan)
        routes = compile_frontend_routes_projection(plan)

        self.assertEqual(combined, {**resources, **routes})

    def test_combined_verify_composes_split_read_only_checks(self) -> None:
        """完整 EDD 校验仍同时核对由不同 owner 产生的资源与路由。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_routes_template(root)
            projection = compile_frontend_authorization_projection(self._plan())
            apply_frontend_resources_projection(root, projection)
            apply_frontend_routes_projection(root, projection)

            self.assertTrue(verify_authorization_frontend_projection(root, projection)["verified"])

    def _write_routes_template(self, root: Path) -> Path:
        """写入带固定托管标记的最小 routes.tsx 模板。"""

        routes = root / "frontend/src/constants/routes.tsx"
        routes.parent.mkdir(parents=True)
        routes.write_text("import { RESOURCES } from '@/constants/resources';\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
        return routes

    def _plan(self) -> dict:
        """构造同时包含受控和公开页面的最小计划。"""
        return {"pages": [{"pageId": "page_personal_assets", "name": "个人资产", "path": "/personal-assets"}, {"pageId": "page_public_assets", "name": "公共资产", "path": "/public-assets"}], "authorization_manifest": {"enabled": True, "resources": [{"resourceKey": "system_authorization_management", "type": "system", "targetResourceRef": "system:authorization_management"}, {"resourceKey": "page_personal_assets", "type": "page", "targetResourceRef": "page:page_personal_assets"}], "bindings": {"pages": [{"pageId": "page_personal_assets", "resourceKey": "page_personal_assets"}], "actions": [], "endpoints": []}}}


if __name__ == "__main__":
    unittest.main()
