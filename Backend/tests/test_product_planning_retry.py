from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.graph.nodes.planning import project_planning
from app.graph.nodes.product_planning import product_planning
from app.graph.nodes.ui_confirmation import ui_confirmation
from app.services.page_implementation_contract import materialize_technical_plan_runtime
from app.services.product_plan import create_product_plan, validate_product_plan
from app.services.project_plan import create_technical_plan
from app.services.requirement_spec import create_requirement_spec


class ProductPlanningRetryTests(unittest.TestCase):
    """验证 ProductPlan 校验失败由工作流内部修复，而不是交给用户重试。"""

    def _technical_planning_state(self) -> dict:
        """构造已确认上游产物的最小 TechnicalPlan 测试状态。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        product_plan["confirmation_status"] = "confirmed"
        return {
            "workflow_scope": "application_planning",
            "requirement_spec": requirement_spec,
            "product_plan": product_plan,
            "ui_designs": {
                "confirmation_status": "confirmed",
                "pages": [{"pageId": page["pageId"]} for page in product_plan["pages"]],
            },
            "request": "",
        }

    @patch("app.graph.nodes.product_planning.write_product_plan_documents")
    @patch("app.graph.nodes.product_planning.plan_product_with_chat_model")
    def test_generation_retries_with_validation_errors(
        self,
        planner_mock,
        writer_mock,
    ) -> None:
        """首次生成不一致时应把错误回灌模型并自动再次生成。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        first_page, second_page = requirement_spec["pages"][:2]
        valid_plan = create_product_plan(
            requirement_spec,
            agent_plan={
                "pages": [
                    {
                        "pageId": first_page["pageId"],
                        "actions": [
                            {
                                "actionId": "open_detail",
                                "name": "查看详情",
                                "description": "进入详情页面。",
                                "requiresConfirmation": False,
                                "targetPageId": second_page["pageId"],
                            }
                        ],
                    }
                ]
            },
        )
        invalid_plan = {
            **valid_plan,
            "pages": [
                {
                    **valid_plan["pages"][0],
                    "navigation_targets": [],
                },
                *valid_plan["pages"][1:],
            ],
        }
        planner_mock.side_effect = [invalid_plan, valid_plan]
        writer_mock.return_value = ("product-plan.md", "product-plan.json")

        update = product_planning({"requirement_spec": requirement_spec, "request": ""})

        self.assertEqual(planner_mock.call_count, 2)
        self.assertEqual(update["status"], "requires_user_input")
        self.assertEqual(update["product_plan"], valid_plan)
        retry_feedback = planner_mock.call_args_list[1].kwargs["user_feedback"]
        self.assertIn("跳转操作未同步到 navigation_targets", retry_feedback)

    def test_same_page_navigation_is_closed_deterministically(self) -> None:
        """同页导航必须同步进 navigation_targets，避免归一化与校验规则冲突。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        page = requirement_spec["pages"][0]

        product_plan = create_product_plan(
            requirement_spec,
            agent_plan={
                "pages": [
                    {
                        "pageId": page["pageId"],
                        "actions": [
                            {
                                "actionId": "open_current_detail",
                                "name": "查看当前详情",
                                "description": "按当前对象进入详情页面。",
                                "requiresConfirmation": False,
                                "behavior": {
                                    "type": "navigation",
                                    "targetPageId": page["pageId"],
                                    "expectedResult": "展示当前对象的详情。",
                                },
                            }
                        ],
                    }
                ]
            },
        )

        self.assertEqual(
            product_plan["pages"][0]["navigation_targets"],
            [page["pageId"]],
        )
        self.assertEqual(validate_product_plan(product_plan, requirement_spec), [])

    @patch("app.graph.nodes.product_planning.plan_product_with_chat_model")
    def test_generation_retry_count_is_bounded(self, planner_mock) -> None:
        """连续返回无效计划时必须在固定次数后停止，禁止形成无限模型循环。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        first_page, second_page = requirement_spec["pages"][:2]
        invalid_plan = create_product_plan(
            requirement_spec,
            agent_plan={
                "pages": [
                    {
                        "pageId": first_page["pageId"],
                        "actions": [
                            {
                                "actionId": "open_detail",
                                "name": "查看详情",
                                "description": "进入详情页面。",
                                "requiresConfirmation": False,
                                "targetPageId": second_page["pageId"],
                            }
                        ],
                    }
                ]
            },
        )
        invalid_plan["pages"][0]["navigation_targets"] = []
        planner_mock.return_value = invalid_plan

        with self.assertRaisesRegex(ValueError, "自动修复达到上限"):
            product_planning({"requirement_spec": requirement_spec, "request": ""})

        self.assertEqual(planner_mock.call_count, 3)

    @patch("app.graph.nodes.product_planning.write_product_plan_documents")
    @patch("app.graph.nodes.product_planning.plan_product_with_chat_model")
    def test_missing_restricted_operation_requests_page_then_creates_action(
        self,
        planner_mock,
        writer_mock,
    ) -> None:
        """模型始终遗漏受限操作时，应转为页面归属澄清并确定性补齐 action。"""

        requirement_spec = create_requirement_spec(
            "人员管理",
            agent_spec={
                "pages": [
                    {
                        "pageId": "people",
                        "name": "人员列表",
                        "path": "/people",
                        "module_id": "people",
                        "description": "管理人员信息。",
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "restrictedPages": [],
                    "restrictedOperations": [
                        {
                            "name": "停用人员",
                            "description": "停用选定人员。",
                            "rationale": "停用会影响人员使用状态。",
                            "sourceRefs": ["业务描述"],
                        }
                    ],
                    "dataRules": [],
                },
            },
        )
        missing_operation_plan = create_product_plan(requirement_spec)
        planner_mock.return_value = missing_operation_plan
        writer_mock.return_value = ("product-plan.md", "product-plan.json")

        clarification_update = product_planning(
            {"requirement_spec": requirement_spec, "request": ""}
        )

        self.assertEqual(planner_mock.call_count, 3)
        self.assertEqual(
            clarification_update["clarification"]["mode"],
            "authorization_operation_action_resolution",
        )
        question = clarification_update["clarification"]["questions"][0]
        self.assertEqual(question["options"][0]["value"], "page:people")

        resolved_update = product_planning(
            {
                "workflow_scope": "application_planning",
                "requirement_spec": requirement_spec,
                "product_plan": clarification_update["product_plan"],
                "clarification": clarification_update["clarification"],
                "application_planning_interaction": {
                    "action": "answer",
                    "answers": {question["id"]: {"selected": ["page:people"]}},
                },
            }
        )

        resolved_plan = resolved_update["product_plan"]
        self.assertEqual(resolved_update["clarification"]["mode"], "requirement_document_confirmation")
        self.assertEqual(resolved_plan["pages"][0]["actions"][0]["name"], "停用人员")
        self.assertEqual(validate_product_plan(resolved_plan, requirement_spec), [])

    def test_technical_plan_retries_missing_business_action_binding(self) -> None:
        """缺少业务 action endpoint 实现时，TechnicalPlan 必须在同一轮自动修复。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        target_page = requirement_spec["pages"][0]
        product_plan = create_product_plan(
            requirement_spec,
            agent_plan={
                "pages": [
                    {
                        "pageId": target_page["pageId"],
                        "actions": [
                            {
                                "actionId": "guide_list_page_search_guides",
                                "name": "搜索指南",
                                "description": "按关键字查询指南。",
                                "requiresConfirmation": False,
                            }
                        ],
                    }
                ]
            },
        )
        product_plan["confirmation_status"] = "confirmed"
        ui_designs = {
            "confirmation_status": "confirmed",
            "pages": [
                {
                    "pageId": page["pageId"],
                    "bindings": {
                        "actions": (
                            [
                                {
                                    "actionId": "guide_list_page_search_guides",
                                    "controlIds": ["guide-search"],
                                }
                            ]
                            if page["pageId"] == target_page["pageId"]
                            else []
                        )
                    },
                }
                for page in product_plan["pages"]
            ],
        }
        invalid_plan = {
            **create_technical_plan(
                {**requirement_spec, "confirmed_product_plan": product_plan}
            ),
            "pages": [
                {
                    "pageId": page["pageId"],
                    "path": page["path"],
                    "references": {
                        "endpoint_dependencies": (
                            [{"endpoint_id": "guides.search"}]
                            if page["pageId"] == target_page["pageId"]
                            else []
                        ),
                        "action_implementations": [],
                    },
                }
                for page in product_plan["pages"]
            ],
            "api_contracts": [
                {
                    "id": "guides-api",
                    "entity_ids": [requirement_spec["entities"][0]["id"]],
                    "base_path": "/api/guides",
                    "authentication": {"required": False},
                    "schemas": {},
                    "endpoints": [{"id": "guides.search", "method": "GET"}],
                }
            ],
        }
        valid_plan = deepcopy(invalid_plan)
        valid_plan["pages"][0]["references"]["action_implementations"] = [
            {
                "actionId": "guide_list_page_search_guides",
                "endpointId": "guides.search",
            }
        ]
        state = {
            "workflow_scope": "application_planning",
            "requirement_spec": requirement_spec,
            "product_plan": product_plan,
            "ui_designs": ui_designs,
            "request": "",
        }

        with (
            patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                side_effect=[invalid_plan, valid_plan],
            ) as planner_mock,
            patch(
                "app.graph.nodes.planning.validate_project_plan_dependencies",
                return_value=[],
            ),
            patch(
                "app.graph.nodes.planning.validate_api_contract_consistency",
                return_value=[],
            ),
            patch(
                "app.graph.nodes.planning.validate_project_plan_datasource_policy",
                return_value=[],
            ),
            patch(
                "app.graph.nodes.planning.write_project_plan_document",
                return_value="project-plan.md",
            ),
            patch(
                "app.graph.nodes.planning.write_technical_plan_document",
                return_value=("technical-plan.md", "technical-plan.json"),
            ),
            patch(
                "app.graph.nodes.planning.edited_technical_plan_markdown",
                return_value=None,
            ),
        ):
            update = project_planning(state)

        self.assertEqual(planner_mock.call_count, 2)
        self.assertEqual(update["status"], "requires_user_input")
        self.assertEqual(update["clarification"]["mode"], "technical_plan_confirmation")
        self.assertNotIn("page_implementation_contracts", update["technical_plan"])
        runtime_plan = materialize_technical_plan_runtime(
            update["technical_plan"],
            requirement_spec,
            product_plan,
            ui_designs,
        )
        binding = runtime_plan["page_implementation_contracts"][0][
            "actionBindings"
        ][0]
        self.assertEqual(binding["endpointId"], "guides.search")

    def test_technical_plan_retries_raw_validation_error_then_confirms(self) -> None:
        """首次原始 JSON 校验失败时应在同一生成预算内修复并返回确认载荷。"""

        state = self._technical_planning_state()
        valid_plan = create_technical_plan(
            {
                **state["requirement_spec"],
                "confirmed_product_plan": state["product_plan"],
            }
        )
        with (
            patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                side_effect=[ValueError("entities[0].fields[0] 缺少 label"), valid_plan],
            ) as planner_mock,
            patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
            patch(
                "app.graph.nodes.planning.write_project_plan_document",
                return_value="technical-plan.md",
            ),
        ):
            update = project_planning(state)

        self.assertEqual(planner_mock.call_count, 2)
        self.assertEqual(update["clarification"]["mode"], "technical_plan_confirmation")
        self.assertEqual(update["technical_plan"]["artifact_type"], "technical-plan")

    def test_technical_plan_generation_error_is_retryable_after_three_attempts(self) -> None:
        """连续三次原始 JSON 非法时应停留在技术规划并且不持久化非法产物。"""

        state = self._technical_planning_state()
        with (
            patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                side_effect=ValueError("字段必须使用 type，禁止 semantic_type"),
            ) as planner_mock,
            patch("app.graph.nodes.planning.write_project_plan_document") as writer_mock,
        ):
            update = project_planning(state)

        self.assertEqual(planner_mock.call_count, 3)
        writer_mock.assert_not_called()
        self.assertEqual(update["phase"], "technical_planning")
        self.assertEqual(update["status"], "requires_user_input")
        self.assertEqual(update["clarification"]["mode"], "technical_plan_generation_error")
        self.assertEqual(update["clarification"]["questions"], [])
        self.assertNotIn("project_plan", update)
        self.assertNotIn("technical_plan", update)

    def test_ui_design_can_be_skipped_and_returns_completed(self) -> None:
        """用户明确跳过 UI 设计时应落盘 skipped Manifest 并直接放行技术规划。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        requirement_spec["confirmation_status"] = "confirmed"
        product_plan = create_product_plan(requirement_spec)
        product_plan["confirmation_status"] = "confirmed"

        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(
                ui_confirmation(
                    {
                        "workspace": directory,
                        "workflow_scope": "application_planning",
                        "application_planning_interaction": {"action": "ui_action"},
                        "requirement_spec": requirement_spec,
                        "product_plan": product_plan,
                        "ui_design_action": {"action": "skip"},
                    }
                )
            )

            persisted = json.loads(
                (
                    Path(directory)
                    / ".xcodeagent"
                    / "specs"
                    / "ui-designs.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["ui_designs"]["confirmation_status"], "skipped")
        self.assertTrue(result["clarification"]["skipped"])
        self.assertEqual(persisted["confirmation_status"], "skipped")

    def test_product_plan_confirm_action_wins_over_revision_words_in_request(self) -> None:
        """ProductPlan 的显式 confirm 即使请求含“只保留首页”也必须直接确认。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        product_plan["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.product_planning.plan_product_with_chat_model",
                side_effect=AssertionError("显式确认不应重新调用产品规划模型。"),
            ) as planner_mock:
                result = product_planning(
                    {
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "application_planning_interaction": {
                            "action": "confirm",
                            "request": "确认产品规划，只保留首页",
                        },
                        "request": "确认产品规划，只保留首页",
                        "requirement_spec": requirement_spec,
                        "product_plan": product_plan,
                    }
                )

        planner_mock.assert_not_called()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["product_plan"]["confirmation_status"], "confirmed")

    def test_product_plan_revise_action_wins_over_confirmation_words_in_request(self) -> None:
        """ProductPlan 的显式 revise 即使请求含确认词也必须重新生成待确认版本。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        product_plan["confirmation_status"] = "pending_user_confirmation"
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.product_planning.plan_product_with_chat_model",
                return_value=product_plan,
            ) as planner_mock:
                result = product_planning(
                    {
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "application_planning_interaction": {
                            "action": "revise",
                            "request": "确认后只保留首页",
                        },
                        "request": "确认后只保留首页",
                        "requirement_spec": requirement_spec,
                        "product_plan": product_plan,
                    }
                )

        planner_mock.assert_called_once()
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["product_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_product_plan_design_revision_without_interaction_keeps_server_request(self) -> None:
        """设计意图路由生成 ProductPlan 时必须消费服务端游标请求。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        product_plan["confirmation_status"] = "confirmed"
        with (
            tempfile.TemporaryDirectory() as workspace,
            patch(
                "app.graph.nodes.product_planning.plan_product_with_chat_model",
                return_value=product_plan,
            ) as planner_mock,
        ):
            result = product_planning(
                {
                    "workspace": workspace,
                    "workflow_scope": "application_planning",
                    "application_planning_interaction": {},
                    "design_change_generation_target": "product_planning",
                    "design_change_generation_request": "只保留首页",
                    "request": "只保留首页",
                    "requirement_spec": requirement_spec,
                    "product_plan": product_plan,
                }
            )

        self.assertEqual(
            planner_mock.call_args.kwargs["user_feedback"],
            "只保留首页",
        )
        self.assertEqual(result["status"], "requires_user_input")

    def test_technical_plan_confirm_action_wins_over_revision_words_in_request(self) -> None:
        """TechnicalPlan 的显式 confirm 不因请求中的页面调整词而重新生成。"""

        state = self._technical_planning_state()
        technical_plan = create_technical_plan(
            {
                **state["requirement_spec"],
                "confirmed_product_plan": state["product_plan"],
            }
        )
        technical_plan["confirmation_status"] = "pending_user_confirmation"
        state.update(
            {
                "technical_plan": technical_plan,
                "application_planning_interaction": {
                    "action": "confirm",
                    "request": "确认技术规划，只保留首页",
                },
                "request": "确认技术规划，只保留首页",
            }
        )
        with (
            patch(
                "app.graph.nodes.planning.plan_project_with_chat_model",
                side_effect=AssertionError("显式确认不应重新调用技术规划模型。"),
            ) as planner_mock,
            patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=[]),
            patch(
                "app.graph.nodes.planning.write_project_plan_document",
                return_value="technical-plan.md",
            ),
            patch(
                "app.graph.nodes.planning.write_technical_plan_document",
                return_value=("technical-plan.md", "technical-plan.json"),
            ),
            patch(
                "app.graph.nodes.planning.edited_technical_plan_markdown",
                return_value=None,
            ),
        ):
            result = project_planning(state)

        planner_mock.assert_not_called()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["technical_plan"]["confirmation_status"], "confirmed")

    @patch("app.graph.nodes.product_planning.plan_product_with_chat_model")
    def test_legacy_product_plan_is_not_migrated(self, planner_mock) -> None:
        """历史 ProductPlan 必须直接拒绝，禁止自动转换为当前结构。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        legacy_plan = create_product_plan(requirement_spec)
        legacy_plan["schema_version"] = "product-plan.v3"
        legacy_plan["frontend_pages"] = []

        with self.assertRaisesRegex(ValueError, "不读取或迁移历史 ProductPlan"):
            product_planning(
                {
                    "requirement_spec": requirement_spec,
                    "product_plan": legacy_plan,
                    "request": "确认产品规划，继续",
                }
            )

        planner_mock.assert_not_called()

    def test_legacy_product_plan_cannot_bypass_downstream_resume(self) -> None:
        """直接恢复 UI 或技术规划节点时也必须拒绝历史 ProductPlan。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        legacy_plan = create_product_plan(requirement_spec)
        legacy_plan["schema_version"] = "product-plan.v3"
        legacy_plan["frontend_pages"] = []
        base_state = {
            "requirement_spec": requirement_spec,
            "product_plan": legacy_plan,
            "workflow_scope": "application_planning",
        }

        with self.assertRaisesRegex(ValueError, "不读取或迁移历史 ProductPlan"):
            asyncio.run(ui_confirmation(base_state))
        with self.assertRaisesRegex(ValueError, "不读取或迁移历史 ProductPlan"):
            project_planning(base_state)

    @patch("app.graph.nodes.product_planning.write_product_plan_documents")
    @patch("app.graph.nodes.product_planning.plan_product_with_chat_model")
    def test_invalid_checkpoint_is_repaired_deterministically(
        self,
        planner_mock,
        writer_mock,
    ) -> None:
        """恢复旧 checkpoint 时应先闭合派生跳转，不要求用户重新发起运行。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        first_page, second_page = requirement_spec["pages"][:2]
        existing = create_product_plan(
            requirement_spec,
            agent_plan={
                "pages": [
                    {
                        "pageId": first_page["pageId"],
                        "actions": [
                            {
                                "actionId": "open_detail",
                                "name": "查看详情",
                                "description": "进入详情页面。",
                                "requiresConfirmation": False,
                                "targetPageId": second_page["pageId"],
                            }
                        ],
                    }
                ]
            },
        )
        existing["pages"][0]["navigation_targets"] = []
        writer_mock.return_value = ("product-plan.md", "product-plan.json")

        update = product_planning(
            {
                "requirement_spec": requirement_spec,
                "product_plan": existing,
                "request": "确认产品规划，继续",
                "application_planning_interaction": {"action": "confirm"},
            }
        )

        planner_mock.assert_not_called()
        self.assertEqual(update["status"], "requires_user_input")
        self.assertEqual(
            update["product_plan"]["pages"][0]["navigation_targets"],
            [second_page["pageId"]],
        )
        self.assertEqual(
            validate_product_plan(update["product_plan"], requirement_spec),
            [],
        )

    def test_product_plan_stays_in_draft_until_confirmation(self) -> None:
        """ProductPlan 待确认期间只写草稿，确认后才提升为正式产物。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.product_planning.plan_product_with_chat_model",
                return_value=create_product_plan(requirement_spec),
            ):
                pending = product_planning(
                    {
                        "workspace": workspace,
                        "requirement_spec": requirement_spec,
                    }
                )
            draft_markdown = Path(pending["product_plan_path"])
            draft_json = Path(pending["product_plan_json_path"])
            formal_markdown = Path(workspace) / ".xcodeagent/plans/product-plan.md"
            formal_json = Path(workspace) / ".xcodeagent/plans/product-plan.json"

            self.assertEqual(pending["status"], "requires_user_input")
            self.assertTrue(draft_markdown.as_posix().endswith("drafts/plans/product-plan.md"))
            self.assertTrue(draft_markdown.is_file())
            self.assertTrue(draft_json.is_file())
            self.assertFalse(formal_markdown.exists())
            self.assertFalse(formal_json.exists())

            confirmed = product_planning(
                {
                    "workspace": workspace,
                    "workflow_scope": "application_planning",
                    "application_planning_interaction": {"action": "confirm"},
                    "requirement_spec": requirement_spec,
                    "product_plan": pending["product_plan"],
                    "product_plan_path": pending["product_plan_path"],
                    "product_plan_json_path": pending["product_plan_json_path"],
                }
            )

            self.assertEqual(confirmed["status"], "completed")
            self.assertEqual(confirmed["product_plan"]["confirmation_status"], "confirmed")
            self.assertTrue(formal_markdown.is_file())
            self.assertTrue(formal_json.is_file())
            self.assertFalse(draft_markdown.exists())
            self.assertFalse(draft_json.exists())


if __name__ == "__main__":
    unittest.main()
