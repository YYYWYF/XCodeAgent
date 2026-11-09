"""UI 后台孤立任务的只读资格与原生恢复入口回归。"""

import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.graph.nodes.ui_confirmation import _apply_ui_design_action
from app.protocols.application_planning_recovery_projection import project_planning_recovery
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
        with patch("app.protocols.application_planning_recovery_projection.interrupted_ui_design_pages", return_value=["orphan"]):
            projection = await project_planning_recovery(workspace="/workspace", thread_id="thread", graph=MagicMock(), snapshot=snapshot, source=None)
        self.assertEqual(projection.classification, "awaiting_user")
        self.assertEqual(projection.to_payload()["uiGenerationRecovery"], {"pageIds": ["orphan"]})
        self.assertIsNone(projection.recovery_action_plan)

    async def test_refresh_delegates_to_read_only_projection_without_regenerate(self):
        """刷新只读当前投影，不能重放原生成或确认动作。"""
        latest = {"confirmation_status": "pending_user_confirmation", "pages": []}
        self.assertEqual(_ui_design_action({"ui_design_action": {"action": "refresh"}}), {"action": "refresh"})
        with patch("app.graph.nodes.ui_confirmation._latest_ui_designs", new=AsyncMock(return_value=latest)) as restore, patch("app.graph.nodes.ui_confirmation._enqueue_ui_design_generation", new=AsyncMock()) as enqueue:
            self.assertEqual(await _apply_ui_design_action(self.state, self.manifest, {"action": "refresh"}), (latest, None))
        restore.assert_awaited_once_with(self.state, self.manifest)
        enqueue.assert_not_awaited()

    async def test_ui_action_updates_owner_without_reconfirming_requirements(self):
        """同一个 UI 审阅阶段的生成/刷新换 Run 后，冷读必须匹配最新 checkpoint 的 owner。"""
        from app.graph.application_planning_workflow import _ui_confirmation
        from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
        from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle, load_application_lifecycle
        with tempfile.TemporaryDirectory() as workspace:
            lifecycle = create_application_lifecycle(application_id="ui-owner", application_name="归属验证",
                initialization_thread_id="ui-thread", active_run_id="previous-run")
            lifecycle.initialization.stage = ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION
            lifecycle.initialization.status = ApplicationLifecycleStatus.AWAITING_USER
            write_application_lifecycle(workspace, lifecycle)
            state = {"workspace": workspace, "active_run_id": "ui-action-run"}
            result = {"status": "requires_user_input", "phase": "ui_confirmation"}
            with (patch("app.graph.application_planning_workflow.design_artifact_node_state", return_value=state),
                  patch("app.graph.application_planning_workflow.nodes.ui_confirmation", new=AsyncMock(return_value=result)),
                  patch("app.graph.application_planning_workflow.design_node_update", side_effect=lambda _state, _node, update: update)):
                update = await _ui_confirmation(state)
            current = load_application_lifecycle(workspace)
            self.assertEqual(current.active_run_id, "ui-action-run")
            self.assertEqual(current.initialization.stage, ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION)
            self.assertEqual(current.initialization.status, ApplicationLifecycleStatus.AWAITING_USER)
            self.assertEqual(update["status"], "requires_user_input")
