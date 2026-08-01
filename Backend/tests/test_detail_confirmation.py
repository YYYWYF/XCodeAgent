from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agents.main.page_designer import design_endpoint_with_chat_model
from app.graph.nodes.planning import detail_confirmation
from app.services.page_detail_plan import (
    compose_endpoint_detail_from_decision,
    create_endpoint_detail_plan,
    create_page_detail_plan,
    extract_endpoint_detail_context,
    extract_page_detail_context,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec


def _endpoint_context_for_dependency(project_plan: dict, dependency: dict) -> dict:
    """按页面依赖反查契约并构造 EndpointDetail 上下文。"""

    endpoint_id = dependency["endpoint_id"]
    contract_id = next(
        contract["id"]
        for contract in project_plan["api_contracts"]
        if any(endpoint.get("id") == endpoint_id for endpoint in contract.get("endpoints", []))
    )
    return extract_endpoint_detail_context(project_plan, contract_id, endpoint_id)


class DetailConfirmationTests(unittest.TestCase):
    def test_endpoint_designer_generates_decision_then_composes_detail(self) -> None:
        """endpoint 模型只生成决策对象，正式详情由确定性第二步完成。"""

        project_plan = create_project_plan(create_requirement_spec("创建人员管理系统"))
        page_context = extract_page_detail_context(
            project_plan,
            project_plan["frontend_pages"][0]["pageId"],
        )
        endpoint_context = _endpoint_context_for_dependency(
            project_plan,
            page_context["references"]["endpoint_dependencies"][0],
        )
        decision = {
            "data_origin": {
                "source_type": "mock",
                "effective_source": {"kind": "mock", "description": "内存数据"},
                "field_mappings": [],
                "differences": [],
                "database_operations": [],
                "notes": [],
            },
            "operation_semantics": {
                "operation_kind": "read",
                "target_cardinality": "many",
                "selector": {"source": "query", "fields": []},
                "transaction_required": False,
                "zero_match_behavior": "返回空集合",
                "multiple_match_behavior": "返回契约约定的列表",
                "success_status_code": 200,
                "side_effect": "none",
            },
            "risks": [],
        }
        model = MagicMock()
        model.bind.return_value.invoke.return_value = SimpleNamespace(
            content=json.dumps(decision, ensure_ascii=False)
        )
        settings = SimpleNamespace(model_name="test-model", default_max_tokens=4096)

        with patch(
            "app.agents.main.page_designer.Settings.from_env",
            return_value=settings,
        ), patch(
            "app.agents.main.page_designer.create_chat_model",
            return_value=model,
        ):
            detail = design_endpoint_with_chat_model(project_plan, endpoint_context)

        prompt = model.bind.return_value.invoke.call_args.args[0]
        self.assertIn("step 1 of EndpointDetail design", prompt)
        self.assertNotIn("processing_logic", prompt)
        self.assertEqual(detail["endpoint_decision"]["operation_semantics"], decision["operation_semantics"])
        self.assertEqual(detail["design_stage"], "complete")
        self.assertTrue(detail["processing_logic"])

    def test_endpoint_decision_stops_before_detail_composition_when_unresolved(self) -> None:
        """第一步仍有未决差异时，不应提前生成处理逻辑和验收承诺。"""

        project_plan = create_project_plan(create_requirement_spec("创建人员管理系统"))
        page_context = extract_page_detail_context(
            project_plan,
            project_plan["frontend_pages"][0]["pageId"],
        )
        endpoint_context = _endpoint_context_for_dependency(
            project_plan,
            page_context["references"]["endpoint_dependencies"][0],
        )
        detail = compose_endpoint_detail_from_decision(
            project_plan,
            endpoint_context,
            {
                "data_origin": {
                    "source_type": "mysql_existing",
                    "effective_source": {
                        "kind": "mysql_existing",
                        "database": "xcode",
                        "tables": ["user"],
                    },
                    "field_mappings": [],
                    "differences": [
                        {
                            "field": "user.id",
                            "expected": "唯一定位一条记录",
                            "actual": "唯一性尚未确认",
                            "resolution_kind": "needs_user_confirmation",
                            "operation_refs": [],
                            "backend_adaptation": None,
                        }
                    ],
                    "database_operations": [],
                    "notes": [],
                },
                "operation_semantics": {
                    "operation_kind": "delete",
                    "target_cardinality": "exactly_one",
                    "selector": {"source": "path", "fields": ["id"]},
                    "transaction_required": True,
                    "zero_match_behavior": "返回 404",
                    "multiple_match_behavior": "拒绝执行并返回数据约束错误",
                    "success_status_code": 204,
                    "side_effect": "delete",
                },
                "risks": ["user.id 唯一性待确认"],
            },
        )

        self.assertEqual(detail["design_stage"], "needs_user_confirmation")
        self.assertEqual(detail["processing_logic"], [])
        self.assertEqual(detail["acceptance_criteria"], [])

    def test_endpoint_detail_is_composed_from_one_closed_decision(self) -> None:
        """闭合决策的处理逻辑与验收标准必须由同一基数和结果规则投影。"""

        project_plan = create_project_plan(create_requirement_spec("创建人员管理系统"))
        page_context = extract_page_detail_context(
            project_plan,
            project_plan["frontend_pages"][0]["pageId"],
        )
        endpoint_context = _endpoint_context_for_dependency(
            project_plan,
            page_context["references"]["endpoint_dependencies"][0],
        )
        decision = {
            "data_origin": {
                "source_type": "mock",
                "effective_source": {"kind": "mock", "description": "内存数据"},
                "field_mappings": [],
                "differences": [],
                "database_operations": [],
                "notes": [],
            },
            "operation_semantics": {
                "operation_kind": "delete",
                "target_cardinality": "exactly_one",
                "selector": {"source": "path", "fields": ["id"]},
                "transaction_required": True,
                "zero_match_behavior": "返回 404",
                "multiple_match_behavior": "拒绝执行",
                "success_status_code": 204,
                "side_effect": "delete",
            },
            "risks": [],
        }

        detail = compose_endpoint_detail_from_decision(
            project_plan,
            endpoint_context,
            decision,
        )

        self.assertEqual(detail["design_stage"], "complete")
        self.assertTrue(any("恰好一个目标" in item for item in detail["processing_logic"]))
        self.assertTrue(any("exactly_one" in item for item in detail["acceptance_criteria"]))
        self.assertTrue(any("拒绝执行" in item for item in detail["processing_logic"]))

    def test_model_json_overrides_endpoint_detail_fields(self) -> None:
        """EndpointDetail 应接受模型正式字段覆盖。"""

        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_context = extract_page_detail_context(
            project_plan,
            "inventory_management_list_page",
        )
        page_detail = create_page_detail_plan(
            project_plan,
            page_context,
        )
        dependency = page_detail["endpoint_dependencies"][0]
        endpoint_context = _endpoint_context_for_dependency(project_plan, dependency)
        endpoint_detail = create_endpoint_detail_plan(
            project_plan,
            endpoint_context,
            agent_detail_plan={"processing_logic": ["按 SKU 查询库存"]},
        )

        self.assertTrue(page_detail["api_dependencies"])
        self.assertEqual(endpoint_detail["processing_logic"], ["按 SKU 查询库存"])

    def test_page_spec_defaults_when_page_description_is_missing(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个人员管理系统")
        )
        project_plan["frontend_pages"] = [
            {
                "pageId": "people_list",
                "name": "人员列表",
                "path": "/people",
                "data_dependencies": [],
                "permissions": ["user"],
            }
        ]

        page_context = extract_page_detail_context(project_plan, "people_list")

        self.assertEqual(page_context["pageId"], "people_list")
        self.assertIn("人员列表", page_context["page_goal"])

    def test_page_detail_tolerates_api_contract_without_data_source_id(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        for contract in project_plan["api_contracts"]:
            contract.pop("data_source_id", None)

        page_context = extract_page_detail_context(
            project_plan,
            "inventory_management_list_page",
        )
        page_detail = create_page_detail_plan(project_plan, page_context)

        self.assertTrue(page_context["endpoint_contracts"])
        self.assertTrue(page_detail["api_dependencies"])

    def test_page_detail_tolerates_structured_layout_and_interactions(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_context = extract_page_detail_context(
            project_plan,
            "inventory_management_list_page",
        )

        page_detail = create_page_detail_plan(
            project_plan,
            page_context,
            agent_detail_plan={
                "component_structure": [
                    {
                        "area": "筛选区",
                        "purpose": "按库存状态筛选",
                        "components": ["搜索框"],
                    }
                ],
            },
        )
        self.assertEqual(page_detail["component_structure"][0]["area"], "筛选区")
        self.assertEqual(page_detail["component_structure"][0]["components"], ["搜索框"])

    def test_page_detail_only_carries_the_page_endpoint_dependencies(self) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        page_context = extract_page_detail_context(
            project_plan,
            "inventory_management_list_page",
        )
        page_context["references"]["endpoint_dependencies"] = [
            {
                "api_contract_id": "inventory_management_source_api",
                "endpoint_id": "inventory_management_source_api.list",
                "method": "GET",
                "url": "/api/inventory-management",
                "usage": "page_load",
                "required": True,
            }
        ]

        page_detail = create_page_detail_plan(project_plan, page_context)

        self.assertEqual(
            [item["endpoint_id"] for item in page_detail["api_dependencies"]],
            ["inventory_management_source_api.list"],
        )
        self.assertEqual(
            page_detail["endpoint_dependencies"],
            page_context["references"]["endpoint_dependencies"],
        )

    def test_page_detail_confirmation_generates_required_endpoint_details(self) -> None:
        """单页设计应先补齐缺失 EndpointDetail 并纳入同轮审核。"""

        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        with patch(
            "app.graph.nodes.planning.design_page_with_chat_model",
            side_effect=create_page_detail_plan,
        ) as page_designer, patch(
            "app.graph.nodes.planning.design_endpoint_with_chat_model",
            side_effect=lambda plan, context, _request: create_endpoint_detail_plan(
                plan,
                context,
            ),
        ) as endpoint_designer, patch(
            "app.graph.nodes.planning.prepare_endpoint_database_context",
            return_value={"status": "skipped", "message": "无需数据库上下文。"},
        ):
            selected_page = project_plan["frontend_pages"][1]
            result = detail_confirmation(
                {
                    "request": "开始页面详细设计",
                    "project_plan": project_plan,
                    "selectedPageId": selected_page["pageId"],
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "detail_review")
        self.assertEqual(
            [item["target_id"] for item in result["clarification"]["review"]["pages"]],
            [selected_page["pageId"]],
        )
        self.assertEqual(
            len(result["clarification"]["review"]["endpoints"]),
            endpoint_designer.call_count,
        )
        self.assertEqual(page_designer.call_count, 1)
        self.assertGreater(endpoint_designer.call_count, 0)
        page_contexts = [call.args[1] for call in page_designer.call_args_list]
        self.assertTrue(
            any(context["endpoint_detail_summaries"] for context in page_contexts)
        )
        self.assertEqual(
            result["pending_project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_page_detail_confirmation_reuses_existing_endpoint_detail(self) -> None:
        """页面设计应复用已设计 endpoint，并把未确认详情纳入同轮审核。"""

        project_plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        selected_page = project_plan["frontend_pages"][1]
        page_context = extract_page_detail_context(project_plan, selected_page["pageId"])
        endpoint_detail = create_endpoint_detail_plan(
            project_plan,
            _endpoint_context_for_dependency(
                project_plan,
                page_context["references"]["endpoint_dependencies"][0],
            ),
        )
        endpoint_detail["status"] = "pending_user_confirmation"
        endpoint_detail["approved"] = False
        project_plan["endpoint_detail_plans"] = [endpoint_detail]

        with patch(
            "app.graph.nodes.planning.design_page_with_chat_model",
            side_effect=create_page_detail_plan,
        ), patch(
            "app.graph.nodes.planning.design_endpoint_with_chat_model",
        ) as endpoint_designer:
            result = detail_confirmation(
                {
                    "request": "开始页面详细设计",
                    "project_plan": project_plan,
                    "selectedPageId": selected_page["pageId"],
                    "timeline": [],
                }
            )

        endpoint_designer.assert_not_called()
        self.assertEqual(len(result["clarification"]["review"]["endpoints"]), 1)

    def test_detail_review_applies_page_patch_and_confirms_once(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建库存系统"))
        page_context = extract_page_detail_context(
            project_plan,
            project_plan["frontend_pages"][0]["pageId"],
        )
        pending_plan = {
            **project_plan,
            "page_detail_plans": [create_page_detail_plan(project_plan, page_context)],
            "endpoint_detail_plans": [
                create_endpoint_detail_plan(
                    project_plan,
                    _endpoint_context_for_dependency(project_plan, dependency),
                )
                for dependency in page_context["references"]["endpoint_dependencies"]
            ],
            "confirmation_status": "pending_user_confirmation",
        }
        pageId = page_context["pageId"]

        result = detail_confirmation(
            {
                "request": "已整体确认设计",
                "project_plan": project_plan,
                "pending_project_plan": pending_plan,
                "detail_review_submission": {
                    "review_status": "confirmed",
                    "target_changes": [
                        {
                            "target_type": "page",
                            "target_id": pageId,
                            "changes": {
                                "page_goal": "快速查看库存并支持批量导出",
                                "interactions": ["搜索", "筛选", "批量导出"],
                            },
                        }
                    ],
                },
                "timeline": [],
            }
        )

        detail = result["project_plan"]["page_detail_plans"][0]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")
        self.assertEqual(detail["page_goal"], "快速查看库存并支持批量导出")
        self.assertEqual(detail["interactions"], ["搜索", "筛选", "批量导出"])

    def test_detail_confirmation_promotes_pending_plan_after_user_confirmation(
        self,
    ) -> None:
        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        pending_project_plan = {
            **project_plan,
            "page_detail_plans": [
                {
                    "id": "page_detail:inventory_management_list_page",
                    "pageId": "inventory_management_list_page",
                }
            ],
        }

        result = detail_confirmation(
            {
                "request": "正确，继续",
                "project_plan": project_plan,
                "pending_project_plan": pending_project_plan,
                "timeline": [],
            }
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["project_plan"]["confirmation_status"], "confirmed")
        self.assertEqual(
            result["project_plan"]["page_detail_plans"][0]["pageId"],
            "inventory_management_list_page",
        )


if __name__ == "__main__":
    unittest.main()
