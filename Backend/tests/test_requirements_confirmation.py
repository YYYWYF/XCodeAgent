from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import ANY, patch

from app.graph.nodes.requirements import requirements as requirements_node
from app.services.requirement_spec import (
    SaveRequirementSpecDraftRequest,
    create_requirement_spec,
    merge_clarification_answers_into_spec,
    save_requirement_spec_draft,
)
from app.tools.ask_user import clear_clarification
from app.workspace.spec_documents import write_requirement_spec_document


def _write_application_config(workspace: str, datasource_type: str = "database") -> None:
    """为需求节点单测创建最小的 application.json 权威数据源配置。"""

    application_dir = Path(workspace) / ".xcodeagent"
    application_dir.mkdir(parents=True, exist_ok=True)
    (application_dir / "application.json").write_text(
        json.dumps(
            {"schemaVersion": 2, "datasource": {"type": datasource_type}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def requirements(state: dict) -> dict:
    """为需求节点测试补齐应用配置后执行真实 requirements 节点。"""

    _write_application_config(str(state["workspace"]))
    return requirements_node(state)


class RequirementsConfirmationTests(unittest.TestCase):
    def test_default_acceptance_criteria_exclude_xcodeagent_workflow(self) -> None:
        """默认产品验收只能描述应用结果，不能泄漏生成器交付门禁。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        combined = "\n".join(spec["acceptance_criteria"])

        for forbidden in ("本地预览地址", "前端运行错误", "集成测试", "质量门禁", "用户验收"):
            self.assertNotIn(forbidden, combined)
        self.assertIn("主要业务流程", combined)

    def test_model_workflow_acceptance_is_removed(self) -> None:
        """模型混入工作流门禁时只保留生成应用本身的产品标准。"""

        spec = create_requirement_spec(
            "创建一个库存管理系统",
            agent_spec={
                "acceptance_criteria": [
                    "集成测试和质量门禁通过后才进入用户验收。",
                    "仓库管理员可以完成库存查询和调整。",
                ]
            },
        )

        self.assertEqual(
            spec["acceptance_criteria"],
            ["仓库管理员可以完成库存查询和调整。"],
        )

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
                "pages": [{"pageId": "people_list", "name": "人员列表", "path": "/", "module_id": "people", "description": "唯一页面"}],
                "data_sources": [{"id": "people_source", "name": "人员数据", "type": "database", "entities": ["Person"], "description": "人员信息"}],
                "business_flows": [{"id": "browse_people", "name": "浏览人员", "steps": ["打开列表"]}],
                "acceptance_criteria": ["列表可以展示人员信息"],
            },
        )

        self.assertEqual([role["id"] for role in spec["user_roles"]], ["user"])
        self.assertEqual([page["pageId"] for page in spec["pages"]], ["people_list"])
        self.assertNotIn("login_page", [page["pageId"] for page in spec["pages"]])
        self.assertNotIn("assumptions", spec)

    def test_clarification_answers_merge_into_requirement_spec_fields(self) -> None:
        spec = create_requirement_spec("创建一个业务管理系统")
        request = "\n".join(
            [
                "- 用户角色：系统需要哪些用户角色？",
                "  回答：已选：仓库管理员、采购员",
                "- 页面清单：需要哪些页面？",
                "  回答：已选：库存列表、入库登记",
            ]
        )

        merged = merge_clarification_answers_into_spec(spec, request)

        self.assertEqual(
            [role["name"] for role in merged["user_roles"]],
            ["仓库管理员", "采购员"],
        )
        self.assertEqual(
            [page["name"] for page in merged["pages"]],
            ["库存列表", "入库登记"],
        )

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
                markdown = Path(result["requirement_spec_path"]).read_text(
                    encoding="utf-8"
                )

        analyzer.assert_called_once_with(
            "创建一个库存管理系统",
            existing_spec=None,
            datasource_type="database",
            on_token=ANY,
        )
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "requirement_spec_confirmation",
        )
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_confirmation",
        )
        self.assertEqual(result["requirement_spec"]["clarification_questions"], [])
        self.assertEqual(result["requirement_spec"]["clarification_status"], "clear")
        self.assertEqual(result["clarification"]["status"], "requires_user_input")
        self.assertEqual(len(result["clarification"]["questions"]), 1)
        self.assertIn("## 待确认问题\n\n- 暂无", markdown)
        self.assertNotIn("请确认已生成的需求文档是否正确", markdown)

    def test_requirement_type_comes_from_static_application_config(self) -> None:
        """即使模型返回 database，Static 应用也必须投影为 static。"""

        spec = create_requirement_spec("创建一个库存管理系统", datasource_type="database")
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace, datasource_type="static")
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": clear_clarification(spec),
                },
            ):
                result = requirements_node(
                    {
                        "request": "创建一个库存管理系统",
                        "workspace": workspace,
                        "timeline": [],
                    }
                )

        self.assertTrue(result["requirement_spec"]["data_sources"])
        self.assertTrue(
            all(source["type"] == "static" for source in result["requirement_spec"]["data_sources"])
        )

    def test_confirmed_requirement_spec_continues_to_planning(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            result = requirements(
                {
                    "request": "正确，继续规划",
                    "workspace": workspace,
                    "workflow_scope": "application_planning",
                    "user_interaction_submission": True,
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

    def test_application_planning_recovery_text_cannot_confirm_requirement(self) -> None:
        """创建规划的恢复文案即使含确认关键词，也必须继续停在需求门禁。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                side_effect=AssertionError("只读恢复不应重新分析需求。"),
            ) as analyzer:
                result = requirements(
                    {
                        "request": "完成需求确认和项目规划",
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "user_interaction_submission": False,
                        "requirement_spec": spec,
                        "timeline": [],
                    }
                )

        analyzer.assert_not_called()
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_confirmation_feedback_does_not_block_or_persist_confirmation(self) -> None:
        """兼容反馈字段不得参与本轮确认语义，也不得保存为正式确认意见。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            result = requirements(
                {
                    "request": "正确，继续规划",
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "requirement_spec_feedback": "建议后续补充移动端说明。",
                    "timeline": [],
                }
            )
            internal_json = json.loads(
                Path(result["requirement_spec_json_path"]).read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "completed")
        self.assertNotIn("confirmation_feedback", result["requirement_spec"])
        self.assertNotIn("confirmation_feedback", internal_json)
        self.assertEqual(result["requirement_spec_feedback"], "")

    def test_stale_feedback_does_not_trigger_revision_when_current_answer_confirms(self) -> None:
        """旧轮次残留的需求意见不能覆盖本轮明确确认。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                side_effect=AssertionError("确认通过时不应重新生成需求文档。"),
            ) as analyzer:
                result = requirements(
                    {
                        "request": "正确，继续规划",
                        "workspace": workspace,
                        "requirement_spec": spec,
                        "requirement_spec_feedback": "请增加移动端页面。",
                        "timeline": [],
                    }
                )

        analyzer.assert_not_called()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "confirmed",
        )
        self.assertEqual(result["requirement_spec_feedback"], "")

    def test_summary_editor_updates_json_and_markdown_before_confirmation(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        edited = {
            **spec,
            "app_info": {**spec["app_info"], "name": "仓储管理应用"},
            "pages": [
                {
                    **spec["pages"][0],
                    "name": "库存总览",
                    "description": "查看全部仓库的库存与预警。",
                    "components": ["库存预警卡片"],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as workspace:
            state = {"workspace": workspace}
            markdown_path = Path(write_requirement_spec_document(state, spec))
            result = requirements(
                {
                    "request": "正确，继续规划",
                    "workspace": workspace,
                    "requirement_spec": spec,
                    "requirement_spec_path": str(markdown_path),
                    "edited_requirement_spec": edited,
                    "timeline": [],
                }
            )
            markdown = markdown_path.read_text(encoding="utf-8")
            internal_json = json.loads(
                Path(result["requirement_spec_json_path"]).read_text(encoding="utf-8")
            )

        self.assertIn("# 仓储管理应用需求 Spec", markdown)
        self.assertIn("库存总览", markdown)
        self.assertIn("组件：库存预警卡片", markdown)
        self.assertEqual(internal_json["app_info"]["name"], "仓储管理应用")
        self.assertEqual(internal_json["pages"][0]["name"], "库存总览")
        self.assertEqual(internal_json["confirmation_status"], "confirmed")
        self.assertEqual(result["edited_requirement_spec"], {})
        self.assertEqual(result["requirement_spec"]["clarification_questions"], [])
        self.assertEqual(result["requirement_spec"]["clarification_status"], "clear")

    def test_summary_editor_can_save_markdown_without_confirming(self) -> None:
        """退出编辑时应同步 Markdown/JSON，但不能越过需求确认门禁。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        edited = {
            **spec,
            "app_info": {**spec["app_info"], "name": "仓储运营中心"},
            "pages": [
                {
                    **spec["pages"][0],
                    "name": "仓储总览",
                    "description": "查看仓储运营指标。",
                }
            ],
            "data_sources": [
                {
                    **spec["data_sources"][0],
                    "id": "tampered_source_id",
                    "type": "mock",
                    "entities": ["Warehouse"],
                },
                *spec["data_sources"][1:],
            ],
        }

        with tempfile.TemporaryDirectory() as workspace:
            state = {"workspace": workspace}
            write_requirement_spec_document(state, spec)
            _write_application_config(workspace)
            saved = save_requirement_spec_draft(
                SaveRequirementSpecDraftRequest.model_validate(
                    {
                        "action": "save",
                        "workspaceRoot": workspace,
                        "spec": edited,
                    }
                )
            )
            markdown = Path(saved["artifact"]["path"]).read_text(encoding="utf-8")
            internal_json = json.loads(
                (Path(workspace) / ".xcodeagent/specs/requirement-spec.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertIn("# 仓储运营中心需求 Spec", markdown)
        self.assertIn("仓储总览", markdown)
        self.assertEqual(
            saved["requirementSpec"]["data_sources"][0]["id"],
            spec["data_sources"][0]["id"],
        )
        self.assertEqual(saved["requirementSpec"]["data_sources"][0]["type"], "database")
        self.assertEqual(
            saved["requirementSpec"]["data_sources"][0]["entities"],
            spec["data_sources"][0]["entities"],
        )
        self.assertEqual(saved["requirementSpec"]["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(internal_json["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(saved["artifact"]["content"], markdown)

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
        self.assertEqual(result["requirement_spec"]["clarification_questions"], [])
        self.assertEqual(result["requirement_spec"]["clarification_status"], "clear")

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

    def test_repeated_optional_role_and_page_questions_are_not_asked_again(self) -> None:
        existing_spec = create_requirement_spec("创建一个库存管理系统")
        existing_spec.update(
            {
                "clarification_status": "requires_user_input",
                "confirmation_status": "pending_user_input",
            }
        )
        completed_spec = {
            **existing_spec,
            "user_roles": [
                {"id": "role_1", "name": "仓库管理员", "description": "管理库存。"}
            ],
            "pages": [
                {
                    "id": "page_1",
                    "name": "库存列表",
                    "path": "/inventory",
                    "module_id": "inventory",
                    "description": "查看库存。",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": completed_spec,
                    "clarification": {
                        "mode": "ask_user_question",
                        "status": "requires_user_input",
                        "question_schema": "gemini_cli.ask_user.v1",
                        "questions": [
                            {
                                "id": "other_roles",
                                "header": "用户角色",
                                "question": "是否还有其他用户角色？",
                                "type": "yesno",
                            },
                            {
                                "id": "other_pages",
                                "header": "页面清单",
                                "question": "是否还有其他页面？",
                                "type": "yesno",
                            },
                        ],
                        "assumptions": [],
                    },
                },
            ):
                result = requirements(
                    {
                        "request": "已选：仓库管理员；页面：库存列表",
                        "workspace": workspace,
                        "requirement_spec": existing_spec,
                        "timeline": [],
                    }
                )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "requirement_spec_confirmation",
        )
        self.assertEqual(len(result["clarification"]["questions"]), 1)
        self.assertEqual(result["requirement_spec"]["clarification_questions"], [])
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_confirmation_ignores_question_text_negative_words(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
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
                "data_sources": [
                    {**spec["data_sources"][0], "type": "mock"},
                    *spec["data_sources"][1:],
                ],
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

        synchronizer.assert_called_once_with(
            spec,
            edited_markdown,
            datasource_type="database",
        )
        self.assertEqual(result["requirement_spec"]["app_info"]["name"], "仓储管理应用")
        self.assertEqual(internal_json["app_info"]["name"], "仓储管理应用")
        self.assertEqual(internal_json["data_sources"][0]["type"], "database")
        self.assertEqual(preserved_markdown, edited_markdown)
        self.assertNotIn("数据源清单", preserved_markdown)
        self.assertIn("仓储管理应用", preserved_markdown)


if __name__ == "__main__":
    unittest.main()
