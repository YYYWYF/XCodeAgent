"""验证复杂应用中记录操作的身份来源与原失败审阅续修输入。"""

from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
import json

from app.services.api_record_identity import validate_record_identity_sources
from app.graph.application_planning_interrupts import application_planning_review_payload, prepare_application_planning_review_resume
from app.agents.main.technical_plan_contract_batches import generate_contract
from app.agents.main.technical_plan_contract_budget import contract_part_prefix
from app.agents.main import planner
from app.graph.nodes.planning import _generate_valid_technical_plan
from tests.test_technical_plan_modular_generation import modular_fixture
from tests.test_technical_plan_contract_batches import BatchModel, batch_fixture
from app.domain.application_planning_recovery import application_planning_sha256
from app.services.project_plan import _attach_technical_entity_field_refs, create_technical_plan
from app.services.requirement_spec import create_requirement_spec
from app.services.product_plan import create_product_plan
from app.services.entity_definitions import entity_json_schema
from app.agents.main.technical_plan_generation_prompts import COMMON_RULES


def identity_contract():
    """构造有列表读取和路径 id 更新的同一业务集合。"""
    return {"id": "items", "schemas": {"Item": {"type": "object", "properties": {"name": {"type": "string"}}},
        "Page": {"type": "object", "properties": {"list": {"type": "array", "items": {"$ref": "Item"}}}}},
        "endpoints": [{"id": "read", "method": "GET", "path": "/api/items", "response_schema_ref": "Page"},
            {"id": "update", "method": "PUT", "path": "/api/items/{id}"}]}


