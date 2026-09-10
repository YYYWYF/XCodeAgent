from __future__ import annotations

import unittest

from app.services.authorization_overlay import (
    compile_authorization_overlay,
    unit_authorization_slice,
)
from app.services.build_task_planner import build_task_candidate_contract_errors
from app.services.build_unit_compiler import annotate_unit_inputs


def _project_plan(*, enabled: bool = True) -> dict:
    """构造同时含页面、操作和 Endpoint ANY-OF 的最小确认权限事实。"""

    return {
        "artifact_type": "technical-plan",
        "pages": [{"pageId": "orders", "path": "/orders"}],
        "api_contracts": [
            {
                "id": "orders_api",
                "endpoints": [
                    {"id": "orders.list", "method": "GET", "path": "/orders"},
                    {"id": "orders.approve", "method": "POST", "path": "/orders/approve"},
                ],
            }
        ],
        "authorization_manifest": {
            "enabled": enabled,
            "bindings": {
                "pages": [{"pageId": "orders", "resourceKey": "orders"}],
                "actions": [
                    {
                        "pageId": "orders",
                        "actionId": "approve",
                        "resourceKey": "orders_approve",
                        "mode": "hidden",
                    }
                ],
                "endpoints": [
                    {
                        "endpointId": "orders.approve",
                        "operationResourceKeys": ["orders_approve", "orders_recheck"],
                    }
                ],
            },
            "defaultRoleAuthorization": {"roles": [{"roleSeedKey": "admin"}]},
            "resources": [
                {"resourceKey": "orders", "type": "page", "targetResourceRef": "page:orders"},
                {"resourceKey": "orders_approve", "type": "operation", "targetResourceRef": "action:orders:approve"},
                {"resourceKey": "orders_recheck", "type": "operation", "targetResourceRef": "action:orders:recheck"},
            ],
        },
    }


class AuthorizationOverlayTests(unittest.TestCase):
    """验证权限 Overlay 只投射当前 Unit 所需的确定性事实。"""

    def test_page_scope_exposes_page_and_action_but_not_api_client_judgement(self) -> None:
        """页面 Unit 只接收当前页面和顶层操作，API Client 不接收权限裁决。"""

        context = compile_authorization_overlay(
            _project_plan(),
            {
                "target": {"type": "page", "id": "orders"},
                "endpoint_ids": ["orders.list", "orders.approve"],
                "required_unit_ids": [
                    "frontend:api-client",
                    "backend:endpoint:orders_api:orders.approve",
                    "page:orders",
                ],
            },
        )

        self.assertEqual(
            unit_authorization_slice("page:orders", context),
            {
                "pages": [{"pageId": "orders", "resourceKey": "orders"}],
                "actions": [
                    {
                        "pageId": "orders",
                        "actionId": "approve",
                        "resourceKey": "orders_approve",
                        "mode": "hidden",
                        "resourceConstant": {
                            "group": "OPERATION",
                            "name": "ORDERS_APPROVE",
                            "resourceKey": "orders_approve",
                        },
                    }
                ],
            },
        )
        self.assertIsNone(unit_authorization_slice("frontend:api-client", context))
        self.assertEqual(
            context["authorization_constraints"]["frontendProjection"]["pages"],
            # 当前完整投影同时携带页面名称和菜单可见性，权限切片仍保持上方断言的边界。
            [{"pageId": "orders", "path": "/orders", "pageKey": "Orders", "name": "orders", "menu": True, "resourceGroup": "PAGE", "resourceName": "ORDERS"}],
        )
        self.assertEqual(
            context["authorization_constraints"]["authConstantsProjection"],
            [
                {"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"},
                {"name": "ORDERS_RECHECK_RESOURCE", "resourceKey": "orders_recheck"},
            ],
        )
        self.assertEqual(
            unit_authorization_slice("backend:endpoint:orders_api:orders.approve", context),
            {
                "endpoints": [
                    {
                        "apiContractId": "orders_api",
                        "endpointId": "orders.approve",
                        "httpMethod": "POST",
                        "path": "/orders/approve",
                        "operationResourceKeys": ["orders_approve", "orders_recheck"],
                        "semantics": "ANY_OF",
                    }
                ],
                "authConstants": [
                    {"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"},
                    {"name": "ORDERS_RECHECK_RESOURCE", "resourceKey": "orders_recheck"},
                ],
            },
        )

    def test_endpoint_unit_fingerprint_contains_only_its_operation_slice(self) -> None:
        """Endpoint Unit 指纹包含自身操作切片，不受默认角色种子变化影响。"""

        context = compile_authorization_overlay(
            _project_plan(),
            {
                "target": {"type": "endpoint", "id": "orders.approve", "api_contract_id": "orders_api"},
                "endpoint_ids": ["orders.approve"],
                "required_unit_ids": ["backend:endpoint:orders_api:orders.approve"],
            },
        )
        units = annotate_unit_inputs(
            {
                "backend:endpoint:orders_api:orders.approve": {
                    "id": "backend:endpoint:orders_api:orders.approve",
                    "task_ids": [],
                }
            },
            context,
            {},
        )

        source_refs = units["backend:endpoint:orders_api:orders.approve"]["source_refs"]
        self.assertEqual(source_refs["authorization"]["endpoints"][0]["semantics"], "ANY_OF")
        self.assertNotIn("defaultRoleAuthorization", source_refs["authorization"])
        self.assertTrue(units["backend:endpoint:orders_api:orders.approve"]["input_fingerprint"])

    def test_disabled_authorization_does_not_compile_overlay(self) -> None:
        """权限关闭时不向 Build Context 或 Unit 注入任何权限字段。"""

        context = compile_authorization_overlay(
            _project_plan(enabled=False),
            {"target": {"type": "page", "id": "orders"}, "authorization_constraints": {"stale": True}},
        )

        self.assertNotIn("authorization_constraints", context)
        self.assertIsNone(unit_authorization_slice("page:orders", context))

    def test_model_authorization_source_refs_are_rejected_for_regeneration(self) -> None:
        """模型不得写入平台拥有的权限来源字段。"""

        errors = build_task_candidate_contract_errors(
            {
                "tasks": [
                    {
                        "id": "page-orders",
                        "owner": "frontend",
                        "unit_id": "page:orders",
                        "source_refs": {"authorization": {"pages": []}},
                        "deliverables": [],
                    }
                ]
            }
        )

        self.assertIn(
            "Task page-orders must not output platform-owned source_refs.authorization.",
            errors,
        )


if __name__ == "__main__":
    unittest.main()
