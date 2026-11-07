"""验证大 Contract 在同一身份内分批、精确缩批与持久恢复的真实编排行为。"""

from copy import deepcopy
import json
import tempfile
from unittest import TestCase
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.main import planner
from app.agents.main.technical_plan_contract_budget import needs_batches, output_targets, contract_part_prefix
from app.agents.main.technical_plan_contract_batch_validation import validate_schema_plan
from app.agents.main.technical_plan_fragment_output import PlanningOutputTruncated, parse_batch_json
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.agents.main.technical_plan_generation_validation import validate_contract
from app.config import Settings
from app.graph.nodes.planning import _generate_valid_technical_plan
from tests.test_technical_plan_modular_generation import modular_fixture
from tests.technical_plan_catalog_fixture import catalog_response


def batch_fixture(count=13, fields=1):
    """构造一个业务 Contract、共享响应和多个独立查询，产品动作逐个绑定。"""

    requirement, catalog, contracts, pages = modular_fixture()
    spec, contract = catalog["contracts"][0], contracts[0]
    catalog["entities"] = catalog["entities"][:1]
    catalog["contracts"] = [spec]
    requirement["confirmed_product_plan"]["pages"] = requirement["confirmed_product_plan"]["pages"][:1]
    product_page = requirement["confirmed_product_plan"]["pages"][0]
    spec["page_ids"] = [product_page["pageId"]]
    spec["endpoints"] = [{**spec["endpoints"][0], "id": f"orders.read_{index}",
        "path": f"/api/orders/view_{index}", "name": f"查询订单视图{index}"} for index in range(count)]
    contract["endpoints"] = [{**deepcopy(contract["endpoints"][0]), **endpoint} for endpoint in spec["endpoints"]]
    properties = {f"code_{index}": {"type": "string", "description": "订单展示编号。", "entity_field_ref": "Order.code"}
        for index in range(fields)}
    contract["schemas"] = {"Output": {"type": "object", "properties": properties, "required": list(properties)}}
    plan = {"authentication": {"required": False}, "schemas": [{"name": "Output", "base": {"type": "object"},
        "fields": list(properties), "required": list(properties), "references": []}],
        "endpoint_refs": [{"endpointId": endpoint["id"], "request_schema_ref": None,
            "response_schema_ref": "Output"} for endpoint in spec["endpoints"]]}
    product_page["actions"] = [{**deepcopy(product_page["actions"][0]), "actionId": f"read_{index}"} for index in range(count)]
    catalog["action_bindings"] = [{"pageId": product_page["pageId"], "actionId": f"read_{index}",
        "stepId": None, "endpointId": endpoint["id"]} for index, endpoint in enumerate(spec["endpoints"])]
    page = {"pageId": product_page["pageId"], "references": {"endpoint_dependencies": [
        {"endpoint_id": endpoint["id"], "usage": "page_load", "trigger": "读取订单信息。", "required_for_initial_load": True}
        for endpoint in spec["endpoints"]], "action_implementations": [
            {"actionId": f"read_{index}", "endpointId": endpoint["id"]} for index, endpoint in enumerate(spec["endpoints"])]}}
    return requirement, catalog, contract, page, plan


def batch_context(prompt):
    """读取只读批次上下文，不把尾部输出约束误当作 JSON 输入。"""

    return next(json.loads(line) for line in reversed(prompt.splitlines()) if line.startswith("{"))


class BatchModel:
    """受控模型只替换输出，真实运行目录、字段组、接口批次校验和磁盘存储。"""

    def __init__(self, catalog, contract, page, plan):
        """保留标准输出和实际每次提示词，允许调用者注入失败。"""

        self.catalog, self.contract, self.page, self.plan = catalog, contract, page, plan
        self.prompts, self.endpoint_groups, self.field_groups = [], [], []

    def __call__(self, prompt):
        """依据内部阶段返回当前任务对应字段，不调用任何线上服务。"""

        self.prompts.append(prompt)
        stage = prompt.splitlines()[0]
        if "contract_schema_plan" in stage:
            value = self.plan
        elif "contract_schema_fields" in stage:
            context = batch_context(prompt)
            fields = context["fields"]
            self.field_groups.append(fields)
            value = {"properties": {field: self.contract["schemas"][context["schema"]["name"]]["properties"][field] for field in fields}}
        elif "contract_endpoints" in stage:
            context = batch_context(prompt)
            ids = [endpoint["id"] for endpoint in context["endpoints"]]
            self.endpoint_groups.append(ids)
            value = {"endpoints": [deepcopy(endpoint) for endpoint in self.contract["endpoints"] if endpoint["id"] in ids]}
        elif "单个 API Contract" in stage:
            value = {"api_contracts": [self.contract]}
        elif "catalog" in stage:
            value = catalog_response(prompt, self.catalog)
        else:
            value = {"pages": [self.page]}
        return json.dumps(value, ensure_ascii=False)


