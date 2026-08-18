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
from app.services.requirement_spec import create_requirement_spec


class ProductPlanningRetryTests(unittest.TestCase):
    """验证 ProductPlan 校验失败由工作流内部修复，而不是交给用户重试。"""

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
                                "actionId": "open-detail",
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
                                "actionId": "open-current-detail",
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
                                "actionId": "open-detail",
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

    def test_technical_plan_retries_missing_business_action_implementations(self) -> None:
        """TechnicalPlan 漏业务绑定时应保留上游上下文自动修复并重新编译契约。"""

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
                                "actionId": "guide_list_page-search-guides",
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
                                    "actionId": "guide_list_page-search-guides",
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
            "artifact_type": "technical-plan",
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
                    "endpoints": [{"id": "guides.search", "method": "GET"}],
                }
            ],
            "data_sources": [],
        }
        valid_plan = deepcopy(invalid_plan)
        valid_plan["pages"][0]["references"]["action_implementations"] = [
            {
                "actionId": "guide_list_page-search-guides",
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
        ):
            update = project_planning(state)

        self.assertEqual(planner_mock.call_count, 2)
        retry_input = planner_mock.call_args_list[1].args[0]
        self.assertIs(retry_input["confirmed_product_plan"], product_plan)
        self.assertIn(
            "guide_list_page-search-guides",
            retry_input["planning_adjustment_request"],
        )
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
                        "user_interaction_submission": True,
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
                                "actionId": "open-detail",
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
                "user_interaction_submission": True,
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


if __name__ == "__main__":
    unittest.main()
