from __future__ import annotations

import unittest

from app.services.build_scheduler import (
    classify_task_result,
    normalize_task_results,
    select_ready_build_batch,
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


if __name__ == "__main__":
    unittest.main()
