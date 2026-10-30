"""验证混合业务/Agent 上下文不会使平台自建 Python 检查引用越界接口。"""

from copy import deepcopy
from unittest import TestCase

from app.services.runtime_business_acceptance import compile_runtime_check


class RuntimeAcceptanceSourceScopeTests(TestCase):
    """重现 bootstrap 读取完整上下文时误带 Agent 接入来源的编译断点。"""

    def test_bootstrap_sources_and_expected_designs_stay_in_business_scope(self):
        """通过真实检查编译器核对来源及预期，不删除边界校验规则。"""
        business = {"apiContractId": "leave", "endpointId": "balance", "implementationKind": "business",
                    "fieldMappings": [], "endpointContract": {"method": "GET"}}
        agent = {"apiContractId": "agent.assistant", "endpointId": "agent.assistant.run",
                 "implementationKind": "agent_runtime", "sourceBinding": {"sourceType": "agent", "agentId": "assistant"}}
        unrelated = {**business, "apiContractId": "another", "endpointId": "balance"}
        contracts = [{"id": api, "endpoints": [{"id": identity, "kind": kind, "method": "GET", "path": "/"}], "schemas": {}}
                     for api, identity, kind in [("leave", "balance", "business"), ("another", "balance", "business"),
                                                 ("agent.assistant", "agent.assistant.run", "agent_runtime")]]
        formal = {"project_plan": {"topology": {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
                  "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"}, "entities": []},
                  "contracts": contracts, "endpoint_ids": ["balance"], "api_contract_ids": ["leave"],
                  "endpoint_designs": [business, agent, unrelated], "entity_details": [],
                  "endpoint_details": [], "runtime_dependency_paths": [], "source_refs": {}}
        before = deepcopy(formal)
        task = {"id": "bootstrap", "unit_id": "backend:bootstrap", "owner": "backend"}
        deliverable = {"id": "bootstrap", "kind": "backend.bootstrap", "target_id": "mysql",
                       "paths": ["agent-runtime/src/app/main.py"]}
        check = compile_runtime_check(task, deliverable, formal)[0]
        self.assertEqual(check["expected"]["endpoint_designs"], [business])
        self.assertEqual({item["target_id"] for item in check["sources"] if item["artifact"] == "api_design"}, {"balance"})
        self.assertEqual({item["api_contract_id"] for item in check["expected"]["endpoints"]}, {"leave"})
        self.assertEqual(before, formal)
