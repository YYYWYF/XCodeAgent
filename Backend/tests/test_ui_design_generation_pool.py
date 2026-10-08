from __future__ import annotations

import asyncio
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services.ui_design_generation_pool import (
    UI_DESIGN_STATUS_GENERATING,
    UI_DESIGN_STATUS_QUEUED,
    UiDesignGenerationCancelled,
    UiDesignGenerationPool,
    UiDesignGenerationTask,
    generate_page_entry,
)
from app.services.ui_design_generator import load_page_code, persist_page_code
from app.workspace.spec_documents import (
    load_ui_designs_json,
    ui_designs_json_path,
    write_ui_designs_json,
)

# 单页设计稿假代码：仅用于验证状态流转，不涉及真实 LLM 生成。
FAKE_CODE = (
    "import React from 'react';\n"
    "export default function Page() {\n"
    "  return <div>页面设计稿</div>;\n"
    "}\n"
)


def _spec_page(page_id: str) -> dict:
    """构造最小 ProductPlan 单页事实。"""

    return {
        "pageId": page_id,
        "name": f"{page_id}页",
        "path": f"/{page_id}",
        "description": "测试页面。",
        "information_items": [{"itemId": f"{page_id}-list", "label": "列表"}],
        "actions": [{"actionId": f"{page_id}-action", "name": "操作"}],
    }


def _task(workspace: str, project_dir: str, page_id: str = "orders", **overrides) -> UiDesignGenerationTask:
    """构造单页生成任务，page_id/page_key 默认一致便于去重。"""

    fields: dict = {
        "workspace": workspace,
        "project_id": "proj",
        "project_dir": project_dir,
        "page_id": page_id,
        "spec_page": _spec_page(page_id),
        "page_key": page_id.title(),
        "action": "regenerate",
        "template_id": "",
    }
    fields.update(overrides)
    return UiDesignGenerationTask(**fields)


class GeneratePageEntryTests(unittest.TestCase):
    """generate_page_entry 同步逻辑：regenerate / select_template 的成功与失败分支。"""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.workspace = self.tmp.name
        self.project_dir = str(Path(self.workspace) / ".xcodeagent" / "ui-design")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_regenerate_success_returns_confirmed(self) -> None:
        """regenerate 成功应返回 confirmed 并落盘代码。"""

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            return_value=FAKE_CODE,
        ):
            entry = generate_page_entry(_task(self.workspace, self.project_dir))

        self.assertEqual(entry["status"], "confirmed")
        self.assertEqual(entry["code"], FAKE_CODE)

    def test_regenerate_failure_returns_generation_failed(self) -> None:
        """regenerate 抛异常应汇总为 generation_failed 并带 error。"""

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            side_effect=RuntimeError("boom"),
        ):
            entry = generate_page_entry(_task(self.workspace, self.project_dir))

        self.assertEqual(entry["status"], "generation_failed")
        self.assertIn("boom", entry["error"])

    def test_screenshot_retry_failure_keeps_previous_preview(self) -> None:
        """截图模式重试失败时，之前可预览的设计稿文件仍应存在。"""

        task = _task(self.workspace, self.project_dir)
        persist_page_code(self.project_dir, task.page_key, FAKE_CODE)
        with patch(
            "app.agents.screenshot_ui_design.regenerate_screenshot_ui_page",
            return_value={"pageId": task.page_id, "status": "generation_failed", "error": "repair failed"},
        ):
            entry = generate_page_entry(task)
        self.assertEqual(entry["status"], "generation_failed")
        self.assertEqual(load_page_code(self.project_dir, task.page_key), FAKE_CODE)

    def test_select_template_success_returns_confirmed(self) -> None:
        """select_template 成功应返回 confirmed 并记录 template_id。"""

        with patch(
            "app.services.ui_design_generation_pool.load_template_source",
            return_value=FAKE_CODE,
        ), patch(
            "app.services.ui_design_generation_pool.generate_adjusted_page_react_code",
            return_value=FAKE_CODE,
        ):
            entry = generate_page_entry(
                _task(self.workspace, self.project_dir, action="select_template", template_id="template-dashboard")
            )

        self.assertEqual(entry["status"], "confirmed")
        self.assertEqual(entry["template_id"], "template-dashboard")

    def test_select_template_missing_template_returns_failed(self) -> None:
        """select_template 缺 template_id 应直接失败，不触发生成。"""

        entry = generate_page_entry(
            _task(self.workspace, self.project_dir, action="select_template", template_id="")
        )

        self.assertEqual(entry["status"], "generation_failed")


