"""验证 SaaS 冲突被转为安全、可操作的提示，而非统一暴露 HTTP 状态。"""

import asyncio
import unittest
from unittest.mock import patch

import httpx

from app.config import Settings
from app.protocols.entity_tables import build_entity_tables_stream
from app.services.managed_table_client import ManagedTableClient, ManagedTableError


class ManagedTableErrorTests(unittest.TestCase):
    """HTTP 传输替身覆盖同为 409 的不同拒绝原因及未知响应。"""

    def failure(self, status: int, code: object, mode: str = "CREATE") -> ManagedTableError:
        """发送一次假请求并取得安全异常，不连接 SaaS 或真实数据库。"""

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            """原始服务错误放入敏感示例，验证界面不会透传正文。"""
            calls.append(request)
            return httpx.Response(status, json={"code": code, "message": "secret=do-not-expose"})

        client = ManagedTableClient(Settings(model_base_url="http://model.invalid", model_api_key="test",
            model_name="test", managed_database_service_key="test-service-key"),
            transport=httpx.MockTransport(handler))
        with self.assertRaises(ManagedTableError) as caught:
            client.request("12ab34cd56ef", "table-operations", body={"mode": mode, "sql": "test SQL"})
        self.assertEqual(len(calls), 1)
        self.assertNotIn("do-not-expose", str(caught.exception))
        self.assertNotIn("HTTP", str(caught.exception))
        return caught.exception

    def test_confirmed_structure_mismatch_offers_explicit_rebuild(self) -> None:
        """只有明确拒绝 CREATE 的结构冲突才建议重建。"""

        error = self.failure(409, "STRUCTURE_MISMATCH")
        self.assertEqual(error.issue["title"], "表结构已变更")
        self.assertEqual(error.issue["action"], "rebuild")
        self.assertIn("清空", str(error))
        self.assertFalse(error.uncertain)

    def test_busy_and_ownership_conflicts_never_offer_rebuild(self) -> None:
        """资源占用及归属冲突不能被当成新增字段而解锁重建。"""

        for code in ["RESOURCE_BUSY", "TABLE_BINDING_CONFLICT"]:
            with self.subTest(code=code):
                error = self.failure(409, code)
                self.assertEqual(error.issue["code"], code)
                self.assertEqual(error.issue["action"], "refresh")

    def test_unknown_or_wrong_status_remains_unconfirmed(self) -> None:
        """不能单凭 409 或错误正文猜测表变更；异常状态仍先核对结果。"""

        for status, code in [(409, "NEW_ERROR"), (409, None), (500, "STRUCTURE_MISMATCH")]:
            with self.subTest(status=status, code=code):
                error = self.failure(status, code)
                self.assertEqual(error.issue["code"], "RESULT_UNCONFIRMED")
                self.assertEqual(error.issue["action"], "refresh")
                self.assertTrue(error.uncertain)
        self.assertEqual(self.failure(409, "STRUCTURE_MISMATCH", mode="REBUILD").issue["action"], "refresh")

    def test_structured_issue_uses_complete_ag_ui_failure_lifecycle(self) -> None:
        """产品提示经现有 AG-UI 失败结果和快照返回，不新增传输合同。"""

        error = self.failure(409, "STRUCTURE_MISMATCH")

        async def collect() -> str:
            """收集一次真实协议输出，不发起建表动作。"""
            return "".join([part async for part in build_entity_tables_stream(payload={
                "threadId": "table-error", "runId": "table-error-run",
                "forwardedProps": {"entityTables": {"action": "execute", "workspaceRoot": "/tmp/table-error-test", "entityId": "Order"}},
            })])

        with patch("app.protocols.entity_tables.run_entity_table_action", side_effect=error):
            output = asyncio.run(collect())
        for event in ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_END", "STATE_SNAPSHOT", "RUN_FINISHED"]:
            self.assertIn(event, output)
        self.assertIn('"status":"failed"', output)
        self.assertIn('"issue":', output)
        self.assertIn("STRUCTURE_MISMATCH", output)
        self.assertIn("rebuild", output)
        self.assertNotIn("do-not-expose", output)
