"""UI 后台孤立任务的只读资格与原生恢复入口回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.graph.nodes.ui_confirmation import _apply_ui_design_action
from app.protocols.application_page_planning import _build_application_planning_recovery_projection
from app.services.ui_design_recovery import interrupted_ui_design_pages
from app.protocols.workflow.request import _ui_design_action


class UiDesignRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """确保健康连接不掩盖孤立 worker，但正常任务和确认门保持不变。"""

    def setUp(self):
        """构造当前正式页面与正在生成、活跃、已完成页面。"""
        self.state = {"workspace": "/workspace", "product_plan": {"pages": [{"pageId": "orphan"}, {"pageId": "active"}, {"pageId": "ready"}]}}
        self.manifest = {"confirmation_status": "pending_user_confirmation", "pages": [
            {"pageId": "orphan", "status": "generating"},
            {"pageId": "active", "status": "queued"},
            {"pageId": "ready", "status": "confirmed"},
            {"pageId": "removed", "status": "generating"},
        ]}

    def test_only_current_orphan_pages_are_reported_without_writes(self):
        """活跃页、完成页和已移出正式计划的页面都不能误进入恢复。"""
        pool = MagicMock()
        pool.pending_page_ids.return_value = {"active"}
        with patch("app.services.ui_design_recovery.load_ui_designs_json", return_value=self.manifest), patch("app.services.ui_design_recovery.get_ui_design_generation_pool", return_value=pool):
            self.assertEqual(interrupted_ui_design_pages(self.state, "/workspace"), ["orphan"])
        pool.submit.assert_not_called()

    def test_confirmed_artifact_has_no_generation_recovery(self):
        """已确认产物不能因旧生成状态被重放。"""
        with patch("app.services.ui_design_recovery.load_ui_designs_json", return_value={**self.manifest, "confirmation_status": "confirmed"}):
            self.assertEqual(interrupted_ui_design_pages(self.state, "/workspace"), [])

    async def test_ui_interrupt_keeps_confirmation_and_exposes_worker_recovery(self):
        """真实确认门仍 awaiting_user，投影单独报告孤立任务且不伪造 Graph 失败。"""
        snapshot = SimpleNamespace(values=self.state, tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"type": "application_planning_review", "artifact": "ui_designs"}, id="gate")])])
        with patch("app.protocols.application_page_planning.interrupted_ui_design_pages", return_value=["orphan"]):
            projection = await _build_application_planning_recovery_projection(workspace="/workspace", thread_id="thread", graph=MagicMock(), snapshot=snapshot, source=None)
        self.assertEqual(projection.classification, "awaiting_user")
        self.assertEqual(projection.to_payload()["uiGenerationRecovery"], {"pageIds": ["orphan"]})
        self.assertIsNone(projection.recovery_action_plan)

    async def test_refresh_delegates_to_existing_self_healing_without_regenerate(self):
        """恢复动作仅调用既有自愈，不能 replay 原 regenerate 或确认动作。"""
        latest = {"confirmation_status": "pending_user_confirmation", "pages": []}
        self.assertEqual(_ui_design_action({"ui_design_action": {"action": "refresh"}}), {"action": "refresh"})
        with patch("app.graph.nodes.ui_confirmation._latest_ui_designs", new=AsyncMock(return_value=latest)) as restore, patch("app.graph.nodes.ui_confirmation._enqueue_ui_design_generation", new=AsyncMock()) as enqueue:
            self.assertEqual(await _apply_ui_design_action(self.state, self.manifest, {"action": "refresh"}), latest)
        restore.assert_awaited_once_with(self.state, self.manifest)
        enqueue.assert_not_awaited()
