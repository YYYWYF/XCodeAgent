from __future__ import annotations

import unittest

from app.services.lighthouse_report import parse_lighthouse_report
from app.services.test_validation import evaluate_quality_gate
from app.workspace.test_documents import render_test_report_markdown


class LighthouseReportTests(unittest.TestCase):
    """验证真实检查证据提取，以及质量门到 Markdown 的完整数据保留。"""

    def test_extracts_real_issues_and_metric_scores_through_quality_gate(self) -> None:
        """只提取计分的实际失败项，去重并按优先级限制为三项。"""

        parsed = parse_lighthouse_report({
            "categories": {
                "performance": {"score": 1},
                "seo": {"score": .77, "auditRefs": [
                    {"id": "meta-description", "weight": 1},
                    {"id": "manual", "weight": 1},
                    {"id": "ignored", "weight": 0},
                    {"id": "passed", "weight": 1},
                ]},
                "accessibility": {"score": .89, "auditRefs": [
                    {"id": key, "weight": 1} for key in ("contrast", "name", "other", "meta-description")
                ]},
            },
            "audits": {
                "first-contentful-paint": {"numericValue": 490, "score": .899},
                "meta-description": {"score": 0, "title": "缺少页面描述", "description": "添加页面摘要。"},
                "contrast": {"score": 0, "title": "对比度不足"},
                "name": {"score": 0, "title": "按钮缺少名称"},
                "other": {"score": .5, "title": "其他问题"},
                "manual": {"score": 0, "scoreDisplayMode": "manual", "title": "需要人工确认"},
                "ignored": {"score": 0, "title": "不参与评分"},
                "passed": {"score": 1, "title": "已通过"},
            },
            "finalUrl": "http://localhost:3000/", "fetchTime": "2026-10-19T07:00:00Z",
            "configSettings": {"formFactor": "desktop"},
        })
        assert parsed is not None
        details = parsed["details"]
        self.assertEqual(details["metric_scores"]["fcp"], .899)
        self.assertEqual([item["id"] for item in details["opportunities"]], ["meta-description", "contrast", "name"])
        check = {
            "id": "frontend_performance", "passed": True, "blocking": False,
            "performance_scores": parsed["scores"], "performance_metrics": parsed["metrics"],
            "performance_details": details,
        }
        report = evaluate_quality_gate(test_results=[check])
        markdown = render_test_report_markdown({"workspace": "/tmp/report-workspace", "frontend_performance_decision": "run"}, report)
        self.assertIn("缺少页面描述", markdown)
        self.assertIn("处理建议：为页面补充简洁、准确的 meta description", markdown)
        self.assertIn("0.49 秒 · 需改进", markdown)
        self.assertIn("设备模式：桌面端", markdown)
        self.assertNotIn("需要人工确认", markdown)

    def test_invalid_numbers_and_absent_fields_are_not_fabricated(self) -> None:
        """拒绝非法数值，保留有效的零值，且不从无关 audit 推测问题。"""

        self.assertIsNone(parse_lighthouse_report({"categories": {"seo": {"score": True}}}))
        parsed = parse_lighthouse_report({
            "categories": {"performance": {"score": float('inf')}},
            "audits": {
                "cumulative-layout-shift": {"numericValue": 0, "score": 1},
                "total-blocking-time": {"numericValue": -1},
                "largest-contentful-paint": {"numericValue": float('nan')},
                "first-contentful-paint": {"numericValue": True},
                "unused": {"score": 0, "title": "无归属项"},
            },
        })
        assert parsed is not None
        self.assertEqual(parsed["metrics"], {"cls": 0})
        self.assertEqual(parsed["scores"], {})
        self.assertEqual(parsed["details"]["opportunities"], [])
