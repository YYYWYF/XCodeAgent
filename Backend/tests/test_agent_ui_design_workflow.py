"""原生成池接入完整工程时，必须保持分组、当前身份和正式确认边界。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_agent_ui_design_generator import formal_facts
from app.services.ui_design_agent_surfaces import project_ui_design_pages
from app.services.ui_design_generation_pool import UiDesignGenerationPool, UiDesignGenerationTask
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path
from app.services.ui_design_manifest import persisted_ui_manifest
from app.protocols.workflow.request import _ui_design_action
from app.graph.nodes.ui_confirmation import _verified_ui_designs_for_confirmation
from app.graph.nodes.ui_confirmation import _apply_ui_design_action
from app.services.agent_ui_design_projection import current_project
from app.services.agent_ui_design_projection import project_agent_workflow_preview
from app.domain.application_lifecycle import ApplicationLifecycleError, ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.graph.application_planning_workflow import _ui_confirmation
from app.services.application_lifecycle import create_application_lifecycle, load_application_lifecycle, write_application_lifecycle


class AgentUiDesignWorkflowTests(unittest.IsolatedAsyncioTestCase):
    """使用实际队列与落盘，隔离昂贵的模型、安装和预览进程。"""

    async def test_successful_ui_action_clears_previous_lifecycle_failure(self) -> None:
        """显式 UI 动作成功后清理旧失败，纯读取确认卡不能替用户消除异常。"""
        for explicit in (False, True):
            with self.subTest(explicit=explicit), TemporaryDirectory() as directory:
                lifecycle = create_application_lifecycle(application_id="test", application_name="test")
                lifecycle = lifecycle.model_copy(update={
                    "initialization": lifecycle.initialization.model_copy(update={
                        "stage": ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
                        "status": ApplicationLifecycleStatus.FAILED,
                    }),
                    "error": ApplicationLifecycleError(code="application_planning_failed",
                        message="previous adjustment failed", occurredAt=datetime.now(UTC)),
                })
                write_application_lifecycle(directory, lifecycle)
                state = {"workspace": directory, "active_run_id": "current-ui-run",
                    "application_planning_interaction": {"action": "ui_action"} if explicit else {}}
                with patch("app.graph.application_planning_workflow.nodes.ui_confirmation",
                        new=AsyncMock(return_value={"status": "requires_user_input"})):
                    result = await _ui_confirmation(state)
                current = load_application_lifecycle(directory)
                self.assertIsNotNone(current)
                self.assertEqual(current.initialization.stage, ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION)
                self.assertEqual(current.initialization.status,
                    ApplicationLifecycleStatus.AWAITING_USER if explicit else ApplicationLifecycleStatus.FAILED)
                self.assertEqual(current.error is None, explicit)
                self.assertEqual(result["lifecycle"]["initialization"]["status"], current.initialization.status.value)

    async def test_shared_project_is_generated_once_and_never_confirms_pages(self) -> None:
        """任一 Agent 页面生成同组工程，普通页独立，重复提交不会再调用模型。"""
        with TemporaryDirectory() as directory:
            requirement, product = formal_facts()
            pages = project_ui_design_pages(product)
            tasks = [UiDesignGenerationTask(
                workspace=directory, project_id="test", project_dir=directory,
                page_id=page["pageId"], spec_page=page, page_key=page["pageId"].title(),
                action="regenerate", requirement_spec=requirement, product_plan=product,
            ) for page in pages]
            pool = UiDesignGenerationPool(2)
            entered = asyncio.Event()
            release = asyncio.Event()

            async def generate(task: UiDesignGenerationTask) -> None:
                """暂停真实 worker 边界，观察分组期间的去重与页面占用。"""
                entered.set()
                await release.wait()
                await pool._write_result(task, {"project_pages": [
                    {"pageId": page["pageId"], "page_key": page["page_key"], "status": "ready",
                     "design_asset": {"kind": "agent-ui-project", "revision": "a" * 64}}
                    for page in [{"pageId": member.page_id, "page_key": member.page_key}
                                 for member in task.members]
                ]})

            try:
                with patch.object(pool, "_process", side_effect=generate):
                    self.assertEqual(await pool.submit(tasks), ["home", "chat"])
                    await entered.wait()
                    self.assertEqual(await pool.submit(tasks), [])
                    self.assertEqual(pool.pending_page_ids(directory), {"home", "chat"})
                    release.set()
                    await asyncio.wait_for(pool._queue.join(), 2)
                manifest = load_ui_designs_json(ui_designs_json_path({"workspace": directory, "project_id": "test"}))
                self.assertEqual([page["status"] for page in manifest["pages"]], ["ready", "ready"])
                self.assertEqual(manifest["confirmation_status"], "pending_user_confirmation")
                self.assertEqual(pool.pending_page_ids(directory), set())
            finally:
                for worker in pool._workers:
                    worker.cancel()
                await asyncio.gather(*pool._workers, return_exceptions=True)


class AgentUiDesignProjectionTests(unittest.IsolatedAsyncioTestCase):
    """验证临时 launch、当前正式事实及正式确认门互不混淆。"""

    async def test_preview_start_failure_survives_workflow_projection(self) -> None:
        """显式打开预览失败后，AG-UI 必须保留本次原因而非覆盖为预览已关闭。"""
        reason = "多文件 Agent UI 环境未配置：请设置独立 Node、pnpm CLI 和工具配置根目录。"
        asset = {"kind": "agent-ui-project", "revision": "a" * 64, "pageIds": ["chat"]}
        page = {"pageId": "chat", "status": "ready", "design_asset": asset}
        manifest = {"confirmation_status": "pending_user_confirmation", "pages": [page]}
        with TemporaryDirectory() as directory:
            state = {"workspace": directory}
            project = SimpleNamespace(workspace=Path(directory))
            with patch("app.services.agent_ui_design_projection.current_project", return_value=(project, asset)), \
                    patch("app.services.agent_ui_design_projection.design_environment", side_effect=ValueError(reason)), \
                    patch("app.services.agent_ui_design_projection.agent_ui_design_previews.status", return_value=None):
                updated = await _apply_ui_design_action(state, manifest, {"action": "preview", "pageId": "chat"})
                projected = project_agent_workflow_preview({**state, "ui_designs": updated,
                    "clarification": {"pages": updated["pages"]}})
        self.assertEqual(projected["ui_designs"]["pages"][0]["preview_error"], reason)
        self.assertEqual(projected["clarification"]["pages"][0]["preview_error"], reason)
        self.assertNotIn("preview_error", page)

    def test_transient_urls_are_not_persisted_and_ready_does_not_confirm(self) -> None:
        """URL 不落盘，显式确认也不能用旧单页源码绕过多文件确认。"""
        design = {"confirmation_status": "pending_user_confirmation", "pages": [{
            "pageId": "chat", "status": "ready", "design_asset": {"kind": "agent-ui-project"},
            "project_preview": {"url": "http://127.0.0.1:1234/current/"}, "preview_error": "closed",
        }]}
        persisted = persisted_ui_manifest(design)
        self.assertNotIn("project_preview", persisted["pages"][0])
        self.assertNotIn("preview_error", persisted["pages"][0])
        requirement, product = formal_facts()
        product["pages"] = [product["pages"][1]]
        result, errors = _verified_ui_designs_for_confirmation(
            {"requirement_spec": requirement, "product_plan": product}, design)
        self.assertEqual(result["pages"][0]["status"], "generation_failed")
        self.assertEqual(result["confirmation_status"], "pending_user_confirmation")
        self.assertTrue(errors)

    def test_preview_and_cancel_actions_drop_file_and_remote_parameters(self) -> None:
        """产品协议只接受当前 pageId，任意路径和 URL 不能流向工具宿主。"""
        for action in ("preview", "cancel"):
            self.assertEqual(_ui_design_action({"ui_design_action": {
                "action": action, "pageId": "chat", "sourcePath": "/etc/passwd", "url": "https://invalid.test"
            }}), {"action": action, "pageId": "chat"})

    def test_invalid_draft_or_changed_facts_fail_before_reading_assets(self) -> None:
        """草稿不能穿越；旧正式事实不得启动预览或产生文件读取权限。"""
        requirement, product = formal_facts()
        facts = hashlib.sha256(json.dumps({"requirement": requirement, "product": product},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with TemporaryDirectory() as directory:
            state = {"workspace": directory, "requirement_spec": requirement, "product_plan": product}
            for asset in ({"kind": "agent-ui-project", "draft": "../../escape", "factsDigest": facts},
                          {"kind": "agent-ui-project", "draft": "a" * 32, "factsDigest": "b" * 64}):
                with patch("app.services.agent_ui_design_projection.validate_design_project") as validate:
                    with self.assertRaises(ValueError):
                        current_project(state, {"pageId": "chat", "design_asset": asset})
                    validate.assert_not_called()

    def test_projection_rejects_malformed_asset_without_mutating_checkpoint(self) -> None:
        """不可信落盘资产只能投影错误，不能把临时 URL 写回原 Graph 对象。"""
        with TemporaryDirectory() as directory:
            page = {"pageId": "chat", "status": "ready", "design_asset": "invalid"}
            original = {"workspace": directory, "ui_designs": {"pages": [page]},
                        "clarification": {"pages": [page]}}
            projected = project_agent_workflow_preview(original)
            self.assertIn("preview_error", projected["ui_designs"]["pages"][0])
            self.assertNotIn("preview_error", page)
            self.assertNotIn("project_preview", page)

    async def test_cancel_one_page_cancels_shared_job_and_keeps_other_work(self) -> None:
        """共享文件只有一个 owner；停止任一成员会收口整组，不能留下假生成状态。"""
        with TemporaryDirectory() as directory:
            requirement, product = formal_facts()
            tasks = [UiDesignGenerationTask(workspace=directory, project_id="test", project_dir=directory,
                page_id=page["pageId"], spec_page=page, page_key=page["pageId"].title(), action="regenerate",
                requirement_spec=requirement, product_plan=product) for page in project_ui_design_pages(product)]
            pool = UiDesignGenerationPool(1)
            try:
                # 保留队列，避免测试依赖 worker 的线程调度时机。
                with patch.object(pool, "_ensure_started"):
                    await pool.submit(tasks)
                self.assertTrue(await pool.cancel_page(directory, "chat"))
                manifest = load_ui_designs_json(ui_designs_json_path({"workspace": directory, "project_id": "test"}))
                self.assertEqual({page["status"] for page in manifest["pages"]}, {"cancelled"})
                self.assertEqual(pool.pending_page_ids(directory), set())
            finally:
                for worker in pool._workers:
                    worker.cancel()


if __name__ == "__main__":
    unittest.main()
