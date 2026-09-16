"""验证 Application Delete 与 PendingPlan Abandon 之间的生命周期并发边界。"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import unittest
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

from app.persistence.checkpoints import close_workflow_checkpointer
from app.protocols.application_deletion import (
    ApplicationDeletionRequest,
    prepare_application_deletion,
)
from app.protocols.workflow.run_control import (
    build_workflow_plan_control_ag_ui_stream,
    workflow_run_registry,
)
from app.services.application_lifecycle import ApplicationLifecycleConflictError
from app.services.build_task_plan_lifecycle import abandon_pending_build_task_plan
from app.services.ui_design_generation_pool import get_ui_design_generation_pool
from app.services.workspace_bootstrap.coordinator import template_mutation_coordinator
from app.services.workspace_process_registry import workspace_process_registry
from app.workspace.planning_run_documents import (
    planning_run_json_path,
    write_planning_run_atomic,
)
from app.workspace.task_documents import (
    build_task_plan_lifecycle_lock,
    build_task_plan_pending_json_path,
    load_pending_build_task_plan,
    write_pending_build_task_plan_atomic,
)
from tests.planning_run_fixtures import run as planning_run_fixture


def _consume_abandon_stream(
    workspace: str,
    request: dict[str, str],
) -> list[str]:
    """在独立线程事件循环中消费 Abandon 流，允许主事件循环推进删除。"""

    async def consume() -> list[str]:
        """消费一次不注册 Workflow 的 Abandon AG-UI 流。"""

        stream = build_workflow_plan_control_ag_ui_stream(
            action="abandon",
            workspace=workspace,
            target_run_id="workflow-race",
            planning_run_id=request["planning_run_id"],
            draft_digest=request["draft_digest"],
            thread_id="thread-race",
            run_id="abandon-request",
        )
        return [frame async for frame in stream]

    return asyncio.run(consume())


class ApplicationDeletionPendingRaceTests(unittest.IsolatedAsyncioTestCase):
    """验证删除 fence 与 Pending lifecycle lock 的交叉临界区。"""

    async def asyncTearDown(self) -> None:
        """关闭删除并发测试可能触碰的共享 SQLite checkpointer。"""

        await close_workflow_checkpointer()

    def _managed_workspace(self, directory: str) -> Path:
        """创建一个满足应用删除协议校验的最小受管工作区。"""

        workspace = Path(directory) / "managed-app"
        marker = workspace / ".xcodeagent" / "application.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}\n", encoding="utf-8")
        return workspace

    def _write_pending(self, workspace: Path) -> dict[str, str]:
        """写入真实 PendingPlan 并返回服务端签发的 DraftIdentity。"""

        state = {"workspace": str(workspace)}
        write_pending_build_task_plan_atomic(
            state,
            {
                "schema_version": "build-dag.v4",
                "status": "ready",
                "task_graph": {"validation": {"is_valid": True, "errors": []}},
            },
            owner_session_id="session-race",
            planning_run_id="planning-race",
            workflow_run_id="workflow-race",
            base_confirmed_plan_digest=None,
            input_fingerprint="a" * 64,
            build_execution_scope={"type": "application", "targetId": "application"},
            created_at="2026-09-16T00:00:00Z",
        )
        pending = load_pending_build_task_plan(state)
        assert pending is not None
        identity = pending["draft_identity"]
        return {
            "planning_run_id": identity["planning_run_id"],
            "draft_digest": identity["draft_digest"],
        }

    def _write_planning_run(self, workspace: Path) -> None:
        """写入与 Pending 同名的 PlanningRun，作为未被 Abandon 修改的哨兵。"""

        planning_run = planning_run_fixture().model_copy(
            update={
                "planning_run_id": "planning-race",
                "workflow_run_id": "workflow-race",
            }
        )
        write_planning_run_atomic({"workspace": str(workspace)}, planning_run)

    def _release_delete_fences(self, workspace: Path) -> None:
        """清理成功删除准备留下的进程内 fence，避免测试污染后续用例。"""

        workspace_text = str(workspace.resolve(strict=False))
        template_mutation_coordinator.cancel_deletion(workspace)
        workflow_run_registry.end_workspace_deletion(workspace_text)
        workspace_process_registry.end_workspace_deletion(workspace)
        get_ui_design_generation_pool().end_workspace_deletion(workspace_text)

    async def test_delete_waits_for_abandon_before_stage_b(self) -> None:
        """Abandon 已持有 lifecycle lock 时，Delete 不能越过它进入 Stage B。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = self._managed_workspace(directory)
            request_identity = self._write_pending(workspace)
            self._write_planning_run(workspace)
            pending_path = build_task_plan_pending_json_path({"workspace": str(workspace)})
            abandon_started = threading.Event()
            release_abandon = threading.Event()
            stage_b_entered = threading.Event()
            abandon_task: asyncio.Task[Any] | None = None
            deletion_task: asyncio.Task[Any] | None = None
            real_abandon = abandon_pending_build_task_plan

            def blocking_abandon(state: dict[str, Any], **kwargs: str) -> Any:
                """在真实 Abandon 外层 lifecycle lock 内暂停，模拟文件写入临界区。"""

                abandon_started.set()
                if not release_abandon.wait(timeout=5):
                    raise TimeoutError("Abandon 并发测试未收到继续信号。")
                return real_abandon(state, **kwargs)

            async def fake_delete_checkpoints(**_kwargs: Any) -> dict[str, Any]:
                """记录 Delete 首次进入持久化 Stage B，避免触碰测试环境数据库。"""

                stage_b_entered.set()
                return {"databasePath": "test", "deletedThreadCount": 0}

            async def fake_close_checkpointer(**_kwargs: Any) -> bool:
                """模拟 Stage B 已完成本地连接检查但不创建真实连接。"""

                return False

            try:
                with (
                    patch(
                        "app.protocols.workflow.run_control.abandon_pending_build_task_plan",
                        side_effect=blocking_abandon,
                    ),
                    patch(
                        "app.protocols.application_deletion.delete_workflow_checkpoints_for_workspace",
                        new=fake_delete_checkpoints,
                    ),
                    patch(
                        "app.protocols.application_deletion.close_workflow_checkpointer_for_workspace",
                        new=fake_close_checkpointer,
                    ),
                    patch(
                        "app.protocols.application_deletion.stop_project_preview",
                        return_value={"status": "stopped"},
                    ),
                ):
                    abandon_task = asyncio.create_task(
                        asyncio.to_thread(
                            _consume_abandon_stream,
                            str(workspace),
                            request_identity,
                        )
                    )
                    self.assertTrue(await asyncio.to_thread(abandon_started.wait, 2))

                    deletion_task = asyncio.create_task(
                        prepare_application_deletion(
                            ApplicationDeletionRequest(
                                action="prepare",
                                applicationId="application-race",
                                workspaceRoot=str(workspace),
                            )
                        )
                    )
                    for _ in range(200):
                        if workflow_run_registry.is_workspace_deleting(str(workspace)):
                            break
                        await asyncio.sleep(0.01)
                    else:
                        self.fail("Application Delete 未建立 workspace deleting fence。")

                    await asyncio.sleep(0.05)
                    self.assertFalse(stage_b_entered.is_set())
                    self.assertTrue(pending_path.exists())

                    release_abandon.set()
                    _abandon_frames, report = await asyncio.gather(
                        abandon_task,
                        deletion_task,
                    )

                    self.assertTrue(stage_b_entered.is_set())
                    self.assertTrue(report["readyForTrash"])
                    self.assertFalse(pending_path.exists())
            finally:
                release_abandon.set()
                pending_tasks = [
                    task
                    for task in (abandon_task, deletion_task)
                    if task is not None and not task.done()
                ]
                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
                self._release_delete_fences(workspace)

    async def test_abandon_is_rejected_after_delete_fence_without_writes(self) -> None:
        """Delete fence 建立后，Abandon 必须拒绝且不改变 Pending/PlanningRun。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = self._managed_workspace(directory)
            request_identity = self._write_pending(workspace)
            self._write_planning_run(workspace)
            pending_path = build_task_plan_pending_json_path({"workspace": str(workspace)})
            planning_path = planning_run_json_path({"workspace": str(workspace)})
            lifecycle_path = workspace / ".xcodeagent" / "application-lifecycle.json"
            lifecycle_path.write_bytes(b"lifecycle-sentinel\n")
            pending_before = pending_path.read_bytes()
            planning_before = planning_path.read_bytes()
            lifecycle_before = lifecycle_path.read_bytes()
            workspace_text = str(workspace.resolve(strict=False))
            workflow_run_registry.begin_workspace_deletion(
                workspace_text,
                application_id="application-race",
            )

            try:
                stream = build_workflow_plan_control_ag_ui_stream(
                    action="abandon",
                    workspace=str(workspace),
                    target_run_id="workflow-race",
                    planning_run_id=request_identity["planning_run_id"],
                    draft_digest=request_identity["draft_digest"],
                    thread_id="thread-race",
                    run_id="abandon-request",
                )
                with patch(
                    "app.protocols.workflow.run_control.abandon_pending_build_task_plan",
                    wraps=abandon_pending_build_task_plan,
                ) as abandon:
                    with self.assertRaisesRegex(
                        ApplicationLifecycleConflictError,
                        "当前应用正在删除，不能放弃 Pending Build DAG。",
                    ):
                        _frames = [frame async for frame in stream]

                abandon.assert_not_called()
                self.assertEqual(pending_path.read_bytes(), pending_before)
                self.assertEqual(planning_path.read_bytes(), planning_before)
                self.assertEqual(lifecycle_path.read_bytes(), lifecycle_before)
            finally:
                workflow_run_registry.end_workspace_deletion(workspace_text)

    async def test_abandon_rechecks_delete_fence_after_waiting_for_lock(self) -> None:
        """Abandon 等锁期间进入删除时，拿锁后必须重新检查并且不写文件。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = self._managed_workspace(directory)
            request_identity = self._write_pending(workspace)
            self._write_planning_run(workspace)
            pending_path = build_task_plan_pending_json_path({"workspace": str(workspace)})
            planning_path = planning_run_json_path({"workspace": str(workspace)})
            lifecycle_path = workspace / ".xcodeagent" / "application-lifecycle.json"
            lifecycle_path.write_bytes(b"lifecycle-sentinel\n")
            pending_before = pending_path.read_bytes()
            planning_before = planning_path.read_bytes()
            lifecycle_before = lifecycle_path.read_bytes()
            real_lock = build_task_plan_lifecycle_lock(workspace)
            lock_attempted = threading.Event()
            abandon_task: asyncio.Task[Any] | None = None
            workspace_text = str(workspace.resolve(strict=False))

            @contextmanager
            def waiting_lock() -> Iterator[None]:
                """标记 Abandon 已开始等锁，再沿用真实工作区 RLock。"""

                lock_attempted.set()
                with real_lock:
                    yield

            def patched_lock(_workspace: str | Path) -> Any:
                """为本测试把 plan-control 外层锁接到可观测的真实 RLock。"""

                return waiting_lock()

            try:
                with (
                    patch(
                        "app.protocols.workflow.run_control.build_task_plan_lifecycle_lock",
                        side_effect=patched_lock,
                    ),
                    patch(
                        "app.protocols.workflow.run_control.abandon_pending_build_task_plan",
                        wraps=abandon_pending_build_task_plan,
                    ) as abandon,
                ):
                    with real_lock:
                        abandon_task = asyncio.create_task(
                            asyncio.to_thread(
                                _consume_abandon_stream,
                                str(workspace),
                                request_identity,
                            )
                        )
                        for _ in range(200):
                            if lock_attempted.is_set():
                                break
                            await asyncio.sleep(0.01)
                        else:
                            self.fail("Abandon 未进入 Pending lifecycle lock 等待状态。")
                        workflow_run_registry.begin_workspace_deletion(
                            workspace_text,
                            application_id="application-race",
                        )

                    with self.assertRaisesRegex(
                        ApplicationLifecycleConflictError,
                        "当前应用正在删除，不能放弃 Pending Build DAG。",
                    ):
                        await abandon_task
                    abandon.assert_not_called()
                    self.assertEqual(pending_path.read_bytes(), pending_before)
                    self.assertEqual(planning_path.read_bytes(), planning_before)
                    self.assertEqual(lifecycle_path.read_bytes(), lifecycle_before)
            finally:
                workflow_run_registry.end_workspace_deletion(workspace_text)
                if abandon_task is not None and not abandon_task.done():
                    await asyncio.gather(abandon_task, return_exceptions=True)

    async def test_abandon_still_succeeds_when_workspace_is_not_deleting(self) -> None:
        """非 deleting workspace 仍沿用原有 Abandon 文件和 PlanningRun 收口结果。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = self._managed_workspace(directory)
            request_identity = self._write_pending(workspace)
            self._write_planning_run(workspace)
            pending_path = build_task_plan_pending_json_path({"workspace": str(workspace)})
            planning_path = planning_run_json_path({"workspace": str(workspace)})

            frames = await asyncio.to_thread(
                _consume_abandon_stream,
                str(workspace),
                request_identity,
            )

            self.assertIn('"status":"abandoned"', "".join(frames))
            self.assertFalse(pending_path.exists())
            self.assertFalse(planning_path.exists())


if __name__ == "__main__":
    unittest.main()
