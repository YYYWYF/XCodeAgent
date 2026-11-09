"""模拟残留任务、迭代后生成及只读刷新，模型之外使用生产实现。"""

import asyncio
import importlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols.ui_design_generation import build_ui_design_generation_stream
from app.services.ui_design_generation_control import inspect_page, stop_page
from app.services.ui_design_generation_pool import UiDesignGenerationPool
from app.services.ui_design_generator import persist_page_code, load_page_code
from app.services.iteration_service import start_iteration
from app.workspace.spec_documents import write_ui_designs_json, ui_designs_json_path
from tests.test_iteration_service import _bootstrapped_workspace, _request
from tests.test_ui_design_generation_pool import _task, _spec_page, FAKE_CODE

node = importlib.import_module("app.graph.nodes.ui_confirmation")


class UiDesignGenerationControlTests(unittest.IsolatedAsyncioTestCase):
    """测试真实文件、AG-UI 生命周期、任务代次及同步模型线程退出。"""

    async def asyncSetUp(self):
        """每例建立独立工作区与后台实例。"""
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = str(Path(self.tmp.name).resolve())
        self.state = {"workspace": self.workspace, "project_id": "proj",
            "product_plan": {"pages": [_spec_page("orders")]}}
        self.pool = UiDesignGenerationPool(1)
        self.release = threading.Event()

    async def asyncTearDown(self):
        """真实线程退出后才清理临时文件，禁止取消掩盖迟到写入。"""
        self.release.set()
        await asyncio.wait_for(self.pool._queue.join(), 5)
        for worker in self.pool._workers:
            worker.cancel()
        await asyncio.gather(*self.pool._workers, return_exceptions=True)
        self.tmp.cleanup()

    async def action(self, action, token=None):
        """经生产协议发起查询或停止，并核对完整 AG-UI 生命周期。"""
        request = {"action": action, "workspaceRoot": self.workspace, "pageId": "orders"}
        if token:
            request["stopToken"] = token
        frames = []
        with patch("app.services.ui_design_generation_pool._POOL", self.pool):
            async for raw in build_ui_design_generation_stream(payload={"forwardedProps": {"uiDesignGeneration": request}}):
                frames.extend(json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:"))
        self.assertEqual(frames[0]["type"], "RUN_STARTED")
        self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
        self.assertIn("STATE_SNAPSHOT", [frame["type"] for frame in frames])
        self.assertIn("TEXT_MESSAGE_END", [frame["type"] for frame in frames])
        return next(frame["value"] for frame in frames if frame.get("name") == "ui-design-generation"
            and frame.get("value", {}).get("status") in {"completed", "failed"})

    def manifest(self, status, identity):
        """落盘与当前合同一致的页面状态，可有或无任务标识。"""
        page = {"pageId": "orders", "page_key": "Orders", "status": status}
        if identity:
            page["generation_id"] = "orphan-generation"
        value = {"confirmation_status": "pending_user_confirmation", "pages": [page]}
        write_ui_designs_json(self.state, value)
        return value

    async def test_orphan_matrix_idempotent_stop_and_refresh_never_submits(self):
        """三种残留状态有无标识均可停止，重复请求成功且刷新不触发模型或写盘。"""
        for status in ("queued", "generating", "stopping"):
            for identity in (False, True):
                with self.subTest(status=status, identity=identity):
                    original = self.manifest(status, identity)
                    path = ui_designs_json_path(self.state)
                    before = path.read_bytes()
                    current = await self.action("get")
                    self.assertEqual(current["taskState"], "orphaned")
                    self.assertEqual(path.read_bytes(), before)
                    with patch("app.services.ui_design_generation_pool._POOL", self.pool), patch.object(self.pool, "submit") as submit:
                        projected = await node._latest_ui_designs(self.state, original)
                        self.assertEqual(projected["pages"][0]["status"], "generation_failed")
                        self.assertEqual(path.read_bytes(), before)
                        stopped = await self.action("cancel", current["stopToken"])
                        self.assertTrue(stopped["settled"])
                        self.assertEqual(stopped["pageStatus"], "cancelled")
                        for _ in range(3):
                            self.assertTrue((await self.action("cancel", current["stopToken"]))["settled"])
                            refreshed, _ = await node._apply_ui_design_action(self.state, original, {"action": "refresh"})
                            self.assertEqual(refreshed["pages"][0]["status"], "cancelled")
                        submit.assert_not_called()

    async def test_changed_manifest_and_backend_instance_reject_old_token(self):
        """已改变的页面与重启后的实例都不能使用旧停止凭据。"""
        self.manifest("generating", False)
        current = await inspect_page(self.pool, self.workspace, "orders")
        self.manifest("queued", False)
        with self.assertRaisesRegex(ValueError, "已变化"):
            await stop_page(self.pool, self.workspace, "orders", current["stopToken"])
        self.manifest("generating", False)
        with self.assertRaisesRegex(ValueError, "已变化"):
            await stop_page(UiDesignGenerationPool(1), self.workspace, "orders", current["stopToken"])

    async def test_pending_submission_cannot_be_closed_as_orphan(self):
        """有尚未退出的 Graph 时，不清理旧稿、不签发停止成功。"""
        self.manifest("generating", False)
        with patch("app.protocols.workflow.run_control.workflow_run_registry.active_run_ids", return_value={"submitting"}):
            current = await self.action("get")
            self.assertEqual(current["taskState"], "preparing")
            self.assertEqual((await self.action("cancel", current["stopToken"]))["status"], "failed")
        self.assertEqual((await self.action("get"))["pageStatus"], "generating")

    async def test_task_finishing_between_get_and_cancel_is_success(self):
        """查询后的同一代自然完成时停止幂等成功，保留已生成内容。"""
        project_dir = str(Path(self.workspace) / ".devagentstudio/ui-design")
        with patch("app.services.ui_design_generation_task.generate_page_react_code", return_value=FAKE_CODE):
            await self.pool.submit([_task(self.workspace, project_dir)])
            current = await inspect_page(self.pool, self.workspace, "orders")
            await self.pool._queue.join()
            result = await stop_page(self.pool, self.workspace, "orders", current["stopToken"])
        self.assertTrue(result["settled"])
        self.assertEqual(result["pageStatus"], "confirmed")
        self.assertEqual(load_page_code(project_dir, "Orders"), FAKE_CODE)

    async def test_new_iteration_real_worker_stop_refresh_and_explicit_regenerate(self):
        """真实发起迭代后停止同步模型线程，刷新零新增调用，显式生成才创建下一代。"""
        root = Path(self.workspace)
        _bootstrapped_workspace(root)
        self.manifest("confirmed", True)
        result = await asyncio.to_thread(start_iteration, _request(root))
        self.assertTrue(result.cleared)
        project_dir = str(root / ".devagentstudio/ui-design")
        persist_page_code(project_dir, "Orders", "上一轮设计稿")
        started = threading.Event()
        calls = []

        def generate(*args, **kwargs):
            """控制模型等待窗口，取消后由生产线程和池执行实际收尾。"""
            calls.append(1)
            started.set()
            if not self.release.wait(5):
                raise RuntimeError("模拟生成超时")
            return FAKE_CODE

        with patch("app.services.ui_design_generation_pool._POOL", self.pool), patch(
                "app.services.ui_design_generation_task.generate_page_react_code", side_effect=generate):
            await self.pool.submit([_task(self.workspace, project_dir)])
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            # 模拟清单丢失 generation_id，后台依然从真实 owner 签发凭据。
            manifest = self.pool._read_manifest(self.workspace, "proj")
            manifest["pages"][0].pop("generation_id")
            write_ui_designs_json(self.state, manifest)
            current = await self.action("get")
            self.assertEqual(current["taskState"], "active")
            stopping = asyncio.create_task(self.action("cancel", current["stopToken"]))
            await asyncio.sleep(.02)
            self.assertFalse(stopping.done())
            self.assertEqual(self.pool._read_manifest(self.workspace, "proj")["pages"][0]["status"], "stopping")
            self.release.set()
            self.assertTrue((await stopping)["settled"])
            self.assertEqual(load_page_code(project_dir, "Orders"), "上一轮设计稿")
            for _ in range(3):
                latest, _ = await node._apply_ui_design_action(self.state, manifest, {"action": "refresh"})
                self.assertEqual(latest["pages"][0]["status"], "cancelled")
            self.assertEqual(len(calls), 1)
            await self.pool.submit([_task(self.workspace, project_dir)])
            await self.pool._queue.join()
            self.assertEqual(len(calls), 2)
            self.assertEqual((await self.action("cancel", current["stopToken"]))["status"], "failed")
