from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.nodes.requirements import requirements
from app.services.application_authorization_config import (
    ApplicationAuthorizationConfigError,
    persist_authorization_configuration,
)
from app.services.requirement_spec import create_requirement_spec


def _write_current_config(workspace: str, datasource_type: str = "database") -> Path:
    """为测试工作区写入最小 schema v5 应用配置。"""

    target = Path(workspace) / ".xcodeagent" / "application.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "schemaVersion": 5,
                "appName": "权限测试应用",
                "datasource": {"type": datasource_type},
                "auth": {"enable": False},
                "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return target


class ApplicationAuthorizationConfigTests(unittest.TestCase):
    """验证应用权限配置原子更新和规划冲突前置澄清。"""

    def test_persist_enables_auth_and_authorization_together(self) -> None:
        """管理员校验通过后，认证和权限开关必须一起写入同一份配置。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = _write_current_config(workspace)
            persisted = persist_authorization_configuration(
                workspace,
                initial_administrator_subjects=[" ops@example.com ", "ops@example.com"],
            )
            self.assertTrue(persisted["auth"]["enable"])
            self.assertTrue(persisted["authorization"]["enabled"])
            self.assertEqual(
                persisted["authorization"]["initialAdministratorSubjects"],
                ["ops@example.com"],
            )
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), persisted)

    def test_persist_rejects_static_datasource_and_magic_subject(self) -> None:
        """非数据库数据源和 current-user 都不能进入权限初始化配置。"""

        with tempfile.TemporaryDirectory() as workspace:
            _write_current_config(workspace, "static")
            with self.assertRaisesRegex(ValueError, "数据库数据源"):
                persist_authorization_configuration(
                    workspace,
                    initial_administrator_subjects=["ops@example.com"],
                )
        with tempfile.TemporaryDirectory() as workspace:
            _write_current_config(workspace)
            with self.assertRaisesRegex(ApplicationAuthorizationConfigError, "current-user"):
                persist_authorization_configuration(
                    workspace,
                    initial_administrator_subjects=["current-user"],
                )

    def test_persist_rejects_legacy_authorization_fields(self) -> None:
        """当前应用配置不得继续写入旧的授权提供方字段。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = _write_current_config(workspace)
            content = json.loads(target.read_text(encoding="utf-8"))
            content["authorization"]["providerMode"] = "external"
            target.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ApplicationAuthorizationConfigError, "必须只包含"):
                persist_authorization_configuration(
                    workspace,
                    initial_administrator_subjects=["ops@example.com"],
                )

    def test_natural_language_permission_request_only_collects_admin_subject(self) -> None:
        """明确受控页面应自动启用权限意图，不能要求用户重复选择开关。"""

        spec = create_requirement_spec(
            "涉及权限控制：是",
            agent_spec={
                "app_info": {"name": "权限测试应用", "summary": "测试。"},
                "feature_modules": [{"id": "core", "name": "核心", "description": "核心模块。"}],
                "pages": [{"pageId": "home", "name": "首页", "path": "/home", "module_id": "core", "description": "首页。"}],
                "entities": [{"id": "User", "name": "用户", "description": "用户。"}],
                "business_flows": [{"id": "browse", "name": "浏览", "steps": ["查看首页"]}],
                "user_roles": [
                    {
                        "id": "administrator",
                        "name": "管理员",
                        "description": "管理系统权限。",
                        "isSystemRole": True,
                        "isInitialAdminRole": True,
                    }
                ],
                "authorization_requirements": {
                    "enabled": True,
                    "initialAdminRoleId": "administrator",
                    "restrictedPages": [
                        {
                            "name": "首页",
                            "targetPageId": "home",
                            "description": "仅管理员可以查看首页。",
                            "rationale": "用户明确要求管理员可见。",
                            "sourceRefs": ["需要管理员才能看到列表页"],
                            "defaultGrantedRoleIds": ["administrator"],
                        }
                    ],
                    "restrictedOperations": [],
                },
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            target = _write_current_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": spec,
                    "clarification": {"status": "clear", "questions": []},
                },
            ):
                first = requirements({"workflow_scope": "application_planning", "workspace": workspace, "request": "设计变更：我想添加权限功能，需要管理员才能看到列表页", "timeline": []})
            self.assertEqual(first["clarification"]["questions"][0]["id"], "authorization_initial_admin")
            self.assertEqual(first["requirement_spec"]["authorization_requirements"]["restrictedPages"][0]["targetPageId"], "home")

            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={"requirement_spec": spec, "clarification": {"status": "clear", "questions": []}},
            ):
                requirements({
                    "workflow_scope": "application_planning",
                    "workspace": workspace,
                    "request": "继续处理。",
                    "timeline": [],
                    "requirement_spec": first["requirement_spec"],
                    "requirement_revision_id": first["requirement_revision_id"],
                    "authorization_config_conflict": first["authorization_config_conflict"],
                    "application_planning_interaction": {"action": "answer", "answers": {"authorization_initial_admin": "ops@example.com"}},
                })
            persisted = json.loads(target.read_text(encoding="utf-8"))
            self.assertTrue(persisted["auth"]["enable"])
            self.assertEqual(persisted["authorization"]["initialAdministratorSubjects"], ["ops@example.com"])

    def test_stale_authorization_conflict_is_not_consumed_by_new_requirement_revision(
        self,
    ) -> None:
        """revisionId 不匹配的旧权限初始化问题不能劫持新增登录需求。"""

        login_spec = create_requirement_spec(
            "我想添加登录功能",
            agent_spec={
                "authentication_requirements": {
                    "enabled": True,
                    "sourceRefs": ["我想添加登录功能"],
                },
                "authorization_requirements": {
                    "enabled": False,
                    "restrictedPages": [],
                    "restrictedOperations": [],
                },
            },
        )
        with tempfile.TemporaryDirectory() as workspace:
            _write_current_config(workspace)
            with patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                return_value={
                    "requirement_spec": login_spec,
                    "clarification": {"status": "clear", "questions": []},
                },
            ):
                result = requirements(
                    {
                        "workflow_scope": "application_planning",
                        "workspace": workspace,
                        "request": "我想添加登录功能",
                        "timeline": [],
                        "requirement_revision_id": "current-revision",
                        "authorization_config_conflict": {
                            "requested": True,
                            "revisionId": "stale-revision",
                            "decision": "enable",
                        },
                    }
                )

        self.assertEqual(result["authorization_config_conflict"], {})
        self.assertNotIn(
            "authorization_initial_admin",
            [
                question.get("id")
                for question in result["clarification"].get("questions", [])
                if isinstance(question, dict)
            ],
        )


if __name__ == "__main__":
    unittest.main()