class TechnicalPlanContractBatchTests(TestCase):
    """验证阈值、完整拼接、严格批次边界及同一三轮预算。"""

    def setUp(self):
        """使用十三个接口触发数量阈值，不依赖供应商 tokenizer。"""

        self.requirement, self.catalog, self.contract, self.page, self.plan = batch_fixture()
        self.model = BatchModel(self.catalog, self.contract, self.page, self.plan)

    def test_threshold_and_small_contract_path(self):
        """十二个轻量接口一次生成，数量、复杂度或低输出预算均可触发分批。"""

        self.assertEqual(output_targets(16384), (6000, 3500))
        self.assertEqual(output_targets(4000), (1600, 1000))
        for count, expected in ((12, False), (13, True)):
            requirement, catalog, contract, page, plan = batch_fixture(count)
            self.assertEqual(needs_batches(catalog, catalog["contracts"][0], 16384), expected)
            model = BatchModel(catalog, contract, page, plan)
            result = generate_technical_plan_sections(requirement, invoke=model)
            self.assertEqual(result["api_contracts"], [contract])
            if count == 12:
                self.assertEqual(len(model.prompts), 6)
        complex_catalog = deepcopy(self.catalog)
        complex_catalog["contracts"][0]["endpoints"] = complex_catalog["contracts"][0]["endpoints"][:1]
        complex_catalog["entities"][0]["fields"] *= 100
        self.assertTrue(needs_batches(complex_catalog, complex_catalog["contracts"][0], 16384))
        self.assertTrue(needs_batches(self.catalog, {**self.catalog["contracts"][0], "endpoints": self.catalog["contracts"][0]["endpoints"][:4]}, 4000))

    def test_large_contract_assembles_once_and_reuses_shared_schema(self):
        """共享响应仅生成一份，接口每批最多六个，最终仍是相同的完整业务契约。"""

        result = generate_technical_plan_sections(self.requirement, invoke=self.model)
        self.assertEqual(result["api_contracts"], [self.contract])
        self.assertEqual(len(self.model.field_groups), 1)
        self.assertEqual(sum(map(len, self.model.endpoint_groups)), 13)
        self.assertTrue(all(len(group) <= 6 for group in self.model.endpoint_groups))
        prompts = [prompt for prompt in self.model.prompts if "contract_endpoints" in prompt.splitlines()[0]]
        self.assertNotIn('"actionId": "read_12"', prompts[0])
        self.assertIn('"actionId": "read_0"', prompts[0])
        self.assertEqual(validate_contract({"api_contracts": [self.contract]}, self.catalog, self.catalog["contracts"][0]), self.contract)
        self.assertFalse(any("SECRET-DO-NOT-PROMPT" in prompt for prompt in self.model.prompts))

    def test_one_large_object_splits_fields_without_duplicate_schema(self):
        """一个七十字段对象分组生成，必填集合与输出字段由代码完整拼回同名 Schema。"""

        requirement, catalog, contract, page, plan = batch_fixture(fields=70)
        model = BatchModel(catalog, contract, page, plan)
        result = generate_technical_plan_sections(requirement, invoke=model)
        self.assertEqual(result["api_contracts"], [contract])
        self.assertGreater(len(model.field_groups), 2)
        self.assertEqual(sum(map(len, model.field_groups)), 70)

    def test_network_failure_reopens_store_and_preserves_completed_parts(self):
        """接口第二批连接失败后无内存重启，Schema 与第一接口批次不会重复请求。"""

        with tempfile.TemporaryDirectory() as workspace:
            requirement = {**self.requirement, "technical_plan_generation_workspace": workspace}
            def disconnect(prompt):
                """第一接口批次成功后在第二批模拟连接失败。"""

                if "contract_endpoints" in prompt.splitlines()[0] and self.model.endpoint_groups:
                    raise ConnectionError("批次连接失败")
                return self.model(prompt)

            with self.assertRaises(ConnectionError):
                generate_technical_plan_sections(requirement, invoke=disconnect)
            resumed = BatchModel(self.catalog, self.contract, self.page, self.plan)
            result = generate_technical_plan_sections(requirement, invoke=resumed)
            self.assertEqual(result["api_contracts"], [self.contract])
            self.assertEqual(resumed.field_groups, [])
            self.assertEqual(len(resumed.endpoint_groups), 2)

    def test_explicit_truncation_splits_only_failed_group_on_next_attempt(self):
        """完成批次保留，截断批次下一轮二分，成功缓存不含不完整响应。"""

        progress = {}
        def truncate_second(prompt):
            """仅第二个接口批次达到明确输出上限。"""

            if "contract_endpoints" in prompt.splitlines()[0] and len(self.model.endpoint_groups) == 1:
                raise PlanningOutputTruncated("length")
            return self.model(prompt)

        with self.assertRaises(PlanningOutputTruncated):
            generate_technical_plan_sections(self.requirement, invoke=truncate_second, fragments=progress)
        self.assertTrue(any(key.endswith(":split") for key in progress["completed"]))
        resumed = BatchModel(self.catalog, self.contract, self.page, self.plan)
        result = generate_technical_plan_sections(self.requirement, invoke=resumed, fragments=progress)
        self.assertEqual(result["api_contracts"], [self.contract])
        self.assertEqual(resumed.field_groups, [])
        self.assertEqual(list(map(len, resumed.endpoint_groups)), [3, 3, 1])

    def test_invalid_json_keeps_same_batch_and_three_attempt_budget(self):
        """普通 JSON 错误不二分、不增加内部重试，连续三次失败即交回原恢复流程。"""

        progress = {}
        def invalid(prompt, **kwargs):
            """只有接口批次返回普通无效 JSON，前置已完成片段照常保存。"""

            return '{"endpoints":[' if "contract_endpoints" in prompt.splitlines()[0] else self.model(prompt)

        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=invalid) as model, patch(
            "app.graph.nodes.planning._planning_token_callback"):
            result, errors, _ = _generate_valid_technical_plan({}, self.requirement, None, generation_fragments=progress)
        self.assertIsNone(result)
        self.assertTrue(errors)
        self.assertEqual(sum("contract_endpoints" in call.args[0].splitlines()[0] for call in model.call_args_list), 3)
        self.assertFalse(any(key.endswith(":split") for key in progress["completed"]))

    def test_small_contract_truncation_switches_to_batches(self):
        """估算偏小时由明确截断触发模式标记，下一次复用目录进入分批。"""

        requirement, catalog, contract, page, plan = batch_fixture(4)
        model, progress = BatchModel(catalog, contract, page, plan), {}
        def truncate_whole(prompt):
            """小契约完整生成阶段模拟供应商 length。"""

            if "单个 API Contract" in prompt.splitlines()[0]:
                raise PlanningOutputTruncated("length")
            return model(prompt)

        with self.assertRaises(PlanningOutputTruncated):
            generate_technical_plan_sections(requirement, invoke=truncate_whole, fragments=progress)
        result = generate_technical_plan_sections(requirement, invoke=model, fragments=progress)
        self.assertEqual(result["api_contracts"], [contract])
        self.assertTrue(model.field_groups)

    def test_schema_directory_rejects_duplicates_unknown_refs_and_incomplete_coverage(self):
        """冻结目录出错时尚未发布缓存，后续重试能重新生成目录。"""

        for kind in ("duplicate", "unknown", "missing", "required"):
            with self.subTest(kind=kind):
                plan = deepcopy(self.plan)
                if kind == "duplicate":
                    plan["schemas"].append(deepcopy(plan["schemas"][0]))
                elif kind == "unknown":
                    plan["schemas"][0]["references"] = ["Other"]
                elif kind == "missing":
                    plan["endpoint_refs"].pop()
                else:
                    plan["schemas"][0]["required"] = ["other"]
                with self.assertRaises(ValueError):
                    validate_schema_plan(plan, self.catalog["contracts"][0], self.catalog)
        with self.assertRaisesRegex(ValueError, "重复键"):
            parse_batch_json('{"properties":{"name":{},"name":{}}}')

    def test_sdk_finish_metadata_is_not_discarded(self):
        """非流式和流式都保留 length/max_tokens 信号，普通 stop 不误触发。"""

        settings = Settings(model_base_url="https://model.test.invalid/v1", model_api_key="test", model_name="test")
        with patch.object(planner, "create_chat_model") as create:
            model = create.return_value
            model.invoke.return_value = AIMessage(content="partial", response_metadata={"finish_reason": "length"})
            with self.assertRaises(PlanningOutputTruncated):
                planner._invoke_prompt_with_chat_model("test", settings=settings)
            model.stream.return_value = iter([AIMessageChunk(content="partial"),
                AIMessageChunk(content="", response_metadata={"stop_reason": "max_tokens"})])
            with self.assertRaises(PlanningOutputTruncated):
                planner._invoke_prompt_with_chat_model("test", settings=settings, on_token=lambda token: None)
            model.invoke.return_value = AIMessage(content="done", response_metadata={"finish_reason": "stop"})
            self.assertEqual(planner._invoke_prompt_with_chat_model("test", settings=settings), "done")