class ApiRecordIdentityTests(TestCase):
    """读取必须给出操作身份，纯展示及明确业务键不强制引入 id。"""

    def test_generation_uses_canonical_json_identity_type_across_modules(self):
        """目录与各 Contract 共用实际实体 JSON 的身份约定，不从 SQL BIGINT 猜测传输类型。"""
        schema = entity_json_schema({"id": "part", "name": "备件", "fields": []})
        self.assertEqual(schema["properties"]["id"]["type"], "string")
        self.assertIn("主键 id 在 API JSON 中统一为 string", COMMON_RULES)
        self.assertIn("关联字段在实体目录中使用 text", COMMON_RULES)
        self.assertIn("type=integer 且 minimum=1", COMMON_RULES)

    def test_formal_materialization_preserves_model_field_ownership(self):
        """最终拼接不得为模型的选项 DTO 或计算字段推测实体来源。"""
        spec = create_requirement_spec("创建库存管理系统")
        spec["confirmed_product_plan"] = create_product_plan(spec)
        entity = {"id": "purchase", "name": "采购", "description": "采购申请。", "fields": [
            {"name": "total", "label": "总额", "type": "number", "required": True, "description": "申请总额。"}]}
        contract = {"id": "purchase_api", "name": "采购接口", "entity_ids": ["purchase"], "base_path": "/api/purchase",
            "authentication": {"required": False}, "schemas": {"Option": {"type": "object", "properties": {
                "id": {"type": "integer"}, "total": {"type": "number"},
                "explicit": {"type": "integer", "entity_field_ref": "purchase.id"}}}}, "endpoints": []}
        plan = create_technical_plan(spec, agent_plan={"entities": [entity], "api_contracts": [contract]})
        self.assertEqual(plan["api_contracts"][0]["schemas"]["Option"]["properties"], contract["schemas"]["Option"]["properties"])

    def test_collection_read_requires_record_identity_for_id_mutations(self):
        """复现库存列表只有备件编号、更新却需要内部 id 的真实断链。"""
        contract = identity_contract()
        self.assertIn("未返回记录 id", validate_record_identity_sources(contract)[0])
        contract["schemas"]["Item"]["properties"]["id"] = {"type": "integer"}
        self.assertEqual(validate_record_identity_sources(contract), [])

    def test_platform_never_guesses_shared_field_owner_from_entity_order(self):
        """复现最终物化错绑备件 id：只有唯一来源可推断，同名 id/状态与显式引用不能被改写。"""
        entities=[{"id":"part","fields":[{"name":"part_number"},{"name":"status"}]},
            {"id":"movement","fields":[{"name":"movement_number"},{"name":"status"}]}]
        contract={"id":"inventory","entity_ids":["part","movement"],"schemas":{"Part":{"type":"object","properties":{
            "id":{"type":"integer"},"status":{"type":"string"},"part_number":{"type":"string"},
            "explicit_id":{"type":"integer","entity_field_ref":"part.id"}}}}}
        forward=_attach_technical_entity_field_refs([contract],entities)
        contract["entity_ids"].reverse()
        reverse=_attach_technical_entity_field_refs([contract],list(reversed(entities)))
        self.assertEqual(forward[0]["schemas"],reverse[0]["schemas"])
        properties=forward[0]["schemas"]["Part"]["properties"]
        self.assertNotIn("entity_field_ref",properties["id"])
        self.assertNotIn("entity_field_ref",properties["status"])
        self.assertEqual(properties["part_number"]["entity_field_ref"],"part.part_number")
        self.assertEqual(properties["explicit_id"]["entity_field_ref"],"part.id")

    def test_entity_identity_alias_and_business_keys_are_supported(self):
        """允许响应属性另名但明确映射实体 id；按业务编号操作时不虚构内部 id。"""
        contract = identity_contract()
        contract["schemas"]["Item"]["properties"]["recordId"] = {"type": "integer", "entity_field_ref": "Item.id"}
        self.assertEqual(validate_record_identity_sources(contract), [])
        contract = identity_contract()
        contract["endpoints"][1]["path"] = "/api/items/{code}"
        self.assertEqual(validate_record_identity_sources(contract), [])
        contract["endpoints"] = contract["endpoints"][:1]
        self.assertEqual(validate_record_identity_sources(contract), [])

    def test_cyclic_schema_does_not_hang_and_composed_identity_is_found(self):
        """引用闭环能终止，组合 Schema 中的真实身份仍可识别。"""
        contract = identity_contract()
        contract["schemas"]["Item"] = {"$ref": "Item"}
        self.assertTrue(validate_record_identity_sources(contract))
        contract["schemas"]["Item"] = {"allOf": [{"type": "object", "properties": {"id": {"type": "integer"}}}]}
        self.assertEqual(validate_record_identity_sources(contract), [])

    def test_builtin_retry_preserves_original_input_but_explicit_revision_changes_it(self):
        """重新生成按钮只授权失败候选续修；真实业务修改仍改变输入，沿同一原生审阅门。"""
        state = {"request": "原始业务要求：禁止重复扣减库存。", "active_run_id": "source-run", "technical_plan": {},
            "clarification": {"mode": "technical_plan_generation_error", "status": "requires_user_input", "errors": ["缺少记录身份。"]}}
        payload = application_planning_review_payload(state, "technical_planning")
        request = "请重新生成技术规划，并修复全部结构和契约错误。"
        submission = {"gateId": payload["gateId"], "artifact": payload["artifact"], "artifactRevision": payload["artifactRevision"],
            "action": "revise", "request": request, "answers": {"planning_recovery": request}}
        with patch("app.graph.application_planning_interrupts.interrupt", return_value=submission):
            decision = prepare_application_planning_review_resume(state, "technical_planning")
        self.assertEqual(decision.update["request"], state["request"])
        self.assertEqual(decision.update["application_planning_interaction"]["request"], state["request"])
        self.assertEqual(decision.update["application_planning_recovery_boundary"]["requestSha256"], application_planning_sha256(state["request"]))
        self.assertEqual(decision.target_node, "technical_planning_begin")
        self.assertEqual(decision.update["technical_plan_repair_source_run_id"], "source-run")
        changed = deepcopy(submission)
        changed.update(request="新增一个取消原因字段。", answers={"technical_plan_confirmation": "新增一个取消原因字段。"})
        with patch("app.graph.application_planning_interrupts.interrupt", return_value=changed):
            revision = prepare_application_planning_review_resume(state, "technical_planning")
        self.assertEqual(revision.update["request"], changed["request"])

    def test_invalid_cached_contract_is_regenerated_once_without_discarding_neighbors(self):
        """旧候选未通过当前校验时视为缓存未命中，只重做该业务能力，不空耗模型重试预算。"""

        requirement, catalog, contracts, pages = modular_fixture()
        spec, broken = catalog["contracts"][0], contracts[0]
        meta = {"id": "orders_api.update", "name": "校准订单", "method": "POST", "path": "/api/orders/{id}/sync", "summary": "按选中订单身份校准信息，无其他输入。"}
        spec["endpoints"].append(meta)
        broken["endpoints"].append({**meta, "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "request_schema_ref": None, "response_schema_ref": "Output", "error_codes": [], "authentication": {"required": False}})
        fixed = deepcopy(broken)
        fixed["schemas"]["Output"]["properties"]["id"] = {"type": "integer", "description": "订单记录身份。", "entity_field_ref": "Order.id"}
        prefix = contract_part_prefix(spec["id"])
        cache = {"contract:orders_api": broken, "contract:customers_api": contracts[1], prefix + "plan": {"invalid": True}}
        calls = []
        def publish(value):
            """模拟同一次原子发布后观察到的内存索引。"""
            cache.clear()
            cache.update(value)
        def fragment(key, prompt, validate):
            """只允许一次完整 Contract 请求，断言错误缓存已先失效。"""
            self.assertNotIn(prefix + "plan", cache)
            self.assertNotIn("contract:orders_api", cache)
            calls.append(key)
            value = validate({"api_contracts": [fixed]})
            cache[key] = value
            return value
        result = generate_contract(requirement,catalog,spec,cache=cache,fragment=fragment,publish=publish,max_output_tokens=16384)
        self.assertEqual(result, fixed)
        self.assertEqual(calls, ["contract:orders_api"])
        self.assertEqual(cache["contract:customers_api"], contracts[1])

    def test_frozen_schema_missing_identity_retries_schema_plan_not_endpoint_batch(self):
        """Schema 缺少身份时应回到其拥有者，接口批次不能反复重试一个自己无权修改的定义。"""

        requirement,catalog,contract,page,plan = batch_fixture()
        last = catalog["contracts"][0]["endpoints"][-1]
        last.update(name="校准订单",method="POST",path="/api/orders/view_0/{id}/sync",summary="按选中订单身份校准信息，无其他输入。")
        contract["endpoints"][-1].update(last)
        contract["endpoints"][-1]["parameters"] = [{"name":"id","in":"path","required":True,"schema":{"type":"integer"}}]
        model = BatchModel(catalog,contract,page,plan)
        plan_calls = []
        def invoke(prompt, **kwargs):
            """第二次 Schema 决策按明确缺口补上 id，其他阶段使用真实局部校验。"""
            if "contract_schema_plan" in prompt.splitlines()[0]:
                plan_calls.append(prompt)
                if len(plan_calls)==2:
                    self.assertIn("未返回记录 id", prompt)
                    plan["schemas"][0]["fields"].append("id")
                    plan["schemas"][0]["required"].append("id")
                    contract["schemas"]["Output"]["properties"]["id"]={"type":"integer","description":"订单记录身份。","entity_field_ref":"Order.id"}
                    contract["schemas"]["Output"]["required"].append("id")
            return model(prompt)
        with patch.object(planner,"_invoke_prompt_with_chat_model",side_effect=invoke),patch(
            "app.graph.nodes.planning._project_plan_validation_errors",return_value=[]),patch("app.graph.nodes.planning._planning_token_callback"):
            result,errors,_ = _generate_valid_technical_plan({},requirement,None)
        self.assertIsNotNone(result, errors)
        self.assertEqual(len(plan_calls),2)
        self.assertEqual(result["api_contracts"],[contract])
        self.assertEqual(sum("catalog_registry" in prompt.splitlines()[0] for prompt in model.prompts),1)
