"""验证复杂应用中记录操作的身份来源与原失败审阅续修输入。"""

from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
import json

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
from app.agents.main.technical_plan_generation_prompts import API_SCHEMA_RULES, COMMON_RULES, contract_prompt


class ApiRecordIdentityTests(TestCase):
    """通过生成上下文明确身份来源，不按固定路径或响应字段名添加硬门槛。"""

    def test_generation_uses_canonical_json_identity_type_across_modules(self):
        """目录与各 Contract 共用实际实体 JSON 的身份约定，不从 SQL BIGINT 猜测传输类型。"""
        schema = entity_json_schema({"id": "part", "name": "备件", "fields": []})
        self.assertEqual(schema["properties"]["id"]["type"], "string")
        self.assertIn("主键 id 在 API JSON 中统一为 string", COMMON_RULES)
        self.assertIn("关联字段在实体目录中使用 text", COMMON_RULES)
        self.assertIn("type=integer 且 minimum=1", API_SCHEMA_RULES)
        requirement, catalog, _, _ = modular_fixture()
        prompt = contract_prompt(requirement, catalog, catalog["contracts"][0])
        self.assertIn("操作参数的真实来源", prompt)
        self.assertIn("业务编号或上游已提供的身份", prompt)

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
        """缓存存在真实引用错误时只重做该能力，记录身份不由字段名启发式决定。"""

        requirement, catalog, contracts, pages = modular_fixture()
        spec, broken = catalog["contracts"][0], contracts[0]
        meta = {"id": "orders_api.update", "name": "校准订单", "method": "POST", "path": "/api/orders/{id}/sync", "summary": "按选中订单身份校准信息，无其他输入。"}
        spec["endpoints"].append(meta)
        broken["endpoints"].append({**meta, "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "request_schema_ref": None, "response_schema_ref": "Output", "error_codes": [], "authentication": {"required": False}})
        fixed = deepcopy(broken)
        broken["endpoints"][-1]["response_schema_ref"] = "UnknownOutput"
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

    def test_path_id_does_not_trigger_schema_rebuild(self):
        """同路径查询可以提供其他业务数据，不能仅凭 id 参数强制重建 Schema。"""

        requirement,catalog,contract,page,plan = batch_fixture()
        last = catalog["contracts"][0]["endpoints"][-1]
        last.update(name="校准订单",method="POST",path="/api/orders/view_0/{id}/sync",summary="按选中订单身份校准信息，无其他输入。")
        contract["endpoints"][-1].update(last)
        contract["endpoints"][-1]["parameters"] = [{"name":"id","in":"path","required":True,"schema":{"type":"integer"}}]
        model = BatchModel(catalog,contract,page,plan)
        plan_calls = []
        def invoke(prompt, **kwargs):
            """记录 Schema 决策次数，其他阶段使用真实局部校验。"""
            if "contract_schema_plan" in prompt.splitlines()[0]:
                plan_calls.append(prompt)
            return model(prompt)
        with patch.object(planner,"_invoke_prompt_with_chat_model",side_effect=invoke),patch(
            "app.graph.nodes.planning._project_plan_validation_errors",return_value=[]),patch("app.graph.nodes.planning._planning_token_callback"):
            result,errors,_ = _generate_valid_technical_plan({},requirement,None)
        self.assertIsNotNone(result, errors)
        self.assertEqual(len(plan_calls),1)
        self.assertEqual(result["api_contracts"],[contract])
        self.assertEqual(sum("catalog_registry" in prompt.splitlines()[0] for prompt in model.prompts),1)
