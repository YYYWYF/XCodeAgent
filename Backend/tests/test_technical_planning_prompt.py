"""验证技术规划提示词保持拓扑无关，且拓扑解析契约对业务事实不再改判。

设计边界见 docs/topology/APPLICATION_TOPOLOGY_DESIGN.md：TechnicalPlan Core 必须
拓扑无关，业务 Entity/API 先行规划完成，再由用户在独立门禁显式选择拓扑。
"""

from __future__ import annotations

import unittest
from copy import deepcopy

from app.agents.main.planner import _technical_planning_prompt
from app.topologies import TopologyType, resolve_product_topology
from tests.agent_runtime_direct_test_utils import direct_product_plan

# Direct 形态要求权限关闭；开启权限必须由 Resolver 拒绝该拓扑。
_CONFIG_ALLOWING_DIRECT = {"auth": {"enable": True}, "authorization": {"enabled": False}}
_CONFIG_REQUIRING_RBAC = {"auth": {"enable": True}, "authorization": {"enabled": True}}

# Core 阶段必须出现在任何产品事实下的拓扑无关规则。
_TOPOLOGY_NEUTRAL_RULES = (
    "The object has exactly five sections: architecture, entities, api_contracts, pages, and agent_contracts.",
    "architecture has exactly frontend, application, and data",
    "without naming Java/Python owners, public edge, Gateway, deployment, or database product",
    "Derive business entities, API contracts, and page bindings exclusively from confirmed ProductPlan facts.",
    "do not emit gatewayEndpointId, invocation, serviceId, or deployment ownership",
    "only after explicit topology selection",
)

# 已废弃的双形态提示词特征，任何场景下都不得再出现。
_OBSOLETE_DIRECT_FORM_MARKERS = (
    "Agent Runtime Direct candidate",
    "The plan uses the Agent Runtime Direct form",
    "Never add a Java Backend",
    "agent_runtime, and data",
)


def _product_plan_with_actions(actions: list[dict]) -> dict:
    """构造只替换页面动作的纯 Agent 产品计划。"""

    plan = deepcopy(direct_product_plan())
    plan["pages"][0]["actions"] = actions
    return plan


def _interface_actions() -> list[dict]:
    """返回不需要服务端业务后端的界面动作。"""

    return [{"actionId": "open_assistant", "behavior": {"type": "interface"}}]


def _business_actions() -> list[dict]:
    """返回需要服务端业务 API 实现的业务动作。"""

    return [{"actionId": "submit_order", "behavior": {"type": "business"}}]


class DirectTopologyResolutionTests(unittest.TestCase):
    """验证统一 Resolver 只用能力事实判定 Direct，不再按业务事实改判。"""

    def test_interface_only_plan_is_a_direct_candidate(self) -> None:
        """只有界面动作、存在启用 Surface、权限关闭时判定为 Direct 候选。"""

        resolution = resolve_product_topology(
            _product_plan_with_actions(_interface_actions()), _CONFIG_ALLOWING_DIRECT
        )

        self.assertEqual(
            resolution.selected_type,
            TopologyType.AGENT_RUNTIME_DIRECT,
        )
        self.assertEqual(resolution.facts.surface_agent_ids, ("assistant",))
        self.assertEqual(resolution.facts.business_action_ids, ())

    def test_business_action_stays_a_direct_candidate(self) -> None:
        """业务动作仍记录为事实，但由 Python Runtime 承载业务 API 后不再排除 Direct。"""

        resolution = resolve_product_topology(
            _product_plan_with_actions(_business_actions()), _CONFIG_ALLOWING_DIRECT
        )

        self.assertEqual(
            resolution.selected_type,
            TopologyType.AGENT_RUNTIME_DIRECT,
        )
        self.assertEqual(resolution.facts.business_action_ids, ("submit_order",))

    def test_missing_behavior_is_treated_as_business(self) -> None:
        """动作缺少 behavior 时按业务动作记录，避免漏掉真实业务事实。"""

        resolution = resolve_product_topology(
            _product_plan_with_actions([{"actionId": "open_assistant"}]),
            _CONFIG_ALLOWING_DIRECT,
        )

        self.assertEqual(
            resolution.selected_type,
            TopologyType.AGENT_RUNTIME_DIRECT,
        )
        self.assertEqual(resolution.facts.business_action_ids, ("open_assistant",))

    def test_sequence_only_records_business_steps(self) -> None:
        """纯导航业务流程不产生业务事实，含业务步骤的业务流程记录该动作。"""

        navigation_only = resolve_product_topology(
            _product_plan_with_actions(
                [
                    {
                        "actionId": "go",
                        "behavior": {
                            "type": "sequence",
                            "steps": [{"stepId": "s1", "type": "navigation"}],
                        },
                    }
                ]
            ),
            _CONFIG_ALLOWING_DIRECT,
        )
        with_business_step = resolve_product_topology(
            _product_plan_with_actions(
                [
                    {
                        "actionId": "flow",
                        "behavior": {
                            "type": "sequence",
                            "steps": [{"stepId": "s1", "type": "business"}],
                        },
                    }
                ]
            ),
            _CONFIG_ALLOWING_DIRECT,
        )

        self.assertEqual(navigation_only.facts.business_action_ids, ())
        self.assertEqual(
            navigation_only.selected_type,
            TopologyType.AGENT_RUNTIME_DIRECT,
        )
        self.assertEqual(with_business_step.facts.business_action_ids, ("flow",))

    def test_rbac_and_missing_surface_block_direct_candidate(self) -> None:
        """开启权限或没有启用 Surface 时不得判定为 Direct 候选。"""

        plan = _product_plan_with_actions(_interface_actions())

        self.assertIsNone(
            resolve_product_topology(plan, _CONFIG_REQUIRING_RBAC).selected_type
        )
        no_surface = deepcopy(plan)
        no_surface["agents"] = []
        resolution = resolve_product_topology(no_surface, _CONFIG_ALLOWING_DIRECT)
        self.assertIsNone(resolution.selected_type)
        self.assertIn(
            "必须至少存在一个可用 Agent",
            resolution.rejection_reasons_for(TopologyType.AGENT_RUNTIME_DIRECT),
        )


