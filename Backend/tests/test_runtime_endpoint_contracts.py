"""当前运行 Endpoint 内部编译器的严格负向合同回归，不调用模型或写工作区。"""

from copy import deepcopy
import unittest

from app.domain.runtime_endpoint_contract import AgentExecutionContract
from app.services.runtime_endpoint_contracts import (
    materialize_runtime_endpoints, runtime_endpoint_capability_snapshot, runtime_endpoint_identity,
)


def capability() -> dict:
    """提供显式完整的首批纯文本能力，缺字段时不能自动采用此默认值。"""

    return {"mode": "conversation", "inputSchema": {"type": "object", "properties": {}, "required": [],
            "additionalProperties": False}, "textOutput": True, "publicStateSchema": None,
            "outcomes": [], "interactionKinds": []}


def agent(agent_id: str, page: str) -> dict:
    """提供正式七模块事实和页面归属，测试不借空对象冒充整个 Agent。"""

    return {"agentId": agent_id, "name": "问答助手", "purpose": "解答业务问题", "publicEndpoint": True, "pageIds": [page],
            "agentSettings": {"prompt": {"systemPrompt": "依据当前业务说明回答。"}, "model": {"source": "project"},
                "memory": {}, "context": {}, "tools": {"enabled": False, "bindings": []},
                "skills": {"enabled": False}, "knowledge": {"enabled": False}}, "interactionContract": capability()}


def plan() -> dict:
    """构造没有实体依赖、两个正式 Agent 分别归属两个页面的新合同输入。"""

    return {"topology": {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
            "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"},
            "pages": [{"pageId": "orders"}, {"pageId": "policy"}], "entities": [], "api_contracts": [],
            "agent_contracts": [agent("order_assistant", "orders"), agent("policy_assistant", "policy")]}


def business(contract_id="order_api", endpoint_id="order.list", path="/api/orders", method="GET") -> dict:
    """提供独立业务接口；运行编译不能改写其已有字段映射语义。"""

    return {"id": contract_id, "name": "订单查询", "entity_ids": ["Order"], "schemas": {"Order": {"type": "object"}},
            "base_path": path, "endpoints": [{"id": endpoint_id, "name": "查询订单", "kind": "business",
            "method": method, "path": path, "request_schema_ref": None, "response_schema_ref": "Order"}]}


