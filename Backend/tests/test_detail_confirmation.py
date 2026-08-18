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
from tests.entity_design_test_utils import confirm_entity_designs


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
    def test_entity_detail_confirmation_starts_with_data_source_selection(self) -> None:
        """选择实体目标时先停在数据源选择界面，不自动生成详情。"""

        project_plan = create_project_plan(create_requirement_spec("创建商品管理系统"))
        entity_id = project_plan["entities"][0]["id"]

        result = detail_confirmation(
            {
                "request": "开始实体详细设计",
                "project_plan": project_plan,
                "selected_entity_id": entity_id,
                "detail_target_type": "entity",
                "timeline": [],
            }
        )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["detail_selection"]["mode"], "entity_review")
        self.assertEqual(result["clarification"]["review"]["entities"], [])
        entity_design = result["clarification"]["review"]["summary"]["entityDesign"]
        self.assertEqual(entity_design["stage"], "data_source_selection")
        self.assertEqual(entity_design["entity_id"], entity_id)
        self.assertEqual(
            {option["value"] for option in entity_design["data_source_options"]},
            {"database", "external_api", "static"},
        )
        self.assertEqual(result["pending_project_plan"].get("entity_detail_plans", []), [])

    def test_entity_detail_confirmation_selects_data_source_then_review(self) -> None:
        """用户选择数据源后生成对应方案的实体设计并停在 review 确认门禁。"""

        project_plan = create_project_plan(create_requirement_spec("创建商品管理系统"))
        entity_id = project_plan["entities"][0]["id"]

        result = detail_confirmation(
            {
                "request": "开始实体详细设计",
                "project_plan": project_plan,
                "pending_project_plan": project_plan,
                "selected_entity_id": entity_id,
                "detail_target_type": "entity",
                "entity_design_action": {
                    "action": "select_data_source",
                    "entity_id": entity_id,
                    "data_source_type": "external_api",
                },
                "timeline": [],
            }
        )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["detail_selection"]["mode"], "entity_review")
        pending = result["pending_project_plan"]
        self.assertEqual(
            [detail["entity_id"] for detail in pending["entity_detail_plans"]],
            [entity_id],
        )
        detail = pending["entity_detail_plans"][0]
        self.assertEqual(detail["data_source_type"], "external_api")
        self.assertEqual(detail["design_stage"], "external_api_input")
        self.assertEqual(
            result["clarification"]["review"]["entities"][0]["entity_id"],
            entity_id,
        )
        entity_design = result["clarification"]["review"]["summary"]["entityDesign"]
        self.assertEqual(entity_design["stage"], "external_api_input")

    def test_entity_database_selection_waits_for_user_table_action(self) -> None:
        """数据库数据源选择后不预扫描、不推表结构，等待用户查询表并绑定。"""

        project_plan = create_project_plan(create_requirement_spec("创建商品管理系统"))
        entity_id = project_plan["entities"][0]["id"]

        result = detail_confirmation(
            {
                "request": "开始实体详细设计",
                "project_plan": project_plan,
                "pending_project_plan": project_plan,
                "selected_entity_id": entity_id,
                "detail_target_type": "entity",
                "entity_design_action": {
                    "action": "select_data_source",
                    "entity_id": entity_id,
                    "data_source_type": "database",
                },
                "timeline": [],
            }
        )

        self.assertEqual(result["status"], "requires_user_input")
        detail = result["pending_project_plan"]["entity_detail_plans"][0]
        self.assertEqual(detail["data_source_type"], "database")
        self.assertEqual(detail["design_stage"], "database_design")
        self.assertNotIn("database_design", detail)
        entity_design = result["clarification"]["review"]["summary"]["entityDesign"]
        self.assertEqual(entity_design["stage"], "database_design")
        self.assertNotIn("database_design", entity_design)

    def test_entity_ai_assist_returns_inline_suggestions_without_persisting(self) -> None:
        """AI 辅助动作只返回表单内建议，不写入实体详情。"""

        project_plan = create_project_plan(create_requirement_spec("创建商品管理系统"))
        entity_id = project_plan["entities"][0]["id"]
        suggestions = {
            "assist_type": "bindings",
            "suggestions": [
                {
                    "id": "bindings-0",
                    "label": "name → name",
                    "payload": {"entity_field": "name", "table_column": "name"},
                }
            ],
            "source": "ai",
            "note": "",
        }
        with patch(
            "app.graph.nodes.planning.entity_design_ai_suggestions",
            return_value=suggestions,
        ):
            result = detail_confirmation(
                {
                    "request": "生成绑定建议",
                    "project_plan": project_plan,
                    "pending_project_plan": project_plan,
                    "selected_entity_id": entity_id,
                    "detail_target_type": "entity",
                    "entity_design_action": {
                        "action": "ai_assist",
                        "entity_id": entity_id,
                        "assist_type": "bindings",
                        "context": {"table_columns": ["id", "name"]},
                    },
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        entity_design = result["clarification"]["review"]["summary"]["entityDesign"]
        self.assertEqual(entity_design["ai_suggestions"]["assist_type"], "bindings")
        self.assertEqual(
            entity_design["ai_suggestions"]["suggestions"][0]["label"],
            "name → name",
        )
        self.assertEqual(
            result["pending_project_plan"].get("entity_detail_plans", []),
            [],
        )

    def test_entity_single_card_submit_confirms_and_completes(self) -> None:
        """submit_entity_design 一次性写入完整设计并确认进入构建。"""

        project_plan = create_project_plan(create_requirement_spec("创建商品管理系统"))
        entity_id = project_plan["entities"][0]["id"]
        field_name = next(
            field["name"]
            for field in project_plan["entities"][0]["fields"]
            if field.get("name")
        )

        result = detail_confirmation(
            {
                "request": "确认实体设计",
                "project_plan": project_plan,
                "pending_project_plan": project_plan,
                "selected_entity_id": entity_id,
                "detail_target_type": "entity",
                "entity_design_action": {
                    "action": "submit_entity_design",
                    "entity_id": entity_id,
                    "data_source_type": "database",
                    "database_design": {
                        "matched_table": "products",
                        "bindings": [
                            {
                                "entity_field": field_name,
                                "table_column": field_name,
                                "rule": "same_name",
                            }
                        ],
                    },
                    "business_rules": [{"name": "编码唯一", "rule_type": "unique"}],
                    "acceptance_criteria": ["商品列表可查询"],
                },
                "timeline": [],
            }
        )

        self.assertEqual(result["status"], "completed")
        detail = result["project_plan"]["entity_detail_plans"][0]
        self.assertEqual(detail["status"], "confirmed")
        self.assertEqual(detail["approved"], True)
        self.assertEqual(detail["data_source_type"], "database")
        self.assertEqual(detail["database_design"]["matched_table"], "products")
        self.assertEqual(detail["database_design"]["bindings"][0]["entity_field"], field_name)

    def test_endpoint_designer_generates_decision_then_composes_detail(self) -> None:
        """endpoint 模型只生成决策对象，正式详情由确定性第二步完成。"""

        project_plan = create_project_plan(create_requirement_spec("创建人员管理系统"))
        project_plan = confirm_entity_designs(project_plan, source_type="external_api")
        page_context = extract_page_detail_context(
            project_plan,
            project_plan["frontend_pages"][0]["pageId"],
        )
        endpoint_context = _endpoint_context_for_dependency(
            project_plan,
            page_context["references"]["endpoint_dependencies"][0],
        )
        decision = {
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
        self.assertIn("endpoint behavior decision model", prompt)
        self.assertNotIn("processing_logic", prompt)
        self.assertIn("do not describe storage or source implementation", prompt)
        self.assertNotIn("data_origin", detail)
        self.assertEqual(detail["endpoint_decision"]["operation_semantics"], decision["operation_semantics"])
        self.assertEqual(detail["design_stage"], "complete")
        self.assertTrue(detail["processing_logic"])

    def test_endpoint_context_reads_confirmed_entity_design(self) -> None:
        """接口上下文应读取已确认实体设计摘要，且不再包含数据源设计字段。"""

        project_plan = create_project_plan(create_requirement_spec("创建人员管理系统"))
        project_plan = confirm_entity_designs(project_plan, source_type="external_api")
        page_context = extract_page_detail_context(
            project_plan,
            project_plan["frontend_pages"][0]["pageId"],
        )
        endpoint_context = _endpoint_context_for_dependency(
            project_plan,
            page_context["references"]["endpoint_dependencies"][0],
        )
        self.assertNotIn("data_source_id", endpoint_context)
        self.assertNotIn("data_source", endpoint_context)
        self.assertTrue(endpoint_context["bound_entities"])
        bound_entity = endpoint_context["bound_entities"][0]
        self.assertEqual(bound_entity["data_source_type"], "external_api")
        self.assertIsInstance(bound_entity["fields"], list)

        detail = compose_endpoint_detail_from_decision(
            project_plan,
            endpoint_context,
            {
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
            },
        )

        self.assertEqual(detail["design_stage"], "complete")
        self.assertNotIn("data_origin", detail)
        self.assertNotIn("data_source_id", detail)
        self.assertTrue(detail["processing_logic"])
        self.assertTrue(detail["acceptance_criteria"])

    def test_endpoint_detail_is_composed_from_one_closed_decision(self) -> None:
        """闭合决策的处理逻辑与验收标准必须由同一基数和结果规则投影。"""

        project_plan = create_project_plan(create_requirement_spec("创建人员管理系统"))
        project_plan = confirm_entity_designs(project_plan, source_type="external_api")
        page_context = extract_page_detail_context(
            project_plan,
            project_plan["frontend_pages"][0]["pageId"],
        )
        endpoint_context = _endpoint_context_for_dependency(
            project_plan,
            page_context["references"]["endpoint_dependencies"][0],
        )
        decision = {
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
        project_plan = confirm_entity_designs(project_plan, source_type="database")
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
        contract = project_plan["api_contracts"][0]
        contract_id = contract["id"]
        endpoint_id = contract["endpoints"][0]["id"]
        page_context["references"]["endpoint_dependencies"] = [
            {
                "api_contract_id": contract_id,
                "endpoint_id": endpoint_id,
                "method": "GET",
                "url": contract["base_path"],
                "usage": "page_load",
                "required": True,
            }
        ]

        page_detail = create_page_detail_plan(project_plan, page_context)

        self.assertEqual(
            [item["endpoint_id"] for item in page_detail["api_dependencies"]],
            [endpoint_id],
        )
        self.assertEqual(
            page_detail["endpoint_dependencies"],
            page_context["references"]["endpoint_dependencies"],
        )

    def test_page_selection_only_generates_required_endpoint_details(self) -> None:
        """选择页面应只补齐 EndpointDetail，不再调用 PageDetail 设计模型。"""

        project_plan = create_project_plan(
            create_requirement_spec("创建一个库存管理系统")
        )
        project_plan = confirm_entity_designs(project_plan, source_type="database")
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
            "app.graph.nodes.planning.write_project_plan_document",
            return_value="/tmp/project-plan.md",
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
        self.assertEqual(result["clarification"]["review"]["pages"], [])
        self.assertEqual(
            len(result["clarification"]["review"]["endpoints"]),
            endpoint_designer.call_count,
        )
        page_designer.assert_not_called()
        self.assertGreater(endpoint_designer.call_count, 0)
        self.assertEqual(
            result["pending_project_plan"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_page_selection_reuses_existing_endpoint_detail(self) -> None:
        """页面开发前应复用已有 endpoint，并把未确认详情纳入同轮审核。"""

        project_plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        project_plan = confirm_entity_designs(project_plan, source_type="database")
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
        ) as page_designer, patch(
            "app.graph.nodes.planning.design_endpoint_with_chat_model",
        ) as endpoint_designer, patch(
            "app.graph.nodes.planning.write_project_plan_document",
            return_value="/tmp/project-plan.md",
        ):
            result = detail_confirmation(
                {
                    "request": "开始页面详细设计",
                    "project_plan": project_plan,
                    "selectedPageId": selected_page["pageId"],
                    "timeline": [],
                }
            )

        endpoint_designer.assert_not_called()
        page_designer.assert_not_called()
        self.assertEqual(len(result["clarification"]["review"]["endpoints"]), 1)

    def test_detail_review_applies_page_patch_and_confirms_once(self) -> None:
        project_plan = create_project_plan(create_requirement_spec("创建库存系统"))
        project_plan = confirm_entity_designs(project_plan, source_type="static")
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

        with patch(
            "app.graph.nodes.planning.write_project_plan_document",
            return_value="/tmp/project-plan.md",
        ):
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

        with patch(
            "app.graph.nodes.planning.write_project_plan_document",
            return_value="/tmp/project-plan.md",
        ):
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

    def test_endpoint_detail_confirmation_gate_carries_missing_entities(self) -> None:
        """接口详细设计前置门禁载荷携带缺失实体的结构化列表。"""

        project_plan = create_project_plan(create_requirement_spec("创建商品管理系统"))
        contract = project_plan["api_contracts"][0]
        endpoint = contract["endpoints"][0]

        result = detail_confirmation(
            {
                "request": "开始接口详细设计",
                "project_plan": project_plan,
                "selected_api_contract_id": contract["id"],
                "selected_endpoint_id": endpoint["id"],
                "detail_target_type": "endpoint",
                "timeline": [],
            }
        )

        self.assertEqual(result["status"], "requires_user_input")
        clarification = result["clarification"]
        self.assertEqual(clarification["mode"], "entity_design_required")
        self.assertTrue(clarification["reason"])
        missing = clarification["missing_entities"]
        self.assertEqual(
            {item["entity_id"] for item in missing},
            set(contract.get("entity_ids") or []),
        )
        entity_by_id = {
            entity["id"]: entity for entity in project_plan["entities"]
        }
        for item in missing:
            self.assertEqual(
                item["entity_name"],
                entity_by_id[item["entity_id"]].get("name"),
            )


if __name__ == "__main__":
    unittest.main()
