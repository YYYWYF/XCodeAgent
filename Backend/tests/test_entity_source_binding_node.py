from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.nodes.planning import _entity_source_binding_implementation
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec


def _plan_with_entity() -> tuple[dict, str]:
    """构造包含单个可选实体的计划，供实体绑定节点回归测试使用。"""

    plan = create_project_plan(create_requirement_spec("创建商品管理系统"))
    entity_id = str(plan["entities"][0]["id"])
    return plan, entity_id


class EntitySourceBindingNodeTests(unittest.TestCase):
    def test_empty_submission_does_not_skip_ai_assist_action(self) -> None:
        """空确认状态不得截断 AI 选表动作，建议必须投射回实体设计卡片。"""

        plan, entity_id = _plan_with_entity()
        suggestions = {
            "assist_type": "table_selection",
            "text": "建议使用商品表。",
            "messages": [{"role": "assistant", "content": "建议使用商品表。"}],
            "suggestions": [
                {
                    "id": "table-products",
                    "label": "products",
                    "payload": {"table_name": "products", "bindings": []},
                }
            ],
            "missing_fields": None,
            "source": "ai",
            "note": "",
        }
        state = {
            "selected_entity_id": entity_id,
            "project_plan": plan,
            "pending_project_plan": plan,
            "entity_source_binding_submission": {},
            "entity_design_action": {
                "action": "ai_assist",
                "entity_id": entity_id,
                "assist_type": "table_selection",
                "context": {"available_tables": [{"name": "products"}]},
            },
        }

        with patch(
            "app.graph.nodes.planning._build_entity_design_ai_suggestions",
            return_value=suggestions,
        ) as build_suggestions:
            result = _entity_source_binding_implementation(state)

        build_suggestions.assert_called_once()
        entity_design = result["clarification"]["review"]["summary"]["entityDesign"]
        self.assertEqual(entity_design["ai_suggestions"], suggestions)
        self.assertEqual(result["status"], "requires_user_input")

    def test_confirmed_submission_keeps_confirmation_priority(self) -> None:
        """显式确认提交仍须优先进入确认校验，不得误执行同时残留的 AI 动作。"""

        plan, entity_id = _plan_with_entity()
        state = {
            "selected_entity_id": entity_id,
            "project_plan": plan,
            "pending_project_plan": plan,
            "entity_source_binding_submission": {
                "review_status": "confirmed",
                "target_changes": [],
            },
            "entity_design_action": {
                "action": "ai_assist",
                "entity_id": entity_id,
                "assist_type": "table_selection",
                "context": {},
            },
        }

        with (
            patch(
                "app.graph.nodes.planning._build_entity_design_ai_suggestions",
            ) as build_suggestions,
            patch(
                "app.graph.nodes.planning._entity_design_requires_revision",
                return_value={"status": "requires_user_input"},
            ) as require_revision,
        ):
            result = _entity_source_binding_implementation(state)

        build_suggestions.assert_not_called()
        require_revision.assert_called_once()
        self.assertEqual(result["status"], "requires_user_input")


if __name__ == "__main__":
    unittest.main()
