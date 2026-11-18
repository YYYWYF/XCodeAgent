"""验证集成子图选择的实际规划输入及授权边界。"""
import tempfile
import unittest
from unittest.mock import patch

from app.graph.subgraphs.testing import integration_repair_planning
from app.services.test_validation import evaluate_quality_gate


class IntegrationPlanningInputTests(unittest.TestCase):
    def test_integration_entry_omits_build_plan(self):
        """非空、空和缺失计划均不进入模型，任务仍由平台授权。"""
        report = evaluate_quality_gate(test_results=[{
            "id": "frontend_build", "name": "构建", "passed": False,
            "evidence": "src/a.ts(2,1): error TS2322",
        }])
        with tempfile.TemporaryDirectory() as workspace:
            for plan in ({"tasks": ["ONLY_IN_BUILD_PLAN"]}, {}, None):
                state = {
                    "workspace": workspace, "test_report": report,
                    "test_results": report["checks"], "revision_requests": report["revision_requests"],
                }
                if plan is not None:
                    state["build_task_plan"] = plan
                with patch("app.agents.repair_planner.planner._invoke_repair_planner_agent",
                           return_value='{"decision":"repair","strategy":"修复类型错误"}') as model:
                    result = integration_repair_planning(state)
                prompt = model.call_args.kwargs["prompt"]
                self.assertNotIn("CurrentBuildTaskPlan", prompt)
                self.assertNotIn("ONLY_IN_BUILD_PLAN", prompt)
                self.assertIn("TS2322", prompt)
                task, = result["repair_tasks"]
                self.assertEqual(task["owner"], "frontend")
                self.assertIn("frontend", task["allowed_paths"])
