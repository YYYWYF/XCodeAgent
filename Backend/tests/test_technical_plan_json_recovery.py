"""TechnicalPlan 模型根 JSON 的受控语法恢复回归测试。"""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.agents.main import planner
from app.graph.nodes.planning import _generate_valid_technical_plan
from app.utils import model_output


def _broken_movie_technical_plan() -> str:
    """返回复现真实 DELETE Endpoint 缺少逗号问题的完整根对象。"""

    return """{
  "architecture": {
    "frontend": "React",
    "backend": "Java8 Springboot",
    "data": "MySQL8 Redis"
  },
  "entities": [],
  "api_contracts": [
    {
      "id": "movie_api",
      "entity_ids": [],
      "base_path": "/api/movies",
      "schemas": {},
      "endpoints": [
        {
          "id": "movie_api.delete",
          "method": "DELETE",
          "path": "/api/movies/{movieId}",
          "summary": "删除电影"
          "parameters": [{"name": "movieId", "in": "path"}],
          "request_schema_ref": null,
          "response_schema_ref": null,
          "error_codes": ["NOT_FOUND"],
          "authentication": {"required": false}
        }
      ]
    }
  ],
  "pages": []
}"""


class TechnicalPlanJsonRecoveryTests(unittest.TestCase):
    """验证 TechnicalPlan 专用根 JSON 恢复边界。"""

    def test_repairs_complete_root_instead_of_nested_architecture(self) -> None:
        """缺少逗号时必须恢复完整 TechnicalPlan，不能回退成 architecture 子对象。"""

        extractor = getattr(model_output, "extract_json_root_object_with_repair", None)
        self.assertIsNotNone(extractor, "TechnicalPlan 根 JSON 修复入口尚未实现")
        result = extractor(_broken_movie_technical_plan())

        self.assertGreaterEqual(
            set(result or {}),
            {"architecture", "entities", "api_contracts", "pages"},
        )
        self.assertEqual(
            result["api_contracts"][0]["endpoints"][0]["method"],
            "DELETE",
        )

    def test_valid_root_does_not_call_repair_parser(self) -> None:
        """合法 JSON 必须走标准库快路径并保持对象内容不变。"""

        text = '{"architecture": {}, "entities": [], "api_contracts": [], "pages": []}'
        with patch.object(
            model_output,
            "repair_json",
            side_effect=AssertionError("合法 JSON 不应调用 repair_json"),
        ):
            result = model_output.extract_json_root_object_with_repair(text)

        self.assertEqual(
            result,
            {
                "architecture": {},
                "entities": [],
                "api_contracts": [],
                "pages": [],
            },
        )

    def test_rejects_truncated_root_instead_of_auto_closing_it(self) -> None:
        """传输截断的 TechnicalPlan 不得被自动闭合并伪装成完整 candidate。"""

        truncated = (
            '{"architecture": {}, "entities": [], "api_contracts": [], '
            '"pages": [{"pageId": "movies"}'
        )

        self.assertIsNone(
            model_output.extract_json_root_object_with_repair(truncated)
        )

    def test_root_guard_rejects_invalid_section_types(self) -> None:
        """根 section 类型错误时必须在归一化前拒绝，避免静默丢失模型内容。"""

        parser = getattr(planner, "_parse_technical_plan_model_output", None)
        self.assertIsNotNone(parser, "TechnicalPlan 根结构守卫尚未实现")
        cases = [
            (
                '{"architecture": {}, "entities": {}, "api_contracts": [], "pages": []}',
                "entities",
            ),
            (
                '{"architecture": [], "entities": [], "api_contracts": [], "pages": []}',
                "architecture",
            ),
            (
                '{"architecture": {}, "entities": [], "api_contracts": [1], "pages": []}',
                "api_contracts",
            ),
        ]
        for payload, invalid_section in cases:
            with self.subTest(invalid_section=invalid_section):
                with self.assertRaisesRegex(ValueError, invalid_section):
                    parser(payload)

    def test_root_guard_rejects_nested_architecture_object(self) -> None:
        """只有 frontend/backend/data 的内部对象不得冒充 TechnicalPlan 根对象。"""

        parser = getattr(planner, "_parse_technical_plan_model_output", None)
        self.assertIsNotNone(parser, "TechnicalPlan 根结构守卫尚未实现")
        with self.assertRaisesRegex(
            ValueError,
            "architecture、entities、api_contracts、pages",
        ):
            parser(
                '{"frontend": "React", "backend": "Java8", "data": "MySQL8"}'
            )

    def test_root_guard_rejects_unrecoverable_text(self) -> None:
        """完全不可恢复的说明文本必须失败，不能生成伪 TechnicalPlan。"""

        with self.assertRaisesRegex(ValueError, "无法恢复"):
            planner._parse_technical_plan_model_output("模型暂时无法生成技术规划。")

    def test_repair_parser_exception_becomes_controlled_parse_failure(self) -> None:
        """第三方修复器异常必须收敛为解析失败，交回现有有界重试。"""

        with patch.object(model_output, "repair_json", side_effect=RuntimeError("boom")):
            result = model_output.extract_json_root_object_with_repair(
                _broken_movie_technical_plan()
            )

        self.assertIsNone(result)

    def test_repaired_syntax_uses_one_model_call_in_generation_loop(self) -> None:
        """本地修复成功后不得消耗第二次 TechnicalPlan 模型生成调用。"""

        calls = {"count": 0}

        def fake_invoke(*args, **kwargs) -> str:
            """记录真实规划入口调用次数并返回带单个缺失逗号的完整响应。"""

            calls["count"] += 1
            return _broken_movie_technical_plan()

        requirement = {"confirmed_product_plan": {"pages": []}}
        with (
            patch.object(planner.Settings, "from_env", return_value=SimpleNamespace()),
            patch.object(planner, "_invoke_live_chat_model", side_effect=fake_invoke),
            patch(
                "app.graph.nodes.planning._attach_technical_plan_contracts",
                side_effect=lambda state, candidate: candidate,
            ),
            patch(
                "app.graph.nodes.planning._project_plan_validation_errors",
                return_value=[],
            ),
        ):
            candidate, errors, repair_candidate = _generate_valid_technical_plan(
                {},
                requirement,
                None,
            )

        self.assertEqual(calls["count"], 1)
        self.assertEqual(errors, [])
        self.assertIsNone(repair_candidate)
        self.assertIsNotNone(candidate)
        endpoint = candidate["api_contracts"][0]["endpoints"][0]
        self.assertEqual(endpoint["method"], "DELETE")


if __name__ == "__main__":
    unittest.main()
