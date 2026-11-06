"""T22 全部成功/阻断/输入错误使用标准 AG-UI 生命周期。"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.protocols.agent_ui_design_transfer import build_transfer_stream, transfer_capabilities
from tests.agent_ui_transfer_fixtures import transfer_fixture
from tests import test_agent_ui_design_transfer as execution_tests
from app.services.agent_ui_design_transfer_plan import prepare_transfer_plan


class TransferProtocolTests(unittest.IsolatedAsyncioTestCase):
    """直接消费真实后端编码，与正式规划和安装准入一起验证。"""

    async def result(self, workspace: Path, action: str, *, thread: str = "transfer-thread", **extra: str) -> dict:
        """每项动作均核对完整 AG-UI 生命周期和当前运行身份。"""
        payload = {"threadId": thread, "runId": "run-" + action, "forwardedProps": {
            "agentUiDesignTransfer": {"workspace": str(workspace), "action": action, **extra}}}
        events = []
        async for frame in build_transfer_stream(payload=payload):
            events.extend(json.loads(line[6:]) for line in frame.splitlines() if line.startswith("data: "))
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        for kind in ("TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT"):
            self.assertIn(kind, [event["type"] for event in events])
        result = events[-1]["result"]["agentUiDesignTransfer"]
        self.assertEqual(result["threadId"], thread)
        self.assertEqual(result["runId"], "run-" + action)
        return result

    async def test_execute_query_duplicate_and_cross_thread_cancel(self) -> None:
        """执行有进度/终态，查询和重入只返回原事实，其他会话不能取消。"""
        with TemporaryDirectory(prefix="T23 protocol ") as directory:
            workspace = Path(directory).resolve()
            _, _, installation = transfer_fixture(str(workspace))
            plan = prepare_transfer_plan(workspace, installation_plan_id=installation, target_source_root="src/助手")
            args = {"installationPlanId": installation, "planId": plan["planId"], "targetSourceRoot": "src/助手"}
            with patch("app.services.agent_ui_design_transfer.run_public_tool", side_effect=execution_tests.TransferExecutionTests().tool) as tool:
                result = await self.result(workspace, "initialize", **args)
                self.assertEqual(result["transfer"]["state"], "initialized", result)
                self.assertNotIn("plan", result["transfer"])
                self.assertEqual((await self.result(workspace, "initialize", **args))["transfer"]["state"], "initialized")
                self.assertEqual(tool.call_count, 1)
            self.assertEqual((await self.result(workspace, "get", planId=plan["planId"]))["transfer"]["state"], "initialized")
            self.assertEqual((await self.result(workspace, "cancel", thread="other", planId=plan["planId"]))["status"], "failed")

    async def test_cancel_targets_original_run(self) -> None:
        """取消请求绑定原运行，查询不会重新提交 initialize/confirm。"""
        with TemporaryDirectory(prefix="T23 cancellation ") as directory:
            workspace = Path(directory).resolve()
            transfer_fixture(str(workspace))
            record = {"planId": "a" * 64, "installationPlanId": "b" * 64, "state": "running",
                "threadId": "transfer-thread", "runId": "original-run", "action": "confirm"}
            with patch("app.protocols.agent_ui_design_transfer.read_transfer", return_value=record), \
                    patch("app.protocols.agent_ui_design_transfer.target_lock") as lock, \
                    patch("app.protocols.agent_ui_design_transfer.workspace_process_registry.cancel_run") as cancel:
                lock.return_value.locked.return_value = True
                result = await self.result(workspace, "cancel", planId=record["planId"])
                self.assertEqual(result["transfer"]["state"], "stopping")
                from app.services.agent_ui_design_transfer import process_run_id
                cancel.assert_called_once_with(process_run_id(workspace, "original-run"))

    async def test_plan_and_invalid_actions_finish_with_snapshot(self) -> None:
        """只读结果可审阅；试图执行或缺少安装身份均正常报告业务失败。"""
        with TemporaryDirectory(prefix="T22 protocol ") as directory:
            workspace = Path(directory).resolve()
            _, target, installation = transfer_fixture(str(workspace))
            for action, extra, expected in (("plan", {"installationPlanId": installation}, "completed"),
                    ("confirm", {"installationPlanId": installation}, "failed"), ("plan", {}, "completed")):
                with self.subTest(action=action, extra=extra):
                    payload = {"threadId": "transfer-thread", "runId": "transfer-run", "forwardedProps": {
                        "agentUiDesignTransfer": {"workspace": str(workspace), "action": action, **extra}}}
                    events = []
                    async for frame in build_transfer_stream(payload=payload):
                        events.extend(json.loads(line[6:]) for line in frame.splitlines() if line.startswith("data: "))
                    self.assertEqual(events[0]["type"], "RUN_STARTED")
                    self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                    for kind in ("TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT"):
                        self.assertIn(kind, [event["type"] for event in events])
                    result = events[-1]["result"]["agentUiDesignTransfer"]
                    self.assertEqual(result["status"], expected, result)
                    if expected == "completed":
                        self.assertTrue(result["plan"]["readOnly"])
                    self.assertFalse((target / "src/agent-ui").exists())

    def test_capabilities_advertise_reviewed_execution(self) -> None:
        """health 只发布当前已实现且需绑定计划的动作。"""
        capability = transfer_capabilities()
        self.assertEqual(capability["actions"], ["plan", "initialize", "confirm", "get", "cancel"])
        self.assertFalse(capability["readOnly"])
        self.assertTrue(capability["executionAvailable"])
