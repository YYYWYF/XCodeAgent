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

        self.assertIn("## 1. 前后端构建与后端启动检查", markdown)
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
                            "performance_details": {
                                "metric_scores": {"fcp": 0.98, "lcp": 0.95, "tbt": 0.8, "cls": 1, "si": 0.4},
                                "opportunities": [{
                                    "id": "meta-description", "category": "seo",
                                    "title": "文档缺少元描述",
                                    "description": "添加简洁的页面描述，帮助搜索引擎理解页面。",
                                }],
                                "context": {
                                    "url": "http://localhost:3000/page/home?token=private#details",
                                    "device": "mobile", "fetch_time": "2026-10-19T07:00:00Z",
                                    "lighthouse_version": "7.3.0",
                                },
                            },
                            "report_path": (
                                f"{workspace}/.devagentstudio/runtime/tests/"
                                "frontend_performance/report.html"
                            ),
                            "raw_report_html": "<html>完整 Lighthouse 正文</html>",
                        },
                    ]
                },
            )

        self.assertIn("## 2. 前端性能测试", markdown)
        self.assertIn("整体结论：页面性能表现良好（92/100）", markdown)
        self.assertIn("建议优先关注：搜索引擎优化", markdown)
        self.assertIn("检测状态：已完成", markdown)
        self.assertIn("| 最佳实践 | 100/100 · 良好 |", markdown)
        self.assertIn("| 搜索引擎优化 | 88/100 · 需改进 |", markdown)
        self.assertIn("| 首次内容显示（FCP） | 0.90 秒 · 良好 |", markdown)
        self.assertIn("| 最大内容呈现（LCP） | 1.80 秒 · 良好 |", markdown)
        self.assertIn("| 总阻塞时间（TBT） | 120 毫秒 · 需改进 |", markdown)
        self.assertIn("| 布局偏移（CLS） | 0.020 · 良好 |", markdown)
        self.assertIn("| 视觉加载速度（SI） | 1.50 秒 · 较差 |", markdown)
        self.assertIn("文档缺少元描述", markdown)
        self.assertIn("用户影响：可能影响搜索引擎", markdown)
        self.assertIn("[查看完整 Lighthouse 报告](./.devagentstudio/runtime/tests/frontend_performance/report.html)", markdown)
        self.assertIn("设备模式：移动端模拟", markdown)
        self.assertIn("2026-10-19T07:00:00+00:00", markdown)
        self.assertNotIn("token=private", markdown)
        self.assertNotIn("#details", markdown)
        self.assertLess(markdown.index("整体结论"), markdown.index("### 分类评分"))
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
        self.assertIn("执行失败或未完整完成", markdown)
        self.assertIn("未获得数据", markdown)
        self.assertNotIn("0.00 秒", markdown)

    def test_partial_or_invalid_data_never_becomes_zero_or_an_invented_rating(self) -> None:
        """非法及缺失数值明确标注，低分也不会被命令成功掩盖。"""

        with tempfile.TemporaryDirectory() as workspace:
            markdown = render_test_report_markdown(
                {"workspace": workspace, "frontend_performance_decision": "run"},
                {"checks": [{
                    "id": "frontend_performance", "passed": True,
                    "performance_scores": {"performance": 40, "accessibility": True},
                    "performance_metrics": {"fcp": 490, "lcp": float('nan'), "tbt": -2, "cls": float('inf')},
                }]},
            )
        self.assertIn("页面性能表现较差（40/100）", markdown)
        self.assertIn("0.49 秒 · 未提供评级", markdown)
        self.assertIn("| 无障碍访问 | 未获得数据 |", markdown)
        self.assertIn("| 最大内容呈现（LCP） | 未获得数据 |", markdown)
        self.assertIn("未获得可展示的具体改进项", markdown)
        self.assertNotIn("缺少元描述", markdown)

    def test_failed_run_retains_partial_metrics_and_rejects_outside_report_links(self) -> None:
        """执行失败仍展示已采集证据，报告入口不能指向工作区外文件。"""

        with tempfile.TemporaryDirectory() as workspace:
            markdown = render_test_report_markdown(
                {"workspace": workspace, "frontend_performance_decision": "run"},
                {"checks": [{
                    "id": "frontend_performance", "passed": False,
                    "evidence": "检测超时", "performance_metrics": {"fcp": 500},
                    "report_path": "/tmp/private-report.html",
                    "performance_details": {"opportunities": [{
                        "category": "seo", "title": "[恶意链接](javascript:alert)",
                        "description": "修复 | 项目\n/Users/private/secret <script>bad</script>",
                    }]},
                }]},
            )
        self.assertIn("已有数据仅供排查", markdown)
        self.assertIn("0.50 秒", markdown)
        self.assertIn("完整 Lighthouse 报告暂不可用", markdown)
        self.assertNotIn("javascript:", markdown)
        self.assertNotIn("/Users/private", markdown)
        self.assertNotIn("<script>", markdown)

    def test_json_and_markdown_are_written_to_separate_current_paths(self) -> None:
        """内部 JSON 与用户 Markdown 必须写入各自固定路径。"""

        with tempfile.TemporaryDirectory() as workspace:
            state = {"workspace": workspace, "frontend_performance_decision": "skip"}
            report = {"passed": True, "checks": []}
            json_path = Path(write_test_report_json(state, report))
            markdown_path = Path(write_test_report_markdown(state, report))

            self.assertEqual(
                json_path,
                Path(workspace).resolve() / ".devagentstudio/reports/test-report.json",
            )
            self.assertEqual(
                markdown_path,
                Path(workspace).resolve() / ".devagentstudio/reports/test-report.md",
            )
            self.assertEqual(load_test_report_json(json_path), report)
            self.assertTrue(markdown_path.is_file())


if __name__ == "__main__":
    unittest.main()
