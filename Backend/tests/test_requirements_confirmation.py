from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from app.graph.nodes.requirements import requirements
from app.services.requirement_spec import create_requirement_spec
from app.tools.ask_user import clear_clarification
from app.workspace.spec_documents import write_requirement_spec_document


class RequirementsConfirmationTests(unittest.TestCase):
    def test_authoritative_markdown_sync_can_remove_generated_items(self) -> None:
        spec = create_requirement_spec(
            "创建一个库存管理系统",
            agent_spec={"pages": [], "acceptance_criteria": []},
            authoritative_agent_spec=True,
        )

        self.assertEqual(spec["pages"], [])
        self.assertEqual(spec["acceptance_criteria"], [])

    def test_model_requirement_spec_replaces_default_roles_and_pages(self) -> None:
        spec = create_requirement_spec(
            "只需要一个人员列表页、一个普通用户角色，不需要登录",
            agent_spec={
                "app_info": {"name": "人员管理应用", "target": "管理人员信息"},
                "user_roles": [{"id": "user", "name": "普通用户", "description": "使用系统"}],
                "feature_modules": [{"id": "people", "name": "人员管理", "description": "人员列表", "priority": "must"}],
                "pages": [{"id": "people_list", "name": "人员列表", "path": "/", "module_id": "people", "description": "唯一页面"}],
                "data_sources": [{"id": "people_source", "name": "人员数据", "type": "database", "entities": ["Person"], "description": "人员信息"}],
                "business_flows": [{"id": "browse_people", "name": "浏览人员", "steps": ["打开列表"]}],
                "acceptance_criteria": ["列表可以展示人员信息"],
                "assumptions": [],
            },
        )

        self.assertEqual([role["id"] for role in spec["user_roles"]], ["user"])
        self.assertEqual([page["id"] for page in spec["pages"]], ["people_list"])
        self.assertNotIn("login_page", [page["id"] for page in spec["pages"]])

    def test_clear_requirement_waits_for_spec_confirmation(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": clear_clarification(spec),
                },
            ) as analyzer:
                result = requirements(
                    {
                        "request": "创建一个库存管理系统",
                        "workspace": workspace,
                        "timeline": [],
                    }
                )

        analyzer.assert_called_once_with("创建一个库存管理系统", existing_spec=None)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "requirement_spec_confirmation",
        )
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_confirmed_requirement_spec_continues_to_planning(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        with tempfile.TemporaryDirectory() as workspace:
            result = requirements(
                {
                    "request": "正确，继续规划",
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification"]["status"], "clear")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "confirmed",
        )

    def test_generated_spec_requires_confirmation_after_clarification(self) -> None:
        existing_spec = create_requirement_spec("创建一个库存管理系统")
        existing_spec.update(
            {
                "clarification_status": "requires_user_input",
                "confirmation_status": "pending_user_input",
            }
        )
        completed_spec = {**existing_spec}

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": completed_spec,
                    "clarification": clear_clarification(completed_spec),
                },
            ):
                result = requirements(
                    {
                        "request": "已补充角色、页面、数据源和验收要求",
                        "workspace": workspace,
                        "requirement_spec": existing_spec,
                        "timeline": [],
                    }
                )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"], "requirement_spec_confirmation"
        )
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_clarification_answer_with_confirmation_words_is_not_short_circuited(self) -> None:
        existing_spec = create_requirement_spec("创建一个库存管理系统")
        existing_spec.update(
            {
                "clarification_status": "requires_user_input",
                "confirmation_status": "pending_user_input",
            }
        )
        completed_spec = {**existing_spec}

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": completed_spec,
                    "clarification": clear_clarification(completed_spec),
                },
            ) as analyzer:
                result = requirements(
                    {
                        "request": "已选：数据库；其他补充：确认需要 PostgreSQL",
                        "workspace": workspace,
                        "requirement_spec": existing_spec,
                        "timeline": [],
                    }
                )

        analyzer.assert_called_once()
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_confirmation_ignores_question_text_negative_words(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        continuation_message = "\n".join(
            [
                "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
                "",
                "原始需求：",
                "创建一个库存管理系统",
                "",
                "用户补充确认：",
                "- 需求确认：请确认已生成的需求文档是否正确。如果正确，请回复“正确，继续规划”；如果需要修改，请直接写出要调整的应用信息、角色、功能、页面、数据源、流程或验收标准。",
                "  回答：正确，继续规划",
            ]
        )

        with tempfile.TemporaryDirectory() as workspace:
            result = requirements(
                {
                    "request": continuation_message,
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "confirmed",
        )

    def test_confirmation_synchronizes_user_edited_markdown_before_continuing(
        self,
    ) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            state = {"workspace": workspace}
            markdown_path = Path(write_requirement_spec_document(state, spec))
            edited_markdown = markdown_path.read_text(encoding="utf-8").replace(
                spec["app_info"]["name"],
                "仓储管理应用",
            )
            markdown_path.write_text(edited_markdown, encoding="utf-8")
            synchronized = {
                **spec,
                "app_info": {**spec["app_info"], "name": "仓储管理应用"},
            }

            with patch(
                "app.graph.nodes.requirements.sync_requirement_spec_from_markdown",
                return_value=synchronized,
            ) as synchronizer:
                result = requirements(
                    {
                        "request": "正确，继续规划",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "requirement_spec_path": str(markdown_path),
                        "timeline": [],
                    }
                )

            internal_json = json.loads(
                Path(result["requirement_spec_json_path"]).read_text(encoding="utf-8")
            )
            preserved_markdown = markdown_path.read_text(encoding="utf-8")

        synchronizer.assert_called_once_with(spec, edited_markdown)
        self.assertEqual(result["requirement_spec"]["app_info"]["name"], "仓储管理应用")
        self.assertEqual(internal_json["app_info"]["name"], "仓储管理应用")
        self.assertEqual(preserved_markdown, edited_markdown)


if __name__ == "__main__":
    unittest.main()
