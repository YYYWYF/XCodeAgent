from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from app.services.build_scheduler import (
    classify_task_result,
    normalize_task_results,
    select_ready_build_batch,
    verify_task_file_changes,
)


class BuildSchedulerTests(unittest.TestCase):
    def test_selects_dependency_ready_lock_compatible_batch(self) -> None:
        tasks = [
            {
                "id": "api",
                "owner": "data_source",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Backend/app/api.py"}],
            },
            {
                "id": "api-test",
                "owner": "data_source",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Backend/app/api.py"}],
            },
            {
                "id": "page",
                "owner": "frontend",
                "status": "pending",
                "dependencies": [],
                "change_scope": [{"path": "Frontend/src/Page.tsx"}],
            },
        ]

        selection = select_ready_build_batch(tasks)

        self.assertEqual(selection["ready_task_ids"], ["api", "page"])

    def test_failed_dependency_blocks_downstream(self) -> None:
        tasks = [
            {"id": "api", "status": "failed", "dependencies": []},
            {"id": "page", "status": "pending", "dependencies": ["api"]},
        ]

        selection = select_ready_build_batch(tasks)

        self.assertEqual(selection["ready_tasks"], [])
        self.assertEqual(selection["blocked_tasks"][0]["id"], "page")
        self.assertEqual(selection["blocked_tasks"][0]["failed_dependencies"], ["api"])

    def test_normalizes_missing_runner_result_as_protocol_failure(self) -> None:
        task = {"id": "page", "owner": "frontend"}

        results = normalize_task_results(dispatched_tasks=[task], raw_results=[])

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["failure_category"], "runner_protocol_error")
        self.assertEqual(
            results[0]["scheduler_decision"]["action"],
            "terminal_failure",
        )

    def test_classifies_implementation_failure_as_repair(self) -> None:
        decision = classify_task_result(
            {"task_id": "page", "status": "failed", "failure_category": "test_failure"}
        )

        self.assertEqual(decision["action"], "repair")

    def test_structured_already_satisfied_requires_exact_files_and_all_criteria(self) -> None:
        """已满足只能由精确磁盘目标和逐条验收证据确定性通过。"""

        task = {
            "id": "page",
            "owner": "frontend",
            "targetFiles": ["frontend/src/pages/Dashboard/index.tsx"],
            "change_scope": [
                {
                    "operation": "modify",
                    "path": "frontend/src/pages/Dashboard/index.tsx",
                }
            ],
            "acceptance_criteria": ["页面可编译", "标题可见"],
        }
        evidence = {
            "target_files": ["frontend/src/pages/Dashboard/index.tsx"],
            "acceptance_criteria": [
                {"criterion": "页面可编译", "status": "passed", "evidence": "pnpm build"},
                {"criterion": "标题可见", "status": "passed", "evidence": "组件包含标题"},
            ],
        }
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / task["targetFiles"][0]
            target.parent.mkdir(parents=True)
            target.write_text("export default function Dashboard() {}", encoding="utf-8")
            results = verify_task_file_changes(
                results=[
                    {
                        "task_id": "page",
                        "status": "already_satisfied",
                        "satisfaction_evidence": evidence,
                    }
                ],
                code_change_set=None,
                tasks=[task],
                workspace_root=workspace,
            )

        self.assertEqual(results[0]["status"], "already_satisfied")
        self.assertEqual(results[0]["scheduler_decision"]["action"], "complete")

    def test_similar_file_cannot_satisfy_missing_exact_target(self) -> None:
        """语义相似但路径不同的文件不得通过已满足校验。"""

        task = {
            "id": "page",
            "owner": "frontend",
            "targetFiles": ["frontend/src/pages/DashboardPage/index.tsx"],
            "change_scope": [
                {
                    "operation": "add",
                    "path": "frontend/src/pages/DashboardPage/index.tsx",
                }
            ],
            "acceptance_criteria": ["目标文件存在"],
        }
        with tempfile.TemporaryDirectory() as workspace:
            similar = Path(workspace) / "frontend/src/pages/Dashboard/index.tsx"
            similar.parent.mkdir(parents=True)
            similar.write_text("export default function Dashboard() {}", encoding="utf-8")
            results = verify_task_file_changes(
                results=[
                    {
                        "task_id": "page",
                        "status": "already_satisfied",
                        "satisfaction_evidence": {
                            "target_files": task["targetFiles"],
                            "acceptance_criteria": [
                                {
                                    "criterion": "目标文件存在",
                                    "status": "passed",
                                    "evidence": "发现 Dashboard 页面",
                                }
                            ],
                        },
                    }
                ],
                code_change_set=None,
                tasks=[task],
                workspace_root=workspace,
            )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["failure_category"], "no_file_changes")

    def test_already_satisfied_dependency_unblocks_downstream_task(self) -> None:
        """结构化已满足是终态，必须和 completed 一样解除下游依赖。"""

        selection = select_ready_build_batch(
            [
                {"id": "layout", "status": "already_satisfied", "dependencies": []},
                {"id": "data", "status": "pending", "dependencies": ["layout"]},
            ]
        )

        self.assertEqual(selection["ready_task_ids"], ["data"])


if __name__ == "__main__":
    unittest.main()
