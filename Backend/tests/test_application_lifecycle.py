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
)
from app.services.application_lifecycle import (
    complete_application_template_generation,
    ApplicationLifecycleConflictError,
    ApplicationLifecycleCorruptedError,
    UnsupportedApplicationLifecycleVersionError,
    create_application_lifecycle,
    application_lifecycle_path,
    load_application_lifecycle,
    repair_misclassified_requirement_clarification,
    submit_pending_interaction,
    transition_application_lifecycle,
    write_application_lifecycle,
)


class ApplicationLifecycleTests(unittest.TestCase):
    """验证生命周期 schema、状态机和原子写入规则。"""

    def test_lifecycle_uses_application_lifecycle_file_name(self) -> None:
        """生命周期持久化应使用与应用配置文件清晰区分的新文件名。"""

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                application_lifecycle_path(directory).name,
                "application-lifecycle.json",
            )

    def test_misclassified_ask_user_interaction_is_repaired(self) -> None:
        """ask_user_question 不能作为需求文档确认交互持久化。"""

        with tempfile.TemporaryDirectory() as directory:
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
                stage=ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
                status=ApplicationLifecycleStatus.RUNNING,
            )
            state = transition_application_lifecycle(
                state,
                stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
                status=ApplicationLifecycleStatus.AWAITING_USER,
                pending_type=PendingInteractionType.REQUIREMENT_CONFIRMATION,
                pending_payload={"mode": "ask_user_question"},
            )
            write_application_lifecycle(directory, state)

            repaired = repair_misclassified_requirement_clarification(directory)

            self.assertEqual(
                repaired.lifecycle.stage,
                ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
            )
            self.assertEqual(
                repaired.pending_interaction.type,
                PendingInteractionType.REQUIREMENT_CLARIFICATION,
            )
            self.assertEqual(repaired.revision, state.revision + 1)
            self.assertEqual(
                load_application_lifecycle(directory).lifecycle.stage,
                ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
            )

    def test_full_creation_transition_sequence(self) -> None:
        """全新应用应按已确认顺序进入工作台。"""

        state = create_application_lifecycle(application_id="app-1", application_name="任务中心")
        sequence = [
            (ApplicationLifecycleStage.ANALYZING_REQUIREMENT, ApplicationLifecycleStatus.RUNNING, None),
            (
                ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
                ApplicationLifecycleStatus.AWAITING_USER,
                PendingInteractionType.REQUIREMENT_CLARIFICATION,
            ),
            (ApplicationLifecycleStage.ANALYZING_REQUIREMENT, ApplicationLifecycleStatus.RUNNING, None),
            (ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC, ApplicationLifecycleStatus.RUNNING, None),
            (
                ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
                ApplicationLifecycleStatus.AWAITING_USER,
                PendingInteractionType.REQUIREMENT_CONFIRMATION,
            ),
            (ApplicationLifecycleStage.GENERATING_PROJECT_PLAN, ApplicationLifecycleStatus.RUNNING, None),
            (
                ApplicationLifecycleStage.AWAITING_PROJECT_PLAN_CONFIRMATION,
                ApplicationLifecycleStatus.AWAITING_USER,
                PendingInteractionType.PROJECT_PLAN_CONFIRMATION,
            ),
            (
                ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
                ApplicationLifecycleStatus.RUNNING,
                None,
            ),
            (ApplicationLifecycleStage.READY_FOR_WORKBENCH, ApplicationLifecycleStatus.COMPLETED, None),
        ]
        for stage, status, pending_type in sequence:
            state = transition_application_lifecycle(
                state,
                stage=stage,
                status=status,
                pending_type=pending_type,
            )

        self.assertEqual(state.lifecycle.stage, ApplicationLifecycleStage.READY_FOR_WORKBENCH)
        self.assertEqual(state.revision, 1 + len(sequence))
        self.assertIsNone(state.pending_interaction)

    def test_pending_submission_is_idempotent_and_rejects_stale_revision(self) -> None:
        """重复提交同一交互应幂等，过期 revision 应显式失败。"""

        state = create_application_lifecycle(application_id="app-1", application_name="任务中心")
        state = transition_application_lifecycle(
            state,
            stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
            status=ApplicationLifecycleStatus.RUNNING,
        )
        state = transition_application_lifecycle(
            state,
            stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            pending_type=PendingInteractionType.REQUIREMENT_CLARIFICATION,
        )
        pending = state.pending_interaction
        assert pending is not None
        submitted = submit_pending_interaction(
            state,
            interaction_id=pending.id,
            based_on_revision=pending.based_on_revision,
        )
        repeated = submit_pending_interaction(
            submitted,
            interaction_id=pending.id,
            based_on_revision=submitted.revision,
        )

        self.assertEqual(repeated.revision, submitted.revision)
        regenerated = transition_application_lifecycle(
            submitted,
            stage=ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            pending_type=PendingInteractionType.REQUIREMENT_CLARIFICATION,
        )
        assert regenerated.pending_interaction is not None
        self.assertNotEqual(regenerated.pending_interaction.id, pending.id)
        self.assertIsNone(regenerated.pending_interaction.submitted_at)
        with self.assertRaises(ApplicationLifecycleConflictError):
            submit_pending_interaction(
                submitted,
                interaction_id="old-interaction",
                based_on_revision=state.revision,
            )

    def test_three_creation_interruptions_survive_reload(self) -> None:
        """澄清、需求确认和计划确认都应在重启读取后保持同一交互点。"""

        targets = [
            (
                ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
                PendingInteractionType.REQUIREMENT_CLARIFICATION,
            ),
            (
                ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
                PendingInteractionType.REQUIREMENT_CONFIRMATION,
            ),
            (
                ApplicationLifecycleStage.AWAITING_PROJECT_PLAN_CONFIRMATION,
                PendingInteractionType.PROJECT_PLAN_CONFIRMATION,
            ),
        ]
        for target_stage, target_type in targets:
            with self.subTest(stage=target_stage), tempfile.TemporaryDirectory() as directory:
                state = create_application_lifecycle(
                    application_id="app-1",
                    application_name="任务中心",
                )
                route = [
                    (ApplicationLifecycleStage.ANALYZING_REQUIREMENT, None),
                    (
                        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CLARIFICATION,
                        PendingInteractionType.REQUIREMENT_CLARIFICATION,
                    ),
                    (ApplicationLifecycleStage.ANALYZING_REQUIREMENT, None),
                    (ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC, None),
                    (
                        ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
                        PendingInteractionType.REQUIREMENT_CONFIRMATION,
                    ),
                    (ApplicationLifecycleStage.GENERATING_PROJECT_PLAN, None),
                    (
                        ApplicationLifecycleStage.AWAITING_PROJECT_PLAN_CONFIRMATION,
                        PendingInteractionType.PROJECT_PLAN_CONFIRMATION,
                    ),
                ]
                for stage, pending_type in route:
                    state = transition_application_lifecycle(
                        state,
                        stage=stage,
                        status=(
                            ApplicationLifecycleStatus.AWAITING_USER
                            if pending_type
                            else ApplicationLifecycleStatus.RUNNING
                        ),
                        pending_type=pending_type,
                    )
                    if stage == target_stage:
                        break
                write_application_lifecycle(directory, state)
                reloaded = load_application_lifecycle(directory)
                assert reloaded is not None and reloaded.pending_interaction is not None
                self.assertEqual(reloaded.lifecycle.stage, target_stage)
                self.assertEqual(reloaded.pending_interaction.type, target_type)

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

    def test_corrupt_and_future_versions_fail_explicitly(self) -> None:
        """损坏与未来版本不得静默推断或兼容。"""

        with tempfile.TemporaryDirectory() as directory:
            path = application_lifecycle_path(directory)
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ApplicationLifecycleCorruptedError):
                load_application_lifecycle(directory)

            path.write_text(json.dumps({"schemaVersion": "9.0.0"}), encoding="utf-8")
            with self.assertRaises(UnsupportedApplicationLifecycleVersionError):
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

    def test_template_generation_failure_retry_and_success_are_persisted(self) -> None:
        """应用模板文件生成失败应阻止工作台，重试成功后才进入 ready。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            specs = workspace / ".xcodeagent/specs"
            plans = workspace / ".xcodeagent/plans"
            specs.mkdir(parents=True)
            plans.mkdir(parents=True)
            for path in (
                specs / "requirement-spec.json",
                plans / "project-plan.json",
            ):
                path.write_text(
                    json.dumps({"confirmation_status": "confirmed"}),
                    encoding="utf-8",
                )
            state = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
            route = [
                ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
                ApplicationLifecycleStage.GENERATING_PROJECT_PLAN,
                ApplicationLifecycleStage.AWAITING_PROJECT_PLAN_CONFIRMATION,
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
                failed.lifecycle.stage,
                ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
            )
            assert failed.error is not None
            self.assertEqual(failed.error.code, "application_template_generation_failed")

            ready = complete_application_template_generation(directory, succeeded=True)
            self.assertEqual(ready.lifecycle.stage, ApplicationLifecycleStage.READY_FOR_WORKBENCH)
            loaded = load_application_lifecycle(directory)
            assert loaded is not None
            self.assertEqual(loaded.lifecycle.stage, ApplicationLifecycleStage.READY_FOR_WORKBENCH)


if __name__ == "__main__":
    unittest.main()
