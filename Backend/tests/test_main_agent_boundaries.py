from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.agents.main import page_designer, planner, requirements_analyzer
from app.services.page_detail_plan import extract_page_detail_context
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.tools.ask_user import ask_user


class FakeChatModel:
    def __init__(self, message: AIMessage) -> None:
        self.message = message
        self.bound_tools: list[object] | None = None
        self.prompts: list[str] = []

    def bind_tools(self, tools: list[object]) -> FakeChatModel:
        self.bound_tools = list(tools)
        return self

    def invoke(self, prompt: str) -> AIMessage:
        self.prompts.append(prompt)
        return self.message


class DirectChatModelBoundaryTests(unittest.TestCase):
    def test_project_planning_prompt_requires_canonical_contract_id(self) -> None:
        """项目规划提示必须固定契约主键并绑定实体，避免模型改用 contract_id。"""

        prompt = planner._planning_prompt(create_requirement_spec("创建天气预报系统"))

        self.assertIn("never contract_id or contractId", prompt)
        self.assertIn("entity_ids", prompt)
        self.assertIn("only contract binding", prompt)

    def test_project_planning_prompt_shows_canonical_api_contract_example(self) -> None:
        """项目规划提示必须给出 API 契约样例，固定 Schema 引用格式。"""

        prompt = planner._planning_prompt(create_requirement_spec("创建库存管理系统"))

        self.assertIn("Canonical api_contracts example", prompt)
        self.assertIn('"schemas": {', prompt)
        self.assertIn('"$ref": "InventoryItem"', prompt)
        self.assertIn('"response_schema_ref": "InventoryListResponse"', prompt)
        self.assertIn("Do NOT use #/definitions/...", prompt)
        self.assertIn("#/components/schemas/...", prompt)

    def test_static_project_planning_prompt_disables_database_backend(self) -> None:
        """Static 规划提示必须把契约定义为前端内存边界并禁止数据库实现。"""

        spec = create_requirement_spec(
            "创建库存查看系统",
            datasource_type="static",
        )
        prompt = planner._planning_prompt(spec, datasource_type="static")

        self.assertIn("type=static", prompt)
        self.assertIn("frontend in-memory mock", prompt)
        self.assertIn("do not represent a real HTTP backend", prompt)
        self.assertIn("Do not emit generic full CRUD", prompt)

    def test_requirements_uses_direct_model_with_only_ask_user(self) -> None:
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "ask_user",
                    "args": {
                        "questions": [
                            {
                                "header": "角色",
                                "question": "主要使用者是谁？",
                                "type": "text",
                            }
                        ]
                    },
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        )
        fake_model = FakeChatModel(message)
        settings = SimpleNamespace(model_name="test-model")

        with (
            patch(
                "app.agents.main.requirements_analyzer.Settings.from_env",
                return_value=settings,
            ) as from_env,
            patch(
                "app.agents.main.requirements_analyzer.create_chat_model",
                return_value=fake_model,
            ) as create_model,
            patch("app.agents.create_agent_bundle") as bundle_factory,
        ):
            result = requirements_analyzer.analyze_requirements_with_chat_model(
                "创建一个库存管理系统"
            )

        from_env.assert_called_once_with()
        create_model.assert_called_once_with(settings)
        bundle_factory.assert_not_called()
        self.assertEqual(fake_model.bound_tools, [ask_user])
        self.assertIn("requirements model", fake_model.prompts[0])
        self.assertEqual(result["clarification"]["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["questions"][0]["header"], "角色")
        self.assertEqual(
            result["requirement_spec"]["analyzed_by"],
            {
                "agent": "chat-model",
                "mode": "direct",
                "model": "test-model",
                "source": "direct_chat_model",
            },
        )

    def test_project_planning_uses_direct_model_without_tools(self) -> None:
        fake_model = FakeChatModel(
            AIMessage(content='{"requirements_overview": {"summary": "ok"}}')
        )
        settings = SimpleNamespace(model_name="test-model")
        spec = create_requirement_spec("创建一个库存管理系统")

        with (
            patch(
                "app.agents.main.planner.Settings.from_env",
                return_value=settings,
            ) as from_env,
            patch(
                "app.agents.main.planner.create_chat_model",
                return_value=fake_model,
            ) as create_model,
            patch("app.agents.create_agent_bundle") as bundle_factory,
        ):
            result = planner.plan_project_with_chat_model(spec)

        from_env.assert_called_once_with()
        create_model.assert_called_once_with(settings)
        bundle_factory.assert_not_called()
        self.assertIsNone(fake_model.bound_tools)
        self.assertIn("project-planning model", fake_model.prompts[0])
        self.assertEqual(result["planning_source"], "direct_chat_model")
        self.assertEqual(result["planned_by"]["agent"], "chat-model")
        self.assertEqual(result["planned_by"]["mode"], "direct")

    def test_page_design_uses_direct_model_without_tools(self) -> None:
        fake_model = FakeChatModel(AIMessage(content="Design the confirmed page."))
        settings = SimpleNamespace(model_name="test-model")
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_context = extract_page_detail_context(
            project_plan,
            project_plan["frontend_pages"][0]["pageId"],
        )

        with (
            patch(
                "app.agents.main.page_designer.Settings.from_env",
                return_value=settings,
            ) as from_env,
            patch(
                "app.agents.main.page_designer.create_chat_model",
                return_value=fake_model,
            ) as create_model,
            patch("app.agents.create_agent_bundle") as bundle_factory,
        ):
            result = page_designer.design_page_with_chat_model(
                project_plan,
                page_context,
            )

        from_env.assert_called_once_with()
        create_model.assert_called_once_with(settings)
        bundle_factory.assert_not_called()
        self.assertIsNone(fake_model.bound_tools)
        self.assertIn("page-design model", fake_model.prompts[0])
        self.assertEqual(result["design_source"], "direct_chat_model")
        self.assertEqual(result["designed_by"]["agent"], "chat-model")
        self.assertEqual(result["designed_by"]["mode"], "direct")


if __name__ == "__main__":
    unittest.main()
