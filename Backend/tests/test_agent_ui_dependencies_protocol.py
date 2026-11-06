"""验证安装控制面始终输出标准 AG-UI 生命周期，且批准不能被读取替代。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch
import unittest

from app.protocols.agent_ui_dependencies import build_dependency_stream, dependency_capabilities
from tests import test_agent_ui_dependency_install as fixtures


class DependencyProtocolTests(unittest.IsolatedAsyncioTestCase):
    """通过真实事件编码验证失败、确认身份及进度。"""

    async def asyncSetUp(self) -> None:
        """复用独立消费者夹具，不操作用户工程。"""
        self.fixture = fixtures.AgentUiDependencyPlanTests()
        self.fixture.setUp()

    async def asyncTearDown(self) -> None:
        """删除本用例的唯一临时消费者。"""
        self.fixture.tearDown()

    async def request(self, action: str, **fields: str) -> dict:
        """消费完整协议并检查标准生命周期，不在产品代码手写事件解析。"""
        payload = {"threadId": "dependency-thread", "runId": "dependency-run", "forwardedProps": {
            "agentUiDependencies": {"workspace": str(self.fixture.workspace), "action": action,
                                    "source": "local-vendor", **fields}}}
        events = []
        async for frame in build_dependency_stream(payload=payload):
            events.extend(json.loads(line[6:]) for line in frame.splitlines() if line.startswith("data: "))
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertIn("TEXT_MESSAGE_END", [event["type"] for event in events])
        self.assertIn("STATE_SNAPSHOT", [event["type"] for event in events])
        return events[-1]["result"]["agentUiDependencies"]

    async def test_plan_is_read_only(self) -> None:
        """读取具体 diff 不写工程、不执行安装。"""
        with patch("app.protocols.agent_ui_dependencies.execute_dependency_plan") as execute:
            result = await self.request("plan")
        self.assertEqual(result["status"], "completed")
        self.assertIn("changes", result["plan"])
        execute.assert_not_called()
        self.assertFalse((self.fixture.workspace / ".devagentstudio").exists())

    async def test_invalid_confirm_has_handled_lifecycle(self) -> None:
        """缺少计划身份也必须正常结束动作流。"""
        with patch("app.protocols.agent_ui_dependencies.execute_dependency_plan") as execute:
            result = await self.request("confirm")
        self.assertEqual(result["status"], "failed")
        execute.assert_not_called()

    async def test_plan_without_entry_uses_current_rsbuild_config(self) -> None:
        """官方动作默认由服务端读取真实布局，不向 Rsbuild 注入 Vite 入口。"""
        fixture = self.fixture
        fixture.package["scripts"] = {"build": "tsc -b && rsbuild build"}
        fixture.write_package()
        (fixture.target / "src/index.tsx").write_text("// entry\n")
        (fixture.target / "rsbuild.config.ts").write_text(
            "export default {source: {entry: {index: './src/index.tsx'}}, output: {distPath: {root: 'build'}}}")
        result = await self.request("plan")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["plan"]["entry"], "src/index.tsx")
        self.assertEqual(result["plan"]["outputDir"], "build")

    async def test_confirm_projects_actual_failure(self) -> None:
        """命令失败不能因 AG-UI RUN_FINISHED 被误记为安装成功。"""
        with patch("app.protocols.agent_ui_dependencies.execute_dependency_plan", return_value={
                "state": "failed", "message": "包不存在", "planId": "a" * 64}):
            result = await self.request("confirm", planId="a" * 64, reviewId="b" * 32)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["installation"]["state"], "failed")

    async def test_cannot_cancel_other_thread(self) -> None:
        """停止响应绑定真实原会话，不使用调用者提供的任意 runId。"""
        with patch("app.protocols.agent_ui_dependencies.read_installation", return_value={
                "state": "running", "threadId": "other", "runId": "original"}), \
             patch("app.protocols.agent_ui_dependencies.workspace_process_registry.cancel_run") as cancel:
            result = await self.request("cancel", planId="a" * 64)
        self.assertEqual(result["status"], "failed")
        cancel.assert_not_called()

    async def test_disconnect_waits_for_worker_cleanup(self) -> None:
        """订阅取消后等待同步命令 owner 收口，不留下继续写入的后台线程。"""
        from threading import Event
        from app.protocols.agent_ui_dependencies import _execute, AgentUiDependencyAction
        from app.services.workspace_process_registry import workspace_process_registry
        from app.services.agent_ui_dependency_install import process_run_id
        started, finished = Event(), Event()
        def execute(*args: object, **kwargs: object) -> dict:
            """模拟只能在原 run 停止后返回的同步执行者。"""
            started.set()
            while not workspace_process_registry.is_run_cancelled(process_run_id(self.fixture.workspace, "disconnect-test")):
                finished.wait(0.01)
            finished.set()
            return {"state": "stopped"}
        async def report(_progress: object) -> None:
            """此场景无需产生阶段回执。"""
            return None
        try:
            with patch("app.protocols.agent_ui_dependencies.execute_dependency_plan", side_effect=execute):
                request = AgentUiDependencyAction(workspace=str(self.fixture.workspace), action="confirm", planId="a" * 64, reviewId="b" * 32)
                task = asyncio.create_task(_execute(request, "thread", "disconnect-test", report))
                await asyncio.to_thread(started.wait, 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(finished.is_set())
        finally:
            workspace_process_registry.allow_run(process_run_id(self.fixture.workspace, "disconnect-test"))

    def test_health_publishes_current_contract(self) -> None:
        """声明安装控制面与不自动重放的边界。"""
        self.assertFalse(dependency_capabilities()["automaticReplay"])
        self.assertEqual(dependency_capabilities()["approval"], "explicit-plan-digest")
