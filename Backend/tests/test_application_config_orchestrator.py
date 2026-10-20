"""统一配置提交的 CAS、幂等与非敏感持久状态测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.application_config_orchestrator import ApplicationConfigOperationError, ApplicationConfigOrchestrator


class ApplicationConfigOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    """覆盖首次 Generate 前只保存 Desired 的事务分支。"""

    def setUp(self) -> None:
        """创建不含 TemplateState 的当前配置工作区。"""

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / ".devagentstudio/application.json"
        self.config_path.parent.mkdir()
        self.config_path.write_text(json.dumps({
            "schemaVersion": 6, "configRevision": 1,
            "appName": "原应用", "appIcon": "app", "theme": {"primaryColor": "#123456"},
            "auth": {"enable": False, "yht": {"clientId": "", "privateKey": "", "publicKey": "", "centerPublicKey": ""}},
            "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database"},
        }, ensure_ascii=False), encoding="utf-8")
        self.service = ApplicationConfigOrchestrator(SimpleNamespace())

    def tearDown(self) -> None:
        """释放临时工作区。"""

        self.temporary.cleanup()

    async def test_preview_apply_and_replay(self) -> None:
        """仅名称变更只修改 Desired，重复 operationId 不再增加修订号。"""

        changes = {"appName": "新应用"}
        projection = self.service.preview(self.root, expected_revision=1, changes=changes)
        self.assertEqual(projection["changedKeys"], ["appName"])
        self.assertFalse(projection["generated"])
        first = await self.service.apply(self.root, operation_id="operation-001", expected_revision=1, changes=changes)
        second = await self.service.apply(self.root, operation_id="operation-001", expected_revision=1, changes=changes)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "APPLIED")
        self.assertEqual(json.loads(self.config_path.read_text())["configRevision"], 2)
        self.assertEqual(first["changedFiles"], [])

    async def test_stale_revision_and_secret_not_logged(self) -> None:
        """过期版本被拒绝，操作记录不含用户密钥值。"""

        with self.assertRaisesRegex(ApplicationConfigOperationError, "CONFIG_REVISION_CONFLICT"):
            self.service.preview(self.root, expected_revision=2, changes={"appName": "新应用"})
        changes = {"auth.yht.privateKey": "sensitive-secret-value"}
        await self.service.apply(self.root, operation_id="operation-002", expected_revision=1, changes=changes)
        record = (self.root / ".devagentstudio/runtime/application-config/operation-002.json").read_text()
        self.assertNotIn("sensitive-secret-value", record)
        self.assertIn("auth.yht.privateKey", record)

    async def test_enable_login_initializes_missing_yht_container(self) -> None:
        """关闭登录时缺失 yht 的配置可在首次启用时写入完整凭据。"""

        current = json.loads(self.config_path.read_text(encoding="utf-8"))
        del current["auth"]["yht"]
        self.config_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        changes = {
            "auth.enable": True,
            "auth.yht.clientId": "client-id",
            "auth.yht.privateKey": "encrypted-private-key",
            "auth.yht.publicKey": "public-key",
            "auth.yht.centerPublicKey": "center-public-key",
        }

        result = await self.service.apply(
            self.root,
            operation_id="operation-003",
            expected_revision=1,
            changes=changes,
        )

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "APPLIED")
        self.assertTrue(saved["auth"]["enable"])
        self.assertEqual(saved["auth"]["yht"], {
            "clientId": "client-id",
            "privateKey": "encrypted-private-key",
            "publicKey": "public-key",
            "centerPublicKey": "center-public-key",
        })


if __name__ == "__main__":
    unittest.main()
