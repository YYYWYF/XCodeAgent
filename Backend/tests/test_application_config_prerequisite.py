"""自然语言中的配置前置条件与业务意图分离。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.application_config_prerequisite import analyze_application_config_prerequisite


class ConfigPrerequisiteTests(unittest.TestCase):
    """验证显式开关、隐含授权和混合请求原文。"""

    def setUp(self) -> None:
        """建立 Base 应用及空能力状态。"""

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        folder = self.root / ".devagentstudio"
        folder.mkdir()
        (folder / "application.json").write_text(json.dumps({
            "schemaVersion": 6, "configRevision": 1,
            "appName": "原应用", "appIcon": "app", "theme": {"primaryColor": "#123456"},
            "auth": {"enable": False, "yht": {"clientId": "", "privateKey": "", "publicKey": "", "centerPublicKey": ""}},
            "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database"},
        }, ensure_ascii=False))
        self.state_path = folder / "template-state.json"
        self.state_path.write_text(json.dumps({
            "schemaVersion": 3, "templateRevision": "2026.10.20.1", "stateSequence": 0,
            "requested": {}, "effective": {}, "installedArtifacts": {}, "managedContributions": {},
        }))

    def tearDown(self) -> None:
        """释放临时目录。"""

        self.temporary.cleanup()

    def test_implicit_admin_rule_keeps_business(self) -> None:
        """管理员可见性是业务修订，权限能力只是前置建议。"""

        request = "页面一只有管理员可以查看"
        result = analyze_application_config_prerequisite(request, self.root)
        self.assertEqual(result.original_request, request)
        self.assertTrue(result.business_request_required)
        self.assertEqual(result.config_intents, ())
        self.assertEqual(result.config_prerequisites[0]["path"], "authorization.enabled")
        self.assertEqual(result.config_prerequisites[0]["source"], "inferred")

    def test_mixed_login_and_page_keeps_full_request(self) -> None:
        """显式 Login 不应吞掉新增页面业务任务。"""

        request = "启用登录，并新增员工管理页面"
        result = analyze_application_config_prerequisite(request, self.root)
        self.assertEqual(result.original_request, request)
        self.assertTrue(result.business_request_required)
        self.assertEqual(result.suggested_changes, {"auth.enable": True})

    def test_rename_plus_query_keeps_business(self) -> None:
        """改名与新增查询必须分别保留。"""

        result = analyze_application_config_prerequisite("把应用名称改为员工中心，并新增员工查询", self.root)
        self.assertEqual(result.suggested_changes["appName"], "员工中心")
        self.assertTrue(result.business_request_required)

    def test_explicit_denial_conflicts_with_admin_rule(self) -> None:
        """不得因权限禁用表达删除原业务访问条件。"""

        result = analyze_application_config_prerequisite("页面一仅管理员可见，但不要开启权限", self.root)
        self.assertIn("CONFIG_DEPENDENCY_CONFLICT", result.conflict)

    def test_installed_authorization_needs_no_card(self) -> None:
        """已安装权限时业务访问限制直接保留给需求修订。"""

        config_path = self.root / ".devagentstudio/application.json"
        config = json.loads(config_path.read_text())
        config["auth"]["enable"] = True
        config["authorization"]["enabled"] = True
        config["authorization"]["initialAdministratorSubjects"] = ["admin-1"]
        config_path.write_text(json.dumps(config, ensure_ascii=False))
        state = json.loads(self.state_path.read_text())
        capability = {"enabled": True, "config": {}}
        state["requested"] = {"login": capability, "authorization": capability}
        state["effective"] = state["requested"]
        self.state_path.write_text(json.dumps(state))
        result = analyze_application_config_prerequisite("页面一只有管理员可以查看", self.root)
        self.assertFalse(result.requires_card)
        self.assertTrue(result.business_request_required)


if __name__ == "__main__":
    unittest.main()
