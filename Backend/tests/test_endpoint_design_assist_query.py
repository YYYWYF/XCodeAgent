"""查询专用提示的阶段隔离与示例合同回归，不调用真实模型或数据库。"""

import json
import unittest
from unittest.mock import patch

from app.agents.endpoint_design_assist_prompts import build_assist_messages
from app.domain.endpoint_design_assist_steps import QueryStepResult
from app.services.endpoint_design_assist_steps import generated_summary, step_model_result
from app.services.endpoint_design_assist_validation import complete_mapping
from tests.test_endpoint_design_assist_steps import context_fixture


def query_context() -> dict:
    """构造关键词、分类、状态及分页参数，覆盖同一输入对应多个筛选列。"""
    context = context_fixture()
    context["endpoint"] = {"method": "GET", "path": "/api/product", "name": "查询商品列表"}
    context["instruction"] = "关键词匹配名称或编号，分类和状态精确匹配，所有筛选同时满足。"
    parameters = [{"side": "request", "location": "query", "path": name, "type": kind,
                   "required": False, "description": description}
                  for name, kind, description in (
                      ("keyword", "string", "名称或编号关键词"), ("categoryId", "integer", "分类 ID"),
                      ("status", "string", "商品状态"), ("current", "integer", "当前页"),
                      ("pageSize", "integer", "每页数量"))]
    context["endpointFields"] = parameters + [field for field in context["endpointFields"] if field["side"] == "response"]
    context["columns"].extend({"name": name, "type": kind, "description": description}
                             for name, kind, description in (
                                 ("name", "varchar(100)", "名称"), ("code", "varchar(32)", "编号"),
                                 ("category_id", "int", "分类 ID"), ("status", "varchar(100)", "商品状态")))
    return context


class EndpointDesignAssistQueryTests(unittest.TestCase):
    """保证给模型的示例能经过真实转换与组合校验，并保持完整分组。"""

    def test_query_uses_scoped_policy_without_other_stage_instructions(self):
        """查询只使用专用规则，映射输入仍保留请求与真实列，不附返回字段。"""
        context = query_context()
        with patch("app.agents.endpoint_design_assist_prompts._POLICY", "GENERIC_POLICY"), patch(
            "app.agents.endpoint_design_assist_prompts._VALUES", "GENERIC_VALUES",
        ):
            system, human = [message.content for message in build_assist_messages(context, "query")]
            self.assertNotIn("GENERIC_POLICY", system)
            self.assertNotIn("GENERIC_VALUES", system)
            write = build_assist_messages(context, "write")[0].content
            self.assertIn("GENERIC_POLICY", write)
            self.assertIn("GENERIC_VALUES", write)
        self.assertLess(len(system), 1800)
        self.assertIn("request:query:keyword", human)
        self.assertIn("request:query:pageSize", human)
        self.assertIn("column:category_id", human)
        self.assertNotIn("response:response_body:price", human)

    def test_prompt_example_passes_real_conversion_and_preserves_filter_groups(self):
        """直接解析生产提示中的示例，验证 OR 关键词、AND 筛选及分页不入条件。"""
        context = query_context()
        system = build_assist_messages(context, "query")[0].content
        example = next(line for line in system.splitlines() if line.startswith('{"query":'))
        result = QueryStepResult.model_validate(json.loads(example))
        converted = step_model_result(result, context)
        self.assertEqual(converted.questions, [])
        mapping = complete_mapping(converted.candidates, context)
        query = mapping["databaseQuery"]
        self.assertEqual(query["join"], "and")
        self.assertEqual(len(query["items"]), 3)
        keyword_group = query["items"][0]
        self.assertEqual(keyword_group["join"], "or")
        self.assertEqual([item["column"] for item in keyword_group["items"]], ["name", "code"])
        self.assertTrue(all(item["operator"] == "contains" for item in keyword_group["items"]))
        leaves = [*keyword_group["items"], *query["items"][1:]]
        self.assertEqual([item["right"]["endpointField"]["path"] for item in leaves],
                         ["keyword", "keyword", "categoryId", "status"])
        self.assertEqual([item["operator"] for item in query["items"][1:]], ["eq", "eq"])
        summary = generated_summary(mapping, [], completed=["query"])
        self.assertIn(" OR ", summary)
        self.assertIn(") AND ", summary)
