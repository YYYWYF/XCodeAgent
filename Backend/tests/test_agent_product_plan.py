from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.agents.main.document_sync import (
    _sync_prompt,
    sync_product_plan_from_markdown,
)
from app.agents.main.product_planner import (
    _product_plan_json_example,
    _product_planning_prompt,
)
from app.graph.nodes.ui_confirmation import _product_plan_hash
from app.services.product_plan import (
    create_product_plan,
    validate_product_plan,
    validate_product_plan_model_output,
)
from app.services.ui_design_agent_surfaces import project_ui_design_pages
from app.services.requirement_spec import create_requirement_spec
from app.workspace.product_plan_documents import render_product_plan_markdown


class AgentProductPlanTests(unittest.TestCase):
    """验证业务智能体需求进入 ProductPlan 产品契约与联合确认链路。"""

    def _requirement_spec_with_agent(self) -> dict:
        """构造包含一个业务智能体及有效入口页面的需求文档。"""

        spec = create_requirement_spec("创建一个库存管理系统，并提供库存问答助手")
        page_id = spec["pages"][0]["pageId"]
        spec["agent_requirements"] = [
            {
                "agentId": "inventory_assistant",
                "name": "库存助手",
                "purpose": "帮助用户理解库存状态并获得处理建议。",
                "capabilities": ["解释库存状态", "提供补货建议"],
                "entryPageIds": [page_id],
                "interactionMode": "conversation",
                "boundaries": ["不得直接修改库存数据"],
            }
        ]
        return spec

    def _agent_model_plan(self, spec: dict) -> dict:
        """构造产品模型应返回的页面动作与智能体产品规划。"""

        model_plan = json.loads(_product_plan_json_example(spec))
        page_id = spec["pages"][0]["pageId"]
        action_id = f"{page_id}_ask_inventory_assistant"
        model_plan["pages"][0]["actions"] = [
            {
                "actionId": action_id,
                "name": "询问库存助手",
                "description": "向库存助手发送问题并获取业务建议。",
                "requiresConfirmation": False,
                "behavior": {
                    "type": "business",
                    "expectedResult": "用户获得基于当前库存信息的明确回复。",
                },
            }
        ]
        model_plan["agents"] = [
            {
                "agentId": "inventory_assistant",
                "name": "库存助手",
                "purpose": "帮助用户理解库存状态并获得处理建议。",
                "capabilities": [
                    {
                        "capabilityId": "explain_inventory_status",
                        "name": "解释库存状态",
                        "expectedResult": "用户理解当前库存状态及异常原因。",
                    },
                    {
                        "capabilityId": "suggest_replenishment",
                        "name": "提供补货建议",
                        "expectedResult": "用户获得可执行的补货建议。",
                    },
                ],
                "entryPageIds": [page_id],
                "pageActionBindings": [
                    {
                        "pageId": page_id,
                        "actionIds": [action_id],
                        "surface": {
                            "type": "floating_panel",
                            "enabled": True,
                            "contextItemIds": [f"{page_id}-primary-information"],
                        },
                    }
                ],
                "interaction": {
                    "mode": "conversation",
                    "supportsMultiTurn": True,
                    "inputDescription": "用户输入库存相关的自然语言问题。",
                    "outputDescription": "返回库存解释、建议和必要的结果说明。",
                    "stateRequirements": {
                        "loading": "处理问题时展示生成状态。",
                        "empty": "无对话时展示可提问范围。",
                        "error": "失败时说明原因并允许重试。",
                        "success": "成功时展示完整回复。",
                        "validation": "空问题不能发送。",
                    },
                },
                "boundaries": ["不得直接修改库存数据"],
                "acceptanceCriteria": ["能够解释当前库存状态并提供明确建议。"],
            }
        ]
        return model_plan

    def test_product_plan_v8_defaults_to_empty_agents_for_ordinary_apps(self) -> None:
        """普通应用必须使用 v8 空智能体数组且保持现有页面行为。"""

        spec = create_requirement_spec("创建一个库存管理系统")

        plan = create_product_plan(spec)

        self.assertEqual(plan["schema_version"], "product-plan.v8")
        self.assertEqual(plan["agents"], [])
        self.assertEqual(validate_product_plan(plan, spec), [])

    def test_product_plan_projects_agent_contract_with_stable_references(self) -> None:
        """ProductPlan 必须承接需求智能体并闭合能力、页面和操作引用。"""

        spec = self._requirement_spec_with_agent()
        model_plan = self._agent_model_plan(spec)

        plan = create_product_plan(spec, agent_plan=model_plan)

        self.assertEqual(plan["agents"], model_plan["agents"])
        self.assertEqual(
            plan["agents"][0]["pageActionBindings"][0]["surface"],
            {
                "type": "floating_panel",
                "enabled": True,
                "contextItemIds": [f"{spec['pages'][0]['pageId']}-primary-information"],
            },
        )
        self.assertEqual(validate_product_plan(plan, spec), [])

    def test_product_plan_accepts_standalone_agent_page_surface(self) -> None:
        """独立智能体页面必须允许显式 standalone_page 且可不携带页面上下文。"""

        spec = self._requirement_spec_with_agent()
        model_plan = self._agent_model_plan(spec)
        model_plan["agents"][0]["pageActionBindings"][0]["surface"] = {
            "type": "standalone_page",
            "enabled": True,
            "contextItemIds": [],
        }

        plan = create_product_plan(spec, agent_plan=model_plan)

        self.assertEqual(validate_product_plan_model_output(model_plan, spec), [])
        self.assertEqual(validate_product_plan(plan, spec), [])

    def test_disabled_floating_surface_is_not_projected_to_ui_design(self) -> None:
        """关闭的悬浮智能体候选必须保留选择记录，但不得进入 UI 设计输入。"""

        spec = self._requirement_spec_with_agent()
        model_plan = self._agent_model_plan(spec)
        model_plan["agents"][0]["pageActionBindings"][0]["surface"]["enabled"] = False

        plan = create_product_plan(spec, agent_plan=model_plan)
        pages = project_ui_design_pages(plan)

        self.assertEqual(validate_product_plan(plan, spec), [])
        self.assertFalse(plan["agents"][0]["pageActionBindings"][0]["surface"]["enabled"])
        self.assertEqual(pages[0]["agent_surfaces"], [])
        self.assertEqual(pages[0]["actions"], [])

    def test_product_revision_preserves_explicit_surface_selection(self) -> None:
        """后续产品修订不得用模型默认开启值覆盖用户已关闭的浮窗。"""

        spec = self._requirement_spec_with_agent()
        initial = create_product_plan(spec, agent_plan=self._agent_model_plan(spec))
        initial["agents"][0]["pageActionBindings"][0]["surface"]["enabled"] = False
        revised_model_plan = self._agent_model_plan(spec)

        revised = create_product_plan(
            spec,
            agent_plan=revised_model_plan,
            existing_plan=initial,
        )

        self.assertFalse(revised["agents"][0]["pageActionBindings"][0]["surface"]["enabled"])

    def test_product_plan_rejects_invalid_or_cross_page_surface_context(self) -> None:
        """Surface 类型、上下文元素及同页 information item 引用必须严格合法。"""

        spec = self._requirement_spec_with_agent()
        model_plan = self._agent_model_plan(spec)
        binding = model_plan["agents"][0]["pageActionBindings"][0]
        binding["surface"] = {
            "type": "sidebar",
            "enabled": True,
            "contextItemIds": ["missing-item", "missing-item", 42],
        }

        model_errors = validate_product_plan_model_output(model_plan, spec)
        plan_errors = validate_product_plan(
            create_product_plan(spec, agent_plan=model_plan),
            spec,
        )

        self.assertTrue(any("surface.type" in error for error in model_errors))
        self.assertTrue(
            any("contextItemIds" in error and "字符串数组" in error for error in model_errors)
        )
        self.assertTrue(any("contextItemIds" in error and "不能重复" in error for error in model_errors))
        self.assertTrue(any("missing-item" in error for error in model_errors))
        self.assertTrue(any("surface.type" in error for error in plan_errors))
        self.assertTrue(any("missing-item" in error for error in plan_errors))

    def test_product_plan_rejects_duplicate_surface_type_on_same_page(self) -> None:
        """第一版同一页面不得挂载两个同类型可视化 Agent Surface。"""

        spec = self._requirement_spec_with_agent()
        model_plan = self._agent_model_plan(spec)
        page_id = spec["pages"][0]["pageId"]
        second_requirement = {
            **deepcopy(spec["agent_requirements"][0]),
            "agentId": "replenishment_assistant",
            "name": "补货助手",
            "purpose": "帮助用户生成补货建议。",
        }
        spec["agent_requirements"].append(second_requirement)
        second_agent = deepcopy(model_plan["agents"][0])
        second_agent.update(
            {
                "agentId": "replenishment_assistant",
                "name": "补货助手",
                "purpose": "帮助用户生成补货建议。",
            }
        )
        second_action_id = f"{page_id}_ask_replenishment_assistant"
        model_plan["pages"][0]["actions"].append(
            {
                "actionId": second_action_id,
                "name": "询问补货助手",
                "description": "向补货助手发送问题。",
                "requiresConfirmation": False,
                "behavior": {
                    "type": "business",
                    "expectedResult": "用户获得补货建议。",
                },
            }
        )
        second_agent["pageActionBindings"] = [
            {
                "pageId": page_id,
                "actionIds": [second_action_id],
                "surface": {
                    "type": "floating_panel",
                    "enabled": True,
                    "contextItemIds": [],
                },
            }
        ]
        model_plan["agents"].append(second_agent)

        errors = validate_product_plan_model_output(model_plan, spec)

        self.assertTrue(any("floating_panel" in error and "最多绑定一个" in error for error in errors))

    def test_product_plan_rejects_unknown_agent_action_and_technical_fields(self) -> None:
        """产品契约必须拒绝不存在的页面操作和提前出现的模型配置。"""

        spec = self._requirement_spec_with_agent()
        plan = create_product_plan(spec, agent_plan=self._agent_model_plan(spec))
        plan["agents"][0]["pageActionBindings"][0]["actionIds"] = ["missing_action"]
        plan["agents"][0]["modelId"] = "forbidden_model"

        errors = validate_product_plan(plan, spec)

        self.assertTrue(any("missing_action" in error for error in errors))
        self.assertTrue(any("modelId" in error for error in errors))

    def test_product_model_output_requires_exact_agent_coverage(self) -> None:
        """模型原始输出必须逐项覆盖 RequirementSpec 中的智能体。"""

        spec = self._requirement_spec_with_agent()
        model_plan = self._agent_model_plan(spec)
        self.assertEqual(validate_product_plan_model_output(model_plan, spec), [])

        missing_surface = deepcopy(model_plan)
        missing_surface["agents"][0]["pageActionBindings"][0].pop("surface")
        missing_surface_errors = validate_product_plan_model_output(missing_surface, spec)
        self.assertTrue(
            any(
                "pageActionBindings[0]" in error and "surface" in error
                for error in missing_surface_errors
            )
        )

        unexpected_surface_field = deepcopy(model_plan)
        unexpected_surface_field["agents"][0]["pageActionBindings"][0]["surface"][
            "position"
        ] = "left"
        unexpected_surface_errors = validate_product_plan_model_output(
            unexpected_surface_field,
            spec,
        )
        self.assertTrue(any("position" in error for error in unexpected_surface_errors))

        disabled_standalone = deepcopy(model_plan)
        disabled_standalone["agents"][0]["pageActionBindings"][0]["surface"] = {
            "type": "standalone_page",
            "enabled": False,
            "contextItemIds": [],
        }
        disabled_standalone_errors = validate_product_plan_model_output(
            disabled_standalone,
            spec,
        )
        self.assertTrue(any("standalone_page" in error for error in disabled_standalone_errors))

        model_plan["agents"] = []

        errors = validate_product_plan_model_output(model_plan, spec)
        self.assertTrue(any("agents" in error and "一一对应" in error for error in errors))

    def test_product_prompt_keeps_agent_plan_at_product_boundary(self) -> None:
        """产品规划提示必须生成可确认行为，但禁止提前选择技术实现。"""

        prompt = _product_planning_prompt(self._requirement_spec_with_agent())

        self.assertIn("agents", prompt)
        self.assertIn("pageActionBindings", prompt)
        self.assertIn("surface", prompt)
        self.assertIn("standalone_page", prompt)
        self.assertIn("floating_panel", prompt)
        self.assertIn("contextItemIds", prompt)
        self.assertIn("capabilityId", prompt)
        self.assertIn("Never return model", prompt)
        self.assertIn("API endpoint", prompt)
        self.assertIn("knowledge", prompt)

    def test_product_markdown_and_sync_preserve_agent_product_contract(self) -> None:
        """联合确认 Markdown 必须展示并可同步智能体产品规划。"""

        spec = self._requirement_spec_with_agent()
        plan = create_product_plan(spec, agent_plan=self._agent_model_plan(spec))
        markdown = render_product_plan_markdown(plan)

        self.assertIn("## 智能体产品规划", markdown)
        self.assertIn("`inventory_assistant` 库存助手", markdown)
        self.assertIn("解释库存状态", markdown)
        self.assertIn("不得直接修改库存数据", markdown)
        self.assertIn("悬浮问答面板", markdown)
        self.assertIn("页面上下文", markdown)
        self.assertIn("ProductPlan", _sync_prompt(
            artifact_name="ProductPlan",
            structured_document=plan,
            edited_markdown=markdown,
        ))

        edited = deepcopy(plan)
        edited["agents"][0]["acceptanceCriteria"] = ["编辑后的智能体验收标准"]
        edited["agents"][0]["capabilities"][0]["capabilityId"] = "changed_by_sync_model"
        edited["agents"][0]["pageActionBindings"][0]["surface"] = {
            "type": "standalone_page",
            "enabled": True,
            "contextItemIds": [],
        }
        with patch(
            "app.agents.main.document_sync._invoke_sync_model",
            return_value=edited,
        ):
            synchronized = sync_product_plan_from_markdown(plan, spec, markdown)

        self.assertEqual(
            synchronized["agents"][0]["acceptanceCriteria"],
            ["编辑后的智能体验收标准"],
        )
        self.assertEqual(
            synchronized["agents"][0]["capabilities"][0]["capabilityId"],
            plan["agents"][0]["capabilities"][0]["capabilityId"],
        )
        self.assertEqual(
            synchronized["agents"][0]["pageActionBindings"][0]["surface"],
            {"type": "standalone_page", "enabled": True, "contextItemIds": []},
        )
        self.assertNotEqual(
            _product_plan_hash({"product_plan": plan}),
            _product_plan_hash({"product_plan": synchronized}),
        )


if __name__ == "__main__":
    unittest.main()
