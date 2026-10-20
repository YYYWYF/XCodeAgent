"""旧任务文件不能覆盖新 checkpoint 中真实存在的上游确认门。"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.services.planning_confirmation_projection import prioritize_checkpoint_confirmation


class PlanningConfirmationProjectionTests(unittest.IsolatedAsyncioTestCase):
    """只验证确认展示归属，不修改 Agent、磁盘或执行状态。"""

    def setUp(self) -> None:
        """构造同一 owner 的旧任务草稿和当前需求确认现场。"""
        self.pending = {"schemaVersion": "planning-refresh.v1", "source": "pending_plan",
                        "status": "awaiting_confirmation", "workflowRunId": "old-task-run",
                        "ownerSessionId": "owner"}
        self.lifecycle = SimpleNamespace(active_run_id="new-design-run",
                                         initialization=SimpleNamespace(thread_id="thread"))
        self.snapshot = SimpleNamespace(values={"active_run_id": "new-design-run",
            "owner_session_id": "owner", "workflow_scope": "application_planning"}, tasks=(
                SimpleNamespace(interrupts=(SimpleNamespace(id="gate-id", value={
                    "type": "application_planning_review", "artifact": "requirement_document"}),)),))

    async def _project(self) -> dict:
        """在原生快照边界注入现场，保留公共确认载荷解释逻辑。"""
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=self.snapshot))
        with patch("app.services.application_lifecycle.load_application_lifecycle", return_value=self.lifecycle), patch(
            "app.graph.application_planning_workflow.application_planning_graph_for_request",
            AsyncMock(return_value=graph),
        ):
            return await prioritize_checkpoint_confirmation("/workspace", self.pending)

    async def test_new_checkpoint_gate_takes_precedence_even_when_old_plan_is_valid(self) -> None:
        """正常返回上游即可触发，不要求文件损坏或依赖哈希已变化。"""
        original = dict(self.pending)
        self.assertEqual((await self._project())["status"], "idle")
        self.assertEqual(self.pending, original)

    async def test_old_plan_unchanged_without_native_confirmation(self) -> None:
        """生命周期指针变化本身不能停用仍有效的旧任务入口。"""
        self.snapshot.tasks = ()
        self.assertIs(await self._project(), self.pending)

    async def test_other_owner_cannot_hide_pending_confirmation(self) -> None:
        """其他用户会话的新门不能抢占该草稿的确认归属。"""
        self.snapshot.values["owner_session_id"] = "other-owner"
        self.assertIs(await self._project(), self.pending)

    async def test_stale_checkpoint_cannot_hide_pending_confirmation(self) -> None:
        """旧 Run checkpoint 与当前定位不符时不能修改展示。"""
        self.snapshot.values["active_run_id"] = "stale-run"
        self.assertIs(await self._project(), self.pending)

    async def test_same_run_keeps_task_confirmation(self) -> None:
        """当前任务自己的确认入口继续保留。"""
        self.lifecycle.active_run_id = "old-task-run"
        self.assertIs(await self._project(), self.pending)
