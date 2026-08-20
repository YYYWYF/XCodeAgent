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
from app.workspace.spec_documents import (
    load_requirement_spec_json,
    write_requirement_spec_document,
    write_requirement_spec_draft_document,
)


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
                "entities": [
                    {
                        "id": "Person",
                        "name": "人员",
                        "description": "人员信息",
                        "fields": [{"label": "姓名", "description": "人员姓名。"}],
                    }
                ],
                "business_flows": [{"id": "browse_people", "name": "浏览人员", "steps": ["打开列表"]}],
                "acceptance_criteria": ["列表可以展示人员信息"],
            },
        )

        self.assertEqual([role["id"] for role in spec["user_roles"]], ["user"])
        self.assertEqual([page["pageId"] for page in spec["pages"]], ["people_list"])
        self.assertNotIn("login_page", [page["pageId"] for page in spec["pages"]])
        self.assertNotIn("assumptions", spec)

    def test_revision_keeps_formal_document_until_confirmation(self) -> None:
        """需求修订先停在分析确认态，不能覆盖上一版正式文档。"""

        old_spec = create_requirement_spec(
            "创建个人喜好应用，使用一个喜好列表页。",
            agent_spec={
                "app_info": {
                    "name": "个人喜好",
                    "summary": "通过喜好列表统一管理个人喜好。",
                },
                "pages": [
                    {
                        "pageId": "preference_list",
                        "name": "喜好列表页",
                        "path": "/preferences",
                        "module_id": "preferences",
                        "description": "统一管理全部喜好。",
                    }
                ],
            },
        )
        old_spec["confirmation_status"] = "confirmed"
        revised_spec = create_requirement_spec(
            "不要喜好列表页，改成奶茶喜好页和零食喜好页。",
            existing_spec=old_spec,
            agent_spec={
                "app_info": {
                    "name": "个人喜好",
                    "summary": "分别在奶茶喜好页和零食喜好页管理两类固定喜好。",
                },
                "user_roles": old_spec["user_roles"],
                "feature_modules": old_spec["feature_modules"],
                "pages": [
                    {
                        "pageId": "milk_tea_preferences",
                        "name": "奶茶喜好页",
                        "path": "/milk-tea-preferences",
                        "module_id": "preferences",
                        "description": "管理奶茶喜好。",
                    },
                    {
                        "pageId": "snack_preferences",
                        "name": "零食喜好页",
                        "path": "/snack-preferences",
                        "module_id": "preferences",
                        "description": "管理零食喜好。",
                    },
                ],
                "business_flows": old_spec["business_flows"],
            },
            authoritative_agent_spec=True,
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace)
            old_path = write_requirement_spec_document(
                {"workspace": workspace},
                old_spec,
            )
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": revised_spec,
                    "clarification": clear_clarification(revised_spec),
                },
            ) as analyzer:
                result = requirements_node(
                    {
                        "request": "不要喜好列表页，改成奶茶喜好页和零食喜好页。",
                        "workspace": workspace,
                        "requirement_spec": old_spec,
                        "requirement_spec_path": old_path,
                        "timeline": [],
                    }
                )
            formal_markdown = Path(old_path).read_text(encoding="utf-8")
            formal_json = load_requirement_spec_json(Path(old_path).with_suffix(".json"))
            draft_markdown = Path(result["requirement_spec_path"]).read_text(encoding="utf-8")
            draft_json = load_requirement_spec_json(result["requirement_spec_json_path"])

        analyzer.assert_called_once_with(
            "不要喜好列表页，改成奶茶喜好页和零食喜好页。",
            existing_spec=old_spec,
            datasource_type="database",
            clarification_round=0,
            on_token=ANY,
        )
        self.assertEqual(result["status"], "requires_user_input")
        self.assertFalse(result["requirements_confirmed"])
        self.assertTrue(result["requirement_spec_path"].endswith("drafts/specs/requirement-spec.md"))
        self.assertEqual(
            [page["name"] for page in result["requirement_spec"]["pages"]],
            ["奶茶喜好页", "零食喜好页"],
        )
        self.assertEqual(
            [page["name"] for page in formal_json["pages"]],
            ["喜好列表页"],
        )
        self.assertIn("喜好列表页", formal_markdown)
        self.assertNotIn("奶茶喜好页", formal_markdown)
        self.assertIn("奶茶喜好页", draft_markdown)
        self.assertEqual(
            [page["name"] for page in draft_json["pages"]],
            ["奶茶喜好页", "零食喜好页"],
        )

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
                markdown_path = Path(workspace) / ".xcodeagent/drafts/specs/requirement-spec.md"
                formal_path = Path(workspace) / ".xcodeagent/specs/requirement-spec.md"
                draft_markdown_exists = markdown_path.exists()
                draft_json_exists = Path(result["requirement_spec_json_path"]).is_file()
                formal_exists = formal_path.exists()

        analyzer.assert_called_once_with(
            "创建一个库存管理系统",
            existing_spec=None,
            datasource_type="database",
            clarification_round=0,
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
        self.assertFalse(result["requirements_confirmed"])
        self.assertEqual(
            Path(result["requirement_spec_path"]).resolve(),
            markdown_path.resolve(),
        )
        self.assertTrue(draft_json_exists)
        self.assertTrue(draft_markdown_exists)
        self.assertFalse(formal_exists)

    def test_generic_completeness_ask_user_becomes_artifact_confirmation(self) -> None:
        """模型的泛化完整性确认不能阻塞在第二个 ask_user 问题上。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": {
                        "mode": "ask_user_question",
                        "status": "requires_user_input",
                        "questions": [
                            {
                                "header": "需求确认",
                                "question": "请确认需求已完整，无需进一步澄清",
                                "type": "yesno",
                            }
                        ],
                    },
                },
            ):
                result = requirements(
                    {
                        "request": "创建一个库存管理系统",
                        "workspace": workspace,
                        "timeline": [],
                    }
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
        self.assertNotIn(
            "请确认需求已完整",
            result["clarification"]["questions"][0]["question"],
        )

    def test_substantive_ask_user_question_still_blocks_for_answer(self) -> None:
        """具体缺口仍然必须先回答，不能被完整性过滤误放行。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": {
                        "mode": "ask_user_question",
                        "status": "requires_user_input",
                        "questions": [
                            {
                                "header": "用户角色",
                                "question": "哪些角色会使用这个系统？",
                                "type": "text",
                            }
                        ],
                    },
                },
            ):
                result = requirements(
                    {
                        "request": "创建一个库存管理系统",
                        "workspace": workspace,
                        "timeline": [],
                    }
                )
            draft_markdown = Path(workspace) / ".xcodeagent/drafts/specs/requirement-spec.md"
            draft_json = Path(workspace) / ".xcodeagent/drafts/specs/requirement-spec.json"

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "ask_user_question")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_input",
        )
        self.assertEqual(
            result["clarification"]["questions"][0]["question"],
            "哪些角色会使用这个系统？",
        )
        self.assertEqual(result["requirement_spec_path"], "")
        self.assertEqual(result["requirement_spec_json_path"], "")
        self.assertFalse(draft_markdown.exists())
        self.assertFalse(draft_json.exists())

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

        self.assertTrue(result["requirement_spec"]["entities"])
        self.assertNotIn("data_sources", result["requirement_spec"])

    def test_confirmed_requirement_spec_continues_to_planning(self) -> None:
        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            result = requirements(
                {
                    "request": "正确，继续规划",
                    "workspace": workspace,
                    "workflow_scope": "application_planning",
                    "application_planning_interaction": {"action": "confirm"},
                    "requirement_spec": spec,
                    "timeline": [],
                }
            )
            markdown_exists = Path(result["requirement_spec_path"]).is_file()

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["requirements_confirmed"])
        self.assertEqual(result["clarification"]["status"], "clear")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "confirmed",
        )
        self.assertTrue(markdown_exists)

    def test_application_planning_revise_does_not_follow_confirmation_word(self) -> None:
        """结构化 revise 即使文本含确认词，也必须重新分析并再次等待确认。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        revised = {
            **spec,
            "app_info": {**spec["app_info"], "name": "库存运营中心"},
        }
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": revised,
                    "clarification": clear_clarification(revised),
                },
            ) as analyzer:
                result = requirements(
                    {
                        "request": "确认后增加审批页面",
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "application_planning_interaction": {
                            "action": "revise",
                            "request": "确认后增加审批页面",
                        },
                        "requirement_spec": spec,
                        "timeline": [],
                    }
                )

        analyzer.assert_called_once()
        self.assertEqual(analyzer.call_args.args[0], "确认后增加审批页面")
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_application_planning_confirm_does_not_need_confirmation_word(self) -> None:
        """结构化 confirm 不依赖请求文本中的确认关键词即可通过需求门。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                side_effect=AssertionError("显式确认不应重新调用需求模型。"),
            ) as analyzer:
                result = requirements(
                    {
                        "request": "只保留首页",
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "application_planning_interaction": {
                            "action": "confirm",
                            "request": "只保留首页",
                        },
                        "requirement_spec": spec,
                        "timeline": [],
                    }
                )

        analyzer.assert_not_called()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["requirements_confirmed"])

    def test_confirmed_requirement_spec_without_revision_context_exits_before_llm(self) -> None:
        """已确认需求在无结构化交互和修订游标时直接早退，不再分析或重写。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "confirmed"
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                side_effect=AssertionError("已确认需求不应重新调用需求模型。"),
            ) as analyzer:
                result = requirements(
                    {
                        "request": "请从上次保存的规划状态继续执行。",
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "application_planning_interaction": {},
                        "requirement_spec": spec,
                        "timeline": [],
                    }
                )

        analyzer.assert_not_called()
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["requirements_confirmed"])

    def test_confirmed_requirement_spec_with_revision_cursor_does_not_exit_early(self) -> None:
        """已确认需求带修订游标时必须进入新一轮分析，不能误走早退。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "confirmed"
        revised = {
            **spec,
            "app_info": {**spec["app_info"], "name": "库存运营中心"},
        }
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": revised,
                    "clarification": clear_clarification(revised),
                },
            ) as analyzer:
                result = requirements(
                    {
                        "request": "增加审批页面",
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "application_planning_interaction": {},
                        "design_change_generation_target": "requirements",
                        "design_change_generation_request": "增加审批页面",
                        "requirement_spec": spec,
                        "timeline": [],
                    }
                )

        analyzer.assert_called_once()
        self.assertEqual(result["status"], "requires_user_input")

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
                        "application_planning_interaction": {},
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
            markdown_path = Path(write_requirement_spec_draft_document(state, spec))
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
            markdown = Path(result["requirement_spec_path"]).read_text(encoding="utf-8")
            internal_json = json.loads(
                Path(result["requirement_spec_json_path"]).read_text(encoding="utf-8")
            )

        self.assertIn("# 仓储管理应用需求 Spec", markdown)
        self.assertIn("- 状态：已确认", markdown)
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
            "entities": spec["entities"],
        }

        with tempfile.TemporaryDirectory() as workspace:
            state = {"workspace": workspace}
            write_requirement_spec_draft_document(state, spec)
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
                (Path(workspace) / ".xcodeagent/drafts/specs/requirement-spec.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertIn("# 仓储运营中心需求 Spec", markdown)
        self.assertIn("仓储总览", markdown)
        self.assertEqual(saved["requirementSpec"]["entities"], spec["entities"])
        self.assertEqual(saved["requirementSpec"]["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(internal_json["confirmation_status"], "pending_user_confirmation")
        self.assertEqual(saved["artifact"]["content"], markdown)

    def test_summary_editor_can_modify_entity_display_fields(self) -> None:
        """需求确认编辑器可修改实体展示信息，稳定 id 与隐藏结构仍保留。"""

        spec = create_requirement_spec("创建一个库存管理系统")
        spec["confirmation_status"] = "pending_user_confirmation"
        edited = {
            **spec,
            "entities": ["Warehouse"],
        }

        with tempfile.TemporaryDirectory() as workspace:
            state = {"workspace": workspace}
            write_requirement_spec_draft_document(state, spec)
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

        saved_entities = saved["requirementSpec"]["entities"]
        self.assertEqual([entity["id"] for entity in saved_entities], ["Warehouse"])
        # 需求层实体字段仍只保留展示信息，不生成字段名与类型。
        self.assertEqual(saved_entities[0]["fields"], [])

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

    def test_third_clarification_batch_is_shown_before_final_consolidation(self) -> None:
        """第三轮问题仍需让用户回答，不能在问题刚生成时丢弃。"""

        existing_spec = create_requirement_spec("创建一个库存管理系统")
        existing_spec.update(
            {
                "clarification_status": "requires_user_input",
                "confirmation_status": "pending_user_input",
            }
        )
        questions = [
            {
                "id": f"gap_{index}",
                "header": f"缺口{index}",
                "question": f"请补充第{index}项业务信息。",
                "type": "text",
            }
            for index in range(1, 6)
        ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": {**existing_spec},
                    "clarification": {
                        "mode": "ask_user_question",
                        "status": "requires_user_input",
                        "question_schema": "gemini_cli.ask_user.v1",
                        "questions": questions,
                        "assumptions": [],
                    },
                },
            ) as analyzer:
                result = requirements(
                    {
                        "request": "已回答第二轮需求澄清",
                        "workspace": workspace,
                        "requirement_spec": existing_spec,
                        "requirements_clarification_round": 2,
                        "timeline": [],
                    }
                )

        analyzer.assert_called_once()
        self.assertEqual(analyzer.call_args.kwargs["clarification_round"], 2)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "ask_user_question")
        self.assertEqual(len(result["clarification"]["questions"]), 5)
        self.assertEqual(result["requirements_clarification_round"], 3)
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_input",
        )

    def test_after_three_clarification_rounds_forces_requirement_confirmation(self) -> None:
        """第三轮回答后的最终合并不得再开启第四轮追问。"""

        existing_spec = create_requirement_spec("创建一个库存管理系统")
        existing_spec.update(
            {
                "clarification_status": "requires_user_input",
                "confirmation_status": "pending_user_input",
            }
        )
        questions = [
            {
                "id": f"gap_{index}",
                "header": f"缺口{index}",
                "question": f"请补充第{index}项业务信息。",
                "type": "text",
            }
            for index in range(1, 6)
        ]

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": {**existing_spec},
                    "clarification": {
                        "mode": "ask_user_question",
                        "status": "requires_user_input",
                        "question_schema": "gemini_cli.ask_user.v1",
                        "questions": questions,
                        "assumptions": [],
                    },
                },
            ) as analyzer:
                result = requirements(
                    {
                        "request": "已回答第三轮需求澄清",
                        "workspace": workspace,
                        "requirement_spec": existing_spec,
                        "requirements_clarification_round": 3,
                        "timeline": [],
                    }
                )

        analyzer.assert_called_once()
        self.assertEqual(analyzer.call_args.kwargs["clarification_round"], 3)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "requirement_spec_confirmation",
        )
        self.assertTrue(result["clarification"]["clarification_limit_reached"])
        self.assertEqual(result["requirements_clarification_round"], 3)
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
            markdown_path = Path(write_requirement_spec_draft_document(state, spec))
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
            formal_markdown = Path(result["requirement_spec_path"]).read_text(encoding="utf-8")
            draft_exists = markdown_path.exists()

        synchronizer.assert_called_once_with(
            spec,
            edited_markdown,
            datasource_type="database",
        )
        self.assertEqual(result["requirement_spec"]["app_info"]["name"], "仓储管理应用")
        self.assertEqual(internal_json["app_info"]["name"], "仓储管理应用")
        self.assertEqual(
            formal_markdown,
            edited_markdown.replace("- 状态：待确认", "- 状态：已确认"),
        )
        self.assertIn("- 状态：已确认", formal_markdown)
        self.assertFalse(draft_exists)
        self.assertNotIn("data_sources", internal_json)
        self.assertNotIn("数据源清单", formal_markdown)
        self.assertIn("仓储管理应用", formal_markdown)
        self.assertIn("实体清单", formal_markdown)


if __name__ == "__main__":
    unittest.main()