class RuntimeEndpointContractTests(unittest.TestCase):
    """验证运行入口不再依赖实体，且新合同不会静默修复用户派生字段。"""

    def test_formal_candidate_rebuilds_descriptions_but_not_forged_route(self):
        """正式修订可刷新 Agent 描述；路径篡改不能借重编译被静默洗掉。"""
        candidate = materialize_runtime_endpoints(plan())
        candidate["agent_contracts"][0]["purpose"] = "解释当前订单业务。"
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(candidate)
        revised = materialize_runtime_endpoints(candidate, refresh_derived=True)
        self.assertEqual(revised["api_contracts"][0]["endpoints"][0]["summary"], "解释当前订单业务。")
        revised["api_contracts"][0]["endpoints"][0]["path"] = "/forged"
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(revised, refresh_derived=True)

    def test_entity_free_endpoints_are_deterministic_without_mutating_input(self):
        """纯 Agent 应用无需实体即可物化，重复调用不增生入口或改变原对象。"""

        original = plan()
        before = deepcopy(original)
        compiled = materialize_runtime_endpoints(original)
        self.assertEqual(original, before)
        self.assertEqual(materialize_runtime_endpoints(compiled), compiled)
        self.assertEqual([item["id"] for item in compiled["api_contracts"]],
                         ["agent.order_assistant", "agent.policy_assistant"])
        for contract in compiled["api_contracts"]:
            self.assertEqual(contract["entity_ids"], [])
            self.assertEqual(contract["schemas"], {})
            endpoint = contract["endpoints"][0]
            self.assertEqual(endpoint["kind"], "agent_runtime")
            self.assertEqual(endpoint["path"], "/agents/" + endpoint["agent_id"] + "/run")
            self.assertNotIn("fieldMappings", endpoint)
        compiled["api_contracts"][0]["endpoints"][0]["interactionContract"]["inputSchema"]["properties"]["forged"] = {}
        self.assertEqual(original, before)

    def test_existing_business_contract_is_preserved_and_scoped_identity_is_used(self):
        """跨契约相同 endpointId 不应被取第一项误匹配，普通接口保持原字节语义。"""

        original = plan()
        ordinary = business(endpoint_id="agent.order_assistant.run")
        original["api_contracts"].append(ordinary)
        compiled = materialize_runtime_endpoints(original)
        self.assertEqual(compiled["api_contracts"][0], ordinary)
        selected = runtime_endpoint_capability_snapshot(compiled, "agent.order_assistant", "agent.order_assistant.run")
        self.assertEqual(selected["agentId"], "order_assistant")
        with self.assertRaises(ValueError):
            runtime_endpoint_capability_snapshot(compiled, "order_api", "agent.order_assistant.run")

    def test_tampered_derived_fields_are_rejected_instead_of_overwritten(self):
        """修改 ID、路由、目标或公开能力均阻断编译，不能忽略人工编辑后确认。"""

        for key, value in [("id", "renamed"), ("path", "/agents/other/run"), ("method", "GET"),
                           ("kind", "business"), ("agent_id", "policy_assistant"), ("invocation", {}),
                           ("interactionContract", {}), ("fieldMappings", [])]:
            with self.subTest(key=key):
                candidate = materialize_runtime_endpoints(plan())
                candidate["api_contracts"][0]["endpoints"][0][key] = value
                before = deepcopy(candidate)
                with self.assertRaises(ValueError):
                    materialize_runtime_endpoints(candidate)
                self.assertEqual(candidate, before)

    def test_contract_id_route_and_normalized_file_collisions_are_rejected(self):
        """业务 ID 占用、路由重复及点号/连字符 stem 碰撞不得覆盖既有设计。"""

        cases = [
            [business(contract_id="agent.order_assistant")],
            [business(path="/agents/order_assistant/run", method="POST")],
            [business(path="/agents/{agent_id}/run", method="POST")],
            [business(path="/{remaining:path}", method="POST")],
            [business(contract_id="agent.order_assistant", endpoint_id="other")],
            [business(contract_id="a.b", endpoint_id="c.d"), business(contract_id="a-b", endpoint_id="c-d", path="/api/other")],
        ]
        for contracts in cases:
            with self.subTest(ids=[item["id"] for item in contracts]):
                candidate = plan()
                candidate["api_contracts"] = contracts
                with self.assertRaises(ValueError):
                    materialize_runtime_endpoints(candidate)

    def test_duplicate_and_dangling_runtime_entries_are_rejected(self):
        """重复物化与已删除 Agent 的旧派生入口必须失败，不当成其他 Agent 复用。"""

        duplicate = materialize_runtime_endpoints(plan())
        duplicate["api_contracts"].append(deepcopy(duplicate["api_contracts"][0]))
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(duplicate)
        dangling = materialize_runtime_endpoints(plan())
        dangling["agent_contracts"].pop(0)
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(dangling)

    def test_missing_new_contract_or_kind_has_no_compatibility_fallback(self):
        """旧 Agent 或缺判别字段的业务 Endpoint 不能由编译器猜测当前合同。"""

        candidate = plan()
        del candidate["agent_contracts"][0]["interactionContract"]
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(candidate)
        candidate = plan()
        item = business()
        del item["endpoints"][0]["kind"]
        candidate["api_contracts"] = [item]
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(candidate)

    def test_capability_schema_rejects_expansion_and_implicit_boolean_conversion(self):
        """工具/交互/公开状态/结构化输入不能通过类型转换或额外字段开放。"""

        cases = [("textOutput", False), ("textOutput", 1), ("textOutput", "true"),
                 ("publicStateSchema", {}), ("interactionKinds", ["unknown"]),
                 ("outcomes", [{"id": "success"}]), ("mode", "unknown"), ("tools", [])]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                candidate = capability()
                candidate[key] = value
                with self.assertRaises(ValueError):
                    AgentExecutionContract.model_validate(candidate)
        for key, value in [("properties", []), ("required", "question"), ("additionalProperties", 0)]:
            with self.subTest(input_key=key):
                candidate = capability()
                candidate["inputSchema"][key] = value
                with self.assertRaises(ValueError):
                    AgentExecutionContract.model_validate(candidate)
        for key in capability():
            candidate = capability()
            del candidate[key]
            with self.assertRaises(ValueError):
                AgentExecutionContract.model_validate(candidate)

    def test_enabled_internal_module_preserves_text_conversation_without_downgrade(self):
        """文本会话保留用户 Tool/Skill/Knowledge，不能擅自删除或全局禁止。"""

        for module in ("tools", "skills", "knowledge"):
            with self.subTest(module=module):
                candidate = plan()
                candidate["api_contracts"] = [business()]
                candidate["agent_contracts"][0]["agentSettings"][module] = {
                    "tools": {"enabled": True, "bindings": [{"toolId": "lookup", "apiContractId": "order_api", "endpointId": "order.list"}]},
                    "skills": {"enabled": True, "files": ["agent-runtime/src/app/skills/business/lookup/SKILL.md"]},
                    "knowledge": {"enabled": True, "files": ["agent-runtime/src/app/knowledge/policy.md"]},
                }[module]
                before = deepcopy(candidate)
                compiled = materialize_runtime_endpoints(candidate)
                self.assertEqual(compiled["agent_contracts"][0]["agentSettings"][module], before["agent_contracts"][0]["agentSettings"][module])
                self.assertEqual(candidate, before)

    def test_write_tool_keeps_confirmation_contract(self):
        """移除纯文本禁用后，写入工具仍不能跳过正式确认能力声明。"""
        candidate = plan()
        candidate["api_contracts"] = [business(method="POST")]
        candidate["agent_contracts"][0]["agentSettings"]["tools"] = {"enabled": True,
            "bindings": [{"toolId": "submit", "apiContractId": "order_api", "endpointId": "order.list"}]}
        with self.assertRaisesRegex(ValueError, "执行前确认") as raised:
            materialize_runtime_endpoints(candidate)
        self.assertIn("agent_contracts[0].interactionContract.interactionKinds", str(raised.exception))
        self.assertIn("submit → order_api/order.list", str(raised.exception))
        candidate["agent_contracts"][0]["interactionContract"]["interactionKinds"] = ["confirmation"]
        compiled = materialize_runtime_endpoints(candidate)
        self.assertEqual(compiled["agent_contracts"][0]["agentSettings"]["tools"]["enabled"], True)

    def test_validated_capability_is_deeply_immutable_and_cannot_be_copied_with_expansion(self):
        """字段冻结不能只保护外层属性，嵌套结构与复制更新同样不可绕过校验。"""

        model = AgentExecutionContract.model_validate(capability())
        with self.assertRaises(TypeError):
            model.input_schema["properties"]["question"] = {}
        self.assertIsInstance(model.interaction_kinds, tuple)
        with self.assertRaises(ValueError):
            model.model_copy(update={"interactionKinds": ["unknown"]})
        self.assertEqual(model.model_dump(mode="json", by_alias=True), {**capability(), "resultSchema": None})

    def test_java_topology_and_invalid_agent_identity_cannot_get_runtime_endpoints(self):
        """不凭名称猜测 Runtime，非法 ID 不得拼进公开路由。"""

        candidate = plan()
        candidate["topology"] = {"type": "backend_direct"}
        with self.assertRaises(ValueError):
            materialize_runtime_endpoints(candidate)
        for agent_id in ("runtime", "a/b", "Mixed", "x" * 129):
            with self.subTest(agent_id=agent_id), self.assertRaises(ValueError):
                runtime_endpoint_identity(agent_id)

    def test_snapshot_digest_changes_only_with_current_public_semantics(self):
        """Prompt 调优不冒充公开能力变化，派生路径/身份变化会形成不同能力摘要。"""

        original = plan()
        key = runtime_endpoint_identity("order_assistant")
        first = runtime_endpoint_capability_snapshot(original, *key)
        original["agent_contracts"][0]["agentSettings"]["prompt"]["systemPrompt"] = "更简洁地回答。"
        original["executionState"] = {"runId": "volatile"}
        self.assertEqual(first, runtime_endpoint_capability_snapshot(original, *key))
        other = runtime_endpoint_capability_snapshot(original, *runtime_endpoint_identity("policy_assistant"))
        self.assertNotEqual(first["capabilitySha256"], other["capabilitySha256"])
        first["interactionContract"]["textOutput"] = False
        self.assertTrue(runtime_endpoint_capability_snapshot(original, *key)["interactionContract"]["textOutput"])
