from __future__ import annotations

import unittest

from app.services.task_scheduler import annotate_task_execution, build_execution_batches


class TaskSchedulerTests(unittest.TestCase):
    def test_overlapping_executable_targets_are_direct_write_and_serial(self) -> None:
        """目标重叠任务应交给受限执行器串行写入，不再停留在无人集成的 plan-only。"""

        tasks = annotate_task_execution(
            [
                {
                    "id": "layout",
                    "task_type": "frontend",
                    "target_files": ["frontend/src/pages/Dashboard/index.tsx"],
                    "dependencies": [],
                },
                {
                    "id": "data",
                    "task_type": "frontend",
                    "target_files": ["frontend/src/pages/Dashboard/index.tsx"],
                    "dependencies": ["layout"],
                },
            ]
        )

        self.assertTrue(all(task["executionMode"] == "subagent-direct-write" for task in tasks))
        self.assertTrue(all(task["can_run_in_parallel"] is False for task in tasks))
        self.assertEqual(
            [batch["tasks"] for batch in build_execution_batches(tasks)],
            [["layout"], ["data"]],
        )


if __name__ == "__main__":
    unittest.main()
