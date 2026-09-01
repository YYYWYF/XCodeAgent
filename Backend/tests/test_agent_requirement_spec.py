from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from app.agents.main.document_sync import (
    _sync_prompt,
    sync_requirement_spec_from_markdown,
)
from app.agents.main.requirements_analyzer import (
    _requirements_prompt,
    _validate_complete_requirement_spec,
)
from app.services.requirement_spec import (
    apply_requirement_spec_editor_changes,
    create_requirement_spec,
    validate_requirement_spec_confirmation_readiness,
)
from app.workspace.spec_documents import render_requirement_spec_markdown


class AgentRequirementSpecTests(unittest.TestCase):
    """验证业务智能体需求进入现有 RequirementSpec 确认链路。"""

    def test_requirement_spec_defaults_to_no_business_agents(self) -> None:
        """普通应用必须显式产生空智能体需求，且不改变既有页面规划。"""

        spec = create_requirement_spec("创建一个库存管理系统")

        self.assertEqual(spec["agent_requirements"], [])
        self.assertTrue(spec["pages"])

    def test_requirement_spec_normalizes_business_agent_requirement(self) -> None:
        """模型识别出的智能体需求必须归一为稳定的产品级字段。"""

        base_spec = create_requirement_spec("创建一个项目回检管理系统")
        target_page_id = base_spec["pages"][0]["pageId"]
        agent_spec = deepcopy(base_spec)
        agent_spec["agent_requirements"] = [
            {
                "agentId": " recheck_assistant ",
                "name": " 回检填报助手 ",
                "purpose": " 帮助用户理解回检状态并完成填报。 ",
                "capabilities": ["解释回检状态", "解释回检状态", "提供下一步建议"],
                "entryPageIds": [target_page_id, target_page_id],
                "interactionMode": " conversation ",
                "boundaries": ["只能读取当前用户可见的回检信息"],
            }
        ]

        spec = create_requirement_spec(
            "创建一个项目回检管理系统",
            agent_spec=agent_spec,
            authoritative_agent_spec=True,
        )

        self.assertEqual(
            spec["agent_requirements"],
            [
                {
                    "agentId": "recheck_assistant",
                    "name": "回检填报助手",
                    "purpose": "帮助用户理解回检状态并完成填报。",
                    "capabilities": ["解释回检状态", "提供下一步建议"],
                    "entryPageIds": [target_page_id],
                    "interactionMode": "conversation",
                    "boundaries": ["只能读取当前用户可见的回检信息"],
                }
            ],
        )

    def test_confirmation_rejects_invalid_agent_identity_and_page_reference(self) -> None:
        """确认门禁必须拒绝重复标识、非法标识和不存在的入口页面。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["agent_requirements"] = [
            {
                "agentId": "Invalid-Agent",
                "name": "库存助手",
                "purpose": "解释库存状态。",
                "capabilities": ["解释库存状态"],
                "entryPageIds": ["missing_page"],
                "interactionMode": "conversation",
                "boundaries": [],
            },
            {
                "agentId": "Invalid-Agent",
                "name": "重复库存助手",
                "purpose": "提供库存建议。",
                "capabilities": ["提供库存建议"],
                "entryPageIds": [],
                "interactionMode": "conversation",
                "boundaries": [],
            },
        ]

        errors = validate_requirement_spec_confirmation_readiness(spec)

        self.assertTrue(any("lower_snake_case" in error for error in errors))
        self.assertTrue(any("重复" in error for error in errors))
        self.assertTrue(any("missing_page" in error for error in errors))

    def test_agent_entry_page_reference_follows_page_id_normalization(self) -> None:
        """页面 ID 被规范化时，智能体入口引用必须同步更新。"""

        base_spec = create_requirement_spec("创建一个库存管理系统")
        agent_spec = deepcopy(base_spec)
        agent_spec["pages"][0]["pageId"] = "InventoryDashboard"
        agent_spec["agent_requirements"] = [
            {
                "agentId": "inventory_assistant",
                "name": "库存助手",
                "purpose": "解释库存状态。",
                "capabilities": ["解释库存状态"],
                "entryPageIds": ["InventoryDashboard"],
                "interactionMode": "conversation",
                "boundaries": [],
            }
        ]

        spec = create_requirement_spec(
            "创建一个库存管理系统",
            agent_spec=agent_spec,
            authoritative_agent_spec=True,
        )

        self.assertEqual(spec["pages"][0]["pageId"], "inventory_dashboard")
        self.assertEqual(
            spec["agent_requirements"][0]["entryPageIds"],
            ["inventory_dashboard"],
        )

    def test_requirement_model_contract_requires_complete_agent_array(self) -> None:
        """需求模型的完整 JSON 必须显式包含智能体需求数组。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec.pop("agent_requirements", None)

        with self.assertRaisesRegex(ValueError, "agent_requirements"):
            _validate_complete_requirement_spec(spec)

    def test_requirement_model_contract_rejects_invalid_agent_boundaries_type(self) -> None:
        """模型显式返回非数组业务边界时仍必须拒绝，不能按缺失字段兜底。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        page_id = spec["pages"][0]["pageId"]
        spec["agent_requirements"] = [
            {
                "agentId": "inventory_assistant",
                "name": "库存助手",
                "purpose": "解释库存状态。",
                "capabilities": ["解释库存状态"],
                "entryPageIds": [page_id],
                "interactionMode": "conversation",
                "boundaries": "不得修改库存数据",
            }
        ]

        with self.assertRaisesRegex(ValueError, r"agent_requirements\[0\]\.boundaries"):
            _validate_complete_requirement_spec(spec)

    def test_requirement_markdown_renders_business_agent_section(self) -> None:
        """需求文档必须展示职责、能力、入口、交互方式与业务边界。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        page_id = spec["pages"][0]["pageId"]
        spec["agent_requirements"] = [
            {
                "agentId": "inventory_assistant",
                "name": "库存助手",
                "purpose": "帮助用户理解库存状态。",
                "capabilities": ["解释库存状态"],
                "entryPageIds": [page_id],
                "interactionMode": "conversation",
                "boundaries": ["不得修改库存数据"],
            }
        ]

        markdown = render_requirement_spec_markdown(spec)

        self.assertIn("## 智能体需求", markdown)
        self.assertIn("`inventory_assistant` 库存助手", markdown)
        self.assertIn("解释库存状态", markdown)
        self.assertIn(page_id, markdown)
        self.assertIn("不得修改库存数据", markdown)

    def test_requirement_editor_can_replace_business_agent_requirements(self) -> None:
        """概览编辑器提交的智能体需求必须合并回内部 RequirementSpec。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        page_id = spec["pages"][0]["pageId"]
        edited = {
            "agent_requirements": [
                {
                    "agentId": "inventory_assistant",
                    "name": "库存助手",
                    "purpose": "解释库存状态。",
                    "capabilities": ["解释库存状态"],
                    "entryPageIds": [page_id],
                    "interactionMode": "conversation",
                    "boundaries": ["不得修改库存数据"],
                }
            ]
        }

        synchronized = apply_requirement_spec_editor_changes(spec, edited)

        self.assertEqual(
            synchronized["agent_requirements"][0]["agentId"],
            "inventory_assistant",
        )

    def test_requirement_prompts_keep_agent_requirements_at_product_boundary(self) -> None:
        """需求生成和 Markdown 回写提示都必须禁止提前生成技术配置。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        analysis_prompt = _requirements_prompt("创建一个带库存助手的库存管理系统")
        sync_prompt = _sync_prompt(
            artifact_name="RequirementSpec",
            structured_document=spec,
            edited_markdown=render_requirement_spec_markdown(spec),
        )

        for prompt in (analysis_prompt, sync_prompt):
            self.assertIn("agent_requirements", prompt)
            self.assertIn("agentId", prompt)
            self.assertIn("model", prompt.lower())
            self.assertIn("tool", prompt.lower())

    def test_markdown_sync_preserves_edited_business_agent_requirements(self) -> None:
        """Markdown 编辑后的智能体需求必须同步回结构化文档。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        page_id = spec["pages"][0]["pageId"]
        synchronized_model_output = {
            **spec,
            "agent_requirements": [
                {
                    "agentId": "inventory_assistant",
                    "name": "库存助手",
                    "purpose": "解释库存状态。",
                    "capabilities": ["解释库存状态"],
                    "entryPageIds": [page_id],
                    "interactionMode": "conversation",
                    "boundaries": ["不得修改库存数据"],
                }
            ],
        }

        with patch(
            "app.agents.main.document_sync._invoke_sync_model",
            return_value=synchronized_model_output,
        ):
            synchronized = sync_requirement_spec_from_markdown(
                spec,
                render_requirement_spec_markdown(spec),
            )

        self.assertEqual(
            synchronized["agent_requirements"],
            synchronized_model_output["agent_requirements"],
        )
        self.assertEqual(synchronized["markdown_sync"]["status"], "synchronized")


if __name__ == "__main__":
    unittest.main()