class TechnicalPlanPromptTests(unittest.TestCase):
    """验证技术规划提示词在任何产品事实与能力配置下都保持同一种拓扑无关形态。"""

    def _prompt(self, actions: list[dict], config: dict) -> str:
        """构造指定产品动作与配置下的技术规划提示词。"""

        return _technical_planning_prompt(
            {
                "confirmed_product_plan": _product_plan_with_actions(actions),
                "application_config": config,
            },
            None,
        )

    def _scenario_prompts(self) -> dict[str, str]:
        """返回界面动作、业务动作与开启权限三种场景下的提示词。"""

        return {
            "interface": self._prompt(_interface_actions(), _CONFIG_ALLOWING_DIRECT),
            "business": self._prompt(_business_actions(), _CONFIG_ALLOWING_DIRECT),
            "rbac": self._prompt(_interface_actions(), _CONFIG_REQUIRING_RBAC),
        }

    def test_every_scenario_keeps_the_topology_neutral_architecture_rule(self) -> None:
        """三段架构规则不得按产品事实切换成 Direct 或 Java 后端形态。"""

        for name, prompt in self._scenario_prompts().items():
            with self.subTest(scenario=name):
                self.assertIn(
                    "has exactly frontend, application, and data",
                    prompt,
                )
                self.assertNotIn("has exactly frontend, agent_runtime, and data", prompt)
                self.assertNotIn("has exactly frontend, backend, and data", prompt)

    def test_every_scenario_states_the_core_boundary_rules(self) -> None:
        """五段结构、实体边界与 Agent 候选边界必须在三种场景下都成立。"""

        for name, prompt in self._scenario_prompts().items():
            with self.subTest(scenario=name):
                for marker in _TOPOLOGY_NEUTRAL_RULES:
                    self.assertIn(marker, prompt)

    def test_obsolete_direct_form_markers_are_gone(self) -> None:
        """已废弃的双形态提示词特征不得再出现在提示词中。"""

        for name, prompt in self._scenario_prompts().items():
            with self.subTest(scenario=name):
                for marker in _OBSOLETE_DIRECT_FORM_MARKERS:
                    self.assertNotIn(marker, prompt)
                self.assertNotIn('"gatewayEndpointId"', prompt)
                self.assertNotIn('"entities": []', prompt)

    def test_core_plans_business_facts_before_any_topology_choice(self) -> None:
        """Core 示例必须规划真实业务实体，并把服务归属留给拓扑选择。"""

        prompt = self._prompt(_business_actions(), _CONFIG_ALLOWING_DIRECT)

        self.assertIn('"id": "Order"', prompt)
        self.assertIn("Do not remove business facts to fit any topology.", prompt)
        self.assertIn(
            "Do not invent a transport-only Agent gateway Endpoint or entity before topology selection.",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()
