"""Runtime 单测报告摘要及无进展循环的回归。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.services.stage_execution_artifacts import bind_unit_test_report, load_stage_execution_artifacts, _file_digest


class RuntimeTestReportBindingTests(TestCase):
    """使用精确临时文件验证报告可复用，变化仍失效且不无限重跑。"""

    def test_runtime_report_survives_confirmation_reload(self):
        """源码、测试、包入口及依赖文件都应获得有效摘要。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = ["agent-runtime/src/app/leave_balance/service.py", "agent-runtime/src/app/leave_balance/__init__.py",
                     "agent-runtime/tests/test_balance.py", "agent-runtime/pyproject.toml", "agent-runtime/uv.lock"]
            for relative in paths:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# test input\n")
            state = {"workspace": tmp, "build_execution_scope": {"type": "application"},
                     "build_task_plan": {"id": "plan"},
                     "unit_test_generation_context": {"source_files": paths[:2]},
                     "unit_test_generation": {"test_files": [paths[2]]}}
            report = bind_unit_test_report(state, {"passed": True})
            self.assertTrue(all(report["artifact_context"]["files"].values()))
            path = root / ".devagentstudio/reports/unit-test-report.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(report))
            fields = load_stage_execution_artifacts(state, state["build_task_plan"], "test_phase_confirmation")
            self.assertTrue(fields["unit_test_gate_passed"])
            (root / paths[0]).write_text("# changed\n")
            self.assertFalse(load_stage_execution_artifacts(state, state["build_task_plan"],
                                                          "test_phase_confirmation")["unit_test_gate_passed"])
            self.assertIsNone(_file_digest(root, "agent-runtime/../outside.py"))

    def test_completed_unit_test_with_invalid_report_stops_loop(self):
        """本轮已执行单测后报告仍失效必须失败退出，而不是再路由到单测。"""
        from app.graph.nodes.lifecycle import test_phase_confirmation
        from app.graph.workflow import route_test_phase_confirmation
        with patch("app.graph.nodes.lifecycle._test_target_record", return_value={}), patch(
            "app.graph.nodes.lifecycle._completed_build_summary", return_value={"status": "completed"}
        ):
            result = test_phase_confirmation({"unit_test_gate_passed": False,
                                              "unit_test_next_action": "test_phase_confirmation"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(route_test_phase_confirmation(result), "handle_failure")
