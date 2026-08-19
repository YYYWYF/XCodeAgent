from __future__ import annotations

import json
import unittest
from copy import deepcopy

from app.agents.main.product_planner import (
    _product_plan_json_example,
    _product_planning_prompt,
)
from app.agents.main.planner import _technical_planning_prompt
from app.agents.main.requirements_analyzer import _requirements_prompt
from app.services.page_implementation_contract import (
    attach_page_implementation_contracts,
    materialize_technical_plan_runtime,
    validate_page_implementation_contracts,
)
from app.services.product_plan import (
    create_product_plan,
    validate_product_plan,
    validate_product_plan_model_output,
)
from app.services.requirement_spec import create_requirement_spec
from app.services.project_plan import create_technical_plan
from app.workspace.spec_documents import render_requirement_spec_markdown
from app.workspace.plan_documents import render_project_plan_markdown


class ProductTechnicalPlanningTests(unittest.TestCase):
    """验证 ProductPlan 与 PageImplementationContract 的核心确定性边界。"""

    def test_product_plan_preserves_requirement_page_set_and_actions(self) -> None:
        """产品规划必须保持需求页面集合，纯展示页可以没有伪造的查看操作。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)

        self.assertEqual(
            [page["pageId"] for page in product_plan["pages"]],
            [page["pageId"] for page in requirement_spec["pages"]],
        )
        self.assertTrue(all(page["actions"] == [] for page in product_plan["pages"]))
        self.assertEqual(validate_product_plan(product_plan, requirement_spec), [])

    def test_product_plan_v4_keeps_only_pages_and_closes_navigation(self) -> None:
        """v4 必须规范产品行为，并从导航操作闭合页面跳转。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        first_page, second_page = requirement_spec["pages"][:2]
        product_plan = create_product_plan(
            requirement_spec,
            agent_plan={
                "pages": [
                    {
                        "pageId": first_page["pageId"],
                        "information_items": [
                            {
                                "itemId": "inventory-summary",
                                "label": "库存摘要",
                                "description": "展示库存核心指标。",
                            }
                        ],
                        "actions": [
                            {
                                "actionId": "open-inventory",
                                "name": "打开库存列表",
                                "description": "进入库存列表页。",
                                "requiresConfirmation": False,
                                "targetPageId": second_page["pageId"],
                            }
                        ],
                    }
                ],
            },
        )

        page = product_plan["pages"][0]
        self.assertEqual(product_plan["schema_version"], "product-plan.v4")
        self.assertNotIn("frontend_pages", product_plan)
        self.assertEqual(page["information_items"][0]["itemId"], "inventory-summary")
        self.assertIsInstance(page["information_items"][0], dict)
        self.assertEqual(page["navigation_targets"], [second_page["pageId"]])
        self.assertEqual(page["actions"][0]["behavior"]["type"], "navigation")
        self.assertEqual(
            page["actions"][0]["behavior"]["targetPageId"],
            second_page["pageId"],
        )
        self.assertNotIn("assumptions", product_plan)
        self.assertNotIn("risks", product_plan)
        self.assertEqual(validate_product_plan(product_plan, requirement_spec), [])

    def test_product_plan_rejects_removed_product_guess_fields(self) -> None:
        """正式 ProductPlan 必须拒绝产品假设和产品风险字段。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        product_plan["assumptions"] = []
        product_plan["risks"] = []

        errors = validate_product_plan(product_plan, requirement_spec)

        self.assertTrue(any("产品假设或产品风险" in error for error in errors))

    def test_product_plan_filters_xcodeagent_workflow_acceptance(self) -> None:
        """ProductPlan 页面级和产品级验收都只能描述生成应用自身。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        page = requirement_spec["pages"][0]
        product_plan = create_product_plan(
            requirement_spec,
            agent_plan={
                "pages": [
                    {
                        "pageId": page["pageId"],
                        "acceptance_criteria": [
                            "质量门禁通过后进入用户验收。",
                            "用户可以查看业务概览。",
                        ],
                    }
                ],
                "product_acceptance_criteria": [
                    "集成测试通过后才交付。",
                    "目标角色可以完成主要业务流程。",
                ],
            },
        )

        self.assertEqual(
            product_plan["pages"][0]["acceptance_criteria"],
            ["用户可以查看业务概览。"],
        )
        self.assertEqual(
            product_plan["product_acceptance_criteria"],
            ["目标角色可以完成主要业务流程。"],
        )

    def test_product_prompt_defines_action_boundary_and_object_shapes(self) -> None:
        """产品规划提示必须拒绝被动浏览 action，并固定对象字段语义。"""

        prompt = _product_planning_prompt(create_requirement_spec("创建一个库存管理系统"))

        self.assertIn("Passive viewing, reading, browsing, scrolling", prompt)
        self.assertIn('"information_items": [', prompt)
        self.assertIn('"itemId":', prompt)
        self.assertIn("Complete JSON response example", prompt)
        self.assertIn("must contain exactly", prompt)
        self.assertNotIn('"frontend_pages":', prompt)
        self.assertIn("Do not return assumptions or product risks", prompt)
        self.assertIn("A display-only page may therefore have an empty actions list", prompt)
        self.assertIn("behavior.type is one of business, navigation, interface, external", prompt)
        self.assertIn("do not include data_sources", _requirements_prompt("创建库存系统"))
        self.assertIn("Do not return assumptions, product risks", _requirements_prompt("创建库存系统"))

    def test_technical_prompt_uses_current_four_part_contract(self) -> None:
        """TechnicalPlan 提示词必须使用三段架构、实体引用和新分页字段。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        prompt = _technical_planning_prompt(
            {**requirement_spec, "confirmed_product_plan": product_plan},
            None,
        )

        self.assertIn("architecture, entities, api_contracts, and pages", prompt)
        self.assertIn("entity_field_ref", prompt)
        self.assertIn("computed, aggregated, and transport properties may omit the mapping", prompt)
        self.assertIn("has exactly four same-level properties", prompt)
        self.assertIn("total, pageSize, current, and list", prompt)
        self.assertIn("Product goal context", prompt)
        self.assertIn("Role context", prompt)
        self.assertIn("Business-flow context", prompt)
        self.assertIn("Page context", prompt)
        self.assertIn("Business-action context", prompt)
        self.assertNotIn("engineering_design", prompt)
        self.assertNotIn('"resource"', prompt)

    def test_product_model_example_contains_every_page_and_no_duplicate_tree(self) -> None:
        """模型完整 JSON 示例必须展开全部页面，并且只保留 pages。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        example = json.loads(_product_plan_json_example(requirement_spec))

        self.assertEqual(
            [page["pageId"] for page in example["pages"]],
            [page["pageId"] for page in requirement_spec["pages"]],
        )
        self.assertEqual(
            set(example),
            {"app", "user_roles", "business_flows", "pages", "product_acceptance_criteria"},
        )
        self.assertNotIn("frontend_pages", example)

    def test_product_model_output_rejects_duplicate_page_tree(self) -> None:
        """模型原始 JSON 一旦生成 frontend_pages，必须在归一化前被格式校验拒绝。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        agent_plan = json.loads(_product_plan_json_example(requirement_spec))
        self.assertEqual(validate_product_plan_model_output(agent_plan, requirement_spec), [])
        agent_plan["frontend_pages"] = []

        errors = validate_product_plan_model_output(agent_plan, requirement_spec)

        self.assertTrue(any("frontend_pages" in error for error in errors))

    def test_requirement_confirmation_keeps_entities_and_hides_source_configuration(self) -> None:
        """需求确认保留业务实体，但不得包含技术数据源配置。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        markdown = render_requirement_spec_markdown(requirement_spec)

        self.assertTrue(requirement_spec["entities"])
        self.assertNotIn("data_sources", requirement_spec)
        self.assertNotIn("assumptions", requirement_spec)
        self.assertNotIn("数据源清单", markdown)
        self.assertNotIn("验收标准", markdown)
        self.assertNotIn("默认假设", markdown)

    def test_technical_plan_persists_only_developer_owned_fields(self) -> None:
        """当前 TechnicalPlan 只落盘开发新增事实，派生页面契约必须留在运行时。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        requirement_with_product = {
            **requirement_spec,
            "confirmed_product_plan": product_plan,
        }
        technical_plan = create_technical_plan(requirement_with_product)
        ui_designs = {
            "schema_version": "ui-manifest.v3",
            "pages": [
                {
                    "pageId": page["pageId"],
                    "code_path": f".xcodeagent/ui-design/pages/{page['pageId']}/index.tsx",
                    "code_sha256": "a" * 64,
                }
                for page in product_plan["pages"]
            ],
        }

        attached = attach_page_implementation_contracts(
            technical_plan,
            product_plan,
            ui_designs,
        )

        self.assertEqual(
            set(attached),
            {
                "artifact_type",
                "architecture",
                "entities",
                "api_contracts",
                "pages",
                "product_plan_sha256",
                "ui_designs_sha256",
            },
        )
        self.assertEqual(attached["artifact_type"], "technical-plan")
        self.assertNotIn("page_implementation_contracts", attached)
        self.assertNotIn("frontend_pages", attached)
        self.assertNotIn("requirements_overview", attached)
        self.assertNotIn("business_flows", attached)
        self.assertNotIn("acceptance_criteria", attached)
        self.assertNotIn("risks", attached)
        self.assertEqual(
            set(attached["architecture"]),
            {"frontend", "backend", "data"},
        )
        self.assertTrue(
            all(
                set(contract)
                == {
                    "id",
                    "entity_ids",
                    "base_path",
                    "authentication",
                    "schemas",
                    "endpoints",
                }
                for contract in attached["api_contracts"]
            )
        )
        self.assertTrue(
            all(
                set(endpoint)
                == {
                    "id",
                    "method",
                    "path",
                    "summary",
                    "parameters",
                    "request_schema_ref",
                    "response_schema_ref",
                    "error_codes",
                    "authentication",
                }
                for contract in attached["api_contracts"]
                for endpoint in contract["endpoints"]
            )
        )
        self.assertTrue(
            all(set(page) == {"pageId", "references"} for page in attached["pages"])
        )
        self.assertTrue(
            all(
                set(page["references"])
                == {"endpoint_dependencies", "action_implementations"}
                for page in attached["pages"]
            )
        )
        markdown = render_project_plan_markdown(attached)
        self.assertIn("业务实体", markdown)
        self.assertIn(f"`{attached['entities'][0]['id']}`", markdown)
        self.assertNotIn("需求概述", markdown)
        self.assertNotIn("业务流程", markdown)
        self.assertNotIn("风险与待细化点", markdown)
        self.assertNotIn("缓存策略", markdown)
        self.assertNotIn("测试策略", markdown)
        runtime = materialize_technical_plan_runtime(
            attached,
            requirement_spec,
            product_plan,
            ui_designs,
        )
        self.assertEqual(
            len(runtime["page_implementation_contracts"]),
            len(product_plan["pages"]),
        )
        self.assertEqual(
            validate_page_implementation_contracts(
                attached,
                product_plan,
                ui_designs,
            ),
            [],
        )

    def test_technical_plan_compiles_ui_and_endpoint_bindings_without_page_detail(self) -> None:
        """技术规划应直接编译页面实现契约，不产生 PageDetail 正文。"""

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
                                "actionId": "open-overview-filter",
                                "name": "打开筛选",
                                "description": "展开概览筛选条件。",
                                "requiresConfirmation": False,
                            }
                        ],
                    }
                ]
            },
        )
        first_page = product_plan["pages"][0]
        page_id = first_page["pageId"]
        action_id = first_page["actions"][0]["actionId"]
        technical_plan = {
            "artifact_type": "technical-plan",
            "confirmation_status": "pending_user_confirmation",
            "entities": deepcopy(requirement_spec["entities"]),
            "pages": [
                {
                    **first_page,
                    "references": {
                        "endpoint_dependencies": [{"endpoint_id": "inventory.list"}],
                        "permissions": first_page.get("allowed_roles", []),
                        "navigation_targets": [],
                        "action_implementations": [
                            {
                                "actionId": action_id,
                                "endpointId": "inventory.list",
                            }
                        ],
                    },
                }
            ],
            "api_contracts": [
                {
                    "id": "inventory-api",
                    "entity_ids": [requirement_spec["entities"][0]["id"]],
                    "endpoints": [
                        {
                            "id": "inventory.list",
                            "method": "GET",
                            "path": "/api/inventory",
                            "summary": "查询库存",
                        }
                    ],
                }
            ],
        }
        scoped_product_plan = {**product_plan, "pages": [first_page]}
        ui_designs = {
            "confirmation_status": "confirmed",
            "pages": [
                {
                    "pageId": page_id,
                    "code_path": ".xcodeagent/ui-design/pages/Inventory/index.tsx",
                    "code_sha256": "a" * 64,
                    "controls": [
                        {
                            "controlId": f"{action_id}-control",
                            "actionId": action_id,
                        }
                    ],
                }
            ],
        }

        attached = attach_page_implementation_contracts(
            technical_plan,
            scoped_product_plan,
            ui_designs,
        )
        runtime = materialize_technical_plan_runtime(
            attached,
            requirement_spec,
            scoped_product_plan,
            ui_designs,
        )

        self.assertNotIn("page_implementation_contracts", attached)
        self.assertEqual(
            runtime["page_implementation_contracts"][0]["requiredEndpointIds"],
            ["inventory.list"],
        )
        self.assertEqual(
            runtime["page_implementation_contracts"][0]["uiDesignRef"]["sha256"],
            "a" * 64,
        )
        self.assertEqual(
            runtime["page_implementation_contracts"][0]["actionBindings"],
            [
                {
                    "actionId": action_id,
                    "bindingType": "endpoint",
                    "endpointId": "inventory.list",
                    "actionName": first_page["actions"][0]["name"],
                    "uiControlRefs": [
                        {
                            "controlId": f"{action_id}-control",
                            "label": "",
                        }
                    ],
                }
            ],
        )
        self.assertEqual(
            validate_page_implementation_contracts(
                attached,
                scoped_product_plan,
                ui_designs,
            ),
            [],
        )
        rendered = render_project_plan_markdown(attached)
        self.assertIn(f"`{action_id}`", rendered)
        self.assertIn("`inventory.list`", rendered)

    def test_product_and_ui_behaviors_compile_with_only_endpoint_technical_input(self) -> None:
        """产品/UI 决定非技术行为，TechnicalPlan 只补齐业务步骤的 endpoint。"""

        product_plan = {
            "pages": [
                {
                    "pageId": "orders",
                    "actions": [
                        {
                            "actionId": "open-filter",
                            "behavior": {"type": "interface", "expectedResult": "展示筛选条件。"},
                        },
                        {
                            "actionId": "save-and-refresh",
                            "behavior": {
                                "type": "sequence",
                                "expectedResult": "保存成功并刷新列表。",
                                "steps": [
                                    {"stepId": "save", "type": "business", "expectedResult": "保存订单。"},
                                    {"stepId": "refresh", "type": "interface", "expectedResult": "刷新列表。"},
                                ],
                            },
                        },
                        {
                            "actionId": "open-detail",
                            "behavior": {
                                "type": "navigation",
                                "targetPageId": "order-detail",
                                "expectedResult": "进入订单详情。",
                            },
                        },
                    ],
                    "navigation_targets": ["order-detail"],
                    "allowed_roles": [],
                },
                {
                    "pageId": "order-detail",
                    "actions": [{
                        "actionId": "close-detail",
                        "behavior": {"type": "interface", "expectedResult": "关闭详情抽屉。"},
                    }],
                    "navigation_targets": [],
                    "allowed_roles": [],
                },
            ]
        }
        technical_plan = {
            "artifact_type": "technical-plan",
            "pages": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [{"endpoint_id": "orders.save"}],
                        "action_implementations": [
                            {
                                "actionId": "save-and-refresh",
                                "stepBindings": [
                                    {"stepId": "save", "endpointId": "orders.save"},
                                ],
                            },
                        ],
                    },
                },
                {
                    "pageId": "order-detail",
                    "references": {
                        "endpoint_dependencies": [],
                        "action_implementations": [],
                    },
                },
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "endpoints": [{"id": "orders.save", "method": "POST"}],
                }
            ],
        }
        ui_designs = {
            "pages": [
                {
                    "pageId": "orders",
                    "bindings": {"actions": [
                        {"actionId": "open-filter", "controlIds": ["filter"], "uiEffect": "展开筛选区域"},
                        {
                            "actionId": "save-and-refresh",
                            "controlIds": ["save"],
                            "stepEffects": [{"stepId": "refresh", "uiEffect": "刷新列表"}],
                        },
                        {"actionId": "open-detail", "controlIds": ["detail"]},
                    ]},
                },
                {
                    "pageId": "order-detail",
                    "bindings": {"actions": [
                        {"actionId": "close-detail", "controlIds": ["close"], "uiEffect": "关闭详情抽屉"},
                    ]},
                },
            ]
        }

        attached = attach_page_implementation_contracts(
            technical_plan,
            product_plan,
            ui_designs,
        )

        self.assertEqual(
            validate_page_implementation_contracts(attached, product_plan, ui_designs),
            [],
        )
        runtime = materialize_technical_plan_runtime(
            attached,
            {"app_info": {}, "data_sources": [], "user_roles": [], "feature_modules": []},
            product_plan,
            ui_designs,
        )
        bindings = runtime["page_implementation_contracts"][0]["actionBindings"]
        self.assertEqual([item["bindingType"] for item in bindings], ["local", "sequence", "navigation"])
        self.assertNotIn("action_bindings", technical_plan["pages"][0]["references"])

    def test_skipped_ui_uses_product_expected_results_for_local_bindings(self) -> None:
        """跳过 UI 设计后，页面契约仍应从 ProductPlan 补齐本地交互语义。"""

        product_plan = {
            "pages": [
                {
                    "pageId": "orders",
                    "actions": [
                        {
                            "actionId": "open-filter",
                            "name": "打开筛选",
                            "behavior": {
                                "type": "interface",
                                "expectedResult": "展开筛选区域。",
                            },
                        }
                    ],
                    "navigation_targets": [],
                    "allowed_roles": [],
                }
            ]
        }
        technical_plan = {
            "artifact_type": "technical-plan",
            "pages": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [],
                        "action_implementations": [],
                    },
                }
            ],
            "api_contracts": [],
        }
        ui_designs = {
            "schema_version": "ui-manifest.v3",
            "confirmation_status": "skipped",
            "pages": [],
        }

        attached = attach_page_implementation_contracts(
            technical_plan,
            product_plan,
            ui_designs,
        )
        runtime = materialize_technical_plan_runtime(
            attached,
            {"app_info": {}, "data_sources": [], "user_roles": [], "feature_modules": []},
            product_plan,
            ui_designs,
        )
        binding = runtime["page_implementation_contracts"][0]["actionBindings"][0]

        self.assertEqual(binding["bindingType"], "local")
        self.assertEqual(binding["localEffect"], "展开筛选区域。")
        self.assertEqual(
            validate_page_implementation_contracts(attached, product_plan, ui_designs),
            [],
        )

    def test_action_binding_validation_rejects_missing_or_ambiguous_decisions(self) -> None:
        """缺失操作绑定及未闭合 endpoint 引用必须阻止 TechnicalPlan 确认。"""

        product_plan = {
            "pages": [
                {
                    "pageId": "orders",
                    "actions": [
                        {
                            "actionId": "open-filter",
                            "behavior": {"type": "interface", "expectedResult": "展开筛选。"},
                        },
                        {
                            "actionId": "delete-order",
                            "behavior": {"type": "business", "expectedResult": "删除订单。"},
                        },
                    ],
                    "navigation_targets": [],
                }
            ]
        }
        technical_plan = {
            "artifact_type": "technical-plan",
            "pages": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [],
                        "action_implementations": [
                            {
                                "actionId": "open-filter",
                                "endpointId": "orders.delete",
                            }
                        ],
                    },
                }
            ],
            "api_contracts": [
                {
                    "id": "orders-api",
                    "endpoints": [{"id": "orders.delete", "method": "DELETE"}],
                }
            ],
        }

        attached = attach_page_implementation_contracts(
            technical_plan,
            product_plan,
            {"pages": [{"pageId": "orders"}]},
        )
        errors = validate_page_implementation_contracts(attached, product_plan)

        self.assertTrue(any("缺少业务 action endpoint 实现" in error for error in errors))
        self.assertTrue(any("不得为导航、界面或外部 action" in error for error in errors))

    def test_ui_manifest_controls_must_reference_exact_product_actions(self) -> None:
        """UI 业务控件与产品操作不一致时必须阻止进入工作台。"""

        product_plan = {
            "pages": [
                {
                    "pageId": "orders",
                    "actions": [
                        {
                            "actionId": "open-filter",
                            "behavior": {"type": "interface", "expectedResult": "展开筛选。"},
                        }
                    ],
                    "navigation_targets": [],
                }
            ]
        }
        technical_plan = {
            "artifact_type": "technical-plan",
            "pages": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [],
                        "action_bindings": [
                            {
                                "actionId": "open-filter",
                                "bindingType": "local",
                                "localEffect": "展开筛选区域",
                            }
                        ],
                    },
                }
            ],
            "api_contracts": [],
        }
        ui_designs = {
            "pages": [
                {
                    "pageId": "orders",
                    "controls": [
                        {"controlId": "unknown-control", "actionId": "unknown-action"}
                    ],
                }
            ]
        }

        attached = attach_page_implementation_contracts(
            technical_plan,
            product_plan,
            ui_designs,
        )
        errors = validate_page_implementation_contracts(
            attached,
            product_plan,
            ui_designs,
        )

        self.assertTrue(any("UiManifest controls" in error for error in errors))

    def test_binding_type_rejects_fields_from_another_type(self) -> None:
        """本地操作夹带 endpointId 时必须报告类型冲突，不能静默猜成接口调用。"""

        product_plan = {
            "pages": [
                {
                    "pageId": "orders",
                    "actions": [
                        {
                            "actionId": "open-filter",
                            "behavior": {"type": "interface", "expectedResult": "展开筛选。"},
                        }
                    ],
                    "navigation_targets": [],
                }
            ]
        }
        technical_plan = {
            "artifact_type": "technical-plan",
            "pages": [
                {
                    "pageId": "orders",
                    "references": {
                        "endpoint_dependencies": [{"endpoint_id": "orders.list"}],
                        "action_bindings": [
                            {
                                "actionId": "open-filter",
                                "bindingType": "local",
                                "localEffect": "展开筛选区域",
                                "endpointId": "orders.list",
                            }
                        ],
                    },
                }
            ],
            "api_contracts": [
                {"id": "orders-api", "endpoints": [{"id": "orders.list"}]}
            ],
        }

        attached = attach_page_implementation_contracts(
            technical_plan,
            product_plan,
            {"pages": [{"pageId": "orders"}]},
        )
        errors = validate_page_implementation_contracts(attached, product_plan)

        self.assertTrue(any("action_bindings" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
