from __future__ import annotations

import unittest

from app.services.build_result_coordinator import create_agent_task_results


class BuildResultCoordinatorTests(unittest.TestCase):
    def test_structured_task_reports_preserve_per_task_status_and_evidence(self) -> None:
        """同批 Agent 的结构化报告必须按任务 ID 分离状态和已满足证据。"""

        tasks = [
            {"id": "layout", "owner": "frontend"},
            {"id": "menu", "owner": "frontend"},
        ]
        note = """{
          "task_results": [
            {"task_id": "layout", "status": "completed", "summary": "已写入页面"},
            {
              "task_id": "menu",
              "status": "already_satisfied",
              "summary": "菜单项已存在",
              "satisfaction_evidence": {
                "target_files": ["frontend/src/constants/menus.ts"],
                "acceptance_criteria": []
              }
            }
          ]
        }"""

        results = create_agent_task_results(tasks, note)

        self.assertEqual([result["status"] for result in results], ["completed", "already_satisfied"])
        self.assertEqual(results[0]["agent_note"], "已写入页面")
        self.assertEqual(
            results[1]["satisfaction_evidence"]["target_files"],
            ["frontend/src/constants/menus.ts"],
        )

    def test_invalid_structured_status_becomes_protocol_failure(self) -> None:
        """结构化状态不合法时必须显式失败，不能回退成 completed。"""

        results = create_agent_task_results(
            [{"id": "layout", "owner": "frontend"}],
            '{"task_id":"layout","status":"done","summary":"ok"}',
        )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["failure_category"], "runner_protocol_error")


if __name__ == "__main__":
    unittest.main()
