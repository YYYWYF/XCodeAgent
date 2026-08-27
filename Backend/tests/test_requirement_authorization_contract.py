from __future__ import annotations

import unittest

from app.agents.main.document_sync import sync_requirement_spec_from_markdown
from app.agents.main.requirements_analyzer import (
    _merge_authorization_facts,
    _validate_authorization_fact_output,
)
from app.services.requirement_spec import (
    create_requirement_spec,
    validate_authorization_requirements,
)


class RequirementAuthorizationContractTests(unittest.TestCase):
    """覆盖 RequirementSpec 当前权限候选契约的核心边界。"""

    def test_empty_candidates_do_not_require_unauthorized_behavior(self) -> None:
        """启用权限但未提出任何受控对象时可直接进入确认。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "user_roles": [
                    {
                        "id": "administrator",
                        "name": "管理员",
                        "description": "负责系统权限管理。",
                        "isSystemRole": True,
                        "isInitialAdminRole": True,
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "initialAdminRoleId": "administrator",
                    "restrictedPages": [],
                    "restrictedOperations": [],
                }
            },
        )

        self.assertNotIn("unauthorizedBehavior", spec["authorization_requirements"])
        self.assertEqual(validate_authorization_requirements(spec), [])

    def test_authorization_fact_extraction_replaces_incomplete_model_candidates(self) -> None:
        """明确角色和权限事实必须覆盖模型的空角色及缺字段候选。"""

        facts = {
            "user_roles": [
                {"id": "administrator", "name": "管理员", "description": "管理办公物品出入库。"},
                {"id": "ordinary_user", "name": "普通用户", "description": "查看自己名下的资产。"},
            ],
            "authorization_requirements": {
                "restrictedPages": [
                    {
                        "name": "资产列表页",
                        "targetPageId": "asset_list",
                        "description": "只有管理员可以进入资产列表页。",
                        "rationale": "列表展示全局资产信息。",
                        "sourceRefs": ["管理员可查看资产列表页"],
                        "defaultGrantedRoleIds": ["administrator"],
                    }
                ],
                "restrictedOperations": [],
                "dataAuthorizationIssues": [
                    {
                        "description": "普通用户只能查看自己名下的资产。",
                        "sourceRefs": ["普通用户查看自己名下的资产"],
                    }
                ],
            },
        }

        self.assertEqual(
            _validate_authorization_fact_output(
                facts,
                [{"pageId": "asset_list", "name": "资产列表", "description": "资产信息。"}],
            ),
            [],
        )
        merged = _merge_authorization_facts(
            {
                "user_roles": [],
                "authorization_requirements": {"restrictedPages": [{}]},
            },
            facts,
            None,
        )

        self.assertEqual([role["id"] for role in merged["user_roles"]], ["administrator", "ordinary_user"])
        self.assertEqual(
            merged["authorization_requirements"]["restrictedPages"][0]["description"],
            "只有管理员可以进入资产列表页。",
        )
        self.assertEqual(
            merged["authorization_capability_issues"][0]["code"],
            "DATA_AUTHORIZATION_NOT_SUPPORTED",
        )

    def test_authorization_fact_output_rejects_incomplete_page_candidate(self) -> None:
        """模型遗漏页面业务说明时必须触发自动修复，不能转嫁给用户。"""

        facts = {
            "user_roles": [
                {"id": "administrator", "name": "管理员", "description": "管理资产。"},
            ],
            "authorization_requirements": {
                "restrictedPages": [
                    {
                        "name": "资产列表页",
                        "targetPageId": "asset_list",
                        "description": "",
                        "rationale": "资产信息需要管理。",
                        "sourceRefs": ["管理员可查看资产列表页"],
                        "defaultGrantedRoleIds": ["administrator"],
                    }
                ],
                "restrictedOperations": [],
                "dataAuthorizationIssues": [],
            },
        }

        errors = _validate_authorization_fact_output(
            facts,
            [{"pageId": "asset_list", "name": "资产列表", "description": "资产信息。"}],
        )

        self.assertTrue(any("缺少业务语义" in error for error in errors))

    def test_data_authorization_issue_blocks_confirmation(self) -> None:
        """数据授权能力问题不能作为 V1 正式 RequirementSpec 确认。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "user_roles": [
                    {
                        "id": "order_manager",
                        "name": "订单管理员",
                        "description": "管理订单和系统权限。",
                        "isSystemRole": True,
                        "isInitialAdminRole": True,
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "initialAdminRoleId": "order_manager",
                }
            },
        )

        spec["authorization_capability_issues"] = [
            {
                "code": "DATA_AUTHORIZATION_NOT_SUPPORTED",
                "capability": "data_authorization",
                "description": "订单数据按负责人隔离。",
                "sourceRefs": ["用户提及订单仅本人可见"],
            }
        ]

        self.assertNotIn("unauthorizedBehavior", spec["authorization_requirements"])
        self.assertIn(
            "当前需求包含不支持的数据权限：DATA_AUTHORIZATION_NOT_SUPPORTED",
            validate_authorization_requirements(spec),
        )

    def test_rule_id_ignores_model_value_and_survives_reordering(self) -> None:
        """模型不得指定 ruleId，编辑器重排则必须复用已有内部标识。"""

        first = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "authorization_requirements": {
                    "enabled": True,
                    "unauthorizedBehavior": {"unauthorizedPage": "show_forbidden"},
                    "restrictedPages": [
                        {
                            "ruleId": "model-controlled-id",
                            "name": "人员列表",
                            "targetPageId": "people_list",
                            "description": "仅授权成员可查看人员信息。",
                            "rationale": "人员信息属于内部资料。",
                            "sourceRefs": ["用户提及人员列表权限"],
                        }
                    ],
                }
            },
        )
        rule_id = first["authorization_requirements"]["restrictedPages"][0]["ruleId"]
        self.assertNotEqual(rule_id, "model-controlled-id")

        reordered = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec=first,
            existing_spec=first,
            authoritative_agent_spec=True,
        )
        self.assertEqual(
            reordered["authorization_requirements"]["restrictedPages"][0]["ruleId"],
            rule_id,
        )

    def test_markdown_rejects_unknown_or_duplicate_rule_id_markers(self) -> None:
        """Markdown 同步拒绝伪造和重复的隐藏权限标识。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "authorization_requirements": {
                    "enabled": True,
                    "unauthorizedBehavior": {"unauthorizedPage": "show_forbidden"},
                    "restrictedPages": [
                        {
                            "name": "人员列表",
                            "targetPageId": "people_list",
                            "description": "仅授权成员可查看人员信息。",
                            "rationale": "人员信息属于内部资料。",
                            "sourceRefs": ["用户提及人员列表权限"],
                        }
                    ],
                }
            },
        )
        rule_id = spec["authorization_requirements"]["restrictedPages"][0]["ruleId"]
        with self.assertRaisesRegex(ValueError, "未知 ruleId"):
            sync_requirement_spec_from_markdown(spec, "<!-- ruleId:forged -->")
        with self.assertRaisesRegex(ValueError, "重复 ruleId"):
            sync_requirement_spec_from_markdown(
                spec,
                f"<!-- ruleId:{rule_id} -->\n<!-- ruleId:{rule_id} -->",
            )

    def test_restricted_page_requires_existing_target_page_id(self) -> None:
        """受控页面必须以已确认 pageId 建立稳定绑定，不能再回退到名称猜测。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "pages": [
                    {
                        "pageId": "asset_list",
                        "name": "资产列表",
                        "path": "/assets",
                        "module_id": "asset",
                        "description": "查看资产。",
                    }
                ],
                "user_roles": [
                    {
                        "id": "administrator",
                        "name": "管理员",
                        "description": "管理资产。",
                        "isSystemRole": True,
                        "isInitialAdminRole": True,
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "initialAdminRoleId": "administrator",
                    "restrictedPages": [
                        {
                            "name": "资产管理功能",
                            "targetPageId": "asset_list",
                            "description": "仅管理员可访问。",
                            "rationale": "资产信息属于内部资料。",
                            "sourceRefs": ["需求"],
                            "defaultGrantedRoleIds": ["administrator"],
                        }
                    ],
                    "restrictedOperations": [],
                },
            },
        )

        self.assertEqual(validate_authorization_requirements(spec), [])
        spec["authorization_requirements"]["restrictedPages"][0]["targetPageId"] = "missing_page"
        self.assertTrue(
            any("targetPageId 必须引用 pages" in error for error in validate_authorization_requirements(spec))
        )