class UiDesignGenerationPoolTests(unittest.TestCase):
    """worker 池：去重、queued→generating→confirmed 流转、失败落盘。"""

    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.workspace = self.tmp.name
        self.project_dir = str(Path(self.workspace) / ".xcodeagent" / "ui-design")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _manifest_pages(self) -> list:
        manifest = load_ui_designs_json(
            ui_designs_json_path({"workspace": self.workspace, "project_id": "proj"})
        )
        return manifest.get("pages", [])

    def test_adjustment_merge_preserves_other_page_queue_state(self) -> None:
        """多页侧栏调整写回时不得把插件页最新 queued 状态覆盖成旧快照。"""

        pool = UiDesignGenerationPool(concurrency=1)
        write_ui_designs_json(
            {"workspace": self.workspace, "project_id": "proj"},
            {
                "schema_version": "ui-manifest.v3",
                "confirmation_status": "pending_user_confirmation",
                "pages": [
                    {"pageId": "chat", "status": "confirmed"},
                    {"pageId": "plugin", "status": "queued"},
                ],
            },
        )

        async def scenario() -> dict:
            """执行同一写锁下的外部页面合并。"""

            return await pool.merge_external_page_entries(
                self.workspace,
                "proj",
                [{"pageId": "chat", "status": "confirmed", "code_path": "new-chat.tsx"}],
            )

        result = asyncio.run(scenario())
        by_id = {page["pageId"]: page for page in result["pages"]}
        self.assertEqual(by_id["plugin"]["status"], "queued")
        self.assertEqual(by_id["chat"]["code_path"], "new-chat.tsx")

    def test_submit_dedups_and_worker_confirms(self) -> None:
        """同页重复提交只接受一次，worker 处理完落盘 confirmed 并退出活跃集。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task = _task(self.workspace, self.project_dir)
        started = threading.Event()
        release = threading.Event()

        def slow_generate(page, page_key, project_dir, *, should_cancel=None):
            started.set()
            release.wait(timeout=10)
            return FAKE_CODE

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            side_effect=slow_generate,
        ):
            async def scenario():
                accepted = await pool.submit([task, task])
                self.assertEqual(accepted, ["orders"])
                # 去重后仅一个活跃页。
                self.assertTrue(pool.is_active(self.workspace, "orders"))
                self.assertEqual(pool.pending_page_ids(self.workspace), {"orders"})
                # 等 worker 领取并写入 generating。
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                self.assertEqual(self._manifest_pages()[0]["status"], UI_DESIGN_STATUS_GENERATING)
                release.set()
                await pool._queue.join()
                self.assertFalse(pool.is_active(self.workspace, "orders"))
                return accepted

            accepted = asyncio.run(scenario())

        self.assertEqual(accepted, ["orders"])
        pages = self._manifest_pages()
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["status"], "confirmed")
        self.assertEqual(pages[0]["code"], FAKE_CODE)

    def test_submit_restarts_finished_workers(self) -> None:
        """历史 worker 已结束时，新提交必须重建 worker，不能永久停在 queued。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task = _task(self.workspace, self.project_dir)

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            return_value=FAKE_CODE,
        ):
            async def scenario():
                async def finished_worker() -> None:
                    """模拟热重载或流取消后已经结束的历史 worker。"""

                worker = asyncio.create_task(finished_worker())
                await worker
                pool._started = True
                pool._workers = [worker]

                accepted = await pool.submit([task])
                self.assertEqual(accepted, ["orders"])
                await asyncio.wait_for(pool._queue.join(), timeout=5)

            asyncio.run(scenario())

        self.assertEqual(self._manifest_pages()[0]["status"], "confirmed")

    def test_ensure_started_recovers_existing_queued_task(self) -> None:
        """轮询检查应恢复已登记但因 worker 退出而无人消费的历史 queued 任务。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task = _task(self.workspace, self.project_dir)

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            return_value=FAKE_CODE,
        ):
            async def scenario():
                async def finished_worker() -> None:
                    """模拟任务入队之后意外结束的历史 worker。"""

                worker = asyncio.create_task(finished_worker())
                await worker
                key = (task.workspace, task.page_id)
                pool._started = True
                pool._workers = [worker]
                pool._pending_ids.add(key)
                pool._tasks_by_id[key] = task
                pool._queue.put_nowait(task)

                await pool.ensure_started()
                await asyncio.wait_for(pool._queue.join(), timeout=5)

            asyncio.run(scenario())

        self.assertEqual(self._manifest_pages()[0]["status"], "confirmed")

    def test_queued_status_persisted_while_worker_busy(self) -> None:
        """并发度为 1 时，worker 忙期间新提交页应落盘 queued，之后随 worker 一起完成。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task_a = _task(self.workspace, self.project_dir, page_id="orders")
        task_b = _task(self.workspace, self.project_dir, page_id="dashboard")
        started = threading.Event()
        release = threading.Event()

        def slow_generate(page, page_key, project_dir, *, should_cancel=None):
            started.set()
            release.wait(timeout=10)
            return FAKE_CODE

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            side_effect=slow_generate,
        ):
            async def scenario():
                await pool.submit([task_a])
                # worker 被 A 阻塞（已写入 generating）。
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                accepted_b = await pool.submit([task_b])
                self.assertEqual(accepted_b, ["dashboard"])
                # A=generating，B=queued（worker 忙，尚未领取）。
                by_id = {page["pageId"]: page["status"] for page in self._manifest_pages()}
                self.assertEqual(by_id["orders"], UI_DESIGN_STATUS_GENERATING)
                self.assertEqual(by_id["dashboard"], UI_DESIGN_STATUS_QUEUED)
                release.set()
                await pool._queue.join()

            asyncio.run(scenario())

        pages = {page["pageId"]: page["status"] for page in self._manifest_pages()}
        self.assertEqual(pages["orders"], "confirmed")
        self.assertEqual(pages["dashboard"], "confirmed")

    def test_worker_failure_marks_generation_failed(self) -> None:
        """worker 内生成失败应落盘 generation_failed，且页面退出活跃集。"""

        pool = UiDesignGenerationPool(concurrency=2)
        task = _task(self.workspace, self.project_dir)

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            side_effect=RuntimeError("boom"),
        ):
            async def scenario():
                accepted = await pool.submit([task])
                self.assertEqual(accepted, ["orders"])
                await pool._queue.join()
                self.assertFalse(pool.is_active(self.workspace, "orders"))

            asyncio.run(scenario())

        pages = self._manifest_pages()
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["status"], "generation_failed")

    def test_cancel_page_while_generating_discards_result(self) -> None:
        """生成中取消：LLM 返回后结果不落盘，状态保持 cancelled，页面退出活跃集。

        取消语义：HTTP 请求不打断（避免网关半截流式计费），但结果丢弃、状态立即
        置 cancelled、前端可立即重试。worker 走到取消分支后不得用 confirmed
        覆写 cancelled 终态。
        """

        pool = UiDesignGenerationPool(concurrency=1)
        task = _task(self.workspace, self.project_dir)
        started = threading.Event()
        release = threading.Event()

        def slow_generate(page, page_key, project_dir, *, should_cancel=None):
            started.set()
            release.wait(timeout=10)
            return FAKE_CODE

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            side_effect=slow_generate,
        ):
            async def scenario():
                await pool.submit([task])
                # worker 已领取并写入 generating。
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                cancelled = await pool.cancel_page(self.workspace, "orders")
                self.assertTrue(cancelled)
                # 取消即写回 cancelled 终态、退出活跃集，无需等 LLM 返回。
                self.assertEqual(self._manifest_pages()[0]["status"], "cancelled")
                self.assertFalse(pool.is_active(self.workspace, "orders"))
                release.set()
                await pool._queue.join()

            asyncio.run(scenario())

        # LLM 返回后：代码不落盘、状态不被 confirmed 覆写。
        self.assertFalse((Path(self.project_dir) / "pages" / "orders" / "index.tsx").exists())
        pages = self._manifest_pages()
        self.assertEqual(pages[0]["status"], "cancelled")
        self.assertIn("取消", pages[0]["error"])

    def test_cancel_page_while_queued_skips_worker_pickup(self) -> None:
        """排队中取消：worker 领取时惰性跳过，状态保持 cancelled，不触发生成。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task_a = _task(self.workspace, self.project_dir, page_id="orders")
        task_b = _task(self.workspace, self.project_dir, page_id="dashboard")
        started = threading.Event()
        release = threading.Event()
        generated: list[str] = []

        def slow_generate(page, page_key, project_dir, *, should_cancel=None):
            generated.append(page_key)
            started.set()
            release.wait(timeout=10)
            return FAKE_CODE

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            side_effect=slow_generate,
        ):
            async def scenario():
                await pool.submit([task_a])
                # A 被 worker 阻塞，B 入队为 queued。
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                await pool.submit([task_b])
                # 取消还在队列里的 B：立即写回 cancelled。
                cancelled_b = await pool.cancel_page(self.workspace, "dashboard")
                self.assertTrue(cancelled_b)
                by_id = {page["pageId"]: page["status"] for page in self._manifest_pages()}
                self.assertEqual(by_id["dashboard"], "cancelled")
                release.set()
                await pool._queue.join()

            asyncio.run(scenario())

        # B 被惰性跳过，从未触发生成；终态保持 cancelled，A 正常 confirmed。
        self.assertNotIn("Dashboard", generated)
        by_id = {page["pageId"]: page["status"] for page in self._manifest_pages()}
        self.assertEqual(by_id["dashboard"], "cancelled")
        self.assertEqual(by_id["orders"], "confirmed")

    def test_cancelled_terminal_cannot_be_overwritten_by_worker_start(self) -> None:
        """停止先落盘时，已领取 worker 不得再把 cancelled 覆盖为 generating。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task = _task(self.workspace, self.project_dir)
        key = (task.workspace, task.page_id)

        async def scenario():
            pool._pending_ids.add(key)
            pool._tasks_by_id[key] = task
            self.assertTrue(await pool.cancel_page(self.workspace, task.page_id))
            with self.assertRaises(UiDesignGenerationCancelled):
                await pool._process(task)

        asyncio.run(scenario())

        page = self._manifest_pages()[0]
        self.assertEqual(page["status"], "cancelled")
        self.assertIn("取消", page["error"])

    def test_cancel_page_without_active_task_is_noop(self) -> None:
        """无在途任务时取消返回 False，不写状态（幂等）。"""

        pool = UiDesignGenerationPool(concurrency=1)

        async def scenario():
            return await pool.cancel_page(self.workspace, "orders")

        self.assertFalse(asyncio.run(scenario()))
        self.assertEqual(self._manifest_pages(), [])

    def test_cancel_page_after_restart_persists_cancelled_tombstone(self) -> None:
        """内存任务丢失后仍须取消磁盘 queued，且恢复任务不得覆盖 cancelled。"""

        write_ui_designs_json(
            {"workspace": self.workspace, "project_id": "proj"},
            {
                "schema_version": "ui-manifest.v3",
                "confirmation_status": "pending_user_confirmation",
                "pages": [
                    {
                        "pageId": "orders",
                        "page_key": "Orders",
                        "status": UI_DESIGN_STATUS_QUEUED,
                    }
                ],
            },
        )
        pool = UiDesignGenerationPool(concurrency=1)

        async def scenario():
            cancelled = await pool.cancel_page(self.workspace, "orders")
            self.assertTrue(cancelled)
            accepted = await pool.submit(
                [_task(self.workspace, self.project_dir, recovery=True)]
            )
            self.assertEqual(accepted, [])

        asyncio.run(scenario())

        page = self._manifest_pages()[0]
        self.assertEqual(page["status"], "cancelled")
        self.assertIn("取消", page["error"])

    def test_immediate_resubmit_does_not_revive_cancelled_attempt(self) -> None:
        """取消旧调用后立即重试时，旧结果不得覆写或清除新 attempt。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task = _task(self.workspace, self.project_dir)
        started = threading.Event()
        release = threading.Event()
        calls = 0
        old_code = FAKE_CODE.replace("页面设计稿", "旧设计稿")
        new_code = FAKE_CODE.replace("页面设计稿", "新设计稿")

        def generate_by_attempt(page, page_key, project_dir, *, should_cancel=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                release.wait(timeout=10)
                return old_code
            return new_code

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            side_effect=generate_by_attempt,
        ):
            async def scenario():
                await pool.submit([task])
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                self.assertTrue(await pool.cancel_page(self.workspace, "orders"))
                accepted = await pool.submit([_task(self.workspace, self.project_dir)])
                self.assertEqual(accepted, ["orders"])
                release.set()
                await asyncio.wait_for(pool._queue.join(), timeout=5)

            asyncio.run(scenario())

        page = self._manifest_pages()[0]
        self.assertEqual(calls, 2)
        self.assertEqual(page["status"], "confirmed")
        self.assertEqual(page["code"], new_code)

    def test_resubmit_after_cancel_clears_cancelled_flag(self) -> None:
        """取消后重新生成同页：新任务不被旧取消标记误杀，正常走到 confirmed。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task = _task(self.workspace, self.project_dir)

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            return_value=FAKE_CODE,
        ):
            async def scenario():
                await pool.submit([task])
                await pool.cancel_page(self.workspace, "orders")
                await pool._queue.join()
                # 取消后立即重新提交同页。
                accepted = await pool.submit([_task(self.workspace, self.project_dir)])
                self.assertEqual(accepted, ["orders"])
                await pool._queue.join()

            asyncio.run(scenario())

        self.assertEqual(self._manifest_pages()[0]["status"], "confirmed")

    def test_cancel_workspace_prevents_late_page_code_write(self) -> None:
        """删除栅栏在模型返回后阻止设计代码和最终 manifest 再写入工作区。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task = _task(self.workspace, self.project_dir)
        started = threading.Event()
        release = threading.Event()

        def slow_generate(page, page_key, project_dir, *, should_cancel=None):
            started.set()
            release.wait(timeout=10)
            return FAKE_CODE

        with patch(
            "app.services.ui_design_generation_pool.generate_page_react_code",
            side_effect=slow_generate,
        ):
            async def scenario():
                await pool.submit([task])
                self.assertTrue(await asyncio.to_thread(started.wait, 5))
                cancellation = asyncio.create_task(pool.cancel_workspace(self.workspace))
                await asyncio.sleep(0)
                release.set()
                result = await cancellation
                await pool._queue.join()
                return result

            result = asyncio.run(scenario())

        self.assertEqual(result["remainingPageIds"], [])
        self.assertFalse((Path(self.project_dir) / "pages" / "orders" / "index.tsx").exists())
        self.assertEqual(self._manifest_pages()[0]["status"], UI_DESIGN_STATUS_GENERATING)


if __name__ == "__main__":
    unittest.main()
