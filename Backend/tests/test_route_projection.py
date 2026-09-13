from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.route_projection import (
    RouteProjectionError,
    apply_route_projection,
    compile_route_projection,
    verify_route_projection,
)


class RouteProjectionTests(unittest.TestCase):
    """验证所有 TemplateState 形态共用同一 routes.tsx 页面投影。"""

    def test_projects_all_pages_without_authorization_and_is_idempotent(self) -> None:
        """无权限 decoration 时仍写入全部页面，重复执行不产生文件变化。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routes = root / "frontend/src/constants/routes.tsx"
            routes.parent.mkdir(parents=True)
            for page_key in ("Orders", "Public"):
                entry = root / "frontend/src/pages" / page_key / "index.tsx"
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text("export default null;\n", encoding="utf-8")
            routes.write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
            projection = compile_route_projection(self._plan())
            apply_route_projection(root, projection)
            first = routes.read_text(encoding="utf-8")
            apply_route_projection(root, projection)
            self.assertEqual(first, routes.read_text(encoding="utf-8"))
            self.assertIn("<Orders />", first)
            self.assertIn("<Public />", first)
            self.assertNotIn("resourceKey:", first)
            self.assertTrue(verify_route_projection(root, projection)["verified"])

    def test_rejects_missing_or_duplicate_markers_and_unknown_decoration(self) -> None:
        """marker 漂移和权限对未知页面的 decoration 必须 fail closed。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routes = root / "frontend/src/constants/routes.tsx"
            routes.parent.mkdir(parents=True)
            for page_key in ("Orders", "Public"):
                entry = root / "frontend/src/pages" / page_key / "index.tsx"
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text("export default null;\n", encoding="utf-8")
            routes.write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n", encoding="utf-8")
            with self.assertRaisesRegex(RouteProjectionError, "标记"):
                apply_route_projection(root, compile_route_projection(self._plan()))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routes = root / "frontend/src/constants/routes.tsx"
            routes.parent.mkdir(parents=True)
            for page_key in ("Orders", "Public"):
                entry = root / "frontend/src/pages" / page_key / "index.tsx"
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text("export default null;\n", encoding="utf-8")
            routes.write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
            with self.assertRaisesRegex(RouteProjectionError, "未知"):
                apply_route_projection(root, compile_route_projection(self._plan()), authorization_decorations=[{"pageId": "missing", "resourceKey": "missing"}])

    def test_rejects_missing_page_entry_without_creating_placeholder(self) -> None:
        """页面入口不存在时必须失败，且 Route Projection 不得创建任何占位页。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routes = root / "frontend/src/constants/routes.tsx"
            routes.parent.mkdir(parents=True)
            routes.write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
            with self.assertRaisesRegex(RouteProjectionError, "真实页面入口"):
                apply_route_projection(root, compile_route_projection(self._plan()))
            self.assertFalse((root / "frontend/src/pages/Orders/index.tsx").exists())

    def _plan(self) -> dict[str, object]:
        """构造一个公开页与一个受控候选页的最小 TechnicalPlan 页面事实。"""

        return {"pages": [{"pageId": "orders", "name": "订单", "path": "/orders"}, {"pageId": "public", "name": "公开", "path": "/public"}]}
