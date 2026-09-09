"""Endpoint API 设计独立 AG-UI 协议测试。"""

from __future__ import annotations

import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.protocols.endpoint_designs import (
    endpoint_designs_capabilities,
)
from app.routes.endpoint_designs import endpoint_designs_router


class EndpointDesignsProtocolTests(unittest.TestCase):
    """验证独立查询端点的能力描述与完整事件生命周期。"""

    def setUp(self) -> None:
        """创建只包含 Endpoint 设计查询路由的测试应用。"""

        self.app = FastAPI()
        self.app.include_router(endpoint_designs_router)
        self.client = TestClient(self.app)

    def test_capabilities_are_workflow_independent(self) -> None:
        """能力声明包含查询、准备和保存，且保持与主工作流隔离。"""

        capabilities = endpoint_designs_capabilities()
        self.assertEqual(capabilities["endpoint"], "/endpoint-designs/run")
        self.assertEqual(capabilities["actions"], ["get", "prepare", "save"])
        self.assertTrue(capabilities["workflowIndependent"])
        self.assertFalse(capabilities["readOnly"])

    def test_invalid_request_still_finishes_ag_ui_run(self) -> None:
        """业务校验失败也必须发送失败状态和 RUN_FINISHED。"""

        response = self.client.post(
            "/endpoint-designs/run",
            json={
                "threadId": "thread-endpoint-test",
                "runId": "run-endpoint-test",
                "forwardedProps": {"endpointDesigns": {"action": "get"}},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"status":"failed"', response.text)
        self.assertIn("RUN_STARTED", response.text)
        self.assertIn("STATE_SNAPSHOT", response.text)
        self.assertIn("RUN_FINISHED", response.text)

    def test_get_action_is_consumed_before_detail_validation(self) -> None:
        """get 动作字段应由协议层消费，合法查询返回 pending 详情而不是 extra 错误。"""

        response = self.client.post(
            "/endpoint-designs/run",
            json={
                "threadId": "thread-endpoint-test",
                "runId": "run-endpoint-test",
                "forwardedProps": {
                    "endpointDesigns": {
                        "action": "get",
                        "workspaceRoot": "C:/workspace",
                        "apiContractId": "orders-api",
                        "endpointId": "orders.list",
                    }
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"status":"completed"', response.text)
        self.assertIn('"status":"pending"', response.text)
        self.assertNotIn("Extra inputs are not permitted", response.text)
