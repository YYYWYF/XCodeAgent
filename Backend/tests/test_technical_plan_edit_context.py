"""验证已定位原文足够直接编辑，且不泄露无关分组或扩大修改范围。"""

from copy import deepcopy
import json
import re
from unittest import TestCase
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.main.technical_plan_edit_context import (
    MAX_CONTEXT_CHARACTERS, MAX_WINDOW_LINES, technical_plan_edit_context,
)
from app.agents.main.technical_plan_editor import (
    TECHNICAL_PLAN_CANDIDATE_PATH, TECHNICAL_PLAN_EDIT_SECTIONS, edit_technical_plan_with_agent,
)
from app.config import Settings
from tests.test_technical_plan_fragment_editor import ScriptedEditorModel, candidate_plan, tool_response


def source_text(plan: dict) -> str:
    """使用与内存候选一致的格式生成测试源文件。"""
    return json.dumps({key: plan[key] for key in TECHNICAL_PLAN_EDIT_SECTIONS}, ensure_ascii=False, indent=2)


def scope() -> dict:
    """沿用真实 Graph 传给编辑器的 Contract、方法和路径选择器。"""
    return {"endpoints": [{"contract_id": "orders_api", "method": "GET", "path": "/api/orders"}], "schemas": {}}


class PreloadedEditorModel(ScriptedEditorModel):
    """直接复制首次预载的接口窗口进行真实文件编辑，随后读回。"""

    def _generate(self, messages, **kwargs):
        """不搜索、不预读，使用实际收到的原文保留唯一匹配所需缩进。"""
        self.seen_messages.append(deepcopy(messages))
        if len(self.seen_messages) == 1:
            initial = messages[-1].content
            snippet = initial.split("\n```\n", 1)[1].split("\n```", 1)[0]
            message = tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                                    old_string=snippet, new_string=snippet.replace('"default": 20', '"default": 8'))
        elif len(self.seen_messages) == 2:
            message = tool_response("read_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH, offset=0, limit=120)
        else:
            message = AIMessage(content="分页默认值已修改，等待正式校验和用户确认。")
        return ChatResult(generations=[ChatGeneration(message=message)])


class TechnicalPlanEditContextTests(TestCase):
    """目标、原文、共享引用和大小边界使用真实候选进行检查。"""

    def test_preloads_exact_endpoint_and_transitive_schemas_with_shared_consumers(self):
        """GET/POST 同路径仍只预载目标接口，并展开循环类型、提示共享使用者。"""
        plan = candidate_plan()
        contract = plan["api_contracts"][0]
        contract["endpoints"][0]["response_schema_ref"] = "OrderListOutput"
        contract["endpoints"].append({"id": "orders.create", "method": "POST", "path": "/api/orders",
                                      "request_schema_ref": "Order", "summary": "UNSELECTED_ENDPOINT_BODY"})
        contract["schemas"] = {
            "OrderListOutput": {"type": "array", "items": {"$ref": "#/components/schemas/Order"}},
            "Order": {"type": "object", "properties": {"address": {"$ref": "Address"}}},
            "Address": {"type": "object", "properties": {"order": {"$ref": "Order"}}},
            "Unused": {"description": "UNRELATED_SCHEMA"},
        }
        plan["api_contracts"].append({"id": "other", "schemas": {"Order": {"description": "WRONG_CONTRACT"}}, "endpoints": []})
        source = source_text(plan)
        context = technical_plan_edit_context(scope(), source)
        self.assertIn("Endpoint=orders.list", context)
        self.assertNotIn("Endpoint=orders.create", context)
        self.assertNotIn("UNSELECTED_ENDPOINT_BODY", context)
        self.assertNotIn("UNRELATED_SCHEMA", context)
        self.assertNotIn("WRONG_CONTRACT", context)
        self.assertIn("引用接口=orders.list,orders.create", context)
        for name in ("OrderListOutput", "Order", "Address"):
            self.assertEqual(context.count(f"Schema={name};"), 1)
        for match in re.finditer(r"offset=(\d+); returned_lines=(\d+)\n```\n(.*?)\n```", context, re.S):
            offset, count, snippet = match.groups()
            position = sum(len(line) + 1 for line in source.splitlines()[:int(offset)])
            self.assertTrue(source.startswith(snippet, position))
            self.assertEqual(len(snippet.splitlines()), int(count))
        self.assertEqual(len(re.findall(r"returned_lines=", context)), 4)

    def test_explicit_schema_without_endpoint_and_updated_candidate(self):
        """直接点名类型时也预载当前修复候选，不回灌旧值。"""
        plan = candidate_plan()
        plan["api_contracts"][0]["schemas"] = {"Order": {"description": "本轮已修改的值"}}
        context = technical_plan_edit_context({"endpoints": [], "schemas": {"orders_api": ["Order"]}}, source_text(plan))
        self.assertIn("本轮已修改的值", context)
        self.assertNotIn("Endpoint=", context)
        self.assertEqual(technical_plan_edit_context(None, source_text(plan)), "")
        self.assertEqual(technical_plan_edit_context({"endpoints": [], "schemas": {}}, source_text(plan)), "")

    def test_path_revision_uses_current_candidate_and_ambiguous_target_is_not_preloaded(self):
        """失败修复可定位已改路径；目标不唯一时不擅自预载某一个接口。"""
        plan = candidate_plan()
        selection = scope()
        selection["endpoints"][0]["new_path"] = "/api/new-orders"
        plan["api_contracts"][0]["endpoints"][0]["path"] = "/api/new-orders"
        self.assertIn("Endpoint=orders.list", technical_plan_edit_context(selection, source_text(plan)))
        plan["api_contracts"][0]["endpoints"].append(deepcopy(plan["api_contracts"][0]["endpoints"][0]))
        self.assertEqual(technical_plan_edit_context(selection, source_text(plan)), "")

    def test_large_schemas_are_bounded_without_disabling_further_reads(self):
        """超大 Schema 只投射完整原文行，明确提示按需补读。"""
        plan = candidate_plan()
        contract = plan["api_contracts"][0]
        contract["endpoints"][0]["response_schema_ref"] = "Huge"
        contract["schemas"] = {"Huge": {"properties": {f"field{i}": {"description": "业务说明" * 100} for i in range(1000)}}}
        context = technical_plan_edit_context(scope(), source_text(plan))
        self.assertLessEqual(len(context), MAX_CONTEXT_CHARACTERS)
        self.assertIn("该窗口未完整展示", context)
        self.assertTrue(all(int(count) <= MAX_WINDOW_LINES for count in re.findall(r"returned_lines=(\d+)", context)))

    def test_editor_can_edit_from_preload_then_read_back_without_initial_search(self):
        """真实 Agent 首轮直接编辑，保留最终读回与候选隔离，不引入自动确认。"""
        plan = candidate_plan()
        # 两个接口具有同一个短键值，必须复制带目标 ID 的原文窗口才能唯一匹配。
        plan["api_contracts"][0]["endpoints"][1]["parameters"][0]["schema"]["default"] = 20
        original = deepcopy(plan)
        model = PreloadedEditorModel(responses=[])
        with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model):
            result = edit_technical_plan_with_agent({
                "confirmed_product_plan": {"pages": []}, "planning_adjustment_request": "订单列表分页默认值改成8。",
                "technical_plan_revision_scope": scope(),
            }, plan, settings=Settings(model_base_url="http://unused", model_api_key="unused", model_name="test"))
        self.assertEqual(result["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"], 8)
        self.assertEqual(result["api_contracts"][0]["endpoints"][1], original["api_contracts"][0]["endpoints"][1])
        self.assertEqual(plan, original)
        self.assertEqual(len(model.seen_messages), 3)
        self.assertNotIn("confirmation_status", result)
        self.assertIn("可直接 edit_file", model.seen_messages[0][-1].content)
