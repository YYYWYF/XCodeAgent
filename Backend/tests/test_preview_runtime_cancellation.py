"""覆盖预览诊断在途模型取消、迟到计划及修复工具安全收尾。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from langchain_openai import ChatOpenAI

from app.agents.execution_control import create_deep_agent
from app.protocols.preview_runtime import build_preview_runtime_stream
from app.protocols.preview_runtime import PreviewRuntimeInput, run_mutation, _cancellations
from app.services.model_request_cancellation import check_model_request, guard_model_response
from app.services.preview_runtime_guard import claim_maintenance, maintenance_owner
from app.services.preview_runtime_repair import save_repair
from app.services.preview_runtime_state import begin_attempt, finish_attempt
from app.services.run_workers import active_worker_runs, current_run_id
from app.services.workspace_process_registry import workspace_process_registry


class PreviewRuntimeCancellationTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 AG-UI、Agent 和模型 SDK，所有文件仅写入临时工作区。"""

    def setUp(self) -> None:
        """准备当前失败启动及隔离会话。"""
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = self.temp.name
        self.thread = Path(self.workspace).name
        self.run_id = f"preview-runtime:{self.thread}"
        root = Path(self.workspace)
        (root / ".devagentstudio").mkdir()
        (root / ".devagentstudio/application.json").write_text("{}")
        (root / "frontend").mkdir()
        (root / "frontend/main.ts").write_text("broken")
        self.attempt = begin_attempt(self.workspace)
        finish_attempt(self.workspace, {"status": "failed", "failed_stage": "frontend_start", "frontend": {"status": "failed"}})

    def tearDown(self) -> None:
        """删除本轮测试专用文件。"""
        self.temp.cleanup()

    async def request(self, action: str, **fields: str) -> dict:
        """完整消费协议流，停止结果也必须正常交付 AG-UI 终态。"""
        payload = {"threadId": self.thread, "runId": f"request-{action}", "forwardedProps": {
            "previewRuntime": {"workspace": self.workspace, "action": action, **fields},
        }}
        events = []
        async for frame in build_preview_runtime_stream(payload=payload):
            events.extend(json.loads(line[6:]) for line in frame.splitlines() if line.startswith("data: "))
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertIn("STATE_SNAPSHOT", [event["type"] for event in events])
        return events[-1]["result"]["previewRuntime"]

    async def test_cancel_interrupts_real_diagnosis_model_response(self) -> None:
        """停止必须到达真实 SDK 心跳读取，不能只把界面改为 stopping。"""
        started, release, closed = Event(), Event(), Event()
        identities = []

        class Stream(httpx.SyncByteStream):
            """保持模型响应在途，不等待最终模型文本即可响应停止。"""

            def __iter__(self):
                """在停止前后分别交付原始响应块。"""
                yield b": keep-alive\n\n"
                started.set()
                release.wait(3)
                yield b": keep-alive\n\n"
                raise AssertionError("停止后不能继续读取模型")

            def close(self) -> None:
                """验证真实模型连接确实关闭。"""
                closed.set()

        def respond(request: httpx.Request) -> httpx.Response:
            """检查嵌套 Agent 的请求使用预览运行身份。"""
            identities.append(current_run_id())
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Stream())

        with httpx.Client(transport=httpx.MockTransport(respond), event_hooks={
            "request": [check_model_request], "response": [guard_model_response],
        }) as client:
            model = ChatOpenAI(model="probe", api_key="test", base_url="https://model.invalid/v1",
                               streaming=True, max_retries=0, http_client=client)
            agent = create_deep_agent(model=model, tools=[])
            with patch("app.agents.create_agent_bundle", return_value=SimpleNamespace(repair_planner=agent)):
                diagnosis = asyncio.create_task(self.request("diagnose", attemptId=self.attempt))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 3))
                    self.assertEqual((await self.request("get"))["repair"]["status"], "running")
                    stopping = await self.request("cancel")
                    self.assertEqual(stopping["repair"]["status"], "stopping")
                    self.assertTrue(workspace_process_registry.is_run_cancelled(self.run_id))
                    self.assertIsNotNone(maintenance_owner(self.workspace))
                finally:
                    release.set()
                    result = await asyncio.wait_for(diagnosis, 5)
        self.assertEqual(identities, [self.run_id])
        self.assertTrue(closed.is_set())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair"]["status"], "stopped")
        self.assertEqual(result["repair"]["iteration"], 0)
        self.assertIsNone(maintenance_owner(self.workspace))
        self.assertNotIn(self.run_id, active_worker_runs())
        self.assertEqual((Path(self.workspace) / "frontend/main.ts").read_text(), "broken")

    async def test_cancelled_diagnosis_cannot_publish_late_confirmation(self) -> None:
        """即使已在途的规划正常返回，停止之后也不能重新进入待确认。"""
        started, release = Event(), Event()

        def prepare(*_args: object) -> dict:
            """模拟取消前已进入、取消后才返回的同步规划。"""
            started.set()
            release.wait(3)
            return {"status": "awaiting_confirmation", "planId": "late-plan", "iteration": 2}

        with patch("app.protocols.preview_runtime.prepare_repair", side_effect=prepare):
            diagnosis = asyncio.create_task(self.request("diagnose", attemptId=self.attempt))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                await self.request("cancel")
                self.assertIsNotNone(maintenance_owner(self.workspace))
            finally:
                release.set()
                result = await asyncio.wait_for(diagnosis, 5)
        self.assertEqual(result["repair"]["status"], "stopped")
        self.assertEqual(result["repair"]["iteration"], 2)
        self.assertIsNone(maintenance_owner(self.workspace))

    async def test_cancel_before_worker_dispatch_preserves_existing_budget(self) -> None:
        """确认或修订刚排队就被停止时，仍保留先前轮次和代码变更。"""
        previous = {"status": "awaiting_confirmation", "iteration": 2, "codeChangeSets": [{"id": "previous-change"}]}
        save_repair(self.workspace, self.thread, previous)
        claim_maintenance(self.workspace, self.thread, "confirm")
        cancelled = Event()
        cancelled.set()
        _cancellations[(self.workspace, self.thread)] = cancelled

        async def report(_progress: object) -> None:
            """确认还未派发时不应产生任何修复进度。"""
            self.fail("停止后不能派发工作")

        with patch("app.protocols.preview_runtime.execute_repair") as execute:
            result = await run_mutation(PreviewRuntimeInput(workspace=self.workspace, action="confirm"), self.thread, report)
        self.assertEqual(result["repair"]["status"], "stopped")
        self.assertEqual(result["repair"]["iteration"], 2)
        self.assertEqual(result["repair"]["codeChangeSets"], previous["codeChangeSets"])
        execute.assert_not_called()
        self.assertIsNone(maintenance_owner(self.workspace))

    async def test_cancelled_repair_preserves_changes_and_skips_validation(self) -> None:
        """修复工具完成安全收尾后保留代码和轮次，禁止后续验证和重启。"""
        started, release = Event(), Event()
        plan = {"decision": "repair", "strategy": "修复入口", "repair_tasks": [{
            "title": "修复入口", "description": "修复语法",
            "change_scope": [{"path": "frontend/main.ts", "operation": "modify"}],
        }]}
        with patch("app.agents.repair_planner.plan_build_failure_repair_with_repair_planner_agent", return_value=plan):
            prepared = await self.request("diagnose", attemptId=self.attempt)

        def execute(**_kwargs: object) -> dict:
            """记录当前工具已完成的写入，并等待取消后的安全退出。"""
            self.assertEqual(current_run_id(), self.run_id)
            (Path(self.workspace) / "frontend/main.ts").write_text("fixed")
            started.set()
            release.wait(3)
            return {"results": [{"status": "completed"}], "codeChangeSets": [{"id": "kept-change"}]}

        with patch("app.services.small_task.execute_small_task_batch", side_effect=execute), patch(
            "app.services.integration_test_runner.run_integration_checks",
        ) as checks, patch("app.services.project_launcher.launch_project_preview") as launch:
            repair = asyncio.create_task(self.request("confirm", planId=prepared["repair"]["planId"]))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                await self.request("cancel")
            finally:
                release.set()
                result = await asyncio.wait_for(repair, 5)
        self.assertEqual(result["repair"]["status"], "stopped")
        self.assertEqual(result["repair"]["iteration"], 1)
        self.assertEqual(result["repair"]["codeChangeSets"], [{"id": "kept-change"}])
        self.assertEqual((Path(self.workspace) / "frontend/main.ts").read_text(), "fixed")
        checks.assert_not_called()
        launch.assert_not_called()
        self.assertIsNone(maintenance_owner(self.workspace))
