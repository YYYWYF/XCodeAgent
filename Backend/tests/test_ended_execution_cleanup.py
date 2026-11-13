"""验证结束任务凭据随业务内容变化落盘删除，运行证据不触发失效。"""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.domain.application_lifecycle import EndedPlanningExecution
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    application_lifecycle_path,
    end_workbench_execution,
    load_application_lifecycle,
    start_workbench_execution,
    write_application_lifecycle,
)
from app.services.ended_workspace import workspace_fingerprint
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class EndedExecutionCleanupTests(unittest.TestCase):
    """使用真实文件和原子生命周期持久化检查两类继续凭据的销毁边界。"""

    def prepare(self, workspace: str, business_file: str = "frontend/app.ts"):
        """准备同一业务基线下的两个开发结束凭据和一个规划结束凭据。"""
        _write_ready_workbench_lifecycle(workspace)
        path = Path(workspace, business_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original", encoding="utf-8")
        for run_id in ("first", "second"):
            start_workbench_execution(
                workspace, scope="page", target_id=run_id, page_id=run_id,
                thread_id=run_id, owner_session_id=run_id, run_id=run_id, phase="build",
            )
            end_workbench_execution(workspace, run_id=run_id, preserve_resume=True)
        state = load_application_lifecycle(workspace)
        planning = EndedPlanningExecution(
            runId="planning", initialization=state.initialization,
            workspace=workspace, fingerprint=workspace_fingerprint(workspace),
        )
        return write_application_lifecycle(
            workspace, state.model_copy(update={"ended_planning_execution": planning}),
            expected_revision=state.revision,
        )

    def assert_removed(self, workspace: str):
        """确认继续凭据既从读取结果消失，也从真实磁盘快照移除。"""
        current = load_application_lifecycle(workspace)
        self.assertEqual(current.ended_executions, {})
        self.assertIsNone(current.ended_planning_execution)
        stored = json.loads(application_lifecycle_path(workspace).read_text(encoding="utf-8"))
        self.assertEqual(stored["endedExecutions"], {})
        self.assertIsNone(stored["endedPlanningExecution"])
        return current

    def test_business_changes_remove_all_old_credentials_and_cannot_revive(self):
        """代码、配置、文档、草稿和任务计划变化都删除旧凭据，恢复文件也不能复活。"""
        for name in (
            "frontend/app.ts", ".devagentstudio/application.json",
            ".devagentstudio/specs/requirement-spec.md",
            ".devagentstudio/drafts/revisions/change/technical-plan/artifact.json",
            ".devagentstudio/plans/build-task-plan.json",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as workspace:
                before = self.prepare(workspace, name)
                path = Path(workspace, name)
                path.write_text("changed", encoding="utf-8")
                current = self.assert_removed(workspace)
                self.assertEqual(current.revision, before.revision + 1)
                self.assertEqual(current.application, before.application)
                self.assertEqual(current.initialization, before.initialization)
                path.write_text("original", encoding="utf-8")
                self.assertEqual(self.assert_removed(workspace).revision, current.revision)

    def test_add_delete_and_rename_invalidate_credentials(self):
        """业务文件新增、删除和改名均构成工作区变化。"""
        for change in ("add", "delete", "rename"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as workspace:
                self.prepare(workspace)
                path = Path(workspace, "frontend/app.ts")
                if change == "add":
                    path.with_name("new.ts").write_text("new", encoding="utf-8")
                elif change == "delete":
                    path.unlink()
                else:
                    path.rename(path.with_name("renamed.ts"))
                self.assert_removed(workspace)

    def test_runtime_and_lifecycle_writes_keep_credentials(self):
        """日志、心跳、checkpoint 与 lifecycle 自身更新不能误删仍有效的凭据。"""
        with tempfile.TemporaryDirectory() as workspace:
            before = self.prepare(workspace)
            for name in (
                "backend/server.log", ".devagentstudio/logs/run.json",
                ".devagentstudio/runtime/heartbeat.json", ".devagentstudio/checkpoints/run.sqlite",
            ):
                path = Path(workspace, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("runtime update", encoding="utf-8")
            written = write_application_lifecycle(workspace, before.model_copy(update={
                "revision": before.revision + 1, "extensions": {"testExecutionStatus": "pending"},
            }), expected_revision=before.revision)
            current = load_application_lifecycle(workspace)
            self.assertEqual(current.ended_executions, before.ended_executions)
            self.assertEqual(current.ended_planning_execution, before.ended_planning_execution)
            self.assertEqual(current.revision, written.revision)

    def test_write_cleans_credentials_without_an_intermediate_read(self):
        """业务修改后的普通写入直接清理旧凭据，并保留本次活动执行信息。"""
        with tempfile.TemporaryDirectory() as workspace:
            before = self.prepare(workspace)
            Path(workspace, "frontend/app.ts").write_text("changed", encoding="utf-8")
            written = write_application_lifecycle(workspace, before.model_copy(update={
                "revision": before.revision + 1, "active_run_id": "current-run",
            }), expected_revision=before.revision)
            self.assertEqual(written.active_run_id, "current-run")
            self.assertEqual(self.assert_removed(workspace).revision, written.revision)

    def test_concurrent_reads_clean_once_and_reject_stale_cas(self):
        """并发检测只递增一次版本，清理后旧写入不能覆盖新状态。"""
        with tempfile.TemporaryDirectory() as workspace:
            before = self.prepare(workspace)
            Path(workspace, "frontend/app.ts").write_text("changed", encoding="utf-8")
            with ThreadPoolExecutor(max_workers=4) as pool:
                states = list(pool.map(load_application_lifecycle, [workspace] * 4))
            self.assertEqual({state.revision for state in states}, {before.revision + 1})
            self.assertTrue(all(not state.ended_executions for state in states))
            with self.assertRaises(ApplicationLifecycleConflictError):
                write_application_lifecycle(workspace, before, expected_revision=before.revision)
            self.assert_removed(workspace)

    def test_failed_scan_does_not_invent_a_workspace_change(self):
        """读取失败保留原凭据和版本，不能把未知状态当成业务修改删除。"""
        with tempfile.TemporaryDirectory() as workspace:
            before = self.prepare(workspace)
            with patch("app.services.ended_workspace.workspace_fingerprint", side_effect=PermissionError):
                self.assertEqual(load_application_lifecycle(workspace), before)
