from __future__ import annotations

import unittest

from app.services.frontend_page_tree import (
    apply_frontend_page_route_hierarchy,
    group_pages_into_menu_tree,
)
from app.services.page_dependencies import validate_project_plan_dependencies


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

    def test_single_visible_page_with_dynamic_detail_does_not_create_parent_menu(self) -> None:
        tree = group_pages_into_menu_tree(
            [
                {
                    "pageId": "core_management_list_page",
                    "name": "核心业务管理列表页",
                    "path": "/page/core-management",
                    "module_id": "core_management",
                    "description": "展示核心业务管理数据。",
                },
                {
                    "pageId": "core_management_detail_page",
                    "name": "核心业务管理详情页",
                    "path": "/page/core-management/:id",
                    "module_id": "core_management",
                    "description": "展示单条核心业务管理记录详情。",
                },
            ]
        )

        self.assertEqual(
            [node["pageId"] for node in tree],
            ["core_management_list_page", "core_management_detail_page"],
        )

    def test_menu_path_equal_to_direct_page_path_is_demoted_to_group(self) -> None:
        routed = apply_frontend_page_route_hierarchy(
            [
                {
                    "name": "核心业务管理",
                    "unique_path": "/page/core-management",
                    "children": [
                        {
                            "pageId": "core_management_list_page",
                            "name": "核心业务管理列表页",
                            "path": "/page/core-management",
                            "module_id": "core_management",
                            "description": "展示核心业务管理数据。",
                        },
                        {
                            "pageId": "core_management_detail_page",
                            "name": "核心业务管理详情页",
                            "path": "/page/core-management/:id",
                            "module_id": "core_management",
                            "description": "展示单条核心业务管理记录详情。",
                        },
                    ],
                }
            ],
            root_route_prefix="/page",
        )

        self.assertEqual(routed[0]["unique_path"], "")
        self.assertEqual(routed[0]["children"][0]["path"], "/page/core-management")
        self.assertEqual(routed[0]["children"][1]["path"], "/page/core-management/:id")

    def test_validation_reports_menu_path_equal_to_direct_page_path(self) -> None:
        errors = validate_project_plan_dependencies(
            {
                "frontend_pages": [
                    {
                        "name": "核心业务管理",
                        "unique_path": "/page/core-management",
                        "children": [
                            {
                                "pageId": "core_management_list_page",
                                "name": "核心业务管理列表页",
                                "path": "/page/core-management",
                                "module_id": "core_management",
                                "description": "展示核心业务管理数据。",
                                "references": {
                                    "permissions": [],
                                    "endpoint_dependencies": [],
                                    "navigation_targets": [],
                                },
                            }
                        ],
                    }
                ],
                "api_contracts": [],
                "data_sources": [],
            }
        )

        self.assertIn(
            "Menu unique_path conflicts with direct page path: /page/core-management.",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
