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
    save_requirement_spec_draft,
    validate_authorization_requirements,
    validate_requirement_spec_confirmation_readiness,
)
from app.tools.ask_user import clear_clarification
from app.workspace.spec_documents import (
    load_requirement_spec_json,
    render_requirement_spec_markdown,
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
    def test_requirement_pages_normalize_ids_to_unique_lower_snake_case(self) -> None:
        """模型给出的 camelCase、连字符页面 ID 必须在进入 ProductPlan 前收敛。"""

        spec = create_requirement_spec(
            "创建页面 ID 规范化应用",
            agent_spec={
                "pages": [
                    {
                        "pageId": "OrderList",
                        "name": "订单列表",
                        "path": "/orders",
                        "module_id": "orders",
                        "description": "查看订单。",
                    },
                    {
                        "pageId": "order-list",
                        "name": "订单导出",
                        "path": "/orders/export",
                        "module_id": "orders",
                        "description": "导出订单。",
                    },
                ]
            },
            authoritative_agent_spec=True,
        )

        self.assertEqual(
            [page["pageId"] for page in spec["pages"]],
            ["order_list", "order_list_2"],
        )
        self.assertEqual(validate_requirement_spec_confirmation_readiness(spec), [])

    def test_requirement_confirmation_rejects_edited_invalid_page_id(self) -> None:
        """Markdown 编辑绕过生成归一化时，也不得把非法页面 ID 交给 ProductPlan。"""

        spec = create_requirement_spec("创建库存管理系统")
        spec["pages"][0]["pageId"] = "inventory-list"

        errors = validate_requirement_spec_confirmation_readiness(spec)

        self.assertIn("页面清单第 1 项的 pageId 必须为 lower_snake_case", errors)

    def test_authorization_candidates_are_normalized_and_rendered(self) -> None:
        """权限候选保留业务语义并在 Markdown 中展示第一阶段边界。"""

        spec = create_requirement_spec(
            "创建库存管理系统\n涉及权限控制：是\n启用运行态权限管理页面：是",
            agent_spec={
                "app_info": {"name": "库存应用", "summary": "管理库存。"},
                "feature_modules": [],
                "user_roles": [
                    {
                        "id": "warehouse_staff",
                        "name": "库管员",
                        "description": "处理库存。",
                        "isSystemRole": True,
                        "isInitialAdminRole": True,
                    }
                ],
                "pages": [
                    {
                        "pageId": "inventory_list",
                        "name": "库存列表",
                        "path": "/inventory",
                        "module_id": "inventory",
                        "description": "查看库存。",
                    }
                ],
                "entities": [
                    {
                        "id": "Inventory",
                        "name": "库存",
                        "description": "库存对象。",
                        "fields": [],
                    }
                ],
                "business_flows": [],
                "authorization_requirements": {
                    "enabled": True,
                    "initialAdminRoleId": "warehouse_staff",
                    "restrictedPages": [
                        {
                            "ruleId": "inventory_page_rule",
                            "targetPageId": "inventory_list",
                            "name": "库存列表",
                            "description": "仅授权成员可访问库存。",
                            "rationale": "库存信息属于内部业务数据。",
                            "sourceRefs": ["业务描述"],
                            "defaultGrantedRoleIds": ["warehouse_staff"],
                        }
                    ],
                    "restrictedOperations": [
                        {
                            "ruleId": "inventory_adjust_rule",
                            "operationId": "inventory_adjust",
                            "pageId": "inventory_list",
                            "name": "调整库存",
                            "description": "允许调整库存数量。",
                            "rationale": "调整会改变库存结果。",
                            "sourceRefs": ["业务描述"],
                            "defaultGrantedRoleIds": ["warehouse_staff"],
                        }
                    ],
                },
            },
        )

        authorization = spec["authorization_requirements"]
        self.assertTrue(authorization["enabled"])
        self.assertNotIn("unauthorizedBehavior", authorization)
        self.assertEqual(authorization["restrictedPages"][0]["name"], "库存列表")
        self.assertEqual(authorization["restrictedPages"][0]["targetPageId"], "inventory_list")
        self.assertEqual(authorization["restrictedOperations"][0]["name"], "调整库存")
        self.assertNotIn("operationId", authorization["restrictedOperations"][0])
        self.assertNotIn("pageId", authorization["restrictedOperations"][0])
        self.assertNotIn("permissions", spec["user_roles"][0])
        self.assertEqual(validate_authorization_requirements(spec), [])
        markdown = render_requirement_spec_markdown(spec)
        self.assertIn("## 权限需求", markdown)
        self.assertIn("/roles", markdown)
        self.assertIn("第一阶段不实现数据范围授权", markdown)

    def test_authorization_validation_requires_page_binding(self) -> None:
        """需求阶段的受控页面必须绑定已确认页面，避免 ProductPlan 按名称猜测。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "user_roles": [
                    {
                        "id": "personnel_manager",
                        "name": "人员管理员",
                        "description": "管理人员信息。",
                        "isSystemRole": True,
                        "isInitialAdminRole": True,
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "initialAdminRoleId": "personnel_manager",
                    "restrictedPages": [
                        {
                            "name": "人员列表",
                            "description": "只有获得授权的成员才能查看人员信息。",
                            "targetPageId": "not_generated_yet",
                            "sourceRefs": ["用户提及人员列表权限"],
                            "defaultGrantedRoleIds": ["personnel_manager"],
                        }
                    ],
                    "restrictedOperations": [
                        {
                            "name": "保存",
                            "description": "保存业务对象。",
                            "sourceRefs": ["用户提及保存权限"],
                            "defaultGrantedRoleIds": ["personnel_manager"],
                        },
                        {
                            "name": "再次保存",
                            "description": "再次保存业务对象。",
                            "sourceRefs": ["用户提及再次保存权限"],
                            "defaultGrantedRoleIds": ["personnel_manager"],
                        },
                    ],
                }
            },
        )

        errors = validate_authorization_requirements(spec)
        self.assertTrue(any("targetPageId 必须引用 pages" in error for error in errors))

    def test_authorization_validation_rejects_incomplete_business_candidate(self) -> None:
        """缺少业务语义时仍需澄清，页面绑定仅适用于受控页面规则。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "pages": [
                    {
                        "pageId": "people_list",
                        "name": "人员列表",
                        "path": "/people",
                        "module_id": "people",
                        "description": "查看人员信息。",
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "restrictedOperations": [{"description": "需要限制某个操作。"}],
                }
            },
        )

        errors = validate_authorization_requirements(spec)
        self.assertTrue(any("受控操作缺少业务操作名称" in error for error in errors))
        self.assertFalse(any("数据范围" in error for error in errors))

    def test_data_authorization_requirement_blocks_confirmation(self) -> None:
        """数据范围授权在第一阶段必须明确阻断，不能转为澄清问题继续执行。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "pages": [
                    {
                        "pageId": "people_list",
                        "name": "人员列表",
                        "path": "/people",
                        "module_id": "people",
                        "description": "查看人员信息。",
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "restrictedPages": [],
                    "restrictedOperations": [],
                    "dataRules": [{"name": "人员数据范围"}],
                }
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": clear_clarification(spec),
                },
            ):
                result = requirements_node(
                    {
                        "request": "普通用户只能查看和修改自己的基本信息",
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "requirement_spec": spec,
                        "requirements_clarification_round": 1,
                        "application_planning_interaction": {
                            "action": "answer",
                            "request": "普通用户只能查看和修改自己的基本信息",
                        },
                        "timeline": [],
                    }
                )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "authorization_capability_not_supported")
        self.assertEqual(result["clarification"]["questions"], [])
        self.assertEqual(
            result["clarification"]["capabilityIssues"][0]["code"],
            "DATA_AUTHORIZATION_NOT_SUPPORTED",
        )

    def test_data_authorization_capability_issue_cannot_be_cleared_by_answer(self) -> None:
        """“无”不是移除原始数据权限需求的确认，仍须重新生成需求文档。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "pages": [
                    {
                        "pageId": "people_list",
                        "name": "人员列表",
                        "path": "/people",
                        "module_id": "people",
                        "description": "查看人员信息。",
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "restrictedPages": [
                        {
                            "name": "人员列表",
                            "targetPageId": "people_list",
                            "description": "只有授权成员可以进入人员列表。",
                            "sourceRefs": ["用户提及人员列表权限"],
                        }
                    ],
                    "restrictedOperations": [],
                    "dataRules": [{"name": "人员数据范围"}],
                }
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": clear_clarification(spec),
                },
            ):
                result = requirements_node(
                    {
                        "request": "数据权限不在第一阶段实施",
                        "workspace": workspace,
                        "workflow_scope": "application_planning",
                        "requirement_spec": spec,
                        "requirements_clarification_round": 1,
                        "application_planning_interaction": {
                            "action": "answer",
                            "request": "数据权限不在第一阶段实施",
                        },
                        "timeline": [],
                    }
                )

        self.assertEqual(len(result["requirement_spec"]["authorization_requirements"]["restrictedPages"]), 1)
        self.assertEqual(result["clarification"]["mode"], "authorization_capability_not_supported")

    def test_authorization_without_explicit_candidates_goes_to_confirmation(self) -> None:
        """权限能力开启但用户未提出具体控制时，空候选直接进入需求确认。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "authorization_requirements": {
                    "enabled": True,
                    "unauthorizedBehavior": {"unauthorizedOperation": "disable"},
                    "restrictedPages": [],
                    "restrictedOperations": [],
                    "dataRules": [],
                }
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": clear_clarification(spec),
                },
            ):
                result = requirements_node(
                    {
                        "request": "涉及权限控制：是",
                        "workspace": workspace,
                        "timeline": [],
                    }
                )

            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["clarification"]["mode"], "ask_user_question")
            self.assertEqual(
                result["clarification"]["questions"][-1]["id"],
                "authorization_initial_admin_role",
            )
            authorization = result["requirement_spec"]["authorization_requirements"]
            self.assertEqual(authorization["restrictedPages"], [])
            self.assertEqual(authorization["restrictedOperations"], [])
            self.assertNotIn("dataRules", authorization)
            self.assertIn(
                "权限启用时必须且只能选择一个初始系统管理员角色",
                validate_authorization_requirements(result["requirement_spec"]),
            )
            self.assertFalse(
                (Path(workspace) / ".xcodeagent/drafts/specs/requirement-spec.md").exists()
            )

    def test_initial_admin_and_default_grants_are_collected_by_stable_questions(self) -> None:
        """权限启用时按稳定问题依次收集初始管理员和规则默认授权。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "pages": [
                    {
                        "pageId": "order_list",
                        "name": "订单列表",
                        "path": "/orders",
                        "module_id": "orders",
                        "description": "查看订单。",
                    }
                ],
                "user_roles": [
                    {
                        "id": "business_manager",
                        "name": "业务管理员",
                        "description": "管理业务记录。",
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "restrictedPages": [
                        {
                            "name": "订单列表",
                            "targetPageId": "order_list",
                            "description": "仅授权成员可查看订单。",
                            "rationale": "订单包含内部业务信息。",
                            "sourceRefs": ["用户提出订单列表权限"],
                        }
                    ],
                    "restrictedOperations": [],
                    "dataRules": [],
                },
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={"requirement_spec": spec, "clarification": clear_clarification(spec)},
            ):
                first = requirements_node(
                    {"request": "涉及权限控制：是", "workspace": workspace, "timeline": []}
                )
            self.assertEqual(
                first["clarification"]["questions"][-1]["id"],
                "authorization_initial_admin_role",
            )
            self.assertEqual(
                [item["label"] for item in first["clarification"]["questions"][-1]["options"]],
                ["业务管理员", "新建独立系统管理员"],
            )

            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": first["requirement_spec"],
                    "clarification": clear_clarification(first["requirement_spec"]),
                },
            ):
                second = requirements_node(
                    {
                        "request": "选择业务管理员承担系统权限管理",
                        "workspace": workspace,
                        "requirement_spec": first["requirement_spec"],
                        "application_planning_interaction": {
                            "action": "answer",
                            "answers": {"authorization_initial_admin_role": "business_manager"},
                        },
                        "timeline": [],
                    }
                )
            rule_id = second["requirement_spec"]["authorization_requirements"]["restrictedPages"][0]["ruleId"]
            self.assertEqual(
                second["clarification"]["questions"][-1]["id"],
                f"authorization_default_grants_{rule_id}",
            )

            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": second["requirement_spec"],
                    "clarification": clear_clarification(second["requirement_spec"]),
                },
            ):
                third = requirements_node(
                    {
                        "request": "默认授予业务管理员",
                        "workspace": workspace,
                        "requirement_spec": second["requirement_spec"],
                        "application_planning_interaction": {
                            "action": "answer",
                            "answers": {f"authorization_default_grants_{rule_id}": ["business_manager"]},
                        },
                        "timeline": [],
                    }
                )
        self.assertEqual(third["clarification"]["mode"], "requirement_document_draft")
        self.assertEqual(validate_authorization_requirements(third["requirement_spec"]), [])

    def test_initial_admin_selection_waits_for_business_role_analysis(self) -> None:
        """没有已识别业务角色时，不能只显示新建系统管理员选项。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "user_roles": [],
                "authorization_requirements": {
                    "enabled": True,
                    "restrictedPages": [],
                    "restrictedOperations": [],
                    "dataRules": [],
                },
            },
            authoritative_agent_spec=True,
            allow_inferred_defaults=False,
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={"requirement_spec": spec, "clarification": clear_clarification(spec)},
            ):
                result = requirements_node(
                    {"request": "涉及权限控制：是", "workspace": workspace, "timeline": []}
                )
        question = result["clarification"]["questions"][-1]
        self.assertEqual(question["id"], "authorization_business_roles")
        self.assertIn("管理员类角色", question["question"])

    def test_operation_candidate_without_page_binding_enters_confirmation(self) -> None:
        """操作候选只说明业务含义时，不因尚未生成 pageId 而停留在澄清。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "authorization_requirements": {
                    "enabled": True,
                    "unauthorizedBehavior": {"unauthorizedOperation": "disable"},
                    "restrictedPages": [],
                    "restrictedOperations": [
                        {
                            "name": "停用人员",
                            "description": "只有获得授权的成员才能停用人员信息。",
                            "rationale": "停用会影响人员使用状态。",
                            "sourceRefs": ["用户提及停用人员权限"],
                        }
                    ],
                    "dataRules": [],
                }
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": clear_clarification(spec),
                },
            ):
                result = requirements_node(
                    {
                        "request": "涉及权限控制：是",
                        "workspace": workspace,
                        "timeline": [],
                    }
                )

            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["clarification"]["mode"], "ask_user_question")
            self.assertEqual(
                result["clarification"]["questions"][-1]["id"],
                "authorization_initial_admin_role",
            )
            self.assertNotIn(
                "pageId",
                result["requirement_spec"]["authorization_requirements"][
                    "restrictedOperations"
                ][0],
            )

    def test_incomplete_authorization_candidates_use_stepwise_business_questions(self) -> None:
        """权限候选不完整时按业务维度逐步提问，而不是展示结构字段错误。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "authorization_requirements": {
                    "enabled": True,
                    "restrictedPages": [{}],
                    "restrictedOperations": [{}],
                }
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": clear_clarification(spec),
                },
            ):
                result = requirements_node(
                    {
                        "request": "涉及权限控制：是",
                        "workspace": workspace,
                        "timeline": [],
                    }
                )

            question = result["clarification"]["questions"][-1]
            self.assertEqual(question["id"], "authorization_page_business")
            self.assertIn("第 1 步", question["question"])
            self.assertNotIn("缺少业务对象名称", question["question"])
            self.assertNotIn("pageId", question["question"])
            self.assertNotIn("operationId", question["question"])

    def test_model_identified_authorization_ambiguity_stays_in_clarification(self) -> None:
        """模型识别到用户明确权限描述存在歧义时，需求节点保留该业务澄清。"""

        spec = create_requirement_spec(
            "涉及权限控制：是\n数据范围需要按业务情况确认",
            agent_spec={
                "authorization_requirements": {
                    "enabled": True,
                    "restrictedPages": [],
                    "restrictedOperations": [],
                }
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_application_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": {
                        "mode": "ask_user_question",
                        "status": "requires_user_input",
                        "questions": [
                            {
                                "id": "authorization_scope",
                                "header": "数据范围",
                                "dimension": "权限业务边界",
                                "question": "订单数据范围应限制为本人还是所属组织？",
                                "type": "text",
                            }
                        ],
                    },
                },
            ):
                result = requirements_node(
                    {
                        "request": "涉及权限控制：是\n数据范围需要按业务情况确认",
                        "workspace": workspace,
                        "timeline": [],
                    }
                )

            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["clarification"]["mode"], "ask_user_question")
            self.assertTrue(
                any("数据范围" in question["question"] for question in result["clarification"]["questions"])
            )
            self.assertFalse(
                (Path(workspace) / ".xcodeagent/drafts/specs/requirement-spec.md").exists()
            )

    def test_disabled_authorization_clears_model_candidates(self) -> None:
        """表单明确关闭权限时，模型返回的权限候选不能进入 RequirementSpec。"""

        spec = create_requirement_spec(
            "涉及权限控制：否",
            agent_spec={
                "authorization_requirements": {
                    "enabled": True,
                    "dataRules": [
                        {
                            "ruleId": "scope",
                            "dataRuleId": "all",
                            "entityId": "User",
                            "scope": "all",
                            "ruleDescription": "全部数据。",
                        }
                    ],
                }
            },
        )

        authorization = spec["authorization_requirements"]
        self.assertFalse(authorization["enabled"])
        self.assertNotIn("dataRules", authorization)
        self.assertNotIn("login_page", [page["pageId"] for page in spec["pages"]])

    def test_requirement_spec_does_not_accept_runtime_management_page_switch(self) -> None:
        """RequirementSpec 不再保存运行态权限管理页面开关。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "authorization_requirements": {
                    "enabled": True,
                    "dataRules": [
                        {
                            "ruleId": "scope",
                            "dataRuleId": "all",
                            "entityId": "User",
                            "scope": "all",
                            "ruleDescription": "所有登录成员可以查看全部数据。",
                        }
                    ],
                }
            },
        )

        self.assertTrue(spec["authorization_requirements"]["enabled"])
        self.assertNotIn("runtimeManagementPageEnabled", spec["authorization_requirements"])

    def test_permission_draft_save_rejects_unclosed_editor_changes(self) -> None:
        """权限编辑草稿保存会执行闭合校验，但不会把无效候选写入磁盘。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "authorization_requirements": {
                    "enabled": True,
                    "dataRules": [
                        {
                            "ruleId": "scope",
                            "dataRuleId": "all",
                            "entityId": "User",
                            "scope": "all",
                            "ruleDescription": "登录成员可以查看全部数据。",
                        }
                    ],
                }
            },
        )
        spec["confirmation_status"] = "pending_user_confirmation"
        edited = {
            **spec,
            "authorization_requirements": {
                **spec["authorization_requirements"],
                "restrictedOperations": [
                    {
                        "name": "保存",
                        "description": "",
                        "pageId": "missing_page",
                    }
                ],
            },
        }

        with tempfile.TemporaryDirectory() as workspace:
            write_requirement_spec_draft_document({"workspace": workspace}, spec)
            _write_application_config(workspace)
            with self.assertRaisesRegex(ValueError, "编辑后的权限需求"):
                save_requirement_spec_draft(
                    SaveRequirementSpecDraftRequest.model_validate(
                        {
                            "action": "save",
                            "workspaceRoot": workspace,
                            "spec": edited,
                        }
                    )
                )

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

    def test_empty_business_structure_cannot_enter_requirement_confirmation(self) -> None:
        """自然语言摘要存在时也不能确认空模块、空页面的需求文档。"""

        spec = create_requirement_spec(
            "创建人员管理应用",
            agent_spec={
                "app_info": {"name": "", "summary": "管理人员信息"},
                "feature_modules": [],
                "pages": [],
                "entities": [],
                "business_flows": [],
            },
            allow_inferred_defaults=False,
        )

        errors = validate_requirement_spec_confirmation_readiness(spec)

        self.assertIn("应用名称不能为空", errors)
        self.assertIn("功能模块不能为空", errors)
        self.assertIn("页面清单不能为空", errors)

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
            "requirement_document_draft",
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
            "requirement_document_draft",
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

    def test_agent_suitability_question_blocks_before_requirement_draft(self) -> None:
        """普通业务的智能体建议也必须等待回答，且不能提前生成需求草稿。"""

        request = "创建一个业务系统，支持处理业务记录。"
        spec = create_requirement_spec(request)
        question = {
            "header": "智能体建议",
            "question": "是否需要加入智能体能力以协助现有业务？",
            "type": "yesno",
        }
        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": {
                        "mode": "ask_user_question",
                        "status": "requires_user_input",
                        "questions": [question],
                    },
                },
            ):
                result = requirements(
                    {
                        "request": request,
                        "workspace": workspace,
                        "timeline": [],
                    }
                )
            draft_markdown = Path(workspace) / ".xcodeagent/drafts/specs/requirement-spec.md"
            draft_json = Path(workspace) / ".xcodeagent/drafts/specs/requirement-spec.json"

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "ask_user_question")
        self.assertEqual(result["clarification"]["questions"], [question])
        self.assertEqual(result["requirement_spec"]["agent_requirements"], [])
        self.assertEqual(
            result["requirement_spec"]["confirmation_status"],
            "pending_user_input",
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
            result["clarification"]["mode"], "requirement_document_draft"
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
            "requirement_document_draft",
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
            "requirement_document_draft",
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
