from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.workspace.test_documents import (
    load_test_report_json,
    render_test_report_markdown,
    write_test_report_json,
    write_test_report_markdown,
)


class TestDocumentsTests(unittest.TestCase):
    """覆盖集成测试 Markdown/JSON 报告的当前存储与展示契约。"""

    def test_markdown_always_contains_frontend_and_backend_build_rows(self) -> None:
        """缺少后端构建结果时仍必须显示固定的不适用行。"""

        with tempfile.TemporaryDirectory() as workspace:
            markdown = render_test_report_markdown(
                {"workspace": workspace, "frontend_performance_decision": "skip"},
                {
                    "checks": [
                        {
                            "id": "frontend_build",
                            "passed": True,
                            "skipped": False,
                            "evidence": f"命令在 {workspace}/frontend 执行通过。",
                        }
                    ]
                },
            )

        self.assertIn("## 1. 前后端构建检查", markdown)
        self.assertIn("| 前端构建检查 | 通过 |", markdown)
        self.assertIn("| 后端构建检查 | 未执行/不适用 |", markdown)
        self.assertNotIn(workspace, markdown)
        self.assertNotIn("## 2. 前端性能测试", markdown)

    def test_markdown_summarizes_executed_lighthouse_report(self) -> None:
        """执行性能测试时只输出重点分类得分和核心指标。"""

        with tempfile.TemporaryDirectory() as workspace:
            markdown = render_test_report_markdown(
                {"workspace": workspace, "frontend_performance_decision": "run"},
                {
                    "checks": [
                        {"id": "frontend_build", "passed": True, "evidence": "通过"},
                        {"id": "backend_build", "passed": True, "evidence": "通过"},
                        {
                            "id": "frontend_performance",
                            "passed": True,
                            "skipped": False,
                            "evidence": "Lighthouse 报告已生成。",
                            "performance_scores": {
                                "performance": 92,
                                "accessibility": 95,
                                "best_practices": 100,
                                "seo": 88,
                            },
                            "performance_metrics": {
                                "fcp": 900,
                                "lcp": 1800,
                                "tbt": 120,
                                "cls": 0.02,
                                "si": 1500,
                            },
                            "report_path": (
                                f"{workspace}/.xcodeagent/runtime/tests/"
                                "frontend_performance/report.html"
                            ),
                            "raw_report_html": "<html>完整 Lighthouse 正文</html>",
                        },
                    ]
                },
            )

        self.assertIn("## 2. 前端性能测试", markdown)
        self.assertIn("| Performance | 92 |", markdown)
        self.assertIn("| Best Practices | 100 |", markdown)
        self.assertIn("| FCP | 0.90s |", markdown)
        self.assertIn("| LCP | 1.80s |", markdown)
        self.assertIn("| CLS | 0.020 |", markdown)
        self.assertNotIn(workspace, markdown)
        self.assertNotIn("完整 Lighthouse 正文", markdown)

    def test_failed_performance_run_keeps_a_bounded_summary(self) -> None:
        """性能执行失败时保留摘要，但不伪造得分与指标。"""

        with tempfile.TemporaryDirectory() as workspace:
            markdown = render_test_report_markdown(
                {"workspace": workspace, "frontend_performance_decision": "run"},
                {
                    "checks": [
                        {
                            "id": "frontend_performance",
                            "passed": False,
                            "skipped": True,
                            "evidence": "前端性能测试执行失败。",
                        }
                    ]
                },
            )

        self.assertIn("## 2. 前端性能测试", markdown)
        self.assertIn("前端性能测试执行失败", markdown)
        self.assertIn("未获得可展示的 Lighthouse", markdown)

    def test_json_and_markdown_are_written_to_separate_current_paths(self) -> None:
        """内部 JSON 与用户 Markdown 必须写入各自固定路径。"""

        with tempfile.TemporaryDirectory() as workspace:
            state = {"workspace": workspace, "frontend_performance_decision": "skip"}
            report = {"passed": True, "checks": []}
            json_path = Path(write_test_report_json(state, report))
            markdown_path = Path(write_test_report_markdown(state, report))

            self.assertEqual(
                json_path,
                Path(workspace).resolve() / ".xcodeagent/reports/test-report.json",
            )
            self.assertEqual(
                markdown_path,
                Path(workspace).resolve() / ".xcodeagent/reports/test-report.md",
            )
            self.assertEqual(load_test_report_json(json_path), report)
            self.assertTrue(markdown_path.is_file())


if __name__ == "__main__":
    unittest.main()
