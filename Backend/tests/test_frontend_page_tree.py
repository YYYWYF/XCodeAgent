from __future__ import annotations

import unittest

from app.services.frontend_page_tree import apply_frontend_page_route_hierarchy


class FrontendPageTreeRouteTests(unittest.TestCase):
    def test_menu_routes_extend_from_root_and_page_routes_extend_from_menu(self) -> None:
        routed = apply_frontend_page_route_hierarchy(
            [
                {
                    "name": "管理中心",
                    "unique_path": "/management",
                    "children": [
                        {
                            "pageId": "role_page",
                            "name": "角色管理",
                            "path": "/role",
                            "module_id": "access_control",
                            "description": "角色管理页面",
                        },
                        {
                            "pageId": "resource_page",
                            "name": "资源管理",
                            "path": "/resource",
                            "module_id": "access_control",
                            "description": "资源管理页面",
                        },
                    ],
                }
            ],
            root_route_prefix="/root",
        )

        self.assertEqual(routed[0]["unique_path"], "/root/management")
        self.assertEqual(routed[0]["children"][0]["path"], "/root/management/role")
        self.assertEqual(routed[0]["children"][1]["path"], "/root/management/resource")

    def test_empty_menu_route_keeps_pages_directly_under_root_prefix(self) -> None:
        routed = apply_frontend_page_route_hierarchy(
            [
                {
                    "name": "管理中心",
                    "unique_path": "",
                    "children": [
                        {
                            "pageId": "role_page",
                            "name": "角色管理",
                            "path": "/role",
                            "module_id": "access_control",
                            "description": "角色管理页面",
                        }
                    ],
                }
            ],
            root_route_prefix="/root",
        )

        self.assertEqual(routed[0]["unique_path"], "")
        self.assertEqual(routed[0]["children"][0]["path"], "/root/role")


if __name__ == "__main__":
    unittest.main()
