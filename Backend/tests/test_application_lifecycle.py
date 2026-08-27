from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    PendingInteractionType,
    WorkbenchExecutionStatus,
)
from app.services.application_lifecycle import (
    begin_application_template_generation,
    complete_application_template_generation,
    complete_workbench_execution,
    ApplicationLifecycleConflictError,
    ApplicationLifecycleCorruptedError,
    create_application_lifecycle,
    end_workbench_execution,
    application_lifecycle_path,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
    start_workbench_execution,
    stop_workbench_execution,
    transition_application_lifecycle,
    update_workbench_execution,
    write_application_lifecycle,
)
from app.services.application_template_generation import prepare_application_template_generation


class ApplicationLifecycleTests(unittest.TestCase):
    """验证生命周期 schema、状态机和原子写入规则。"""

    def test_lifecycle_uses_application_lifecycle_file_name(self) -> None:
        """生命周期持久化应使用与应用配置文件清晰区分的新文件名。"""

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                application_lifecycle_path(directory).name,
                "application-lifecycle.json",
            )

    def test_full_creation_transition_sequence(self) -> None:
        """全新应用应按已确认顺序进入工作台。"""

        state = create_application_lifecycle(
            application_id="app-1",
            application_name="任务中心",
            initialization_thread_id="thread-init",
        )
        sequence = [
            (ApplicationLifecycleStage.ANALYZING_REQUIREMENT, ApplicationLifecycleStatus.RUNNING),
            (
                ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
                ApplicationLifecycleStatus.AWAITING_USER,
            ),
            (ApplicationLifecycleStage.ANALYZING_REQUIREMENT, ApplicationLifecycleStatus.RUNNING),
            (
                ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                ApplicationLifecycleStatus.RUNNING,
            ),
            (
                ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
                ApplicationLifecycleStatus.AWAITING_USER,
            ),
            (ApplicationLifecycleStage.GENERATING_UI_DESIGNS, ApplicationLifecycleStatus.RUNNING),
            (
                ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
                ApplicationLifecycleStatus.AWAITING_USER,
            ),
            (
                ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
                ApplicationLifecycleStatus.AWAITING_USER,
            ),
            (ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN, ApplicationLifecycleStatus.RUNNING),
            (
                ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
                ApplicationLifecycleStatus.AWAITING_USER,
            ),
            (
                ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
                ApplicationLifecycleStatus.RUNNING,
            ),
            (ApplicationLifecycleStage.READY_FOR_WORKBENCH, ApplicationLifecycleStatus.COMPLETED),
        ]
        for stage, status in sequence:
            state = transition_application_lifecycle(
                state,
                stage=stage,
                status=status,
            )

        self.assertEqual(state.initialization.stage, ApplicationLifecycleStage.READY_FOR_WORKBENCH)
        self.assertEqual(state.initialization.thread_id, "thread-init")
        self.assertEqual(state.revision, 1 + len(sequence))

    def test_three_creation_interruptions_survive_reload(self) -> None:
        """澄清、需求确认和计划确认都应在重启读取后保持初始化阶段。"""

        targets = [
            ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
            ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
            ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
        ]
        for target_stage in targets:
            with self.subTest(stage=target_stage), tempfile.TemporaryDirectory() as directory:
                state = create_application_lifecycle(
                    application_id="app-1",
                    application_name="任务中心",
                    initialization_thread_id="thread-init",
                )
                route = [
                    ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                    ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
                    ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                    ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                    ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
                    ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
                    ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
                    ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
                    ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
                    ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
                ]
                for stage in route:
                    state = transition_application_lifecycle(
                        state,
                        stage=stage,
                        status=(
                            ApplicationLifecycleStatus.AWAITING_USER
                            if stage.value.startswith("awaiting_")
                            else ApplicationLifecycleStatus.RUNNING
                        ),
                    )
                    if stage == target_stage:
                        break
                write_application_lifecycle(directory, state)
                reloaded = load_application_lifecycle(directory)
                assert reloaded is not None
                self.assertEqual(reloaded.initialization.stage, target_stage)
                self.assertEqual(reloaded.initialization.thread_id, "thread-init")

    def test_requirement_confirmation_revision_returns_to_analysis(self) -> None:
        """需求确认选择 revise 时，持久化服务必须允许回到需求分析阶段。"""

        with tempfile.TemporaryDirectory() as directory:
            state = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="thread-init",
            )
            write_application_lifecycle(directory, state)
            for stage, status in (
                (
                    ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
            ):
                state = persist_application_lifecycle_transition(
                    directory,
                    stage=stage,
                    status=status,
                )

            revised = persist_application_lifecycle_transition(
                directory,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
                active_run_id="revision-run",
            )
            loaded = load_application_lifecycle(directory)

        assert loaded is not None
        self.assertEqual(
            revised.initialization.stage,
            ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
        )
        self.assertEqual(
            revised.initialization.status,
            ApplicationLifecycleStatus.RUNNING,
        )
        self.assertEqual(revised.active_run_id, "revision-run")
        self.assertEqual(loaded.revision, revised.revision)

    def test_atomic_write_interruption_preserves_previous_file(self) -> None:
        """原子替换前失败时应保留上一版完整状态。"""

        with tempfile.TemporaryDirectory() as directory:
            state = create_application_lifecycle(application_id="app-1", application_name="任务中心")
            write_application_lifecycle(directory, state)
            original = application_lifecycle_path(directory).read_text(encoding="utf-8")
            updated = transition_application_lifecycle(
                state,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
            )
            with patch("app.services.application_lifecycle.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    write_application_lifecycle(directory, updated)

            self.assertEqual(application_lifecycle_path(directory).read_text(encoding="utf-8"), original)

    def test_corrupt_snapshot_fails_explicitly(self) -> None:
        """损坏的生命周期快照不得被静默当作缺失状态。"""

        with tempfile.TemporaryDirectory() as directory:
            path = application_lifecycle_path(directory)
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ApplicationLifecycleCorruptedError):
                load_application_lifecycle(directory)

    def test_revision_compare_and_swap_rejects_lost_update(self) -> None:
        """并发写入必须通过 expected_revision 防止覆盖新状态。"""

        with tempfile.TemporaryDirectory() as directory:
            state = create_application_lifecycle(application_id="app-1", application_name="任务中心")
            write_application_lifecycle(directory, state)
            updated = transition_application_lifecycle(
                state,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
            )
            with self.assertRaises(ApplicationLifecycleConflictError):
                write_application_lifecycle(directory, updated, expected_revision=99)

    def test_concurrent_compare_and_swap_allows_only_one_writer(self) -> None:
        """同一 revision 的并发事务只能有一个完成，另一个必须冲突。"""

        with tempfile.TemporaryDirectory() as directory:
            state = create_application_lifecycle(application_id="app-1", application_name="任务中心")
            write_application_lifecycle(directory, state)
            updated = transition_application_lifecycle(
                state,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
            )

            def write_once() -> str:
                """执行一次共享 revision 的 CAS 写入并返回结果分类。"""

                try:
                    write_application_lifecycle(directory, updated, expected_revision=state.revision)
                    return "written"
                except ApplicationLifecycleConflictError:
                    return "conflict"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: write_once(), range(2)))

            self.assertEqual(sorted(results), ["conflict", "written"])

    def test_template_generation_failure_is_terminal(self) -> None:
        """应用模板文件生成失败后不能从失败状态重新启动。"""

        with tempfile.TemporaryDirectory() as directory:
            state = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
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
            write_application_lifecycle(directory, state)

            failed = complete_application_template_generation(
                directory,
                succeeded=False,
                error_message="页面文件写入失败",
            )
            self.assertEqual(
                failed.initialization.stage,
                ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
            )
            assert failed.error is not None
            self.assertEqual(failed.error.code, "application_template_generation_failed")

            with self.assertRaisesRegex(ApplicationLifecycleConflictError, "只有用户确认 TechnicalPlan"):
                begin_application_template_generation(directory)
            with self.assertRaisesRegex(ApplicationLifecycleConflictError, "不能提交应用模板文件生成结果"):
                complete_application_template_generation(directory, succeeded=True)

    def test_template_generation_success_is_persisted_after_technical_confirmation(self) -> None:
        """TechnicalPlan 确认后进入模板阶段，完成门禁才能进入工作台。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            specs = workspace / ".xcodeagent/specs"
            plans = workspace / ".xcodeagent/plans"
            specs.mkdir(parents=True)
            plans.mkdir(parents=True)
            for path in (
                specs / "requirement-spec.json",
                plans / "product-plan.json",
                specs / "ui-designs.json",
                plans / "technical-plan.json",
            ):
                payload = {
                    "confirmation_status": (
                        "skipped" if path.name == "ui-designs.json" else "confirmed"
                    )
                }
                if path.name == "product-plan.json":
                    payload.update({"schema_version": "product-plan.v6", "pages": []})
                if path.name == "ui-designs.json":
                    payload.update({"schema_version": "ui-manifest.v3", "pages": []})
                if path.name == "technical-plan.json":
                    payload["artifact_type"] = "technical-plan"
                path.write_text(json.dumps(payload), encoding="utf-8")

            state = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
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
            write_application_lifecycle(directory, state)

            (workspace / "frontend/src/constants").mkdir(parents=True)
            (workspace / "frontend/package.json").write_text("{}", encoding="utf-8")
            (workspace / "frontend/src/constants/resources.ts").write_text("export const RESOURCES = {} as const;\n", encoding="utf-8")
            (workspace / "frontend/src/constants/routes.tsx").write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
            (workspace / "backend").mkdir()
            (workspace / "backend/pom.xml").write_text("<project />", encoding="utf-8")
            prepare_application_template_generation(
                workspace,
                {
                    "status": "succeeded",
                    "failedTargets": [],
                    "targets": {
                        "frontend": {"status": "succeeded", "attempt": 0, "branch": "auth"},
                        "backend": {"status": "succeeded", "attempt": 0, "branch": "auth"},
                    },
                },
            )

            ready = complete_application_template_generation(directory, succeeded=True)
            self.assertEqual(
                ready.initialization.stage,
                ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            )
            loaded = load_application_lifecycle(directory)
            assert loaded is not None
            self.assertEqual(
                loaded.initialization.stage,
                ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            )

    def test_current_snapshot_loads_with_workbench_defaults(self) -> None:
        """当前初始化快照应获得空的工作台字段默认值且不包含 delivery。"""

        with tempfile.TemporaryDirectory() as directory:
            state = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
            payload = state.model_dump(mode="json", by_alias=True)
            payload.pop("activeExecutions", None)
            payload.pop("resourceLocks", None)
            path = application_lifecycle_path(directory)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = load_application_lifecycle(directory)

            assert loaded is not None
            self.assertEqual(loaded.active_executions, {})
            self.assertEqual(loaded.resource_locks.pages, {})
            self.assertNotIn("schemaVersion", loaded.model_dump(mode="json", by_alias=True))
            self.assertNotIn("project", payload)
            self.assertNotIn("delivery", payload)

    def test_workbench_execution_requires_completed_creation_planning(self) -> None:
        """创建规划完成前不能登记工作台执行或改变生命周期 revision。"""

        with tempfile.TemporaryDirectory() as directory:
            state = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
            write_application_lifecycle(directory, state)

            with self.assertRaisesRegex(
                ApplicationLifecycleConflictError,
                "尚未完成创建规划",
            ):
                start_workbench_execution(
                    directory,
                    scope="application",
                    target_id="application",
                    page_id=None,
                    thread_id="thread-early",
                    run_id="run-early",
                    phase="requirements",
                )

            loaded = load_application_lifecycle(directory)
            assert loaded is not None
            self.assertEqual(loaded.revision, state.revision)
            self.assertEqual(
                loaded.initialization.stage,
                ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
            )
            self.assertEqual(loaded.active_executions, {})

    def test_workbench_execution_stop_end_and_acceptance_are_persisted(self) -> None:
        """计划执行应持久化停止、释放输入锁和页面验收边界。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            plan_path = workspace / ".xcodeagent/plans/technical-plan.json"
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(
                json.dumps({"artifact_type": "technical-plan", "pages": [{"pageId": "dashboard"}]}),
                encoding="utf-8",
            )
            state = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
            state = transition_application_lifecycle(
                state,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                status=ApplicationLifecycleStatus.RUNNING,
            )
            state = transition_application_lifecycle(
                state,
                stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                status=ApplicationLifecycleStatus.RUNNING,
            )
            state = transition_application_lifecycle(
                state,
                stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
                status=ApplicationLifecycleStatus.AWAITING_USER,
            )
            for stage, status in (
                (ApplicationLifecycleStage.GENERATING_UI_DESIGNS, ApplicationLifecycleStatus.RUNNING),
                (ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION, ApplicationLifecycleStatus.AWAITING_USER),
                (ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY, ApplicationLifecycleStatus.AWAITING_USER),
                (ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN, ApplicationLifecycleStatus.RUNNING),
                (ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION, ApplicationLifecycleStatus.AWAITING_USER),
            ):
                state = transition_application_lifecycle(state, stage=stage, status=status)
            state = transition_application_lifecycle(
                state,
                stage=ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
                status=ApplicationLifecycleStatus.RUNNING,
            )
            state = transition_application_lifecycle(
                state,
                stage=ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                status=ApplicationLifecycleStatus.COMPLETED,
            )
            write_application_lifecycle(directory, state)

            running = start_workbench_execution(
                directory,
                scope="page",
                target_id="dashboard",
                page_id="dashboard",
                thread_id="thread-1",
                run_id="run-1",
                phase="build",
            )
            self.assertEqual(
                running.initialization.stage,
                ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            )
            self.assertEqual(
                running.initialization.status,
                ApplicationLifecycleStatus.COMPLETED,
            )
            self.assertEqual(
                running.active_executions["run-1"].status,
                WorkbenchExecutionStatus.RUNNING,
            )

            stopped = stop_workbench_execution(directory, run_id="run-1")
            self.assertEqual(
                stopped.active_executions["run-1"].status,
                WorkbenchExecutionStatus.STOPPED,
            )
            ended = end_workbench_execution(directory, run_id="run-1")
            self.assertEqual(ended.active_executions, {})
            self.assertEqual(
                ended.initialization.stage,
                ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            )

            start_workbench_execution(
                directory,
                scope="page",
                target_id="dashboard",
                page_id="dashboard",
                thread_id="thread-2",
                run_id="run-2",
                phase="launch_project",
            )
            update_workbench_execution(
                directory,
                run_id="run-2",
                phase="launch_project",
                status=WorkbenchExecutionStatus.AWAITING_USER,
                pending_type=PendingInteractionType.PAGE_ACCEPTANCE,
            )
            completed = complete_workbench_execution(directory, run_id="run-2")
            self.assertEqual(
                completed.initialization.stage,
                ApplicationLifecycleStage.READY_FOR_WORKBENCH,
            )
            self.assertNotIn("delivery", completed.model_dump(mode="json", by_alias=True))

    def test_overlapping_pages_keep_independent_active_executions(self) -> None:
        """页面执行可并存，同一资源键只投影最近一次运行的登记。"""

        with tempfile.TemporaryDirectory() as directory:
            state = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
            state = state.model_copy(
                update={
                    "initialization": state.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, state)

            start_workbench_execution(
                directory,
                scope="page",
                target_id="dashboard",
                page_id="dashboard",
                thread_id="thread-1",
                run_id="run-1",
                phase="build",
            )
            parallel = start_workbench_execution(
                directory,
                scope="page",
                target_id="settings",
                page_id="settings",
                thread_id="thread-2",
                run_id="run-2",
                phase="build",
            )

            self.assertEqual(set(parallel.active_executions), {"run-1", "run-2"})
            overlapping = start_workbench_execution(
                directory,
                scope="page",
                target_id="dashboard",
                page_id="dashboard",
                thread_id="thread-3",
                run_id="run-3",
                phase="build",
            )

            self.assertEqual(
                set(overlapping.active_executions),
                {"run-1", "run-2", "run-3"},
            )
            self.assertEqual(overlapping.resource_locks.pages["dashboard"].run_id, "run-3")


if __name__ == "__main__":
    unittest.main()
