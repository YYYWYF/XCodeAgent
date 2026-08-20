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
    UiDesignGenerationPool,
    UiDesignGenerationTask,
    generate_page_entry,
)
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path

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

    def test_submit_dedups_and_worker_confirms(self) -> None:
        """同页重复提交只接受一次，worker 处理完落盘 confirmed 并退出活跃集。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task = _task(self.workspace, self.project_dir)
        started = threading.Event()
        release = threading.Event()

        def slow_generate(page, page_key, project_dir):
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

    def test_queued_status_persisted_while_worker_busy(self) -> None:
        """并发度为 1 时，worker 忙期间新提交页应落盘 queued，之后随 worker 一起完成。"""

        pool = UiDesignGenerationPool(concurrency=1)
        task_a = _task(self.workspace, self.project_dir, page_id="orders")
        task_b = _task(self.workspace, self.project_dir, page_id="dashboard")
        started = threading.Event()
        release = threading.Event()

        def slow_generate(page, page_key, project_dir):
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


if __name__ == "__main__":
    unittest.main()
