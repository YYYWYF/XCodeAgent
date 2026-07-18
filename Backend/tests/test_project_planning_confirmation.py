from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from app.graph.nodes.planning import project_planning
from app.services.project_plan import apply_project_plan_feedback, create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import write_project_plan_document


class ProjectPlanningConfirmationTests(unittest.TestCase):
    def test_project_planning_waits_for_user_confirmation_after_generation(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(spec)

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                return_value=plan,
            ) as planner:
                result = project_planning(
                    {
                        "request": "创建一个库存管理系统",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "timeline": [],
                    }
                )

        planner.assert_called_once_with(spec)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "project_plan_confirmation")
        self.assertEqual(
            result["project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_project_planning_continues_after_user_confirms_plan(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(spec)

        with tempfile.TemporaryDirectory() as workspace:
            result = project_planning(
                {
                    "request": "正确，继续",
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "project_plan": plan,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification"]["status"], "clear")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")

    def test_project_planning_revision_uses_existing_plan_once(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        existing_plan = create_project_plan(spec)
        revised_plan = {
            **existing_plan,
            "frontend_pages": [existing_plan["frontend_pages"][1]],
        }

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                return_value=revised_plan,
            ) as planner:
                result = project_planning(
                    {
                        "request": "只保留库存列表页，删除其他页面",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "project_plan": existing_plan,
                        "timeline": [],
                    }
                )

        planner.assert_called_once()
        self.assertEqual(planner.call_args.kwargs["existing_plan"], existing_plan)
        self.assertIn(
            "只保留库存列表页",
            planner.call_args.args[0]["planning_adjustment_request"],
        )
        self.assertEqual(len(result["project_plan"]["frontend_pages"]), 1)

    def test_project_plan_feedback_updates_page_data_api_dependencies(self) -> None:
        plan = {
            "frontend_pages": [
                {
                    "id": "personnel_list_page",
                    "name": "人员列表页",
                    "path": "/personnel",
                    "module_id": "personnel",
                    "data_dependencies": [],
                    "permissions": ["admin"],
                }
            ],
            "data_sources": [
                {
                    "id": "personnel_source",
                    "name": "人员数据源",
                    "type": "mock",
                    "entities": ["Personnel"],
                    "schema_refs": ["Personnel"],
                }
            ],
            "api_contracts": [
                {
                    "id": "personnel_source_api",
                    "data_source_id": "personnel_source",
                    "resource": "Personnel",
                    "base_path": "/api/personnel",
                    "schemas": {"Personnel": {"type": "object"}},
                    "endpoints": [
                        {
                            "id": "personnel_source_api.list",
                            "method": "GET",
                            "path": "/api/personnel",
                            "summary": "查询人员列表",
                            "response_schema_ref": "PersonnelListOutput",
                        }
                    ],
                }
            ],
            "page_data_dependencies": [
                {
                    "pageId": "personnel_list_page",
                    "data_source_ids": ["无"],
                    "api_contract_ids": ["无"],
                    "endpoint_dependencies": [],
                }
            ],
            "task_inputs": {"frontend": [], "data_source": []},
            "architecture": {},
        }

        updated = apply_project_plan_feedback(
            plan,
            "回答：人员列表页依赖数据源/api/database",
        )

        page = updated["frontend_pages"][0]
        dependency = updated["page_data_dependencies"][0]
        self.assertEqual(page["data_dependencies"], ["personnel_source"])
        self.assertEqual(dependency["data_source_ids"], ["personnel_source"])
        self.assertEqual(dependency["api_contract_ids"], ["personnel_source_api"])
        self.assertEqual(
            dependency["endpoint_dependencies"][0]["endpoint_id"],
            "personnel_source_api.list",
        )
        self.assertEqual(updated["data_sources"][0]["type"], "database")
        self.assertEqual(
            updated["task_inputs"]["frontend"][0]["depends_on"],
            ["data_source:personnel_source"],
        )

    def test_project_plan_revision_applies_dependency_feedback_after_model(self) -> None:
        spec = create_requirement_spec("创建一个人员管理系统")
        existing_plan = create_project_plan(spec)
        existing_plan["frontend_pages"] = [
            {
                "pageId": "personnel_list_page",
                "name": "人员列表页",
                "path": "/personnel",
                "module_id": "personnel",
                "data_dependencies": [],
                "permissions": ["admin"],
            }
        ]
        existing_plan["data_sources"] = [
            {
                "id": "personnel_source",
                "name": "人员数据源",
                "type": "mock",
                "entities": ["Personnel"],
                "schema_refs": ["Personnel"],
            }
        ]
        existing_plan["api_contracts"] = [
            {
                "id": "personnel_source_api",
                "data_source_id": "personnel_source",
                "resource": "Personnel",
                "base_path": "/api/personnel",
                "schemas": {"Personnel": {"type": "object"}},
                "endpoints": [
                    {
                        "id": "personnel_source_api.list",
                        "method": "GET",
                        "path": "/api/personnel",
                        "summary": "查询人员列表",
                        "response_schema_ref": "PersonnelListOutput",
                    }
                ],
            }
        ]
        existing_plan["page_data_dependencies"] = [
            {
                "pageId": "personnel_list_page",
                "data_source_ids": [],
                "api_contract_ids": [],
                "endpoint_dependencies": [],
            }
        ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                return_value=existing_plan,
            ):
                result = project_planning(
                    {
                        "request": "回答：人员列表页依赖数据源/api/database",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "project_plan": existing_plan,
                        "timeline": [],
                    }
                )

        dependency = result["project_plan"]["page_data_dependencies"][0]
        self.assertEqual(dependency["data_source_ids"], ["personnel_source"])
        self.assertEqual(dependency["api_contract_ids"], ["personnel_source_api"])
        self.assertEqual(
            dependency["endpoint_dependencies"][0]["endpoint_id"],
            "personnel_source_api.list",
        )

    def test_project_plan_confirmation_ignores_question_text_negative_words(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(spec)
        continuation_message = "\n".join(
            [
                "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
                "",
                "原始需求：",
                "创建一个库存管理系统",
                "",
                "用户补充确认：",
                "- 计划确认：请确认已生成的项目规划书是否正确。如果正确，请回复“正确，继续”；如果需要调整，请直接写出要修改的架构、API、页面、数据源、权限或验收标准。",
                "  回答：正确，继续",
            ]
        )

        with tempfile.TemporaryDirectory() as workspace:
            result = project_planning(
                {
                    "request": continuation_message,
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "project_plan": plan,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")

    def test_confirmation_synchronizes_user_edited_plan_markdown(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(spec)
        plan["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            state = {"workspace": workspace}
            markdown_path = Path(write_project_plan_document(state, plan))
            edited_markdown = markdown_path.read_text(encoding="utf-8").replace(
                plan["app"]["name"],
                "仓储计划应用",
            )
            markdown_path.write_text(edited_markdown, encoding="utf-8")
            synchronized = {
                **plan,
                "app": {**plan["app"], "name": "仓储计划应用"},
            }

            with patch(
                "app.graph.nodes.planning.sync_project_plan_from_markdown",
                return_value=synchronized,
            ) as synchronizer:
                result = project_planning(
                    {
                        "request": "正确，继续",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "project_plan": plan,
                        "project_plan_path": str(markdown_path),
                        "timeline": [],
                    }
                )

            internal_json = json.loads(
                Path(result["project_plan_json_path"]).read_text(encoding="utf-8")
            )
            preserved_markdown = markdown_path.read_text(encoding="utf-8")

        synchronizer.assert_called_once_with(plan, spec, edited_markdown)
        self.assertEqual(result["project_plan"]["app"]["name"], "仓储计划应用")
        self.assertEqual(internal_json["app"]["name"], "仓储计划应用")
        self.assertEqual(preserved_markdown, edited_markdown)


if __name__ == "__main__":
    unittest.main()
