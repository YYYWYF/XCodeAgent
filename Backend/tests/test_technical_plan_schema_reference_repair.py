"""复现真实分页 Schema 漏声明共享引用导致三轮字段重试空转的问题。"""

from copy import deepcopy
import json
import tempfile
from unittest import TestCase
from unittest.mock import patch

from app.agents.main import planner
from app.agents.main.technical_plan_contract_batch_validation import SchemaPlanRepairRequired, validate_schema_fields
from app.agents.main.technical_plan_contract_batch_prompts import schema_fields_prompt
from app.agents.main.technical_plan_contract_budget import contract_part_prefix
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.graph.nodes.planning import _generate_valid_technical_plan
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from tests.test_technical_plan_contract_batches import BatchModel, batch_fixture
from tests.test_technical_plan_modular_generation import modular_fixture


def reference_fixture():
    """订单大契约遗漏已注册引用，客户小契约先完成用于核验无关片段保留。"""

    requirement, catalog, order, page, bad = batch_fixture()
    other_requirement, other_catalog, other_contracts, other_pages = modular_fixture()
    customer = other_contracts[1]
    catalog["entities"].append(other_catalog["entities"][1])
    catalog["contracts"].insert(0, other_catalog["contracts"][1])
    catalog["action_bindings"].extend(binding for binding in other_catalog["action_bindings"] if binding["pageId"] == "customers")
    requirement["confirmed_product_plan"]["pages"].append(other_requirement["confirmed_product_plan"]["pages"][2])
    order["schemas"]["Output"]["properties"]["details"] = {"type": "array", "items": {"$ref": "Detail"}, "description": "订单详情集合。"}
    order["schemas"]["Detail"] = {"type": "object", "properties": {
        "code": {"type": "string", "description": "订单编号。", "entity_field_ref": "Order.code"}}, "required": ["code"]}
    bad["schemas"][0]["fields"].append("details")
    bad["schemas"].append({"name": "Detail", "base": {"type": "object"}, "fields": ["code"], "required": ["code"], "references": []})
    good = deepcopy(bad)
    good["schemas"][0]["references"] = ["Detail"]
    return requirement, catalog, order, page, customer, other_pages[2], bad, good


def routed_model(catalog, order, page, customer, customer_page, plan):
    """只替换两个模块的模型输出，目录、字段、预算和存储使用生产实现。"""

    model = BatchModel(catalog, order, page, plan)

    def invoke(prompt, **kwargs):
        """按实际目标返回客户小契约、客户页面或订单分批响应。"""

        stage = prompt.splitlines()[0]
        if "单个 API Contract" in stage:
            model.prompts.append(prompt)
            return json.dumps({"api_contracts": [customer]})
        if "单页技术引用" in stage and '目标页面：{"pageId": "customers"' in prompt:
            model.prompts.append(prompt)
            return json.dumps({"pages": [customer_page]})
        return model(prompt)

    return model, invoke


