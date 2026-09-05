from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    transition_application_lifecycle,
    write_application_lifecycle,
)
from app.services.workspace_bootstrap.coordinator import TemplateMutationCoordinator
from app.services.workspace_bootstrap.materializer import BOOTSTRAP_STAGING_RELATIVE_PATH
from app.services.template_state import TEMPLATE_STATE_RELATIVE_PATH


def _generating_workspace(workspace: Path) -> None:
    """写入一个已进入 Bootstrap 但尚未完成的生命周期快照。"""

    state = create_application_lifecycle(application_id="app-1", application_name="测试应用")
    route = [
        ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
        ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
        ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
        ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
        ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
        ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
        ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
        ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
    ]
    for stage in route:
        state = transition_application_lifecycle(
            state,
            stage=stage,
            status=ApplicationLifecycleStatus.RUNNING,
        )
    write_application_lifecycle(workspace, state)


class TemplateMutationCoordinatorTests(unittest.TestCase):
    """验证删除 fence 和 Attach 只在设计的事务边界改变 Workspace。"""

    def test_deletion_cancels_preparation_but_waits_commit_section(self) -> None:
        """删除应标记 Preparation 取消，Commit 则等待任务 finish 后才返回。"""

        with tempfile.TemporaryDirectory() as directory:
            coordinator = TemplateMutationCoordinator()
            coordinator.begin_preparation(directory)
            deletion_finished = threading.Event()

            def delete_preparation() -> None:
                """在独立线程等待 Preparation 响应删除取消并结束。"""

                coordinator.begin_deletion(directory, timeout_seconds=1)
                deletion_finished.set()

            deletion_thread = threading.Thread(target=delete_preparation)
            deletion_thread.start()
            self.assertTrue(_wait_for_cancel(coordinator, directory))
            with self.assertRaisesRegex(Exception, "已被应用删除取消"):
                coordinator.raise_if_preparation_cancelled(directory)
            coordinator.finish(directory)
            self.assertTrue(deletion_finished.wait(0.5))
            deletion_thread.join(timeout=0.5)
            coordinator.cancel_deletion(directory)

            coordinator.begin_preparation(directory)
            coordinator.enter_commit_section(directory)
            finished = threading.Event()

            def delete_after_commit() -> None:
                """在另一个线程等待 Commit Section 完成。"""

                coordinator.begin_deletion(directory, timeout_seconds=1)
                finished.set()

            thread = threading.Thread(target=delete_after_commit)
            thread.start()
            self.assertFalse(finished.wait(0.05))
            coordinator.finish(directory)
            self.assertTrue(finished.wait(0.5))
            thread.join(timeout=0.5)

    def test_attach_cleans_orphaned_generating_workspace_and_marks_failed(self) -> None:
        """无 active Task 的 GENERATING 工作区必须清理精确受管范围后转为失败。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _generating_workspace(workspace)
            for relative in ("frontend", "backend", ".git", BOOTSTRAP_STAGING_RELATIVE_PATH):
                (workspace / relative).mkdir(parents=True, exist_ok=True)
            state_path = workspace / TEMPLATE_STATE_RELATIVE_PATH
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text("{}", encoding="utf-8")

            result = TemplateMutationCoordinator().attach_workspace(workspace)
            lifecycle = load_application_lifecycle(workspace)

            self.assertEqual(result.action, "recovered")
            assert lifecycle is not None
            self.assertEqual(lifecycle.initialization.stage, ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED)
            for relative in ("frontend", "backend", ".git", TEMPLATE_STATE_RELATIVE_PATH, BOOTSTRAP_STAGING_RELATIVE_PATH):
                self.assertFalse((workspace / relative).exists(), relative)

    def test_attach_cleanup_failure_keeps_generating_for_later_retry(self) -> None:
        """受管清理失败时 Attach 不得伪造失败完成，应保留 GENERATING。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _generating_workspace(workspace)
            (workspace / "frontend").mkdir()
            with patch("app.services.workspace_bootstrap.coordinator.shutil.rmtree", side_effect=OSError("busy")):
                result = TemplateMutationCoordinator().attach_workspace(workspace)

            lifecycle = load_application_lifecycle(workspace)
            self.assertEqual(result.action, "cleanup_failed")
            assert lifecycle is not None
            self.assertEqual(lifecycle.initialization.stage, ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES)

    def test_attach_lifecycle_cas_failure_is_retried_idempotently(self) -> None:
        """最终 lifecycle 写入冲突时下次 Attach 仍应从已清理范围安全完成收尾。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _generating_workspace(workspace)
            (workspace / "frontend").mkdir()
            coordinator = TemplateMutationCoordinator()
            with patch(
                "app.services.workspace_bootstrap.coordinator.persist_application_lifecycle_transition",
                side_effect=RuntimeError("cas failed"),
            ):
                failed = coordinator.attach_workspace(workspace)

            recovered = coordinator.attach_workspace(workspace)
            lifecycle = load_application_lifecycle(workspace)
            self.assertEqual(failed.action, "cleanup_failed")
            self.assertEqual(recovered.action, "recovered")
            assert lifecycle is not None
            self.assertEqual(lifecycle.initialization.stage, ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED)


def _wait_for_cancel(coordinator: TemplateMutationCoordinator, workspace: str, attempts: int = 20) -> bool:
    """轮询有限次数，等待删除线程把 Preparation 标记为已取消。"""

    import time

    for _ in range(attempts):
        try:
            coordinator.raise_if_preparation_cancelled(workspace)
        except Exception:
            return True
        time.sleep(0.01)
    return False
