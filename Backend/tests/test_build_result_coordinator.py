from __future__ import annotations

import unittest
import json

from app.services.build_result_coordinator import create_agent_task_results


class BuildResultCoordinatorTests(unittest.TestCase):
    def test_missing_display_summary_does_not_invalidate_task_batch(self) -> None:
        """缺失、空白及 null 摘要不能阻断 knowledge 或连带拒绝同批其他任务。"""
        tasks = [{"id": "agent:it_assistant::knowledge", "owner": "agent"},
                 {"id": "context", "owner": "agent"}]
        for summary in ({}, {"summary": ""}, {"summary": "  "}, {"summary": None}):
            with self.subTest(summary=summary):
                note = json.dumps({"task_results": [
                    {"task_id": tasks[0]["id"], "status": "completed", **summary},
                    {"task_id": "context", "status": "already_satisfied"}]})
                results = create_agent_task_results(tasks, note, require_structured=True, strict_schema=True)
                self.assertEqual([item["status"] for item in results], ["completed", "already_satisfied"])
                self.assertTrue(all(item["agent_note"].strip() for item in results))
                self.assertTrue(all(not item["failure_category"] for item in results))

    def test_missing_summary_does_not_hide_real_failure(self) -> None:
        """不要求摘要仍须保留真实失败，缺少失败原因也不得伪造完成。"""
        task = {"id": "knowledge", "owner": "agent"}
        for extra, category in [({"failure_category": "tool_error", "failure_reason": "文件写入失败"}, "tool_error"),
                                ({}, "invalid_structured_response")]:
            note = json.dumps({"task_results": [{"task_id": "knowledge", "status": "failed", **extra}]})
            result = create_agent_task_results([task], note, require_structured=True, strict_schema=True)[0]
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failure_category"], category)

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

        results = create_agent_task_results(tasks, note, strict_schema=True)

        self.assertEqual([result["status"] for result in results], ["completed", "already_satisfied"])
        self.assertEqual(results[0]["agent_note"], "已写入页面")
        self.assertEqual(
            results[1]["satisfaction_evidence"]["target_files"],
            ["frontend/src/constants/menus.ts"],
        )

    def test_strict_schema_accepts_failed_change_request_contract(self) -> None:
        """合同不匹配失败必须携带固定失败字段和非空变更请求。"""

        results = create_agent_task_results(
            [{"id": "backend", "owner": "backend"}],
            """{
              "task_results": [{
                "task_id": "backend",
                "status": "failed",
                "summary": "接口合同缺少实现语义",
                "failure_category": "contract_mismatch",
                "failure_reason": "缺少冲突处理规则",
                "change_request": {"reason": "请补充冲突处理规则"}
              }]
            }""",
            require_structured=True,
            strict_schema=True,
        )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["failure_category"], "contract_mismatch")
        self.assertEqual(
            results[0]["change_request"]["reason"],
            "请补充冲突处理规则",
        )

    def test_strict_schema_accepts_already_satisfied_without_agent_evidence(self) -> None:
        """已满足状态的可信证据应由平台工程验收生成，而非要求 Agent 自报。"""

        results = create_agent_task_results(
            [{"id": "backend-bootstrap", "owner": "backend"}],
            """{
              "task_results": [{
                "task_id": "backend-bootstrap",
                "status": "already_satisfied",
                "summary": "现有工程配置已满足要求"
              }]
            }""",
            require_structured=True,
            strict_schema=True,
        )

        self.assertEqual(results[0]["status"], "already_satisfied")
        self.assertIsNone(results[0]["satisfaction_evidence"])

    def test_strict_schema_rejects_duplicate_unknown_missing_and_extra_top_level(self) -> None:
        """严格协议必须拒绝重复、未知、缺失任务以及额外顶层字段。"""

        tasks = [
            {"id": "first", "owner": "backend"},
            {"id": "second", "owner": "backend"},
        ]
        notes = {
            "duplicate": """{"task_results":[
              {"task_id":"first","status":"completed","summary":"ok"},
              {"task_id":"first","status":"completed","summary":"ok"}
            ]}""",
            "unknown": """{"task_results":[
              {"task_id":"first","status":"completed","summary":"ok"},
              {"task_id":"unknown","status":"completed","summary":"ok"}
            ]}""",
            "missing": """{"task_results":[
              {"task_id":"first","status":"completed","summary":"ok"}
            ]}""",
            "extra_top_level": """{
              "task_results":[
                {"task_id":"first","status":"completed","summary":"ok"},
                {"task_id":"second","status":"completed","summary":"ok"}
              ],
              "summary":"not allowed"
            }""",
        }

        for case, note in notes.items():
            with self.subTest(case=case):
                results = create_agent_task_results(
                    tasks,
                    note,
                    require_structured=True,
                    strict_schema=True,
                )
                self.assertEqual(
                    [result["failure_category"] for result in results],
                    ["invalid_structured_response", "invalid_structured_response"],
                )

    def test_non_strict_schema_accepts_extra_top_level_fields(self) -> None:
        """严格模式关闭时保留任务结果，并忽略 Agent 返回的额外顶层摘要。"""

        results = create_agent_task_results(
            [{"id": "backend", "owner": "backend"}],
            """{
              "task_results": [
                {"task_id": "backend", "status": "completed", "summary": "ok"}
              ],
              "summary": "extra top-level summary"
            }""",
            require_structured=True,
        )

        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[0]["agent_note"], "ok")

    def test_invalid_structured_status_becomes_protocol_failure(self) -> None:
        """结构化状态不合法时必须显式失败，不能回退成 completed。"""

        results = create_agent_task_results(
            [{"id": "layout", "owner": "frontend"}],
            '{"task_id":"layout","status":"done","summary":"ok"}',
        )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["failure_category"], "runner_protocol_error")

    def test_unescaped_summary_quotes_are_repaired_once(self) -> None:
        """summary 内未转义双引号应受控修复，不能把已返回任务误报为 omitted。"""

        note = """{
          "task_results": [{
            "task_id": "menu",
            "status": "already_satisfied",
            "satisfaction_evidence": {
              "acceptance_criteria": [
                {"criterion": "label 为中文"概览页"。", "status": "passed", "evidence": "line 17"},
                {"criterion_index": 1, "status": "passed", "evidence": "line 18"}
              ]
            }
          }]
        }"""

        results = create_agent_task_results(
            [{"id": "menu", "owner": "frontend"}],
            note,
            require_structured=True,
        )

        self.assertEqual(results[0]["status"], "already_satisfied")
        self.assertTrue(results[0]["structured_response_recovered"])

    def test_unrecoverable_structured_json_has_precise_failure_category(self) -> None:
        """无法修复的结构化 JSON 必须报告格式损坏，而不是误报任务遗漏。"""

        results = create_agent_task_results(
            [{"id": "menu", "owner": "frontend"}],
            '{"task_results":[{"task_id":"menu"',
            require_structured=True,
        )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(
            results[0]["failure_category"],
            "invalid_structured_response",
        )
        self.assertIn("malformed", results[0]["failure_reason"])

    def test_required_structured_report_rejects_empty_agent_text(self) -> None:
        """要求结构化协议时，无最终文本不得兼容成 completed。"""

        results = create_agent_task_results(
            [{"id": "menu", "owner": "frontend"}],
            "Agent completed without a text message.",
            require_structured=True,
        )

        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(
            results[0]["failure_category"],
            "invalid_structured_response",
        )


if __name__ == "__main__":
    unittest.main()
