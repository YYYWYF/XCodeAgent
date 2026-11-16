"""复现实体数值规则串层和普通刷新被虚构为组合步骤的问题。"""

from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import patch

from app.agents.main import planner
from app.agents.main.technical_plan_catalog_parts_prompts import bindings_prompt, entity_prompt
from app.agents.main.technical_plan_catalog_parts_validation import (
    catalog_registry, validate_entity_fields, validate_page_bindings,
)
from app.agents.main.technical_plan_contract_batch_prompts import schema_plan_prompt, schema_fields_prompt
from app.agents.main.technical_plan_generation_prompts import API_SCHEMA_RULES, contract_prompt, page_prompt
from app.agents.main.technical_plan_generation_validation import validate_page
from app.graph.nodes.planning import _generate_valid_technical_plan
from tests.technical_plan_catalog_fixture import catalog_responses
from tests.test_technical_plan_modular_generation import modular_fixture


class TechnicalPlanPromptBoundaryTests(TestCase):
    """实际校验片段和三轮修复，只替换外部模型响应。"""

    def setUp(self):
        """准备一个普通业务刷新和一个已确认组合动作，保留原始产品快照。"""

        self.requirement, self.catalog, self.contracts, self.pages = modular_fixture()
        self.page = self.requirement["confirmed_product_plan"]["pages"][1]
        self.page["actions"][0]["behavior"]["expectedResult"] = "显示最新订单信息和关联客户信息。"
        self.original = deepcopy(self.requirement)

    def test_integer_rules_only_apply_to_api_schema_stages(self):
        """整数实体仍使用 number，完整与分批 API Schema 仍执行整数及最小值规则。"""

        registry = catalog_registry(self.catalog)
        prompt = entity_prompt(self.requirement, registry, registry["entities"][0])
        self.assertNotIn(API_SCHEMA_RULES, prompt)
        self.assertIn("整数使用 type=number", prompt)
        field = {"name": "quantity", "label": "数量", "description": "必须为正整数。", "type": "number", "required": True}
        self.assertEqual(validate_entity_fields({"fields": [field]}, registry["entities"][0])["fields"], [field])
        with self.assertRaises(ValueError):
            validate_entity_fields({"fields": [{**field, "type": "integer", "minimum": 1}]}, registry["entities"][0])
        spec = self.catalog["contracts"][0]
        for api_prompt in (contract_prompt(self.requirement, self.catalog, spec),
                schema_plan_prompt(self.requirement, self.catalog, spec),
                schema_fields_prompt(self.requirement, self.catalog, spec, {"schemas": []}, {"name": "Input"}, ["quantity"])):
            self.assertIn(API_SCHEMA_RULES, api_prompt)

    def test_binding_prompt_exposes_only_confirmed_business_slots(self):
        """普通动作只允许 null，组合动作只允许真实 business 步骤，界面步骤不能绑定接口。"""

        for page, step_id in ((self.page, None), (self.requirement["confirmed_product_plan"]["pages"][2], "load")):
            prompt = bindings_prompt(self.requirement, page, self.catalog)
            context = json.loads(prompt.splitlines()[-1])
            self.assertEqual(context["required_slots"], [{"pageId": page["pageId"], "actionId": "refresh", "stepId": step_id}])
        self.assertEqual(self.requirement, self.original)

    def test_invented_step_has_actionable_feedback_without_invalidating_catalog(self):
        """错误步骤仍严格拒绝，并指出完整结果和合法映射，不能默默变更产品动作。"""

        value = {"action_bindings": [{"pageId": self.page["pageId"], "actionId": "refresh",
            "stepId": "load_related_orders", "endpointId": "orders_api.read"}]}
        before = deepcopy(self.catalog)
        with self.assertRaisesRegex(ValueError, "合法 stepId 只能为 null") as caught:
            validate_page_bindings(value, page=self.page, catalog=self.catalog)
        self.assertIn("显示最新订单信息和关联客户信息", str(caught.exception))
        self.assertNotIn("catalog_issue", str(caught.exception))
        self.assertEqual(self.catalog, before)
        self.assertEqual(self.requirement, self.original)

    def test_page_prompt_explicitly_excludes_navigation_from_business_slots(self):
        """页面生成读取完整产品动作，但技术实现清单只允许已确认业务动作。"""

        page = deepcopy(self.page)
        page["actions"].append({"actionId": "open_customer", "name": "查看客户", "description": "跳转客户详情。",
            "requiresConfirmation": False, "behavior": {"type": "navigation", "targetPageId": "customers", "expectedResult": "查看客户。"}})
        prompt = page_prompt(self.requirement, self.catalog, page, self.contracts)
        line = next(line for line in prompt.splitlines() if line.startswith("合法业务槽位"))
        slots = json.loads(line.split("：", 1)[1])
        self.assertEqual(slots, [{"pageId": page["pageId"], "actionId": "refresh", "stepId": None}])
        self.assertIn("即使跳转目标有读取接口也不能把导航动作", prompt)
        mapping_line = next(line for line in prompt.splitlines() if line.startswith("本页目录动作映射"))
        self.assertEqual(json.loads(mapping_line.split("：", 1)[1]),
            [binding for binding in self.catalog["action_bindings"] if binding["pageId"] == page["pageId"]])
        self.assertNotIn("catalog_issue", prompt)

    def test_refresh_reuses_both_reads_without_inventing_product_steps(self):
        """真实六页案例的刷新通过主体绑定及两个读取依赖完整表达，不新增接口或业务步骤。"""

        page = deepcopy(self.pages[1])
        page["references"]["endpoint_dependencies"][0]["trigger"] = "初始加载及刷新时读取订单。"
        page["references"]["endpoint_dependencies"].append({"endpoint_id": "customers_api.read",
            "usage": "page_load", "trigger": "初始加载及刷新时读取关联客户。", "required_for_initial_load": True})
        result = validate_page({"pages": [page]}, self.page, self.contracts)
        self.assertEqual(result, page)
        self.assertEqual(result["references"]["action_implementations"], [{"actionId": "refresh", "endpointId": "orders_api.read"}])
        self.assertEqual(self.requirement, self.original)

    def test_invalid_step_retries_only_mapping_within_fragment_budget(self):
        """虚构步骤在当前映射片段修正，接口目录和其他页面不因模型审查重做。"""

        initial = catalog_responses(self.catalog)
        bad = deepcopy(initial[-2])
        bad["action_bindings"].append({**bad["action_bindings"][0], "stepId": "load_related_orders"})
        responses = [*initial[:-2], bad, *initial[-2:],
            *[{"api_contracts": [contract]} for contract in self.contracts],
            *[{"pages": [page]} for page in self.pages]]
        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=[json.dumps(value) for value in responses]) as model, patch(
            "app.graph.nodes.planning._project_plan_validation_errors", return_value=[]), patch(
            "app.graph.nodes.planning._planning_token_callback"):
            plan, errors, _ = _generate_valid_technical_plan({}, self.requirement, None)
        self.assertIsNotNone(plan, errors)
        self.assertEqual(plan["api_contracts"], self.contracts)
        self.assertEqual(self.requirement, self.original)
        self.assertEqual(model.call_count, len(responses))
        prompts = [call.args[0] for call in model.call_args_list]
        self.assertTrue(all("catalog_bindings" in p.splitlines()[0] for p in prompts[len(initial) - 2:len(initial)]))
        self.assertFalse(any("catalog_issue" in p for p in prompts))
        entity_calls = [json.loads(p.splitlines()[-1])["entity"]["id"]
            for p in prompts if "catalog_entity" in p.splitlines()[0]]
        self.assertEqual(entity_calls.count("Customer"), 1)
