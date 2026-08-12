from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import ANY, patch

from app.graph.nodes.planning import project_planning as run_project_planning
from app.services.entity_definitions import plan_data_sources
from app.services.project_plan import (
    apply_project_plan_datasource_policy,
    apply_project_plan_feedback,
    create_project_plan,
)
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import write_project_plan_document
from tests.entity_design_test_utils import confirm_entity_designs


def project_planning(state: dict) -> dict:
    """为节点测试写入合法应用配置，避免绕过正式工作区边界。"""

    workspace = Path(str(state["workspace"]))
    source_type = "database"
    spec = state.get("requirement_spec")
    if isinstance(spec, dict) and isinstance(spec.get("data_sources"), list):
        sources = [item for item in spec["data_sources"] if isinstance(item, dict)]
        candidate = str(sources[0].get("type") or "") if sources else ""
        if candidate in {"database", "static"}:
            source_type = candidate
    config_dir = workspace / ".xcodeagent"
    config_dir.mkdir(parents=True, exist_ok=True)
    application_config: dict = {"datasource": {"type": source_type}}
    if source_type == "database":
        # 创建应用填写了数据库连接时，实体默认数据源为 database。
        application_config["datasource"]["db"] = {
            "useBuiltin": False,
            "plantMode": {
                "domain": "127.0.0.1",
                "port": 3306,
                "userName": "root",
                "pwd": "test",
                "schema": "demo",
            },
        }
    (config_dir / "application.json").write_text(
        json.dumps(application_config, ensure_ascii=False),
        encoding="utf-8",
    )
    return run_project_planning(state)


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

        planner.assert_called_once_with(spec, on_token=ANY)
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
                    "workflow_scope": "application_planning",
                    "user_interaction_submission": True,
                    "requirement_spec": spec,
                    "project_plan": plan,
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification"]["status"], "clear")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")

    def test_application_planning_recovery_text_cannot_confirm_plan(self) -> None:
        """创建规划的继续文案没有结构化提交时不得确认 ProjectPlan。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        plan = create_project_plan(spec)
        plan["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                side_effect=AssertionError("只读恢复不应重新生成计划。"),
            ) as planner:
                result = project_planning(
                    {
                        "request": "请从上次保存的规划状态继续执行。",
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "user_interaction_submission": False,
                        "requirement_spec": spec,
                        "project_plan": plan,
                        "timeline": [],
                    }
                )

        planner.assert_not_called()
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_generated_project_plan_blocks_unknown_response_schema(self) -> None:
        """首次计划生成后必须拦截 Endpoint 引用的未知响应 Schema。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        invalid_plan = create_project_plan(spec)
        endpoint = invalid_plan["api_contracts"][0]["endpoints"][0]
        endpoint["response_schema_ref"] = "MissingListResponse"

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.graph.nodes.planning.plan_project_with_chat_model",
                    return_value=invalid_plan,
                ),
                patch(
                    "app.graph.nodes.planning.revise_project_plan_with_chat_model",
                    return_value=invalid_plan,
                ),
            ):
                result = project_planning(
                    {
                        "request": "创建一个库存管理系统",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "timeline": [],
                    }
                )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "project_plan_dependency_validation_error",
        )
        self.assertIn(
            f"Endpoint {endpoint['id']} references unknown schema MissingListResponse.",
            result["clarification"]["errors"],
        )
        self.assertEqual(
            result["project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_project_plan_confirmation_revalidates_unknown_response_schema(self) -> None:
        """用户确认前必须再次校验 API Schema，不能把无效计划标记为已确认。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        invalid_plan = create_project_plan(spec)
        endpoint = invalid_plan["api_contracts"][0]["endpoints"][0]
        endpoint["response_schema_ref"] = "MissingListResponse"

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.planning.revise_project_plan_with_chat_model",
                return_value=invalid_plan,
            ):
                result = project_planning(
                    {
                        "request": "正确，继续",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "project_plan": invalid_plan,
                        "timeline": [],
                    }
                )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "project_plan_dependency_validation_error",
        )
        self.assertIn(
            f"Endpoint {endpoint['id']} references unknown schema MissingListResponse.",
            result["clarification"]["errors"],
        )
        self.assertEqual(
            result["project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

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

    def test_project_plan_feedback_updates_database_type_without_task_inputs(self) -> None:
        plan = {
            "frontend_pages": [
                {
                    "pageId": "personnel_list_page",
                    "name": "人员列表页",
                    "path": "/personnel",
                    "module_id": "personnel",
                    "data_dependencies": [],
                    "permissions": ["admin"],
                }
            ],
            "entities": [
                {
                    "id": "Personnel",
                    "name": "Personnel",
                    "fields": [],
                    "data_source": "static",
                }
            ],
            "api_contracts": [
                {
                    "id": "personnel_source_api",
                    "entity_ids": ["Personnel"],
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
            "static",
        )
        updated = confirm_entity_designs(updated, source_type="static")

        page = updated["frontend_pages"][0]
        dependency = updated["page_data_dependencies"][0]
        self.assertEqual(page["pageId"], "personnel_list_page")
        self.assertEqual(dependency["data_source_ids"], ["无"])
        self.assertEqual(dependency["api_contract_ids"], ["无"])
        self.assertEqual(dependency["endpoint_dependencies"], [])
        self.assertEqual(plan_data_sources(updated)[0]["type"], "static")
        self.assertNotIn("task_inputs", updated)

    def test_project_plan_revision_feedback_updates_database_type_only(self) -> None:
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
        existing_plan["entities"] = [
            {
                "id": "Personnel",
                "name": "Personnel",
                "fields": [],
                "data_source": "static",
            }
        ]
        existing_plan["api_contracts"] = [
            {
                "id": "personnel_source_api",
                "data_source_id": "static",
                "entity_ids": ["Personnel"],
                "resource": "Personnel",
                "base_path": "/api/personnel",
                "schemas": {
                    "Personnel": {"type": "object"},
                    "PersonnelListOutput": {"type": "object"},
                },
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
        self.assertEqual(dependency["data_source_ids"], [])
        self.assertEqual(dependency["api_contract_ids"], [])
        self.assertEqual(dependency["endpoint_dependencies"], [])
        self.assertEqual(plan_data_sources(result["project_plan"]), [])
        result_plan = confirm_entity_designs(result["project_plan"], source_type="static")
        result_plan = apply_project_plan_datasource_policy(result_plan)
        self.assertEqual(plan_data_sources(result_plan)[0]["type"], "static")
        self.assertNotIn("database", result_plan["architecture"]["backend_tech_stack"])
        self.assertNotIn("cache", result_plan["architecture"]["backend_tech_stack"])
        self.assertNotIn("task_inputs", result["project_plan"])

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
            ).replace("类型 database", "类型 static")
            markdown_path.write_text(edited_markdown, encoding="utf-8")
            synchronized = {
                **plan,
                "app": {**plan["app"], "name": "仓储计划应用"},
                "entities": [
                    entity
                    for entity in plan["entities"]
                ],
            }
            synchronized = confirm_entity_designs(synchronized, source_type="static")

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

        synchronizer.assert_called_once_with(
            plan,
            spec,
            edited_markdown,
        )
        self.assertEqual(result["project_plan"]["app"]["name"], "仓储计划应用")
        self.assertTrue(
            all(
                source["type"] == "static"
                for source in plan_data_sources(result["project_plan"])
            )
        )
        self.assertEqual(internal_json["app"]["name"], "仓储计划应用")
        self.assertIn("仓储计划应用", preserved_markdown)
        self.assertIn("实体详细设计", preserved_markdown)
        self.assertIn("状态：已确认", preserved_markdown)


if __name__ == "__main__":
    unittest.main()
