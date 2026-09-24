from __future__ import annotations

import unittest

from app.topologies import (
    TopologyType,
    compile_registered_topology,
    resolve_product_topology,
    resolve_registered_topology,
    resolve_topology,
    TopologyFactSource,
    topology_type_from_plan,
)
from app.topologies.registry import registered_topologies


class ApplicationTopologyFrameworkTests(unittest.TestCase):
    """验证 Agent Runtime Direct 只在完整不变量满足时参与编译。"""

    def _direct_plan(self) -> dict:
        """构造最小纯 Agent TechnicalPlan 候选。"""

        return {
            "artifact_type": "technical-plan",
            "entities": [],
            "api_contracts": [],
            "pages": [{"pageId": "assistant", "references": {}}],
            "agent_contracts": [
                {"agentId": "assistant", "agentSettings": {"tools": {"bindings": []}}}
            ],
        }

    def _config(self, *, auth: bool = True, authorization: bool = False) -> dict:
        """构造 canonical 应用能力快照。"""

        return {
            "configRevision": 3,
            "auth": {"enable": auth},
            "authorization": {"enabled": authorization},
        }

    def test_direct_topology_compiles_three_stage_blueprint(self) -> None:
        """纯 Agent 正式事实应得到唯一 Direct 蓝图。"""

        self.assertEqual(len(registered_topologies()), 1)
        blueprint = resolve_registered_topology(self._direct_plan(), self._config())
        self.assertIsNotNone(blueprint)
        assert blueprint is not None
        self.assertEqual(blueprint.type, TopologyType.AGENT_RUNTIME_DIRECT)
        self.assertEqual(blueprint.planning.managed_roots, ("frontend", "agent-runtime"))
        self.assertIn("agent_runtime_public_auth", blueprint.planning.required_template_capabilities)
        self.assertIn("python_business_migrations", blueprint.planning.required_template_capabilities)
        self.assertNotIn("backend:bootstrap", blueprint.planning.unit_ids)
        self.assertEqual(blueprint.development.launch_stages[1], "agent_runtime")
        self.assertNotIn("database_migration", blueprint.development.launch_stages)
        self.assertIn("page:assistant", blueprint.planning.unit_ids)
        self.assertTrue(blueprint.design.source_facts_sha256.startswith("sha256:"))

    def test_direct_topology_rejects_rbac_or_backend_facts(self) -> None:
        """Direct 承载业务 Entity，但拒绝 RBAC 与 Java Backend 专属 Tool。"""

        self.assertIsNone(
            resolve_registered_topology(self._direct_plan(), self._config(authorization=True))
        )
        with_entity = {**self._direct_plan(), "entities": [{"id": "order"}]}
        self.assertIsNotNone(resolve_registered_topology(with_entity, self._config()))
        with_tool = self._direct_plan()
        with_tool["agent_contracts"][0]["agentSettings"]["tools"]["bindings"] = [
            {"source": {"type": "backend_endpoint"}}
        ]
        self.assertIsNone(resolve_registered_topology(with_tool, self._config()))

    def test_product_and_technical_stages_share_one_resolver_contract(self) -> None:
        """产品预判与技术计划终判必须通过同一 Resolver 返回相同拓扑枚举。"""

        product_plan = {
            "agents": [
                {
                    "agentId": "assistant",
                    "pageActionBindings": [{"surface": {"enabled": True}}],
                }
            ],
            "pages": [
                {
                    "pageId": "assistant",
                    "actions": [
                        {
                            "actionId": "open_assistant",
                            "behavior": {"type": "interface"},
                        }
                    ],
                }
            ],
        }

        product_resolution = resolve_product_topology(product_plan, self._config())
        technical_resolution = resolve_topology(
            self._direct_plan(),
            self._config(),
            source=TopologyFactSource.TECHNICAL_PLAN,
        )

        self.assertEqual(
            product_resolution.selected_type,
            TopologyType.AGENT_RUNTIME_DIRECT,
        )
        self.assertEqual(
            technical_resolution.selected_type,
            product_resolution.selected_type,
        )
        self.assertIsNotNone(technical_resolution.blueprint)

    def test_resolution_exposes_stable_rejection_reasons(self) -> None:
        """统一 Resolver 应返回可诊断的 RBAC 和 Backend 事实拒绝原因。"""

        with_entity = {**self._direct_plan(), "entities": [{"id": "order"}]}
        resolution = resolve_topology(
            with_entity,
            self._config(authorization=True),
            source=TopologyFactSource.TECHNICAL_PLAN,
        )

        reasons = resolution.rejection_reasons_for(
            TopologyType.AGENT_RUNTIME_DIRECT
        )
        self.assertIn("authorization.enabled 必须为 false", reasons)
        self.assertFalse(any("entity:order" in reason for reason in reasons))

    def test_confirmed_direct_topology_fails_when_facts_drift(self) -> None:
        """已确认枚举在能力事实漂移后必须失败关闭。"""

        with self.assertRaisesRegex(ValueError, "不再满足"):
            compile_registered_topology(
                TopologyType.AGENT_RUNTIME_DIRECT,
                self._direct_plan(),
                self._config(authorization=True),
            )

    def test_topology_type_parser_rejects_unknown_value(self) -> None:
        """TechnicalPlan 中的未知拓扑类型必须失败关闭。"""

        self.assertEqual(
            topology_type_from_plan(
                {"topology": {"type": TopologyType.AGENT_RUNTIME_DIRECT.value}}
            ),
            TopologyType.AGENT_RUNTIME_DIRECT,
        )
        with self.assertRaisesRegex(ValueError, "不受支持"):
            topology_type_from_plan({"topology": {"type": "unknown"}})


if __name__ == "__main__":
    unittest.main()
