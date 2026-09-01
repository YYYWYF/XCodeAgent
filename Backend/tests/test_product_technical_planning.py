from __future__ import annotations

import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from app.agents.main.product_planner import (
    _product_plan_json_example,
    _product_planning_prompt,
)
from app.agents.main.planner import (
    _technical_contract_ids_for_errors,
    _technical_contract_repair_prompt,
    _technical_planning_prompt,
    repair_technical_plan_api_contracts_with_chat_model,
    technical_plan_contract_repair_applicable,
)
from app.agents.main.requirements_analyzer import (
    _requirements_prompt,
    _validate_complete_requirement_spec,
)
from app.services.page_implementation_contract import (
    attach_page_implementation_contracts,
    materialize_technical_plan_runtime,
    validate_page_implementation_contracts,
)
from app.services.api_contracts import normalize_api_contracts
from app.services.page_dependencies import normalize_page_dependencies
from app.services.product_plan import (
    create_product_plan,
    validate_product_plan,
    validate_product_plan_model_output,
)
from app.services.requirement_spec import create_requirement_spec
from app.services.project_plan import create_technical_plan
from app.workspace.spec_documents import render_requirement_spec_markdown
from app.workspace.plan_documents import render_project_plan_markdown


def technical_model_entities(requirement_spec: dict) -> dict:
    """构造测试中的技术规划模型实体输出。"""

    return {"entities": deepcopy(requirement_spec.get("entities", []))}


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

    def test_technical_action_endpoints_close_page_dependencies(self) -> None:
        """TechnicalPlan 的业务操作选择 endpoint 后必须自动进入页面接口依赖。"""

        normalized = normalize_page_dependencies(
            [
                {
                    "pageId": "asset_list",
                    "references": {
                        "endpoint_dependencies": [
                            {"endpoint_id": "asset_api.list", "usage": "read"}
                        ],
                        "action_implementations": [
                            {
                                "actionId": "asset_outbound",
                                "endpointId": "asset_api.outbound",
                            },
                            {
                                "actionId": "asset_inbound",
                                "stepBindings": [
                                    {
                                        "stepId": "submit_inbound",
                                        "endpointId": "asset_api.inbound",
                                    }
                                ],
                            },
                        ],
                    },
                }
            ],
            [
                {
                    "id": "asset_api",
                    "endpoints": [
                        {"id": "asset_api.list"},
                        {"id": "asset_api.outbound"},
                        {"id": "asset_api.inbound"},
                    ],
                }
            ],
            include_action_implementations=True,
        )

        dependencies = normalized[0]["references"]["endpoint_dependencies"]
        self.assertEqual(
            [item["endpoint_id"] for item in dependencies],
            ["asset_api.list", "asset_api.outbound", "asset_api.inbound"],
        )
        self.assertEqual(dependencies[1]["required_for_initial_load"], False)

    def test_product_plan_rejects_requirement_pair_hash_mismatch(self) -> None:
        """联合需求文档中的 ProductPlan 必须绑定同一版本 RequirementSpec。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        changed_spec = deepcopy(requirement_spec)
        changed_spec["app_info"]["summary"] = "已经修改的需求摘要。"

        errors = validate_product_plan(product_plan, changed_spec)

        self.assertTrue(any("requirement_spec_sha256" in error for error in errors))

    def test_product_plan_v6_keeps_only_pages_and_closes_navigation(self) -> None:
        """v6 必须规范产品行为，并从导航操作闭合页面跳转。"""

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
                                "actionId": "open_inventory",
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
        self.assertEqual(product_plan["schema_version"], "product-plan.v6")
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

    def test_product_plan_rejects_runtime_authorization_and_system_page(self) -> None:
        """ProductPlan 不得携带运行态角色授权或模板固定权限管理页。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        product_plan["user_roles"] = [{"id": "administrator"}]
        product_plan["pages"][0]["allowed_roles"] = ["administrator"]
        product_plan["resourceKey"] = "forbidden"

        errors = validate_product_plan(product_plan, requirement_spec)

        self.assertTrue(any("不得包含技术规划字段" in error for error in errors))
        self.assertTrue(any("不得包含角色或授权字段" in error for error in errors))
        self.assertTrue(any("resourceKey" in error for error in errors))

    def test_product_plan_maps_page_rules_from_target_page_id(self) -> None:
        """页面规则必须直接消费 targetPageId，展示名称不再参与权限目标推断。"""

        requirement_spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "pages": [
                    {
                        "pageId": "people_list",
                        "name": "人员列表",
                        "path": "/people",
                        "module_id": "people",
                        "description": "管理人员信息。",
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "unauthorizedBehavior": {
                        "unauthorizedPage": "show_forbidden",
                        "unauthorizedOperation": "disable",
                    },
                    "restrictedPages": [
                        {
                            "name": "人员管理功能",
                            "targetPageId": "people_list",
                            "description": "仅授权成员可访问。",
                            "rationale": "人员信息属于内部资料。",
                            "sourceRefs": ["用户提及人员列表权限"],
                        }
                    ],
                    "restrictedOperations": [
                        {
                            "name": "停用人员",
                            "description": "仅授权成员可停用人员。",
                            "rationale": "停用会影响人员使用状态。",
                            "sourceRefs": ["用户提及停用人员权限"],
                        }
                    ],
                    "dataRules": [],
                },
            },
        )
        product_plan = create_product_plan(
            requirement_spec,
            agent_plan={
                "pages": [
                    {
                        "pageId": "people_list",
                        "actions": [
                            {
                                "actionId": "disable_person",
                                "name": "停用人员",
                                "description": "停用选定人员。",
                                "requiresConfirmation": True,
                                "behavior": {"type": "business", "expectedResult": "人员被停用。"},
                            }
                        ],
                    }
                ]
            },
        )

        self.assertEqual(validate_product_plan(product_plan, requirement_spec), [])
        targets = product_plan["authorizationTargets"]
        self.assertEqual(targets["pageRules"][0]["pageId"], "people_list")
        self.assertEqual(targets["operationRules"][0]["pageId"], "people_list")
        self.assertEqual(targets["operationRules"][0]["actionId"], "disable_person")
        product_plan["authorizationTargets"]["operationRules"] = []
        self.assertTrue(any("operationRules 必须与已确认" in error for error in validate_product_plan(product_plan, requirement_spec)))

    def test_product_plan_rejects_incomplete_operation_target_and_resource_collision(self) -> None:
        """操作规则必须闭合到页面，且预编译资源键不得与页面或系统资源冲突。"""

        requirement_spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "pages": [
                    {"pageId": "orders", "name": "订单", "path": "/orders", "module_id": "orders", "description": "管理订单。"},
                    {"pageId": "orders_export", "name": "导出中心", "path": "/exports", "module_id": "orders", "description": "导出订单。"},
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "restrictedPages": [{"name": "导出中心", "targetPageId": "orders_export", "description": "仅授权成员访问。"}],
                    "restrictedOperations": [{"name": "导出订单", "description": "仅授权成员导出。"}],
                },
            },
        )
        product_plan = create_product_plan(
            requirement_spec,
            agent_plan={
                "pages": [
                    {"pageId": "orders", "actions": [{"actionId": "export", "name": "导出订单", "description": "导出订单文件。", "requiresConfirmation": False, "behavior": {"type": "business", "expectedResult": "生成导出文件。"}}]},
                    {"pageId": "orders_export", "actions": []},
                ]
            },
        )

        self.assertTrue(any("跨类型碰撞" in error for error in validate_product_plan(product_plan, requirement_spec)))
        product_plan["authorizationTargets"]["operationRules"][0].pop("pageId")
        self.assertTrue(any("映射字段必须为" in error for error in validate_product_plan(product_plan, requirement_spec)))

    def test_product_plan_rejects_fixed_roles_page(self) -> None:
        """固定权限管理页不属于业务 ProductPlan，也不能进入 UiDesign 输入。"""

        requirement_spec = create_requirement_spec(
            "创建应用",
            agent_spec={
                "pages": [{"pageId": "system_authorization_management", "name": "角色管理", "path": "/roles", "module_id": "system", "description": "管理权限。"}],
            },
        )
        product_plan = create_product_plan(requirement_spec)

        self.assertTrue(any("固定权限管理页面" in error for error in validate_product_plan(product_plan, requirement_spec)))

    def test_product_prompt_requires_exact_coverage_for_restricted_operations(self) -> None:
        """受限业务操作必须作为模型生成 ProductPlan action 的显式覆盖约束。"""

        requirement_spec = create_requirement_spec(
            "人员管理",
            agent_spec={
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
                }
            },
        )

        prompt = _product_planning_prompt(requirement_spec)

        self.assertIn("权限操作覆盖约束", prompt)
        self.assertIn("停用人员", prompt)
        self.assertIn("action.name 必须与该名称完全相同", prompt)

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
        self.assertIn("do not return authorizationTargets", prompt)
        self.assertNotIn('"frontend_pages":', prompt)
        self.assertIn("Do not return assumptions or product risks", prompt)
        self.assertIn("A display-only page may therefore have an empty actions list", prompt)
        self.assertIn("behavior.type is one of business, navigation, interface, external", prompt)
        self.assertIn("do not include data_sources", _requirements_prompt("创建库存系统"))
        self.assertIn("Do not return assumptions, product risks", _requirements_prompt("创建库存系统"))

    def test_technical_prompt_uses_current_five_part_contract(self) -> None:
        """TechnicalPlan 提示词必须使用产品上下文生成实体和新分页字段。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        requirement_spec["entities"] = [
            {
                "id": "REQUIREMENT_ENTITY_SENTINEL",
                "name": "需求实体哨兵",
                "description": "技术规划不得读取这个实体。",
                "fields": [{"name": "requirement_field_sentinel"}],
            }
        ]
        product_plan = create_product_plan(requirement_spec)
        prompt = _technical_planning_prompt(
            {**requirement_spec, "confirmed_product_plan": product_plan},
            None,
        )

        self.assertIn(
            "architecture, entities, api_contracts, pages, and agent_contracts",
            prompt,
        )
        self.assertNotIn("authorization_data_bindings", prompt)
        self.assertIn("entity_field_ref", prompt)
        self.assertIn("computed, aggregated, and transport properties may omit the mapping", prompt)
        self.assertIn("has exactly five sections", prompt)
        self.assertIn("has exactly four same-level properties", prompt)
        self.assertIn("total, pageSize, current, and list", prompt)
        self.assertIn("Product goal context", prompt)
        self.assertIn("Authorization context", prompt)
        self.assertIn("Business-flow context", prompt)
        self.assertIn("Page context", prompt)
        self.assertIn("Business-action context", prompt)
        self.assertIn("operation semantics, never from the HTTP method alone", prompt)
        self.assertIn("command-with-body", prompt)
        self.assertIn("command-without-body", prompt)
        self.assertIn("Never invent an empty request object", prompt)
        self.assertIn("RequirementSpec entities are not provided", prompt)
        self.assertNotIn("REQUIREMENT_ENTITY_SENTINEL", prompt)
        self.assertNotIn("requirement_field_sentinel", prompt)
        self.assertNotIn("engineering_design", prompt)
        self.assertNotIn('"resource"', prompt)

    def test_technical_plan_entities_come_only_from_model_output(self) -> None:
        """TechnicalPlan 实体不得继承 RequirementSpec.entities。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        requirement_spec["entities"] = [
            {
                "id": "RequirementOnly",
                "name": "需求阶段实体",
                "description": "不得进入技术规划。",
                "fields": [{"name": "requirement_only_field"}],
            }
        ]
        product_plan = create_product_plan(requirement_spec)
        technical_plan = create_technical_plan(
            {**requirement_spec, "confirmed_product_plan": product_plan},
            agent_plan={
                "entities": [
                    {
                        "id": "PlannedRecord",
                        "name": "技术规划实体",
                        "description": "根据 ProductPlan 生成。",
                        "fields": [
                            {
                                "name": "record_code",
                                "label": "记录编码",
                                "description": "技术规划字段。",
                                "type": "text",
                                "required": True,
                            }
                        ],
                    }
                ]
            },
        )

        self.assertEqual(
            [entity["id"] for entity in technical_plan["entities"]],
            ["PlannedRecord"],
        )
        self.assertNotIn("RequirementOnly", json.dumps(technical_plan, ensure_ascii=False))

    def test_contract_repair_prompt_projects_only_implicated_contract_context(self) -> None:
        """Contract 定向修复不得把无关 API、实体或页面动作重新注入模型。"""

        existing_plan = {
            "entities": [
                {"id": "Photo", "fields": [{"name": "id"}]},
                {"id": "User", "fields": [{"name": "id"}]},
            ],
            "api_contracts": [
                {
                    "id": "photo_api",
                    "entity_ids": ["Photo"],
                    "schemas": {"PhotoOutput": {"type": "object"}},
                    "endpoints": [{"id": "photo_api.like", "method": "POST"}],
                },
                {
                    "id": "user_api",
                    "entity_ids": ["User"],
                    "schemas": {"UnrelatedSchema": {"type": "object"}},
                    "endpoints": [
                        {"id": "user_api.unrelated_endpoint", "method": "GET"}
                    ],
                },
            ],
            "pages": [
                {
                    "pageId": "photo_page",
                    "references": {
                        "endpoint_dependencies": [
                            {"endpoint_id": "photo_api.like"}
                        ]
                    },
                }
            ],
        }
        requirement_spec = {
            "confirmed_product_plan": {
                "pages": [
                    {
                        "pageId": "photo_page",
                        "actions": [{"actionId": "like-photo"}],
                    },
                    {
                        "pageId": "user_page",
                        "actions": [{"actionId": "UNRELATED_ACTION_SENTINEL"}],
                    },
                ]
            }
        }
        errors = ["Endpoint photo_api.like references unknown schema MissingInput."]

        contract_ids = _technical_contract_ids_for_errors(existing_plan, errors)
        prompt = _technical_contract_repair_prompt(
            requirement_spec,
            existing_plan,
            errors,
            contract_ids,
        )

        self.assertEqual(contract_ids, ["photo_api"])
        self.assertIn("photo_api.like", prompt)
        self.assertIn("like-photo", prompt)
        self.assertNotIn("user_api.unrelated_endpoint", prompt)
        self.assertNotIn("UnrelatedSchema", prompt)
        self.assertNotIn("UNRELATED_ACTION_SENTINEL", prompt)

    def test_contract_repair_merges_replacement_without_rewriting_other_contracts(self) -> None:
        """Contract 修复结果只能替换目标 Contract，其他完整计划部分必须保持不变。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        technical_input = {
            **requirement_spec,
            "confirmed_product_plan": product_plan,
        }
        existing_plan = create_technical_plan(
            technical_input,
            agent_plan=technical_model_entities(requirement_spec),
        )
        target_contract = deepcopy(existing_plan["api_contracts"][0])
        target_contract["schemas"]["RepairOutput"] = {
            "type": "object",
            "properties": {"success": {"type": "boolean"}},
            "required": ["success"],
        }
        target_contract_id = str(target_contract["id"])
        untouched_contracts = deepcopy(existing_plan["api_contracts"][1:])
        errors = [
            f"Schema {target_contract_id}.Broken references unknown schema MissingItem."
        ]

        with (
            patch("app.agents.main.planner.Settings.from_env", return_value=object()),
            patch(
                "app.agents.main.planner._invoke_prompt_with_chat_model",
                return_value=json.dumps(
                    {"api_contracts": [target_contract]},
                    ensure_ascii=False,
                ),
            ),
        ):
            repaired = repair_technical_plan_api_contracts_with_chat_model(
                technical_input,
                existing_plan,
                errors,
            )

        self.assertIn("RepairOutput", repaired["api_contracts"][0]["schemas"])
        self.assertEqual(repaired["api_contracts"][1:], untouched_contracts)
        self.assertEqual(repaired["pages"], existing_plan["pages"])

    def test_contract_repair_requires_every_error_to_be_contract_scoped(self) -> None:
        """页面错误或混合错误不得因包含已知 Endpoint ID 而进入 Contract-only 修复。"""

        existing_plan = {
            "api_contracts": [
                {
                    "id": "person_name_api",
                    "endpoints": [{"id": "person_name_api.create"}],
                }
            ]
        }
        contract_error = (
            "Endpoint person_name_api.create references unknown schema MissingInput."
        )
        page_error = (
            "页面 name_entry 的业务 action submit_name 使用的 endpoint "
            "person_name_api.create 未列入 requiredEndpointIds。"
        )

        self.assertTrue(
            technical_plan_contract_repair_applicable(
                existing_plan,
                [contract_error],
                [contract_error],
            )
        )
        self.assertFalse(
            technical_plan_contract_repair_applicable(
                existing_plan,
                [page_error],
                [contract_error],
            )
        )
        self.assertFalse(
            technical_plan_contract_repair_applicable(
                existing_plan,
                [contract_error, page_error],
                [contract_error],
            )
        )

    def test_action_endpoints_close_page_dependencies_without_guessing_unknown_ids(self) -> None:
        """direct/sequence 已知 Endpoint 必须补入页面依赖，未知 Endpoint 继续留给校验。"""

        pages = [
            {
                "pageId": "name_entry",
                "references": {
                    "endpoint_dependencies": [
                        {
                            "endpoint_id": "person_name_api.list",
                            "usage": "page_load",
                            "trigger": "进入页面",
                            "required_for_initial_load": True,
                        }
                    ],
                    "action_implementations": [
                        {
                            "actionId": "submit_name",
                            "endpointId": "person_name_api.create",
                        },
                        {
                            "actionId": "save_and_refresh",
                            "stepBindings": [
                                {
                                    "stepId": "refresh",
                                    "endpointId": "person_name_api.list",
                                },
                                {
                                    "stepId": "notify",
                                    "endpointId": "person_name_api.unknown",
                                },
                            ],
                        },
                    ],
                },
            }
        ]
        contracts = [
            {
                "id": "person_name_api",
                "endpoints": [
                    {"id": "person_name_api.list"},
                    {"id": "person_name_api.create"},
                ],
            }
        ]

        closed = normalize_page_dependencies(
            pages,
            contracts,
            include_action_implementations=True,
        )
        dependencies = closed[0]["references"]["endpoint_dependencies"]

        self.assertEqual(
            [item["endpoint_id"] for item in dependencies],
            ["person_name_api.list", "person_name_api.create"],
        )
        self.assertEqual(dependencies[0]["trigger"], "进入页面")
        self.assertEqual(dependencies[1]["usage"], "business_action")
        self.assertEqual(dependencies[1]["trigger"], "业务操作 submit_name")
        self.assertFalse(dependencies[1]["required_for_initial_load"])
        self.assertEqual(
            pages[0]["references"]["endpoint_dependencies"],
            [
                {
                    "endpoint_id": "person_name_api.list",
                    "usage": "page_load",
                    "trigger": "进入页面",
                    "required_for_initial_load": True,
                }
            ],
        )

    def test_requirement_prompt_limits_rounds_and_asks_only_required_gaps(self) -> None:
        """需求提示必须固定三轮预算，并且只询问实际缺失的必需字段。"""

        first_prompt = _requirements_prompt("创建一个库存管理系统")
        final_prompt = _requirements_prompt(
            "已回答第三轮需求澄清",
            create_requirement_spec("创建一个库存管理系统"),
            clarification_round=3,
        )

        self.assertIn("at most 3 clarification rounds", first_prompt)
        self.assertIn("check these required fields in order", first_prompt)
        self.assertIn("(1) app_info.name is non-empty", first_prompt)
        self.assertIn("(5) business_flows has at least one flow", first_prompt)
        self.assertIn("call ask_user once for exactly those missing fields", first_prompt)
        self.assertNotIn("5 to 8 focused questions", first_prompt)
        self.assertIn("clarification round 1 of 3", first_prompt)
        self.assertIn("never call ask_user in this pass", final_prompt)
        self.assertIn("After the user has answered round 3, never call ask_user again", final_prompt)

    def test_requirement_revision_uses_complete_merged_summary(self) -> None:
        """需求修订不得用本轮增量输入覆盖原应用摘要。"""

        existing = create_requirement_spec("创建一个库存管理系统")
        revised = create_requirement_spec(
            "新增低库存预警功能",
            existing_spec=existing,
            agent_spec={
                "app_info": {
                    "name": existing["app_info"]["name"],
                    "summary": "库存管理系统支持库存查看、出入库管理和低库存预警。",
                }
            },
        )

        self.assertEqual(
            revised["app_info"]["summary"],
            "库存管理系统支持库存查看、出入库管理和低库存预警。",
        )
        self.assertEqual(revised["summary"], revised["app_info"]["summary"])
        self.assertNotEqual(revised["summary"], "新增低库存预警功能")
        self.assertEqual(
            create_product_plan(revised)["app"]["summary"],
            revised["app_info"]["summary"],
        )

    def test_requirement_revision_fallback_merges_old_summary_and_delta(self) -> None:
        """模型漏掉摘要时仍需确定性保留原需求并标记最新调整。"""

        existing = create_requirement_spec("创建一个库存管理系统")
        revised = create_requirement_spec(
            "新增低库存预警功能",
            existing_spec=existing,
            agent_spec={},
        )

        self.assertIn(existing["app_info"]["summary"], revised["summary"])
        self.assertIn("新增低库存预警功能", revised["summary"])

    def test_revision_prompts_define_incremental_merge_semantics(self) -> None:
        """需求与产品规划修订提示都必须把自由输入解释为增量补丁。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        product_plan = create_product_plan(requirement_spec)
        requirement_prompt = _requirements_prompt(
            "新增低库存预警功能",
            requirement_spec,
        )
        product_prompt = _product_planning_prompt(
            requirement_spec,
            product_plan,
            "新增低库存预警操作",
        )

        self.assertIn("incremental patch", requirement_prompt)
        self.assertIn("complete merged application", requirement_prompt)
        self.assertIn("authoritatively replace the old document", requirement_prompt)
        self.assertIn("never return a partial patch", requirement_prompt)
        self.assertIn("incremental patch", product_prompt)
        self.assertIn("complete merged ProductPlan", product_prompt)

    def test_requirement_revision_rejects_incomplete_ai_document(self) -> None:
        """需求 AI 未返回完整新文档时必须失败，不能用旧字段静默补齐。"""

        with self.assertRaisesRegex(ValueError, "缺少完整字段"):
            _validate_complete_requirement_spec(
                {
                    "app_info": {
                        "name": "个人喜好",
                        "summary": "改成奶茶和零食两个固定页面。",
                    },
                    "pages": [],
                }
            )

    def test_requirement_model_rejects_empty_page_inventory(self) -> None:
        """模型即使返回完整字段名，也不能用空页面文档进入确认流程。"""

        with self.assertRaisesRegex(ValueError, r"pages\(non-empty\)"):
            _validate_complete_requirement_spec(
                {
                    "app_info": {
                        "name": "人员管理",
                        "summary": "管理人员信息。",
                    },
                    "user_roles": [],
                    "feature_modules": [],
                    "pages": [],
                    "entities": [],
                    "business_flows": [],
                }
            )

    def test_requirement_markdown_uses_complete_ai_summary(self) -> None:
        """Markdown 必须展示 AI 合并后的完整摘要，而不是旧 source_request。"""

        spec = create_requirement_spec("旧需求：使用统一喜好列表页。")
        spec["source_request"] = "旧需求：使用统一喜好列表页。"
        spec["summary"] = "新需求：使用奶茶喜好页和零食喜好页。"
        markdown = render_requirement_spec_markdown(spec)

        self.assertIn("确认需求摘要：新需求：使用奶茶喜好页和零食喜好页。", markdown)
        self.assertNotIn("确认需求摘要：旧需求：使用统一喜好列表页。", markdown)

    def test_product_model_example_contains_every_page_and_no_duplicate_tree(self) -> None:
        """模型完整 JSON 示例必须展开全部页面和智能体产品契约。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        example = json.loads(_product_plan_json_example(requirement_spec))

        self.assertEqual(
            [page["pageId"] for page in example["pages"]],
            [page["pageId"] for page in requirement_spec["pages"]],
        )
        self.assertEqual(
            set(example),
            {"app", "agents", "business_flows", "pages", "product_acceptance_criteria"},
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

    def test_product_model_output_rejects_server_derived_authorization_targets(self) -> None:
        """模型不能手写由服务端在归一化后生成的权限目标映射。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        agent_plan = json.loads(_product_plan_json_example(requirement_spec))
        agent_plan["authorizationTargets"] = {"pageRules": [], "operationRules": []}

        errors = validate_product_plan_model_output(agent_plan, requirement_spec)

        self.assertTrue(any("authorizationTargets" in error for error in errors))

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
        technical_plan = create_technical_plan(
            requirement_with_product,
            agent_plan=technical_model_entities(requirement_spec),
        )
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
                "agent_contracts",
                "authorization_manifest",
                "product_plan_sha256",
                "ui_designs_sha256",
            },
        )
        self.assertEqual(attached["artifact_type"], "technical-plan")
        self.assertEqual(attached["agent_contracts"], [])
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

    def test_api_authentication_uses_required_as_the_only_canonical_field(self) -> None:
        """共享 API 契约归一化不得再生成废弃的角色字段。"""

        normalized = normalize_api_contracts(
            [
                {
                    "id": "people_api",
                    "authentication": {"required": True, "roles": ["admin"]},
                    "endpoints": [
                        {
                            "id": "people_api.list",
                            "method": "GET",
                            "authentication": {"required": False, "roles": []},
                        }
                    ],
                }
            ]
        )

        self.assertEqual(normalized[0]["authentication"], {"required": True})
        self.assertEqual(normalized[0]["endpoints"][0]["authentication"], {"required": False})

    def test_technical_plan_rejects_model_authentication_roles_before_normalization(self) -> None:
        """模型显式输出角色语义必须在归一化前被拒绝。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        requirement_with_product = {
            **requirement_spec,
            "confirmed_product_plan": create_product_plan(requirement_spec),
        }

        with self.assertRaisesRegex(ValueError, "模型输出 API Contract people_api"):
            create_technical_plan(
                requirement_with_product,
                agent_plan={
                    "api_contracts": [
                        {
                            "id": "people_api",
                            "authentication": {"required": True, "roles": ["admin"]},
                            "endpoints": [],
                        }
                    ]
                },
            )

    def test_technical_plan_rejects_model_owned_authorization_fields_before_normalization(self) -> None:
        """模型不得用隐藏字段绕过平台确定性权限编译。"""

        requirement_spec = create_requirement_spec("创建一个库存管理系统")
        requirement_with_product = {
            **requirement_spec,
            "confirmed_product_plan": create_product_plan(requirement_spec),
        }

        with self.assertRaisesRegex(ValueError, "authorization_manifest"):
            create_technical_plan(
                requirement_with_product,
                agent_plan={"authorization_manifest": {"enabled": True}},
            )
        with self.assertRaisesRegex(ValueError, "dataRules"):
            create_technical_plan(
                requirement_with_product,
                agent_plan={"dataRules": []},
            )
        with self.assertRaisesRegex(ValueError, "模型输出 Endpoint people_api.list"):
            create_technical_plan(
                requirement_with_product,
                agent_plan={
                    "api_contracts": [
                        {
                            "id": "people_api",
                            "authentication": {"required": True},
                            "endpoints": [
                                {
                                    "id": "people_api.list",
                                    "authentication": {"required": True, "roles": ["admin"]},
                                }
                            ],
                        }
                    ]
                },
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

    def test_missing_action_endpoint_dependency_is_reported_once(self) -> None:
        """同一业务 action 的页面依赖缺口只向修复链路报告一次。"""

        product_plan = {
            "pages": [
                {
                    "pageId": "name_entry",
                    "actions": [
                        {
                            "actionId": "submit_name",
                            "behavior": {
                                "type": "business",
                                "expectedResult": "保存姓名。",
                            },
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
                    "pageId": "name_entry",
                    "references": {
                        "endpoint_dependencies": [
                            {"endpoint_id": "person_name_api.list"}
                        ],
                        "action_implementations": [
                            {
                                "actionId": "submit_name",
                                "endpointId": "person_name_api.create",
                            }
                        ],
                    },
                }
            ],
            "api_contracts": [
                {
                    "id": "person_name_api",
                    "endpoints": [
                        {"id": "person_name_api.list"},
                        {"id": "person_name_api.create"},
                    ],
                }
            ],
        }

        errors = validate_page_implementation_contracts(
            technical_plan,
            product_plan,
        )

        dependency_errors = [
            error for error in errors if "未列入 requiredEndpointIds" in error
        ]
        self.assertEqual(
            dependency_errors,
            [
                "页面 name_entry 的业务 action submit_name 使用的 endpoint "
                "person_name_api.create 未列入 requiredEndpointIds。"
            ],
        )

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