class TechnicalPlanSchemaReferenceRepairTests(TestCase):
    """已注册但漏登记的引用重建目录；真正未知的引用仍在原字段阶段拒绝。"""

    def test_missing_registered_reference_atomically_replans_only_own_contract_and_resumes(self):
        """真实落盘撤下错误目录及其字段，重启式续跑保留已完成客户 Contract 和全局目录。"""

        requirement, catalog, order, page, customer, customer_page, bad, good = reference_fixture()
        model, invoke = routed_model(catalog, order, page, customer, customer_page, bad)
        prefix = contract_part_prefix(order["id"])
        with tempfile.TemporaryDirectory() as workspace:
            requirement["technical_plan_generation_workspace"] = workspace
            with self.assertRaisesRegex(SchemaPlanRepairRequired, "Output.*漏声明引用.*Detail"):
                generate_technical_plan_sections(requirement, invoke=invoke)
            store = TechnicalPlanFragmentStore(workspace)
            digest = json.loads(store.path.read_text())["inputSha256"]
            retained = store.load(digest)
            self.assertEqual(set(retained), {"catalog", "contract:customers_api", prefix + "mode"})
            self.assertEqual(retained["contract:customers_api"], customer)
            resumed, invoke = routed_model(catalog, order, page, customer, customer_page, good)
            result = generate_technical_plan_sections(requirement, invoke=invoke)
            self.assertEqual(result["api_contracts"], [customer, order])
            self.assertFalse(any("catalog" in prompt.splitlines()[0] or "单个 API Contract" in prompt.splitlines()[0]
                for prompt in resumed.prompts))
            self.assertEqual(sum("contract_schema_plan" in prompt.splitlines()[0] for prompt in resumed.prompts), 1)
            self.assertEqual(store.load(digest)[prefix + "plan"], good)

    def test_existing_budget_returns_to_schema_plan_instead_of_repeating_fields(self):
        """在原三轮总预算内先重建漏引用目录，下一轮完成同一 Contract。"""

        requirement, catalog, order, page, customer, customer_page, bad, good = reference_fixture()
        model, invoke = routed_model(catalog, order, page, customer, customer_page, bad)
        plans = []

        def repair(prompt, **kwargs):
            """首份目录遗漏引用，收到明确目录修复反馈后才返回正确引用图。"""

            if "contract_schema_plan" in prompt.splitlines()[0]:
                plans.append(prompt)
                model.plan = bad if len(plans) == 1 else good
                if len(plans) > 1:
                    self.assertIn("Output.references", prompt)
                    self.assertIn("Detail", prompt)
            return invoke(prompt)

        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=repair), patch(
                "app.graph.nodes.planning._project_plan_validation_errors", return_value=[]), patch(
                "app.graph.nodes.planning._planning_token_callback"):
            result, errors, _ = _generate_valid_technical_plan({}, requirement, None)
        self.assertIsNotNone(result, errors)
        self.assertEqual(result["api_contracts"], [customer, order])
        self.assertEqual(len(plans), 2)
        self.assertEqual(sum("单个 API Contract" in prompt.splitlines()[0] for prompt in model.prompts), 1)
        for prompt in model.prompts:
            if "单页技术引用" in prompt.splitlines()[0]:
                self.assertNotIn("Output.references", prompt)
                self.assertIn(requirement["planning_adjustment_request"], prompt)

    def test_unregistered_reference_does_not_request_directory_rebuild(self):
        """字段捏造名称属于当前字段错误，不能借此撤下整个冻结目录。"""

        _, catalog, order, _, _, _, bad, _ = reference_fixture()
        with self.assertRaisesRegex(ValueError, "未注册名称.*OtherContractSchema") as caught:
            validate_schema_fields({"properties": {"code_0": {"$ref": "OtherContractSchema"}}},
                schema=bad["schemas"][0], fields=["code_0"], catalog=catalog, spec=catalog["contracts"][1], plan=bad)
        self.assertNotIsInstance(caught.exception, SchemaPlanRepairRequired)

    def test_qualified_ref_is_rejected_and_prompt_supplies_exact_bare_examples(self):
        """OpenAPI 前缀不能作为别名接受；提示给出当前注册名的完整 JSON 引用示例。"""

        requirement, catalog, _, _, _, _, bad, _ = reference_fixture()
        spec, schema = catalog["contracts"][1], bad["schemas"][0]
        prompt = schema_fields_prompt(requirement, catalog, spec, bad, schema, ["code_0"])
        context = next(json.loads(line) for line in reversed(prompt.splitlines()) if line.startswith("{"))
        self.assertEqual(context["schema_ref_examples"], [{"$ref": item["name"]} for item in bad["schemas"]])
        with self.assertRaisesRegex(ValueError, "禁止 #/components/schemas/") as caught:
            validate_schema_fields({"properties": {"code_0": {"$ref": "#/components/schemas/Detail"}}},
                schema=schema, fields=["code_0"], catalog=catalog, spec=spec, plan=bad)
        self.assertNotIsInstance(caught.exception, SchemaPlanRepairRequired)
