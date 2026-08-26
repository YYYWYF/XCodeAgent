from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.subgraphs.acceptance import run_acceptance_subgraph


class AcceptanceSubgraphTests(unittest.TestCase):
    """验收子图负责启动项目、恢复启动快照并投影待验收状态。"""

    def test_successful_launch_enters_acceptance_review(self) -> None:
        """启动成功后必须进入 acceptance_review 并生成 page_acceptance。"""

        launch_result = {
            "status": "completed",
            "preview_url": "http://127.0.0.1:3000",
            "frontend": {"status": "completed"},
        }
        launch_update = {
            "phase": "launch_project",
            "status": "requires_user_input",
            "preview_url": launch_result["preview_url"],
            "launch_result": launch_result,
            "acceptance_request": {
                "status": "requires_user_input",
                "preview_url": launch_result["preview_url"],
            },
            "clarification": {
                "mode": "page_acceptance",
                "status": "requires_user_input",
            },
        }
        with patch("app.graph.nodes.lifecycle.launch_project", return_value=launch_update) as launcher:
            result = run_acceptance_subgraph({"workspace": "/workspace"})

        launcher.assert_called_once()
        self.assertEqual(result["phase"], "acceptance")
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "page_acceptance")
        self.assertEqual(result["preview_url"], launch_result["preview_url"])
        self.assertEqual(result["launch_result"], launch_result)
        self.assertEqual(result["acceptance_request"]["status"], "requires_user_input")

    def test_launch_failure_does_not_enter_acceptance_waiting(self) -> None:
        """启动失败直接结束子图，不得生成待验收 page_acceptance。"""

        launch_update = {
            "phase": "launch_project",
            "status": "failed",
            "preview_url": "项目启动失败",
            "launch_result": {"status": "failed", "message": "项目启动失败"},
            "acceptance_request": {"status": "failed"},
        }
        with patch("app.graph.nodes.lifecycle.launch_project", return_value=launch_update):
            result = run_acceptance_subgraph({"workspace": "/workspace"})

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "launch_project")
        self.assertNotEqual(
            (result.get("clarification") or {}).get("mode"), "page_acceptance"
        )

    def test_minimal_launch_failure_also_ends_without_acceptance_review(self) -> None:
        """即使启动器只返回失败状态，也不能误进入验收等待。"""

        with patch(
            "app.graph.nodes.lifecycle.launch_project",
            return_value={"status": "failed", "message": "启动失败"},
        ):
            result = run_acceptance_subgraph({"workspace": "/workspace"})

        self.assertEqual(result["status"], "failed")
        self.assertNotEqual(
            (result.get("clarification") or {}).get("mode"), "page_acceptance"
        )

    def test_successful_launch_snapshot_skips_duplicate_start(self) -> None:
        """恢复已有成功启动快照时不得再次调用项目启动器。"""

        state = {
            "phase": "acceptance",
            "launch_result": {
                "status": "completed",
                "preview_url": "http://127.0.0.1:3000",
            },
            "preview_url": "http://127.0.0.1:3000",
        }
        with patch("app.graph.nodes.lifecycle.launch_project") as launcher:
            result = run_acceptance_subgraph(state)

        launcher.assert_not_called()
        self.assertEqual(result["phase"], "acceptance")
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["preview_url"], "http://127.0.0.1:3000")

    def test_existing_acceptance_decision_keeps_finalize_capability(self) -> None:
        """后端已有 accepted 决策时保留直接完成验收的能力。"""

        with patch("app.graph.nodes.lifecycle.launch_project") as launcher:
            result = run_acceptance_subgraph({"acceptance_decision": "accepted"})

        launcher.assert_not_called()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["accepted"])


if __name__ == "__main__":
    unittest.main()
