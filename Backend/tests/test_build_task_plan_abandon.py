"""T7.4 PendingPlan Abandon 的身份隔离、文件安全与 AG-UI 动作测试。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.domain.application_lifecycle import ApplicationLifecycle
from app.protocols.workflow.request import workflow_run_inputs
from app.protocols.workflow.run_control import build_workflow_plan_control_ag_ui_stream
from app.services.application_lifecycle import (
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.build_task_plan_lifecycle import abandon_pending_build_task_plan
from app.services.planning_refresh_recovery import resolve_planning_refresh_state
from app.workspace.task_documents import (
    build_task_plan_json_path,
    build_task_plan_pending_json_path,
    load_pending_build_task_plan,
    write_pending_build_task_plan_atomic,
)


class AbandonPendingBuildTaskPlanTests(unittest.IsolatedAsyncioTestCase):
    """验证 Abandon 只删除用户指明的当前 PendingPlan。"""

    def setUp(self) -> None:
        """创建带稳定 DraftIdentity 的独立 Pending 与 Formal 哨兵。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = {"workspace": directory.name}
        self.pending_path = build_task_plan_pending_json_path(self.state)
        self.formal_path = build_task_plan_json_path(self.state)
        self.formal_path.parent.mkdir(parents=True)
        self.formal_bytes = b'{"confirmation_status":"confirmed","sentinel":true}\r\n'
        self.formal_path.write_bytes(self.formal_bytes)
        self._write_pending()

    def _write_pending(self, *, base_digest: str | None = "a" * 64) -> dict:
        """写入有效最小 Pending，并返回服务端签发的请求身份。"""

        plan = {
            "schema_version": "build-dag.v3",
            "status": "ready",
            "task_graph": {"validation": {"is_valid": True, "errors": []}},
        }
        write_pending_build_task_plan_atomic(
            self.state,
            plan,
            planning_run_id="planning-run-current",
            base_confirmed_plan_digest=base_digest,
            input_fingerprint="b" * 64,
            build_execution_scope={"type": "page", "targetId": "orders"},
            created_at="2026-09-06T00:00:00Z",
        )
        identity = load_pending_build_task_plan(self.state)["draft_identity"]
        return {
            "planning_run_id": identity["planning_run_id"],
            "draft_digest": identity["draft_digest"],
        }

    def _write_lifecycle(self) -> None:
        """写入停在 DAG 确认门禁的最小 application lifecycle。"""

        lifecycle = ApplicationLifecycle.model_validate(
            {
                "application": {"id": "app-abandon", "name": "Abandon 测试"},
                "updatedAt": "2026-09-08T00:00:00Z",
                "revision": 4,
                "initialization": {"stage": "ready_for_workbench", "status": "completed"},
                "activeRunId": "workflow-current",
                "activeExecutions": {
                    "workflow-current": {
                        "scope": "page",
                        "targetId": "orders",
                        "pageId": "orders",
                        "threadId": "thread-abandon",
                        "runId": "workflow-current",
                        "phase": "prepare_build_tasks",
                        "status": "awaiting_user",
                        "pendingInteraction": {
                            "id": "pending-dag",
                            "type": "task_plan_confirmation",
                            "basedOnRevision": 4,
                            "payload": {"mode": "build_task_plan_confirmation"},
                            "artifactRefs": [],
                            "createdAt": "2026-09-08T00:00:00Z",
                        },
                        "startedAt": "2026-09-08T00:00:00Z",
                        "updatedAt": "2026-09-08T00:00:00Z",
                    }
                },
            }
        )
        write_application_lifecycle(self.state["workspace"], lifecycle)

    def test_normal_abandon_deletes_pending_and_preserves_formal(self) -> None:
        """精确身份应删除 Pending，刷新读取为空且 Formal 字节不变。"""

        request = load_pending_build_task_plan(self.state)["draft_identity"]

        result = abandon_pending_build_task_plan(
            self.state,
            planning_run_id=request["planning_run_id"],
            draft_digest=request["draft_digest"],
        )

        self.assertEqual(result.status, "abandoned")
        self.assertFalse(self.pending_path.exists())
        self.assertIsNone(load_pending_build_task_plan(self.state))
        self.assertEqual(self.formal_path.read_bytes(), self.formal_bytes)

    def test_stale_abandon_does_not_delete_current_pending(self) -> None:
        """旧 Run 或旧 digest 请求必须保留当前 Pending 和 Formal。"""

        original_pending = self.pending_path.read_bytes()
        stale_requests = (
            {"planning_run_id": "planning-run-old", "draft_digest": "c" * 64},
            {"planning_run_id": "planning-run-current", "draft_digest": "d" * 64},
        )

        for request in stale_requests:
            with self.subTest(request=request):
                result = abandon_pending_build_task_plan(self.state, **request)
                self.assertEqual(result.status, "stale_draft")
                self.assertEqual(self.pending_path.read_bytes(), original_pending)
                self.assertEqual(self.formal_path.read_bytes(), self.formal_bytes)

    def test_no_pending_is_idempotent_and_preserves_formal(self) -> None:
        """无 Pending 时返回明确结果，不创建文件也不修改 Formal。"""

        request = load_pending_build_task_plan(self.state)["draft_identity"]
        self.pending_path.unlink()

        result = abandon_pending_build_task_plan(
            self.state,
            planning_run_id=request["planning_run_id"],
            draft_digest=request["draft_digest"],
        )

        self.assertEqual(result.status, "no_pending")
        self.assertFalse(self.pending_path.exists())
        self.assertEqual(self.formal_path.read_bytes(), self.formal_bytes)

    def test_tampered_pending_is_not_deleted(self) -> None:
        """正文被篡改时即使请求身份字段相同也不能删除证据。"""

        pending = load_pending_build_task_plan(self.state)
        request = pending["draft_identity"]
        pending["tampered"] = True
        self.pending_path.write_text(json.dumps(pending), encoding="utf-8")

        result = abandon_pending_build_task_plan(
            self.state,
            planning_run_id=request["planning_run_id"],
            draft_digest=request["draft_digest"],
        )

        self.assertEqual(result.status, "stale_draft")
        self.assertTrue(self.pending_path.exists())
        self.assertEqual(self.formal_path.read_bytes(), self.formal_bytes)

    def test_request_adapter_forwards_abandon_identity(self) -> None:
        """AG-UI plan control 必须原样转发后端签发的 Run 与 digest。"""

        inputs = workflow_run_inputs(
            {
                "forwardedProps": {
                    "planControlAction": "abandon",
                    "planningRunId": "planning-run-current",
                    "draftDigest": "e" * 64,
                }
            }
        )

        self.assertEqual(inputs["plan_control_action"], "abandon")
        self.assertEqual(inputs["plan_control_planning_run_id"], "planning-run-current")
        self.assertEqual(inputs["plan_control_draft_digest"], "e" * 64)

    async def test_abandon_action_does_not_cancel_or_end_execution(self) -> None:
        """AG-UI Abandon 只删除 Pending，不调用 Scheduler cancel 或 lifecycle end。"""

        self.formal_path.unlink()
        self.pending_path.unlink()
        request = self._write_pending(base_digest=None)
        self._write_lifecycle()
        stream = build_workflow_plan_control_ag_ui_stream(
            action="abandon",
            workspace=self.state["workspace"],
            target_run_id="workflow-current",
            planning_run_id=request["planning_run_id"],
            draft_digest=request["draft_digest"],
            thread_id="thread-abandon",
            run_id="request-abandon",
        )

        with (
            patch("app.protocols.workflow.run_control.workflow_run_registry.cancel") as cancel,
            patch("app.protocols.workflow.run_control.end_workbench_execution") as end,
            patch("app.protocols.workflow.run_control.stop_workbench_execution") as stop,
        ):
            frames = [frame async for frame in stream]

        cancel.assert_not_called()
        end.assert_not_called()
        stop.assert_not_called()
        self.assertFalse(self.pending_path.exists())
        self.assertIn('"status":"abandoned"', "".join(frames))
        self.assertIn('"source":"abandoned"', "".join(frames))
        persisted = load_application_lifecycle(self.state["workspace"])
        self.assertNotIn("workflow-current", persisted.active_executions)
        self.assertEqual(
            persisted.extensions["planningResultLifecycle"]["planningRunId"],
            request["planning_run_id"],
        )

    async def test_stale_abandon_returns_current_pending_authority(self) -> None:
        """身份拒绝必须保留 Pending，并通过同一 AG-UI 流返回当前 authoritative 投影。"""

        self.formal_path.unlink()
        self.pending_path.unlink()
        self._write_pending(base_digest=None)
        self._write_lifecycle()
        stream = build_workflow_plan_control_ag_ui_stream(
            action="abandon",
            workspace=self.state["workspace"],
            target_run_id="workflow-current",
            planning_run_id="planning-run-stale",
            draft_digest="f" * 64,
            thread_id="thread-abandon",
            run_id="request-stale-abandon",
        )

        frames = "".join([frame async for frame in stream])

        self.assertTrue(self.pending_path.exists())
        self.assertIn('"status":"stale_draft"', frames)
        self.assertIn('"source":"pending"', frames)
        self.assertIn("workflow-current", load_application_lifecycle(
            self.state["workspace"]
        ).active_executions)

    def test_abandoned_snapshot_remains_authoritative_after_reload(self) -> None:
        """重载后 tombstone 仍压制旧 PlanningRun/Pending UI，不依赖前端本地清空。"""

        self.formal_path.unlink()
        self.pending_path.unlink()
        request = self._write_pending(base_digest=None)
        self._write_lifecycle()
        result = abandon_pending_build_task_plan(
            self.state,
            **request,
            workflow_run_id="workflow-current",
        )
        reloaded = load_application_lifecycle(self.state["workspace"])
        snapshot = resolve_planning_refresh_state(
            self.state["workspace"],
            lifecycle=reloaded,
            runtime_active=lambda _run_id: False,
        )

        self.assertEqual(result.status, "abandoned")
        self.assertEqual(snapshot["source"], "abandoned")
        self.assertEqual(snapshot["status"], "abandoned")
        self.assertEqual(snapshot["planningRunId"], request["planning_run_id"])


if __name__ == "__main__":
    unittest.main()
