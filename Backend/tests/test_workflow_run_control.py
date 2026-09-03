from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.application_lifecycle import PendingInteractionType
from app.protocols.workflow.run_control import abandon_pending_build_task_plan


class WorkflowRunControlTests(unittest.TestCase):
    """验证不启动 Graph 的计划控制动作仍完整收口业务产物。"""

    def test_abandon_pending_dag_marks_plan_before_execution_unlock(self) -> None:
        """放弃 DAG 必须持久化 abandoned，后续新流程不得复用旧 pending 计划。"""

        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / ".xcodeagent/plans/build-task-plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                json.dumps({"status": "ready", "confirmation_status": "pending"}),
                encoding="utf-8",
            )
            lifecycle = SimpleNamespace(
                active_executions={
                    "run-dag": SimpleNamespace(
                        pending_interaction=SimpleNamespace(
                            type=PendingInteractionType.TASK_PLAN_CONFIRMATION,
                            payload={"mode": "build_task_plan_confirmation"},
                        )
                    )
                }
            )

            with patch(
                "app.protocols.workflow.run_control.load_application_lifecycle",
                return_value=lifecycle,
            ):
                abandoned = abandon_pending_build_task_plan(directory, run_id="run-dag")

            persisted = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertTrue(abandoned)
            self.assertEqual(persisted["confirmation_status"], "abandoned")
            self.assertTrue(persisted["abandoned_at"])

    def test_end_other_pending_interaction_does_not_touch_dag(self) -> None:
        """结束非 DAG 交互时不得误改工作区中的任务计划。"""

        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / ".xcodeagent/plans/build-task-plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                json.dumps({"status": "ready", "confirmation_status": "pending"}),
                encoding="utf-8",
            )
            lifecycle = SimpleNamespace(
                active_executions={
                    "run-acceptance": SimpleNamespace(
                        pending_interaction=SimpleNamespace(
                            type=PendingInteractionType.PAGE_ACCEPTANCE,
                            payload={"mode": "page_acceptance"},
                        )
                    )
                }
            )

            with patch(
                "app.protocols.workflow.run_control.load_application_lifecycle",
                return_value=lifecycle,
            ):
                abandoned = abandon_pending_build_task_plan(
                    directory,
                    run_id="run-acceptance",
                )

            persisted = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertFalse(abandoned)
            self.assertEqual(persisted["confirmation_status"], "pending")


if __name__ == "__main__":
    unittest.main()
