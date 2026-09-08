from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest

from app.services.planning_refresh_recovery import resolve_planning_refresh_state
from app.workspace.json_documents import write_json_atomic
from app.workspace.planning_run_documents import write_planning_run_atomic
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_pending_build_task_plan,
    write_pending_build_task_plan_atomic,
)
from tests.planning_run_fixtures import run
from tests.test_pending_build_task_plan_documents import _validated_plan


class PlanningRefreshRecoveryTests(unittest.TestCase):
    """验证刷新解析只选择当前最高优先级 Planning/Confirmation 事实。"""

    def setUp(self) -> None:
        """为每例创建独立工作区和一致的 PlanningRun 身份。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = temporary.name
        self.state = {"workspace": self.workspace}
        self.planning = run().model_copy(
            update={
                "planning_run_id": "planning-refresh-run",
                "workflow_run_id": "workflow-refresh-run",
                "thread_id": "thread-refresh-run",
                "input_fingerprint": "b" * 64,
                "base_confirmed_plan_digest": None,
            }
        )

    def _write_planning(self) -> None:
        """写入当前 active PlanningRun 的严格轻量快照。"""

        write_planning_run_atomic(self.state, self.planning)

    def _write_pending(self) -> dict:
        """写入与当前 PlanningRun 身份一致的合法 PendingPlan。"""

        write_pending_build_task_plan_atomic(
            self.state,
            _validated_plan(),
            planning_run_id=self.planning.planning_run_id,
            base_confirmed_plan_digest=None,
            input_fingerprint=self.planning.input_fingerprint,
            build_execution_scope=self.planning.build_execution_scope,
            created_at=self.planning.updated_at,
        )
        pending = load_pending_build_task_plan(self.state)
        assert pending is not None
        return pending

    def _resolve(self, *, active: bool) -> dict:
        """以可控进程注册表结果调用公共刷新解析器。"""

        return resolve_planning_refresh_state(
            self.workspace,
            lifecycle=None,
            runtime_active=lambda run_id: active and run_id == self.planning.workflow_run_id,
        )

    def test_browser_refresh_recovers_active_runtime_snapshot(self) -> None:
        """浏览器刷新时，进程仍持有 Workflow 就恢复 active PlanningRun。"""

        self._write_planning()

        recovered = self._resolve(active=True)

        self.assertEqual(recovered["source"], "active_planning_run")
        self.assertEqual(recovered["status"], "planning")
        self.assertEqual(recovered["workflowRunId"], self.planning.workflow_run_id)
        self.assertEqual(recovered["dagGeneration"]["status"], "active")

    def test_refresh_pending_wins_over_same_run_runtime(self) -> None:
        """同 Run 的 Pending 与仍登记 runtime 并存时，必须优先进入确认。"""

        self._write_planning()
        pending = self._write_pending()

        recovered = self._resolve(active=True)

        self.assertEqual(recovered["source"], "pending")
        self.assertEqual(recovered["status"], "awaiting_confirmation")
        self.assertEqual(
            recovered["draftDigest"],
            pending["draft_identity"]["draft_digest"],
        )
        self.assertEqual(
            recovered["confirmation"]["taskPlan"]["confirmationStatus"],
            "pending",
        )

    def test_backend_restart_marks_disk_active_run_interrupted(self) -> None:
        """Backend 重启后只有磁盘 active 证据时不恢复 Scheduler。"""

        self._write_planning()

        recovered = self._resolve(active=False)

        self.assertEqual(recovered["source"], "active_planning_run")
        self.assertEqual(recovered["status"], "planning_run_interrupted")
        self.assertIn("不会自动续跑", recovered["message"])

    def test_promoted_formal_ignores_pending_and_planning_residue(self) -> None:
        """Formal 已提升后，匹配的 Pending 与 planning-run 残留均不能覆盖它。"""

        self._write_planning()
        pending = self._write_pending()
        identity = pending["draft_identity"]
        formal = deepcopy(pending)
        formal.pop("draft_identity")
        formal.update(
            confirmation_status="confirmed",
            confirmed_at="2026-09-08T00:00:00Z",
            confirmed_from={
                "planning_run_id": identity["planning_run_id"],
                "draft_digest": identity["draft_digest"],
            },
        )
        write_json_atomic(build_task_plan_json_path(self.state), formal)

        recovered = self._resolve(active=True)

        self.assertEqual(recovered["source"], "confirmed_plan")
        self.assertEqual(recovered["status"], "confirmed")
        self.assertEqual(recovered["planningRunId"], self.planning.planning_run_id)

    def test_stale_planning_snapshot_cannot_override_newer_formal(self) -> None:
        """基于旧 Formal digest 的磁盘 active snapshot 必须让位给当前 Formal。"""

        formal = {
            **_validated_plan(),
            "confirmation_status": "confirmed",
            "confirmed_at": "2026-09-08T00:00:00Z",
            "confirmed_from": {
                "planning_run_id": "newer-run",
                "draft_digest": "c" * 64,
            },
        }
        write_json_atomic(build_task_plan_json_path(self.state), formal)
        stale = self.planning.model_copy(
            update={"base_confirmed_plan_digest": "a" * 64}
        )
        write_planning_run_atomic(self.state, stale)

        recovered = self._resolve(active=True)

        self.assertEqual(recovered["source"], "confirmed_plan")
        self.assertEqual(recovered["planningRunId"], "newer-run")

    def test_no_active_state_returns_idle(self) -> None:
        """没有 Pending、PlanningRun 或 Formal 时返回显式空状态。"""

        recovered = self._resolve(active=False)

        self.assertEqual(
            recovered,
            {
                "schemaVersion": "planning-refresh.v1",
                "source": "none",
                "status": "idle",
                "message": "当前没有可恢复的 Planning 或 Confirmation 状态。",
            },
        )


if __name__ == "__main__":
    unittest.main()
