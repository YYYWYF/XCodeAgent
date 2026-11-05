"""内部/公开 Agent 与业务调用的冻结输入回归，不执行模型或写入应用。"""

import unittest

from app.services.frozen_contract_catalog import ContractCatalogBindingError
from app.services.frozen_contract_manifest import compile_expected_unit_formal_source_refs
from app.services.frozen_contract_store import FrozenContractStore
from app.services.execution_resource_scope import resolve_execution_resource_claims, resource_claim_keys
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from app.services.runtime_endpoint_dependencies import resolve_runtime_target
from app.services.unit_generation_requirement_targets import scoped_formal_targets
from tests.test_agent_capability_refactor import detection_plan
from tests.test_runtime_endpoint_contracts import business


def frozen_store(plan):
    """冻结当前正式定义及对应接口设计，让测试覆盖真实 manifest 编译入口。"""
    return FrozenContractStore.create(planning_run_id="agent-manifest", formal_inputs={
        "product_plan": {"content": {"name": "检测应用"}, "source": {"artifact": "product-plan"}},
        "technical_plan": {"content": plan, "source": {"artifact": "technical-plan"}},
        "page_contracts": [], "authorization_slices": [],
        "api_contracts": [{"content": api, "source": {"artifact": "technical-plan", "id": api["id"]}}
                          for api in plan["api_contracts"]],
        "endpoint_api_designs": [{"content": {"apiContractId": api["id"], "endpointId": endpoint["id"]},
                                  "source": {"artifact": "api-design", "api": api["id"], "endpoint": endpoint["id"]}}
                                 for api in plan["api_contracts"] for endpoint in api["endpoints"]],
    })


def compile_agent(plan, keys=None):
    """以依赖解析器输出的真实 Scope 编译内部能力职责的正式来源。"""
    return compile_expected_unit_formal_source_refs(
        unit_id="agent:api_scan", unit_kind="agent",
        generation_requirements=[{"requirement_id": "agent.api_scan.tools", "source_refs": {
            "kind": "agent.runtime", "agent_id": "api_scan"}}],
        scoped_endpoint_keys=resolve_runtime_target(plan, "agent", "api_scan")["endpoint_keys"] if keys is None else keys,
        frozen_contract_store=frozen_store(plan),
    )


class AgentFrozenManifestTests(unittest.TestCase):
    """检查依赖解析、正式 Scope 与冻结清单遵循同一份当前合同。"""

    def test_internal_agent_without_tools_needs_no_endpoint(self):
        """后台能力无需虚构公开 SSE 入口，也不会反向冻结调用它的业务接口。"""
        refs = compile_agent(detection_plan())
        self.assertEqual({ref.kind for ref in refs}, {"technical_plan"})
        self.assertIn("/agent_contracts/0", refs[0].selectors)

    def test_public_agent_freezes_explicit_entry(self):
        """公开能力仍冻结自身运行接口及接入设计，不放松真正依赖的校验。"""
        plan = detection_plan()
        plan["agent_contracts"][0]["publicEndpoint"] = True
        plan = materialize_runtime_endpoints(plan)
        refs = compile_agent(plan)
        self.assertEqual({ref.kind for ref in refs}, {"technical_plan", "api_contract", "endpoint_api_design"})
        with self.assertRaisesRegex(ContractCatalogBindingError, "公开运行入口 agent.api_scan/agent.api_scan.run"):
            compile_agent(plan, [])

    def test_internal_agent_freezes_only_tools(self):
        """内部能力带工具时只冻结精确工具接口，遗漏时明确指出接口身份。"""
        plan = detection_plan()
        plan["api_contracts"].append(business("data_api", "data.read", "/api/data"))
        plan["agent_contracts"][0]["agentSettings"]["tools"] = {"enabled": True, "bindings": [
            {"toolId": "read_data", "apiContractId": "data_api", "endpointId": "data.read"}]}
        refs = compile_agent(plan)
        api_refs = [ref for ref in refs if ref.kind == "api_contract"]
        self.assertEqual(len(api_refs), 1)
        self.assertEqual(api_refs[0].source["id"], "data_api")
        with self.assertRaisesRegex(ContractCatalogBindingError, "Tool 业务依赖 data_api/data.read"):
            compile_agent(plan, [])

    def test_business_service_can_read_only_called_agent(self):
        """非流式接口生成可读取其目标 Agent 合同，不泄露无关能力定义。"""
        plan = detection_plan()
        refs = compile_expected_unit_formal_source_refs(
            unit_id="backend:endpoint:order_api:order.list", unit_kind="backend",
            generation_requirements=[{"requirement_id": "service", "source_refs": {
                "kind": "backend.application_service", "api_contract_id": "order_api", "endpoint_id": "order.list"}}],
            scoped_endpoint_keys=[("order_api", "order.list")], frozen_contract_store=frozen_store(plan))
        technical = next(ref for ref in refs if ref.kind == "technical_plan")
        self.assertIn("/agent_contracts/0", technical.selectors)
        self.assertNotIn("/agent_contracts/1", technical.selectors)

    def test_scopes_share_the_same_dependency_closure(self):
        """页面、接口、Agent 和应用范围都使用统一闭包，页面消费业务接口可带入内部能力。"""
        plan = detection_plan()
        plan["confirmation_status"] = "confirmed"
        plan["page_implementation_contracts"] = [{"pageId": "orders", "requiredEndpointIds": ["order.list"]}]
        for scope in ({"type": "agent", "targetId": "api_scan"},
                      {"type": "endpoint", "targetId": "order.list", "apiContractId": "order_api"},
                      {"type": "page", "targetId": "orders"},
                      {"type": "application", "targetId": "application"}):
            with self.subTest(scope=scope):
                _, endpoints = scoped_formal_targets(plan, scope)
                resolved = resolve_runtime_target(plan, scope["type"], scope["targetId"], scope.get("apiContractId"))
                self.assertEqual(set(endpoints), set(resolved["endpoint_keys"]))
                self.assertIn("api_scan", resolved["agent_ids"])

    def test_resource_locks_include_indirect_agents_and_tools(self):
        """页面或业务接口开发均锁住内部能力、共享 Runtime 及工具实现，不锁无关 Agent。"""
        plan = detection_plan()
        plan["api_contracts"].append(business("data_api", "data.read", "/api/data"))
        plan["agent_contracts"][0]["agentSettings"]["tools"] = {"enabled": True, "bindings": [
            {"toolId": "read_data", "apiContractId": "data_api", "endpointId": "data.read"}]}
        plan["page_implementation_contracts"] = [{"pageId": "orders", "requiredEndpointIds": ["order.list"]}]
        for scope in ({"type": "page", "targetId": "orders"},
                      {"type": "endpoint", "targetId": "order.list", "apiContractId": "order_api"}):
            with self.subTest(scope=scope):
                claims = resolve_execution_resource_claims(plan, scope)
                keys = set(resource_claim_keys(claims))
                self.assertTrue({"agent:api_scan", "agent:runtime", "endpoint:data_api:data.read",
                                 "api_contract:order_api", "api_contract:data_api"} <= keys, keys)
                self.assertNotIn("agent:database_scan", keys)
                primary = [claim for claim in claims if claim.role.value == "primary"]
                self.assertEqual(len(primary), 1)
