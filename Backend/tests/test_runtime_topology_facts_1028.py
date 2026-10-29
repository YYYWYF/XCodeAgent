"""验证 Runtime 基础事实遵守 1028 配置权威和当前字段合同。"""

import unittest

from app.topologies.facts import extract_topology_facts
from app.topologies.model import TopologyFactSource


class RuntimeTopologyFactsTests(unittest.TestCase):
    """只校验事实提取，不注册拓扑、不启动工程或访问数据库。"""

    def test_application_config_owns_authentication(self) -> None:
        """模型产物的认证值不能覆盖当前应用配置。"""
        facts = extract_topology_facts(
            {"auth": {"enable": True}, "authorization": {"enabled": True},
             "agent_contracts": [{"agentId": "assistant"}]},
            {"auth": {"enable": False}, "authorization": {"enabled": False}},
            source=TopologyFactSource.TECHNICAL_PLAN,
        )
        self.assertIs(facts.auth_enabled, False)
        self.assertIs(facts.authorization_enabled, False)
        self.assertEqual(facts.agent_ids, ("assistant",))

    def test_missing_configuration_is_not_guessed(self) -> None:
        """缺失开关保持未知，由后续选择门禁拒绝而非隐式关闭。"""
        facts = extract_topology_facts({}, {}, source=TopologyFactSource.TECHNICAL_PLAN)
        self.assertIsNone(facts.auth_enabled)
        self.assertIsNone(facts.authorization_enabled)

    def test_business_entities_are_not_removed_for_direct(self) -> None:
        """完整保留业务实体和接口依赖，表名前缀不参与拓扑猜测。"""
        plan = {
            "entities": [{"id": "ChatSession", "table_name": "12ab34cd56ef_chat_session"}],
            "api_contracts": [{"id": "chat_api"}],
            "agent_contracts": [{"agentId": "assistant", "agentSettings": {
                "tools": {"bindings": [{"source": {"type": "application_service"}}]}}}],
        }
        facts = extract_topology_facts(
            plan, {}, source=TopologyFactSource.TECHNICAL_PLAN,
        )
        self.assertEqual(facts.backend_requirement_ids, ("entity:ChatSession", "api_contract:chat_api"))
        self.assertEqual(plan["entities"][0]["table_name"], "12ab34cd56ef_chat_session")

    def test_old_entity_identity_is_rejected(self) -> None:
        """不接受旧字段别名或以数组位置代替正式业务身份。"""
        with self.assertRaisesRegex(ValueError, "缺少 id"):
            extract_topology_facts(
                {"entities": [{"entityId": "ChatSession"}]}, {},
                source=TopologyFactSource.TECHNICAL_PLAN,
            )

    def test_java_only_tool_is_retained_as_constraint(self) -> None:
        """Java 专属工具继续作为拒绝 Direct 的事实，不能丢弃工具需求。"""
        facts = extract_topology_facts(
            {"agent_contracts": [{"agentId": "assistant", "agentSettings": {
                "tools": {"bindings": [{"source": {"type": "backend_endpoint"}}]}}}]},
            {}, source=TopologyFactSource.TECHNICAL_PLAN,
        )
        self.assertEqual(facts.backend_requirement_ids, ("backend_tool:assistant",))

